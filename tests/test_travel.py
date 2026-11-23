import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.link import HostLink
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
