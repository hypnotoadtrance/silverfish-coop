import unittest

from tools.silverfish.native_roster import RosterEnrollment
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def connection(number, *, ready=True, controller=True, pawn_number=None):
    return {
        "state": "ready" if ready else "joining",
        "id": f"c{number}",
        "connection": f"IpConnection /Engine/Transient.IpConnection_{number}",
        "controller": f"PlayerControler_FP_C /Game/Peer.Peer:PersistentLevel.PC_{number}" if controller else None,
        "pawn": f"Player_FP_C /Game/Peer.Peer:PersistentLevel.Pawn_{pawn_number or number}" if ready else None,
    }


def snapshot(sequence, *connections, driver="d1", map_name=CROSSROADS):
    return {
        "mode": "host", "state": "hosting", "server_connection": False, "sequence": sequence,
        "map": map_name, "remote_clients": len(connections),
        "native_roster": {"state": "qualified", "driver": driver, "count": len(connections),
                          "reason": "", "connections": list(connections)},
    }


class RosterEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.roster = RosterEnrollment()
        self.first = connection(1)
        self.second = connection(2)

    def enroll_first(self):
        result = self.roster.reserve("first", "char-1", snapshot(1))
        self.assertEqual(result.state, "reserved")
        self.assertEqual(result.reservation.driver, "d1")
        confirmed = self.roster.observe(snapshot(2, self.first))
        self.assertEqual(confirmed.state, "bound")
        self.assertEqual(confirmed.reservation, result.reservation)
        return confirmed.assignment

    def test_serial_enrollment_and_control_reconnect_keep_exact_assignments(self):
        first_binding = self.enroll_first()
        started = self.roster.reserve("second", "char-2", snapshot(2, self.first))
        self.assertEqual(started.state, "reserved")
        self.assertEqual(self.roster.reserve("third", "char-3", snapshot(2, self.first)).reason,
                         "enrollment_in_progress")
        repeated = self.roster.reserve("second", "char-2", snapshot(2, self.first))
        self.assertEqual((repeated.state, repeated.reservation), ("pending", started.reservation))

        same_frame = self.roster.observe(snapshot(2, self.first))
        self.assertEqual((same_frame.state, same_frame.reason), ("pending", "awaiting_new_connection"))
        partial = connection(2, ready=False, controller=False)
        without_controller = self.roster.observe(snapshot(3, self.first, partial))
        self.assertEqual(without_controller.reason, "awaiting_new_pawn")
        self.assertIsNone(without_controller.joining)
        attached = connection(2, ready=False)
        without_pawn = self.roster.observe(snapshot(4, attached, self.first))
        self.assertEqual(without_pawn.reason, "awaiting_new_pawn")
        self.assertEqual(without_pawn.joining, (self.second["connection"], self.second["controller"]))

        reconnected = self.roster.reserve("first", "char-1", snapshot(4, self.first, attached))
        self.assertEqual((reconnected.state, reconnected.reason), ("bound", "retained_native_connection"))
        self.assertEqual(reconnected.assignment, first_binding)
        self.assertEqual(self.roster.pending, started.reservation)

        confirmed = self.roster.observe(snapshot(5, self.second, self.first))
        self.assertEqual((confirmed.state, confirmed.reason), ("bound", "new_connection_verified"))
        self.assertEqual(confirmed.reservation, started.reservation)
        self.assertEqual(confirmed.assignment.connection_id, "c2")
        self.assertEqual(confirmed.assignment.connection, self.second["connection"])
        self.assertEqual(confirmed.assignment.controller, self.second["controller"])
        self.assertEqual(confirmed.assignment.pawn, self.second["pawn"])
        self.assertEqual(self.roster.assignments, {"first": first_binding, "second": confirmed.assignment})

    def test_existing_binding_rejects_character_switch_and_duplicate_character(self):
        original = self.enroll_first()
        self.assertEqual(self.roster.reserve("first", "char-2", snapshot(2, self.first)).reason,
                         "character_change_requires_release")
        self.assertEqual(self.roster.reserve("other", "char-1", snapshot(2, self.first)).reason,
                         "character_already_enrolled")
        self.assertEqual(self.roster.assignments, {"first": original})

    def test_ambiguous_new_connections_abort_without_later_guess(self):
        original = self.enroll_first()
        reservation = self.roster.reserve("second", "char-2", snapshot(2, self.first)).reservation
        third = connection(3, ready=False, controller=False)
        aborted = self.roster.observe(snapshot(3, self.first, self.second, third))
        self.assertEqual((aborted.state, aborted.reason), ("aborted", "ambiguous_new_connections"))
        self.assertEqual(aborted.reservation, reservation)
        self.assertIsNone(self.roster.pending)

        leftover = snapshot(4, self.first, self.second)
        self.assertEqual(self.roster.observe(leftover).reason, "unassigned_connections")
        self.assertEqual(self.roster.reserve("second", "char-2", leftover).reason, "unassigned_connections")
        self.assertEqual(self.roster.assignments, {"first": original})

    def test_baseline_disappearance_or_replacement_aborts(self):
        for changed, reason in (
            ((), "known_connection_missing"),
            ((connection(1, pawn_number=9),), "known_connection_changed"),
        ):
            with self.subTest(reason=reason):
                self.roster = RosterEnrollment()
                original = self.enroll_first()
                pending = self.roster.reserve("second", "char-2", snapshot(2, self.first))
                aborted = self.roster.observe(snapshot(3, self.second, *changed))
                self.assertEqual((aborted.state, aborted.reason), ("aborted", reason))
                self.assertEqual(aborted.reservation, pending.reservation)
                self.assertEqual(self.roster.assignments, {"first": original})

    def test_joining_candidate_cannot_be_replaced(self):
        for changed, reason in (
            ((), "new_connection_missing"),
            ((connection(3),), "new_connection_changed"),
            ((dict(self.second, connection="IpConnection /Game/ReusedName"),), "new_connection_changed"),
            ((dict(self.second, controller=self.first["controller"]),), "new_connection_changed"),
        ):
            with self.subTest(reason=reason, changed=changed):
                roster = RosterEnrollment()
                started = roster.reserve("second", "char-2", snapshot(1))
                self.assertEqual(roster.observe(snapshot(2, connection(2, ready=False))).state, "pending")
                aborted = roster.observe(snapshot(3, *changed))
                self.assertEqual((aborted.state, aborted.reason), ("aborted", reason))
                self.assertEqual(aborted.reservation, started.reservation)
                self.assertFalse(roster.assignments)

    def test_disconnect_releases_only_the_absent_native_connection(self):
        first_binding = self.enroll_first()
        self.roster.reserve("second", "char-2", snapshot(2, self.first))
        second_binding = self.roster.observe(snapshot(3, self.first, self.second)).assignment
        self.assertEqual(self.roster.retire("first", snapshot(3, self.first, self.second)).reason,
                         "connection_still_present")
        left = self.roster.retire("first", snapshot(4, self.second))
        self.assertEqual((left.state, left.assignment), ("released", first_binding))
        self.assertEqual(self.roster.assignments, {"second": second_binding})
        new = self.roster.reserve("first", "char-3", snapshot(4, self.second))
        self.assertEqual(new.state, "reserved")
        rebound = self.roster.observe(snapshot(5, self.second, connection(3))).assignment
        self.assertEqual((rebound.character_id, rebound.connection_id), ("char-3", "c3"))

    def test_departure_requires_current_census_and_original_driver_and_character(self):
        self.enroll_first()
        self.assertFalse(self.roster.departure_current("first", "char-1", snapshot(3)))
        self.assertEqual(self.roster.retire("first", snapshot(3)).state, "released")
        self.assertTrue(self.roster.departure_current("first", "char-1", snapshot(3)))
        self.assertFalse(self.roster.departure_current("first", "char-2", snapshot(3)))

        for unqualified in (snapshot(2), snapshot(3, self.first), snapshot(4, driver="d2"),
                            snapshot(4, map_name=OBSERVATORY), dict(snapshot(3), native_roster=None)):
            self.assertFalse(self.roster.departure_current("first", "char-1", unqualified))

        # The old actor identities must also be absent even if a row has a new ID.
        self.roster.observe(snapshot(4, dict(self.first, id="c3")))
        self.assertFalse(self.roster.departure_current("first", "char-1", snapshot(4, dict(self.first, id="c3"))))
        self.roster.observe(snapshot(5))
        self.assertTrue(self.roster.departure_current("first", "char-1", snapshot(5)))
        pending = self.roster.reserve("second", "char-2", snapshot(5)).reservation
        self.assertFalse(self.roster.departure_current("first", "char-1", snapshot(5)))
        self.roster.cancel(pending.id)
        self.roster.begin_travel()
        self.assertFalse(self.roster.departure_current("first", "char-1", snapshot(5)))
        self.roster.observe(snapshot(6, driver="d2", map_name=OBSERVATORY))
        self.assertFalse(self.roster.departure_current("first", "char-1", snapshot(6, driver="d2", map_name=OBSERVATORY)))

    def test_stale_cancel_cannot_cancel_another_reservation(self):
        first = self.roster.reserve("first", "char-1", snapshot(1)).reservation
        self.assertEqual(self.roster.cancel(first.id).reason, "reservation_cancelled")
        second = self.roster.reserve("second", "char-2", snapshot(1)).reservation
        self.assertEqual(self.roster.cancel(first.id).reason, "unknown_reservation")
        self.assertEqual(self.roster.pending, second)

    def test_travel_requires_fresh_driver_and_new_proof(self):
        old_binding = self.enroll_first()
        second = self.roster.reserve("second", "char-2", snapshot(2, self.first)).reservation
        ended = self.roster.begin_travel()
        self.assertEqual((ended.state, ended.reason), ("aborted", "driver_retired"))
        self.assertEqual(ended.reservation, second)
        self.assertFalse(self.roster.assignments)
        self.assertEqual(self.roster.reserve("first", "char-1", snapshot(3, self.first)).reason, "driver_retired")
        destination = snapshot(4, driver="d2", map_name=OBSERVATORY)
        self.assertEqual(self.roster.reserve("first", "char-1", destination).state, "reserved")
        confirmed = self.roster.observe(snapshot(5, connection(3), driver="d2", map_name=OBSERVATORY)).assignment
        self.assertEqual((confirmed.guest_id, confirmed.character_id), (old_binding.guest_id, old_binding.character_id))
        self.assertEqual((confirmed.driver, confirmed.connection_id), ("d2", "c3"))
        self.assertEqual(self.roster.observe(snapshot(6, self.first)).reason, "stale_driver")

    def test_unexpected_driver_change_cannot_claim_old_pending_guest(self):
        self.enroll_first()
        pending = self.roster.reserve("second", "char-2", snapshot(2, self.first)).reservation
        changed = self.roster.observe(snapshot(3, connection(3), driver="d2", map_name=OBSERVATORY))
        self.assertEqual((changed.state, changed.reason, changed.reservation), ("aborted", "driver_changed", pending))
        self.assertFalse(self.roster.assignments)
        self.assertEqual(self.roster.reserve("second", "char-2",
                                             snapshot(3, connection(3), driver="d2", map_name=OBSERVATORY)).reason,
                         "unassigned_connections")

    def test_rejects_client_census_and_keeps_pending_during_missing_observation(self):
        client = dict(snapshot(1), mode="client", state="connected", server_connection=True)
        self.assertEqual(self.roster.reserve("first", "char-1", client).reason, "census_unavailable")
        reservation = self.roster.reserve("first", "char-1", snapshot(1)).reservation
        self.assertEqual(self.roster.observe(None).reason, "census_unavailable")
        self.assertEqual(self.roster.pending, reservation)
        self.assertEqual(self.roster.observe(snapshot(2, self.first)).state, "bound")

    def test_duplicate_or_malformed_census_aborts_without_reserving_actor(self):
        for malformed, reason in (
            (snapshot(2, self.first, self.first), "census_duplicate_identity"),
            (dict(snapshot(2, self.first), remote_clients=0), "census_invalid"),
            (snapshot(2, dict(self.first, pawn=None)), "census_invalid"),
            (dict(snapshot(2), native_roster={"state": "unavailable", "reason": "connection_identity_ambiguous"}),
             "census_duplicate_identity"),
        ):
            with self.subTest(reason=reason, malformed=malformed):
                roster = RosterEnrollment()
                pending = roster.reserve("first", "char-1", snapshot(1)).reservation
                result = roster.observe(malformed)
                self.assertEqual((result.state, result.reason, result.reservation), ("aborted", reason, pending))
                self.assertFalse(roster.assignments)
                self.assertEqual(roster.observe(snapshot(3, self.first)).reason, "unassigned_connections")

    def test_older_sample_cannot_complete_and_same_sequence_conflict_aborts(self):
        pending = self.roster.reserve("first", "char-1", snapshot(5)).reservation
        self.assertEqual(self.roster.observe(snapshot(4, self.first)).reason, "stale_census")
        self.assertEqual(self.roster.pending, pending)
        result = self.roster.observe(snapshot(5, self.first))
        self.assertEqual((result.state, result.reason), ("aborted", "census_sequence_conflict"))
        self.assertFalse(self.roster.assignments)
