import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_player_avatar as avatar, service
from tools.silverfish.travel import OBSERVATORY


VISOR = "/Game/Classes/Armor_Items/Helm_TacLight.Helm_TacLight_C"


class VisorProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_power_requires_same_helmet_and_clears_on_swap(self):
        pawn = f"Player_FP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_3"
        member = {"connected": True, "pawn": pawn}
        game = {"state": "hosting", "map": OBSERVATORY, avatar.KEY: {
            "3": {"pawn": pawn, "head": VISOR, "weapon": "-"},
        }}
        host = SimpleNamespace(last_game=game, zone_epoch="a" * 32, party={"players": {"guest": {"downed": False}}},
                               native_recipients=lambda: {"guest": member}, guest_avatar_aims={}, guest_avatar_visors={})
        bridge = SimpleNamespace(command=AsyncMock(return_value=game))
        with patch.object(avatar.time, "monotonic", return_value=10.0):
            avatar.guest_visor(host, "guest", member, VISOR)
            incoming = avatar.frame(host)
            self.assertEqual(incoming["visor_players"], ["3"])
            await avatar.project_host(host, bridge)
            self.assertEqual(bridge.command.await_args.args[3:], ("visor", "3"))
            game[avatar.KEY]["3"]["head"] = "-"
            self.assertEqual(avatar.frame(host)["visor_players"], [])
            avatar.guest_visor(host, "guest", member, VISOR)
            self.assertFalse(host.guest_avatar_visors)

        incoming["visor_players"] = ["4"]
        bridge.command.reset_mock()
        view = {"state": "connected", "map": OBSERVATORY}
        self.assertIs(await avatar.project(bridge, view, incoming, host.zone_epoch), view)
        bridge.command.assert_not_awaited()

    async def test_guest_sends_power_and_clear_edges_only_with_negotiated_support(self):
        request = AsyncMock(return_value={"ok": True})
        connection = SimpleNamespace(address=("127.0.0.1", 7777), request=request, session_end=None)
        client = service.Client(Mock(), connection, {"character_id": "guest"}, Mock())
        self.addAsyncCleanup(client.cancel_gameplay_snapshot)
        client.zone_epoch, client.expected_zone = "a" * 32, OBSERVATORY
        client.last_game = {"state": "connected", "map": OBSERVATORY, avatar.LOCAL_VISOR: VISOR}
        client._schedule_gameplay_snapshot(edges_only=True)
        await asyncio.sleep(0)
        request.assert_not_awaited()

        client.gameplay = {avatar.KEY: {"visor_players": []}}
        for path in (VISOR, "-"):
            if path == "-":
                del client.last_game[avatar.LOCAL_VISOR]
            client._schedule_gameplay_snapshot(edges_only=True)
            await asyncio.sleep(0)
            self.assertEqual(request.await_args.args[0], {
                "action": "snapshot", "zone_epoch": client.zone_epoch, avatar.GUEST_VISOR: path,
            })
            await client.cancel_gameplay_snapshot()
