import unittest

from tools.silverfish.travel import PLAYTEST_MAPS, travel_decision

SOURCE, DESTINATION = sorted(PLAYTEST_MAPS)


def pawn(location=(0, 0, 0), *, zone=SOURCE):
    return {"map": zone, "location": location}


class TravelGateTests(unittest.TestCase):
    def test_guest_cannot_initiate_travel(self):
        decision = travel_decision("guest", SOURCE, DESTINATION, (), {"host": pawn()})
        self.assertEqual(decision, {
            "allowed": False, "blockers": [{"id": "host", "reason": "host_only"}],
        })

    def test_rejects_unknown_maps_and_current_map(self):
        for source, target, reason in (
            ("Unknown", DESTINATION, "unknown_source"),
            (SOURCE, "Unknown", "unknown_destination"),
            (SOURCE, SOURCE, "same_zone"),
        ):
            with self.subTest(reason=reason):
                decision = travel_decision("host", source, target, (), {"host": pawn()})
                self.assertFalse(decision["allowed"])
                self.assertEqual(decision["blockers"], [{"id": "host", "reason": reason}])

    def test_exact_3d_radius_is_inclusive(self):
        positions = {"host": pawn([200, 100, 20]), "guest": pawn([200, 1000, 1220])}
        self.assertEqual(travel_decision("host", SOURCE, DESTINATION, ("host", "guest"), positions), {
            "allowed": True, "blockers": [],
        })
        positions["guest"]["location"][2] += 0.01
        decision = travel_decision("host", SOURCE, DESTINATION, ("guest",), positions)
        self.assertEqual(decision["blockers"], [{"id": "guest", "reason": "too_far"}])

    def test_reports_active_and_in_grace_blockers_in_stable_order(self):
        active_ids = ("z-far", "a-reserved", "b-stale", "c-invalid", "a-reserved")
        positions = {
            "host": pawn(),
            "z-far": pawn((0, 0, 1600)),
            "b-stale": pawn(zone=DESTINATION),
            "c-invalid": pawn([0, 0, float("nan")]),
            "inactive": pawn((0, 0, 3000)),
        }
        self.assertEqual(travel_decision("host", SOURCE, DESTINATION, active_ids, positions), {
            "allowed": False, "blockers": [
                {"id": "a-reserved", "reason": "missing_pawn"},
                {"id": "b-stale", "reason": "wrong_zone"},
                {"id": "c-invalid", "reason": "invalid_position"},
                {"id": "z-far", "reason": "too_far"},
            ],
        })

    def test_requires_valid_host_position_in_source(self):
        self.assertEqual(travel_decision("host", SOURCE, DESTINATION, (), {}), {
            "allowed": False, "blockers": [{"id": "host", "reason": "missing_pawn"}],
        })
        self.assertEqual(travel_decision("host", SOURCE, DESTINATION, (), {"host": pawn(zone=DESTINATION)}), {
            "allowed": False, "blockers": [{"id": "host", "reason": "wrong_zone"}],
        })
        self.assertEqual(travel_decision("host", SOURCE, DESTINATION, (), {"host": pawn((True, 0, 0))}), {
            "allowed": False, "blockers": [{"id": "host", "reason": "invalid_position"}],
        })

    def test_reverse_route_on_the_other_playtest_map(self):
        self.assertTrue(travel_decision("host", DESTINATION, SOURCE, (), {"host": pawn(zone=DESTINATION)})["allowed"])


if __name__ == "__main__":
    unittest.main()
