import unittest

from tools.silverfish.authored_routes import AUTHORED_ROUTES, MAPS
from tools.silverfish.travel import CISTERN, CROSSROADS, DEAD_BRIDGE, OBSERVATORY, RAIL_YARD, SCI_TUNNEL


class AuthoredRouteTests(unittest.TestCase):
    def test_all_catalog_maps_are_reachable_from_core(self):
        self.assertEqual(len(AUTHORED_ROUTES), 131)
        self.assertEqual(len(MAPS), 52)
        self.assertEqual(len({(row.source, row.actor) for row in AUTHORED_ROUTES}), len(AUTHORED_ROUTES))
        reached = {CROSSROADS, DEAD_BRIDGE, OBSERVATORY, CISTERN}
        while True:
            connected = reached | {row.destination for row in AUTHORED_ROUTES if row.source in reached}
            if connected == reached:
                break
            reached = connected
        self.assertEqual(reached, MAPS)

    def test_overlaps_preserve_stock_marker_and_clock_step(self):
        route, = (entry for entry in AUTHORED_ROUTES if entry.source == CROSSROADS and entry.destination == RAIL_YARD)
        self.assertEqual((route.kind, route.entrance, route.actor, route.arrival_actor, route.travel_time_step),
                         ("overlap", "Depot_Cross_Rail", "Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_0", 20))
        self.assertIsNone(route.child)
        self.assertIsNone(route.arrival_child)

    def test_door_preserves_both_exact_child_exports(self):
        route, = (entry for entry in AUTHORED_ROUTES if entry.source == CROSSROADS and entry.destination == CISTERN)
        self.assertEqual((route.kind, route.entrance, route.actor, route.arrival_actor),
                         ("door", "CR_Cstrn", "Dungeon_TransitionDoor_BP_C_1", "Dungeon_TransitionDoor_BP_C_1"))
        self.assertEqual(route.child, "Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_268")
        self.assertEqual(route.arrival_child, "LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_178")

    def test_cistern_stock_lock_excludes_sci_tunnel_from_the_normal_graph(self):
        self.assertNotIn(SCI_TUNNEL, MAPS)
        self.assertFalse(any(entry.source == CISTERN and entry.destination == SCI_TUNNEL for entry in AUTHORED_ROUTES))

    def test_mismatched_observatory_dock_reverse_is_excluded(self):
        dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        self.assertTrue(any(entry.source == OBSERVATORY and entry.destination == dock
                            and entry.entrance == "Obs_Dock" for entry in AUTHORED_ROUTES))
        self.assertFalse(any(entry.source == dock and entry.destination == OBSERVATORY for entry in AUTHORED_ROUTES))
