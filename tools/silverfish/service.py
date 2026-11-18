"""Run the staged game and coordinate the Direct IP session."""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import link, party, wipe
from .campaign import CampaignError, CampaignStore, DEFAULT_RECONNECT_GRACE
from .control import Control
from .travel import PLAYTEST_MAPS, travel_decision
from .runtime import (Bridge, MAP, has_native_death_patch, launch, read_json, spare_udp_port, staged_build,
                      stop_game, verify_loopback_socket, write_json)


PROTOCOL = 2
POSITION_MAX_AGE = 3.0


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
    return {"map": state["map"], "location": pawn["location"], "health": pawn["health"]}


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
                 bind: str, port: int, fixtures: bool, death_rules: dict | None, *, native_death: bool):
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
        self.native_death = native_death
        self.party = party.load_party(store.status()["world"].get("party"))
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
        self.guest = {
            "guest_id": request["guest_id"], "lease_id": active["lease_id"],
            "character_id": active["character"]["character_id"], "disconnected": None,
            "restore": {} if after_grace else active["character"]["state"],
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
        try:
            self.checkpoint()
        except (OSError, RuntimeError) as error:
            with contextlib.suppress(OSError):
                self.control.event("disconnect_checkpoint_error", error_type=type(error).__name__)
        with contextlib.suppress(OSError):
            self.control.event("guest_link_lost", guest_id=guest_id, grace_seconds=DEFAULT_RECONNECT_GRACE)

    def checkpoint(self, proposed_party: dict | None = None) -> int:
        if self.last_game.get("state") != "hosting":
            raise RuntimeError("Wait for the host map to finish loading")
        world = self.store.status()["world"]
        world.update({
            "map": self.last_game["map"], "host": _pawn(self.last_game, "local"),
            "party": self.party if proposed_party is None else proposed_party,
        })
        native = self.native_world_state()
        if native:
            world["native_observation"] = native
        guest = _pawn(self.last_game, "guest")
        active = self.guest and any(
            lease["character_id"] == self.guest["character_id"] for lease in self.store.status()["leases"]
        )
        changes = {self.guest["lease_id"]: guest} if guest and active and self.guest_pawn else {}
        return self.store.checkpoint(world, changes)

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
        return {key: self.last_game[key] for key in ("native_scene", "native_rules", "native_reputation")
                if key in self.last_game}

    def public_gameplay(self) -> dict:
        view = party.public_state(self.party, self.roles(), test_fixtures=self.fixtures)
        view["host_world"] = self.native_world_state()
        return view

    def act(self, actor: str, operation_id: str, action: str, target: str | None,
            amount: int | None = None, retained: dict | None = None) -> dict:
        if self.stopping or self.transitioning or time.monotonic() - self.last_seen > POSITION_MAX_AGE:
            raise party.RuleError("game_snapshot_unavailable")
        roles = self.roles()
        pawns = self.authoritative_pawns(roles)
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

    async def travel(self, bridge: Bridge, transport, destination: str, *, respawning: bool = False) -> dict:
        if self.party["wipe"] and not respawning:
            return {"ok": False, "error": "wipe_in_progress"}
        roles = self.roles()
        if not respawning and any(actor in self.party["pending_spawns"] for actor in roles.values()):
            return {"ok": False, "error": "respawn_in_progress"}
        current = self.last_game.get("map", "")
        gate = travel_decision("host", current, destination, ("host",) if respawning else tuple(roles.values()),
                               self.authoritative_pawns(roles))
        if not gate["allowed"]:
            return {"ok": False, "error": "travel_blocked", "blockers": gate["blockers"]}
        self.transitioning = True
        self.control.event("zone_travel_requested", source=current, destination=destination)
        try:
            await bridge.command("travel", destination)
            try:
                observed = await bridge.wait_for(
                    lambda game: game.get("state") == "hosting" and game.get("map") == destination,
                    timeout=22,
                )
                await asyncio.to_thread(verify_loopback_socket, bridge.process.pid, self.internal_port)
                await transport.refresh_game_routes()
            except (OSError, RuntimeError):
                self.stopping = True
                raise
            self.last_game = observed
            self.last_seen = time.monotonic()
            self.zone_epoch = uuid.uuid4().hex
            self.awaiting_zone_guest = roles.get("guest")
            self.guest_pawn = None
            self.marker_ready = False
            if self.guest:
                self.guest["restore"] = {}
            revision = self.checkpoint()
            self.control.event("zone_travel_ready", source=current, destination=destination,
                               epoch=self.zone_epoch, revision=revision)
            return {"ok": True, "map": destination, "travel_epoch": self.zone_epoch, "revision": revision}
        finally:
            self.transitioning = False

    def guest_request(self, guest_id: str, request: dict) -> dict:
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
        if action not in ("interact", "buy", "keep") or set(request) - {"action", "operation_id", "target", "retained"}:
            return {"ok": False, "error": "action_unavailable"}
        try:
            result = self.act(self.guest["character_id"], request.get("operation_id"),
                              action, request.get("target"), retained=request.get("retained"))
        except party.RuleError as error:
            return {"ok": False, "error": str(error)}
        return {"ok": True, **result}

    async def project_gameplay(self, bridge: Bridge) -> None:
        now = time.monotonic()
        if self.last_game.get("state") != "hosting" or now < self.next_projection:
            return
        try:
            await project_players(bridge, self.last_game, self.public_gameplay(), client=False)
            if not self.fixtures:
                return
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
            money = self.party["economy"]["wallet"]
            if (self.party["economy"]["quest_completed"] and self.last_game.get("native_wallet") != money):
                await bridge.command("wallet-set", str(money), timeout=4)
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
        if checkpoint["map"] != self.last_game.get("map"):
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
        if pawn and self.guest and pawn["name"] != self.guest_pawn:
            saved = self.guest["restore"]
            if saved.get("map") == self.last_game["map"]:
                restored = None
                try:
                    restored = await bridge.restore("guest", saved)
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

    async def service_requests(self, bridge: Bridge, transport) -> None:
        for path, request in self.control.requests():
            action = request.get("action")
            try:
                if action == "invite":
                    result = self.invite(request)
                elif action in ("native-death-probe", "native-damage-probe") and self.native_death and self.fixtures:
                    target = request.get("target")
                    if target not in ("host", "guest"):
                        raise ValueError("The native death probe needs --target host or guest")
                    snapshot = await bridge.command(action, "local" if target == "host" else "guest", timeout=4)
                    result = {"native_death_hook": snapshot.get("native_death_hook"),
                              "native_damage_canary": snapshot.get("native_damage_canary")}
                elif action == "quest-probe" and self.fixtures:
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_quest": snapshot.get("native_quest")}
                elif action in ("vitals-probe", "vitals-reset") and self.fixtures:
                    snapshot = await bridge.command(action, timeout=4)
                    result = {"native_vitals_probe": snapshot.get("native_vitals_probe")}
                elif action == "travel":
                    result = await self.travel(bridge, transport, request.get("target"))
                    success = result.pop("ok")
                    self.control.respond(path, success, **result)
                    continue
                elif action == "walk":
                    await bridge.walk(request.get("x"), request.get("y"), request.get("milliseconds"))
                    result = {"movement_scheduled": True}
                elif action in ("interact", "buy", "test-damage", "test-quest-reward", "rest", "keep"):
                    result = self.act("host", request.get("operation_id") or path.stem,
                                      action, request.get("target"), request.get("amount"),
                                      retained=request.get("retained"))
                elif action == "checkpoint" or action == "stop":
                    result = {"revision": self.checkpoint()}
                    self.control.event("checkpoint_saved", revision=result["revision"], command_id=path.stem)
                    if action == "stop":
                        self.stopping = True
                else:
                    raise ValueError("Action unavailable on the host")
                self.control.respond(path, True, **result)
            except (ValueError, OSError, CampaignError, RuntimeError) as error:
                self.control.respond(path, False, error=str(error))

    async def run(self, bridge: Bridge, transport) -> None:
        while not self.stopping:
            if bridge.process.poll() is not None:
                raise RuntimeError(f"Host game exited with code {bridge.process.returncode}")
            await self.observe(bridge, transport)
            if self.native_death:
                for slot, operation_id in bridge.native_deaths():
                    target = self.roles().get("host" if slot == "local" else "guest")
                    try:
                        self.act("host", operation_id, "native-down", target, 1000)
                    except (ValueError, OSError, RuntimeError) as error:
                        self.control.event("native_death_rejected", operation_id=operation_id, reason=str(error))
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
                self.checkpoint()
            self.control.status(active=not self.stopping, role="host", state="hosting", game=self.last_game,
                                endpoint={"address": self.bind, "port": self.port},
                                campaign=self.store.status(), gameplay=self.public_gameplay(),
                                zone_epoch=self.zone_epoch, awaiting_zone_guest=self.awaiting_zone_guest,
                                link_connected=bool(self.guest and transport.connected(self.guest["guest_id"])))
            await asyncio.sleep(0.25)


async def host_game(work: Path, directory: Path, bind: str, port: int, headless: bool,
                    fixtures: bool = False, death_rules: dict | None = None) -> None:
    game_build = staged_build(work)
    with Control(directory / "run") as control, CampaignStore(directory / "save") as store:
        process = transport = None
        controller = None
        result = "stopped"
        try:
            control.status(active=True, role="host", state="starting")
            identity = _identity(directory / "save")
            internal_port = spare_udp_port()
            bridge_directory = directory / "run" / "game-bridge" / uuid.uuid4().hex
            process = launch(work, directory / "game-user", bridge_directory, headless, internal_port)
            bridge = Bridge(bridge_directory, process)
            await bridge.wait_for(lambda state: state["state"] == "ready")
            old_world = store.status()["world"]
            entry_map = old_world.get("map", MAP)
            if entry_map not in PLAYTEST_MAPS:
                raise ValueError("The campaign is saved in an unsupported test zone")
            await bridge.command("host", entry_map)
            initial_game = await bridge.wait_for(lambda state: state["state"] == "hosting")
            await asyncio.to_thread(verify_loopback_socket, process.pid, internal_port)
            saved = old_world.get("host", {})
            if saved.get("map") == entry_map:
                try:
                    await bridge.restore("local", saved)
                except (RuntimeError, TimeoutError):
                    control.event("host_restore_partial")
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        await bridge.restore("local", dict(saved, health=None))
            controller = Host(store, control, game_build, identity, bind, port, fixtures, death_rules,
                              native_death=has_native_death_patch(work))
            controller.internal_port = internal_port
            controller.last_game = bridge.snapshot() or initial_game
            controller.last_seen = time.monotonic()
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
                if result == "stopped" and controller.last_game.get("state") == "hosting":
                    with contextlib.suppress(CampaignError, OSError, RuntimeError):
                        controller.checkpoint()
            if transport:
                await transport.close()
            if process:
                await stop_game(process)
            control.status(active=False, role="host", state=result, campaign=store.status())


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
    def __init__(self, control: Control, transport, invite: dict, bridge: Bridge, auto_delay: float = 1):
        self.control = control
        self.transport = transport
        self.invite = invite
        self.bridge = bridge
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
        if result.get("zone") not in PLAYTEST_MAPS or not isinstance(result.get("zone_epoch"), str):
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
        reply = await self.transport.request(request, timeout=4)
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
                elif action in ("interact", "buy", "keep"):
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
                    character_id: str | None = None) -> None:
    invite = load_invite(invitation, staged_build(work))
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
            process = launch(work, directory / "game-user", bridge_directory, headless)
            bridge = Bridge(bridge_directory, process)
            client = Client(control, transport, invite, bridge)
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
