import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_wallet, party
from tools.silverfish.travel import OBSERVATORY


SESSION = "a" * 32
VISIT = "b" * 32
OPERATION = "c" * 32
CHARACTER = "96d75fab-3f2f-5e77-a484-0fa4bbbcc941"


class NativeWalletTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_tracks_stock_receipts_without_checkpointing(self):
        stream = "e" * 32
        scene = {"state": "hosting", "map": OBSERVATORY}
        host = SimpleNamespace(
            control=Mock(generation=SESSION), native_guest=True, native_profile=True,
            stopping=False, transitioning=False, native_checkpoint_unsafe=False,
            last_game=scene.copy(), party=party.new_party(), guest_wallet_receipts={},
            checkpoint_native=AsyncMock(),
        )
        bridge = SimpleNamespace(local_bridge=object(), command=AsyncMock(return_value={
            **scene, "ack": {"message": "2 37"}, "native_wallet": 145,
        }))
        target = f"{SESSION}@{stream}@1@50"
        settled = await native_wallet.settle(host, bridge, CHARACTER, target, OPERATION)
        self.assertEqual(settled, {native_wallet.ACK: [stream, 2, 37]})
        self.assertEqual(host.party["economy"]["wallet"], 145)
        self.assertEqual(host.guest_wallet_receipts[CHARACTER], [stream, 2, 37])
        bridge.command.assert_awaited_once_with(native_wallet.REPORT, SESSION, CHARACTER.replace("-", ""),
                                                stream, "1", "50", timeout=3)
        host.checkpoint_native.assert_not_awaited()
        bridge.command.return_value = {**scene, "ack": {}, "native_wallet": 200}
        with self.assertRaisesRegex(RuntimeError, "did not include its counters"):
            await native_wallet.settle(host, bridge, CHARACTER, target, OPERATION)
        self.assertEqual(host.party["economy"]["wallet"], 145)

    async def test_debt_changes_are_applied_even_when_cash_stays_the_same(self):
        wallet = native_wallet.GuestWallet()
        scope = (SESSION, VISIT, OBSERVATORY, "exact pawn")
        client = SimpleNamespace(
            bridge=SimpleNamespace(local_bridge=object(), command=AsyncMock(), snapshot=dict),
            stock_interaction_scope=lambda: scope,
            gameplay={"shared_wallet": 20, native_wallet.ACK: [], native_wallet.DEBT: 3_000_000},
            gameplay_seen=time.monotonic(), last_game={}, gameplay_action=AsyncMock(),
        )
        await wallet.sync(client)
        await wallet.sync(client)
        client.gameplay[native_wallet.DEBT] = 2_000_000
        await wallet.sync(client)
        self.assertEqual([call.args for call in client.bridge.command.await_args_list], [
            (native_wallet.SYNC, SESSION, wallet.stream, "20", "0", "0", "3000000"),
            (native_wallet.SYNC, SESSION, wallet.stream, "20", "0", "0", "2000000"),
        ])
        client.gameplay_action.assert_not_awaited()

    async def test_guest_ignores_an_older_wallet_frame_after_acknowledgement(self):
        wallet = native_wallet.GuestWallet()
        scope = (SESSION, VISIT, OBSERVATORY, "exact pawn")
        game = {native_wallet.CHANGE: (SESSION, wallet.stream, 1, 50)}
        client = SimpleNamespace(
            bridge=SimpleNamespace(local_bridge=object(), command=AsyncMock(), snapshot=lambda: game),
            stock_interaction_scope=lambda: scope, gameplay={"shared_wallet": 20, native_wallet.ACK: []},
            gameplay_seen=time.monotonic(), last_game=game, gameplay_action=AsyncMock(),
        )
        await wallet.sync(client)
        await wallet.report
        client.gameplay = {"shared_wallet": 73, native_wallet.ACK: [wallet.stream, 1, 50]}
        await wallet.sync(client)
        client.gameplay = {"shared_wallet": 20, native_wallet.ACK: []}
        await wallet.sync(client)
        client.gameplay_action.assert_awaited_once()
        frames = [call.args for call in client.bridge.command.await_args_list]
        self.assertEqual(frames, [
            (native_wallet.SYNC, SESSION, wallet.stream, "20", "0", "0"),
            (native_wallet.SYNC, SESSION, wallet.stream, "73", "1", "50"),
        ])
