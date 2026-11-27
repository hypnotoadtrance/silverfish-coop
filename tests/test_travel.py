import asyncio
from dataclasses import replace
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish import native_enemy_loot, native_loot
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.link import HostLink
from tools.silverfish.native_roster import RosterEnrollment
from tools.silverfish.runtime import Bridge
from tools.silverfish.service import Host

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

    def test_approved_same_map_placement_still_enforces_the_party_radius(self):
        positions = {"host": pawn(), "guest": pawn((1500, 0, 0))}
        roles = ("host", "guest")
        self.assertFalse(travel_decision("host", SOURCE, SOURCE, roles, positions)["allowed"])
        self.assertTrue(travel_decision("host", SOURCE, SOURCE, roles, positions,
                                        allow_same_zone=True)["allowed"])
        positions["guest"] = pawn((1501, 0, 0))
        blocked = travel_decision("host", SOURCE, SOURCE, roles, positions, allow_same_zone=True)
        self.assertEqual(blocked["blockers"], [{"id": "guest", "reason": "too_far"}])


class AuthoredCrossingEventTests(unittest.TestCase):
    def test_mounted_campaign_world_can_publish_stock_player_identity(self):
        area = "/CommunityWorld/Maps/Outpost"
        pawn = f"Player_FP_C {area}.Outpost:PersistentLevel.Player_FP_C_1"
        bridge = Bridge(Path("unused"), Mock())
        status = ("schema\t1\nseq\t7\n" f"session\thost\thosting\t{area}\t0\t0\n"
                  f"native-avatar-outfit\t5\t{pawn}\t-\t-\n")
        frame = bridge._parse_status(status)
        self.assertEqual(frame["native_avatar_outfits"]["5"]["pawn"], pawn)
        self.assertIsNone(bridge._parse_status(status.replace(".Outpost:", ".Other:")))
        self.assertIn("Invalid authoritative outfit identity", bridge.status_error)

    def test_unregistered_exit_uses_its_live_route_and_requires_a_crossing_ticket(self):
        route = next(row for row in AUTHORED_ROUTES if row.kind == "overlap")
        fields = ["authored-request", "4", route.source, route.destination, "LiveEntrance_8", "CustomExit_72",
                  "discovered-overlap", "37", "19"]
        pending = ["\t".join(row).encode() for row in (
            fields, fields[:-1], [*fields[:5], route.actor, *fields[6:]],
            [*fields[:3], "/Game/Classes/Maps/../Other", *fields[4:]], [*fields[:7], "2147483648", "19"],
        )]
        bridge = Bridge(Path("unused"), Mock())
        bridge.local_bridge = Mock(input_ready=lambda: bool(pending), read_input=lambda limit: [
            pending.pop(0) for _ in range(min(limit, len(pending)))
        ])
        (sequence, live_route, ticket), = bridge.authored_zone_requests()
        self.assertEqual((sequence, ticket), (4, 19))
        self.assertEqual((live_route.source, live_route.destination, live_route.kind, live_route.discovered),
                         (route.source, route.destination, "overlap", True))
        self.assertEqual((live_route.entrance, live_route.actor, live_route.travel_time_step),
                         ("LiveEntrance_8", "CustomExit_72", 37))

    def test_discovered_door_requires_a_ticket_from_the_native_hook(self):
        door = next(route for route in AUTHORED_ROUTES if route.kind == "door")
        fields = ("authored-request", "5", door.source, door.destination, "BackDoor", "RuntimeDoor_22",
                  "discovered-door", "12")
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(Path(directory), Mock())
            (bridge.directory / "native-authored-exit-events").write_text(
                "\t".join((*fields, "2")) + "\n" + "\t".join(fields) + "\n", encoding="utf-8")
            (sequence, route, ticket), = bridge.authored_zone_requests()
        self.assertEqual((sequence, route.kind, route.discovered, ticket), (5, "door", True, 2))
        self.assertEqual((route.entrance, route.arrival_actor, route.travel_time_step), ("BackDoor", "-", 12))

    def test_only_exact_stock_overlaps_carry_a_positive_ticket(self):
        overlap = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        door = next(route for route in AUTHORED_ROUTES if route.kind == "door")

        def event(sequence, route, *tail):
            step = "-" if route.travel_time_step is None else str(route.travel_time_step)
            return "\t".join(("authored-request", str(sequence), route.source, route.destination, route.entrance,
                              route.actor, route.kind, step, *tail)) + "\n"

        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(Path(directory), Mock())
            (bridge.directory / "native-authored-exit-events").write_text(
                event(1, door) + event(2, overlap) + event(3, overlap, "17") + event(4, door, "18")
                + event(5, overlap, "0") + event(6, overlap, str(2**53)), encoding="utf-8", newline="")
            self.assertEqual(bridge.authored_zone_requests(), [(1, door, None), (2, overlap, None), (3, overlap, 17)])
            self.assertEqual(bridge.authored_zone_requests(), [])
            standard = f"request\t7\t{SOURCE}\t{DESTINATION}".encode()
            pending = [event(1, door).strip().encode(), standard, event(3, overlap, "17").strip().encode(),
                       event(4, door, "18").strip().encode()]
            bridge.local_bridge = Mock(input_ready=lambda: bool(pending), read_input=lambda limit: [
                pending.pop(0) for _ in range(min(limit, len(pending)))
            ])
            # Other collectors can drain the native FIFO first without losing either exit type.
            self.assertEqual(bridge.native_crate_inputs(), [])
            self.assertEqual(bridge.native_zone_requests(), [(7, SOURCE, DESTINATION, None)])
            self.assertEqual(bridge.authored_zone_requests(), [(1, door, None), (3, overlap, 17)])
            self.assertEqual(bridge.native_zone_requests(), [])
            self.assertEqual(bridge.authored_zone_requests(), [])


class DepartureGateTests(unittest.IsolatedAsyncioTestCase):
    def host(self, source, destination):
        host = Host.__new__(Host)
        host.last_game = self.frame(source, 1, 1500)
        host.last_seen = time.monotonic()
        host.party = {"wipe": None, "pending_spawns": {}}
        host.allowed_maps = frozenset((source, destination))
        host.native_profile = object()
        host.native_guest = False
        host.expanded_zones = True
        host.enrollment = None
        host.world_flush_supported = True
        host.transitioning = host.stopping = False
        host.roles = Mock(return_value={"host": "host", "guest": "scout"})
        host.item_travel_pending = Mock(return_value=False)
        host.control = Mock()
        host.begin_zone_guests = Mock()
        host.flush_native_world = AsyncMock()

        async def checkpoint(_bridge, _candidate=None):
            if host.last_game["map"] != source:
                return 2
            host.last_game = self.frame(source, 2, 1500)
            return 1

        host.checkpoint_native = AsyncMock(side_effect=checkpoint)
        return host

    @staticmethod
    def frame(source, sequence, distance, *, driver="d1", guarded=False):
        guest_controller, guest_pawn = "PlayerControler_FP_C guest-controller", "Player_FP_C guest-pawn"
        return {
            "mode": "host", "state": "hosting", "map": source, "sequence": sequence,
            "server_connection": False, "remote_clients": 1, "native_zone_party_guard": guarded,
            "native_roster": {"state": "qualified", "driver": driver, "count": 1, "connections": [
                {"state": "ready", "id": "c1", "connection": "IpConnection game-guest",
                 "controller": guest_controller, "pawn": guest_pawn},
            ]},
            "pawns": {
                "local": {"name": "host-pawn", "controller": "host-controller", "location": [0, 0, 0], "health": 100},
                "guest": {"name": guest_pawn, "controller": guest_controller,
                          "location": [distance, 0, 0], "health": 100},
            },
        }

    def bridge(self, checkpoint_frame, latest_frame):
        async def wait_for(condition, *, timeout):
            self.assertFalse(condition(checkpoint_frame))
            if latest_frame is None:
                raise TimeoutError
            self.assertTrue(condition(latest_frame))
            return latest_frame

        return Mock(command=AsyncMock(side_effect=AssertionError("Denied travel reached the game bridge")),
                    wait_for=AsyncMock(side_effect=wait_for))

    async def test_overlap_arrival_uses_live_tag_while_door_keeps_its_placed_identity(self):
        overlap = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        door = next(route for route in AUTHORED_ROUTES if route.kind == "door")
        for route in (overlap, door):
            host = self.host(route.source, route.destination)
            basename = route.destination.rsplit("/", 1)[-1]
            level = f"{route.destination}.{basename}:PersistentLevel."
            actor = (f"CommunityArrival_C {level}RenamedMarker_5" if route is overlap
                     else f"Dungeon_TransitionDoor_BP_C {level}{route.arrival_actor}")
            proof = {"destination": route.destination, "actor": actor, "placement": "arrow", "location": [1, 2, 3]}
            snapshot = {"state": "hosting", "map": route.destination, "native_authored_arrival": proof}
            bridge = Mock(command=AsyncMock(return_value=snapshot))
            self.assertIs(await host.authored_arrival(bridge, route), snapshot)
            bridge.command.assert_awaited_once_with(
                "native-authored-arrival", route.arrival_entrance or route.entrance, route.kind,
                route.arrival_actor if route is door else "-", timeout=7)
            proof["actor"] = actor.replace(level, "/Game/Other.Other:PersistentLevel.")
            with self.assertRaisesRegex(RuntimeError, "arrival could not be verified"):
                await host.authored_arrival(bridge, route)

    async def test_overlap_arrival_retries_missing_marker_in_a_loading_sublevel(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        host = self.host(route.source, route.destination)
        fallback = self.frame(route.destination, 4, 0)
        fallback["native_authored_arrival"] = {
            "destination": route.destination, "actor": "host-pawn",
            "placement": "stock-spawn:entrance-unavailable", "location": [1, 2, 3],
        }
        arrived = self.frame(route.destination, 5, 0)
        arrived["native_authored_arrival"] = {
            "destination": route.destination, "actor": "ModArrival_C /Game/Mod/Streamed.Streamed:PersistentLevel.Marker",
            "placement": "streamed-arrow", "location": [4, 5, 6],
        }
        bridge = Mock(command=AsyncMock(side_effect=(fallback, arrived)))
        self.assertIs(await host.authored_arrival(bridge, route), arrived)
        self.assertEqual(bridge.command.await_count, 2)

    async def test_overlap_arrival_can_keep_verified_stock_spawn_when_tag_is_ambiguous(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        host = self.host(route.source, route.destination)
        proof = {"destination": route.destination, "actor": "host-pawn",
                 "placement": "stock-spawn:duplicate-entrance-tag", "location": [1, 2, 3]}
        snapshot = self.frame(route.destination, 4, 0)
        snapshot["native_authored_arrival"] = proof
        bridge = Mock(command=AsyncMock(return_value=snapshot))
        self.assertIs(await host.authored_arrival(bridge, route), snapshot)
        host.control.event.assert_called_once_with("native_authored_arrival_fallback", map=route.destination,
                                                  entrance=route.entrance, reason="duplicate-entrance-tag")
        proof["actor"] = "some-other-pawn"
        with self.assertRaisesRegex(RuntimeError, "arrival could not be verified"):
            await host.authored_arrival(bridge, route)

    async def test_ordinary_and_authored_travel_recheck_positions_after_checkpoint(self):
        route = AUTHORED_ROUTES[0]
        for source, destination, plan in ((SOURCE, DESTINATION, None), (route.source, route.destination, route)):
            with self.subTest(source=source):
                host = self.host(source, destination)
                bridge = self.bridge(self.frame(source, 2, 1500), self.frame(source, 3, 1501))
                if plan:
                    result = await host.travel_authored(bridge, None, plan)
                else:
                    result = await host.travel(bridge, None, destination)
                self.assertEqual(result, {"ok": False, "error": "travel_blocked",
                                          "blockers": [{"id": "scout", "reason": "too_far"}]})
                host.flush_native_world.assert_awaited_once_with(bridge)
                host.checkpoint_native.assert_awaited_once_with(bridge)
                host.begin_zone_guests.assert_not_called()
                bridge.command.assert_not_awaited()
                self.assertFalse(host.transitioning)
                self.assertFalse(host.stopping)

    async def test_a_stock_departure_hold_leaves_the_live_party_on_the_source_map(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        for refusal in ("stock_kill_volume_recovery_in_progress", "stock_live_dig_checkpoint_pending"):
            with self.subTest(refusal=refusal):
                host = self.host(route.source, route.destination)
                host.last_game = self.frame(route.source, 1, 1500, guarded=True)
                source = self.frame(route.source, 3, 1500, guarded=True)
                bridge = self.bridge(self.frame(route.source, 2, 1500), source)
                bridge.command.side_effect = None
                bridge.command.return_value = {"sequence": 3, "ack": {"message": refusal}}

                result = await host.travel_authored(bridge, None, route)

                self.assertEqual(result, {"ok": False, "error": refusal})
                self.assertIs(host.last_game, source)
                host.begin_zone_guests.assert_not_called()
                self.assertFalse(host.stopping)
                self.assertFalse(host.transitioning)

    async def test_a_compact_enemy_loot_hold_reads_a_fresh_source_snapshot(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        host = self.host(route.source, route.destination)
        host.native_dead_bridge_enemy_loot = True
        host.last_game = self.frame(route.source, 1, 1500, guarded=True)
        checked = self.frame(route.source, 3, 1500, guarded=True)
        fresh = self.frame(route.source, 4, 1500, guarded=True)
        bridge = self.bridge(self.frame(route.source, 2, 1500), checked)
        bridge.command.side_effect = (
            {"sequence": 3, "ack": {"message": native_enemy_loot.PENDING}},
            {"sequence": 4, "ack": {"message": native_enemy_loot.PENDING}},
        )

        async def wait_for(predicate, *, timeout):
            if bridge.wait_for.await_count == 1:
                self.assertTrue(predicate(checked))
                return checked
            self.assertFalse(predicate(checked))
            self.assertFalse(predicate(dict(fresh, map=route.destination)))
            self.assertTrue(predicate(fresh))
            return fresh

        bridge.wait_for.side_effect = wait_for
        with (patch.dict(native_enemy_loot.BY_MAP, {route.source: Mock(actors=["fixture"])}),
              patch.object(native_enemy_loot, "pending", return_value=True),
              patch.object(native_enemy_loot, "observe", new_callable=AsyncMock, return_value=False) as settle):
            result = await host.travel_authored(bridge, None, route)

        self.assertEqual(result, {"ok": False, "error": native_enemy_loot.PENDING})
        settle.assert_awaited_once_with(host, bridge)
        self.assertIs(host.last_game, fresh)
        host.begin_zone_guests.assert_not_called()
        self.assertFalse(host.stopping)

    async def test_crossing_reaches_live_native_guard_when_host_outruns_regroup_radius(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        host = self.host(route.source, route.destination)
        host.last_game = self.frame(route.source, 1, 1750, guarded=True)
        bridge = self.bridge(self.frame(route.source, 2, 1500), self.frame(route.source, 3, 1900, guarded=True))
        bridge.command.side_effect = None
        bridge.command.return_value = {"ack": {"message": "exit_crossing_unavailable"}}

        result = await host.travel_authored(bridge, None, route, exit_crossing=17)

        self.assertEqual(result, {"ok": False, "error": "exit_crossing_unavailable"})
        host.flush_native_world.assert_awaited_once_with(bridge)
        host.checkpoint_native.assert_awaited_once_with(bridge)
        bridge.command.assert_awaited_once_with(
            "travel-party", route.destination, route.source, f"{route.travel_time_step or 0}:17", "d1",
            "host-pawn", "host-controller", "c1", "IpConnection game-guest",
            "PlayerControler_FP_C guest-controller", "Player_FP_C guest-pawn")
        host.begin_zone_guests.assert_not_called()
        self.assertFalse(host.transitioning)
        self.assertFalse(host.stopping)

    async def test_discovered_route_requires_a_native_ticket_and_installed_destination(self):
        route = replace(next(route for route in AUTHORED_ROUTES if route.kind == "overlap"), discovered=True)
        host = self.host(route.source, route.destination)
        host.last_game = self.frame(route.source, 1, 500, guarded=True)
        bridge = Mock(command=AsyncMock(return_value={"ack": {"message": "map-package:missing"}}))
        self.assertEqual(await host.travel_authored(bridge, None, route),
                         {"ok": False, "error": "exit_crossing_unavailable"})
        bridge.command.assert_not_awaited()
        invalid = replace(route, destination="/CustomCampaign/Maps/Arena")
        self.assertEqual(await host.travel_authored(bridge, None, invalid, exit_crossing=17),
                         {"ok": False, "error": "destination_package_unavailable"})
        bridge.command.assert_awaited_once_with("native-map-package", invalid.destination, "register", timeout=3)
        host.checkpoint_native.assert_not_awaited()

    async def test_unlisted_world_requires_every_guest_and_matches_current_visit(self):
        route = replace(next(route for route in AUTHORED_ROUTES if route.kind == "overlap"), discovered=True,
                        destination="/Game/Classes/Maps/Custom/Arena")
        for installed in (False, True):
            host = self.host(route.source, route.source)
            host.last_game = self.frame(route.source, 1, 500, guarded=True)
            host.zone_epoch = "source-visit"
            host.roles.return_value = {"host": "host", "guest": "scout", "guest2": "miner"}
            host.travel_refusal = Mock(return_value=None)
            host.switch_zone = AsyncMock(return_value={"ok": True})
            bridge = Mock(command=AsyncMock(return_value={"ack": {"message": "map-package:world"}}))
            travel = asyncio.create_task(host.travel_authored(bridge, None, route, exit_crossing=17))
            await asyncio.sleep(0)
            preflight = host.map_preflight
            self.assertIsNotNone(preflight)
            nonce, package = preflight.offer("scout")
            self.assertEqual(package, route.destination)
            self.assertIsNone(preflight.offer("outsider"))
            preflight.receive("miner", "expired-visit", [nonce, True])
            preflight.receive("scout", host.zone_epoch, [nonce, True])
            await asyncio.sleep(0)
            self.assertFalse(travel.done())
            preflight.receive("miner", host.zone_epoch, [nonce, installed])
            result = await travel
            self.assertIsNone(host.map_preflight)
            if installed:
                self.assertEqual(result, {"ok": True})
                self.assertIn(route.destination, host.allowed_maps)
                host.switch_zone.assert_awaited_once()
            else:
                self.assertEqual(result, {"ok": False, "error": "guest_destination_package_unavailable",
                                          "characters": ["miner"]})
                self.assertNotIn(route.destination, host.allowed_maps)
                host.switch_zone.assert_not_awaited()
                host.checkpoint_native.assert_not_awaited()

    async def test_crossing_cannot_survive_an_application_roster_change(self):
        route = next(route for route in AUTHORED_ROUTES if route.kind == "overlap")
        host = self.host(route.source, route.destination)
        host.last_game = self.frame(route.source, 1, 1750, guarded=True)
        host.roles.side_effect = [{"host": "host", "guest": "scout"}, {"host": "host", "guest": "new-scout"}]
        bridge = self.bridge(self.frame(route.source, 2, 1500), self.frame(route.source, 3, 1900, guarded=True))

        self.assertEqual(await host.travel_authored(bridge, None, route, exit_crossing=17),
                         {"ok": False, "error": "travel_source_changed"})
        bridge.command.assert_not_awaited()

    async def test_rejects_departure_without_fresh_same_world_census(self):
        for latest, error in ((None, "game_snapshot_unavailable"),
                              (self.frame(SOURCE, 3, 500, driver="d2"), "travel_source_changed"),
                              (self.frame(DESTINATION, 3, 500), "travel_source_changed")):
            with self.subTest(error=error, latest=latest):
                host = self.host(SOURCE, DESTINATION)
                bridge = self.bridge(self.frame(SOURCE, 2, 1500), latest)
                self.assertEqual(await host.travel(bridge, None, DESTINATION), {"ok": False, "error": error})
                host.begin_zone_guests.assert_not_called()
                bridge.command.assert_not_awaited()
                self.assertFalse(host.transitioning)
                self.assertFalse(host.stopping)

    async def test_guarded_departure_checks_live_party_before_retiring_and_reopens_after_accept(self):
        route = AUTHORED_ROUTES[0]
        for acknowledgement in ("travel_blocked:1", "zone travel requested"):
            with self.subTest(acknowledgement=acknowledgement):
                host = self.host(route.source, route.destination)
                host.internal_port = 17000
                host.last_game["native_zone_loading_support"] = True
                host.zone_epoch = "source"
                host.crate_publication = native_loot.RollPublication()
                host.public_gameplay = Mock(return_value={})
                host.relay = host.prop_carry_bridge = None
                transport = HostLink(("127.0.0.1", 17000), Mock(), None)
                transport.restrict_game_routes({"old-guest"})
                source = self.frame(route.source, 3, 1500, guarded=True)
                destination = self.frame(route.destination, 4, 0, driver="d2", guarded=True)
                bridge = self.bridge(self.frame(route.source, 2, 1500), source)
                host.authored_arrival = AsyncMock(return_value=destination)

                async def depart(*fields, **options):
                    if fields[0] == "native-zone-loading-start":
                        self.assertEqual(host.snapshot_for_guest("peer", "guest")["loading_zone"], route.destination)
                        self.assertNotIn("zone_fade_from", host.snapshot_for_guest("peer", "guest"))
                        host.checkpoint_native.assert_awaited_once_with(bridge)
                        self.assertTrue(transport._native_route_permitted("old-guest"))
                        return {"native_zone_loading_cover": route.destination, "native_zone_loading_cue": {
                            "destination": route.destination, "seat": "host", "dispatched": True,
                        }}
                    if fields[0] == "native-zone-loading-clear":
                        self.assertNotIn("loading_zone", host.snapshot_for_guest("peer", "guest"))
                        return {}
                    self.assertEqual(fields, ("travel-party", route.destination, route.source,
                                              str(route.travel_time_step or 0), "d1", "host-pawn", "host-controller",
                                              "c1", "IpConnection game-guest", "PlayerControler_FP_C guest-controller",
                                              "Player_FP_C guest-pawn"))
                    self.assertFalse(transport._native_route_permitted("old-guest"))
                    host.begin_zone_guests.assert_not_called()
                    return {"ack": {"message": acknowledgement}}

                async def wait_for(condition, *, timeout):
                    if bridge.wait_for.await_count == 1:
                        self.assertTrue(condition(source))
                        return source
                    self.assertTrue(condition(destination))
                    return destination

                def retire(active_transport):
                    self.assertFalse(active_transport._native_route_permitted("old-guest"))
                    active_transport.restrict_game_routes(set())

                async def reopen(active_transport, roles, _prior):
                    self.assertFalse(active_transport._native_route_permitted("old-guest"))
                    self.assertEqual(roles, {"host": "host", "guest": "scout"})
                    active_transport.restrict_game_routes({"old-guest"})

                bridge.command.side_effect = depart
                bridge.wait_for.side_effect = wait_for
                host.begin_zone_guests.side_effect = retire
                host.reopen_zone_guests = AsyncMock(side_effect=reopen)
                with (patch("tools.silverfish.service.verify_loopback_socket"),
                      patch("tools.silverfish.service.asyncio.sleep", new_callable=AsyncMock) as fade):
                    result = await host.travel_authored(bridge, transport, route)
                fade.assert_awaited_once_with(1)
                self.assertEqual(bridge.command.await_args_list[0],
                                 call("native-zone-loading-start", route.destination, timeout=2))
                self.assertEqual(bridge.command.await_args_list[-1],
                                 call("native-zone-loading-clear", route.destination, timeout=2))
                host.control.event.assert_any_call("zone_transition_presentation", destination=route.destination,
                                                   seat="host", cue=True, covered=True)
                self.assertTrue(transport._native_route_permitted("old-guest"))
                snapshot = host.snapshot_for_guest("peer", "guest")
                if acknowledgement == "zone travel requested":
                    self.assertEqual(snapshot["zone_fade_from"], route.source)
                else:
                    self.assertNotIn("zone_fade_from", snapshot)
                self.assertFalse(transport._native_route_permitted("other-guest"))
                self.assertFalse(host.transitioning)
                self.assertFalse(host.stopping)
                if acknowledgement == "zone travel requested":
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["map"], route.destination)
                    self.assertIs(host.last_game, destination)
                    self.assertEqual(result["revision"], 2)
                    host.begin_zone_guests.assert_called_once_with(transport)
                    host.reopen_zone_guests.assert_awaited_once()
                else:
                    self.assertEqual(result, {"ok": False, "error": "travel_blocked",
                                              "blockers": [{"id": "scout", "reason": "too_far"}]})
                    host.begin_zone_guests.assert_not_called()
                    host.reopen_zone_guests.assert_not_awaited()

    async def test_observatory_travel_waits_for_the_new_empty_roster_before_guest_reservation(self):
        for arrival_has_pawn in (True, False):
            with self.subTest(arrival_has_pawn=arrival_has_pawn):
                await self.verify_observatory_roster_settles(arrival_has_pawn)

    async def verify_observatory_roster_settles(self, arrival_has_pawn):
        route = next(route for route in AUTHORED_ROUTES
                     if route.source.endswith("/Observitory_P") and route.destination.endswith("/CrossRoads_P"))
        host = self.host(route.source, route.destination)
        host.internal_port = 17000
        host.guest = {"guest_id": "old-guest", "character_id": "scout"}
        host.enrollment = RosterEnrollment()
        host.saved_native_guest = Mock(return_value=None)
        host.begin_zone_guests.side_effect = lambda _transport: host.enrollment.begin_travel()
        source = self.frame(route.source, 3, 1500, guarded=True)
        host.enrollment.observe(source)
        listener = self.frame(route.destination, 5, 0, driver="d2", guarded=True)
        listener["remote_clients"] = 0
        listener["native_roster"].update(count=0, connections=[])
        arrival = {**listener, "sequence": 4, "native_roster": {"state": "unavailable"},
                   "native_authored_arrival": {"actor": route.arrival_actor}}
        if not arrival_has_pawn:
            arrival["pawns"] = {}
        old_driver = {**listener, "native_roster": {**listener["native_roster"], "driver": "d1"}}
        other_pawn = {**listener, "pawns": {"local": {"name": "another-host"}}}
        missing_pawn = {**listener, "pawns": {}}
        foreign_connection = self.frame(route.destination, 5, 0, driver="d2", guarded=True)
        transport = HostLink(("127.0.0.1", 17000), Mock(), None)
        transport.restrict_game_routes({"old-guest"})
        bridge = Mock(command=AsyncMock(return_value={"ack": {"message": "zone travel requested"}}))
        host.authored_arrival = AsyncMock(return_value=arrival)

        async def wait_for(condition, *, timeout):
            if bridge.wait_for.await_count == 1:
                self.assertTrue(condition(source))
                return source
            if bridge.wait_for.await_count == 2:
                self.assertTrue(condition(arrival))
                return arrival
            self.assertFalse(transport._native_route_permitted("old-guest"))
            self.assertIsNone(host.enrollment.pending)
            for premature in (source, arrival, old_driver, missing_pawn, foreign_connection):
                self.assertFalse(condition(premature), premature)
            if arrival_has_pawn:
                self.assertFalse(condition(other_pawn))
            self.assertTrue(condition(listener))
            return listener

        async def refresh():
            self.assertFalse(transport._native_route_permitted("old-guest"))
            self.assertIs(host.last_game, listener)

        bridge.wait_for = AsyncMock(side_effect=wait_for)
        transport.refresh_game_routes = AsyncMock(side_effect=refresh)
        with patch("tools.silverfish.service.verify_loopback_socket") as verify_listener:
            result = await host.travel_authored(bridge, transport, route)

        self.assertTrue(result["ok"], result)
        self.assertFalse(host.stopping)
        self.assertIs(host.last_game, listener)
        self.assertEqual(host.awaiting_zone_guest, "scout")
        reservation = host.enrollment.pending
        self.assertEqual((reservation.guest_id, reservation.character_id, reservation.driver),
                         ("old-guest", "scout", "d2"))
        verify_listener.assert_called_once_with(bridge.process.pid, 17000)
        transport.refresh_game_routes.assert_awaited_once()
        self.assertTrue(transport._native_route_permitted("old-guest"))
        self.assertFalse(transport._native_route_permitted("other-guest"))
        host.control.event.assert_any_call("zone_travel_ready", source=route.source, destination=route.destination,
                                           entrance=route.entrance, epoch=result["travel_epoch"], revision=2,
                                           authored_arrival=arrival["native_authored_arrival"])

    async def test_guarded_departure_rejects_unattributed_native_connection(self):
        host = self.host(SOURCE, DESTINATION)
        snapshot = self.frame(SOURCE, 3, 100, guarded=True)
        snapshot["remote_clients"] = snapshot["native_roster"]["count"] = 2
        snapshot["native_roster"]["connections"].append({
            "state": "joining", "id": "c2", "connection": "IpConnection unknown",
            "controller": None, "pawn": None,
        })
        bridge = self.bridge(self.frame(SOURCE, 2, 1500), snapshot)
        self.assertEqual(await host.travel(bridge, None, DESTINATION),
                         {"ok": False, "error": "native_roster_unavailable"})
        host.begin_zone_guests.assert_not_called()
        bridge.command.assert_not_awaited()

    async def test_refused_authored_request_releases_its_source_without_requiring_a_ready_scope(self):
        route = AUTHORED_ROUTES[0]
        for current, expected_release in ((route.source, True), (route.destination, False)):
            with self.subTest(current=current):
                host = self.host(route.source, route.destination)
                host.next_zone_heartbeat = float("inf")
                host.matrix_supported = False
                host.last_game = self.frame(current, 3, 100, guarded=True)
                host.travel_authored = AsyncMock(return_value={"ok": False, "error": "native_roster_unavailable"})
                bridge = Mock(command=AsyncMock(side_effect=RuntimeError("The source scope disappeared")),
                              native_rest_requests=Mock(return_value=()),
                              authored_zone_requests=Mock(return_value=((1, route, None),)),
                              native_cube_requests=Mock(return_value=()),
                              native_zone_requests=Mock(return_value=()))
                await host.service_native_zones(bridge, None, time.monotonic())
                if expected_release:
                    bridge.command.assert_awaited_once_with("native-exit-release", route.source, timeout=3)
                else:
                    bridge.command.assert_not_awaited()
                self.assertFalse(host.stopping)


if __name__ == "__main__":
    unittest.main()
