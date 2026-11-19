import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.silverfish import native_gold, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import Control
from tools.silverfish.service import Host
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
        self.operations = []

    def game(self):
        return {"state": "hosting", "map": self.map, "pawns": {"local": PLAYER}}

    async def command(self, action, *arguments, timeout=8):
        self.operations.append(action)
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
            "operation": operation, "state": "present" if operation == "inspect" else "absent",
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
