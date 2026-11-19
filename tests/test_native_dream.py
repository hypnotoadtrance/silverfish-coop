import json
import unittest

from tools.silverfish import native_blue, native_dream, party
from tools.silverfish.travel import (
    BLACK_OCEAN, CROSSROADS, DEAD_BRIDGE, EXCHANGE, OBSERVATORY, PYROCLAST, SLATE, THE_ROOK,
)


class StardustVisitTests(unittest.TestCase):
    def test_committed_stack_visit_timing_and_early_return_are_durable(self):
        original = party.new_party()
        guid = "AB" * 16
        charged = native_dream.begin(original, guid, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")
        self.assertIsNone(native_dream.visit(original))
        trip = native_dream.visit(charged)
        self.assertEqual(trip["phase"], "departing")
        self.assertIsNone(trip["deadline"])
        with self.assertRaisesRegex(ValueError, "visit_in_progress"):
            native_dream.begin(charged, guid, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")

        with self.assertRaisesRegex(ValueError, "commit its effects"):
            native_dream.arrived(charged, 100)
        committed = native_dream.effects_applied(charged)
        with self.assertRaisesRegex(ValueError, "must be pending"):
            native_dream.effects_applied(committed)
        active = native_dream.arrived(committed, 100)
        self.assertFalse(native_dream.return_due(active, 195.99))
        self.assertTrue(native_dream.return_due(active, 196))
        accelerated = native_dream.accelerate(active, 150)
        self.assertEqual(native_dream.visit(active)["deadline"], 193)
        self.assertIs(native_dream.accelerate(accelerated, 151), accelerated)
        self.assertFalse(native_dream.return_due(accelerated, 152.99))
        self.assertTrue(native_dream.return_due(accelerated, 153))

        returning = native_dream.returning(accelerated)
        self.assertTrue(native_dream.return_due(returning, 100))
        returned = native_dream.finished(returning)
        self.assertIsNone(native_dream.visit(returned))
        self.assertEqual(returned["last_stardust_visit"], {
            "id": trip["id"], "guid": guid, "remaining": 1, "return_map": OBSERVATORY,
        })

    def test_stack_recovery_requires_qualified_metadata(self):
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, "", OBSERVATORY, "")

    def test_fruiting_duration_and_route_are_typed(self):
        for flag, destination, expected in (("Tarot_Rook", THE_ROOK, 463), ("Tarot_Obelisk", PYROCLAST, 643)):
            with self.subTest(flag=flag):
                charged = native_dream.begin(party.new_party(), "AB" * 16, 2, "stock-fruiting-default-v1",
                                             OBSERVATORY, "", source=DEAD_BRIDGE, kind="fruiting-stardust", tarot=flag)
                active = native_dream.arrived(native_dream.effects_applied(charged), 100)
                self.assertEqual(native_dream.visit(active)["destination"], destination)
                self.assertEqual(native_dream.visit(active)["deadline"], expected)
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "",
                               kind="fruiting-stardust", tarot="Tarot_Rook")
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "",
                               tarot="Tarot_Nothing")

    def test_stock_level_script_timers_and_fades(self):
        for flag, destination, deadline in (("Tarot_Night", SLATE, 280), ("Tarot_BlackStag", BLACK_OCEAN, 260)):
            with self.subTest(flag=flag):
                committed = native_dream.begin(party.new_party(), "AB" * 16, 1, "", OBSERVATORY, "",
                                               kind="fruiting-stardust", tarot=flag)
                active = native_dream.arrived(native_dream.effects_applied(committed), 100)
                self.assertEqual(native_dream.visit(active)["destination"], destination)
                self.assertEqual(native_dream.visit(active)["deadline"], deadline)
                self.assertFalse(native_dream.return_due(active, deadline + 1.99))
                self.assertTrue(native_dream.return_due(active, deadline + 2))

    def test_old_exchange_departure_can_finish_without_replaying_new_effects(self):
        legacy = native_dream.begin(party.new_party(), "AB" * 16, 2, native_dream.STOCK_PAYLOAD, OBSERVATORY, "")
        legacy["stardust_visit"] = {key: legacy["stardust_visit"][key] for key in native_dream.LEGACY_KEYS}
        self.assertEqual(native_dream.visit(legacy)["effects"], "legacy")
        active = native_dream.arrived(legacy, 100)
        self.assertEqual(native_dream.visit(active)["deadline"], 193)

    def test_stock_next_dream_and_tarot_priority(self):
        for source, destination in ((CROSSROADS, EXCHANGE), (EXCHANGE, THE_ROOK), (THE_ROOK, PYROCLAST),
                                    (PYROCLAST, SLATE), (SLATE, BLACK_OCEAN)):
            with self.subTest(source=source):
                self.assertEqual(native_dream.destination_for(source, ""), destination)
        self.assertEqual(native_dream.destination_for(EXCHANGE, "Tarot_Obelisk"), PYROCLAST)
        self.assertEqual(native_dream.destination_for(BLACK_OCEAN, "Tarot_Rook"), THE_ROOK)
        self.assertIsNone(native_dream.destination_for(THE_ROOK, "Tarot_Rook"))
        self.assertIsNone(native_dream.destination_for(BLACK_OCEAN, ""))
        self.assertIsNone(native_dream.destination_for(BLACK_OCEAN, "Tarot_Nothing"))
        self.assertIsNone(native_dream.destination_for("unsupported", "Tarot_Rook"))
        self.assertIsNone(native_dream.destination_for(EXCHANGE, None))

    def test_follow_on_chain_preserves_original_bed_and_latest_debit_on_reload(self):
        guid = "AB" * 16
        bed = native_blue.BED_IDS[DEAD_BRIDGE]
        current = native_dream.begin(party.new_party(), guid, 5, native_dream.STOCK_PAYLOAD, DEAD_BRIDGE, bed)
        previous_id = native_dream.visit(current)["id"]
        current = native_dream.arrived(native_dream.effects_applied(current), 20)
        steps = ((EXCHANGE, THE_ROOK, 4, 50, 233), (THE_ROOK, PYROCLAST, 3, 65, 338),
                 (PYROCLAST, SLATE, 2, 78, 168), (SLATE, BLACK_OCEAN, 1, 83, 243))
        for source, destination, quantity, now, deadline in steps:
            with self.subTest(source=source):
                latest = native_dream.begin(current, guid, quantity, native_dream.STOCK_PAYLOAD,
                                            OBSERVATORY, "", source=source)
                self.assertEqual(native_dream.visit(current)["id"], previous_id)
                latest = json.loads(json.dumps(latest))
                departing = native_dream.visit(latest)
                self.assertNotEqual(departing["id"], previous_id)
                self.assertEqual(departing["source"], source)
                self.assertEqual(departing["destination"], destination)
                self.assertEqual(departing["before"], quantity)
                self.assertEqual(departing["effects"], "pending")
                self.assertIsNone(departing["deadline"])
                self.assertEqual((departing["return_map"], departing["return_bed"]), (DEAD_BRIDGE, bed))
                with self.assertRaisesRegex(ValueError, "commit its effects"):
                    native_dream.arrived(latest, now)
                current = native_dream.arrived(native_dream.effects_applied(latest), now)
                self.assertEqual(native_dream.visit(current)["deadline"], deadline)
                previous_id = departing["id"]
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(current, guid, 1, "", OBSERVATORY, "", source=BLACK_OCEAN)
        returned = native_dream.finished(native_dream.returning(current))
        self.assertEqual(returned["last_stardust_visit"], {
            "id": previous_id, "guid": guid, "remaining": 0, "return_map": DEAD_BRIDGE,
        })

    def test_follow_on_requires_the_active_arrival_and_a_guarded_distinct_destination(self):
        guid = "AB" * 16
        with self.assertRaisesRegex(ValueError, "stardust_visit_missing"):
            native_dream.begin(party.new_party(), guid, 1, "", OBSERVATORY, "", source=EXCHANGE)
        first = native_dream.begin(party.new_party(), guid, 1, "", OBSERVATORY, "", tarot="Tarot_Rook")
        with self.assertRaisesRegex(ValueError, "stardust_visit_in_progress"):
            native_dream.begin(first, guid, 1, "", OBSERVATORY, "", source=THE_ROOK)
        visiting = native_dream.arrived(native_dream.effects_applied(first), 100)
        with self.assertRaisesRegex(ValueError, "stardust_visit_in_progress"):
            native_dream.begin(visiting, guid, 1, "", OBSERVATORY, "", source=EXCHANGE)
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.begin(visiting, guid, 1, "", OBSERVATORY, "", source=THE_ROOK, tarot="Tarot_Rook")
        returning = native_dream.returning(visiting)
        with self.assertRaisesRegex(ValueError, "stardust_visit_in_progress"):
            native_dream.begin(returning, guid, 1, "", OBSERVATORY, "", source=THE_ROOK)
        next_trip = native_dream.begin(visiting, guid, 1, "", OBSERVATORY, "", source=THE_ROOK, tarot="Tarot_Night")
        self.assertEqual(native_dream.visit(next_trip)["destination"], SLATE)
        invalid_route = party.load_party(next_trip)
        invalid_route["stardust_visit"]["destination"] = PYROCLAST
        with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
            native_dream.visit(invalid_route)

    def test_departure_and_visit_reject_invalid_times(self):
        departure = native_dream.begin(party.new_party(), "AB" * 16, 1, "", OBSERVATORY, "")
        committed = native_dream.effects_applied(departure)
        visiting = native_dream.arrived(committed, 100)
        for value in (float("nan"), float("inf"), True, -1, 10 ** 400):
            with self.subTest(time=value):
                with self.assertRaisesRegex(ValueError, "Invalid Stardust arrival time"):
                    native_dream.arrived(committed, value)
                with self.assertRaisesRegex(ValueError, "Invalid Stardust acceleration time"):
                    native_dream.accelerate(visiting, value)
                with self.assertRaisesRegex(ValueError, "Invalid Stardust return time"):
                    native_dream.return_due(visiting, value)
                invalid_deadline = party.load_party(visiting)
                invalid_deadline["stardust_visit"]["deadline"] = value
                with self.assertRaisesRegex(ValueError, "Invalid saved Stardust visit"):
                    native_dream.visit(invalid_deadline)
