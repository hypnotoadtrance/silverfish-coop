import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import uuid

from tools.silverfish import native_mindcore as core, native_mindcore_view as view
from tools.silverfish.native_tape_profiles import DRILL


PAWN = f"Player_FP_C {core.LEVEL}Player_FP_C_3"
CHARACTER = "75a8b566-e13c-46d6-b542-313ec5163c3f"
GUID = "A" * 32
CONNECTION = "exact-host-session:12"


def frame(state, serial, opened, guid=None):
    return {"map": core.MAP, "state": "connected", "pawns": {"local": {"name": PAWN}},
            view.KEY: view.parse([view.TAG, core.MAP, PAWN, core.CRADLE, state, str(serial), str(opened), guid or "-"])}


def ready_stage(status, installed=True):
    return {"host_world": {core.SCENE: dict(map=core.MAP, cradle=core.CRADLE, uploader=core.UPLOADER,
                                           core=core.CORE, flag=core.FLAG, status=status, installed=installed)}}


class MindCoreViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = SimpleNamespace(input_namespace=uuid.uuid4(), command=AsyncMock(side_effect=self.render))
        self.control = Mock()
        self.local = view.MindCoreView(self.bridge, self.control, CHARACTER)
        self.authority = AsyncMock(side_effect=self.authorized)

    def render(self, command, serial, opened, result, guid, *, timeout):
        self.assertEqual((command, timeout), (view.RENDER, 3))
        return frame(result, int(serial), int(opened), None if guid == "-" else guid)

    def authorized(self, action, operation, target):
        if action == core.PREFLIGHT:
            self.assertIsNone(target)
            return {core.PROOF: dict(map=core.MAP, operation=core.PREFLIGHT, status=1, proof="owned", guid=GUID)}
        self.assertEqual((action, target), (core.UPLOAD, GUID))
        return dict(effect="native_mindcore_results_compiled", map=core.MAP, cradle=core.CRADLE, uploader=core.UPLOADER,
                    flag=core.FLAG, class_path=DRILL.product, stock_status=2, owner=CHARACTER, guid=target, operation_id=operation)

    async def test_first_stock_press_only_checks_owned_tape_second_uploads_and_replay_is_ignored(self):
        first = frame("checking", 1, 1)
        offer = await self.local.sync(first, CONNECTION, True, self.authority)
        self.assertEqual(offer, frame("offer", 1, 1, GUID))
        self.authority.assert_awaited_once()
        self.assertEqual(self.authority.await_args.args[0], core.PREFLIGHT)

        second = frame("submitting", 2, 1, GUID)
        compiled = await self.local.sync(second, CONNECTION, True, self.authority)
        self.assertEqual(compiled, frame("compiled", 2, 1, GUID))
        self.assertEqual(self.authority.await_args.args[::2], (core.UPLOAD, GUID))
        await self.local.sync(second, CONNECTION, True, self.authority)
        self.assertEqual(self.authority.await_count, 2)

    async def test_host_epoch_and_confirmation_guid_cannot_change(self):
        for next_epoch, wrong_guid in (("changed-epoch", GUID), (CONNECTION, "B" * 32)):
            with self.subTest(next_epoch=next_epoch, wrong_guid=wrong_guid):
                local = view.MindCoreView(self.bridge, self.control, CHARACTER)
                await local.sync(frame("checking", 1, 1), CONNECTION, True, self.authority)
                refused = await local.sync(frame("submitting", 2, 1, wrong_guid), next_epoch, True, self.authority)
                self.assertEqual(refused, frame("unavailable", 2, 1))
        self.assertEqual([call.args[0] for call in self.authority.await_args_list], [core.PREFLIGHT] * 2)

    async def test_no_stage_lease_consumes_stale_local_intent_without_sending_it(self):
        old = frame("checking", 1, 1)
        await self.local.sync(old, CONNECTION, False, self.authority)
        await self.local.sync(old, CONNECTION, True, self.authority)
        self.authority.assert_not_awaited()

    def test_only_exact_mounted_stage_one_is_qualified_and_local_row_is_typed(self):
        self.assertTrue(view.qualified_stage(ready_stage(1)))
        for stage, installed in ((0, False), (1, False), (2, True)):
            self.assertFalse(view.qualified_stage(ready_stage(stage, installed)))
        report = ready_stage(1)
        report["host_world"][core.SCENE]["cradle"] = "other stock object"
        self.assertFalse(view.qualified_stage(report))
        for state, serial, opened, guid in (("submitting", 1, 1, GUID), ("offer", 2, 1, GUID),
                                             ("pass-through", 2, 1, None), ("checking", 1, 1, GUID)):
            with self.subTest(state=state), self.assertRaises(ValueError):
                frame(state, serial, opened, guid)
