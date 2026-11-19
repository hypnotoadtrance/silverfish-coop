import unittest

from tools.silverfish.authored_routes import AUTHORED_ROUTES, MAPS
from tools.silverfish.generate_authored_exit_registry import render, render_wall_routes
from tools.silverfish.travel import (
    CISTERN, CROSSROADS, DEAD_BRIDGE, EAST_WING, OBSERVATORY, RAIL_YARD, RUIN_FIELDS, SCI_TUNNEL, SCIENTIST_SHOP,
)


class AuthoredRouteTests(unittest.TestCase):
    def test_all_catalog_maps_are_reachable_from_core(self):
        self.assertEqual(len(AUTHORED_ROUTES), 138)
        self.assertEqual(len(MAPS), 54)
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

    def test_stock_gated_doors_and_reverse_routes_preserve_exact_children(self):
        for source, destination, tag, actor_id, arrival_id, child_id, arrival_child_id in (
            (CISTERN, SCI_TUNNEL, "Cistern_Sci", 2, 0, 346, 0),
            (EAST_WING, SCIENTIST_SHOP, "RgIn", 1, 4, 1, 588),
            (RUIN_FIELDS, SCIENTIST_SHOP, "Market_Flood", 2, 2, 191, 5),
        ):
            actor, marker = f"Dungeon_TransitionDoor_BP_C_{actor_id}", f"Dungeon_TransitionDoor_BP_C_{arrival_id}"
            with self.subTest(source=source, tag=tag):
                route, = (entry for entry in AUTHORED_ROUTES if entry.source == source and entry.actor == actor)
                self.assertEqual((route.destination, route.arrival_actor, route.entrance, route.kind),
                                 (destination, marker, tag, "door"))
                self.assertEqual(route.child, f"Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_{child_id}")
                self.assertEqual(route.arrival_child, f"LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_{arrival_child_id}")
                reverse, = (entry for entry in AUTHORED_ROUTES if entry.source == destination and entry.actor == marker)
                self.assertEqual((reverse.destination, reverse.arrival_actor, reverse.entrance), (source, actor, tag))
                self.assertEqual(reverse.child, f"Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_{arrival_child_id}")
                self.assertEqual(reverse.arrival_child, f"LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_{child_id}")
                self.assertIsNone(route.arrival_entrance)

    def test_observatory_dock_uses_a_destination_only_tag_override(self):
        dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        departing, = (entry for entry in AUTHORED_ROUTES if entry.source == OBSERVATORY and entry.destination == dock)
        returning, = (entry for entry in AUTHORED_ROUTES if entry.source == dock and entry.destination == OBSERVATORY)
        self.assertEqual((departing.entrance, departing.arrival_entrance), ("Obs_Dock", None))
        self.assertEqual((returning.actor, returning.arrival_actor, returning.travel_time_step),
                         ("Level_Exit_BP_C_1", "LevelExit_Spawn_BP_C_213", 10))
        self.assertEqual((returning.entrance, returning.arrival_entrance), ("Obs_Dock", "Obs_Docke"))
        native = render()
        walls = render_wall_routes()
        self.assertIn('Route{detail::Tut_Dock_P, detail::Observitory_P, Mode::overlap,\n'
                      '            STR("Level_Exit_BP_C_1"), STR("Obs_Dock"), STR("LevelExit_Spawn_BP_C_213"), 10}', native)
        self.assertIn(f'["{dock}"] = {{\n'
                      '        { actor = "Level_Exit_BP_C_1", kind = "overlap", entrance = "Obs_Dock" },', walls)
        self.assertNotIn("Obs_Docke", native + walls)
