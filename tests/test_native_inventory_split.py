import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish import native_inventory as inventory, party
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.guest_weapon_profiles import NINE_MM_BALL
from tools.silverfish.runtime import Bridge
from tools.silverfish.travel import OBSERVATORY


GUID, OPERATION = "C" * 32, "d" * 32
PAWN = "Player_FP_C exact-detached-pawn"


class StackSplitTests(unittest.IsolatedAsyncioTestCase):
    def host(self, store):
        state = party.new_party()
        for character in ("host", "guest", "other"):
            party.add_player(state, character)

        async def checkpoint(_bridge, candidate, *, require_guest_frames):
            self.assertEqual(require_guest_frames, frozenset({"guest"}))
            return store.save_world({"party": candidate})

        return SimpleNamespace(
            native_guest=True, native_zones=True, native_profile=Path("stage"), last_seen=time.monotonic(),
            last_game={"state": "hosting", "map": OBSERVATORY, inventory.SPLIT_CAPABILITY: 1},
            stopping=False, transitioning=False, native_checkpoint_unsafe=False, party=state,
            native_recipients=lambda: {"guest": {"connected": True, "pawn": PAWN}},
            store=store, checkpoint_native=AsyncMock(side_effect=checkpoint), control=Mock(),
        )

    def bridge(self):
        async def split(action, pawn, source, output, take, expected, **_):
            self.assertEqual((action, pawn, source, take, expected), (inventory.SPLIT, PAWN, GUID, "4", "9"))
            return {"state": "hosting", "map": OBSERVATORY, inventory.SPLIT_CAPABILITY: 1, inventory.SPLIT_KEY: {
                "map": OBSERVATORY, "pawn": pawn, "source_guid": source, "new_guid": output, "quantity_before": 9,
                "split": 4, "remaining": 5, "class_path": NINE_MM_BALL.item_path,
            }}
        return SimpleNamespace(command=AsyncMock(side_effect=split))

    def test_target_validation_and_private_status(self):
        selected = f"{GUID}|4|9"
        self.assertEqual(inventory.split_target(selected), (GUID, 4, 9))
        for target in (f"{GUID}|0|9", f"{GUID}|9|9", f"{GUID}|10|9", f"{GUID}|01|9", f"{GUID}|3|2147483648", GUID, {}):
            self.assertIsNone(inventory.split_target(target))
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            status = directory / "status.tsv"
            status.write_text(f"schema\t1\nsession\thost\thosting\t{OBSERVATORY}\t1\t0\n"
                              "native-inventory-split-capability\t1\n"
                              f"native-inventory-split\t{OBSERVATORY}\t{PAWN}\t{GUID}\t{'A' * 32}\t"
                              f"{NINE_MM_BALL.item_path}\t9\t5\t4\n", encoding="utf-8")
            snapshot = Bridge(directory, Mock()).snapshot()
            self.assertEqual(snapshot[inventory.SPLIT_CAPABILITY], 1)
            self.assertEqual(snapshot[inventory.SPLIT_KEY]["remaining"], 5)

    async def test_exact_split_result_is_durable_and_replays_to_its_owner_only(self):
        selected = f"{GUID}|4|9"
        with TemporaryDirectory() as directory:
            with CampaignStore(directory) as store:
                host, bridge = self.host(store), self.bridge()
                receipt = await inventory.split_stack(host, bridge, "guest", selected, OPERATION)
                self.assertEqual(receipt[inventory.SPLIT_KEY]["owner"], "guest")
                self.assertNotEqual(receipt[inventory.SPLIT_KEY]["new_guid"], GUID)
                self.assertEqual(await inventory.split_stack(host, bridge, "guest", selected, OPERATION), receipt)
                with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
                    await inventory.split_stack(host, bridge, "guest", f"{GUID}|5|9", OPERATION)
                with self.assertRaisesRegex(party.RuleError, "guest_not_active"):
                    await inventory.split_stack(host, bridge, "other", selected, OPERATION)
                bridge.command.assert_awaited_once()
            with CampaignStore(directory) as store:
                restarted, bridge = self.host(store), self.bridge()
                restarted.party = party.load_party(store.status()["world"]["party"])
                self.assertEqual(await inventory.split_stack(restarted, bridge, "guest", selected, OPERATION), receipt)
                bridge.command.assert_not_awaited()

    async def test_preview_keeps_stock_generated_guid_and_replays_once(self):
        with TemporaryDirectory() as temporary, CampaignStore(temporary) as store:
            host, bridge = self.host(store), self.bridge()
            created = "A" * 32
            target = f"{GUID}|4|9|{created}"
            result = await inventory.split_stack(host, bridge, "guest", target, OPERATION)
            self.assertEqual(result[inventory.SPLIT_KEY]["new_guid"], created)
            self.assertEqual(await inventory.split_stack(host, bridge, "guest", target, OPERATION), result)
            with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
                await inventory.split_stack(host, bridge, "guest", f"{GUID}|4|9|" + "B" * 32, OPERATION)
            with self.assertRaisesRegex(party.RuleError, "requires_guid_amount_and_expected_quantity"):
                await inventory.split_stack(host, bridge, "guest", f"{GUID}|4|9|{GUID}", OPERATION)
            bridge.command.assert_awaited_once_with(inventory.SPLIT, PAWN, GUID, created, "4", "9", timeout=6)

    async def test_refusal_does_not_commit_and_interrupted_commit_fences_host(self):
        with TemporaryDirectory() as directory, CampaignStore(directory) as store:
            host, bridge = self.host(store), self.bridge()
            native = bridge.command.side_effect
            bridge.command.side_effect = RuntimeError("native_inventory_split_refused_stale_quantity")
            with self.assertRaisesRegex(party.RuleError, "stale_quantity"):
                await inventory.split_stack(host, bridge, "guest", f"{GUID}|4|9", OPERATION)
            self.assertFalse(host.native_checkpoint_unsafe or host.stopping)
            host.checkpoint_native.assert_not_awaited()
            bridge.command.side_effect = native
            host.checkpoint_native.side_effect = asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await inventory.split_stack(host, bridge, "guest", f"{GUID}|4|9", OPERATION)
            self.assertTrue(host.native_checkpoint_unsafe and host.stopping)
            self.assertFalse(host.party["receipts"])

    async def test_backpack_sort_is_qualified_to_one_guest_and_replays_once(self):
        with TemporaryDirectory() as temporary, CampaignStore(temporary) as store:
            host = self.host(store)
            host.last_game[inventory.SORT_CAPABILITY] = 1
            report = {"map": OBSERVATORY, "pawn": PAWN, "count": 2, "state": "sorted"}
            snapshot = {**host.last_game, inventory.SORT_KEY: report}
            bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot))
            result = await inventory.sort_backpack(host, bridge, "guest", None, OPERATION)
            self.assertEqual(result, {inventory.SORT_KEY: {"owner": "guest", **report}})
            self.assertEqual(await inventory.sort_backpack(host, bridge, "guest", None, OPERATION), result)
            with self.assertRaisesRegex(party.RuleError, "guest_not_active"):
                await inventory.sort_backpack(host, bridge, "other", None, OPERATION)
            self.assertEqual(store.status()["world"]["party"]["receipts"]["guest"][0]["result"], result)
            bridge.command.assert_awaited_once_with(inventory.SORT, PAWN, timeout=6)
            host.checkpoint_native.assert_awaited_once()
            self.assertFalse(host.native_checkpoint_unsafe)

    async def test_backpack_sort_distinguishes_clean_refusal_from_unverified_mutation(self):
        with TemporaryDirectory() as temporary, CampaignStore(temporary) as store:
            host = self.host(store)
            host.last_game[inventory.SORT_CAPABILITY] = 1
            bridge = SimpleNamespace(command=AsyncMock(side_effect=RuntimeError("native_inventory_sort_refused_stock_schema")))
            with self.assertRaisesRegex(party.RuleError, "native_inventory_sort_refused_stock_schema"):
                await inventory.sort_backpack(host, bridge, "guest", None, OPERATION)
            self.assertFalse(host.native_checkpoint_unsafe)
            self.assertFalse(host.stopping)
            bridge.command.side_effect = None
            bridge.command.return_value = {
                **host.last_game, inventory.SORT_KEY: {"map": OBSERVATORY, "pawn": "somebody else",
                                                      "count": 2, "state": "sorted"},
            }
            with self.assertRaisesRegex(RuntimeError, "exact guest inventory"):
                await inventory.sort_backpack(host, bridge, "guest", None, OPERATION)
            self.assertTrue(host.native_checkpoint_unsafe)
            self.assertTrue(host.stopping)
            host.checkpoint_native.assert_not_awaited()

    async def test_overlay_rechecks_exact_visible_selection_before_dispatching_split(self):
        view = {"state": "splitting", "sequence": 18, "map": OBSERVATORY,
                "pawn": PAWN, "class_path": NINE_MM_BALL.item_path, "guid": GUID, "count": 9}
        displayed = {"state": "rendered", "sequence": 18, "map": OBSERVATORY, "pawn": PAWN, "offset": 16}
        game = {"state": "connected", "map": OBSERVATORY, inventory.SPLIT_VIEW: view, "pawns": {"local": {"name": PAWN}},
                "native_inventory_view": displayed}
        item = {"source": "backpack", "guid": GUID, "class_path": NINE_MM_BALL.item_path, "quantity": 9,
                "stackable": True, "important": False}
        page = {"native_inventory": {"owner": "guest", "offset": 16, "total": 17, "items": [item]}}
        result = {inventory.SPLIT_KEY: {"state": "split", "owner": "guest", "map": OBSERVATORY, "source_guid": GUID,
                                      "new_guid": "A" * 32, "class_path": NINE_MM_BALL.item_path, "quantity_before": 9,
                                      "split": 4, "remaining": 5}}
        for latest, accepted in (({**game, inventory.SPLIT_VIEW: dict(view, state="closed")}, False),
                                 ({**game, "native_inventory_view": dict(displayed, offset=0)}, False), (game, True)):
            with self.subTest(accepted=accepted):
                bridge = SimpleNamespace(input_namespace=uuid.uuid4(), snapshot=Mock(return_value=latest),
                                         command=AsyncMock(return_value=game))
                client = SimpleNamespace(last_game=game, last_inventory_split=0, host_session="generation", zone_epoch="epoch",
                                         stopping=False, gameplay={inventory.SPLIT_CAPABILITY: 1}, gameplay_seen=time.monotonic(),
                                         bridge=bridge, invite={"character_id": "guest"},
                                         gameplay_action=AsyncMock(side_effect=[page, result]), control=Mock())
                await inventory.service_split_view(client, True)
                await inventory.service_split_view(client, True)
                self.assertEqual(client.gameplay_action.await_count, 2 if accepted else 1)
                self.assertEqual(client.gameplay_action.await_args_list[0].args[::2], ("native-inventory-list", "16"))
                if accepted:
                    self.assertEqual(client.gameplay_action.await_args.args[::2], (inventory.SPLIT, f"{GUID}|4|9"))
                bridge.command.assert_awaited_once_with("native-inventory-view-split-result", "18", OBSERVATORY, GUID,
                                                       "split" if accepted else "refused", timeout=2)
