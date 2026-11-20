"""Hand a title selection to an owned staged game and its embedded co-op worker."""

import asyncio
import hashlib
from ipaddress import ip_address
from pathlib import Path
import time
import uuid

from . import embedded_startup as startup, runtime, service
from .title_handoff import Claim, _isolated_stock_title
from .title_session import SessionPlan, join_plan


_MOD = "SilverfishCoopEmbeddedRuntime"


def installed(work: Path) -> bool:
    stage = work / "stage"
    mods = stage / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    module = mods / _MOD
    try:
        rows = (mods / "mods.txt").read_text(encoding="utf-8-sig").splitlines()
        paths = (module / "dlls/main.dll", module / "runtime/runtime-manifest.json", module / "runtime/python314.dll")
        return ([row.partition(":")[2].strip() for row in rows if row.partition(":")[0].strip() == _MOD] == ["1"]
                and all(path.is_file() and path.resolve(strict=True) == path for path in paths))
    except (OSError, ValueError):
        return False


class _RoleGame:
    def __init__(self, work: Path, directory: Path, role: str, headless: bool):
        self.work, self.directory, self.role, self.headless = work, directory, role, headless
        self.process = self.bridge = self.request = None

    def status(self) -> dict:
        return runtime.read_json(self.directory / "run" / "status.json")

    def producer_pid(self, claim: Claim) -> int | None:
        request = self.request
        if request is not None and (request.title_generation, request.title_request_id) == (claim.generation, claim.request_id):
            if startup.accepted(self.bridge, request):
                return request.pid
        return None

    async def start(self, claim: Claim, selection, options: dict, invitation: Path | None = None) -> None:
        bridge_id = uuid.uuid4().hex
        self.bridge = self.directory / "run" / "game-bridge" / bridge_id
        started = time.time()
        port = selection.private_port if isinstance(selection, startup.HostSelection) else None
        game_build = runtime.staged_build(self.work)
        self.process = runtime.launch(self.work, self.directory / "game-user", self.bridge, self.headless, port,
                                      embedded_engine=True, embedded_startup=bridge_id, **options)
        game_bridge = runtime.Bridge(self.bridge, self.process)
        self.request = startup.StartupRequest(self.role, claim.generation, claim.request_id, bridge_id, self.process.pid,
                                              game_build, self.directory.name, selection)
        try:
            startup.publish(self.bridge, self.request, invitation=invitation)
            while self.process.poll() is None:
                if self.producer_pid(claim) is not None:
                    try:
                        status = self.status()
                    except (OSError, ValueError):
                        status = {}
                    if (isinstance(status, dict) and (status.get("schema"), status.get("pid"), status.get("role"), status.get("active"))
                            == (1, self.request.pid, self.role, False)
                            and type(status.get("started_unix")) in (int, float) and status["started_unix"] >= started):
                        if status.get("state") == "failed":
                            raise RuntimeError("The embedded co-op service failed")
                        if (self.headless and status.get("state") == "stopped"
                                and _isolated_stock_title(game_bridge.snapshot(), self.directory / "game-user" / "Saved")):
                            return
                await asyncio.sleep(.1)
            if self.process.returncode != 0:
                raise RuntimeError("The embedded co-op game exited unexpectedly")
        finally:
            await runtime.stop_game(self.process)
            if invitation is not None:
                (self.bridge / startup.INVITE_FILE).unlink(missing_ok=True)


def host_plan(work: Path, campaign: Path, bind: str, port: int, mode: str, expected_map: str | None,
              *, headless: bool = False) -> SessionPlan:
    bind = str(ip_address(bind))
    role = _RoleGame(work, campaign, "host", headless)

    async def start(claim):
        mode_now, area = service.menu_host_start(campaign)
        if mode_now != mode:
            raise ValueError("The selected Host campaign changed before startup")
        private = runtime.spare_udp_port()
        if private == port:
            private = runtime.spare_udp_port()
        if private == port:
            raise RuntimeError("A distinct private Unreal port was not available")
        authored = area in service.AUTHORED_MAPS or area in service.EXPANDED_MAPS
        persistent, profile = service._host_save_options(work, campaign / "game-user", True, authored)
        options = service.MENU_HOST_OPTIONS | service._host_stage_options(work, True, True)
        options["host_dig_live"] = service._host_dig_available(work, authored, persistent, profile)
        await role.start(claim, startup.HostSelection(bind, port, private, mode), options)

    return SessionPlan(start, role.status,
                       lambda status: status.get("endpoint") == {"address": bind, "port": port}
                       and (expected_map is None or status["game"]["map"] == expected_map), role.producer_pid)


def guest_plan(work: Path, directory: Path, invitation: Path, character_id: str | None,
               *, headless: bool = False) -> SessionPlan:
    selected = service.load_invite(invitation, runtime.staged_build(work))
    if selected.get("native_zones") is not True:
        return join_plan(work, directory, invitation, character_id=character_id, headless=headless)
    character = selected["character_id"] if character_id is None else character_id
    if not isinstance(character, str) or str(uuid.UUID(character)) != character:
        raise ValueError("The title Join action requires an exact character ID")
    with invitation.open("rb") as stream:
        digest = hashlib.sha256(stream.read(startup.INVITE_LIMIT + 1)).hexdigest()
    role = _RoleGame(work, directory, "client", headless)

    async def start(claim):
        if service.load_invite(invitation, runtime.staged_build(work)) != selected:
            raise ValueError("The selected invitation changed")
        options = service.guest_engine_options(work, selected)
        await role.start(claim, startup.JoinSelection(digest, character), options, invitation)

    return SessionPlan(start, role.status,
                       lambda status: status.get("character_id") == character and status.get("gameplay_fresh") is True,
                       role.producer_pid)
