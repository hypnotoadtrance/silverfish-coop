import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.silverfish import native_gold, native_host_save, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control
from tools.silverfish.service import Host, verify_travel_checkpoint
from tools.silverfish.travel import CROSSROADS, GOLD_NEXUS


CUBE = "0A1B2C3D" * 4
PAWN = "Exact local test pawn"
PLAYER = {"name": PAWN, "health": 250, "location": [0, 0, 150]}
REQUEST = native_gold.GoldRequest(1, CROSSROADS, PAWN, CUBE, "use_held_item", tuple(PLAYER["location"]))


class InterruptedTravelBridge:
    def __init__(self, directory: Path, store: CampaignStore):
        self.directory = directory
        self.store = store
        self.map = CROSSROADS
        self.process = SimpleNamespace(pid=1)
        self.interrupted = False
        self.debit_state = "absent"
        self.native_profile = None
        self.operations = []

    def game(self):
        return {"state": "hosting", "map": self.map, "pawns": {"local": PLAYER}}

    async def command(self, action, *arguments, timeout=8):
        self.operations.append(action)
        if action == "native-host-save-capture" and self.native_profile:
            slot, = arguments
            path = native_host_save.slot_path(self.native_profile, slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"GVAS" + self.map.encode("ascii"))
            return dict(self.game(), native_host_save={"state": "captured", "slot": slot, "map": self.map, "pawn": PAWN})
        if action == "travel":
            assert arguments == (GOLD_NEXUS, CUBE)
            assert native_gold.ledger(self.store.status()["world"]["party"])["pending"]["guid"] == CUBE
            self.map = GOLD_NEXUS
            return self.game()
        if action == "native-exit-arrival":
            assert arguments == (native_gold.ARRIVAL,)
            return self.game()
        assert arguments == (PAWN, CUBE)
        operation = action.removeprefix("native-gold-")
        if operation == "debit":
            world = self.store.status()["world"]
            assert world["map"] == CROSSROADS
            assert native_gold.ledger(world["party"])["pending"]["guid"] == CUBE
            if self.interrupted:
                raise OSError("connection to game lost during the debit")
        return dict(self.game(), native_gold={
            "operation": operation, "state": "present" if operation == "inspect" else self.debit_state,
            "map": self.map, "pawn": PAWN, "guid": CUBE,
        })

    async def wait_for(self, predicate, timeout):
        snapshot = self.game()
        assert predicate(snapshot)
        return snapshot


class GoldenCubeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.control = self.enterContext(Control(self.directory / "run"))

    def host(self, store, bridge):
        controller = Host(store, self.control, "build", None, "127.0.0.1", 1, False, None,
                          native_backend="cpp", native_events=False, native_guest=False,
                          native_zones=True, authored_zones=True)
        controller.internal_port = 1
        controller.last_game = bridge.game()
        controller.last_seen = time.monotonic()
        party.add_player(controller.party, "host")
        return controller

    def test_existing_mismatched_primary_loads_the_last_recoverable_map(self):
        save = self.directory / "save"
        pending = native_gold.begin(party.load_party(None), CUBE, CROSSROADS)
        with CampaignStore(save) as old_host_store:
            revision = old_host_store.save_world({"map": CROSSROADS, "party": pending})
            old_host_store.save_world({"map": GOLD_NEXUS, "party": pending})
        with CampaignStore(save, verify_world=verify_travel_checkpoint) as restored:
            self.assertTrue(restored.recovered)
            self.assertEqual(restored.status()["revision"], revision)
            world = restored.status()["world"]
            self.assertEqual(world["map"], CROSSROADS)
            self.assertEqual(native_gold.ledger(world["party"])["pending"]["guid"], CUBE)

    async def test_travel_recovers_committed_debit_after_restart_and_rejects_replay(self):
        save = self.directory / "save"
        with CampaignStore(save) as store:
            interrupted = InterruptedTravelBridge(self.directory, store)
            interrupted.interrupted = True
            host = self.host(store, interrupted)

            with self.assertRaisesRegex(OSError, "lost during the debit"):
                await host.activate_gold(interrupted, None, REQUEST)

            self.assertTrue(host.stopping)
            self.assertEqual(interrupted.operations, ["native-gold-inspect", "native-gold-debit"])

        with CampaignStore(save) as store:
            recovered = InterruptedTravelBridge(self.directory, store)
            host = self.host(store, recovered)
            with patch("tools.silverfish.service.verify_loopback_socket"):
                await host.resume_gold(recovered, None, recovery=True)

            world = store.status()["world"]
            self.assertEqual(world["map"], GOLD_NEXUS)
            self.assertEqual(native_gold.ledger(world["party"]), {"pending": None, "spent": {CUBE: "arrived"}})
            self.assertEqual(recovered.operations, ["native-gold-debit", "travel", "native-exit-arrival"])

            recovered.map = CROSSROADS
            host.last_game = recovered.game()
            result = await host.activate_gold(recovered, None, REQUEST)
            self.assertEqual(result, {"ok": False, "error": "gold_already_spent"})
            self.assertEqual(len(recovered.operations), 3)

    async def test_distant_party_and_direct_route_cannot_consume_the_item(self):
        with CampaignStore(self.directory / "save") as store:
            bridge = InterruptedTravelBridge(self.directory, store)
            host = self.host(store, bridge)
            denied = await host.travel(bridge, None, GOLD_NEXUS)
            self.assertEqual(denied["error"], "authored_exit_required")

            host.guest = {"character_id": "guest-character", "disconnected": None}
            host.guest_pawn = "exact guest pawn"
            host.last_game["pawns"]["guest"] = dict(PLAYER, location=[1600, 0, 150])
            result = await host.activate_gold(bridge, None, REQUEST)
            self.assertEqual(result, {
                "ok": False, "error": "travel_blocked", "blockers": [{"id": "guest-character", "reason": "too_far"}],
            })
            self.assertEqual(native_gold.ledger(host.party), {"pending": None, "spent": {}})
            self.assertEqual(bridge.operations, [])

    async def test_cancelled_destination_reopen_preserves_recoverable_source_checkpoint(self):
        save = self.directory / "save"
        profile = self.directory / "game-user"

        def verify(checkpoint):
            native_host_save.verify_checkpoint(profile, checkpoint)

        reached_destination = asyncio.Event()

        async def wait_to_reopen_routes():
            reached_destination.set()
            await asyncio.Future()

        with CampaignStore(save, verify_checkpoint=verify) as store:
            bridge = InterruptedTravelBridge(self.directory, store)
            host = self.host(store, bridge)
            bridge.native_profile = host.native_profile = profile
            bridge.debit_state = "removed"
            transport = SimpleNamespace(refresh_game_routes=wait_to_reopen_routes)
            with patch("tools.silverfish.service.verify_loopback_socket"):
                departure = asyncio.create_task(host.activate_gold(bridge, transport, REQUEST))
                try:
                    await asyncio.wait_for(reached_destination.wait(), timeout=2)
                finally:
                    departure.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await departure

            self.assertEqual(host.last_game["map"], GOLD_NEXUS)
            source = store.status()["world"]
            self.assertEqual(source["map"], CROSSROADS)
            self.assertEqual(native_gold.ledger(source["party"])["pending"]["guid"], CUBE)
            self.assertFalse(host.native_checkpoint_unsafe)
            with self.assertRaisesRegex(RuntimeError, "committed item travel"):
                await host.checkpoint_native(bridge)
            self.assertEqual(store.status()["world"], source)

        with CampaignStore(save, verify_checkpoint=verify) as store:
            self.assertEqual(store.status()["world"], source)
            bridge = InterruptedTravelBridge(self.directory, store)
            host = self.host(store, bridge)
            bridge.native_profile = host.native_profile = profile
            with patch("tools.silverfish.service.verify_loopback_socket"):
                result = await host.resume_gold(bridge, None, recovery=True)

            self.assertTrue(result["ok"])
            world = store.status()["world"]
            self.assertEqual(world["map"], GOLD_NEXUS)
            self.assertEqual(world["native_host_save"]["map"], GOLD_NEXUS)
            self.assertEqual(native_gold.ledger(world["party"]), {"pending": None, "spent": {CUBE: "arrived"}})
