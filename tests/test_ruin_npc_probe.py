"""Focused census coverage for inactive stock Ruin NPC roles."""

import unittest

from tests.ruin_npc_probe import REFRESH_MARKER, RUIN_LEVEL, compare, decode, decode_refresh


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

    def test_matches_the_classes_declared_by_a_mixed_ambient_receipt(self):
        stag, stag_row = stock_actor("NPC_MinorStag_C", 1, 3, (1000, -2000, 20))
        doe, doe_row = stock_actor("NPC_Doe_C", 2, 3, (1010, -2000, 20))
        proxy_stag, proxy_stag_row = stock_actor("NPC_MinorStag_C", 3, 1, (1004, -2000, 20))
        proxy_doe, proxy_doe_row = stock_actor("NPC_Doe_C", 4, 1, (1013, -2000, 20))
        worker, worker_row = stock_actor("NPC_Survivor_Worker_C", 5, 3, (0, 0, 250))
        host = decode(["host", stag_row, doe_row], "host")
        guest = decode(["client", proxy_stag_row, proxy_doe_row, worker_row], "client")
        report = compare(host, guest, {stag, doe}, classes={host[stag]["class"], host[doe]["class"]})
        self.assertEqual({owner: row["replica"] for owner, row in report["matched"].items()},
                         {stag: proxy_stag, doe: proxy_doe})
        self.assertEqual(report["unsupported"]["guest"], {worker: guest[worker]})

    def test_refresh_preserves_unloaded_class_and_distinguishes_geometry_from_attempt(self):
        worker = "SquadData_C /Game/Classes/Libraries/DataAssets/Squads/Monsters/Surv_Workers.Surv_Workers"
        fields = ["client", "refresh", worker, REFRESH_MARKER, "false", "false", "true",
                  "NPC_Survivor_Worker=false", "4500", "4", "restored", "12", "12", "13", "12", "1"]
        report = decode_refresh("\t".join(fields), "client")
        self.assertFalse(report["original_geometry"])
        self.assertTrue(report["geometry_override"])
        self.assertFalse(report["classes_loaded_before"]["NPC_Survivor_Worker"])
        self.assertTrue(report["population_cap_raised"])
        self.assertEqual(report["stock_population_during_refresh"], 12)
        fields[11:] = ["7", "12", "12", "7", "1"]
        self.assertFalse(decode_refresh("\t".join(fields), "client")["population_cap_raised"])
        fields[5:7] = ["nil", "false"]
        fields[14:] = ["nil", "0"]
        no_body = decode_refresh("\t".join(fields), "client")
        self.assertIsNone(no_body["stock_geometry_during_refresh"])
        self.assertIsNone(no_body["stock_population_during_refresh"])
        fields[6] = "true"
        with self.assertRaises(AssertionError):
            decode_refresh("\t".join(fields), "client")

    def test_rejects_client_authority_and_out_of_range_roles(self):
        authority, owner_row = stock_actor("NPC_Zomb_01_C", 1, 3, (1000, -2000, 20))
        _, wrong_role = stock_actor("NPC_Zomb_01_C", 2, 3, (1004, -2000, 20))
        _, unknown_role = stock_actor("NPC_FeralDog_C", 3, 4, (0, 0, 0))
        with self.assertRaises(AssertionError):
            compare(decode(["host", owner_row], "host"), decode(["client", wrong_role], "client"), {authority})
        with self.assertRaises(AssertionError):
            decode(["client", unknown_role], "client")
