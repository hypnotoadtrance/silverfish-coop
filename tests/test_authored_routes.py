from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from tools.silverfish.__main__ import execute, parser
from tools.silverfish.authored_routes import AUTHORED_ROUTES, MAPS, ROUTE_MAPS
from tools.silverfish.control import write_json
from tools.silverfish.generate_authored_exit_registry import render, render_wall_routes
from tools.silverfish.travel import (
    BEACH, CISTERN, CROSSROADS, DEAD_BRIDGE, EAST_WING, GOLD_NEXUS, NORTHEAST_RAIL, OBSERVATORY, RAIL_YARD,
    RUIN_FIELDS, SCI_TUNNEL, SCIENTIST_SHOP, SLUMS,
)


class AuthoredRouteCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_preserve_distinct_pads_and_require_a_fresh_connected_view(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "clients" / "scout" / "run" / "status.json"
            path.parent.mkdir(parents=True)
            session = {"active": True, "role": "client", "state": "connected", "updated_unix": time.time(),
                       "zone_epoch": "a" * 32, "gameplay_fresh": True,
                       "game": {"state": "connected", "map": GOLD_NEXUS}}
            write_json(path, session)
            options = parser().parse_args(["--work", directory, "routes", "--profile", "scout"])
            result = await execute(options)
            self.assertTrue(result["live"])
            self.assertEqual(result["zone_epoch"], session["zone_epoch"])
            self.assertEqual(len(result["routes"]), 7)
            pads = [row["actor"] for row in result["routes"] if row["entrance"] == "Nexus_CrossRoads"]
            self.assertEqual(pads, ["Level_Exit_BP_C_178", "Level_Exit_BP_C_181", "Level_Exit_BP_C_182"])

            for changes in ({"gameplay_fresh": False}, {"updated_unix": time.time() - 30},
                            {"game": {"state": "traveling", "map": GOLD_NEXUS}}):
                write_json(path, {**session, **changes})
                suppressed = await execute(options)
                self.assertFalse(suppressed["live"])
                self.assertEqual(suppressed["routes"], [])
                self.assertIsNone(suppressed["zone_epoch"])


class AuthoredRouteTests(unittest.TestCase):
    def test_all_catalog_maps_are_reachable_from_core(self):
        self.assertEqual(len(AUTHORED_ROUTES), 145)
        self.assertEqual(len(MAPS), 54)
        self.assertEqual(ROUTE_MAPS - MAPS, {GOLD_NEXUS})
        self.assertEqual(len({(row.source, row.actor) for row in AUTHORED_ROUTES}), len(AUTHORED_ROUTES))
        reached = {CROSSROADS, DEAD_BRIDGE, OBSERVATORY, CISTERN}
        while True:
            connected = reached | {row.destination for row in AUTHORED_ROUTES if row.source in reached}
            if connected == reached:
                break
            reached = connected
        self.assertEqual(reached, MAPS)

    def test_golden_nexus_has_seven_outbound_stock_pads(self):
        swamp = "/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P"
        pads = [entry for entry in AUTHORED_ROUTES if entry.source == GOLD_NEXUS]
        self.assertFalse(any(entry.destination == GOLD_NEXUS for entry in AUTHORED_ROUTES))
        expected = {
            178: (CROSSROADS, "Nexus_CrossRoads", 278), 179: (NORTHEAST_RAIL, "Nexus_M35", 705),
            180: (swamp, "Nexus_Swamp", 3), 181: (CROSSROADS, "Nexus_CrossRoads", 278),
            182: (CROSSROADS, "Nexus_CrossRoads", 278), 183: (SLUMS, "Nexus_Slums", 287),
            184: (BEACH, "Nexus_Beach", 139),
        }
        self.assertEqual(len(pads), len(expected))
        native, walls = render(), render_wall_routes()
        self.assertIn(f'inline constexpr World GoldNexus_P{{STR("{GOLD_NEXUS}.GoldNexus_P")', native)
        self.assertIn(f'["{GOLD_NEXUS}"] = {{', walls)
        for actor_id, (destination, tag, marker_id) in expected.items():
            actor = f"Level_Exit_BP_C_{actor_id}"
            pad, = [entry for entry in pads if entry.actor == actor]
            self.assertEqual((pad.destination, pad.entrance, pad.arrival_actor, pad.kind, pad.travel_time_step),
                             (destination, tag, f"LevelExit_Spawn_BP_C_{marker_id}", "overlap", 1))
            self.assertIn(f'{{ actor = "{actor}", kind = "overlap", entrance = "{tag}" }}', walls)

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
