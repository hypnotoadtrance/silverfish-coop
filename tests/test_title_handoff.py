from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.silverfish import native_title_entry
from tools.silverfish.title_handoff import Claim, HostDefaults, TitleHandoff


def ready(sequence=1):
    title_map = native_title_entry.MAP
    row = native_title_entry.parse([
        native_title_entry.TAG, title_map, "ready",
        f"PlayerControler_FP_C {title_map}.MainMenu_P:PersistentLevel.PlayerControler_FP_C_1",
        "MainMenu_Widget_C /Engine/Transient.GameEngine_1:GameInstance_FP_C_1.MainMenu_Widget_C_1",
        "GameViewportClient /Engine/Transient.GameEngine_1:GameViewportClient_1",
        "1", "1", "1", "1", "-",
    ], "ready", title_map)
    return dict(schema=1, sequence=sequence, state="ready", map=title_map,
                mode="idle", remote_clients=0, server_connection=False, native_title_entry=row)


class TitleHandoffTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.now = 1_700_000_000
        self.handoff = TitleHandoff(Path(directory), clock=lambda: self.now)
        self.host = object()
        self.join = object()

    def descriptor(self):
        return self.handoff.descriptor_path.read_bytes().decode("ascii").rstrip("\n").split("\t")

    def publish(self, bindings, sequence=1):
        self.assertTrue(self.handoff.refresh(ready(sequence), bindings))
        version, generation, _, _ = self.descriptor()
        self.assertEqual(version, "1")
        self.assertRegex(generation, r"^[a-f0-9]{32}$")
        return generation

    def submit(self, generation, *, action="host", request_id="a" * 32, ending=b"\n"):
        self.handoff.request_path.write_bytes(f"1\t{generation}\t{request_id}\t{action}".encode("ascii") + ending)

    def assert_refused(self, code, snapshot):
        self.assertIsNone(self.handoff.poll(snapshot))
        self.assertEqual(self.handoff.last_refusal, code)
        self.assertFalse(self.handoff.request_path.exists())

    def test_advertises_only_supervisor_bound_actions_and_removes_empty_offer(self):
        self.assertFalse(self.handoff.refresh(ready(), {}))
        self.assertFalse(self.handoff.descriptor_path.exists())
        binding = {"credential": "supervisor-only", "port": 27014}
        generation = self.publish({"join": binding})
        self.assertEqual(self.descriptor(), ["1", generation, "join", str(self.now + 10)])
        self.assertFalse(self.handoff.refresh(ready(2), {}))
        self.assertFalse(self.handoff.descriptor_path.exists())
        self.assertNotIn("supervisor-only", repr(Claim("join", "a" * 32, generation, binding)))

    def test_resident_host_offer_publishes_canonical_defaults_on_each_renewal(self):
        bindings = {"host": self.host, "join": self.join}
        defaults = HostDefaults("Campaign_One", "2001:DB8:0:0::10", 27890)
        self.assertTrue(self.handoff.refresh(ready(), bindings, host_defaults=defaults))
        generation = self.handoff.generation
        self.assertEqual(self.descriptor(),
                         ["2", generation, "host,join", str(self.now + 10), "Campaign_One", "2001:db8::10", "27890"])
        self.now += 7
        self.assertTrue(self.handoff.refresh(ready(2), bindings, host_defaults=defaults))
        self.assertEqual(self.descriptor(),
                         ["2", generation, "host,join", str(self.now + 10), "Campaign_One", "2001:db8::10", "27890"])
        self.submit(generation)
        self.assertIsNone(self.handoff.poll(ready(3)))
        self.assertEqual(self.handoff.poll(ready(4)), Claim("host", "a" * 32, generation, self.host))

    def test_changed_defaults_retire_the_old_host_offer_and_join_only_stays_v1(self):
        defaults = HostDefaults("Original", "0.0.0.0", 7777)
        self.assertTrue(self.handoff.refresh(ready(), {"host": self.host}, host_defaults=defaults))
        original = self.handoff.generation
        updated = HostDefaults("Updated", "127.0.0.1", 27890)
        self.assertTrue(self.handoff.refresh(ready(2), {"host": self.host}, host_defaults=updated))
        replacement = self.handoff.generation
        self.assertNotEqual(original, replacement)
        self.assertEqual(self.descriptor(),
                         ["2", replacement, "host", str(self.now + 10), "Updated", "127.0.0.1", "27890"])
        self.submit(original)
        self.assert_refused("stale_generation", ready(3))
        self.assertTrue(self.handoff.refresh(ready(4), {"join": self.join}, host_defaults=updated))
        self.assertEqual(self.descriptor(), ["1", self.handoff.generation, "join", str(self.now + 10)])

    def test_host_defaults_refuse_invalid_fields(self):
        invalid = [
            ("", "0.0.0.0", 7777), ("name with space", "127.0.0.1", 7777),
            ("Default", "localhost", 7777), ("Default", "fe80::1%3", 7777),
            ("Default", "0.0.0.0", False), ("Default", "0.0.0.0", 0),
            ("Default", "0.0.0.0", 65536),
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                HostDefaults(*fields)

    def test_claim_requires_later_exact_ready_frame_and_is_one_use(self):
        generation = self.publish({"join": self.join, "host": self.host})
        self.assertEqual(self.descriptor()[2], "host,join")
        self.submit(generation, action="join", ending=b"\r\n")
        self.assertIsNone(self.handoff.poll(ready(2)))
        self.assertIsNone(self.handoff.poll(None))
        self.assertIsNone(self.handoff.poll(ready(2)))
        self.assertTrue(self.handoff.request_path.is_file())
        self.assertEqual(self.handoff.poll(ready(3)), Claim("join", "a" * 32, generation, self.join))
        self.assertFalse(self.handoff.request_path.exists())
        self.assertFalse(self.handoff.descriptor_path.exists())
        self.submit(generation, action="host")
        self.assert_refused("duplicate_request", ready(4))
        self.submit(generation, request_id="b" * 32)
        self.assert_refused("handoff_consumed", ready(5))
        self.assertFalse(self.handoff.refresh(ready(6), {"host": self.host}))

    def test_local_choice_shares_the_stock_mailbox_and_cannot_replace_it(self):
        generation = self.publish({"host": self.host, "join": self.join})
        self.submit(generation, action="join")
        physical = self.handoff.request_path.read_bytes()
        self.assertEqual(self.handoff.queue_local(ready(2), generation, "b" * 32, "host"), "selection_pending")
        self.assertEqual(self.handoff.request_path.read_bytes(), physical)
        self.assertIsNone(self.handoff.poll(ready(2)))
        self.assertEqual(self.handoff.poll(ready(3)), Claim("join", "a" * 32, generation, self.join))

    def test_version_two_carries_bounded_host_settings_with_the_same_frame_and_one_use_gate(self):
        generation = self.publish({"host": self.host, "join": self.join})
        prefix = f"2\t{generation}\t{'a' * 32}\t".encode("ascii")
        for data in (b"join\t\t\t\n", b"host\tpath/file\t127.0.0.1\t7777\n",
                     b"host\t" + b"c" * 65 + b"\t\t\n", b"host\t\t\t123456\n"):
            with self.subTest(data=data):
                self.handoff.request_path.write_bytes(prefix + data)
                self.assert_refused("malformed_request", ready(2))
        self.handoff.request_path.write_bytes(prefix + b"host\tprivate_campaign\tfe80::1%3\t7777\n")
        self.assertIsNone(self.handoff.poll(ready(2)))
        self.assertIsNone(self.handoff.poll(ready(2)))
        settings = "private_campaign", "fe80::1%3", "7777"
        claim = self.handoff.poll(ready(3))
        self.assertEqual(claim, Claim("host", "a" * 32, generation, self.host, settings))
        self.assertNotIn(settings[0], repr(claim))

    def test_version_two_join_uses_only_the_opaque_invite_and_exact_character(self):
        generation = self.publish({"join": self.join})
        invite = "b" * 32
        character = "fc8fa10f-540b-4b9f-babe-fd4082a78f6d"
        prefix = f"2\t{generation}\t{'a' * 32}\tjoin\t"
        for data in (f"share.invite.json\tguest\t{character}", f"{invite}\t../guest\t{character}",
                     f"{invite}\tguest\t{character.upper()}", f"{invite}\tguest\t{character}\tsecret"):
            with self.subTest(data=data):
                self.handoff.request_path.write_bytes((prefix + data + "\n").encode("ascii"))
                self.assert_refused("malformed_request", ready(2))
        self.handoff.request_path.write_bytes((prefix + f"{invite}\tguest\t{character}\n").encode("ascii"))
        self.assertIsNone(self.handoff.poll(ready(2)))
        claim = self.handoff.poll(ready(3))
        self.assertEqual(claim, Claim("join", "a" * 32, generation, self.join,
                                      join_settings=(invite, "guest", character)))
        self.assertNotIn(invite, repr(claim))
        self.assertNotIn(character, repr(claim))

    def test_steam_host_carries_access_and_password_without_a_network_endpoint(self):
        generation = self.publish({"host": self.host})
        prefix = f"6\t{generation}\t{'a' * 32}\thost\t".encode("ascii")
        for suffix in (b"\tunknown\tsecret\tcreator\n", b"\tinvite\tsecret\tresume\n",
                       b"campaign\tinvite\t\xff\tresume\n"):
            self.handoff.request_path.write_bytes(prefix + suffix)
            self.assert_refused("malformed_request", ready(2))
        self.handoff.request_path.write_bytes(prefix + b"campaign\tpublic\tsecret\tresume\n")
        self.assertIsNone(self.handoff.poll(ready(2)))
        claim = self.handoff.poll(ready(3))
        self.assertEqual(claim, Claim("host", "a" * 32, generation, self.host,
                                      host_settings=("campaign", "", ""), direct_password="secret",
                                      stock_resume=True, steam_access="public"))
        self.assertNotIn("secret", repr(claim))

    def test_resident_host_friendly_fire_flag_is_explicit_for_either_transport(self):
        for version, values, expected in (
            (b"3", b"campaign\t127.0.0.1\t7777\tpass", True),
            (b"4", b"campaign\t127.0.0.1\t7777\t\tcreator", True),
            (b"5", b"campaign\t127.0.0.1\t7777\t\tresume", False),
            (b"6", b"campaign\tfriends\tpass\tresume", True),
        ):
            with self.subTest(version=version):
                self.handoff = TitleHandoff(self.handoff.request_path.parent, clock=lambda: self.now)
                generation = self.publish({"host": self.host})
                envelope = f"\t{generation}\t{'a' * 32}\thost\t".encode("ascii")
                prefix = version + envelope + values
                self.handoff.request_path.write_bytes(prefix + b"\t0\n")
                self.assert_refused("malformed_request", ready(2))
                self.handoff.request_path.write_bytes(prefix + (b"\t1\n" if expected else b"\n"))
                self.assertIsNone(self.handoff.poll(ready(2)))
                self.assertIs(self.handoff.poll(ready(3)).friendly_fire, expected)

    def test_rejects_stale_generation_and_unadvertised_action(self):
        original = self.publish({"join": self.join})
        current = self.publish({"host": self.host}, sequence=2)
        self.assertNotEqual(original, current)
        self.submit(original, action="join")
        self.assert_refused("stale_generation", ready(3))
        self.submit(current, action="join")
        self.assert_refused("unadvertised_action", ready(3))
        self.assertEqual(self.descriptor(), ["1", current, "host", str(self.now + 10)])

    def test_rejects_payloads_and_noncanonical_request_tokens(self):
        generation = self.publish({"host": self.host})
        valid = f"1\t{generation}\t{'a' * 32}\thost".encode("ascii")
        invalid = (
            valid + b"\tC:\\config\\save.sav\n",
            valid + b"\tinvite_token=secret\n",
            valid + b" --port 1234\n",
            valid + b"\n" + valid + b"\n",
            valid.replace(b"a" * 32, b"A" * 32) + b"\n",
            valid.replace(b"host", b"load") + b"\n",
            valid + b"\xff\n",
            valid,
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                self.handoff.request_path.write_bytes(payload)
                self.assert_refused("malformed_request", ready(2))
        self.assertEqual(self.descriptor()[1], generation)

    def test_refuses_pending_choice_when_stock_title_changes(self):
        generation = self.publish({"host": self.host})
        self.submit(generation)
        self.assertIsNone(self.handoff.poll(ready(2)))
        changed = ready(3)
        changed["native_title_entry"]["widget"] += "_reopened"
        self.assert_refused("stock_title_unready", changed)
        self.assertFalse(self.handoff.descriptor_path.exists())

    def test_only_standalone_mounted_stock_title_can_publish(self):
        for key, invalid in (("schema", 2), ("mode", "client"), ("state", "opening"),
                             ("map", "Observatory_P"), ("remote_clients", 1), ("server_connection", True)):
            with self.subTest(key=key):
                self.assertFalse(self.handoff.refresh(ready() | {key: invalid}, {"host": self.host}))
        for key, invalid in (("state", "waiting"), ("mounted", False), ("top", False), ("visible", False),
                             ("menu_stack", 0), ("viewport", "OtherViewport /Engine/Transient.Viewport")):
            with self.subTest(key=key):
                snapshot = ready()
                snapshot["native_title_entry"][key] = invalid
                self.assertFalse(self.handoff.refresh(snapshot, {"host": self.host}))
        self.assertFalse(self.handoff.descriptor_path.exists())

    def test_renewal_requires_advancing_stock_title_status(self):
        generation = self.publish({"host": self.host})
        self.now += 7
        self.assertTrue(self.handoff.refresh(ready(), {"host": self.host}))
        self.assertEqual(int(self.descriptor()[3]), self.now + 3)
        self.assertTrue(self.handoff.refresh(ready(2), {"host": self.host}))
        self.assertEqual(self.descriptor(), ["1", generation, "host", str(self.now + 10)])
        self.submit(generation)
        self.now += 11
        self.assert_refused("expired_generation", ready(3))
        self.assertFalse(self.handoff.descriptor_path.exists())
        self.assertNotEqual(self.publish({"host": self.host}, sequence=4), generation)

    def test_bad_binding_fails_before_descriptor_is_published(self):
        for bindings in ({"travel": self.host}, {"host": None}):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                self.handoff.refresh(ready(), bindings)
        self.assertFalse(self.handoff.descriptor_path.exists())
