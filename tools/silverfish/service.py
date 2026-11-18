"""Run the staged game and coordinate the Direct IP session."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from . import link
from .campaign import CampaignError, CampaignStore, DEFAULT_RECONNECT_GRACE
from .control import Control
from .runtime import (Bridge, MAP, launch, read_json, spare_udp_port, staged_build,
                      stop_game, verify_loopback_socket, write_json)


PROTOCOL = 1


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


class Host:
    def __init__(self, store: CampaignStore, control: Control, game_build: str, identity, bind: str, port: int):
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

    def admit(self, request: dict) -> dict:
        if request["protocol_version"] != PROTOCOL or request["game_build"] != self.game_build:
            return {"accepted": False, "reason": "incompatible_game_or_protocol"}
        if self.guest and self.guest["guest_id"] != request["guest_id"]:
            if self.guest["disconnected"] is None or self.last_game.get("remote_clients", 0):
                return {"accepted": False, "reason": "party_full"}
            if time.monotonic() - self.guest["disconnected"] < DEFAULT_RECONNECT_GRACE:
                return {"accepted": False, "reason": "slot_reserved"}
        try:
            active = self.store.activate(request["guest_id"], request["credential"], request["character_id"])
        except CampaignError:
            return {"accepted": False, "reason": "guest_or_character_unavailable"}
        self.guest = {
            "guest_id": request["guest_id"], "lease_id": active["lease_id"],
            "character_id": active["character"]["character_id"], "disconnected": None,
            "restore": active["character"]["state"],
        }
        if not active["resumed"]:
            self.guest_pawn = None
        with contextlib.suppress(OSError):
            self.control.event("guest_link_authenticated", guest_id=request["guest_id"],
                               character_id=self.guest["character_id"], resumed=active["resumed"])
        return {"accepted": True, "resumed": active["resumed"],
                "character_id": self.guest["character_id"], "session_id": self.control.generation}

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

    def checkpoint(self) -> int:
        if self.last_game.get("state") != "hosting":
            raise RuntimeError("Wait for the host map to finish loading")
        world = {"map": self.last_game["map"], "host": _pawn(self.last_game, "local")}
        guest = _pawn(self.last_game, "guest")
        active = self.guest and any(
            lease["character_id"] == self.guest["character_id"] for lease in self.store.status()["leases"]
        )
        changes = {self.guest["lease_id"]: guest} if guest and active and self.guest_pawn else {}
        return self.store.checkpoint(world, changes)

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

    async def observe(self, bridge: Bridge, transport) -> None:
        state = bridge.snapshot()
        if state is not None:
            self.last_game = state
        pawn = self.last_game.get("pawns", {}).get("guest")
        if pawn and self.guest and pawn["name"] != self.guest_pawn:
            saved = self.guest["restore"]
            if saved.get("map") == self.last_game["map"]:
                try:
                    await bridge.restore("guest", saved)
                except (RuntimeError, TimeoutError):
                    # The stock game can share the SaveGame object between players.
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        await bridge.restore("guest", dict(saved, health=None))
                    self.control.event("guest_restore_partial", character_id=self.guest["character_id"])
            self.guest["restore"] = {}
            self.guest_pawn = pawn["name"]
            self.control.event("guest_pawn_ready", character_id=self.guest["character_id"], pawn=self.guest_pawn)
        if self.guest and self.guest["disconnected"] is not None:
            expired = time.monotonic() - self.guest["disconnected"] >= DEFAULT_RECONNECT_GRACE
            if expired and not self.last_game.get("remote_clients", 0):
                expired_guest = self.guest
                await transport.release_guest(expired_guest["guest_id"])
                if self.guest is expired_guest:
                    self.guest = None
                    self.guest_pawn = None

    def service_requests(self) -> None:
        for path, request in self.control.requests():
            action = request.get("action")
            try:
                if action == "invite":
                    result = self.invite(request)
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
            self.service_requests()
            now = time.monotonic()
            if not self.stopping and now >= self.next_save:
                self.next_save = now + 5
                self.checkpoint()
            self.control.status(active=not self.stopping, role="host", state="hosting", game=self.last_game,
                                endpoint={"address": self.bind, "port": self.port},
                                campaign=self.store.status(),
                                link_connected=bool(self.guest and transport.connected(self.guest["guest_id"])))
            await asyncio.sleep(0.25)


async def host_game(work: Path, directory: Path, bind: str, port: int, headless: bool) -> None:
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
            await bridge.command("host", MAP)
            await bridge.wait_for(lambda state: state["state"] == "hosting")
            await asyncio.to_thread(verify_loopback_socket, process.pid, internal_port)
            saved = store.status()["world"].get("host", {})
            if saved.get("map") == MAP:
                try:
                    await bridge.restore("local", saved)
                except (RuntimeError, TimeoutError):
                    control.event("host_restore_partial")
                    with contextlib.suppress(RuntimeError, TimeoutError):
                        await bridge.restore("local", dict(saved, health=None))
            controller = Host(store, control, game_build, identity, bind, port)
            transport = await link.start_host(bind, port, "127.0.0.1", internal_port, identity,
                                              controller.admit, on_disconnect=controller.disconnected)
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
    if invite["game_build"] != build or invite["protocol_version"] != PROTOCOL:
        raise ValueError("Invitation and staged game/mod build are incompatible")
    if not isinstance(invite["host"], str) or type(invite["port"]) is not int or not 0 < invite["port"] < 65536:
        raise ValueError("Invitation has an invalid endpoint")
    if not all(isinstance(invite[key], str) for key in ("certificate", "guest_id", "credential", "character_id")):
        raise ValueError("Invitation has invalid credential fields")
    return invite


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
        self.stopping = False

    async def connect(self, timeout: float = 6) -> dict:
        request = {key: self.invite[key] for key in
                   ("guest_id", "credential", "character_id", "game_build", "protocol_version")}
        result = await self.transport.connect(self.invite["host"], self.invite["port"],
                                              self.invite["certificate"], request, timeout=timeout)
        if not result.get("accepted"):
            self.next_retry = time.monotonic() + 3
            raise RuntimeError("Host declined admission")
        session = result.get("session_id")
        if not isinstance(session, str) or len(session) != 32:
            await self.transport.disconnect()
            raise RuntimeError("Host did not return a valid session ID")
        if self.host_session and self.host_session != session:
            self.needs_travel = True
        self.host_session = session
        self.control.event("client_link_authenticated", character_id=request["character_id"],
                           resumed=bool(result.get("resumed")))
        return result

    async def open_game_link(self) -> None:
        self.transport.reset_game_endpoint()
        self.needs_travel = True
        await self.bridge.command("join", self.endpoint)
        self.last_game = await self.bridge.wait_for(lambda state: state["state"] == "connected", timeout=18)
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
        await self.reconcile_game()
        return result

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
            repair_needed = (not self.transport.connected or self.needs_travel
                             or self.last_game.get("state") in {"disconnected", "ready"})
            if not self.stopping and not self.suspended and repair_needed and time.monotonic() >= self.next_retry:
                try:
                    await self.recover()
                except (OSError, RuntimeError) as error:
                    self.control.event("reconnect_retry", error_type=type(error).__name__)
                    self.next_retry = time.monotonic() + self.auto_delay
            active = self.transport.connected and self.last_game.get("state") == "connected"
            self.control.status(active=not self.stopping, role="client",
                                state="connected" if active else "reconnecting",
                                link_connected=self.transport.connected, reconnect_suspended=self.suspended,
                                character_id=self.invite["character_id"], host_session=self.host_session,
                                game=self.last_game)
            await asyncio.sleep(0.25)


async def join_game(work: Path, directory: Path, invitation: Path, headless: bool) -> None:
    invite = load_invite(invitation, staged_build(work))
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
            await client.connect()
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
