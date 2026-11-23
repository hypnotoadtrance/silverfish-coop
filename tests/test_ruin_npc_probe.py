"""Focused census coverage for inactive stock Ruin NPC roles."""

import unittest

from tests.ruin_npc_probe import RUIN_LEVEL, compare, decode


def stock_actor(kind, identifier, role, position):
    name = f"{kind} {RUIN_LEVEL}{kind}_{identifier}"
    return name, "\t".join((kind, name, str(role), "true", "true", *(str(axis) for axis in position)))


class RuinNpcProbeTests(unittest.TestCase):
    def test_keeps_inactive_npcs_as_evidence_outside_active_matching(self):
        authority, owner_row = stock_actor("NPC_Zomb_01_C", 1, 3, (1000, -2000, 20))
        proxy, proxy_row = stock_actor("NPC_Zomb_01_C", 2, 1, (1004, -2000, 20))
        inactive_dog, inactive_dog_row = stock_actor("NPC_FeralDog_C", 3, 0, (0, 0, 0))
        inactive_husk, inactive_husk_row = stock_actor("NPC_Zomb_01_C", 4, 0, (1004, -2000, 20))
        worker, worker_row = stock_actor("NPC_Survivor_Worker_C", 5, 3, (0, 0, 250))
        host = decode(["host", owner_row, inactive_dog_row], "host")
        guest = decode(["client", proxy_row, inactive_husk_row, worker_row], "client")

        report = compare(host, guest, {authority})
        self.assertEqual(report["matched"][authority]["replica"], proxy)
        self.assertEqual(report["inactive"], {"host": {inactive_dog: host[inactive_dog]},
                                              "guest": {inactive_husk: guest[inactive_husk]}})
        self.assertEqual(report["unsupported"], {"host": {}, "guest": {worker: guest[worker]}})
        self.assertEqual(report["unobserved_authorities"], [])
        with self.assertRaisesRegex(ValueError, "outside the guest"):
            compare(host, {inactive_husk: guest[inactive_husk]}, {authority})

    def test_rejects_client_authority_and_out_of_range_roles(self):
        authority, owner_row = stock_actor("NPC_Zomb_01_C", 1, 3, (1000, -2000, 20))
        _, wrong_role = stock_actor("NPC_Zomb_01_C", 2, 3, (1004, -2000, 20))
        _, unknown_role = stock_actor("NPC_FeralDog_C", 3, 4, (0, 0, 0))
        with self.assertRaises(AssertionError):
            compare(decode(["host", owner_row], "host"), decode(["client", wrong_role], "client"), {authority})
        with self.assertRaises(AssertionError):
            decode(["client", unknown_role], "client")
