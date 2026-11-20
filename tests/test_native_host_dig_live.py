from types import SimpleNamespace
import time
import unittest
from unittest.mock import AsyncMock, Mock

from tools.silverfish import native_host_dig as stock, native_host_dig_live as dig, party
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS


PAWN = f"Player_FP_C {stock.WORLD}:PersistentLevel.Player_FP_C_987"
ACTOR = f"Package_Item_FarmHouse_C {stock.WORLD}:PersistentLevel.Package_Item_FarmHouse_C_654"
SPADE, ESCROW, PACKAGE = "A" * 32, "B" * 32, "C" * 32
MATERIAL = "PhysicalMaterial /Game/Classes/Libraries/PhysicMaterials/PM_Dirt.PM_Dirt"
SLOT = "SFCoop_" + "a" * 32
GUEST_ID = "11aa22bb-33cc-44dd-88ee-9900aabbccdd"


def game(state, *, serial=1, package=None, escrow=None):
    row = dict(map=CROSSROADS, pawn=PAWN, state=state, serial=serial, spade_guid=SPADE, actor=ACTOR,
               package_guid=package, contact=list(stock.SITE), material=MATERIAL, escrow_guid=escrow, fault=None)
    if state == "ready":
        row.update(spade_guid=None, actor=None, contact=None, material=None)
    return {"map": CROSSROADS, "state": "hosting", "pawns": {"local": {"name": PAWN}}, dig.KEY: row}


def custody(state):
    row = dict(state=state, map=CROSSROADS, source=stock.SOURCE, flag=stock.FLAG, flag_value=1, spade_guid=SPADE,
               contact=list(stock.SITE), material=MATERIAL, package_guid=PACKAGE if state == "owned" else ESCROW)
    if state == "escrowed":
        row["escrow"] = dict(slot=SLOT, map=CROSSROADS, size=1536, sha256="d" * 64)
    return row


class DigReportTests(unittest.TestCase):
    def test_diagnostic_belongs_to_the_current_listen_host(self):
        fields = [dig.DIAGNOSTIC, CROSSROADS, PAWN, "qualified", "fresh", "inactive_interned", "armed", "ready"]
        self.assertEqual(dig.parse_diagnostic(fields, "hosting", CROSSROADS)["read"], "ready")
        self.assertIsNone(dig.parse_diagnostic(fields, "connected", CROSSROADS))

    def test_live_receipt_rejects_wrong_session_and_landmark(self):
        fields = [dig.TAG, CROSSROADS, PAWN, "unclaimed", "1", SPADE, ACTOR, "-",
                  *map(str, stock.SITE), MATERIAL, "1", ESCROW, SLOT, "-"]
        result = dig.parse(fields, "hosting", CROSSROADS)
        self.assertEqual((result["actor"], result["escrow_guid"], result["incidental"]), (ACTOR, ESCROW, 1))
        with self.assertRaises(ValueError):
            dig.parse(fields, "connected", CROSSROADS)
        fields[8] = "101"
        with self.assertRaises(ValueError):
            dig.parse(fields, "hosting", CROSSROADS)

    def test_only_loose_custody_retains_escrow(self):
        loose = custody("escrowed")
        self.assertEqual(dig.escrow_frames({dig.CUSTODY: loose}), [loose["escrow"]])
        self.assertEqual(dig.escrow_frames({dig.CUSTODY: custody("owned")}), [])
        with self.assertRaises(ValueError):
            dig.escrow_frames({dig.CUSTODY: dict(custody("owned"), escrow=loose["escrow"])})
        handed_off = dict(custody("owned"), owner=GUEST_ID)
        party_data = {dig.CUSTODY: handed_off, "players": {GUEST_ID: {}}}
        self.assertEqual(dig.escrow_frames(party_data, {GUEST_ID}), [])
        with self.assertRaisesRegex(ValueError, "package owner"):
            dig.escrow_frames(party_data, set())
        with self.assertRaisesRegex(ValueError, "package identity"):
            dig.escrow_frames({**party_data, dig.CUSTODY: dict(loose, owner=GUEST_ID)})


class DigCustodyTests(unittest.IsolatedAsyncioTestCase):
    def make_host(self, snapshot):
        return SimpleNamespace(native_host_dig_live=True, party=dict(party.load_party(None), **{dig.CUSTODY: custody("escrowed")}),
                               last_game=snapshot, last_seen=0.0, control=SimpleNamespace(event=Mock()),
                               checkpoint_native=AsyncMock(return_value=48))

    async def test_actual_pickup_commits_owned_generation_before_ack(self):
        host = self.make_host(game("collected", package=PACKAGE, escrow=ESCROW))

        async def acknowledge(*arguments, **_):
            self.assertEqual(arguments, (dig.TAG + "-ack", PAWN, "1", "owned", PACKAGE))
            self.assertEqual(host.party[dig.CUSTODY], custody("owned"))
            return game("owned", package=PACKAGE, escrow=ESCROW)

        bridge = SimpleNamespace(command=AsyncMock(side_effect=acknowledge))
        self.assertFalse(await dig.settle(host, bridge))
        _, candidate = host.checkpoint_native.await_args.args
        self.assertEqual(candidate[dig.CUSTODY], custody("owned"))
        self.assertEqual(host.checkpoint_native.await_args.kwargs["dig_token"], f"dig@1@owned@{PACKAGE}")

    async def test_refused_token_preserves_previous_custody(self):
        host = self.make_host(game("collected", package=PACKAGE, escrow=ESCROW))
        host.checkpoint_native.side_effect = RuntimeError(dig.WAITING)
        bridge = SimpleNamespace(command=AsyncMock())
        self.assertTrue(await dig.settle(host, bridge))
        self.assertEqual(host.party[dig.CUSTODY], custody("escrowed"))
        bridge.command.assert_not_awaited()

    async def test_new_world_restores_exact_loose_package_once(self):
        host = self.make_host(game("ready", serial=0))
        bridge = SimpleNamespace(command=AsyncMock(return_value=game("escrowed", serial=2, escrow=ESCROW)))
        self.assertFalse(await dig.settle(host, bridge))
        bridge.command.assert_awaited_once_with(dig.TAG + "-restore", PAWN, SLOT, ESCROW, SPADE,
                                               *map(str, stock.SITE), MATERIAL, timeout=6)
        host.checkpoint_native.assert_not_awaited()


class PackageHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        data = party.new_party()
        for character in ("host", GUEST_ID):
            party.add_player(data, character)
        data[dig.CUSTODY] = custody("owned")
        pawns = {"local": {"name": "host pawn", "location": [0, 0, 0]},
                 "guest": {"name": "guest pawn", "location": [10, 20, 30]}}
        state = dict(state="hosting", map=BLACK_MARKET, pawns=pawns)
        self.host = SimpleNamespace(native_host_dig_live=True, persist_native_guest=True, native_profile=True,
                                    transitioning=False, stopping=False, native_checkpoint_unsafe=False,
                                    last_game=state, last_seen=time.monotonic(), party=data,
                                    roles=lambda: {"host": "host", "guest": GUEST_ID},
                                    native_recipients=lambda: {GUEST_ID: {"alias": "guest", "pawn": "guest pawn", "connected": True}},
                                    checkpoint_native=AsyncMock(return_value=49), control=Mock())
        self.bridge = SimpleNamespace(command=AsyncMock(return_value={
            **state, "ack": {"message": f"stock sealed package handed off {PACKAGE}"}}))

    async def test_handoff_captures_both_saves_before_publishing_new_owner(self):
        owned = dict(custody("owned"), owner=GUEST_ID)
        result = await dig.handoff(self.host, self.bridge, "guest")
        self.assertEqual(result, {dig.CUSTODY: owned})
        self.assertEqual(self.host.party[dig.CUSTODY], owned)
        before, after = self.host.checkpoint_native.await_args_list
        self.assertEqual(before.args, (self.bridge,))
        self.assertEqual(after.args[1][dig.CUSTODY], owned)
        for call in (before, after):
            self.assertEqual(call.kwargs, {"require_guest_frames": frozenset({GUEST_ID})})
        self.bridge.command.assert_awaited_once_with(dig.HANDOFF, "host pawn", "guest pawn", GUEST_ID, PACKAGE, timeout=5)
        self.assertEqual(await dig.handoff(self.host, self.bridge, GUEST_ID), result)
        self.bridge.command.assert_awaited_once()
        self.assertEqual(self.host.checkpoint_native.await_count, 2)

    async def test_failed_pair_commit_does_not_publish_the_new_owner(self):
        self.host.checkpoint_native.side_effect = [48, RuntimeError("guest frame omitted")]
        with self.assertRaisesRegex(RuntimeError, "guest frame omitted"):
            await dig.handoff(self.host, self.bridge, GUEST_ID)
        self.assertEqual(self.host.party[dig.CUSTODY], custody("owned"))
        self.assertTrue(self.host.stopping and self.host.native_checkpoint_unsafe)
        self.assertFalse(self.host.transitioning)

    async def test_a_safe_stock_refusal_leaves_the_host_running(self):
        self.bridge.command.side_effect = RuntimeError("stock_missing_contact_handoff_out_of_range")
        with self.assertRaisesRegex(party.RuleError, "handoff_out_of_range"):
            await dig.handoff(self.host, self.bridge, GUEST_ID)
        self.assertEqual(self.host.party[dig.CUSTODY], custody("owned"))
        self.assertFalse(self.host.stopping or self.host.native_checkpoint_unsafe or self.host.transitioning)

    async def test_out_of_range_refuses_without_a_checkpoint(self):
        self.host.last_game["pawns"]["guest"]["location"] = [301, 0, 0]
        with self.assertRaisesRegex(party.RuleError, "handoff_out_of_range"):
            await dig.handoff(self.host, self.bridge, GUEST_ID)
        self.host.checkpoint_native.assert_not_awaited()
        self.bridge.command.assert_not_awaited()
