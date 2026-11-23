"""Regression coverage for the compiled stock Data Silo station profile."""

import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
import uuid

from test_native_mindcore import CHARACTER, OPERATION, PAWN, host, scene
from tools.silverfish import native_host_save, native_mindcore as core, native_mosby_tape as tape
from tools.silverfish.native_tape_profiles import DRILL, INSTRUCTIONS


BLANK = "D" * 32
PRODUCT = uuid.uuid5(uuid.NAMESPACE_URL, f"{INSTRUCTIONS.namespace}:{CHARACTER}:{BLANK}").hex.upper()
RESULTS_BLANK = "B" * 32
RESULTS = uuid.uuid5(uuid.NAMESPACE_URL, f"{DRILL.namespace}:{CHARACTER}:{RESULTS_BLANK}").hex.upper()


def compiled_host(stage=2):
    value = host(stage)
    value.native_loot_input = False
    value.party[DRILL.ledger][RESULTS_BLANK].update(
        blank_guid=RESULTS_BLANK, filled_guid=RESULTS, operation_id="c" * 32, mosby_drill_test=1,
    )
    value.party[core.LEDGER] = dict(
        owner=CHARACTER, guid=RESULTS, operation_id="b" * 32, map=core.MAP, cradle=core.CRADLE,
        uploader=core.UPLOADER, class_path=DRILL.product, flag=core.FLAG, stock_status=2,
    )
    return value


def fields(verb, blank=BLANK, filled="-", stock="2"):
    return [
        INSTRUCTIONS.prefix, INSTRUCTIONS.verb(verb), core.MAP, PAWN, INSTRUCTIONS.station,
        "offer" if verb == "preflight" else "exchanged", blank, filled, "-", "301", "5", "1", "1", "0", "0", stock,
    ]


class DrillInstructionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_compiled_receipt_and_live_mounted_consent_required_before_any_command(self):
        bridge = SimpleNamespace(command=AsyncMock())
        for stage in (0, 1):
            with self.subTest(stage=stage), self.assertRaisesRegex(ValueError, "compiled_console"):
                await tape.preflight(compiled_host(stage), bridge, CHARACTER, INSTRUCTIONS.verb("preflight"), BLANK)
        owner = compiled_host()
        del owner.party[core.LEDGER]
        with self.assertRaisesRegex(ValueError, "compiled_console"):
            await tape.convert(owner, bridge, CHARACTER, BLANK, OPERATION, profile=INSTRUCTIONS)
        bridge.command.assert_not_awaited()

    async def test_exact_stock_blank_exchange_keeps_results_and_commits_both_owned_frames(self):
        owner = compiled_host()
        before = owner.party[core.LEDGER].copy()
        world = {"state": "hosting", "map": core.MAP, core.SCENE: scene(2)}
        commands = SimpleNamespace(command=AsyncMock(side_effect=[
            {**world, INSTRUCTIONS.key: tape.parse_exchange(fields("preflight"))},
            {**world, INSTRUCTIONS.key: tape.parse_exchange(fields("convert", filled=PRODUCT))},
        ]))
        result = await tape.convert(owner, commands, CHARACTER, BLANK, OPERATION, profile=INSTRUCTIONS)
        receipt = owner.party[INSTRUCTIONS.ledger][BLANK]
        self.assertEqual(result, {"effect": INSTRUCTIONS.effect + "_exchanged", **receipt})
        self.assertEqual((receipt["console_status"], receipt["filled_guid"]), (2, PRODUCT))
        self.assertEqual(owner.party[core.LEDGER], before)
        self.assertEqual([call.kwargs["require_guest_frames"] for call in owner.checkpoint_native.await_args_list],
                         [frozenset({CHARACTER}), frozenset({CHARACTER})])
        self.assertFalse(owner.native_checkpoint_unsafe or owner.transitioning or owner.stopping)
        checkpoint = {"world": {"party": owner.party}, "characters": {CHARACTER: {}}}
        self.assertEqual(native_host_save._escrow_frames(checkpoint), [])
        del owner.party[core.LEDGER]
        with self.assertRaisesRegex(native_host_save.CampaignFormatError, "no durable MindCore compilation"):
            native_host_save._escrow_frames(checkpoint)

    def test_stock_wire_requires_exact_station_recipe_and_compiled_status(self):
        good = fields("preflight")
        parsed = tape.parse_exchange(good)
        self.assertEqual(parsed["give"], {"class_path": tape.BLANK_CLASS, "quantity": 1, "cash": 0})
        self.assertEqual(parsed["receive"], {"class_path": INSTRUCTIONS.product, "quantity": 1, "cash": 0})
        for field, value in ((4, core.CRADLE), (8, "2"), (13, "1"), (15, "1")):
            edited = good[:]
            edited[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                tape.parse_exchange(edited)
