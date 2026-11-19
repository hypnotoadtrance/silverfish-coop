import json
import unittest

from tools.silverfish import native_matrix, party
from tools.silverfish.travel import CROSSROADS, DEAD_BRIDGE, EXCHANGE, OBSERVATORY


CUBE = "AB" * 16
SECOND_CUBE = "BC" * 16
TRIP = "CD" * 16
SECOND_TRIP = "DE" * 16
FULL = "EF" * 16
EMPTY = "FA" * 16


def eather(remainder, full_ammo, full_value, empty_ammo=0, empty_value=10):
    return {"remainder": remainder, "canisters": {
        FULL: {"ammo": full_ammo, "value": full_value},
        EMPTY: {"ammo": empty_ammo, "value": empty_value},
    }}


def proof():
    return {
        "nominal_cost": native_matrix.NOMINAL_COST, "charged": 75,
        "before": eather(0.625, 80, 1000, 10, 50),
        "after": eather(0.625, 15, 200),
    }


class RegressionMatrixTests(unittest.TestCase):
    def setup_anchor(self, destination=DEAD_BRIDGE):
        return native_matrix.record(party.new_party(), CUBE, destination, [12, 34, 56], [0, 90, 0])

    def begin(self, state, debit=None):
        return native_matrix.begin(state, TRIP, CUBE, CROSSROADS, 1, proof() if debit is None else debit)

    def test_exact_anchor_is_bounded_and_revisioned_per_cube(self):
        old = self.setup_anchor()
        self.assertEqual(native_matrix.anchor(old, CUBE), {
            "map": DEAD_BRIDGE, "position": [12.0, 34.0, 56.0], "rotation": [0.0, 90.0, 0.0], "revision": 1,
        })
        self.assertIs(native_matrix.record(old, CUBE, DEAD_BRIDGE, [12, 34, 56], [0, 90, -0.0]), old)
        new = native_matrix.record(old, CUBE, OBSERVATORY, [23, 45, 67], [0, -40, 0])
        self.assertEqual(native_matrix.anchor(new, CUBE)["revision"], 2)
        self.assertEqual(native_matrix.anchor(old, CUBE)["revision"], 1)
        self.assertIsNone(native_matrix.anchor(new, SECOND_CUBE))

        for world, position, rotation in (
            (EXCHANGE, [0, 0, 0], [0, 0, 0]),
            (DEAD_BRIDGE, [0, float("inf"), 0], [0, 0, 0]),
            (DEAD_BRIDGE, [0, 0, 0], [0, float("nan"), 0]),
            (DEAD_BRIDGE, [0, 0, 0], [0, True, 0]),
            (DEAD_BRIDGE, [0, 0, 0], [0, 361, 0]),
            (DEAD_BRIDGE, [0, 0, 100_000_001], [0, 0, 0]),
        ):
            with self.subTest(world=world, position=position, rotation=rotation):
                with self.assertRaises(ValueError):
                    native_matrix.record(old, CUBE, world, position, rotation)

    def test_pending_intent_is_unique_and_pins_exact_anchor_revision(self):
        state = self.setup_anchor()
        active = self.begin(state)
        self.assertIsNone(native_matrix.pending(state))
        self.assertIs(self.begin(active), active)
        self.assertEqual(native_matrix.pending(active)["destination"], DEAD_BRIDGE)
        self.assertEqual(native_matrix.pending(active)["anchor_revision"], 1)
        self.assertEqual(native_matrix.pending(active)["phase"], "planned")
        with self.assertRaisesRegex(ValueError, "revision changed"):
            native_matrix.begin(state, TRIP, CUBE, CROSSROADS, 2, proof())
        with self.assertRaisesRegex(ValueError, "in use"):
            native_matrix.record(active, CUBE, CROSSROADS, [0, 0, 0], [0, 0, 0])
        separate = native_matrix.record(active, SECOND_CUBE, CROSSROADS, [0, 0, 0], [0, 0, 0])
        with self.assertRaisesRegex(ValueError, "different Regression Matrix trip"):
            native_matrix.begin(separate, SECOND_TRIP, SECOND_CUBE, CROSSROADS, 1, proof())

    def test_restart_classifies_exact_before_and_after_without_reapplying(self):
        cargo = proof()
        active = json.loads(json.dumps(self.begin(self.setup_anchor(), cargo)))
        self.assertEqual(native_matrix.debit_status(active, TRIP, cargo["before"]), "before")
        self.assertEqual(native_matrix.debit_status(active, TRIP, cargo["after"]), "after")
        with self.assertRaisesRegex(ValueError, "has not been applied"):
            native_matrix.commit_debit(active, TRIP, cargo["before"])
        for wrong in (eather(0.5, 15, 200), eather(0.625, 14, 200), eather(0.625, 15, 205)):
            with self.assertRaisesRegex(ValueError, "does not match"):
                native_matrix.debit_status(active, TRIP, wrong)
        paid = native_matrix.commit_debit(active, TRIP, cargo["after"])
        self.assertIs(native_matrix.commit_debit(paid, TRIP, cargo["after"]), paid)
        self.assertIs(self.begin(paid), paid)
        with self.assertRaisesRegex(ValueError, "does not match"):
            native_matrix.debit_status(paid, TRIP, cargo["before"])
        self.assertEqual(native_matrix.pending(active)["phase"], "planned")
        self.assertEqual(native_matrix.pending(paid)["phase"], "debited")

    def test_verified_trip_settles_same_map_and_cross_map_once(self):
        for destination in (CROSSROADS, DEAD_BRIDGE):
            with self.subTest(destination=destination):
                active = self.begin(self.setup_anchor(destination))
                with self.assertRaisesRegex(ValueError, "paid trip"):
                    native_matrix.arrive(active, TRIP, destination)
                paid = native_matrix.commit_debit(active, TRIP, proof()["after"])
                with self.assertRaisesRegex(ValueError, "paid trip"):
                    native_matrix.arrive(paid, TRIP, OBSERVATORY)
                completed = native_matrix.arrive(paid, TRIP, destination)
                self.assertIsNone(native_matrix.pending(completed))
                self.assertEqual(native_matrix.anchor(completed, CUBE), native_matrix.anchor(active, CUBE))
                receipt = native_matrix.ledger(completed)["completed"][TRIP]
                self.assertEqual(receipt["in_zone"], destination == CROSSROADS)
                self.assertEqual(receipt["destination"], destination)
                self.assertEqual(receipt["charged"], 75)
                self.assertIs(native_matrix.arrive(completed, TRIP, destination), completed)
                self.assertIs(native_matrix.commit_debit(completed, TRIP, proof()["after"]), completed)
                with self.assertRaisesRegex(ValueError, "cannot be reapplied"):
                    native_matrix.debit_status(completed, TRIP, proof()["before"])

                rerecorded = native_matrix.record(completed, CUBE, OBSERVATORY, [0, 0, 0], [0, 0, 0])
                self.assertIs(self.begin(rerecorded), rerecorded)
                with self.assertRaisesRegex(ValueError, "different receipt"):
                    native_matrix.begin(rerecorded, TRIP, CUBE, CROSSROADS, 2, proof())
                reused = native_matrix.begin(rerecorded, SECOND_TRIP, CUBE, CROSSROADS, 2, proof())
                self.assertEqual(native_matrix.pending(reused)["destination"], OBSERVATORY)

    def test_native_result_can_be_journaled_after_a_quote_without_predicting_canister_order(self):
        observed = proof()
        intent = dict(observed, after=None)
        planned = self.begin(self.setup_anchor(), intent)
        self.assertEqual(native_matrix.debit_status(planned, TRIP, observed["before"]), "before")
        with self.assertRaisesRegex(ValueError, "does not match"):
            native_matrix.debit_status(planned, TRIP, observed["after"])
        with self.assertRaisesRegex(ValueError, "does not match the committed intent"):
            native_matrix.commit_native_debit(planned, TRIP, dict(observed, before=eather(0.5, 80, 1000, 10, 50)))
        paid = native_matrix.commit_native_debit(planned, TRIP, observed)
        self.assertIs(native_matrix.commit_native_debit(paid, TRIP, observed), paid)
        self.assertIs(self.begin(paid, intent), paid)
        arrived = native_matrix.arrive(paid, TRIP, DEAD_BRIDGE)
        self.assertIs(self.begin(arrived, intent), arrived)

    def test_native_proof_does_not_invent_a_cost_or_drop_stock_canisters(self):
        origin = self.setup_anchor()
        invalid = [dict(proof(), nominal_cost=50), dict(proof(), charged=74),
                   dict(proof(), after={"remainder": 0.625, "canisters": {FULL: {"ammo": 15, "value": 200}}}),
                   dict(proof(), after=eather(0.625, 0, 200, 15, 10)),
                   dict(proof(), after=eather(1, 15, 200))]
        for debit in invalid:
            with self.subTest(debit=debit):
                with self.assertRaises(ValueError):
                    self.begin(origin, debit)
        exact_noop = dict(proof(), charged=0, before=eather(0, 80, 1000), after=eather(0.0, 80, 1000))
        active = self.begin(origin, exact_noop)
        self.assertEqual(native_matrix.debit_status(active, TRIP, exact_noop["after"]), "after")
        self.assertEqual(native_matrix.pending(native_matrix.commit_debit(active, TRIP, exact_noop["after"]))["phase"],
                         "debited")
