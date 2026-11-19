"""Run the staged game and coordinate the Direct IP session."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import link, native_blue, native_dream, native_gold, native_host_save, native_quest, native_red, party, wipe
from .campaign import CampaignError, CampaignStore, DEFAULT_RECONNECT_GRACE
from .control import Control
from .native_pickup import NativePickup, STAGED_SHOPS
from .travel import AUTHORED_EXITS, AUTHORED_MAPS, CROSSROADS, EXCHANGE, GOLD_NEXUS, PLAYTEST_MAPS, travel_decision
from .runtime import (Bridge, MAP, launch, native_death_backend, qualified_authored_routes, read_json, spare_udp_port, staged_build,
                      staged_script, stop_game, verify_loopback_socket, write_json, write_text)


PROTOCOL = 2
POSITION_MAX_AGE = 3.0
GUEST_NATIVE_ACTIONS = ("native-pickup", "native-shop-buy", "native-quest-start", "native-quest-turnin")
TRANSPORT_FIXTURES = ("stardust", "green", "blue", "red", "gold")
CUBE_RESULTS = {"inspect": ("present", "absent"), "debit": ("removed", "absent"),
                "seed": ("seeded", "present"), "use-probe": ("requested",), "action-probe": ("requested",)}
STARDUST_RESULTS = {"inspect": ("present", "absent"), "seed": ("seeded", "present"),
                    "debit": ("removed", "decremented", "already_debited"),
                    "restore": ("restored", "present"), "use-probe": ("requested",)}


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


def pending_cube(state: dict) -> tuple[str, dict] | None:
    pending = [(cube, receipt) for cube, module in (("gold", native_gold), ("blue", native_blue), ("red", native_red))
               if (receipt := module.ledger(state)["pending"])]
    if len(pending) > 1:
        raise ValueError("The campaign has two pending item transports")
    return pending[0] if pending else None


def checkpoint_seed(world: dict) -> int | None:
    value = world.get("native_observation", {}).get("native_scene", {}).get("instance_seed")
    if type(value) in (int, float) and -2147483648 <= value <= 2147483647 and value == int(value):
        return int(value)
    return None


async def project_players(bridge: Bridge, game: dict, public: dict, *, client: bool) -> None:
    """Apply authority decisions to Unreal and the guest's movement prediction."""
    for role, player in public.get("players", {}).items():
        local = (role == "guest") if client else (role == "host")
        bridge_role = "local" if local else "guest"
        if bridge_role not in game.get("pawns", {}):
            continue
        expected = "downed" if player["downed"] else "standing"
        actual = game.get("player_effects", {}).get(bridge_role, {}).get("state")
        if actual != expected:
            await bridge.command("player-state", bridge_role, expected, timeout=3)


class Host:
    def __init__(self, store: CampaignStore, control: Control, game_build: str, identity,
                 bind: str, port: int, fixtures: bool, death_rules: dict | None, *,
                 native_backend: str | None, native_events: bool, native_guest: bool, native_zones: bool = False,
                 authored_zones: bool = False, native_zone_walls: bool = False, native_profile: Path | None = None):
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
        self.allowed_maps = AUTHORED_MAPS if authored_zones else PLAYTEST_MAPS
        self.next_native_heartbeat = 0.0
        self.next_zone_heartbeat = 0.0
        self.native_guest_health = None
        self.native_guest_position = None
        self.hazard_origin = None
        self.native_pickup = NativePickup()
        self.native_requests = asyncio.Queue(maxsize=8)
        self.pending_native_deaths: list[tuple[str, str, str | None]] = []
        self.party = party.load_party(store.status()["world"].get("party"))
        if pending_cube(self.party) and native_dream.visit(self.party):
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
        try:
            active = self.store.activate(request["guest_id"], request["credential"], request["character_id"])
        except CampaignError:
            return {"accepted": False, "reason": "guest_or_character_unavailable"}
        saved = active["character"]["state"]
        if after_grace:
            saved = {"health": saved.get("health")} if self.native_guest else {}
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

    def checkpoint(self, proposed_party: dict | None = None, *, native_record: dict | None = None) -> int:
        if self.last_game.get("state") != "hosting":
            raise RuntimeError("Wait for the host map to finish loading")
        world = self.store.status()["world"]
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
        guest = _pawn(self.last_game, "guest")
        if self.native_guest and self.last_game.get("native_guest_save", {}).get("pawn") != self.guest_pawn:
            guest = {}
        active = self.guest and any(
            lease["character_id"] == self.guest["character_id"] for lease in self.store.status()["leases"]
        )
        changes = {self.guest["lease_id"]: guest} if guest and active and self.guest_pawn else {}
        return self.store.checkpoint(world, changes)

    async def checkpoint_native(self, bridge: Bridge, proposed_party: dict | None = None) -> int:
        if self.native_profile is None:
            return self.checkpoint(proposed_party)
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
        self.last_game = snapshot
        self.last_seen = time.monotonic()
        revision = self.checkpoint(proposed_party, native_record=record)
        try:
            previous = read_json(self.store.previous_path)["world"].get("native_host_save", {})
            keep = {slot, previous.get("slot", "")}
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

    def native_world_state(self) -> dict:
        return {key: self.last_game[key] for key in ("native_scene", "native_rules", "native_reputation", "native_seed")
                if key in self.last_game}

    def public_gameplay(self) -> dict:
        view = party.public_state(self.party, self.roles(), test_fixtures=self.fixtures)
        view["host_world"] = self.native_world_state()
        view["native_quests"] = self.party.get("native_quests", {})
        if pending := pending_cube(self.party):
            cube, itinerary = pending
            view["pending_item_travel"] = {"item": cube, "source": itinerary["source"],
                                           "destination": itinerary["destination"]}
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
            details = _pawn(self.last_game, "local" if role == "host" else "guest")
            if details and all(axis is not None for axis in details["location"]):
                pawns[character_id] = details
        return pawns

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
        roles = self.roles()
        if refusal := self.travel_refusal(destination, roles, respawning=respawning):
            return refusal
        current = self.last_game["map"]
        if not respawning and (current in AUTHORED_MAPS or entrance is not None):
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
                          dream_plan: dict | None = None, red_plan: dict | None = None) -> dict:
        current = self.last_game["map"]
        guest = _pawn(self.last_game, "guest")
        self.control.event("zone_travel_requested", source=current, destination=destination)
        if (self.native_profile is not None or self.native_guest and guest.get("health") is not None) and not (
            gold_guid or blue_plan or dream_plan or red_plan
        ):
            await self.checkpoint_native(bridge)
        try:
            authorization = ("blue", blue_plan["guid"]) if blue_plan else (gold_guid,) if gold_guid else ()
            if red_plan:
                authorization = ("red", red_plan["guid"])
            returning = dream_plan and dream_plan["phase"] == "returning"
            if dream_plan:
                authorization = ("stardust-return",) if returning else ("stardust", dream_plan["guid"])
            await bridge.command("travel", destination, *authorization)
            observed = await bridge.wait_for(
                lambda game: game.get("state") == "hosting" and game.get("map") == destination,
                timeout=22,
            )
            await asyncio.to_thread(verify_loopback_socket, bridge.process.pid, self.internal_port)
            if dream_plan and not returning:
                await bridge.wait_for(lambda game: self.dream_isolated(game), timeout=7)
                observed = await bridge.command("native-dream-arrival", EXCHANGE, timeout=6)
                proof = observed.get("native_dream_arrival", {})
                if (not self.dream_isolated(observed) or proof.get("actor") != native_dream.ARRIVAL
                        or proof.get("destination") != EXCHANGE or None in proof.get("location", [None])):
                    raise RuntimeError("The stock Exchange arrival was not isolated and verified")
            elif returning:
                observed = await self.blue_arrival(bridge, {"source": EXCHANGE, "destination": destination,
                                                            "bed": dream_plan["return_bed"]})
            elif blue_plan:
                observed = await self.blue_arrival(bridge, blue_plan)
            elif entrance:
                observed = await bridge.command("native-exit-arrival", entrance, timeout=6)
                if observed.get("map") != destination or observed.get("state") != "hosting":
                    raise RuntimeError("The authored arrival was acknowledged in the wrong world")
            if transport is not None:
                await transport.refresh_game_routes()
            self.last_game = observed
            self.last_seen = time.monotonic()
            if dream_plan:
                await self.ensure_dream_stack(bridge, dream_plan)
            self.zone_epoch = uuid.uuid4().hex
            self.awaiting_zone_guest = roles.get("guest")
            self.guest_pawn = None
            self.marker_ready = False
            if self.guest:
                self.guest["restore"] = {"health": guest.get("health")} if self.native_guest else {}
            candidate = native_gold.arrive(self.party, gold_guid, observed["map"]) if gold_guid else None
            if blue_plan:
                candidate = native_blue.arrive(self.party, blue_plan["guid"], observed["map"])
            if red_plan:
                candidate = native_red.arrive(self.party, red_plan["guid"], observed["map"])
            if dream_plan:
                candidate = native_dream.finished(self.party) if returning else native_dream.arrived(self.party, time.time())
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

    async def checked_cube(self, bridge: Bridge, cube: str, operation: str, guid: str) -> tuple[dict, dict]:
        game = self.last_game
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        allowed = (CROSSROADS, GOLD_NEXUS) if cube == "gold" else AUTHORED_MAPS
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
                or request.source != CROSSROADS or self.last_game.get("map") != request.source
                or self.last_game.get("state") != "hosting" or request.entry not in native_gold.INVENTORY_ENTRIES
                or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "gold_scope_unavailable"}
        history = native_gold.ledger(self.party)
        if request.guid in history["spent"]:
            return {"ok": False, "error": "gold_already_spent"}
        if pending_cube(self.party):
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
            candidate = native_gold.begin(self.party, request.guid)
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
        if (not self.native_zones or self.stopping or self.transitioning or request.source not in AUTHORED_MAPS
                or self.last_game.get("map") != request.source or self.last_game.get("state") != "hosting"
                or request.entry not in native_gold.INVENTORY_ENTRIES or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "blue_scope_unavailable"}
        if request.guid in native_blue.ledger(self.party)["spent"]:
            return {"ok": False, "error": "blue_already_spent"}
        if pending_cube(self.party):
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
        if (not self.native_zones or self.stopping or self.transitioning or request.source not in native_red.SOURCES
                or self.last_game.get("map") != request.source or self.last_game.get("state") != "hosting"
                or request.entry not in native_gold.INVENTORY_ENTRIES or not native_gold.valid_guid(request.guid)
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "red_scope_unavailable"}
        if request.guid in native_red.ledger(self.party)["spent"]:
            return {"ok": False, "error": "red_already_spent"}
        if pending_cube(self.party) or native_dream.visit(self.party):
            return {"ok": False, "error": "item_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        self.transitioning = True
        try:
            snapshot, item = await self.checked_cube(bridge, "red", "inspect", request.guid)
            if item["state"] != "present":
                return {"ok": False, "error": "red_item_unavailable"}
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            # Check the full roster before making the one stock-weighted draw.
            if refusal := self.travel_refusal(request.source, self.roles(), staying=True):
                return refusal
            candidate = native_red.begin(self.party, request.guid, request.source, native_red.draw())
            await self.checkpoint_native(bridge, candidate)
            self.party = candidate
            return await self.resume_red(bridge, transport, recovery=False)
        finally:
            self.transitioning = False

    async def resume_red(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        pending = native_red.ledger(self.party)["pending"]
        try:
            if (not pending or not self.native_zones or self.last_game.get("map") != pending["source"]
                    or pending["destination"] not in self.allowed_maps):
                raise RuntimeError("The committed Red Cube draw is out of scope")
            guid, destination = pending["guid"], pending["destination"]
            snapshot, item = await self.checked_cube(bridge, "red", "debit", guid)
            if item["state"] != "removed" and not recovery:
                raise RuntimeError("The committed Red Cube could not be removed from the active inventory")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            roles = self.roles()
            if destination != pending["source"]:
                result = await self.switch_zone(bridge, transport, destination, native_red.ARRIVAL, roles,
                                                red_plan=pending)
            else:
                arrival = await bridge.command("native-red-arrival", destination, guid, timeout=6)
                if arrival.get("state") != "hosting" or arrival.get("map") != destination:
                    raise RuntimeError("The same-map Red Cube arrival belongs to the wrong world")
                await self.place_party_in_zone(bridge, roles, arrival)
                candidate = native_red.arrive(self.party, guid, destination)
                revision = await self.checkpoint_native(bridge, candidate)
                self.party = candidate
                result = {"ok": True, "map": destination, "entrance": native_red.ARRIVAL, "revision": revision,
                          "travel_epoch": self.zone_epoch, "in_zone": True}
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event("red_travel_recovered" if recovery else "red_travel_completed", guid=guid,
                               debit=item["state"], **result)
        return result

    @staticmethod
    def dream_isolated(game: dict) -> bool:
        guard = game.get("native_dream_isolation", {})
        return (game.get("state") == "hosting" and game.get("map") == EXCHANGE and guard.get("map") == EXCHANGE
                and guard.get("state") == "ready" and guard.get("actor") == "Dream_CountDown_BP_C_1")

    async def stardust_operation(self, bridge: Bridge, operation: str, guid: str, *arguments: str) -> tuple[dict, dict]:
        game = self.last_game
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        if (not native_gold.valid_guid(guid) or not pawn or game.get("state") != "hosting"
                or area not in AUTHORED_MAPS or time.monotonic() - self.last_seen > POSITION_MAX_AGE):
            raise ValueError("Stardust requires the active host pawn and an exact stack GUID")
        write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
        snapshot = await bridge.command(f"native-stardust-{operation}", pawn, guid, *arguments, timeout=4)
        row = snapshot.get("native_stardust", {})
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
        guid, remaining = trip["guid"], trip["before"] - 1
        _, item = await self.stardust_operation(bridge, "inspect", guid)
        if item["quantity"] == remaining:
            if (remaining == 0 and item["state"] == "absent") or (
                remaining and item["state"] == "present" and item["payload"] == trip["payload"]
            ):
                return
        if item["state"] != "absent" or not remaining or trip["payload"] != native_dream.STOCK_PAYLOAD:
            raise RuntimeError("The native Stardust remainder conflicts with the committed quantity")
        _, restored = await self.stardust_operation(bridge, "restore", guid, str(remaining), trip["payload"])
        if restored["quantity"] != remaining:
            raise RuntimeError("The native Stardust remainder could not be restored exactly")

    async def activate_stardust(self, bridge: Bridge, transport, request: native_gold.GoldRequest) -> dict:
        if (not self.native_zones or self.stopping or self.transitioning or EXCHANGE not in self.allowed_maps
                or request.source != CROSSROADS or self.last_game.get("map") != CROSSROADS
                or self.last_game.get("state") != "hosting" or request.entry not in native_gold.INVENTORY_ENTRIES
                or self.last_game.get("pawns", {}).get("local", {}).get("name") != request.pawn):
            return {"ok": False, "error": "stardust_scope_unavailable"}
        if pending_cube(self.party) or native_dream.visit(self.party):
            return {"ok": False, "error": "item_travel_pending"}
        if self.party["players"]["host"]["downed"]:
            return {"ok": False, "error": "actor_downed"}
        if refusal := self.travel_refusal(EXCHANGE, self.roles()):
            return refusal
        self.transitioning = True
        try:
            _, item = await self.stardust_operation(bridge, "inspect", request.guid)
            if item["state"] != "present" or not item["quantity"]:
                return {"ok": False, "error": "stardust_stack_unavailable"}
            if item["quantity"] > 1 and item["payload"] != native_dream.STOCK_PAYLOAD:
                return {"ok": False, "error": "unqualified_stardust_stack"}
            snapshot = await bridge.command("native-dream-route", request.pawn, timeout=5)
            route = snapshot.get("native_dream_route", {})
            if (snapshot.get("state") != "hosting" or snapshot.get("map") != CROSSROADS
                    or route.get("source") != CROSSROADS or route.get("destination") != EXCHANGE
                    or route.get("pawn") != request.pawn or route.get("arrival") != native_dream.ARRIVAL
                    or route.get("visit_seconds", 0) + route.get("startup_seconds", 0) != native_dream.VISIT_SECONDS
                    or route.get("fade_seconds") != native_dream.RETURN_FADE_SECONDS
                    or not native_blue.valid_rest(CROSSROADS, route.get("return_map"), route.get("return_bed"))):
                return {"ok": False, "error": "stardust_route_unavailable"}
            self.last_game = snapshot
            self.last_seen = time.monotonic()
            if refusal := self.travel_refusal(EXCHANGE, self.roles()):
                return refusal
            proposed = native_dream.begin(self.party, request.guid, item["quantity"], item["payload"],
                                          route["return_map"], route["return_bed"])
            await self.checkpoint_native(bridge, proposed)
            self.party = proposed
            return await self.depart_dream(bridge, transport, recovery=False)
        finally:
            self.transitioning = False

    async def depart_dream(self, bridge: Bridge, transport, *, recovery: bool) -> dict:
        trip = native_dream.visit(self.party)
        try:
            if not trip or trip["phase"] != "departing" or self.last_game.get("map") != CROSSROADS:
                raise RuntimeError("The committed Stardust departure is out of scope")
            if recovery and trip["before"] > 1:
                _, item = await self.stardust_operation(bridge, "inspect", trip["guid"])
                if item["state"] == "absent":
                    await self.ensure_dream_stack(bridge, trip)
            _, debit = await self.stardust_operation(bridge, "debit", trip["guid"], str(trip["before"]))
            if debit["quantity"] != trip["before"] - 1 or (not recovery and debit["state"] == "already_debited"):
                raise RuntimeError("The committed Stardust use did not remove exactly one unit")
            result = await self.switch_zone(bridge, transport, EXCHANGE, native_dream.ARRIVAL, self.roles(),
                                            dream_plan=trip)
        except (OSError, RuntimeError, ValueError):
            self.stopping = True
            raise
        with contextlib.suppress(OSError):
            self.control.event("stardust_visit_recovered" if recovery else "stardust_visit_started", id=trip["id"],
                               guid=trip["guid"], remaining=debit["quantity"], **result)
        return result

    async def advance_dream(self, bridge: Bridge, transport, *, wiped: bool = False) -> None:
        trip = native_dream.visit(self.party)
        if not trip or trip["phase"] == "departing" or not self.dream_isolated(self.last_game):
            return
        now = time.time()
        if self.last_game["native_dream_isolation"]["early_requested"]:
            candidate = native_dream.accelerate(self.party, now)
            if candidate is not self.party:
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
        if not self.guest or self.guest["guest_id"] != guest_id or self.guest["disconnected"] is not None:
            return {"ok": False, "error": "guest_not_active"}
        if request == {"action": "snapshot"}:
            return {"ok": True, "gameplay": self.public_gameplay(), "zone_epoch": self.zone_epoch,
                    "zone": self.last_game["map"], "travel_in_progress": self.transitioning}
        action = request.get("action")
        if action in GUEST_NATIVE_ACTIONS and set(request) == {"action", "operation_id", "target"}:
            return self.enqueue_native_request(guest_id, request)
        if action not in ("interact", "buy", "keep") or set(request) - {"action", "operation_id", "target", "retained"}:
            return {"ok": False, "error": "action_unavailable"}
        try:
            result = self.act(self.guest["character_id"], request.get("operation_id"),
                              action, request.get("target"), retained=request.get("retained"))
        except party.RuleError as error:
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
            if not self.guest or self.guest["guest_id"] != guest_id or self.guest["disconnected"] is not None:
                raise party.RuleError("guest_not_active")
            character = self.guest["character_id"]
            action, target = request["action"], request["target"]
            if action == "native-pickup":
                result = await self.native_pickup.claim(self, bridge, character, request["operation_id"], target)
            elif action == "native-shop-buy":
                result = await self.native_pickup.buy(self, bridge, character, request["operation_id"], target)
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
        result = wipe.finish_wipe(draft, now)
        if result is None:
            return
        checkpoint = result["checkpoint"]
        if self.last_game.get("map") == EXCHANGE and native_dream.visit(self.party):
            await self.advance_dream(bridge, transport, wiped=True)
            if native_dream.visit(self.party):
                return
            checkpoint = _pawn(self.last_game, "local")
        elif checkpoint["map"] != self.last_game.get("map"):
            outcome = await self.travel(bridge, transport, checkpoint["map"], respawning=True)
            if not outcome["ok"]:
                raise RuntimeError("Rest checkpoint is unavailable")
        candidate = party.load_party(self.party)
        candidate["players"].update(result["players"])
        candidate["backpacks"].update(result["backpacks"])
        x, y, z = checkpoint["location"]
        for offset, actor in enumerate(("host", *[name for name in draft["roster"] if name != "host"])):
            candidate["pending_spawns"][actor] = {"map": checkpoint["map"], "location": [x + 110 * offset, y, z]}
        candidate["wipe"] = None
        revision = self.checkpoint(candidate)
        self.party = candidate
        self.control.event("party_respawn_committed", wipe_id=result["wipe_id"], revision=revision,
                           backpack_ids=list(result["backpacks"]))

    async def apply_pending_spawns(self, bridge: Bridge) -> None:
        for role, actor in self.roles().items():
            spot = self.party["pending_spawns"].get(actor)
            name = "local" if role == "host" else "guest"
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
                    spot = dict(spot, health=body["max_health"])
                state = await bridge.restore(name, spot)
            except (OSError, RuntimeError) as error:
                self.control.event("respawn_placement_pending", actor=actor, reason=str(error))
                continue
            if state:
                self.last_game = state
                self.last_seen = time.monotonic()
            candidate = party.load_party(self.party)
            candidate["pending_spawns"].pop(actor, None)
            self.checkpoint(candidate)
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
        })
        self.control.event("invite_created", guest_id=guest["guest_id"], character_id=guest["character_id"])
        return {"invite_file": str(file), "guest_id": guest["guest_id"], "character_id": guest["character_id"]}

    async def place_new_guest(self, bridge: Bridge, pawn: dict) -> dict | None:
        host = _pawn(self.last_game, "local")
        if not host or any(axis is None for axis in host["location"] + pawn["location"]):
            return None
        point = host["location"]
        if math.dist(point, pawn["location"]) <= party.INTERACT_RANGE:
            return None
        x, y, z = point
        for dx, dy in ((110, 0), (-110, 0), (0, 110), (0, -110)):
            try:
                return await bridge.restore("guest", {"location": [x + dx, y + dy, z]})
            except (RuntimeError, TimeoutError):
                continue
        self.control.event("safe_spawn_unavailable", character_id=self.guest["character_id"])

    async def observe(self, bridge: Bridge, transport) -> None:
        state = bridge.snapshot()
        if state is not None:
            if state.get("sequence") != self.last_game.get("sequence"):
                self.last_seen = time.monotonic()
            self.last_game = state
        party.add_player(self.party, "host")
        pawn = self.last_game.get("pawns", {}).get("guest")
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
            snapshot = await bridge.command("native-guest-bind", pawn["name"], health, timeout=4)
            if snapshot.get("native_guest_save", {}).get("pawn") != pawn["name"]:
                raise RuntimeError("The guest's independent native save could not be verified")
            self.last_game = snapshot
            self.last_seen = time.monotonic()
        if pawn and self.guest and pawn["name"] != self.guest_pawn:
            saved = self.guest["restore"]
            if saved.get("map") == self.last_game["map"]:
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
                elif action in ("native-quest-start", "native-quest-objective", "native-quest-turnin"):
                    result = await native_quest.action(self, bridge, "host", action, request.get("target"))
                elif (action == "native-inventory-probe" and self.native_backend == "cpp"
                      and self.fixtures and not self.native_guest):
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_inventory_canary": snapshot.get("native_inventory_canary")}
                elif (action in ("native-death-probe", "native-damage-probe", "native-guest-damage", "native-host-damage")
                      and self.native_backend and self.fixtures):
                    if (action in ("native-guest-damage", "native-host-damage")) != self.native_guest:
                        raise ValueError("Use the damage probe for this host's native ownership mode")
                    target = request.get("target")
                    if target not in ("host", "guest"):
                        raise ValueError("The native death probe needs --target host or guest")
                    snapshot = await bridge.command(action, "local" if target == "host" else "guest", timeout=4)
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
                    if target not in ("host", "guest"):
                        raise ValueError("Specify the host or guest for the exit fixture")
                    if entrance is not None and not any(
                        source == self.last_game.get("map") and tag == entrance
                        for (source, _), tag in AUTHORED_EXITS.items()
                    ):
                        raise ValueError("Choose an authored exit entrance tag in the host's current map")
                    arguments = [action, "local" if target == "host" else "guest"]
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
                    if target not in ("host", "guest"):
                        raise ValueError("Specify the host or guest for the door fixture")
                    arguments = [action, "local" if target == "host" else "guest"]
                    if action == "native-door-position":
                        position = request.get("exit_position")
                        if position not in ("near", "far"):
                            raise ValueError("Specify a near or far door fixture position")
                        arguments.append(position)
                    snapshot = await bridge.command(*arguments, timeout=4)
                    result = {"native_zone": snapshot.get("native_zone"), "native_door": snapshot.get("native_door")}
                elif action == "native-transport-probe" and self.native_zones and self.fixtures:
                    result = await probe_transport(bridge, request.get("target"))
                elif (action in ("native-gold-seed", "native-gold-inspect", "native-gold-use-probe",
                                 "native-gold-action-probe", "native-blue-seed", "native-blue-inspect",
                                 "native-blue-use-probe", "native-red-seed", "native-red-inspect",
                                 "native-red-use-probe")
                      and self.native_zones and self.fixtures and GOLD_NEXUS in self.allowed_maps):
                    _, cube, operation = action.split("-", 2)
                    _, item = await self.checked_cube(bridge, cube, operation, request.get("target"))
                    result = {f"native_{cube}": item}
                elif action == "native-blue-rest-fixture" and self.native_zones and self.fixtures:
                    _, route = await self.blue_route(bridge, action)
                    result = {"native_blue_route": route}
                elif (action in ("native-stardust-seed", "native-stardust-inspect", "native-stardust-use-probe")
                      and self.native_zones and self.fixtures):
                    operation = action.removeprefix("native-stardust-")
                    arguments = ()
                    if operation == "seed":
                        amount = request.get("amount")
                        if type(amount) is not int or not 1 <= amount <= native_dream.MAX_STACK:
                            raise ValueError("Supply a positive Stardust stack quantity in --amount")
                        arguments = (str(amount),)
                    _, item = await self.stardust_operation(bridge, operation, request.get("target"), *arguments)
                    result = {"native_stardust": item}
                elif action == "native-dream-power-fixture" and self.native_zones and self.fixtures:
                    if not self.dream_isolated(self.last_game) or not native_dream.visit(self.party):
                        raise ValueError("The stock power fixture requires an active isolated Exchange visit")
                    write_text(bridge.directory / "native-zone-heartbeat", f"{int(time.time())}\n")
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_dream_isolation": snapshot.get("native_dream_isolation")}
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
        for cube, activate in (("gold", self.activate_gold), ("blue", self.activate_blue),
                               ("red", self.activate_red), ("stardust", self.activate_stardust)):
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

    async def run(self, bridge: Bridge, transport) -> None:
        while not self.stopping:
            if bridge.process.poll() is not None:
                raise RuntimeError(f"Host game exited with code {bridge.process.returncode}")
            await self.observe(bridge, transport)
            now = time.monotonic()
            if (self.native_events and self.last_game.get("state") == "hosting"
                    and now >= self.next_native_heartbeat and now - self.last_seen < 1
                    and (not self.native_guest or
                         (self.roles().get("guest") and self.last_game.get("native_guest_save", {}).get("pawn")
                          == self.guest_pawn and self.guest_pawn))):
                write_text(bridge.directory / "native-events-heartbeat", f"{int(time.time())}\n")
                self.next_native_heartbeat = now + 0.6
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
                    self.act("host", operation_id, "interact", None)
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
                                link_connected=bool(self.guest and transport.connected(self.guest["guest_id"])))
            await asyncio.sleep(0.25)


async def host_game(work: Path, directory: Path, bind: str, port: int, headless: bool,
                    fixtures: bool = False, death_rules: dict | None = None, *,
                    native_events: bool = False, native_guest: bool = False, native_zones: bool = False,
                    native_zone_walls: bool = False, start_zone: str | None = None) -> None:
    if start_zone in (GOLD_NEXUS, EXCHANGE):
        raise ValueError("Enter this destination through its required host transport item")
    game_build = staged_build(work)
    native_backend = native_death_backend(work)
    if native_events and native_backend != "cpp":
        raise ValueError("--native-events requires the staged C++ lab")
    if native_guest and not (native_events and fixtures):
        raise ValueError("--native-guest requires --fixtures and --native-events")
    profile = directory / "game-user"
    with Control(directory / "run") as control, CampaignStore(
        directory / "save", verify_world=lambda world: native_host_save.verify(profile, world),
    ) as store:
        old_world = store.status()["world"]
        if start_zone and old_world.get("map") not in (None, start_zone):
            raise ValueError("An existing campaign resumes in its saved zone")
        entry_map = old_world.get("map", start_zone or MAP)
        authored = entry_map in AUTHORED_MAPS
        if authored and not (native_zones and qualified_authored_routes(work)):
            raise ValueError("Authored co-op routes require the qualified C++ stage and --native-zones")
        if not authored and entry_map not in PLAYTEST_MAPS:
            raise ValueError("The campaign is saved in an unqualified zone")
        native_profile = profile if authored and staged_script(work, "native_host_save.lua") else None
        native_record = old_world.get("native_host_save")
        if native_record and native_profile is None:
            raise ValueError("Restage the current mod to restore this campaign's native host save")
        saved_party = party.load_party(old_world.get("party"))
        pending = pending_cube(saved_party)
        dream = native_dream.visit(saved_party)
        if pending and (not authored or entry_map != pending[1]["source"]):
            raise ValueError("The campaign's committed item travel does not match its saved zone")
        if ((dream or entry_map == EXCHANGE) and (
            pending or not dream or not authored
            or entry_map != (CROSSROADS if dream["phase"] == "departing" else EXCHANGE)
        )):
            raise ValueError("The campaign's Stardust visit does not match its saved zone")
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
                             native_zone_walls=native_zone_walls, native_fixtures=fixtures)
            bridge = Bridge(bridge_directory, process)
            await bridge.wait_for(lambda state: state["state"] == "ready")
            seed = checkpoint_seed(old_world) if staged_script(work, "world_seed.lua") else None
            arguments = (str(seed),) if seed is not None else ()
            if native_record:
                arguments = ("native-save", native_record["slot"])
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
                        or receipt["pawn"] != initial_game.get("pawns", {}).get("local", {}).get("name")):
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
            controller = Host(store, control, game_build, identity, bind, port, fixtures, death_rules,
                              native_backend=native_backend, native_events=native_events, native_guest=native_guest,
                              native_zones=native_zones, authored_zones=authored, native_zone_walls=native_zone_walls,
                              native_profile=native_profile)
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
                resume = {"gold": controller.resume_gold, "blue": controller.resume_blue, "red": controller.resume_red}[pending[0]]
                await resume(bridge, None, recovery=True)
            if dream and dream["phase"] == "departing":
                await controller.depart_dream(bridge, None, recovery=True)
            elif dream:
                controller.last_game = await bridge.wait_for(controller.dream_isolated, timeout=7)
                controller.last_seen = time.monotonic()
                await controller.ensure_dream_stack(bridge, dream)
                await controller.advance_dream(bridge, None)
            transport = await link.start_host(bind, port, "127.0.0.1", internal_port, identity,
                                              controller.admit, on_disconnect=controller.disconnected,
                                              on_request=controller.guest_request)
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
                if result == "stopped" and controller.last_game.get("state") == "hosting" and process.poll() is None:
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
        raise ValueError("Choose Stardust or a green, blue, red, or gold cube")
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
    if any(type(invite[key]) is not bool for key in ("native_zones", "native_zone_walls") if key in invite):
        raise ValueError("Invitation has invalid native travel settings")
    return invite


async def open_admission(transport, invite: dict, request: dict, *, timeout: float,
                         queue: bool = False, on_wait=None) -> dict:
    """Retry only bounded, authenticated waiting states reported by the host."""
    windows = {"guest_already_connected": 2.0}
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
        self.expected_zone = None
        self.loot_tracking = None
        self.next_snapshot = 0.0
        self.next_projection = 0.0
        self.gameplay_seen = 0.0
        self.server_transitioning = False
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

    async def refresh_gameplay(self) -> None:
        now = time.monotonic()
        if now >= self.next_snapshot:
            self.next_snapshot = now + 0.5
            try:
                reply = await self.transport.request({"action": "snapshot"}, timeout=2)
                if reply.get("ok"):
                    self.gameplay = reply["gameplay"]
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
                await project_players(self.bridge, self.last_game, self.gameplay, client=True)
            except (OSError, RuntimeError) as error:
                self.next_projection = time.monotonic() + 2
                self.control.event("gameplay_projection_error", reason=str(error))

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
                    native_zone_walls: bool = False) -> None:
    invite = load_invite(invitation, staged_build(work))
    native_zones = native_zones or invite.get("native_zones", False)
    native_zone_walls = native_zone_walls or invite.get("native_zone_walls", False)
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
                             native_zones=native_zones, native_zone_walls=native_zone_walls)
            bridge = Bridge(bridge_directory, process)
            maps = PLAYTEST_MAPS | AUTHORED_MAPS if native_zones and qualified_authored_routes(work) else PLAYTEST_MAPS
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
