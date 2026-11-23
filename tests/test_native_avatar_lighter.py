import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from tools.silverfish import native_player_avatar as avatar, service
from tools.silverfish.travel import OBSERVATORY


class LighterOutfitTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_the_exact_current_guest_lighter_accepts_a_visual_edge(self):
        meshes = [
            f"/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/{name}.{name}"
            for name in ("SM_Item_Lighter_Flip_01", "SM_Item_Lighter_Flip_Lid_01")
        ]
        pose = "@0.00@0.00@0.00@0.00@0.00@0.00@1.00@1.00@1.00"
        shape = "|".join("s@" + path + pose for path in meshes)
        pawn = f"Player_FP_C {OBSERVATORY}.Observitory_P:PersistentLevel.Player_FP_C_3"
        newcomer = {"connected": True, "pawn": pawn}
        player = {"downed": False}
        host = SimpleNamespace(zone_epoch="a" * 32, party={"players": {"guest": player}},
                               native_recipients=lambda: {"guest": newcomer},
                               guest_avatar_aims={}, guest_avatar_lighters={})
        host.last_game = {"state": "hosting", "map": OBSERVATORY, avatar.KEY: {
            "3": {"pawn": pawn, "weapon": shape + "~closed"}}}

        with patch.object(avatar.time, "monotonic", return_value=10.0):
            avatar.guest_lighter(host, "guest", {"pawn": "unrelated pawn"}, "lit")
            self.assertEqual(host.guest_avatar_lighters, {})
            avatar.guest_lighter(host, "guest", newcomer, "lit")
            incoming = avatar.frame(host)
        self.assertEqual(incoming["outfits"]["3"]["weapon"], shape)
        self.assertEqual(incoming["lighter_modes"], {"3": "lit"})
        self.assertEqual(avatar.weapon_meshes(shape + "~lit"), meshes)
        self.assertIsNone(avatar.weapon_meshes("s@" + meshes[0] + pose + "~lit"))

        snapshot = {"state": "connected", "map": OBSERVATORY}
        bridge = SimpleNamespace(command=AsyncMock(return_value={"ok": True}))
        self.assertEqual(await avatar.project(bridge, snapshot, incoming, host.zone_epoch), {"ok": True})
        self.assertIn(shape + "~lit", bridge.command.call_args.args)

        incoming["lighter_modes"] = {"9": "lit"}
        bridge.command.reset_mock()
        self.assertIs(await avatar.project(bridge, snapshot, incoming, host.zone_epoch), snapshot)
        bridge.command.assert_not_awaited()
        with patch.object(avatar.time, "monotonic", return_value=12.0):
            self.assertEqual(avatar.frame(host)["lighter_modes"], {"3": "closed"})
            self.assertFalse(host.guest_avatar_lighters)

    async def test_lighter_edges_reuse_the_negotiated_snapshot_without_legacy_requests(self):
        request = AsyncMock(return_value={"ok": True})
        connection = SimpleNamespace(address=("127.0.0.1", 7777), request=request, session_end=None)
        client = service.Client(Mock(), connection, {"character_id": "guest"}, Mock())
        self.addAsyncCleanup(client.cancel_gameplay_snapshot)
        client.zone_epoch, client.expected_zone = "a" * 32, OBSERVATORY
        client.last_game = {"state": "connected", "map": OBSERVATORY, avatar.LOCAL_LIGHTER: "lit"}
        client._schedule_gameplay_snapshot(edges_only=True)
        await asyncio.sleep(0)
        request.assert_not_awaited()

        client.gameplay = {avatar.KEY: {"lighter_modes": {}}}
        client._schedule_gameplay_snapshot(edges_only=True)
        await asyncio.sleep(0)
        request.assert_awaited_once_with({"action": "snapshot", "zone_epoch": client.zone_epoch,
                                         avatar.GUEST_LIGHTER: "lit"}, timeout=2)
        await client.cancel_gameplay_snapshot()
        del client.last_game[avatar.LOCAL_LIGHTER]
        client._schedule_gameplay_snapshot(edges_only=True)
        await asyncio.sleep(0)
        self.assertEqual(request.await_args.args[0], {"action": "snapshot", "zone_epoch": client.zone_epoch,
                                                      avatar.GUEST_LIGHTER: "-"})
