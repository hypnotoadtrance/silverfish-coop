"""Run the staged game and coordinate the Direct IP session."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from functools import partial
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import (link, matrix_service, native_blue, native_broker, native_dream, native_gold, native_green, native_guest_npc,
               native_guest_weapon, native_host_save, native_inventory, native_inventory_death, native_loot, native_matrix,
               native_purple, native_quest, native_red, native_rest, native_rewards, party, wipe)
from .campaign import CampaignError, CampaignStore, DEFAULT_RECONNECT_GRACE
from .authored_routes import AUTHORED_ROUTES, MAPS as EXPANDED_MAPS, AuthoredRoute
from .control import Control
from .native_cube_draw import SOURCES as RANDOM_CUBE_SOURCES
from .native_pickup import NativePickup, STAGED_SHOPS
from .native_roster import RosterEnrollment
from .travel import (AUTHORED_EXITS, AUTHORED_MAPS, BLACK_OCEAN, CISTERN, CITY, CROSSROADS, GOLD_NEXUS,
                     OBSERVATORY, PLAYTEST_MAPS, SLATE, travel_decision)
from .runtime import (Bridge, MAP, WORLD_WEATHER_FIELDS, launch, native_death_backend, qualified_authored_routes, read_json, spare_udp_port, staged_build,
                      staged_script, stop_game, verify_loopback_socket, write_json, write_text)


PROTOCOL = 2
POSITION_MAX_AGE = 3.0
WORLD_VIEW_ACTORS = {
    area: f"DayCycle_SunControler_BP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.DayCycle_SunControler_BP_C_{index}"
    for area, index in ((CROSSROADS, 1), (OBSERVATORY, 0), (CITY, 0),
                        ("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P", 0))
}
GUEST_NATIVE_ACTIONS = ("native-pickup", "native-shop-buy", "native-quest-start", "native-quest-turnin",
                        "native-inventory-list", "native-loot-inspect", "native-loot-open", "native-loot-claim",
                        "native-wipe-items", "native-wipe-recover",
                        *native_guest_weapon.GUEST_ACTIONS)
TRANSPORT_FIXTURES = ("stardust", "fruiting-stardust", "green", "blue", "red", "gold", "purple", "teleport-cube")
CUBE_RESULTS = {"inspect": ("present", "absent"), "debit": ("removed", "absent"),
                "seed": ("seeded", "present"), "use-probe": ("requested",), "action-probe": ("requested",)}
STARDUST_RESULTS = {"inspect": ("present", "absent"), "seed": ("seeded", "present"),
                    "debit": ("removed", "decremented", "already_debited"),
                    "restore": ("restored", "present"), "use-probe": ("requested",)}
DRAW_CUBES = {"red": native_red, "green": native_green, "purple": native_purple}


def _identity(directory: Path):
    file = directory / "host-identity.json"
    if file.exists():
        record = read_json(file, limit=32_768)
        return link.load_server_identity(record["certificate"], record["private_key"])
    identity = link.make_server_identity()
    private_key = identity.private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ).decode("ascii")
    write_json(file, {"certificate": identity.certificate_pem, "private_key": private_key})
    return identity


def _pawn(state: dict, role: str) -> dict:
    pawn = state.get("pawns", {}).get(role)
    if not pawn:
        return {}
    health = pawn["health"]
    return {"map": state["map"], "location": pawn["location"],
            "health": max(0, health) if health is not None else None}


def guest_arrival_positions(point: list[float], first: int = 0):
    x, y, z = point
    offsets = ((110, 0), (-110, 0), (0, 110), (0, -110))
    for height in (0, 50, 100):
        for dx, dy in offsets[first:] + offsets[:first]:
            yield [x + dx, y + dy, z + height]


def pending_cube(state: dict) -> tuple[str, dict] | None:
    pending = [(cube, receipt) for cube, module in (("gold", native_gold), ("blue", native_blue), *DRAW_CUBES.items())
               if (receipt := module.ledger(state)["pending"])]
    if len(pending) > 1:
        raise ValueError("The campaign has two pending item transports")
    return pending[0] if pending else None


def checkpoint_seed(world: dict) -> int | None:
    value = world.get("native_observation", {}).get("native_scene", {}).get("instance_seed")
    if type(value) in (int, float) and -2147483648 <= value <= 2147483647 and value == int(value):
        return int(value)
    return None


async def project_players(bridge: Bridge, game: dict, public: dict, *, client: bool, viewer_role: str = "guest") -> None:
    """Apply authority decisions to Unreal and the guest's movement prediction."""
    pawns = game.get("pawns", {})
    exact_names = public.get("native_pawns")
    for role, player in public.get("players", {}).items():
        local = role == (viewer_role if client else "host")
        if exact_names is not None:
            name = pawns.get("local", {}).get("name") if local else exact_names.get(role)
            bridge_role = next((alias for alias, body in pawns.items() if name and body.get("name") == name), None)
            operation, target = "player-state-exact", name
        else:
            bridge_role = "local" if local else "guest"
            operation, target = "player-state", bridge_role
        if bridge_role not in pawns:
            continue
        expected = "downed" if player["downed"] else "standing"
        actual = game.get("player_effects", {}).get(bridge_role, {}).get("state")
        if actual != expected:
            await bridge.command(operation, target, expected, timeout=3)


class Host:
    def __init__(self, store: CampaignStore, control: Control, game_build: str, identity,
                 bind: str, port: int, fixtures: bool, death_rules: dict | None, *,
                 native_backend: str | None, native_events: bool, native_guest: bool, native_zones: bool = False,
                 authored_zones: bool = False, native_zone_walls: bool = False, native_profile: Path | None = None,
                 verify_native_roster: bool = False, persist_native_guest: bool = False, matrix_supported: bool = False,
                 world_flush_supported: bool = False, expanded_zones: bool = False, exact_guest_binding: bool = False):
        self.store = store
        self.control = control
        self.game_build = game_build
        self.identity = identity
        self.bind = bind
        self.port = port
        self.guest = None
        self.guest_pawn = None
        self.last_game = {}
        self.stopping = False
        self.next_save = 0.0
        self.last_seen = 0.0
        self.fixtures = fixtures
        self.native_backend = native_backend
        self.native_events = native_events
        self.native_guest = native_guest
        self.native_zones = native_zones
        self.native_zone_walls = native_zone_walls
        self.native_profile = native_profile
        self.persist_native_guest = persist_native_guest
        self.exact_guest_binding = exact_guest_binding
        self.enrollment = RosterEnrollment() if verify_native_roster else None
        self.allowed_maps = AUTHORED_MAPS if authored_zones else PLAYTEST_MAPS
        self.expanded_zones = expanded_zones
        if authored_zones and expanded_zones:
            self.allowed_maps |= EXPANDED_MAPS
        self.next_zone_heartbeat = 0.0
        self.native_checkpoint_unsafe = False
        self.matrix_supported = matrix_supported
        self.world_flush_supported = world_flush_supported
        self.native_guest_health = None
        self.native_guest_position = None
        self.hazard_origin = None
        self.fall_origin = None
        self.native_pickup = NativePickup()
        self.native_requests = asyncio.Queue(maxsize=8)
        self.pending_native_deaths: list[tuple[str, str, str | None]] = []
        self.party = party.load_party(store.status()["world"].get("party"))
        pending_trips = pending_cube(self.party), native_dream.visit(self.party), native_matrix.pending(self.party)
        if sum(bool(item) for item in pending_trips) > 1:
            raise ValueError("The campaign has overlapping committed item transports")
        self.dream_blockers = None
        if death_rules is not None:
            if self.party["wipe"]:
                raise ValueError("Finish the existing party wipe before changing its settings")
            self.party["death_rules"] = death_rules
        self.marker_ready = False
        self.next_projection = 0.0
        self.zone_epoch = uuid.uuid4().hex
        self.transitioning = False
        self.awaiting_zone_guest = None

    def admit(self, request: dict) -> dict:
        if self.transitioning:
            return {"accepted": False, "reason": "travel_in_progress"}
        if request["protocol_version"] != PROTOCOL or request["game_build"] != self.game_build:
            return {"accepted": False, "reason": "incompatible_game_or_protocol"}
        if request.get("catalog") is True:
            try:
                self.store.list_characters(request["guest_id"], request["credential"])
            except CampaignError:
                return {"accepted": False, "reason": "guest_unavailable"}
            return {"accepted": True, "game_access": False, "session_id": self.control.generation}
        if self.guest and self.guest["guest_id"] != request["guest_id"]:
            if self.guest["disconnected"] is None or self.last_game.get("remote_clients", 0):
                return {"accepted": False, "reason": "party_full"}
            if time.monotonic() - self.guest["disconnected"] < DEFAULT_RECONNECT_GRACE:
                return {"accepted": False, "reason": "slot_reserved"}
        if (self.guest and self.guest["character_id"] != request["character_id"]
                and self.last_game.get("remote_clients", 0)):
            return {"accepted": False, "reason": "previous_pawn_still_present"}
        if self.party["wipe"] and request["character_id"] not in self.party["wipe"]["roster"]:
            return {"accepted": False, "reason": "wipe_in_progress"}
        after_grace = (
            self.guest is not None and self.guest["guest_id"] == request["guest_id"]
            and self.guest["disconnected"] is not None
            and time.monotonic() - self.guest["disconnected"] >= DEFAULT_RECONNECT_GRACE
        )
        enrollment = None
        if self.enrollment:
            enrollment = self.enrollment.reserve(request["guest_id"], request["character_id"], self.last_game)
            if enrollment.state not in ("reserved", "pending", "bound"):
                return {"accepted": False, "reason": "native_roster_unavailable"}
        try:
            active = self.store.activate(request["guest_id"], request["credential"], request["character_id"])
        except CampaignError:
            if enrollment and enrollment.state == "reserved":
                self.enrollment.cancel(enrollment.reservation.id)
            return {"accepted": False, "reason": "guest_or_character_unavailable"}
        saved = active["character"]["state"]
        if after_grace:
            saved = {key: saved[key] for key in ("health", "native_guest_save") if key in saved} if self.native_guest else {}
        self.guest = {
            "guest_id": request["guest_id"], "lease_id": active["lease_id"],
            "character_id": active["character"]["character_id"], "disconnected": None,
            "restore": saved,
        }
        if not active["resumed"]:
            self.guest_pawn = None
        with contextlib.suppress(OSError):
            self.control.event("guest_link_authenticated", guest_id=request["guest_id"],
                               character_id=self.guest["character_id"], resumed=active["resumed"])
        return {"accepted": True, "resumed": active["resumed"],
                "character_id": self.guest["character_id"], "session_id": self.control.generation,
                "zone_epoch": self.zone_epoch, "zone": self.last_game["map"]}

    def disconnected(self, guest_id: str) -> None:
        if not self.guest or self.guest["guest_id"] != guest_id or self.stopping:
            return
        if self.guest["disconnected"] is not None:
            return
        self.guest["disconnected"] = time.monotonic()
        self.store.disconnect(self.guest["lease_id"])
        if not self.transitioning:
            try:
                self.checkpoint()
            except (OSError, RuntimeError) as error:
                with contextlib.suppress(OSError):
                    self.control.event("disconnect_checkpoint_error", error_type=type(error).__name__)
        with contextlib.suppress(OSError):
            self.control.event("guest_link_lost", guest_id=guest_id, grace_seconds=DEFAULT_RECONNECT_GRACE)

    def checkpoint(self, proposed_party: dict | None = None, *, native_record: dict | None = None,
                   guest_records: dict[str, dict] | None = None, unleased_states: dict | None = None) -> int:
        if self.last_game.get("state") != "hosting":
            raise RuntimeError("Wait for the host map to finish loading")
        status = self.store.status()
        world = status["world"]
        world.update({
            "map": self.last_game["map"], "host": _pawn(self.last_game, "local"),
            "party": self.party if proposed_party is None else proposed_party,
        })
        if native_record is not None:
            if native_record["map"] != world["map"]:
                raise RuntimeError("The stock host save belongs to a different campaign map")
            world["native_host_save"] = native_record
        elif world.get("native_host_save", {}).get("map", world["map"]) != world["map"]:
            raise RuntimeError("Capture the stock host save before checkpointing the new map")
        native = self.native_world_state()
        if native:
            world["native_observation"] = native
        scene = native.get("native_scene", {})
        level = scene.get("rest_level")
        if self.native_zones and scene.get("active_map") == self.last_game["map"] and level is not None:
            rest = native_blue.saved_rest(level, scene.get("bed"))
            if rest:
                world["native_rest"] = rest
            elif level:
                world.pop("native_rest", None)
        return self.store.checkpoint(world, self.checkpoint_guest_changes(status, guest_records or {}),
                                     unleased_states=unleased_states)

    def checkpoint_guest_changes(self, status: dict, guest_records: dict[str, dict]) -> dict[str, dict]:
        guest = _pawn(self.last_game, "guest")
        if self.native_guest and self.last_game.get("native_guest_save", {}).get("pawn") != self.guest_pawn:
            guest = {}
        active = self.guest and any(
            lease["character_id"] == self.guest["character_id"] for lease in status["leases"]
        )
        if guest and active and self.guest_pawn:
            character_id = self.guest["character_id"]
            prior = next((entry["state"].get("native_guest_save") for entry in status["characters"]
                          if entry["character_id"] == character_id), None)
            frame = guest_records.get(character_id) or prior
            if frame:
                if frame.get("character_id") != character_id:
                    raise RuntimeError("The native checkpoint belongs to a different guest character")
                guest["native_guest_save"] = frame
        return {self.guest["lease_id"]: guest} if guest and active and self.guest_pawn else {}

    def saved_native_guest(self) -> dict | None:
        if not self.guest:
            return None
        return next((entry["state"].get("native_guest_save") for entry in self.store.status()["characters"]
                     if entry["character_id"] == self.guest["character_id"]), None)

    async def capture_native_guest(self, bridge: Bridge) -> dict | None:
        if (not self.persist_native_guest or not self.native_profile or not self.guest or not self.guest_pawn
                or self.last_game.get("native_guest_save", {}).get("pawn") != self.guest_pawn
                or self.last_game.get("pawns", {}).get("guest", {}).get("name") != self.guest_pawn):
            return None
        slot = native_host_save.new_slot()
        source = self.last_game["map"]
        snapshot = await bridge.command("native-guest-save-capture", self.guest_pawn, slot, timeout=7)
        proof = snapshot.get("native_guest_save_frame", {})
        if (proof.get("state") != "captured" or proof.get("slot") != slot or proof.get("map") != source
                or proof.get("pawn") != self.guest_pawn or snapshot.get("state") != "hosting"
                or snapshot.get("map") != source):
            raise RuntimeError("The game did not verify the exact detached guest save generation")
        frame = await asyncio.to_thread(native_host_save.record, self.native_profile, slot, source, flush=True)
        self.last_game = snapshot
        self.last_seen = time.monotonic()
        return dict(frame, character_id=self.guest["character_id"])

    async def capture_guest_records(self, bridge: Bridge) -> dict[str, dict]:
        frame = await self.capture_native_guest(bridge)
        return {frame["character_id"]: frame} if frame else {}

    async def flush_native_world(self, bridge: Bridge) -> None:
        source = self.last_game.get("map")
        snapshot = await bridge.command("native-world-flush", timeout=7)
        proof = snapshot.get("native_world_flush", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != source or proof.get("map") != source
                or proof.get("state") not in ("flushed", "partial", "unavailable", "skipped")):
            raise RuntimeError("The stock world flush did not return an exact current-map report")
        self.last_game, self.last_seen = snapshot, time.monotonic()
        self.control.event("native_world_checkpoint_flushed", **proof)

    async def checkpoint_native(self, bridge: Bridge, proposed_party: dict | None = None, *,
                                unleased_states: dict | None = None) -> int:
        if self.native_profile is None:
            return self.checkpoint(proposed_party, unleased_states=unleased_states)
        source = self.last_game.get("map")
        pawn = self.last_game.get("pawns", {}).get("local", {}).get("name")
        if self.last_game.get("state") != "hosting" or not pawn or source not in self.allowed_maps:
            raise RuntimeError("The canonical native host is unavailable for checkpointing")
        slot = native_host_save.new_slot()
        snapshot = await bridge.command("native-host-save-capture", slot, timeout=7)
        observed = snapshot.get("native_host_save", {})
        if (observed.get("state") != "captured" or observed.get("slot") != slot or observed.get("map") != source
                or observed.get("pawn") != pawn or snapshot.get("map") != source or snapshot.get("state") != "hosting"):
            raise RuntimeError("The game did not verify the exact canonical host save generation")
        record = await asyncio.to_thread(native_host_save.record, self.native_profile, slot, source, flush=True)
        if "eather_remainder" in observed:
            carry = observed["eather_remainder"]
            if type(carry) not in (int, float) or not 0 <= carry < 1:
                raise RuntimeError("The native host save did not verify its stock Eather carry")
            record["eather_remainder"] = carry
        self.last_game = snapshot
        self.last_seen = time.monotonic()
        guest_records = await self.capture_guest_records(bridge)
        revision = self.checkpoint(proposed_party, native_record=record, guest_records=guest_records,
                                   unleased_states=unleased_states)
        try:
            keep = {entry["slot"] for path in (self.store.path, self.store.previous_path)
                    for entry in native_host_save.references(read_json(path))}
            native_host_save.prune(self.native_profile, keep)
        except (OSError, KeyError, ValueError) as error:
            self.control.event("native_host_save_prune_deferred", reason=str(error))
        return revision

    def roles(self) -> dict[str, str]:
        roles = {"host": "host"}
        if self.guest:
            lost = self.guest["disconnected"]
            character = self.guest["character_id"]
            has_entered = self.guest_pawn or self.awaiting_zone_guest == character
            if (lost is None or time.monotonic() - lost < DEFAULT_RECONNECT_GRACE) and has_entered:
                roles["guest"] = character
        return roles

    def role_pawn(self, role: str) -> tuple[str, object | None]:
        return ("local" if role == "host" else "guest"), None

    def native_recipients(self) -> dict[str, dict]:
        if not self.native_guest or not self.guest or not self.guest_pawn:
            return {}
        scope = self.last_game.get("native_guest_save", {})
        if scope.get("state") != "bound" or scope.get("pawn") != self.guest_pawn:
            return {}
        return {self.guest["character_id"]: {"role": "guest", "alias": "guest", "pawn": self.guest_pawn,
                                             "connected": self.guest["disconnected"] is None}}

    def native_world_state(self) -> dict:
        return {key: self.last_game[key] for key in ("native_scene", "native_rules", "native_reputation", "native_seed")
                if key in self.last_game}

    def public_gameplay(self) -> dict:
        view = party.public_state(self.party, self.roles(), test_fixtures=self.fixtures)
        view["host_world"] = self.native_world_state()
        view["native_backpacks"] = native_inventory_death.visible_bags(self.party)
        world_view = self.last_game.get("native_world_view")
        area = self.last_game.get("map")
        if (world_view and world_view.get("mode") == "sample" and world_view.get("map") == area
                and world_view.get("actor") == WORLD_VIEW_ACTORS.get(area)):
            view["host_world"]["native_world_view"] = world_view
        view["native_quests"] = self.party.get("native_quests", {})
        if rolls := self.party.get(native_loot.STATE_KEY):
            view[native_loot.STATE_KEY] = {key: native_loot.public_roll(roll) for key, roll in rolls.items()}
        if pending := pending_cube(self.party):
            cube, itinerary = pending
            view["pending_item_travel"] = {"item": cube, "source": itinerary["source"],
                                           "destination": itinerary["destination"]}
        if matrix := native_matrix.pending(self.party):
            view["pending_item_travel"] = {"item": "matrix", **{key: matrix[key] for key in ("source", "destination", "phase")}}
        if dream := native_dream.visit(self.party):
            view["stardust_visit"] = {key: dream[key] for key in ("phase", "destination", "return_map", "deadline")}
            view["stardust_visit"]["return_blockers"] = self.dream_blockers
        if self.native_guest:
            view["native_inventory_projected"] = self.native_pickup.projected()
        return view

    def act(self, actor: str, operation_id: str, action: str, target: str | None,
            amount: int | None = None, retained: dict | None = None, *,
            last_native_target: dict | None = None) -> dict:
        if self.stopping or self.transitioning or time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            raise party.RuleError("game_snapshot_unavailable")
        roles = self.roles()
        pawns = self.authoritative_pawns(roles)
        if action == "native-down" and target is not None and last_native_target:
            pawns.setdefault(target, last_native_target)
        if action == "test-damage":
            if actor != "host" or not self.fixtures:
                raise party.RuleError("action_unavailable")
            if target is not None and isinstance(target, str):
                target = roles.get(target, target)
        candidate, result = party.transition(
            self.party, actor, operation_id, action, target, pawns,
            loot_available=self.fixtures and self.marker_ready, test_fixtures=self.fixtures,
            test_damage=amount, eligible=tuple(roles.values()), retained=retained, now=time.time(),
        )
        if candidate is not self.party:
            revision = self.checkpoint(candidate)
            self.party = candidate
            with contextlib.suppress(OSError):
                self.control.event("gameplay_action", character_id=actor, operation_id=operation_id,
                                   revision=revision, **result)
        return result

    def authoritative_pawns(self, roles: dict[str, str]) -> dict:
        pawns = {}
        if time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            return pawns
        for role, character_id in roles.items():
            alias, _ = self.role_pawn(role)
            details = _pawn(self.last_game, alias)
            if details and all(axis is not None for axis in details["location"]):
                pawns[character_id] = details
        return pawns

    def item_travel_pending(self) -> bool:
        return bool(pending_cube(self.party) or native_dream.visit(self.party) or native_matrix.pending(self.party))

    def travelling_guests(self) -> dict[str, dict]:
        guest = _pawn(self.last_game, "guest")
        return {"guest": guest} if guest else {}

    def begin_zone_guests(self, transport) -> None:
        if self.enrollment:
            self.enrollment.begin_travel()

    async def reopen_zone_guests(self, transport, roles: dict[str, str], prior: dict[str, dict]) -> None:
        if transport is not None:
            await transport.refresh_game_routes()
        if self.enrollment and "guest" in roles and self.guest:
            reservation = self.enrollment.reserve(self.guest["guest_id"], roles["guest"], self.last_game)
            if reservation.state != "reserved":
                raise RuntimeError(f"The destination could not reserve the native guest ({reservation.reason})")
        self.awaiting_zone_guest = roles.get("guest")
        self.guest_pawn = None
        if self.guest:
            self.guest["restore"] = {"health": prior.get("guest", {}).get("health")} if self.native_guest else {}
            if frame := self.saved_native_guest():
                self.guest["restore"]["native_guest_save"] = frame

    def travel_refusal(self, destination: str, roles: dict[str, str], *, respawning: bool = False,
                       staying: bool = False) -> dict | None:
        if self.party["wipe"] and not respawning:
            return {"ok": False, "error": "wipe_in_progress"}
        if not respawning and any(actor in self.party["pending_spawns"] for actor in roles.values()):
            return {"ok": False, "error": "respawn_in_progress"}
        current = self.last_game.get("map", "")
        gate = travel_decision("host", current, destination, ("host",) if respawning else tuple(roles.values()),
                               self.authoritative_pawns(roles), allowed_maps=self.allowed_maps, allow_same_zone=staying)
        if not gate["allowed"]:
            return {"ok": False, "error": "travel_blocked", "blockers": gate["blockers"]}
        return None

    async def travel(self, bridge: Bridge, transport, destination: str, *, respawning: bool = False,
                     entrance: str | None = None) -> dict:
        if not respawning and self.item_travel_pending():
            return {"ok": False, "error": "item_travel_pending"}
        roles = self.roles()
        if refusal := self.travel_refusal(destination, roles, respawning=respawning):
            return refusal
        current = self.last_game["map"]
        if not respawning and (current not in PLAYTEST_MAPS or entrance is not None):
            expected = AUTHORED_EXITS.get((current, destination))
            if not expected or entrance != expected:
                return {"ok": False, "error": "authored_exit_required"}
        self.transitioning = True
        try:
            return await self.switch_zone(bridge, transport, destination, entrance, roles)
        finally:
            self.transitioning = False

    async def switch_zone(self, bridge: Bridge, transport, destination: str, entrance: str | None,
                          roles: dict[str, str], *, gold_guid: str | None = None, blue_plan: dict | None = None,
                          dream_plan: dict | None = None, random_plan: tuple[str, dict] | None = None,
                          matrix_plan: dict | None = None, authored_plan: AuthoredRoute | None = None,
                          respawn_plan: dict | None = None, source_flushed: bool = False) -> dict:
        current = self.last_game["map"]
        guests = self.travelling_guests()
        self.control.event("zone_travel_requested", source=current, destination=destination)
        if self.world_flush_supported and not source_flushed:
            await self.flush_native_world(bridge)
            await self.checkpoint_native(bridge)
        elif (self.native_profile is not None or self.native_guest and any(
            guest.get("health") is not None for guest in guests.values()
        )) and not (
            gold_guid or blue_plan or dream_plan or random_plan or matrix_plan
        ):
            await self.checkpoint_native(bridge)
        try:
            authorization = ("blue", blue_plan["guid"]) if blue_plan else (gold_guid,) if gold_guid else ()
            if random_plan:
                cube, itinerary = random_plan
                authorization = (cube, itinerary["guid"])
                write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
            returning = dream_plan and dream_plan["phase"] == "returning"
            if dream_plan:
                authorization = ("stardust-return",) if returning else (dream_plan["kind"], dream_plan["guid"])
                write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
            if matrix_plan:
                authorization = ("matrix", matrix_plan["guid"])
                write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
            if authored_plan:
                authorization = ("authored", str(authored_plan.travel_time_step or 0))
            self.begin_zone_guests(transport)
            await bridge.command("travel", destination, *authorization)
            observed = await bridge.wait_for(
                lambda game: game.get("state") == "hosting" and game.get("map") == destination,
                timeout=22,
            )
            await asyncio.to_thread(verify_loopback_socket, bridge.process.pid, self.internal_port)
            if authored_plan:
                observed = await self.authored_arrival(bridge, authored_plan)
            elif matrix_plan:
                guid = matrix_plan["guid"]
                observed = await matrix_service.place(bridge, observed, guid, native_matrix.anchor(self.party, guid))
            elif dream_plan and not returning:
                await bridge.wait_for(partial(self.dream_isolated, destination=destination), timeout=7)
                observed = await bridge.command("native-dream-arrival", destination, timeout=6)
                proof = observed.get("native_dream_arrival", {})
                if (not self.dream_isolated(observed, destination=destination)
                        or proof.get("actor") != native_dream.ARRIVAL or proof.get("destination") != destination
                        or None in proof.get("location", [None])):
                    raise RuntimeError("The stock dream arrival was not isolated and verified")
            elif returning:
                observed = await self.blue_arrival(bridge, {"source": current, "destination": destination,
                                                            "bed": dream_plan["return_bed"]})
            elif blue_plan:
                observed = await self.blue_arrival(bridge, blue_plan)
            elif respawn_plan:
                observed = await self.stock_respawn_marker(bridge, respawn_plan)
            elif random_plan and cube in ("green", "purple"):
                observed = await self.cube_bed_arrival(bridge, cube, destination)
            elif entrance:
                observed = await bridge.command("native-exit-arrival", entrance, timeout=6)
                if observed.get("map") != destination or observed.get("state") != "hosting":
                    raise RuntimeError("The authored arrival was acknowledged in the wrong world")
            self.last_game = observed
            self.last_seen = time.monotonic()
            await self.reopen_zone_guests(transport, roles, guests)
            if dream_plan:
                await self.ensure_dream_stack(bridge, dream_plan)
            self.zone_epoch = uuid.uuid4().hex
            self.marker_ready = False
            candidate = native_gold.arrive(self.party, gold_guid, observed["map"]) if gold_guid else None
            if blue_plan:
                candidate = native_blue.arrive(self.party, blue_plan["guid"], observed["map"])
            if random_plan:
                candidate = DRAW_CUBES[cube].arrive(self.party, itinerary["guid"], observed["map"])
            if dream_plan:
                candidate = native_dream.finished(self.party) if returning else native_dream.arrived(self.party, time.time())
            if matrix_plan:
                candidate = native_matrix.arrive(self.party, matrix_plan["id"], destination)
            revision = await self.checkpoint_native(bridge, candidate)
            if candidate is not None:
                self.party = candidate
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        self.control.event("zone_travel_ready", source=current, destination=destination,
                           entrance=entrance, epoch=self.zone_epoch, revision=revision)
        return {"ok": True, "map": destination, "entrance": entrance,
                "travel_epoch": self.zone_epoch, "revision": revision}

    async def authored_arrival(self, bridge: Bridge, route: AuthoredRoute) -> dict:
        tag = getattr(route, "arrival_entrance", None) or route.entrance
        snapshot = await bridge.command("native-authored-arrival", tag, route.kind, route.arrival_actor, timeout=7)
        proof = snapshot.get("native_authored_arrival", {})
        actor_class = "Dungeon_TransitionDoor_BP_C" if route.kind == "door" else "LevelExit_Spawn_BP_C"
        basename = route.destination.rsplit("/", 1)[-1]
        expected = f"{actor_class} {route.destination}.{basename}:PersistentLevel.{route.arrival_actor}"
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != route.destination
                or proof.get("destination") != route.destination or proof.get("actor") != expected
                or proof.get("placement") != "arrow" or None in proof.get("location", [None])):
            raise RuntimeError("The stock authored arrival could not be verified")
        return snapshot

    async def travel_authored(self, bridge: Bridge, transport, route: AuthoredRoute) -> dict:
        if (not self.expanded_zones or self.stopping or self.transitioning or self.item_travel_pending()
                or self.last_game.get("state") != "hosting" or self.last_game.get("map") != route.source):
            return {"ok": False, "error": "authored_route_unavailable"}
        roles = self.roles()
        if refusal := self.travel_refusal(route.destination, roles):
            return refusal
        self.transitioning = True
        try:
            return await self.switch_zone(bridge, transport, route.destination, route.entrance,
                                          roles, authored_plan=route)
        finally:
            self.transitioning = False

    async def checked_cube(self, bridge: Bridge, cube: str, operation: str, guid: str) -> tuple[dict, dict]:
        game = self.last_game
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        allowed = (native_gold.SOURCES | {GOLD_NEXUS}) & self.allowed_maps if cube == "gold" else self.allowed_maps - PLAYTEST_MAPS
        if (not native_gold.valid_guid(guid) or game.get("state") != "hosting" or not pawn
                or area not in allowed or time.monotonic() - self.last_seen > POSITION_MAX_AGE):
            raise ValueError("Cube operations require a current host pawn and an exact item GUID")
        write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
        snapshot = await bridge.command(f"native-{cube}-{operation}", pawn, guid, timeout=4)
        row = snapshot.get(f"native_{cube}", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or snapshot.get("pawns", {}).get("local", {}).get("name") != pawn
                or row.get("operation") != operation or row.get("map") != area or row.get("pawn") != pawn
                or row.get("guid") != guid or row.get("state") not in CUBE_RESULTS[operation]):
            raise RuntimeError("The game did not acknowledge the exact cube operation")
        return snapshot, row

    async def activate_gold(self, bridge: Bridge, transport, request: native_gold.GoldRequest) -> dict:
        if (not self.native_zones or GOLD_NEXUS not in self.allowed_maps or self.stopping or self.transitioning
                or request.source not in native_gold.SOURCES or request.source not in self.allowed_maps
                or self.last_game.get("map") != request.source
                or self.last_game.get("state") != "hosting" or request.entry not in native_gold.INVENTORY_ENTRIES
                or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "gold_scope_unavailable"}
        history = native_gold.ledger(self.party)
        if request.guid in history["spent"]:
            return {"ok": False, "error": "gold_already_spent"}
        if self.item_travel_pending():
            return {"ok": False, "error": "gold_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        if refusal := self.travel_refusal(GOLD_NEXUS, self.roles()):
            return refusal
        self.transitioning = True
        try:
            snapshot, item = await self.checked_cube(bridge, "gold", "inspect", request.guid)
            if item["state"] != "present":
                return {"ok": False, "error": "gold_item_unavailable"}
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            if refusal := self.travel_refusal(GOLD_NEXUS, self.roles()):
                return refusal
            candidate = native_gold.begin(self.party, request.guid, request.source)
            await self.checkpoint_native(bridge, candidate)
            self.party = candidate
            return await self.resume_gold(bridge, transport, recovery=False)
        finally:
            self.transitioning = False

    async def resume_gold(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        pending = native_gold.ledger(self.party)["pending"]
        try:
            if (not pending or self.last_game.get("map") != pending["source"]
                    or not self.native_zones or pending["destination"] not in self.allowed_maps):
                raise RuntimeError("Cannot recover Golden Cube travel from the current campaign map")
            snapshot, item = await self.checked_cube(bridge, "gold", "debit", pending["guid"])
            if item["state"] != "removed" and not recovery:
                raise RuntimeError("The committed Golden Cube could not be removed from the active inventory")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            result = await self.switch_zone(bridge, transport, pending["destination"], pending["entrance"],
                                            self.roles(), gold_guid=pending["guid"])
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event("gold_travel_recovered" if recovery else "gold_travel_completed", guid=pending["guid"],
                               debit=item["state"], map=result["map"], revision=result["revision"])
        return result

    async def blue_route(self, bridge: Bridge, operation: str, *arguments: str) -> tuple[dict, dict]:
        game = self.last_game
        source = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        if not pawn or game.get("state") != "hosting" or source not in self.allowed_maps:
            raise RuntimeError("The authoritative Blue Cube routing pawn is unavailable")
        snapshot = await bridge.command(operation, pawn, *arguments, timeout=4)
        route = snapshot.get("native_blue_route", {})
        if (route.get("source") != source or route.get("pawn") != pawn or snapshot.get("map") != source
                or snapshot.get("state") != "hosting"
                or not native_blue.valid_rest(source, route.get("destination"), route.get("bed"))):
            raise RuntimeError("The host's exact stock rest destination could not be verified")
        return snapshot, route

    async def blue_arrival(self, bridge: Bridge, pending: dict, *, staying: bool = False) -> dict:
        destination, bed = pending["destination"], pending["bed"]
        authorization = (pending["guid"],) if staying else ()
        snapshot = await bridge.command("native-blue-arrival", destination, bed or "-", *authorization, timeout=6)
        proof = snapshot.get("native_blue_arrival", {})
        if (proof.get("destination") != destination or snapshot.get("map") != destination
                or snapshot.get("state") != "hosting" or not proof.get("actor")
                or not native_blue.valid_rest(pending["source"], destination, proof.get("bed"))
                or (bed and proof["bed"] != bed)
                or proof.get("placement") not in ("arrow", "alternate")
                or len(proof.get("location", [])) != 3 or any(axis is None for axis in proof["location"])):
            raise RuntimeError("The verified Blue Cube rest placement did not match the committed destination")
        return snapshot

    async def activate_blue(self, bridge: Bridge, transport, request: native_gold.GoldRequest) -> dict:
        if (not self.native_zones or self.stopping or self.transitioning or request.source not in self.allowed_maps
                or request.source in PLAYTEST_MAPS
                or self.last_game.get("map") != request.source or self.last_game.get("state") != "hosting"
                or request.entry not in native_gold.INVENTORY_ENTRIES or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "blue_scope_unavailable"}
        if request.guid in native_blue.ledger(self.party)["spent"]:
            return {"ok": False, "error": "blue_already_spent"}
        if self.item_travel_pending():
            return {"ok": False, "error": "item_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        self.transitioning = True
        try:
            _, item = await self.checked_cube(bridge, "blue", "inspect", request.guid)
            if item["state"] != "present":
                return {"ok": False, "error": "blue_item_unavailable"}
            snapshot, route = await self.blue_route(bridge, "native-blue-route")
            if snapshot.get("pawns", {}).get("local", {}).get("name") != request.pawn:
                return {"ok": False, "error": "blue_scope_unavailable"}
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            destination = route["destination"]
            if refusal := self.travel_refusal(destination, self.roles(), staying=destination == request.source):
                return refusal
            candidate = native_blue.begin(self.party, request.guid, request.source, destination, route["bed"])
            await self.checkpoint_native(bridge, candidate)
            self.party = candidate
            return await self.resume_blue(bridge, transport, recovery=False)
        finally:
            self.transitioning = False

    async def resume_blue(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        pending = native_blue.ledger(self.party)["pending"]
        try:
            if (not pending or self.last_game.get("map") != pending["source"] or not self.native_zones
                    or pending["destination"] not in self.allowed_maps):
                raise RuntimeError("Cannot recover Blue Cube travel from the current campaign map")
            snapshot, item = await self.checked_cube(bridge, "blue", "debit", pending["guid"])
            if item["state"] != "removed" and not recovery:
                raise RuntimeError("The committed Blue Cube could not be removed from the active inventory")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            roles = self.roles()
            if pending["destination"] != pending["source"]:
                result = await self.switch_zone(bridge, transport, pending["destination"], "Bed", roles,
                                                blue_plan=pending)
            else:
                result = await self.place_blue_party(bridge, pending, roles)
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event("blue_travel_recovered" if recovery else "blue_travel_completed", guid=pending["guid"],
                               debit=item["state"], arrival=self.last_game.get("native_blue_arrival"), **result)
        return result

    async def place_blue_party(self, bridge: Bridge, pending: dict, roles: dict[str, str]) -> dict:
        arrival = await self.blue_arrival(bridge, pending, staying=True)
        await self.place_party_in_zone(bridge, roles, arrival)
        candidate = native_blue.arrive(self.party, pending["guid"], self.last_game["map"])
        revision = await self.checkpoint_native(bridge, candidate)
        self.party = candidate
        return {"ok": True, "map": self.last_game["map"], "entrance": "Bed", "travel_epoch": self.zone_epoch,
                "revision": revision, "in_zone": True}

    async def place_party_in_zone(self, bridge: Bridge, roles: dict[str, str], arrival: dict) -> None:
        self.last_game = arrival
        self.last_seen = time.monotonic()
        if "guest" in roles:
            guest = self.last_game.get("pawns", {}).get("guest", {})
            if not guest or guest.get("name") != self.guest_pawn:
                raise RuntimeError("The active guest could not be verified for the same-map arrival")
            restored = await self.place_new_guest(bridge, guest)
            if restored:
                self.last_game = restored
                self.last_seen = time.monotonic()
            pawns = self.authoritative_pawns(roles)
            host, peer = pawns.get("host"), pawns.get(roles["guest"])
            if not host or not peer or math.dist(host["location"], peer["location"]) > party.INTERACT_RANGE:
                raise RuntimeError("The guest could not be placed by the host's verified arrival")

    async def activate_red(self, bridge: Bridge, transport, request: native_gold.GoldRequest) -> dict:
        return await self.activate_random_cube(bridge, transport, request, "red")

    async def activate_green(self, bridge: Bridge, transport, request: native_gold.GoldRequest) -> dict:
        return await self.activate_random_cube(bridge, transport, request, "green")

    async def activate_random_cube(self, bridge: Bridge, transport, request: native_gold.GoldRequest, cube: str) -> dict:
        module = DRAW_CUBES[cube]
        if (not self.native_zones or self.stopping or self.transitioning or request.source not in RANDOM_CUBE_SOURCES
                or request.source not in self.allowed_maps
                or self.last_game.get("map") != request.source or self.last_game.get("state") != "hosting"
                or request.entry not in native_gold.INVENTORY_ENTRIES or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": f"{cube}_scope_unavailable"}
        if request.guid in module.ledger(self.party)["spent"]:
            return {"ok": False, "error": f"{cube}_already_spent"}
        if self.item_travel_pending():
            return {"ok": False, "error": "item_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        self.transitioning = True
        try:
            snapshot, item = await self.checked_cube(bridge, cube, "inspect", request.guid)
            if item["state"] != "present":
                return {"ok": False, "error": f"{cube}_item_unavailable"}
            eligible = ()
            if cube == "green":
                snapshot = await bridge.command("native-green-options", request.pawn, timeout=4)
                proof = snapshot.get("native_green_options", {})
                if (snapshot.get("state") != "hosting" or snapshot.get("map") != request.source
                        or proof.get("source") != request.source or proof.get("pawn") != request.pawn):
                    raise RuntimeError("The active host's Green Cube candidates could not be verified")
                eligible = native_green.candidates(proof.get("reputation", {}))
                if tuple(proof.get("destinations", ())) != eligible:
                    raise RuntimeError("The native Green Cube draw set did not match stock reputation rules")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            # Check the full roster before making the one stock-weighted draw.
            if refusal := self.travel_refusal(request.source, self.roles(), staying=True):
                return refusal
            destination = native_green.draw(eligible) if cube == "green" else module.draw()
            candidate = module.begin(self.party, request.guid, request.source, destination)
            await self.checkpoint_native(bridge, candidate)
            self.party = candidate
            return await self.resume_random_cube(bridge, transport, cube, recovery=False)
        finally:
            self.transitioning = False

    async def resume_red(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        return await self.resume_random_cube(bridge, transport, "red", recovery=recovery)

    async def resume_green(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        return await self.resume_random_cube(bridge, transport, "green", recovery=recovery)

    async def resume_random_cube(self, bridge: Bridge, transport, cube: str, *, recovery: bool) -> dict:
        module = DRAW_CUBES[cube]
        pending = module.ledger(self.party)["pending"]
        try:
            if (not pending or not self.native_zones or self.last_game.get("map") != pending["source"]
                    or pending["destination"] not in self.allowed_maps):
                raise RuntimeError(f"The committed {cube} cube draw is out of scope")
            guid, destination = pending["guid"], pending["destination"]
            snapshot, item = await self.checked_cube(bridge, cube, "debit", guid)
            if item["state"] != "removed" and not recovery:
                raise RuntimeError(f"The committed {cube} cube could not be removed from the active inventory")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            roles = self.roles()
            if destination != pending["source"]:
                result = await self.switch_zone(bridge, transport, destination, module.ARRIVAL, roles,
                                                random_plan=(cube, pending))
            else:
                arrival = (await self.cube_bed_arrival(bridge, cube, destination, guid) if cube in ("green", "purple") else
                           await bridge.command("native-red-arrival", destination, guid, timeout=6))
                if arrival.get("state") != "hosting" or arrival.get("map") != destination:
                    raise RuntimeError("The same-map cube arrival belongs to the wrong world")
                await self.place_party_in_zone(bridge, roles, arrival)
                candidate = module.arrive(self.party, guid, destination)
                revision = await self.checkpoint_native(bridge, candidate)
                self.party = candidate
                result = {"ok": True, "map": destination, "entrance": module.ARRIVAL, "revision": revision,
                          "travel_epoch": self.zone_epoch, "in_zone": True}
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event(f"{cube}_travel_recovered" if recovery else f"{cube}_travel_completed", guid=guid,
                               debit=item["state"], **result)
        return result

    async def cube_bed_arrival(self, bridge: Bridge, cube: str, destination: str, guid: str | None = None) -> dict:
        snapshot = await bridge.command(f"native-{cube}-arrival", destination, *((guid,) if guid else ()), timeout=6)
        arrival = snapshot.get(f"native_{cube}_arrival", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != destination
                or arrival.get("destination") != destination or not arrival.get("actor")
                or arrival.get("placement") not in ("arrow", "alternate", "start")
                or len(arrival.get("location", [])) != 3 or None in arrival["location"]):
            raise RuntimeError(f"The {cube.title()} Cube stock arrival could not be verified")
        return snapshot

    @staticmethod
    def dream_isolated(game: dict, *, destination: str | None = None) -> bool:
        guard = game.get("native_dream_isolation", {})
        area = game.get("map")
        profile = native_dream.PROFILES.get(area)
        return (game.get("state") == "hosting" and profile is not None
                and destination in (None, area) and guard.get("map") == area
                and guard.get("state") == "ready" and guard.get("actor") == profile.actor)

    async def stardust_operation(self, bridge: Bridge, operation: str, guid: str, *arguments: str,
                                 kind: str = "stardust") -> tuple[dict, dict]:
        game = self.last_game
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        if (not native_gold.valid_guid(guid) or not pawn or game.get("state") != "hosting"
                or kind not in native_dream.BASE_SECONDS or area not in self.allowed_maps or area in PLAYTEST_MAPS
                or time.monotonic() - self.last_seen > POSITION_MAX_AGE):
            raise ValueError("Stardust requires the active host pawn and an exact stack GUID")
        write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
        snapshot = await bridge.command(f"native-{kind}-{operation}", pawn, guid, *arguments, timeout=4)
        row = snapshot.get("native_" + kind.replace("-", "_"), {})
        quantity = row.get("quantity")
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != area
                or snapshot.get("pawns", {}).get("local", {}).get("name") != pawn
                or row.get("operation") != operation or row.get("map") != area or row.get("pawn") != pawn
                or row.get("guid") != guid or row.get("state") not in STARDUST_RESULTS[operation]
                or type(quantity) is not int or not 0 <= quantity <= native_dream.MAX_STACK):
            raise RuntimeError("The game did not acknowledge the exact Stardust operation")
        self.last_game = snapshot
        self.last_seen = time.monotonic()
        return snapshot, row

    async def ensure_dream_stack(self, bridge: Bridge, trip: dict) -> None:
        guid, remaining, kind = trip["guid"], trip["before"] - 1, trip["kind"]
        _, item = await self.stardust_operation(bridge, "inspect", guid, kind=kind)
        if item["quantity"] == remaining:
            if (remaining == 0 and item["state"] == "absent") or (
                remaining and item["state"] == "present" and item["payload"] == trip["payload"]
            ):
                return
        if item["state"] != "absent" or not remaining or trip["payload"] != native_dream.STACK_PAYLOADS[kind]:
            raise RuntimeError("The native Stardust remainder conflicts with the committed quantity")
        _, restored = await self.stardust_operation(bridge, "restore", guid, str(remaining), trip["payload"], kind=kind)
        if restored["quantity"] != remaining:
            raise RuntimeError("The native Stardust remainder could not be restored exactly")

    async def activate_stardust(self, bridge: Bridge, transport, request: native_gold.GoldRequest, *,
                                kind: str = "stardust") -> dict:
        source = request.source
        if (not self.native_zones or self.native_profile is None or self.stopping or self.transitioning
                or source not in self.allowed_maps
                or self.last_game.get("map") != source
                or self.last_game.get("state") != "hosting" or request.entry not in native_gold.INVENTORY_ENTRIES
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "stardust_scope_unavailable"}
        active = native_dream.visit(self.party)
        continuing = bool(active and active["phase"] == "visiting" and active["destination"] == source
                          and time.time() < active["deadline"]
                          and self.dream_isolated(self.last_game, destination=source)
                          and not self.last_game["native_dream_isolation"]["early_requested"])
        if (source in native_dream.PROFILES and not continuing) or (self.item_travel_pending() and not continuing):
            return {"ok": False, "error": "item_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        self.transitioning = True
        try:
            _, item = await self.stardust_operation(bridge, "inspect", request.guid, kind=kind)
            if item["state"] != "present" or not item["quantity"]:
                return {"ok": False, "error": "stardust_stack_unavailable"}
            if item["quantity"] > 1 and item["payload"] != native_dream.STACK_PAYLOADS[kind]:
                return {"ok": False, "error": "unqualified_stardust_stack"}
            snapshot = await bridge.command("native-dream-route", request.pawn, kind, timeout=5)
            route = snapshot.get("native_dream_route", {})
            destination = native_dream.destination_for(source, route.get("tarot"))
            profile = native_dream.PROFILES.get(destination)
            if (snapshot.get("state") != "hosting" or snapshot.get("map") != source
                    or route.get("source") != source or route.get("destination") != destination
                    or not profile or destination not in self.allowed_maps or route.get("kind") != kind
                    or route.get("pawn") != request.pawn or route.get("arrival") != native_dream.ARRIVAL
                    or route.get("visit_seconds") != profile.duration(kind)
                    or route.get("startup_seconds") != profile.startup or route.get("fade_seconds") != profile.fade
                    or not native_blue.valid_rest(source, route.get("return_map"), route.get("return_bed"))):
                return {"ok": False, "error": "stardust_route_unavailable"}
            if continuing and (
                time.time() >= active["deadline"]
                or not self.dream_isolated(snapshot, destination=source)
                or snapshot["native_dream_isolation"]["early_requested"]
                or (active["return_map"], active["return_bed"]) != (route["return_map"], route["return_bed"])
            ):
                return {"ok": False, "error": "stardust_continuation_unavailable"}
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            if refusal := self.travel_refusal(destination, self.roles()):
                return refusal
            proposed = native_dream.begin(self.party, request.guid, item["quantity"], item["payload"],
                                          route["return_map"], route["return_bed"], source=source, kind=kind,
                                          tarot=route["tarot"])
            await self.checkpoint_native(bridge, proposed)
            self.party = proposed
            return await self.depart_dream(bridge, transport, recovery=False)
        finally:
            self.transitioning = False

    async def depart_dream(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        trip = native_dream.visit(self.party)
        try:
            if not trip or trip["phase"] != "departing" or self.last_game.get("map") != trip["source"]:
                raise RuntimeError("The committed Stardust departure is out of scope")
            self.native_checkpoint_unsafe = True
            guid, kind, destination = trip["guid"], trip["kind"], trip["destination"]
            if recovery and trip["before"] > 1:
                _, item = await self.stardust_operation(bridge, "inspect", guid, kind=kind)
                if item["state"] == "absent":
                    await self.ensure_dream_stack(bridge, trip)
            _, debit = await self.stardust_operation(bridge, "debit", guid, str(trip["before"]), kind=kind)
            if debit["quantity"] != trip["before"] - 1 or (not recovery and debit["state"] == "already_debited"):
                raise RuntimeError("The committed Stardust use did not remove exactly one unit")
            source_flushed = False
            if trip["effects"] == "pending":
                source_flushed = await self.commit_dream_effects(bridge, trip)
                trip = native_dream.visit(self.party)
            result = await self.switch_zone(bridge, transport, destination, native_dream.ARRIVAL, self.roles(),
                                            dream_plan=trip, source_flushed=source_flushed)
            self.native_checkpoint_unsafe = False
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event("stardust_visit_recovered" if recovery else "stardust_visit_started", id=trip["id"],
                               guid=trip["guid"], remaining=debit["quantity"], **result)
        return result

    async def commit_dream_effects(self, bridge: Bridge, trip: dict) -> bool:
        if self.native_profile is None:
            raise RuntimeError("The Stardust effects require an isolated canonical host save")
        pawn = self.last_game.get("pawns", {}).get("local", {}).get("name")
        write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
        snapshot = await bridge.command("native-dream-effects", pawn, trip["kind"], trip["destination"],
                                        trip["tarot"] or "-", timeout=5)
        proof = snapshot.get("native_dream_effects", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != trip["source"] or proof.get("pawn") != pawn
                or any(proof.get(key) != trip[key] for key in ("source", "kind", "destination", "tarot"))
                or not -(2**31) <= proof.get("before", 0) < proof.get("after", 0) < 2**31):
            raise RuntimeError("The host's stock Stardust effects were not verified")
        self.last_game = snapshot
        self.last_seen = time.monotonic()
        flushed = proof.get("stock_event") == "Player_Teleport" and self.world_flush_supported
        if flushed:
            await self.flush_native_world(bridge)
        candidate = native_dream.effects_applied(self.party)
        revision = await self.checkpoint_native(bridge, candidate)
        self.party = candidate
        self.control.event("native_dream_effects_committed", id=trip["id"], guid=trip["guid"], revision=revision, **proof)
        return flushed

    async def advance_dream(self, bridge: Bridge, transport, *, wiped: bool = False) -> None:
        trip = native_dream.visit(self.party)
        if not trip or trip["phase"] == "departing" or not self.dream_isolated(self.last_game, destination=trip["destination"]):
            return
        now = time.time()
        stone = self.last_game.get("native_black_stone", {})
        stone_status = stone.get("status", "")
        if trip["destination"] == BLACK_OCEAN and (stone_status == "pickup_receipt_expired"
                or stone_status.startswith("stock_pickup_") and stone_status != "stock_pickup_pending"):
            self.native_checkpoint_unsafe = self.stopping = True
            raise RuntimeError(f"The stock Black Stone outcome could not be verified ({stone_status})")
        previously_collected = self.party.get("native_black_stone_pickup", {})
        picked_stone = bool(trip["destination"] == BLACK_OCEAN and stone.get("map") == BLACK_OCEAN
                            and stone.get("entry") in ("stock_f", "fixture") and native_gold.valid_guid(stone.get("guid"))
                            and stone.get("pawn") == self.last_game.get("pawns", {}).get("local", {}).get("name")
                            and previously_collected.get("visit_id") != trip["id"])
        if picked_stone or self.last_game["native_dream_isolation"]["early_requested"]:
            candidate = native_dream.accelerate(self.party, now)
            if picked_stone:
                if candidate is self.party:
                    candidate = party.load_party(self.party)
                receipt = {"visit_id": trip["id"], "guid": stone["guid"], "entry": stone["entry"]}
                candidate["native_black_stone_pickup"] = receipt
                self.native_checkpoint_unsafe = True
                revision = await self.checkpoint_native(bridge, candidate)
                self.party = candidate
                self.native_checkpoint_unsafe = False
                self.control.event("native_black_stone_pickup_committed", revision=revision, **receipt)
                self.control.event("stardust_early_return_requested", id=trip["id"], deadline=now)
            elif candidate is not self.party:
                self.checkpoint(candidate)
                self.party = candidate
                self.control.event("stardust_early_return_requested", id=trip["id"], deadline=now)
        if not wiped and not native_dream.return_due(self.party, now):
            return
        roles = self.roles()
        refusal = None if wiped or trip["phase"] == "returning" else self.travel_refusal(trip["return_map"], roles)
        if refusal:
            if refusal != self.dream_blockers:
                self.control.event("stardust_return_blocked", id=trip["id"], result=refusal)
                self.dream_blockers = refusal
            return
        self.dream_blockers = None
        self.transitioning = True
        try:
            if trip["phase"] != "returning":
                candidate = native_dream.returning(self.party)
                await self.checkpoint_native(bridge, candidate)
                self.party = candidate
                trip = native_dream.visit(self.party)
            result = await self.switch_zone(bridge, transport, trip["return_map"], "Bed", roles, dream_plan=trip)
            self.control.event("stardust_return_completed", id=trip["id"], guid=trip["guid"],
                               arrival=self.last_game.get("native_blue_arrival"), **result)
        finally:
            self.transitioning = False

    def authenticated_guest(self, guest_id: str) -> tuple[str, str] | None:
        if self.guest and self.guest["guest_id"] == guest_id and self.guest["disconnected"] is None:
            return "guest", self.guest["character_id"]
        return None

    def snapshot_for_guest(self, guest_id: str, role: str) -> dict:
        gameplay = self.public_gameplay()
        gameplay["native_backpacks"] = native_inventory_death.visible_bags(self.party, self.roles().get(role, ""))
        return {"ok": True, "gameplay": gameplay, "zone_epoch": self.zone_epoch, "viewer_role": role,
                "zone": self.last_game["map"], "travel_in_progress": self.transitioning}

    def guest_request(self, guest_id: str, request: dict):
        """Derive character identity from the authenticated link, never the payload."""
        if request == {"action": "characters"}:
            return {"ok": True, "characters": self.store.linked_character_catalog(guest_id)}
        if request.get("action") == "create-character" and set(request) == {"action", "name", "operation_id"}:
            try:
                created = self.store.create_linked_character(guest_id, request["name"], request["operation_id"])
            except CampaignError as error:
                return {"ok": False, "error": str(error)}
            return {"ok": True, "character": created}
        identity = self.authenticated_guest(guest_id)
        if not identity:
            return {"ok": False, "error": "guest_not_active"}
        role, character = identity
        if request == {"action": "snapshot"}:
            return self.snapshot_for_guest(guest_id, role)
        action = request.get("action")
        if action in GUEST_NATIVE_ACTIONS and set(request) == {"action", "operation_id", "target"}:
            return self.enqueue_native_request(guest_id, request)
        if action not in ("interact", "buy", "keep") or set(request) - {"action", "operation_id", "target", "retained"}:
            return {"ok": False, "error": "action_unavailable"}
        try:
            result = self.act(character, request.get("operation_id"),
                              action, request.get("target"), retained=request.get("retained"))
        except party.RuleError as error:
            if action == "interact" and request.get("target") is None and str(error) == "no_interaction_in_range":
                bag = native_inventory_death.nearby_bag(self, character)
                if bag:
                    return self.enqueue_native_request(guest_id, {"action": "native-wipe-recover", "target": bag,
                                                                  "operation_id": request.get("operation_id")})
            return {"ok": False, "error": str(error)}
        return {"ok": True, **result}

    async def enqueue_native_request(self, guest_id: str, request: dict) -> dict:
        future = asyncio.get_running_loop().create_future()
        try:
            self.native_requests.put_nowait((guest_id, request, future))
        except asyncio.QueueFull:
            return {"ok": False, "error": "native_actions_busy"}
        return await future

    async def service_guest_native_requests(self, bridge: Bridge) -> None:
        if self.native_requests.empty():
            return
        guest_id, request, future = self.native_requests.get_nowait()
        if future.cancelled():
            return
        try:
            identity = self.authenticated_guest(guest_id)
            if not identity:
                raise party.RuleError("guest_not_active")
            _, character = identity
            action, target = request["action"], request["target"]
            if action == "native-pickup":
                result = await self.native_pickup.claim(self, bridge, character, request["operation_id"], target)
            elif action == "native-shop-buy":
                result = await self.native_pickup.buy(self, bridge, character, request["operation_id"], target)
            elif action in native_guest_weapon.GUEST_ACTIONS:
                result = await native_guest_weapon.perform(self, bridge, character, request["operation_id"], action, target)
            elif action in ("native-loot-inspect", "native-loot-open", "native-loot-claim"):
                result = await native_loot.perform(self, bridge, character, action.removeprefix("native-loot-"),
                                                   target, request["operation_id"])
            elif action == "native-inventory-list":
                offset = target if target is not None else "0"
                if not isinstance(offset, str) or not offset.isascii() or not offset.isdigit() or len(offset) > 4:
                    raise party.RuleError("invalid_inventory_offset")
                result = await native_inventory.inspect(self, bridge, character, int(offset))
            elif action == "native-wipe-items":
                result = native_inventory_death.items(self, character, target)
            elif action == "native-wipe-recover":
                result = await native_inventory_death.recover(self, bridge, character, target, request["operation_id"])
            else:
                result = await native_quest.action(self, bridge, character, action, target)
        except (OSError, RuntimeError, ValueError) as error:
            result = {"ok": False, "error": str(error)}
        if not future.done():
            future.set_result({"ok": True, **result})

    async def project_gameplay(self, bridge: Bridge) -> None:
        now = time.monotonic()
        if self.last_game.get("state") != "hosting" or now < self.next_projection:
            return
        try:
            await project_players(bridge, self.last_game, self.public_gameplay(), client=False)
            await native_rewards.settle(self, bridge)
            if not self.fixtures:
                return
            await native_quest.project(self, bridge)
            if self.native_guest:
                await self.native_pickup.restore(self, bridge)
            money = self.party["economy"]["wallet"]
            quest_paid = any(row.get("step") == native_quest.COMPLETED
                             for row in self.party.get("native_quests", {}).values())
            if ((self.party["economy"]["quest_completed"] or quest_paid
                 or self.party["economy"].get("native_wallet_active"))
                    and self.last_game.get("native_wallet") != money):
                await bridge.command("wallet-set", str(money), timeout=4)
            if self.party["loot"] is None:
                if self.last_game["map"] != MAP:
                    return
                host = _pawn(self.last_game, "local")
                if not host or any(axis is None for axis in host["location"]):
                    return
                x, y, z = host["location"]
                position = [x + 30, y + 100, z - 55]
                await bridge.command("loot-spawn", party.LOOT_ID, *(str(axis) for axis in position), timeout=4)
                candidate = party.load_party(self.party)
                party.seed_loot(candidate, self.last_game["map"], position)
                self.checkpoint(candidate)
                self.party = candidate
                self.marker_ready = True
            else:
                loot = self.party["loot"]
                desired = loot["claimed_by"] is None
                if loot["map"] == self.last_game["map"] and desired != self.marker_ready:
                    if desired:
                        await bridge.command("loot-spawn", loot["id"],
                                             *(str(axis) for axis in loot["position"]), timeout=4)
                    else:
                        await bridge.command("loot-clear", loot["id"], timeout=4)
                    self.marker_ready = desired
        except (OSError, RuntimeError) as error:
            self.next_projection = time.monotonic() + 2
            self.control.event("gameplay_projection_error", reason=str(error))

    async def stock_respawn_marker(self, bridge: Bridge, rest: dict) -> dict:
        if rest["bed"]:
            return await self.blue_arrival(bridge, rest)
        snapshot = await bridge.command("native-wipe-fallback", timeout=6)
        proof = snapshot.get("native_blue_arrival", {})
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != rest["destination"]
                or proof.get("destination") != rest["destination"] or proof.get("bed") != ""
                or proof.get("placement") != "player_start" or not proof.get("actor", "").endswith(".PlayerStart_4")
                or len(proof.get("location", [])) != 3 or any(axis is None for axis in proof["location"])):
            raise RuntimeError("The default stock game-over arrival was not verified")
        return snapshot

    async def stock_wipe_arrival(self, bridge: Bridge, transport) -> dict:
        snapshot, rest = await self.blue_route(bridge, "native-blue-route")
        self.last_game, self.last_seen = snapshot, time.monotonic()
        destination = rest["destination"]
        roles = self.roles()
        staying = destination == self.last_game["map"]
        if self.travel_refusal(destination, roles, respawning=True, staying=staying):
            raise RuntimeError("The exact stock respawn destination is unavailable")
        self.transitioning = True
        try:
            if staying:
                observed = await self.stock_respawn_marker(bridge, rest)
                await self.place_party_in_zone(bridge, roles, observed)
            else:
                await self.switch_zone(bridge, transport, destination, "Bed", roles, respawn_plan=rest)
        finally:
            self.transitioning = False
        if self.last_game.get("map") != destination:
            raise RuntimeError("The party did not reach the host's stock rest")
        return _pawn(self.last_game, "local")

    async def settle_wipe(self, bridge: Bridge, transport) -> None:
        now = time.time()
        draft = self.party["wipe"]
        if not draft:
            roles = self.roles()
            draft = party.begin_when_all_down(self.party, tuple(roles.values()),
                                              self.authoritative_pawns(roles), now)
            if not draft:
                return
            candidate = party.load_party(self.party)
            candidate["wipe"] = draft
            self.checkpoint(candidate)
            self.party = candidate
        stock_rest = bool(self.native_guest and self.native_zones and self.native_profile is not None
                          and self.last_game.get("map") in self.allowed_maps - PLAYTEST_MAPS)
        if stock_rest and "native" not in draft:
            rules = self.last_game.get("native_rules") or self.store.status()["world"].get("native_observation", {}).get("native_rules", {})
            fixture = self.party["death_rules"]
            extraction = rules.get("extraction_mode") == 1
            configured = fixture["keep_slots"] if self.fixtures else None
            if extraction or configured is not None or (self.fixtures and fixture["drop_backpacks"]):
                slots = configured if configured is not None else rules.get("items_kept_on_death")
                if type(slots) is not int or not 0 <= slots <= 10:
                    raise RuntimeError("The exact host stock wipe retention setting is unavailable")
                drops = fixture["drop_backpacks"] if configured is not None else (
                    rules.get("drop_bone_pile") == 1 or (self.fixtures and fixture["drop_backpacks"]))
                if not set(draft["roster"]).issubset(native_inventory_death.owner_pawns(self)):
                    return
                frozen = await native_inventory_death.freeze(
                    self, bridge, draft["id"], draft["roster"], keep_slots=slots, drop_backpacks=drops,
                    lose_slot=extraction and configured is None and rules.get("lose_slot_per_death") == 1,
                )
                candidate = party.load_party(self.party)
                candidate["wipe"].update(native=frozen, keep_slots=slots, drop_backpacks=drops,
                                         deadline=time.time() + (30 if slots else 0))
                self.checkpoint(candidate)
                self.party = candidate
                draft = self.party["wipe"]
                now = time.time()
        result = wipe.finish_wipe(draft, now)
        if result is None:
            return
        checkpoint = result["checkpoint"]
        if draft.get("native") and not await native_inventory_death.settle(self, bridge, draft):
            return
        if self.dream_isolated(self.last_game) and native_dream.visit(self.party):
            await self.advance_dream(bridge, transport, wiped=True)
            if native_dream.visit(self.party):
                return
            checkpoint = _pawn(self.last_game, "local")
        elif stock_rest:
            checkpoint = await self.stock_wipe_arrival(bridge, transport)
        elif checkpoint["map"] != self.last_game.get("map"):
            outcome = await self.travel(bridge, transport, checkpoint["map"], respawning=True)
            if not outcome["ok"]:
                raise RuntimeError("Rest checkpoint is unavailable")
        candidate = party.load_party(self.party)
        candidate["players"].update(result["players"])
        candidate["backpacks"].update(result["backpacks"])
        x, y, z = checkpoint["location"]
        for offset, actor in enumerate(("host", *[name for name in draft["roster"] if name != "host"])):
            candidate["pending_spawns"][actor] = {"map": checkpoint["map"],
                                                  "location": [x + 110 * offset, y, z]}
            if stock_rest:
                candidate["pending_spawns"][actor]["at_native_rest"] = True
        candidate["wipe"] = None
        revision = await self.checkpoint_native(bridge, candidate)
        self.party = candidate
        self.control.event("party_respawn_committed", wipe_id=result["wipe_id"], revision=revision,
                           backpack_ids=list(result["backpacks"]), stock_rest=stock_rest,
                           map=checkpoint["map"], location=checkpoint["location"])

    async def apply_pending_spawns(self, bridge: Bridge) -> None:
        for role, actor in self.roles().items():
            spot = self.party["pending_spawns"].get(actor)
            name, binding = self.role_pawn(role)
            if not spot or name not in self.last_game.get("pawns", {}):
                continue
            if spot["map"] != self.last_game.get("map"):
                # The locked checkpoint has been superseded by a later zone.
                candidate = party.load_party(self.party)
                candidate["pending_spawns"].pop(actor, None)
                self.checkpoint(candidate)
                self.party = candidate
                continue
            try:
                if self.native_guest:
                    body = self.last_game["pawns"][name]
                    if body["health"] is None or body.get("max_health") is None:
                        continue
                    location = body["location"] if spot.get("at_native_rest") else spot["location"]
                    if any(axis is None for axis in location):
                        continue
                    spot = {"location": location, "health": body["max_health"]}
                state = await bridge.restore(name, spot, binding=binding,
                                             respawning=self.party["pending_spawns"][actor].get("at_native_rest", False))
            except (OSError, RuntimeError) as error:
                self.control.event("respawn_placement_pending", actor=actor, reason=str(error))
                continue
            if state:
                self.last_game = state
                self.last_seen = time.monotonic()
            candidate = party.load_party(self.party)
            candidate["pending_spawns"].pop(actor, None)
            await self.checkpoint_native(bridge, candidate)
            self.party = candidate

    def invite(self, request: dict) -> dict:
        address = request.get("address") or self.bind
        if (not isinstance(address, str) or not address.strip() or len(address) > 253
                or any(character.isspace() for character in address) or address in {"0.0.0.0", "::"}):
            raise ValueError("Supply a reachable host address for the invitation")
        guest = self.store.issue_invitation(request.get("name"), request.get("character"))
        file = self.control.run / "invites" / f"{guest['guest_id']}.invite.json"
        write_json(file, {
            "schema": 1, "certificate": self.identity.certificate_pem,
            "host": address, "port": self.port, "guest_id": guest["guest_id"],
            "credential": guest["credential"], "character_id": guest["character_id"],
            "game_build": self.game_build, "protocol_version": PROTOCOL,
            "native_zones": self.native_zones, "native_zone_walls": self.native_zone_walls,
            "expanded_zones": self.expanded_zones,
        })
        self.control.event("invite_created", guest_id=guest["guest_id"], character_id=guest["character_id"])
        return {"invite_file": str(file), "guest_id": guest["guest_id"], "character_id": guest["character_id"]}

    async def place_new_guest(self, bridge: Bridge, pawn: dict) -> dict | None:
        host = _pawn(self.last_game, "local")
        if not host or any(axis is None for axis in host["location"] + pawn["location"]):
            return None
        if self.last_game["map"] == SLATE and self.dream_isolated(self.last_game):
            placed = await bridge.command("native-dream-place-guest", pawn["name"], timeout=5)
            current = placed.get("pawns", {}).get("guest", {})
            lead = placed.get("pawns", {}).get("local", {})
            if (not self.dream_isolated(placed) or current.get("name") != pawn["name"]
                    or not lead or math.dist(lead["location"], current["location"]) > party.INTERACT_RANGE):
                raise RuntimeError("The enrolled guest's Slate descent placement could not be verified")
            self.control.event("guest_slate_descent_placed", pawn=pawn["name"],
                               host_position=lead["location"], guest_position=current["location"])
            return placed
        point = host["location"]
        if math.dist(point, pawn["location"]) <= party.INTERACT_RANGE:
            return None
        for candidate in guest_arrival_positions(point):
            try:
                restored = await bridge.restore("guest", {"location": candidate})
                placed = _pawn(restored or {}, "guest")
                if placed and None not in placed["location"] and math.dist(point, placed["location"]) <= party.INTERACT_RANGE:
                    return restored
            except (RuntimeError, TimeoutError):
                continue
        self.control.event("safe_spawn_unavailable", character_id=self.guest["character_id"])

    async def enrolled_pawn(self, pawn: dict | None, transport) -> dict | None:
        if self.enrollment is None:
            return pawn
        if self.guest is None:
            return None
        guest_id = self.guest["guest_id"]
        result = self.enrollment.observe(self.last_game)
        if result.state == "bound":
            binding = result.assignment
            self.control.event("native_guest_enrolled", guest_id=binding.guest_id, driver=binding.driver,
                               connection_id=binding.connection_id, pawn=binding.pawn)
        elif result.state == "aborted" and result.reservation and result.reservation.guest_id == guest_id:
            self.control.event("native_guest_enrollment_aborted", guest_id=guest_id, reason=result.reason)
            self.disconnected(guest_id)
            await transport.release_guest(guest_id)
        elif (result.reason == "known_connection_missing" and not self.last_game.get("remote_clients")
              and self.enrollment.retire(guest_id, self.last_game).state == "released"):
            self.guest_pawn = None
            if self.guest["disconnected"] is None:
                self.enrollment.reserve(guest_id, self.guest["character_id"], self.last_game)
        binding = self.enrollment.assignments.get(guest_id)
        if (not pawn or not binding or binding.pawn != pawn["name"] or binding.controller != pawn["controller"]
                or result.state in ("aborted", "blocked")):
            return None
        return pawn

    async def observe(self, bridge: Bridge, transport) -> None:
        state = bridge.snapshot()
        if state is not None:
            if state.get("sequence") != self.last_game.get("sequence"):
                self.last_seen = time.monotonic()
            self.last_game = state
        party.add_player(self.party, "host")
        pawn = await self.enrolled_pawn(self.last_game.get("pawns", {}).get("guest"), transport)
        if not pawn and "guest" in self.last_game.get("pawns", {}) and self.enrollment:
            self.last_game["pawns"].pop("guest")
        bound_pawn = self.last_game.get("native_guest_save", {}).get("pawn")
        lease_active = self.guest and (self.guest["disconnected"] is None
                                       or time.monotonic() - self.guest["disconnected"] < DEFAULT_RECONNECT_GRACE)
        if self.native_guest and pawn and lease_active and bound_pawn == pawn["name"]:
            health = _pawn(self.last_game, "guest")["health"]
            if health is not None:
                self.native_guest_health = (self.guest["character_id"], bound_pawn, health)
        if (self.native_guest and pawn and lease_active and self.last_game.get("remote_clients") == 1
                and bound_pawn != pawn["name"]):
            saved_health = self.guest["restore"].get("health")
            if self.native_guest_health and self.native_guest_health[:2] == (self.guest["character_id"], pawn["name"]):
                saved_health = self.native_guest_health[2]
            health = "-" if saved_health is None else str(saved_health)
            self.native_pickup.invalidate()
            frame = self.guest["restore"].get("native_guest_save")
            arguments = ()
            if frame:
                if not self.persist_native_guest or self.native_profile is None:
                    raise RuntimeError("This guest character requires the staged native save bridge")
                verified = await asyncio.to_thread(native_host_save.guest_record, self.native_profile, frame,
                                                   self.guest["character_id"])
                arguments = (verified["slot"],)
            if self.exact_guest_binding and self.enrollment:
                binding = self.enrollment.assignments.get(self.guest["guest_id"])
                if not binding or binding.pawn != pawn["name"] or binding.character_id != self.guest["character_id"]:
                    raise RuntimeError("The authenticated native guest save scope is unavailable")
                snapshot = await bridge.command("native-guest-bind-exact", binding.pawn, binding.controller,
                                                binding.connection, binding.character_id, health, *arguments, timeout=7)
            else:
                snapshot = await bridge.command("native-guest-bind", pawn["name"], health, *arguments, timeout=7)
            if snapshot.get("native_guest_save", {}).get("pawn") != pawn["name"]:
                raise RuntimeError("The guest's independent native save could not be verified")
            if frame:
                proof = snapshot.get("native_guest_save_frame", {})
                if (proof.get("state") != "restored" or proof.get("slot") != frame["slot"]
                        or proof.get("pawn") != pawn["name"]):
                    raise RuntimeError("The guest's exact persisted stock save generation could not be verified")
                self.control.event("native_guest_save_restored", character_id=self.guest["character_id"], **proof)
            self.last_game = snapshot
            self.last_seen = time.monotonic()
        if pawn and self.guest and pawn["name"] != self.guest_pawn:
            saved = self.guest["restore"]
            if self.last_game["map"] == SLATE and self.dream_isolated(self.last_game):
                restored = await self.place_new_guest(bridge, pawn)
            elif saved.get("map") == self.last_game["map"]:
                restored = None
                try:
                    restored = await bridge.restore("guest", dict(saved, health=None) if self.native_guest else saved)
                except (RuntimeError, TimeoutError):
                    # The stock game can share the SaveGame object between players.
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        restored = await bridge.restore("guest", dict(saved, health=None))
                    self.control.event("guest_restore_partial", character_id=self.guest["character_id"])
            else:
                restored = await self.place_new_guest(bridge, pawn)
            if restored:
                self.last_game = restored
                self.last_seen = time.monotonic()
            self.guest["restore"] = {}
            self.guest_pawn = pawn["name"]
            self.awaiting_zone_guest = None
            party.add_player(self.party, self.guest["character_id"])
            self.control.event("guest_pawn_ready", character_id=self.guest["character_id"], pawn=self.guest_pawn)
        if self.guest and self.guest["disconnected"] is not None:
            expired = time.monotonic() - self.guest["disconnected"] >= DEFAULT_RECONNECT_GRACE
            if expired and not self.last_game.get("remote_clients", 0):
                expired_guest = self.guest
                if self.enrollment:
                    reservation = self.enrollment.pending
                    if reservation and reservation.guest_id == expired_guest["guest_id"]:
                        self.enrollment.cancel(reservation.id)
                    self.enrollment.retire(expired_guest["guest_id"], self.last_game)
                await transport.release_guest(expired_guest["guest_id"])
                if self.guest is expired_guest:
                    self.guest = None
                    self.guest_pawn = None
                    self.awaiting_zone_guest = None
        if self.native_guest and pawn and self.guest and self.guest_pawn == pawn["name"]:
            position = _pawn(self.last_game, "guest")
            if position and all(axis is not None for axis in position["location"]):
                self.native_guest_position = (self.guest["character_id"], pawn["name"], position, self.last_seen)

    async def service_requests(self, bridge: Bridge, transport) -> None:
        for path, request in self.control.requests():
            action = request.get("action")
            try:
                if action == "invite":
                    result = self.invite(request)
                elif action == "native-pickup-spawn" and self.native_guest and self.fixtures:
                    result = await self.native_pickup.spawn(self, bridge)
                elif action in native_guest_npc.HOST_ACTIONS:
                    result = await native_guest_npc.host_fixture(self, bridge, action, request.get("target"))
                elif action == "native-shop-approach" and self.native_guest and self.fixtures:
                    target = request.get("target")
                    shop = next((entry for entry in self.last_game.get("native_shops", [])
                                 if entry["id"] == target and entry["state"] in ("ok", "partial")), None)
                    if (not shop or target not in STAGED_SHOPS or self.last_game.get("map") != MAP
                            or any(axis is None for axis in shop["location"])):
                        raise ValueError("The staged stock merchant is unavailable")
                    x, y, z = shop["location"]
                    for dx, dy in ((130, 0), (-130, 0), (0, 130), (0, -130)):
                        try:
                            await bridge.restore("guest", {"location": [x + dx, y + dy, z + 20]})
                            break
                        except (RuntimeError, TimeoutError):
                            continue
                    else:
                        raise RuntimeError("No safe position near the staged merchant")
                    result = {"near_native_shop": target}
                elif action == "native-weapon-roundtrip" and self.native_guest and self.fixtures and self.guest_pawn:
                    snapshot = await bridge.command(action, self.guest_pawn, timeout=4)
                    proof = snapshot.get("native_weapon_roundtrip") or {}
                    if proof.get("state") != "verified":
                        raise RuntimeError("Native weapon round-trip did not verify its typed payload")
                    result = {"native_weapon_roundtrip": proof}
                elif action == "native-inventory-list":
                    character = self.roles().get(request.get("target") or "host")
                    offset = request.get("amount")
                    result = await native_inventory.inspect(self, bridge, character, 0 if offset is None else offset)
                elif action == "native-wipe-inventory-fixture":
                    character = self.roles().get(request.get("target") or "guest")
                    result = await native_inventory_death.probe(self, bridge, character, request.get("item_guid"))
                elif action == "native-wipe-items":
                    result = native_inventory_death.items(self, "host", request.get("target"))
                elif action == "native-wipe-recover":
                    result = await native_inventory_death.recover(self, bridge, "host", request.get("target"),
                                                                  request.get("operation_id") or path.stem)
                elif action in ("native-guest-weapon-seed", "native-guest-weapon-seed-ammo") and self.native_guest and self.fixtures:
                    character = self.roles().get(request.get("target") or "guest")
                    guid = request.get("item_guid") or uuid.uuid4().hex.upper()
                    result = await native_guest_weapon.perform(self, bridge, character, request.get("operation_id") or path.stem,
                                                               action, guid)
                elif action in ("native-loot-inspect", "native-loot-open", "native-loot-approach",
                                "native-loot-seed", "native-loot-claim"):
                    operation = action.removeprefix("native-loot-")
                    character = (self.roles().get(request.get("target") or "guest")
                                 if operation in ("seed", "approach") else "host")
                    target = request.get("target") if operation == "claim" else native_loot.CRATE
                    result = await native_loot.perform(self, bridge, character, operation, target,
                                                       request.get("operation_id") or path.stem)
                elif action == "native-quest-catalog":
                    snapshot = await bridge.command(action, timeout=7)
                    catalog = snapshot.get("native_quest_catalog")
                    if not catalog or catalog["sequence"] != snapshot.get("sequence"):
                        raise RuntimeError("The stock quest catalog did not return a complete native frame")
                    result = {"native_quest_catalog": catalog}
                elif action in ("native-quest-start", "native-quest-objective", "native-quest-turnin"):
                    result = await native_quest.action(self, bridge, "host", action, request.get("target"))
                elif action == "native-broker-issue-fixture" and self.fixtures:
                    result = await native_broker.action(self, bridge, request.get("target") or "host",
                                                        "native-quest-start", fixture=True)
                elif action == "native-broker-position-fixture" and self.fixtures:
                    slot, point = request.get("target"), request.get("entrance")
                    if slot not in ("host", "guest") or point not in ("issue", "redeem"):
                        raise ValueError("Choose a Broker fixture pawn and issue or redeem position")
                    pawn = self.last_game.get("pawns", {}).get("local" if slot == "host" else "guest", {}).get("name")
                    if not pawn or slot == "guest" and pawn != self.guest_pawn:
                        raise ValueError("The fixture requires an already enrolled pawn")
                    snapshot = await bridge.command(action, pawn, point, timeout=6)
                    proof = snapshot.get("native_broker_position", {})
                    if (proof.get("pawn") != pawn or proof.get("point") != point
                            or proof.get("map") != self.last_game.get("map") or None in proof.get("location", [None])):
                        raise RuntimeError("The stock Broker fixture position was not verified")
                    self.last_game, self.last_seen = snapshot, time.monotonic()
                    result = {"native_broker_position": proof}
                elif action == "native-broker-grant-manifest" and self.fixtures:
                    guid = request.get("target")
                    if not native_gold.valid_guid(guid):
                        raise ValueError("The Broker fixture requires a valid exact Manifest GUID")
                    snapshot = await bridge.command(action, guid, timeout=6)
                    proof = snapshot.get("native_broker", {})
                    if proof.get("manifest_guid") != guid or proof.get("id") != native_broker.QUEST_ID:
                        raise RuntimeError("The native Broker fixture Manifest was not verified")
                    self.last_game, self.last_seen = snapshot, time.monotonic()
                    result = {"native_broker": proof, "revision": await self.checkpoint_native(bridge)}
                elif (action == "native-inventory-probe" and self.native_backend == "cpp"
                      and self.fixtures and not self.native_guest):
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_inventory_canary": snapshot.get("native_inventory_canary")}
                elif (action in ("native-death-probe", "native-damage-probe", "native-guest-damage", "native-host-damage")
                      and self.native_backend and self.fixtures):
                    if (action in ("native-guest-damage", "native-host-damage")) != self.native_guest:
                        raise ValueError("Use the damage probe for this host's native ownership mode")
                    target = request.get("target")
                    supported_roles = self.roles() if action == "native-guest-damage" else ("host", "guest")
                    if target not in supported_roles:
                        raise ValueError("Choose a current host or guest role for the native death probe")
                    alias, _ = self.role_pawn(target)
                    if not alias:
                        raise ValueError("The exact native death probe pawn is unavailable")
                    snapshot = await bridge.command(action, alias, timeout=4)
                    result = {"native_death_hook": snapshot.get("native_death_hook"),
                              "native_damage_canary": snapshot.get("native_damage_canary")}
                elif action == "quest-probe" and self.fixtures:
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_quest": snapshot.get("native_quest")}
                elif action == "native-friendly-fire" and self.native_guest and self.fixtures:
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_friendly_fire": snapshot.get("native_friendly_fire")}
                elif action in ("native-hazard-position", "native-hazard-start", "native-hazard-return"):
                    result = await self.native_hazard(bridge, action)
                elif action == "native-fall-position" and self.native_guest and self.fixtures:
                    guest = self.last_game.get("pawns", {}).get("guest", {})
                    scope = self.last_game.get("native_fall", {})
                    if (self.last_game.get("map") != MAP or not self.guest_pawn or guest.get("name") != self.guest_pawn
                            or scope.get("state") != "ready" or scope.get("pawn") != self.guest_pawn
                            or not guest.get("health") or guest["health"] <= 0 or self.fall_origin):
                        raise ValueError("The staged enrolled guest fall fixture is unavailable")
                    x, y, z = guest["location"]
                    await bridge.restore("guest", {"location": [x, y, z + 6500]})
                    self.fall_origin = (self.guest_pawn, [x, y, z])
                    result = {"pawn": self.guest_pawn, "height_uu": 6500, "before": scope}
                elif action == "native-fall-return" and self.native_guest and self.fixtures:
                    character = self.roles().get("guest")
                    if (self.last_game.get("map") != MAP or not self.fall_origin or self.fall_origin[0] != self.guest_pawn
                            or not character or not self.party["players"][character]["downed"]):
                        raise ValueError("The fall fixture can only return its exact downed guest")
                    self.last_game = await bridge.restore("guest", {"location": self.fall_origin[1]})
                    self.last_seen = time.monotonic()
                    self.fall_origin = None
                    result = {"native_fall_guest_returned": True}
                elif action in ("vitals-probe", "vitals-reset") and self.fixtures:
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_vitals_probe": snapshot.get("native_vitals_probe")}
                elif action == "travel":
                    result = await self.travel(bridge, transport, request.get("target"))
                    success = result.pop("ok")
                    self.control.respond(path, success, **result)
                    continue
                elif action in ("native-exit-position", "native-exit-wall-probe") and self.native_zones and self.fixtures:
                    target = request.get("target")
                    entrance = request.get("entrance")
                    if target not in self.roles():
                        raise ValueError("Specify a current player for the exit fixture")
                    if entrance is not None:
                        source = self.last_game.get("map")
                        legacy_tag = any(area == source and tag == entrance for (area, _), tag in AUTHORED_EXITS.items())
                        expanded_tag = (action == "native-exit-wall-probe" and self.expanded_zones
                                        and any(route.source == source and route.entrance == entrance
                                                for route in AUTHORED_ROUTES))
                        if not (legacy_tag or expanded_tag):
                            raise ValueError("Choose an authored exit entrance tag in the host's current map")
                    alias, binding = self.role_pawn(target)
                    if not alias:
                        raise ValueError("The exit fixture pawn is unavailable")
                    arguments = [action, binding.pawn if binding else alias]
                    position = request.get("exit_position")
                    placing = action == "native-exit-position"
                    if placing:
                        if position not in ("near", "inside", "far"):
                            raise ValueError("Choose a position for the exit fixture")
                        arguments.append(position)
                    if entrance:
                        arguments.append(entrance)
                    snapshot = await bridge.command(*arguments, timeout=4)
                    if placing:
                        result = {"native_zone": snapshot.get("native_zone"), "target": target,
                                  "position": position, "entrance": entrance}
                    else:
                        result = {"native_zone_wall": snapshot.get("native_zone_wall")}
                elif action in ("native-door-position", "native-door-probe") and self.native_zones and self.fixtures:
                    target = request.get("target")
                    if target not in self.roles():
                        raise ValueError("Specify a current player for the door fixture")
                    alias, binding = self.role_pawn(target)
                    if not alias:
                        raise ValueError("The door fixture pawn is unavailable")
                    arguments = [action, binding.pawn if binding else alias]
                    if action == "native-door-position":
                        position = request.get("exit_position")
                        if position not in ("near", "far"):
                            raise ValueError("Specify a near or far door fixture position")
                        arguments.append(position)
                    if entrance := request.get("entrance"):
                        if not self.expanded_zones or not any(
                            route.kind == "door" and route.source == self.last_game.get("map")
                            and route.entrance == entrance for route in AUTHORED_ROUTES
                        ):
                            raise ValueError("Choose an authored door entrance in the current map")
                        arguments.append(entrance)
                    snapshot = await bridge.command(*arguments, timeout=4)
                    result = {"native_zone": snapshot.get("native_zone"), "native_door": snapshot.get("native_door")}
                elif action == "native-cistern-keypad" and self.expanded_zones and self.fixtures:
                    key = request.get("amount")
                    if type(key) is not int or not 0 <= key <= 11 or self.last_game.get("map") != CISTERN:
                        raise ValueError("Supply one stock keypad index from 0 through 11 in the Cistern")
                    snapshot = await bridge.command(action, str(key), timeout=4)
                    proof = snapshot.get("native_cistern_keypad", {})
                    if (proof.get("map") != CISTERN or proof.get("key") != key
                            or proof.get("pawn") != snapshot.get("pawns", {}).get("local", {}).get("name")
                            or not proof.get("door", "").endswith("Dungeon_TransitionDoor_BP_C_2")):
                        raise RuntimeError("The stock Cistern keypad action was not verified")
                    result = {"native_cistern_keypad": proof}
                elif action == "native-transport-probe" and self.native_zones and self.fixtures:
                    result = await probe_transport(bridge, request.get("target"))
                elif action in ("native-matrix-inspect", "native-matrix-seed", "native-matrix-equip",
                                "native-matrix-fuel", "native-matrix-probe"):
                    result = await matrix_service.control_action(self, bridge, action.removeprefix("native-matrix-"), request)
                elif (action in ("native-gold-seed", "native-gold-inspect", "native-gold-use-probe",
                                 "native-gold-action-probe", "native-blue-seed", "native-blue-inspect",
                                 "native-blue-use-probe", "native-red-seed", "native-red-inspect",
                                 "native-red-use-probe", "native-green-seed", "native-green-inspect",
                                 "native-green-use-probe", "native-purple-seed", "native-purple-inspect",
                                 "native-purple-use-probe")
                      and self.native_zones and self.fixtures and GOLD_NEXUS in self.allowed_maps):
                    _, cube, operation = action.split("-", 2)
                    _, item = await self.checked_cube(bridge, cube, operation, request.get("target"))
                    result = {f"native_{cube}": item}
                elif action == "native-blue-rest-fixture" and self.native_zones and self.fixtures:
                    _, route = await self.blue_route(bridge, action)
                    result = {"native_blue_route": route}
                elif (action in {f"native-{kind}-{operation}" for kind in native_dream.BASE_SECONDS
                                 for operation in ("seed", "inspect", "use-probe")}
                      and self.native_zones and self.fixtures):
                    kind = "fruiting-stardust" if action.startswith("native-fruiting-") else "stardust"
                    operation = action.removeprefix(f"native-{kind}-")
                    arguments = ()
                    if operation == "seed":
                        amount = request.get("amount")
                        if type(amount) is not int or not 1 <= amount <= native_dream.MAX_STACK:
                            raise ValueError("Supply a positive Stardust stack quantity in --amount")
                        arguments = (str(amount),)
                    _, item = await self.stardust_operation(bridge, operation, request.get("target"), *arguments, kind=kind)
                    result = {"native_" + kind.replace("-", "_"): item}
                elif action == "native-dream-tarot-fixture" and self.native_zones and self.fixtures:
                    flag, points = request.get("target"), request.get("amount")
                    if flag not in native_dream.TAROT_DESTINATIONS or not flag or type(points) is not int or not 1 <= points <= 100:
                        raise ValueError("The Tarot fixture requires a qualified flag and positive points")
                    write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
                    await bridge.command(action, flag, str(points), timeout=4)
                    result = {"flag": flag, "points": points}
                elif action == "native-dream-power-fixture" and self.native_zones and self.fixtures:
                    if not self.dream_isolated(self.last_game) or not native_dream.visit(self.party):
                        raise ValueError("The stock power fixture requires an active isolated dream visit")
                    write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_dream_isolation": snapshot.get("native_dream_isolation")}
                elif action in ("native-dream-stone-position", "native-dream-stone-fixture") and self.native_zones and self.fixtures:
                    slot = request.get("target") or "host"
                    if slot not in self.roles() or not self.dream_isolated(self.last_game, destination=BLACK_OCEAN):
                        raise ValueError("Choose an enrolled participant during the Black Ocean visit")
                    alias, binding = self.role_pawn(slot)
                    pawn = binding.pawn if binding else self.last_game.get("pawns", {}).get(alias, {}).get("name")
                    if not pawn:
                        raise ValueError("The exact Black Ocean fixture pawn is unavailable")
                    write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
                    snapshot = await bridge.command(action, pawn, timeout=5)
                    self.last_game, self.last_seen = snapshot, time.monotonic()
                    result = {"native_black_stone": snapshot.get("native_black_stone", {})}
                elif action == "native-rest-inspect":
                    result = {"native_rest": await native_rest.checked(self, bridge, action)}
                elif action == "native-rest":
                    result = await native_rest.perform(self, bridge, request.get("operation_id") or path.stem,
                                                       request.get("amount"))
                elif action == "native-rest-approach" and self.fixtures:
                    probe = await native_rest.checked(self, bridge, "native-rest-inspect")
                    rest = {"source": probe["map"], "destination": probe["map"], "bed": probe["bed"]}
                    self.last_game = await self.blue_arrival(bridge, rest)
                    self.last_seen = time.monotonic()
                    result = {"native_rest": await native_rest.checked(self, bridge, "native-rest-inspect")}
                elif action == "native-rest-ui-fixture" and self.fixtures:
                    hours = request.get("amount")
                    proof = await native_rest.checked(self, bridge, "native-rest-inspect")
                    if type(hours) is not int or not 0 <= hours <= 10 or not proof["eligible"]:
                        raise ValueError("The exact stock bed and sleep hours are required")
                    snapshot = await bridge.command(action, proof["pawn"], str(hours), timeout=6)
                    result = {"native_rest_hook": snapshot.get("native_rest_hook", {})}
                elif action == "native-rest-guest-interaction-fixture" and self.fixtures:
                    character = self.roles().get(request.get("target") or "guest")
                    owner = self.native_recipients().get(character)
                    if not owner:
                        raise ValueError("Choose an enrolled stock rest guest")
                    snapshot = await bridge.command(action, owner["pawn"], timeout=6)
                    result = {"native_rest_guard": snapshot.get("native_rest_guard", {})}
                elif action == "walk":
                    await bridge.walk(request.get("x"), request.get("y"), request.get("milliseconds"))
                    result = {"movement_scheduled": True}
                elif action in ("interact", "buy", "test-damage", "test-quest-reward", "rest", "keep"):
                    result = self.act("host", request.get("operation_id") or path.stem,
                                      action, request.get("target"), request.get("amount"),
                                      retained=request.get("retained"))
                elif action == "checkpoint" or action == "stop":
                    result = {"revision": await self.checkpoint_native(bridge)}
                    self.control.event("checkpoint_saved", revision=result["revision"], command_id=path.stem)
                    if action == "stop":
                        self.stopping = True
                else:
                    raise ValueError("Action unavailable on the host")
                self.control.respond(path, True, **result)
            except (ValueError, OSError, CampaignError, RuntimeError) as error:
                self.control.respond(path, False, error=str(error))

    async def native_hazard(self, bridge: Bridge, action: str) -> dict:
        if not self.native_guest or not self.fixtures or self.last_game.get("map") != MAP or not self.guest_pawn:
            raise ValueError("The stock hazard probe requires the paired C++ fixtures")
        status = self.last_game.get("native_hazard", {})
        if action == "native-hazard-position":
            if self.hazard_origin:
                raise ValueError("Return the guest from the previous hazard probe first")
            position = _pawn(self.last_game, "guest")
            if not position or any(axis is None for axis in position["location"]):
                raise ValueError("The guest's safe return position is unavailable")
            await bridge.command(action, self.guest_pawn, timeout=4)
            self.hazard_origin = (self.guest_pawn, position)
            return {"hazard_positioned": True}
        if not self.hazard_origin or self.hazard_origin[0] != self.guest_pawn:
            raise ValueError("The exact hazard probe pawn is unavailable")
        if action == "native-hazard-start":
            snapshot = await bridge.command(action, self.guest_pawn, timeout=4)
            return {"native_hazard": snapshot.get("native_hazard")}
        if status.get("state") not in ("downed", "stopped", "failed", "timeout"):
            raise ValueError("The stock gas must finish shutting down before moving the guest")
        await bridge.restore("guest", dict(self.hazard_origin[1], health=None))
        self.hazard_origin = None
        return {"hazard_guest_returned": True}

    def deliver_native_deaths(self, bridge: Bridge) -> None:
        self.pending_native_deaths.extend(bridge.native_deaths())
        if time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            return
        pending = []
        for slot, operation_id, pawn in self.pending_native_deaths:
            observed = self.last_game.get("pawns", {}).get(slot, {}).get("name")
            target = self.roles().get("host" if slot == "local" else "guest")
            last_target = None
            if slot == "guest" and observed is None and self.native_guest_position and target and pawn:
                actor, prior, position, observed_at = self.native_guest_position
                if (actor == target and prior == pawn == self.guest_pawn
                        and position["map"] == self.last_game.get("map")
                        and time.monotonic() - observed_at <= POSITION_MAX_AGE):
                    last_target = position
            if (pawn and pawn != observed and not last_target) or (self.native_guest and not pawn):
                self.control.event("native_death_rejected", operation_id=operation_id, reason="pawn_identity_changed")
                continue
            try:
                if last_target:
                    self.act("host", operation_id, "native-down", target, 1000, last_native_target=last_target)
                else:
                    self.act("host", operation_id, "native-down", target, 1000)
            except OSError:
                pending.append((slot, operation_id, pawn))
            except (ValueError, RuntimeError) as error:
                self.control.event("native_death_rejected", operation_id=operation_id, reason=str(error))
        self.pending_native_deaths = pending

    async def service_native_zones(self, bridge: Bridge, transport, now: float) -> None:
        if now >= self.next_zone_heartbeat and now - self.last_seen < 1:
            write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
            self.next_zone_heartbeat = now + 0.6
        for operation_id, source, pawn, bed, hours in bridge.native_rest_requests():
            local = self.last_game.get("pawns", {}).get("local", {}).get("name")
            site = native_rest.site(source)
            if source != self.last_game.get("map") or not site or pawn != local or bed != site[2]:
                continue
            try:
                await native_rest.perform(self, bridge, operation_id, hours)
            except (OSError, RuntimeError, ValueError) as error:
                self.control.event("native_rest_input_denied", operation_id=operation_id, reason=str(error))
                if self.stopping:
                    return
        if self.expanded_zones:
            for sequence, route in bridge.authored_zone_requests():
                result = await self.travel_authored(bridge, transport, route)
                self.control.event("native_authored_zone_request", sequence=sequence, source=route.source,
                                   destination=route.destination, actor=route.actor, entrance=route.entrance,
                                   kind=route.kind, travel_time_step=route.travel_time_step, result=result)
                if not result["ok"] and not self.stopping and self.last_game.get("map") == route.source:
                    await bridge.command("native-exit-release", timeout=3)
        if self.matrix_supported:
            for request in bridge.native_matrix_requests():
                try:
                    result = await matrix_service.activate(self, bridge, transport, request)
                except (OSError, RuntimeError, ValueError) as error:
                    if self.stopping:
                        raise
                    result = {"ok": False, "error": str(error)}
                self.control.event("native_matrix_request", sequence=request.sequence, source=request.source,
                                   guid=request.guid, action=request.action, result=result)
        for cube, activate in (("gold", self.activate_gold), ("blue", self.activate_blue),
                               ("red", self.activate_red), ("green", self.activate_green),
                               ("purple", partial(self.activate_random_cube, cube="purple")),
                               ("stardust", self.activate_stardust),
                               ("fruiting-stardust", partial(self.activate_stardust, kind="fruiting-stardust"))):
            for request in bridge.native_cube_requests(cube):
                try:
                    result = await activate(bridge, transport, request)
                except (OSError, RuntimeError, ValueError) as error:
                    self.control.event(f"native_{cube}_request_failed", sequence=request.sequence,
                                       source=request.source, guid=request.guid, error=str(error))
                    if self.stopping:
                        raise
                    result = {"ok": False, "error": str(error)}
                self.control.event(f"native_{cube}_request", sequence=request.sequence, source=request.source,
                                   guid=request.guid, entry=request.entry, position=request.location, result=result)
        for sequence, source, destination, entrance in bridge.native_zone_requests():
            if self.last_game.get("state") != "hosting" or self.last_game.get("map") != source:
                continue
            try:
                result = await self.travel(bridge, transport, destination, entrance=entrance)
            except (RuntimeError, OSError, TimeoutError) as error:
                result = {"ok": False, "error": str(error)}
            self.control.event("native_zone_request", sequence=sequence, source=source, destination=destination,
                               result=result)
            if not result["ok"] and not self.stopping and self.last_game.get("map") == source:
                with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                    await bridge.command("native-exit-release", timeout=2)

    def should_keep_native_scope(self) -> bool:
        return bool(not self.native_guest or (
            self.roles().get("guest") and self.guest_pawn
            and self.last_game.get("native_guest_save", {}).get("pawn") == self.guest_pawn
        ))

    def game_link_connected(self, transport) -> bool:
        return bool(self.guest and transport.connected(self.guest["guest_id"]))

    async def renew_native_scope(self, bridge: Bridge) -> None:
        sequence, seen = None, 0.0
        while not self.stopping and bridge.process.poll() is None:
            game = bridge.snapshot()
            now = time.monotonic()
            if game and game.get("sequence") != sequence:
                sequence, seen = game.get("sequence"), now
            if (game and game.get("state") == "hosting" and now - seen < 1.5
                    and self.should_keep_native_scope()):
                with contextlib.suppress(OSError):
                    write_text(bridge.directory / "native-events-heartbeat", f"{int(time.time())}\n")
            await asyncio.sleep(.4)

    async def run(self, bridge: Bridge, transport) -> None:
        heartbeat = asyncio.create_task(self.renew_native_scope(bridge)) if self.native_events else None
        try:
            await self.run_session(bridge, transport)
        finally:
            if heartbeat:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def run_session(self, bridge: Bridge, transport) -> None:
        while not self.stopping:
            if bridge.process.poll() is not None:
                raise RuntimeError(f"Host game exited with code {bridge.process.returncode}")
            await self.observe(bridge, transport)
            now = time.monotonic()
            if self.native_backend:
                self.deliver_native_deaths(bridge)
            if self.native_zones:
                await self.service_native_zones(bridge, transport, now)
                if not self.stopping:
                    await self.advance_dream(bridge, transport)
            if not self.stopping:
                await self.service_guest_native_requests(bridge)
            for operation_id in bridge.interactions():
                try:
                    try:
                        self.act("host", operation_id, "interact", None)
                    except party.RuleError as error:
                        bag = native_inventory_death.nearby_bag(self, "host") if str(error) == "no_interaction_in_range" else None
                        if not bag:
                            raise
                        await native_inventory_death.recover(self, bridge, "host", bag, operation_id)
                except (ValueError, OSError, RuntimeError) as error:
                    self.control.event("interaction_rejected", operation_id=operation_id, reason=str(error))
            await self.service_requests(bridge, transport)
            if not self.stopping:
                await self.settle_wipe(bridge, transport)
                await self.apply_pending_spawns(bridge)
                await self.project_gameplay(bridge)
            now = time.monotonic()
            if not self.stopping and now >= self.next_save:
                self.next_save = now + 5
                try:
                    await self.checkpoint_native(bridge)
                except (OSError, RuntimeError) as error:
                    self.control.event("native_host_checkpoint_error", reason=str(error))
            self.control.status(active=not self.stopping, role="host", state="hosting", game=self.last_game,
                                endpoint={"address": self.bind, "port": self.port},
                                campaign=self.store.status(), gameplay=self.public_gameplay(),
                                zone_epoch=self.zone_epoch, awaiting_zone_guest=self.awaiting_zone_guest,
                                link_connected=self.game_link_connected(transport))
            await asyncio.sleep(0.25)


async def host_game(work: Path, directory: Path, bind: str, port: int, headless: bool,
                    fixtures: bool = False, death_rules: dict | None = None, *,
                    native_events: bool = False, native_guest: bool = False, native_zones: bool = False,
                    native_zone_walls: bool = False, start_zone: str | None = None, max_guests: int = 1,
                    expanded_zones: bool = False) -> None:
    if start_zone == GOLD_NEXUS or start_zone in native_dream.PROFILES:
        raise ValueError("Enter this destination through its required host transport item")
    game_build = staged_build(work)
    native_backend = native_death_backend(work)
    if native_events and native_backend != "cpp":
        raise ValueError("--native-events requires the staged C++ lab")
    if native_guest and not native_events:
        raise ValueError("--native-guest requires the staged C++ event scope (--native-events)")
    if type(max_guests) is not int or not 1 <= max_guests <= 4:
        raise ValueError("The staged host accepts one to four guests")
    if max_guests > 1 and not (native_guest and native_zones):
        raise ValueError("Multiple guests require the staged --native-guest and --native-zones scopes")
    if expanded_zones and not (native_zones and staged_script(work, "authored_zone_maps.lua")
                               and (not native_zone_walls or staged_script(work, "authored_wall_routes.lua"))):
        raise ValueError("Expanded stock routes require --native-zones and the current stage")
    profile = directory / "game-user"
    with Control(directory / "run") as control, CampaignStore(
        directory / "save", verify_checkpoint=lambda checkpoint: native_host_save.verify_checkpoint(profile, checkpoint),
    ) as store:
        old_world = store.status()["world"]
        if start_zone and old_world.get("map") not in (None, start_zone):
            raise ValueError("An existing campaign resumes in its saved zone")
        entry_map = old_world.get("map", start_zone or MAP)
        authored = entry_map in AUTHORED_MAPS or expanded_zones and entry_map in EXPANDED_MAPS
        if authored and not (native_zones and qualified_authored_routes(work)):
            raise ValueError("Authored co-op routes require the qualified C++ stage and --native-zones")
        if not authored and entry_map not in PLAYTEST_MAPS:
            raise ValueError("The campaign is saved in an unqualified zone")
        persist_guest = native_guest and staged_script(work, "guest_save.lua", "native-guest-save-frame")
        native_profile = profile if (authored or persist_guest) and staged_script(work, "native_host_save.lua") else None
        native_record = old_world.get("native_host_save")
        if native_record and native_profile is None:
            raise ValueError("Restage the current mod to restore this campaign's native host save")
        saved_party = party.load_party(old_world.get("party"))
        pending = pending_cube(saved_party)
        dream = native_dream.visit(saved_party)
        matrix = native_matrix.pending(saved_party)
        matrix_supported = authored and staged_script(work, "native_matrix.lua") and staged_script(work, "native_matrix_eather.lua")
        if matrix and (pending or dream or not matrix_supported or not native_profile or not native_record
                       or entry_map != matrix["source"]):
            raise ValueError("The committed Matrix trip does not match its paired stock host save")
        if pending and (not authored or entry_map != pending[1]["source"]):
            raise ValueError("The campaign's committed item travel does not match its saved zone")
        if ((dream or entry_map in native_dream.PROFILES) and (
            pending or not dream or not authored
            or entry_map != (dream["source"] if dream["phase"] == "departing" else dream["destination"])
        )):
            raise ValueError("The campaign's Stardust visit does not match its saved zone")
        if dream and dream["effects"] != "legacy" and (not native_profile or not native_record):
            raise ValueError("The campaign's Stardust visit is missing its paired stock host save")
        process = transport = None
        controller = None
        result = "stopped"
        try:
            control.status(active=True, role="host", state="starting")
            identity = _identity(directory / "save")
            internal_port = spare_udp_port()
            bridge_directory = directory / "run" / "game-bridge" / uuid.uuid4().hex
            process = launch(work, profile, bridge_directory, headless, internal_port,
                             native_events=native_events, native_guest=native_guest, native_zones=native_zones,
                             native_zone_walls=native_zone_walls, native_fixtures=fixtures, expanded_zones=expanded_zones)
            bridge = Bridge(bridge_directory, process)
            await bridge.wait_for(lambda state: state["state"] == "ready")
            seed = checkpoint_seed(old_world) if staged_script(work, "world_seed.lua") else None
            arguments = (str(seed),) if seed is not None else ()
            if native_record:
                arguments = ("native-save", native_record["slot"])
                if "eather_remainder" in native_record:
                    arguments += (f"eather={native_record['eather_remainder']:.17g}",)
                if dream and (dream["phase"] != "departing" or entry_map in native_dream.PROFILES):
                    arguments += (dream["kind"],)
            await bridge.command("host", entry_map, *arguments)
            initial_game = await bridge.wait_for(lambda state: state["state"] == "hosting")
            await asyncio.to_thread(verify_loopback_socket, process.pid, internal_port)
            if native_record:
                initial_game = await bridge.wait_for(
                    lambda game: game.get("native_host_save", {}).get("state") in {"restored", "changed"}, timeout=7,
                )
                receipt = initial_game["native_host_save"]
                if (receipt["state"] != "restored" or receipt["slot"] != native_record["slot"]
                        or initial_game.get("map") != entry_map or receipt["map"] != entry_map
                        or receipt["pawn"] != initial_game.get("pawns", {}).get("local", {}).get("name")
                        or receipt.get("eather_remainder") != native_record.get("eather_remainder")):
                    raise RuntimeError("The host pawn did not adopt the exact saved stock generation")
                control.event("native_host_save_restored", **receipt)
            if seed is not None or native_record:
                initial_game = await bridge.wait_for(
                    lambda game: game.get("native_seed", {}).get("state") in {"verified", "save_only", "changed", "unavailable"},
                    timeout=7,
                )
                control.event("native_seed_restored", **initial_game["native_seed"])
            saved = old_world.get("host", {})
            if saved.get("map") == entry_map:
                try:
                    await bridge.restore("local", saved)
                except (RuntimeError, TimeoutError):
                    control.event("host_restore_partial")
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        await bridge.restore("local", dict(saved, health=None))
            constructor, multi_options = Host, {}
            if max_guests > 1:
                from .multi_host import MultiHost
                constructor, multi_options = MultiHost, {"max_guests": max_guests}
            controller = constructor(store, control, game_build, identity, bind, port, fixtures, death_rules,
                              native_backend=native_backend, native_events=native_events, native_guest=native_guest,
                              native_zones=native_zones, authored_zones=authored, native_zone_walls=native_zone_walls,
                              native_profile=native_profile, verify_native_roster=staged_script(work, "native_roster.lua"),
                              persist_native_guest=persist_guest, matrix_supported=matrix_supported,
                              world_flush_supported=bool(authored and native_profile and staged_script(work, "native_world_flush.lua")),
                              expanded_zones=expanded_zones,
                              exact_guest_binding=persist_guest and staged_script(work, "guest_save.lua", "native-guest-bind-exact"),
                              **multi_options)
            controller.internal_port = internal_port
            controller.last_game = bridge.snapshot() or initial_game
            controller.last_seen = time.monotonic()
            rest = old_world.get("native_rest")
            if authored and isinstance(rest, dict):
                qualified_rest = native_blue.saved_rest(rest.get("destination"), rest.get("bed"))
                if not qualified_rest:
                    raise ValueError("The campaign's saved native rest point is invalid")
                restored, route = await controller.blue_route(bridge, "native-blue-rest-restore",
                                                              qualified_rest["destination"], qualified_rest["bed"])
                if route["destination"] != qualified_rest["destination"] or route["bed"] != qualified_rest["bed"]:
                    raise RuntimeError("The saved native rest point did not restore")
                controller.last_game = restored
                controller.last_seen = time.monotonic()
            if pending:
                methods = {"gold": controller.resume_gold, "blue": controller.resume_blue}
                resume = methods.get(pending[0]) or partial(controller.resume_random_cube, cube=pending[0])
                await resume(bridge, None, recovery=True)
            if dream and dream["phase"] == "departing":
                await controller.depart_dream(bridge, None, recovery=True)
            elif dream:
                controller.last_game = await bridge.wait_for(
                    partial(controller.dream_isolated, destination=dream["destination"]), timeout=7)
                controller.last_seen = time.monotonic()
                await controller.ensure_dream_stack(bridge, dream)
                await controller.advance_dream(bridge, None)
            elif matrix:
                await matrix_service.resume(controller, bridge, None, recovery=True)
            transport = await link.start_host(bind, port, "127.0.0.1", internal_port, identity,
                                              controller.admit, on_disconnect=controller.disconnected,
                                              on_request=controller.guest_request, max_game_guests=max_guests)
            controller.port = transport.address[1]
            control.event("listening", address=bind, port=controller.port)
            await controller.run(bridge, transport)
        except (Exception, asyncio.CancelledError) as error:
            result = "stopped" if isinstance(error, asyncio.CancelledError) else "failed"
            control.event("host_stopping", outcome=result, error_type=type(error).__name__)
            raise
        finally:
            if controller:
                controller.stopping = True
                if (result == "stopped" and not controller.native_checkpoint_unsafe
                        and controller.last_game.get("state") == "hosting" and process.poll() is None):
                    with contextlib.suppress(CampaignError, OSError, RuntimeError):
                        await controller.checkpoint_native(bridge)
                if native_guest and process.poll() is None:
                    with contextlib.suppress(OSError, RuntimeError):
                        await bridge.command("native-guest-clear", timeout=2)
            if transport:
                await transport.close()
            if process:
                await stop_game(process)
            control.status(active=False, role="host", state=result, campaign=store.status())


async def probe_transport(bridge: Bridge, item: str) -> dict:
    if item not in TRANSPORT_FIXTURES:
        raise ValueError("Choose " + ", ".join(TRANSPORT_FIXTURES))
    snapshot = await bridge.command("native-transport-probe", item, timeout=6)
    return {"native_transport": snapshot.get("native_transport")}


def load_invite(file: Path, build: str) -> dict:
    invite = read_json(file, limit=32_768)
    required = {"schema", "host", "port", "certificate", "guest_id", "credential", "character_id",
                "game_build", "protocol_version"}
    if not required.issubset(invite) or invite["schema"] != 1:
        raise ValueError("Unrecognized invitation file")
    if invite["game_build"] != build or invite["protocol_version"] not in {1, PROTOCOL}:
        raise ValueError("Invitation and staged game/mod build are incompatible")
    if not isinstance(invite["host"], str) or type(invite["port"]) is not int or not 0 < invite["port"] < 65536:
        raise ValueError("Invitation has an invalid endpoint")
    if not all(isinstance(invite[key], str) for key in ("certificate", "guest_id", "credential", "character_id")):
        raise ValueError("Invitation has invalid credential fields")
    if any(type(invite[key]) is not bool for key in ("native_zones", "native_zone_walls", "expanded_zones") if key in invite):
        raise ValueError("Invitation has invalid native travel settings")
    return invite


async def open_admission(transport, invite: dict, request: dict, *, timeout: float,
                         queue: bool = False, on_wait=None) -> dict:
    """Retry only bounded, authenticated waiting states reported by the host."""
    windows = {"guest_already_connected": 2.0, "native_roster_unavailable": 5.0}
    if queue:
        windows.update(travel_in_progress=25.0, wipe_in_progress=35.0)
    started = time.monotonic()
    while True:
        result = await transport.connect(
            invite["host"], invite["port"], invite["certificate"], request, timeout=timeout,
        )
        if result.get("accepted"):
            return result
        reason = result.get("reason")
        if not isinstance(reason, str) or time.monotonic() - started >= windows.get(reason, 0):
            return result
        if on_wait:
            on_wait(reason)
        await asyncio.sleep(0.25)


async def character_catalog(work: Path, invitation: Path, name: str | None,
                            operation_id: str | None) -> dict:
    invite = load_invite(invitation, staged_build(work))
    admission = {key: invite[key] for key in ("guest_id", "credential", "character_id", "game_build")}
    admission.update({"protocol_version": PROTOCOL, "catalog": True})
    client = await link.start_client(catalog_only=True)
    try:
        result = await open_admission(client, invite, admission, timeout=6)
        if not result.get("accepted") or result.get("game_access") is not False:
            return {"ok": False, "error": "The host declined character catalog access"}
        if name is None:
            return await client.request({"action": "characters"})
        return await client.request({"action": "create-character", "name": name,
                                     "operation_id": operation_id or uuid.uuid4().hex})
    finally:
        await client.close()


class Client:
    def __init__(self, control: Control, transport, invite: dict, bridge: Bridge, auto_delay: float = 1, *,
                 allowed_maps: frozenset[str] = PLAYTEST_MAPS):
        self.control = control
        self.transport = transport
        self.invite = invite
        self.bridge = bridge
        self.allowed_maps = allowed_maps
        self.endpoint = f"127.0.0.1:{transport.address[1]}"
        self.auto_delay = auto_delay
        self.next_retry = 0.0
        self.suspended = False
        self.host_session = None
        self.needs_travel = False
        self.last_game = {}
        self.gameplay = {}
        self.zone_epoch = None
        self.viewer_role = "guest"
        self.expected_zone = None
        self.loot_tracking = None
        self.next_snapshot = 0.0
        self.next_projection = 0.0
        self.gameplay_seen = 0.0
        self.server_transitioning = False
        self.last_reload_intent = 0
        self.stopping = False

    async def connect(self, timeout: float = 6, *, queue: bool = False) -> dict:
        request = {key: self.invite[key] for key in ("guest_id", "credential", "character_id", "game_build")}
        request["protocol_version"] = PROTOCOL
        result = await open_admission(
            self.transport, self.invite, request, timeout=timeout, queue=queue,
            on_wait=self.admission_wait if queue else None,
        )
        if not result.get("accepted") or not self.transport.game_access:
            self.next_retry = time.monotonic() + 3
            raise RuntimeError("Host declined admission")
        session = result.get("session_id")
        if not isinstance(session, str) or len(session) != 32:
            await self.transport.disconnect()
            raise RuntimeError("Host did not return a valid session ID")
        if self.host_session and self.host_session != session:
            self.needs_travel = True
            self.gameplay = {}
            self.zone_epoch = None
            self.expected_zone = None
            self.loot_tracking = None
            self.gameplay_seen = 0.0
            self.server_transitioning = False
        if result.get("zone") not in self.allowed_maps or not isinstance(result.get("zone_epoch"), str):
            await self.transport.disconnect()
            raise RuntimeError("Host did not return a supported zone")
        if self.zone_epoch and result["zone_epoch"] != self.zone_epoch:
            self.needs_travel = True
        self.zone_epoch = result["zone_epoch"]
        self.expected_zone = result["zone"]
        self.host_session = session
        self.control.event("client_link_authenticated", character_id=request["character_id"],
                           resumed=bool(result.get("resumed")))
        return result

    def admission_wait(self, reason: str) -> None:
        self.control.status(active=True, role="client", state="waiting_for_host", wait_reason=reason,
                            character_id=self.invite["character_id"])

    async def open_game_link(self) -> None:
        self.transport.reset_game_endpoint()
        self.needs_travel = True
        await self.bridge.command("join", self.endpoint)
        self.last_game = await self.bridge.wait_for(
            lambda state: state["state"] == "connected" and state["map"] == self.expected_zone, timeout=18,
        )
        self.needs_travel = False

    async def reconcile_game(self) -> None:
        state = self.bridge.snapshot()
        if state is None:
            return
        self.last_game = state
        if not self.needs_travel and state.get("state") not in {"disconnected", "ready"}:
            return
        if state.get("state") != "ready":
            await self.bridge.command("leave")
            await self.bridge.wait_for(lambda snapshot: snapshot["state"] == "ready", timeout=15)
        await self.open_game_link()

    async def recover(self) -> dict:
        result = await self.connect(timeout=4) if not self.transport.connected else {"resumed": False}
        await self.refresh_gameplay()
        if not self.server_transitioning:
            await self.reconcile_game()
        return result

    async def gameplay_action(self, action: str, operation_id: str, target: str | None,
                              retained: dict | None = None) -> dict:
        if not self.transport.connected or (action != "keep" and self.last_game.get("state") != "connected"):
            raise RuntimeError("game_connection_unavailable")
        request = {"action": action, "operation_id": operation_id, "target": target}
        if action == "keep":
            request["retained"] = retained
        reply = await self.transport.request(request, timeout=7 if action in GUEST_NATIVE_ACTIONS else 4)
        if not reply.get("ok"):
            raise ValueError(reply.get("error", "interaction_refused"))
        return {key: value for key, value in reply.items() if key != "ok"}

    async def service_reload_input(self, active: bool) -> None:
        intent = self.last_game.get("native_guest_reload_intent", {})
        sequence, pawn = intent.get("sequence"), intent.get("pawn")
        if type(sequence) is not int or not self.last_reload_intent < sequence < 2**53:
            return
        self.last_reload_intent = sequence
        if (not active or self.stopping or not self.host_session or intent.get("map") != self.last_game.get("map")
                or not pawn or self.last_game.get("pawns", {}).get("local", {}).get("name") != pawn):
            return
        operation = uuid.uuid5(uuid.NAMESPACE_OID, f"{self.host_session}:{self.zone_epoch}:{pawn}:reload:{sequence}").hex
        try:
            result = await self.gameplay_action("native-guest-weapon-reload", operation, "equipped")
            self.control.event("native_guest_reload_input", operation_id=operation, sequence=sequence, **result)
        except (ValueError, OSError, RuntimeError) as error:
            self.control.event("native_guest_reload_input_rejected", operation_id=operation, sequence=sequence, reason=str(error))

    async def refresh_gameplay(self) -> None:
        now = time.monotonic()
        if now >= self.next_snapshot:
            self.next_snapshot = now + 0.5
            try:
                reply = await self.transport.request({"action": "snapshot"}, timeout=2)
                if reply.get("ok"):
                    self.gameplay = reply["gameplay"]
                    viewer = reply.get("viewer_role")
                    if viewer in ("guest", "guest2", "guest3", "guest4"):
                        self.viewer_role = viewer
                    self.gameplay_seen = time.monotonic()
                    self.server_transitioning = bool(reply.get("travel_in_progress"))
                    epoch = reply["zone_epoch"]
                    if self.zone_epoch and epoch != self.zone_epoch:
                        self.needs_travel = True
                        self.loot_tracking = None
                        self.control.event("zone_rejoin_requested", zone_epoch=epoch)
                    self.zone_epoch = epoch
                    self.expected_zone = reply["zone"]
            except (OSError, RuntimeError) as error:
                self.control.event("gameplay_snapshot_unavailable", error_type=type(error).__name__)
        if (now >= self.next_projection and not self.needs_travel and not self.server_transitioning
                and self.last_game.get("state") == "connected"):
            try:
                loot = self.gameplay.get("loot")
                if loot and loot["map"] == self.last_game.get("map"):
                    tracking = (loot["id"], *loot["position"], loot["map"])
                    if tracking != self.loot_tracking:
                        await self.bridge.command("loot-track", loot["id"],
                                                  *(str(axis) for axis in loot["position"]), timeout=3)
                        self.loot_tracking = tracking
                await project_players(self.bridge, self.last_game, self.gameplay, client=True, viewer_role=self.viewer_role)
                await self.project_world_view()
            except (OSError, RuntimeError) as error:
                self.next_projection = time.monotonic() + 2
                self.control.event("gameplay_projection_error", reason=str(error))

    async def project_world_view(self) -> None:
        area = self.last_game.get("map")
        expected_actor = WORLD_VIEW_ACTORS.get(area)
        if time.monotonic() - self.gameplay_seen >= 2 or not expected_actor:
            return
        frame = self.gameplay.get("host_world", {}).get("native_world_view")
        if (not isinstance(frame, dict) or frame.get("map") != area or frame.get("mode") != "sample"
                or frame.get("actor") != expected_actor):
            return
        if not self.zone_epoch or type(frame.get("sequence")) is not int or not 0 < frame["sequence"] < 2**53:
            return
        prior = self.last_game.get("native_world_view", {})
        if (prior.get("map") == area and prior.get("actor") == expected_actor
                and prior.get("epoch") == self.zone_epoch and prior.get("sequence", 0) >= frame["sequence"]):
            return
        keys = ("game_seconds", "pitch", "yaw", "roll", "sun_x", "sun_y", "sun_z", "sun_intensity")
        numeric = [frame.get(key) for key in keys]
        sky = [frame.get(key) for key in ("sky_r", "sky_g", "sky_b")]
        weather = [frame.get(key) for key in WORLD_WEATHER_FIELDS]
        if (not all(type(value) in (int, float) and abs(value) < 10**12 for value in numeric)
                or (any(value is not None for value in sky) and not all(
                    type(value) in (int, float) and abs(value) <= 1.01 for value in sky))
                or (any(value is not None for value in weather) and not all(
                    type(value) in (int, float) and 0 <= value <= 100 for value in weather))
                or type(frame.get("sun_visible")) is not bool):
            return
        weather_args = tuple(str(value) for value in weather) if all(value is not None for value in weather) else ()
        snapshot = await self.bridge.command(
            "native-world-view-apply", self.zone_epoch, str(frame["sequence"]), area, expected_actor,
            *(str(value) for value in numeric[:-1]), "1" if frame["sun_visible"] else "0", str(numeric[-1]),
            *("-" if value is None else str(value) for value in sky), *weather_args, timeout=3,
        )
        proof = snapshot.get("native_world_view", {})
        if (proof.get("map") != area or proof.get("actor") != expected_actor
                or proof.get("mode") != "applied" or proof.get("epoch") != self.zone_epoch
                or proof.get("sequence") != frame["sequence"] or proof.get("save_unchanged") is not True
                or weather_args and proof.get("weather_verified") is not True):
            raise RuntimeError("The connected guest did not verify the host's rendered world frame")
        self.last_game = snapshot

    async def service_requests(self) -> None:
        for path, request in self.control.requests():
            action = request.get("action")
            try:
                if action == "drop-link":
                    self.suspended = True
                    await self.transport.disconnect()
                    result = {"link_connected": False}
                elif action == "reconnect":
                    self.suspended = False
                    data = await self.recover()
                    result = {"link_connected": True, "resumed": data.get("resumed", False),
                              "game_connected": self.last_game.get("state") == "connected"}
                elif action == "walk":
                    await self.bridge.walk(request.get("x"), request.get("y"), request.get("milliseconds"))
                    result = {"movement_scheduled": True}
                elif action == "native-transport-probe" and self.last_game.get("native_transport", {}).get("state") == "ready":
                    result = await probe_transport(self.bridge, request.get("target"))
                elif action == native_guest_npc.CLIENT_ACTION:
                    result = await native_guest_npc.client_fixture(self, action)
                elif action == "native-guest-reload-input-fixture":
                    pawn = self.last_game.get("pawns", {}).get("local", {}).get("name")
                    if not pawn or self.last_game.get("state") != "connected":
                        raise ValueError("Connect the staged reload input client first")
                    self.last_game = await self.bridge.command(action, pawn, timeout=5)
                    result = {"native_guest_reload_fixture": self.last_game.get("native_guest_reload_fixture", {})}
                elif action == "native-rest-guest-interaction-fixture":
                    pawn = self.last_game.get("pawns", {}).get("local", {}).get("name")
                    if not pawn or self.last_game.get("state") != "connected":
                        raise ValueError("Connect the enrolled stock rest guest first")
                    snapshot = await self.bridge.command(action, pawn, timeout=6)
                    result = {"native_rest_guard": snapshot.get("native_rest_guard", {})}
                elif action in ("interact", "buy", "keep", *GUEST_NATIVE_ACTIONS):
                    result = await self.gameplay_action(action, request.get("operation_id") or path.stem,
                                                        request.get("target"), request.get("retained"))
                elif action == "stop":
                    self.stopping = True
                    result = {"stopping": True}
                else:
                    raise ValueError("Action unavailable on the client")
                self.control.event("client_command", action=action, command_id=path.stem)
                self.control.respond(path, True, **result)
            except (ValueError, OSError, RuntimeError, TimeoutError) as error:
                self.control.respond(path, False, error=str(error))

    async def run(self) -> None:
        while not self.stopping:
            if self.bridge.process.poll() is not None:
                raise RuntimeError(f"Client game exited with code {self.bridge.process.returncode}")
            state = self.bridge.snapshot()
            if state is not None:
                self.last_game = state
            await self.service_requests()
            if self.transport.connected and not self.stopping:
                await self.refresh_gameplay()
            repair_needed = (not self.transport.connected or self.needs_travel
                             or self.last_game.get("state") in {"disconnected", "ready"})
            if (not self.stopping and not self.suspended and repair_needed
                    and (not self.server_transitioning or not self.transport.connected)
                    and time.monotonic() >= self.next_retry):
                try:
                    await self.recover()
                except (OSError, RuntimeError) as error:
                    self.control.event("reconnect_retry", error_type=type(error).__name__)
                    self.next_retry = time.monotonic() + self.auto_delay
            active = (self.transport.connected and self.last_game.get("state") == "connected"
                      and not self.needs_travel and not self.server_transitioning)
            await self.service_reload_input(active)
            interactions = self.bridge.interactions()
            if active and not self.stopping:
                for operation_id in interactions:
                    try:
                        result = await self.gameplay_action("interact", operation_id, None)
                        self.control.event("local_interaction", operation_id=operation_id, **result)
                    except (ValueError, OSError, RuntimeError) as error:
                        self.control.event("interaction_rejected", operation_id=operation_id, reason=str(error))
            self.control.status(active=not self.stopping, role="client",
                                state="connected" if active else "reconnecting",
                                link_connected=self.transport.connected, reconnect_suspended=self.suspended,
                                character_id=self.invite["character_id"], host_session=self.host_session,
                                game=self.last_game, gameplay=self.gameplay,
                                gameplay_fresh=active and time.monotonic() - self.gameplay_seen < 3,
                                zone_epoch=self.zone_epoch)
            await asyncio.sleep(0.25)


async def join_game(work: Path, directory: Path, invitation: Path, headless: bool,
                    character_id: str | None = None, *, native_zones: bool = False,
                    native_zone_walls: bool = False, guest_reload_input: bool = False) -> None:
    invite = load_invite(invitation, staged_build(work))
    native_zones = native_zones or invite.get("native_zones", False)
    native_zone_walls = native_zone_walls or invite.get("native_zone_walls", False)
    expanded_zones = invite.get("expanded_zones", False)
    if expanded_zones and not (native_zones and staged_script(work, "authored_zone_maps.lua")
                               and (not native_zone_walls or staged_script(work, "authored_wall_routes.lua"))):
        raise ValueError("Restage the current mod to join the host's expanded stock routes")
    if character_id is not None:
        try:
            if str(uuid.UUID(character_id)) != character_id:
                raise ValueError
        except ValueError:
            raise ValueError("Supply a character ID from the host catalog") from None
        invite["character_id"] = character_id
    with Control(directory / "run") as control:
        process = transport = None
        result = "stopped"
        try:
            control.status(active=True, role="client", state="starting")
            transport = await link.start_client()
            bridge_directory = directory / "run" / "game-bridge" / uuid.uuid4().hex
            process = launch(work, directory / "game-user", bridge_directory, headless,
                             native_zones=native_zones, native_zone_walls=native_zone_walls, expanded_zones=expanded_zones,
                             guest_reload_input=guest_reload_input)
            bridge = Bridge(bridge_directory, process)
            maps = PLAYTEST_MAPS | AUTHORED_MAPS if native_zones and qualified_authored_routes(work) else PLAYTEST_MAPS
            if expanded_zones:
                maps |= EXPANDED_MAPS
            client = Client(control, transport, invite, bridge, allowed_maps=maps)
            await bridge.wait_for(lambda state: state["state"] == "ready")
            await client.connect(queue=True)
            await client.open_game_link()
            await client.run()
        except (Exception, asyncio.CancelledError) as error:
            result = "stopped" if isinstance(error, asyncio.CancelledError) else "failed"
            control.event("client_stopping", outcome=result, error_type=type(error).__name__)
            raise
        finally:
            if transport:
                await transport.close()
            if process:
                await stop_game(process)
            control.status(active=False, role="client", state=result, character_id=invite["character_id"])
