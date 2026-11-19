import tempfile
import unittest
from pathlib import Path

from tools.silverfish.campaign import CampaignStore
from tools.silverfish.party_members import PartyMemberError, PartyMembers
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def connection(index):
    return {
        "id": f"c{index}",
        "state": "ready",
        "connection": f"IpConnection /Engine/Transient.Link_{index}",
        "controller": f"PlayerControler_FP_C /Game/Map:PersistentLevel.PC_{index}",
        "pawn": f"Player_FP_C /Game/Map:PersistentLevel.Player_{index}",
    }


def telemetry(row, index):
    return {
        "name": row["pawn"],
        "controller": row["controller"],
        "location": [float(index), 10.0, 20.0],
        "health": 100.0 - index,
        "max_health": 100.0,
    }


def snapshot(sequence, *connections, aliases=None, driver="d1", map_name=CROSSROADS):
    if aliases is None:
        aliases = ["guest" if index == 1 else f"guest{index}" for index in range(1, len(connections) + 1)]
    return {
        "mode": "host", "state": "hosting", "server_connection": False, "sequence": sequence,
        "map": map_name, "remote_clients": len(connections),
        "native_roster": {"state": "qualified", "driver": driver, "count": len(connections),
                          "reason": "", "connections": list(connections)},
        "pawns": {alias: telemetry(row, int(row["id"][1:])) for alias, row in zip(aliases, connections)},
    }


class PartyMembersTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.now = 1000
        self.store = CampaignStore(Path(scratch.name) / "campaign", clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.party = PartyMembers(self.store, max_guests=3, clock=lambda: self.now)
        self.first = self.store.issue_invitation("First guest", "First")
        self.second = self.store.issue_invitation("Second guest", "Second")
        self.third = self.store.issue_invitation("Third guest", "Third")
        self.one = connection(1)
        self.two = connection(2)

    def admit(self, invitation, game):
        return self.party.admit(game=game, **invitation)

    def enroll_first(self):
        admission = self.admit(self.first, snapshot(1))
        self.assertEqual(admission.enrollment.state, "reserved")
        self.assertEqual(self.party.roles(), {"host": "host"})
        established = snapshot(2, self.one)
        binding = self.party.observe(established).assignment
        self.assertEqual(binding, admission.member.binding)
        self.party.mark_ready(self.first["guest_id"], binding)
        return admission

    def enroll_two(self):
        first = self.enroll_first()
        second = self.admit(self.second, snapshot(2, self.one))
        binding = self.party.observe(snapshot(3, self.one, self.two)).assignment
        self.party.mark_ready(self.second["guest_id"], binding)
        return first, second

    def test_authentication_and_serial_native_enrollment_keep_independent_leases(self):
        first = self.enroll_first()
        denied = {**self.second, "credential": "Z" * 43}
        with self.assertRaisesRegex(PartyMemberError, "^guest_or_character_unavailable$"):
            self.admit(denied, snapshot(2, self.one))
        self.assertIsNone(self.party.enrollment.pending)
        self.assertIsNone(self.party.get(self.second["guest_id"]))
        second = self.admit(self.second, snapshot(2, self.one))
        with self.assertRaisesRegex(PartyMemberError, "^enrollment_in_progress$"):
            self.admit(self.third, snapshot(2, self.one))
        self.assertEqual(self.party.enrollment.pending, second.enrollment.reservation)
        self.assertNotEqual(first.member.lease_id, second.member.lease_id)
        self.assertEqual({entry["guest_id"] for entry in self.store.status()["leases"]},
                         {self.first["guest_id"], self.second["guest_id"]})
        self.assertEqual(self.party.roles(), {"host": "host", "guest": self.first["character_id"]})

    def test_native_pawns_match_exact_identity_when_bridge_aliases_reverse(self):
        first, second = self.enroll_two()
        reordered = snapshot(4, self.two, self.one)
        self.party.observe(reordered)
        matched = self.party.match_pawns(reordered)
        self.assertEqual((matched["guest"].alias, matched["guest"].binding), ("guest2", first.member.binding))
        self.assertEqual((matched["guest2"].alias, matched["guest2"].binding), ("guest", second.member.binding))
        self.assertEqual(matched["guest2"].pawn["location"], [2.0, 10.0, 20.0])
        self.assertEqual(self.party.roles(), {"host": "host", "guest": self.first["character_id"],
                                              "guest2": self.second["character_id"]})

    def test_stale_ambiguous_or_mismatched_telemetry_fails_closed(self):
        self.enroll_two()
        self.assertEqual(self.party.match_pawns(snapshot(2, self.one)), {})
        self.assertEqual(self.party.match_pawns(snapshot(3, self.one)), {})
        wrong_world = snapshot(4, self.one, self.two, driver="d2", map_name=OBSERVATORY)
        self.assertEqual(self.party.match_pawns(wrong_world), {})

        ambiguous = snapshot(4, self.one, self.two)
        ambiguous["pawns"]["guest3"] = telemetry(self.one, 1)
        self.assertEqual(set(self.party.match_pawns(ambiguous)), {"guest2"})

        invalid = snapshot(4, self.one, self.two)
        invalid["pawns"]["guest2"]["controller"] = self.one["controller"]
        invalid["pawns"]["guest"]["location"][0] = float("nan")
        self.assertEqual(self.party.match_pawns(invalid), {})

    def test_retained_reconnect_does_not_interrupt_another_enrollment(self):
        first = self.enroll_first()
        second = self.admit(self.second, snapshot(2, self.one))
        self.party.disconnect(self.first["guest_id"])
        self.now += 30
        self.assertEqual(self.party.roles(), {"host": "host", "guest": self.first["character_id"]})
        resumed = self.admit(self.first, snapshot(2, self.one))
        self.assertTrue(resumed.resumed)
        self.assertIs(resumed.member, first.member)
        self.assertTrue(resumed.member.ready)
        self.assertFalse(resumed.member.spawn_near_host)
        self.assertIsNone(resumed.member.grace_until)
        self.assertEqual(self.party.enrollment.pending, second.enrollment.reservation)

    def test_grace_expiry_and_native_departure_remove_only_the_expired_member(self):
        first, second = self.enroll_two()
        self.store.save_character(first.member.lease_id, {"health": 19, "downed": True})
        self.party.disconnect(self.first["guest_id"])
        self.now += 90
        self.assertEqual(self.party.roles(), {"host": "host", "guest2": self.second["character_id"]})
        self.assertEqual(self.party.expire(snapshot(3, self.one, self.two)), (first.member,))
        self.assertIs(self.party.get(self.first["guest_id"]), first.member)
        self.assertEqual(self.party.expire(snapshot(4, self.two)), (first.member,))
        self.assertIsNone(self.party.get(self.first["guest_id"]))
        self.assertIs(self.party.get(self.second["guest_id"]), second.member)
        after_departure = self.party.match_pawns(snapshot(4, self.two))
        self.assertEqual(after_departure["guest2"].alias, "guest")
        later = self.admit(self.first, snapshot(4, self.two))
        self.assertFalse(later.resumed)
        self.assertTrue(later.member.spawn_near_host)
        self.assertNotEqual(later.member.lease_id, first.member.lease_id)
        self.assertEqual(later.member.restore, {"health": 19, "downed": True})

    def test_travel_retires_old_bindings_while_active_roles_wait_for_new_proof(self):
        first, second = self.enroll_two()
        roles = self.party.roles()
        self.party.begin_travel()
        self.assertEqual(self.party.roles(), roles)
        self.assertIsNone(first.member.binding)
        self.assertIsNone(second.member.binding)
        self.assertEqual(self.party.match_pawns(snapshot(3, self.one, self.two)), {})

        destination = snapshot(4, driver="d2", map_name=OBSERVATORY)
        self.assertEqual(self.party.reserve_returning(self.first["guest_id"], destination).state, "reserved")
        self.assertEqual(self.party.reserve_returning(self.second["guest_id"], destination).reason,
                         "enrollment_in_progress")
        arrived = snapshot(5, connection(3), driver="d2", map_name=OBSERVATORY)
        first_binding = self.party.observe(arrived).assignment
        self.party.mark_ready(self.first["guest_id"], first_binding)
        self.assertEqual(self.party.reserve_returning(self.second["guest_id"], arrived).state, "reserved")
        both = snapshot(6, connection(4), connection(3), driver="d2", map_name=OBSERVATORY)
        self.party.mark_ready(self.second["guest_id"], self.party.observe(both).assignment)
        self.assertEqual(self.party.match_pawns(both)["guest"].alias, "guest2")
        self.assertEqual(self.party.roles(), roles)


if __name__ == "__main__":
    unittest.main()
