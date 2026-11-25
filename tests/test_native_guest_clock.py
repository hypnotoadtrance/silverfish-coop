import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.silverfish import native_guest_clock as clock, runtime
from tools.silverfish.native_guest_clock import GuestClockView


AREA = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"


class GuestClockTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(
            host_session="host-generation", zone_epoch="a" * 32, gameplay_seen=time.monotonic(),
            last_game={"state": "connected", "map": AREA, "pawns": {"local": {"name": "exact guest pawn"}}},
            gameplay={"host_world": {"native_scene": {"active_map": AREA, "game_seconds": 456.125}}},
            bridge=SimpleNamespace(command=AsyncMock()),
        )

    async def test_arms_only_the_exact_guest_with_authoritative_clock_time(self):
        client = self.client()
        reported = {**client.last_game, "native_guest_clock": {
            "map": AREA, "pawn": "exact guest pawn", "epoch": client.zone_epoch,
            "sequence": 1, "milliseconds": 456125, "state": "armed",
        }}
        client.bridge.command.return_value = reported
        view = GuestClockView()
        await view.sync(client)
        client.bridge.command.assert_awaited_once_with("native-guest-clock-apply", client.zone_epoch, "1",
                                                      AREA, "456125", timeout=2)
        self.assertIs(client.last_game, reported)

    async def test_ignores_stale_or_other_map_clock_frames(self):
        client = self.client()
        view = GuestClockView()
        client.gameplay_seen -= 2
        await view.sync(client)
        client.gameplay_seen = time.monotonic()
        client.gameplay["host_world"]["native_scene"]["active_map"] = "another level"
        await view.sync(client)
        client.bridge.command.assert_not_awaited()

    async def test_compact_clock_reply_keeps_the_current_pawn_and_world_data(self):
        client = self.client()
        client.last_game.update(sequence=42, native_loot_crates={"crate": {"state": "ready"}})
        fields = ("a" * 32, clock.APPLY, client.zone_epoch, "1", AREA, "456125")
        header = f"{clock.REPLY}\t1\t{fields[0]}\t42\taccepted\tclock armed\n"
        proof = f"native-guest-clock\t{AREA}\texact guest pawn\t{client.zone_epoch}\t1\t456125\tarmed\t0\t-\n"
        client.bridge.command.return_value = runtime.Bridge._local_guest_clock_reply(header + proof, fields)
        await GuestClockView().sync(client)
        self.assertEqual(client.last_game[clock.KEY]["milliseconds"], 456125)
        self.assertEqual(client.last_game["native_loot_crates"], {"crate": {"state": "ready"}})
        self.assertEqual(client.last_game["sequence"], 42)
        for wrong in (proof.replace("456125", "416125"), proof.replace(client.zone_epoch, "b" * 32), ""):
            with self.assertRaises(RuntimeError):
                runtime.Bridge._local_guest_clock_reply(header + wrong, fields)

        stale = self.client()
        stale.last_game["sequence"] = 43
        stale.bridge.command.return_value = client.bridge.command.return_value
        await GuestClockView().sync(stale)
        self.assertNotIn(clock.KEY, stale.last_game)
