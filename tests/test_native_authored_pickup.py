import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

from tools.silverfish import native_authored_pickup as pickup, native_computer_board, native_host_save, native_quest
from tools.silverfish import native_wire_board, party, runtime
from tools.silverfish.travel import BLACK_MARKET, CITY, OBSERVATORY


WIRE, BRANDY = pickup.CITY_WIRE, pickup.CITY_BRANDY
GUIDS = {WIRE: "AA" * 16, BRANDY: "BB" * 16}
GUEST, PAWN = "courier", "Player_FP_C authenticated pawn"


def row(source, state="available"):
    area, actor, item_class = pickup.SOURCES[source]
    return {"source": source, "map": area, "actor": actor, "class_path": item_class, "state": state}


def frame(wire="available", brandy="available", *, state="hosting"):
    return {"state": state, "map": CITY, "pawns": {"local": {"name": PAWN}},
            pickup.SOURCES_KEY: {WIRE: row(WIRE, wire), BRANDY: row(BRANDY, brandy)}}


class AuthoredPickupSelectorTests(unittest.TestCase):
    def test_multiple_sources_require_an_exact_selector(self):
        self.assertEqual(pickup.select_source(OBSERVATORY, None), pickup.SOURCE)
        self.assertEqual(pickup.select_source(CITY, WIRE), WIRE)
        self.assertEqual(pickup.select_source(CITY, BRANDY), BRANDY)
        for wrong in (None, "City-Paladin-Brandy", pickup.SOURCE):
            with self.subTest(selector=wrong), self.assertRaisesRegex(party.RuleError, "exact_source_required"):
                pickup.select_source(CITY, wrong)

    def test_status_keeps_each_exact_source_and_preserves_singleton_alias(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        status = path / "status.tsv"
        bridge = runtime.Bridge(path, Mock())

        def status_row(source):
            return "\t".join(("native-authored-pickup", *row(source).values()))

        stock = f"native-authored-pickup-interaction\t{WIRE}\t{CITY}\t{PAWN}\t8\tstock"
        fixture = f"native-authored-pickup-interaction\t{BRANDY}\t{CITY}\t{PAWN}\t9\tfixture"
        city_header = f"schema\t1\nsession\tclient\tconnected\t{CITY}\t1\t1\npawn\tlocal\t{PAWN}\t100\t0\t0\t0\tplr\n"
        status.write_text(city_header + "\n".join((status_row(WIRE), stock, status_row(BRANDY), fixture)), encoding="utf-8")
        snapshot = bridge.snapshot()
        self.assertEqual(snapshot[pickup.SOURCES_KEY], frame()[pickup.SOURCES_KEY])
        self.assertNotIn("native_authored_pickup", snapshot)
        self.assertNotIn("native_authored_pickup_interaction", snapshot)
        self.assertEqual(pickup.fixture_proof(snapshot, BRANDY)["native_authored_pickup_interaction"]["sequence"], 9)
        self.assertEqual(pickup.fixture_proof(snapshot, WIRE, "stock")["native_authored_pickup_interaction"]["sequence"], 8)
        with self.assertRaisesRegex(RuntimeError, "selected source and pawn"):
            pickup.fixture_proof(snapshot, WIRE)

        status.write_text(city_header + status_row(WIRE) + "\n" + status_row(WIRE), encoding="utf-8")
        self.assertIsNone(bridge.snapshot())
        status.write_text(city_header + status_row(WIRE), encoding="utf-8")
        self.assertNotIn("native_authored_pickup", bridge.snapshot())

        status.write_text(f"schema\t1\nsession\thost\thosting\t{OBSERVATORY}\t1\t0\n" + status_row(pickup.SOURCE), encoding="utf-8")
        self.assertEqual(bridge.snapshot()["native_authored_pickup"], row(pickup.SOURCE))

    def test_existing_f_event_format_carries_both_resolved_source_ids(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (path / "input.tsv").write_text(f"interact\t1\t{WIRE}\ninteract\t2\t{BRANDY}\n", encoding="ascii")
        bridge = runtime.Bridge(path, Mock())
        commands = bridge.interactions()
        self.assertEqual([target for _, target in commands], [WIRE, BRANDY])
        self.assertNotEqual(commands[0][0], commands[1][0])
        self.assertEqual(bridge.interactions(), [])


class AuthoredPickupTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_hides_only_the_selected_durable_source(self):
        service = SimpleNamespace(last_game=frame(state="connected"))
        bridge = SimpleNamespace(command=AsyncMock(return_value=frame("claimed", state="connected")))
        await pickup.project(service, bridge, {WIRE: {}})
        bridge.command.assert_awaited_once_with("native-authored-pickup-project", "claimed", WIRE, timeout=5)
        self.assertEqual(service.last_game[pickup.SOURCES_KEY][BRANDY]["state"], "available")

        service.last_game = frame(state="connected")
        bridge.command.reset_mock()
        bridge.command.side_effect = [frame("claimed", state="connected"), frame("claimed", "claimed", state="connected")]
        await pickup.project(service, bridge, {WIRE: {}, BRANDY: {}})
        self.assertEqual(bridge.command.await_args_list, [
            call("native-authored-pickup-project", "claimed", WIRE, timeout=5),
            call("native-authored-pickup-project", "claimed", BRANDY, timeout=5),
        ])

        service.last_game = frame(state="connected")
        bridge.command.side_effect = None
        bridge.command.return_value = frame("claimed", state="hosting")
        with self.assertRaisesRegex(RuntimeError, "exact consumed placed pickup projection"):
            await pickup.project(service, bridge, {BRANDY: {}})

    async def test_durable_receipts_and_replay_are_independent_per_selected_source(self):
        profile = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = party.new_party()
        party.add_player(state, GUEST)
        host = SimpleNamespace(native_zones=True, native_profile=profile, stopping=False, transitioning=False,
                               native_checkpoint_unsafe=False, last_game=frame(), last_seen=time.monotonic(),
                               party=state, native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
                               checkpoint_native=AsyncMock(return_value=4), control=Mock())

        async def command(action, pawn, slot, source, *, timeout):
            self.assertEqual((action, pawn, timeout), (pickup.ACTION, PAWN, 7))
            path = native_host_save.slot_path(profile, slot)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"GVAS " + source.encode("ascii"))
            return {**frame(), "native_authored_pickup_claim": {
                "source": source, "map": CITY, "pawn": pawn, "slot": slot,
                "ok": True, "applied": True, "status": "claimed", "guid": GUIDS[source],
            }}

        bridge = SimpleNamespace(command=AsyncMock(side_effect=command))
        first = await pickup.claim(host, bridge, GUEST, WIRE, "a" * 32)
        with self.assertRaisesRegex(party.RuleError, "operation_id_conflict"):
            await pickup.claim(host, bridge, GUEST, BRANDY, "a" * 32)
        second = await pickup.claim(host, bridge, GUEST, BRANDY, "b" * 32)
        for receipt, source in ((first, WIRE), (second, BRANDY)):
            self.assertEqual(receipt["native_authored_pickup_claim"], {
                **{k: v for k, v in row(source).items() if k != "state"}, "guid": GUIDS[source], "owner": GUEST,
            })
        self.assertEqual(set(host.party[pickup.STATE_KEY]), {WIRE, BRANDY})
        self.assertEqual(host.checkpoint_native.call_args.kwargs["require_guest_frames"], {GUEST})

        host.last_game = {"state": "hosting", "map": CITY}
        self.assertEqual(await pickup.claim(host, bridge, GUEST, WIRE, "a" * 32), first)
        self.assertEqual(await pickup.claim(host, bridge, GUEST, BRANDY, "b" * 32), second)
        self.assertEqual(bridge.command.await_count, 2)

    async def test_fine_vintage_redeems_only_the_saved_city_brandy_guid(self):
        spec = native_computer_board.BRANDY
        self.assertEqual(native_wire_board.fixture_selection(BLACK_MARKET, spec.quest_id), ("Clt_Brandy",))
        state = party.new_party()
        for actor in ("host", GUEST):
            party.add_player(state, actor)
        state[pickup.STATE_KEY] = {
            source: dict(row(source, "claimed"), guid=GUIDS[source], owner=GUEST) for source in (WIRE, BRANDY)
        }
        game = {"state": "hosting", "map": BLACK_MARKET}
        host = SimpleNamespace(native_zones=True, native_guest=True, native_profile=True, stopping=False, transitioning=False,
                               native_checkpoint_unsafe=False, last_game=game, last_seen=time.monotonic(), party=state,
                               roles=lambda: {"host": "host", "guest": GUEST},
                               native_recipients=lambda: {GUEST: {"pawn": PAWN, "connected": True}},
                               checkpoint_native=AsyncMock(return_value=4), control=Mock())

        def snapshot(guid, *, complete=False):
            return {**game, spec.key: {
                "operation": spec.command("turn-in" if complete else "preflight"), "map": BLACK_MARKET,
                "pawn": PAWN, "board": native_computer_board.BOARDS[BLACK_MARKET], "quest": spec.quest_id, "can_turn_in": True,
                "step_before": 1, "step_after": -10 if complete else 1, "cash_before": 100, "cash_after": 1000 if complete else 100,
                "xp_before": 3, "xp_after": 4 if complete else 3, "objective_guid": guid, "objective_owner": "guest",
                "reputation": {"8": {"before": 5, "after": 20 if complete else 5}},
            }}

        async def redeem():
            return await native_quest.action(host, bridge, GUEST, "native-quest-turnin", spec.quest_id)

        bridge = SimpleNamespace(command=AsyncMock(return_value=snapshot(GUIDS[WIRE])))
        with self.assertRaisesRegex(party.RuleError, "objective_required"):
            await redeem()
        host.checkpoint_native.assert_not_awaited()
        bridge.command.side_effect = [snapshot(GUIDS[BRANDY]), snapshot(GUIDS[BRANDY], complete=True)]
        result = await redeem()
        self.assertEqual((result["consumed_brandy"], result["objective_owner"], result["cash"], result["xp"]),
                         (GUIDS[BRANDY], GUEST, 900, 1))
        self.assertEqual(result["reputation"], {"8": 20})
        self.assertEqual(host.party[pickup.STATE_KEY][WIRE]["guid"], GUIDS[WIRE])
