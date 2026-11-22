import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.silverfish import native_host_save, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.restart_wipe_guard import KEY, RestartWipeGuard
from tools.silverfish.service import Host, PROTOCOL
from tools.silverfish.travel import CROSSROADS, SCIENTIST_SHOP


class RestartWipeGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.profile = self.root / "GameProfile"
        native_host_save.directory(self.profile).mkdir(parents=True)
        self.wall = 10_000.0
        self.clock = 1_000.0

    def open_store(self, name="campaign"):
        return CampaignStore(
            self.root / name, clock=lambda: self.clock,
            verify_checkpoint=lambda data: native_host_save.verify_checkpoint(self.profile, data),
        )

    def test_native_health_limits_keep_the_restored_host_downed_and_match_stock_revive(self):
        host = Mock(native_guest=True, native_zones=True, native_profile=self.profile)
        host.party = party.load_party(None)
        party.add_player(host.party, "host")
        party.add_player(host.party, "traveler")
        host.party["players"]["host"].update(health=0, downed=True)
        host.last_game = {"state": "hosting", "pawns": {
            "local": {"max_health": 250, "health": 250}, "guest": {"name": "OtherPawn", "max_health": 400, "health": 350},
        }}
        host.native_recipients.return_value = {"traveler": {"alias": "guest", "pawn": "TravelersPawn"}}

        Host.sync_native_health_limits(host)

        self.assertEqual(host.party["players"]["host"]["health"], 0)
        self.assertEqual(host.party["players"]["host"]["max_health"], 250)
        self.assertTrue(host.party["players"]["host"]["downed"])
        self.assertEqual(host.party["players"]["traveler"]["max_health"], party.BASE_MAX_HEALTH)
        self.assertNotIn("traveler", host.native_health_readback[2])

    def guard(self, status):
        return RestartWipeGuard(status, wall=lambda: self.wall, clock=lambda: self.clock)

    def frame(self, number, character=None):
        slot = f"SFCoop_{number:032x}"
        native_host_save.slot_path(self.profile, slot).write_bytes(b"GVAS" + bytes((number,)) * 20)
        frame = native_host_save.record(self.profile, slot, SCIENTIST_SHOP)
        if character:
            frame["character_id"] = character
        return frame

    def checkpoint(self, store, *, standing=True, paired=True, expire_to=None, host_downed=True):
        invitation = store.issue_invitation("Scout", "Returning Scout")
        alternate = store.create_character(
            invitation["guest_id"], invitation["credential"], "Different Character",
        )["character_id"]
        outsider = store.issue_invitation("New Guest", "Fresh Character")
        active = store.activate(**invitation)
        if expire_to is not None:
            store.disconnect(active["lease_id"], grace_seconds=expire_to)
        character = invitation["character_id"]
        game_party = party.new_party()
        for player in ("host", character):
            party.add_player(game_party, player)
        game_party["players"]["host"].update(downed=host_downed, health=0 if host_downed else 250)
        game_party["players"][character].update(downed=not standing, health=250 if standing else 0)
        game_party["checkpoint"] = {"map": CROSSROADS, "location": [10, 20, 30]}
        world = {"map": SCIENTIST_SHOP, "party": game_party, "native_host_save": self.frame(1)}
        guest_frame = self.frame(2, character)
        guest_state = {"map": SCIENTIST_SHOP, "health": 250 if standing else 0, "native_guest_save": guest_frame,
                       "location": [20, 0, 0]}
        changes = {active["lease_id"]: guest_state}
        status = store.status()
        self.guard(status).checkpoint(status, world, {"host", character}, {character: {}},
                                      paired=paired, captures={character: guest_frame}, changes=changes, unleased={})
        store.checkpoint(world, changes)
        return invitation, alternate, outsider

    def make_host(self, store):
        host = Host(
            store, Mock(generation="current-host-run"), "pinned", None, "127.0.0.1", 12345, False, None,
            native_backend="cpp", native_events=True, native_guest=True, native_zones=True,
            native_profile=self.profile, verify_native_roster=True, persist_native_guest=True,
            exact_guest_binding=True,
        )
        host.restart_wipe_guard = self.guard(store.status())
        host.last_game = {
            "state": "hosting", "mode": "host", "map": SCIENTIST_SHOP, "server_connection": False,
            "sequence": 1, "remote_clients": 0,
            "native_roster": {"state": "qualified", "driver": "d1", "count": 0, "connections": []},
            "pawns": {"local": {"name": "stock host pawn", "location": [0, 0, 0], "health": 0}},
        }
        return host

    @staticmethod
    def request(invitation, **override):
        return {**invitation, "protocol_version": PROTOCOL, "game_build": "pinned", **override}

    async def test_persisted_standing_guest_defers_wipe_until_exact_rebind(self):
        with self.open_store() as store:
            invitation, alternate, outsider = self.checkpoint(store)
            ticket, = store.status()["world"][KEY]["tickets"]
            self.assertEqual(ticket, {"guest_id": invitation["guest_id"], "character_id": invitation["character_id"],
                                      "slot": f"SFCoop_{2:032x}", "expires": self.wall + 90})

        self.wall += 5
        self.clock += 5
        with self.open_store() as store:
            self.assertEqual(store.status()["leases"], [])
            host = self.make_host(store)
            revision = store.status()["revision"]
            await host.settle_wipe(None, None)
            self.assertIsNone(host.party["wipe"])
            self.assertEqual(store.status()["revision"], revision)
            self.assertEqual(host.roles(), {"host": "host"})
            self.assertEqual(host.native_recipients(), {})
            snapshot = host.last_game
            host.last_game = {"state": "loading"}
            await host.settle_wipe(None, None)
            self.assertIsNone(host.party["wipe"])
            host.last_game = snapshot
            self.assertEqual(host.admit(self.request(outsider))["reason"], "slot_reserved")
            self.assertEqual(host.admit(self.request(invitation, character_id=alternate))["reason"], "slot_reserved")
            self.assertFalse(host.admit(self.request(invitation, credential="incorrect"))["accepted"])
            admitted = host.admit(self.request(invitation))
            self.assertTrue(admitted["accepted"], admitted)
            self.assertFalse(admitted["resumed"])  # Authentication still creates a real new process lease.
            await host.settle_wipe(None, None)
            self.assertEqual(host.roles(), {"host": "host"})
            self.assertIsNone(host.party["wipe"])
            self.assertEqual(host.guest["restore"]["native_guest_save"]["slot"], ticket["slot"])

            # Mirror the result after the normal enrollment has verified the restored generation.
            host.guest_pawn = "new exact guest pawn"
            host.last_game["native_guest_save"] = {"state": "bound", "pawn": host.guest_pawn}
            self.assertEqual(host.roles()["guest"], invitation["character_id"])
            await host.settle_wipe(None, None)
            self.assertEqual(host.restart_wipe_guard.waiting, {})
            self.assertIsNone(host.party["wipe"])

    async def test_grace_remaining_survives_restarts_without_reset(self):
        with self.open_store() as store:
            invitation, _, _ = self.checkpoint(store, expire_to=12)
            original = store.status()["world"][KEY]

        self.wall += 9
        self.clock += 9
        with self.open_store() as store:
            host = self.make_host(store)
            await host.settle_wipe(None, None)
            host.checkpoint(native_record=self.frame(3))
            self.assertEqual(store.status()["world"][KEY], original)
            self.assertEqual(store.status()["leases"], [])

        self.wall += 3
        self.clock += 3
        with self.open_store() as store:
            host = self.make_host(store)
            self.assertEqual(host.wipe_roster(host.roles()), ("host",))
            draft = party.begin_when_all_down(host.party, host.wipe_roster(host.roles()), {}, self.wall)
            self.assertEqual(draft["roster"], ["host"])
            self.assertNotIn(invitation["character_id"], draft["roster"])
            self.assertEqual(draft["checkpoint"], {"map": CROSSROADS, "location": [10, 20, 30]})

    async def test_logical_host_down_commit_can_reuse_recent_paired_guest_frame(self):
        with self.open_store() as store:
            invitation, _, _ = self.checkpoint(store, host_downed=False)
            status = store.status()
            self.assertEqual(self.guard(status).waiting, {})
            world = copy.deepcopy(status["world"])
            world["party"]["players"]["host"].update(downed=True, health=0)
            self.guard(status).checkpoint(status, world, {"host", invitation["character_id"]}, {},
                                          paired=False, captures={}, changes={}, unleased={})
            self.assertEqual(world[KEY], status["world"][KEY])
            store.checkpoint(world, {})

        with self.open_store() as store:
            host = self.make_host(store)
            await host.settle_wipe(None, None)
            self.assertIsNone(host.party["wipe"])

    def test_missing_standing_pair_or_exact_frame_never_reserves_a_slot(self):
        for option in ("paired", "standing"):
            with self.subTest(option=option), self.open_store(option) as store:
                self.checkpoint(store, **{option: False})
                status = store.status()
                self.assertNotIn(KEY, status["world"])
                self.assertEqual(self.guard(status).waiting, {})

        with self.open_store() as store:
            self.checkpoint(store)
            for change in ("old_schema", "wrong_slot", "wrong_guest", "existing_wipe"):
                with self.subTest(change=change):
                    status = store.status()
                    world = status["world"]
                    ticket, = world[KEY]["tickets"]
                    if change == "old_schema":
                        del world[KEY]
                    elif change == "wrong_slot":
                        ticket["slot"] = f"SFCoop_{9:032x}"
                    elif change == "wrong_guest":
                        ticket["guest_id"] = "c04e6ce9-9fc7-40a1-8d4e-ba49a0cd0cd1"
                    else:
                        world["party"]["wipe"] = {"id": "already-decided"}
                    self.assertEqual(self.guard(status).waiting, {})

    def test_multi_capacity_reservation_uses_identity_once_and_expires_monotonically(self):
        with self.open_store() as store:
            invitation, _, outsider = self.checkpoint(store, expire_to=20)
            status = store.status()
            world = status["world"]
            guard = self.guard(status)
            scout, fresh = invitation["guest_id"], outsider["guest_id"]
            scout_character, fresh_character = invitation["character_id"], outsider["character_id"]
            self.assertIsNone(guard.admission_refusal(world, {}, [], 2, fresh, fresh_character))
            self.assertIsNone(guard.admission_refusal(world, {}, status["leases"], 2, fresh, fresh_character))
            self.assertEqual(guard.admission_refusal(world, {}, status["leases"], 1, fresh, fresh_character),
                             "slot_reserved")
            self.assertIsNone(guard.admission_refusal(world, {}, status["leases"], 1, scout, scout_character))
            self.wall -= 10
            self.clock += 20
            self.assertEqual(guard.pending(world), {})
