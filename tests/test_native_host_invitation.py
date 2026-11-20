from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from tools.silverfish import native_host_invitation as invitation, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control


GEN, EPOCH, NEXT_EPOCH, OPERATION = "a" * 32, "b" * 32, "c" * 32, "d" * 32


class HostInvitationMailboxTests(unittest.TestCase):
    def setUp(self):
        base = Path(self.enterContext(TemporaryDirectory())).resolve()
        self.run = base / "campaigns" / "exact-host" / "run"
        self.bridge = self.run / "game-bridge" / ("e" * 32)
        self.bridge.mkdir(parents=True)
        self.control = self.enterContext(Control(self.run))
        self.store = self.enterContext(CampaignStore(self.run.parent / "save"))
        host = service.Host.__new__(service.Host)
        host.control, host.store, host.bind, host.port = self.control, self.store, "0.0.0.0", 17777
        host.game_build = "f" * 64
        host.identity = SimpleNamespace(certificate_pem="private fixture certificate")
        for flag in ("native_zones", "native_zone_walls", "native_guest_controls", "expanded_zones", "native_shop_view",
                     "native_loot_input", "broker_input", "wipe_view_enabled", "wire_board_enabled", "guest_mre",
                     "mosby_station_enabled", "mindcore_view_enabled"):
            setattr(host, flag, True)
        host.dialogue_sessions = host.smg_holds = None
        self.issue = Mock(wraps=host.invite)
        self.now = 1700
        self.mailbox = invitation.HostInvitationMailbox(self.bridge, self.run, self.issue, clock=lambda: self.now)
        self.addCleanup(self.mailbox.close)
        self.mailbox.poll(GEN, EPOCH, True)

    def request(self, *, generation=GEN, epoch=EPOCH, operation=OPERATION, name="Nélie",
                character="Starter Scout", address="co-op.example.test"):
        self.mailbox.request_path.write_bytes(
            f"1\t{generation}\t{epoch}\t{operation}\t{name}\t{character}\t{address}\n".encode("utf-8"))

    def response(self, status, code, *, epoch=EPOCH, operation=OPERATION):
        self.assertFalse(self.mailbox.request_path.exists())
        self.assertEqual(self.mailbox.response_path.read_bytes(),
                         f"1\t{GEN}\t{epoch}\t{operation}\t{status}\t{code}\n".encode("ascii"))

    @property
    def draft(self):
        return self.bridge / f"host-invite-{OPERATION}.invite.json"

    def canonical(self):
        copies = list((self.run / "invites").glob("*.invite.json"))
        self.assertEqual(len(copies), 1)
        return copies[0]

    def test_real_invite_creates_one_owned_starter_and_copies_exact_bytes_privately(self):
        self.assertEqual(self.mailbox.offer_path.read_bytes(),
                         f"1\t{GEN}\t{EPOCH}\t{self.now + invitation.OFFER_SECONDS}\n".encode("ascii"))
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("ok", "exported")
        canonical = self.canonical()
        self.assertEqual(self.draft.read_bytes(), canonical.read_bytes())
        issued = runtime.read_json(canonical)
        self.assertEqual((issued["host"], issued["port"]), ("co-op.example.test", 17777))
        owned = self.store.list_characters(issued["guest_id"], issued["credential"])
        self.assertEqual([(item["character_id"], item["name"]) for item in owned], [(issued["character_id"], "Starter Scout")])
        self.assertEqual(self.store.status()["guest_count"], 1)
        secret = issued["credential"].encode("ascii")
        for body in (self.mailbox.offer_path.read_bytes(), self.mailbox.response_path.read_bytes(),
                     (self.run / "events.jsonl").read_bytes(), repr(self.store.status()).encode("utf-8")):
            self.assertNotIn(secret, body)
        self.assertNotIn(b"co-op.example.test", self.mailbox.response_path.read_bytes())

        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("ok", "exported")
        self.assertEqual(self.draft.read_bytes(), canonical.read_bytes())
        self.request(character="Different starter")
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("error", "operation_conflict")
        self.issue.assert_called_once()

    def test_stale_expired_and_inactive_requests_are_discarded_without_issuing(self):
        self.request(generation="1" * 32)
        self.mailbox.poll(GEN, EPOCH, True)
        self.assertFalse(self.mailbox.response_path.exists())
        self.now += invitation.OFFER_SECONDS + 1
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.assertFalse(self.mailbox.request_path.exists() or self.mailbox.response_path.exists())
        self.request(epoch=EPOCH)
        self.mailbox.poll(GEN, NEXT_EPOCH, True)
        self.assertFalse(self.mailbox.request_path.exists() or self.mailbox.response_path.exists())
        self.request(epoch=NEXT_EPOCH)
        self.mailbox.poll(GEN, NEXT_EPOCH, False)
        self.assertFalse(self.mailbox.offer_path.exists() or self.mailbox.request_path.exists())
        self.issue.assert_not_called()

    def test_validation_refusals_do_not_reach_the_host_or_change_custody(self):
        invalid = ({"name": "Guest\nAnother row"}, {"character": "a" * 65}, {"address": "0.0.0.0"},
                   {"address": "fe80::1%unsafe zone"}, {"address": "host.test:17777"}, {"address": "https://host.test"})
        for index, field in enumerate(invalid):
            op = f"{index + 1:032x}"
            self.request(operation=op, **field)
            self.mailbox.poll(GEN, EPOCH, True)
            self.response("error", "invalid_address" if "address" in field else "invalid_request", operation=op)
        self.issue.assert_not_called()
        self.request(address="")
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("error", "invalid_address")
        self.assertEqual(self.store.status()["guest_count"], 0)

    def test_private_copy_retries_after_source_error_without_issuing_again(self):
        original = invitation._read_file

        def unavailable(path, limit):
            if path.parent == self.run / "invites":
                raise PermissionError("a credential should never be in an error")
            return original(path, limit)

        self.request()
        with patch.object(invitation, "_read_file", side_effect=unavailable):
            self.mailbox.poll(GEN, EPOCH, True)
        self.response("error", "export_unverified")
        canonical = self.canonical()
        self.assertFalse(self.draft.exists())
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("ok", "exported")
        self.assertEqual(self.draft.read_bytes(), canonical.read_bytes())
        canonical.write_bytes(canonical.read_bytes() + b" ")
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("error", "export_changed")
        self.issue.assert_called_once()

    def test_zone_and_stop_prune_only_bridge_copies_and_never_reissue_old_nonce(self):
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        canonical = self.canonical()
        self.assertTrue(self.draft.exists())
        unowned = self.bridge / ("host-invite-" + "9" * 32 + ".invite.json")
        unowned.write_bytes(b"unrelated bytes")
        self.mailbox.poll(GEN, NEXT_EPOCH, True)
        self.assertFalse(self.draft.exists() or self.mailbox.response_path.exists())
        self.assertTrue(canonical.is_file() and unowned.is_file())
        self.request(epoch=NEXT_EPOCH)
        self.mailbox.poll(GEN, NEXT_EPOCH, True)
        self.response("error", "operation_conflict", epoch=NEXT_EPOCH)
        self.mailbox.close()
        self.assertTrue(canonical.is_file() and unowned.is_file())
        self.assertFalse(self.mailbox.offer_path.exists() or self.mailbox.response_path.exists())
        self.issue.assert_called_once()

    def test_unverified_receipt_never_reads_or_copies_a_foreign_file(self):
        self.issue.side_effect = lambda _: {
            "invite_file": str(self.run.parent / "foreign.invite.json"),
            "guest_id": "07cc990a-3550-4da6-9020-4e882b31b711",
            "character_id": "d50f07d3-2d17-4c40-b752-9aa89a2f0562",
        }
        self.request()
        self.mailbox.poll(GEN, EPOCH, True)
        self.response("error", "export_unverified")
        self.assertFalse(self.draft.exists())
        self.issue.assert_called_once()

    def test_callback_failure_is_redacted_and_never_retried_as_a_new_issue(self):
        self.issue.side_effect = OSError("private callback details")
        for _ in range(2):
            self.request()
            self.mailbox.poll(GEN, EPOCH, True)
            self.response("error", "invite_unavailable")
        self.issue.assert_called_once()
        self.assertFalse(self.draft.exists())


if __name__ == "__main__":
    unittest.main()
