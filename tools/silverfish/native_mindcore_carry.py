"""Transport exact Data Silo carry edges and authenticated camera samples."""

from contextlib import suppress
from dataclasses import dataclass
import math
import re
import time
import uuid

from . import native_mindcore as core, party
from .native_mindcore_view import valid_pawn


ACTION = "native-mindcore-carry"
FIXTURE = "native-mindcore-guest-carry-fixture"
APPROACH_FIXTURE = "native-mindcore-carry-guest-approach"
AUTHORITY_TAG = "native-mindcore-authority-carry"
AUTHORITY = "native_mindcore_authority_carry"
CAPABILITY_TAG = "native-mindcore-carry-capability"
CAPABILITY = "native_mindcore_carry_capability"
VIEW_TAG = "native-mindcore-guest-carry"
VIEW = "native_mindcore_guest_carry"
VIEW_SCOPE_TAG = "native-mindcore-carry-view-scope"
VIEW_SCOPE = "native_mindcore_carry_view_scope"
AIM_TAG = "native-mindcore-carry-aim"
AIM = "native_mindcore_carry_aim"
INPUT = "native-mindcore-carry-input.tsv"
VIEW_LEASE = "native-mindcore-carry-heartbeat"
AUTHORITY_AIM = "native-mindcore-authority-aim.tsv"
PUBLIC = "native_mindcore_carry"
_TOKEN = re.compile(r"[a-f0-9]{32}")
_SERIAL = re.compile(r"[1-9][0-9]{0,15}")
_ANGLE = re.compile(r"-?(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,4})?")
_REASON = re.compile(r"[a-z0-9_]{1,96}")
_STATES = {"held", "released", "mount_released", "failure"}
_GAMEPLAY_FRESH_FOR = 2.0
_NATIVE_HOLD_FOR = 2.0


def token(value):
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None and value != "0" * 32


def serial(value):
    if not isinstance(value, str) or not _SERIAL.fullmatch(value) or int(value) >= 2**53:
        raise ValueError("Invalid Core carry sequence")
    return int(value)


def angles(pitch, yaw):
    if not all(isinstance(value, str) and _ANGLE.fullmatch(value) for value in (pitch, yaw)):
        raise ValueError("Invalid Core camera angles")
    pitch, yaw = float(pitch), float(yaw)
    if not -89.9 <= pitch <= 89.9 or not -180 <= yaw <= 180:
        raise ValueError("Core camera angles are out of bounds")
    return pitch, yaw


def parse_authority(fields):
    if len(fields) != 10:
        raise ValueError("Invalid authoritative Core carry report")
    tag, area, actor, pawn, lease, state, number, reason, pitch, yaw = fields
    if ((tag, area, actor) != (AUTHORITY_TAG, core.MAP, core.CORE) or not valid_pawn(pawn)
            or not token(lease) or state not in _STATES or not _REASON.fullmatch(reason)):
        raise ValueError("Invalid authoritative Core carry report")
    pitch, yaw = angles(pitch, yaw)
    return dict(pawn=pawn, core=actor, lease=lease, state=state, sequence=serial(number), reason=reason, pitch=pitch, yaw=yaw)


def _guest_scope(fields):
    area, pawn, actor, session, epoch = fields
    if (area, actor) != (core.MAP, core.CORE) or not valid_pawn(pawn) or not token(session) or not token(epoch):
        raise ValueError("Invalid local Core carry scope")
    return pawn, session, epoch


def parse_view(fields):
    if len(fields) != 7 or fields[0] != VIEW_TAG or fields[6] not in ("ready", "held"):
        raise ValueError("Invalid local Core carry view")
    pawn, session, epoch = _guest_scope(fields[1:6])
    return dict(pawn=pawn, session=session, epoch=epoch, state=fields[6])


def parse_aim(fields):
    if len(fields) != 9 or fields[0] != AIM_TAG:
        raise ValueError("Invalid local Core camera report")
    pawn, session, epoch = _guest_scope(fields[1:6])
    pitch, yaw = angles(*fields[7:])
    return dict(pawn=pawn, session=session, epoch=epoch, sequence=serial(fields[6]), pitch=pitch, yaw=yaw)


def parse_view_scope(fields):
    if (len(fields) != 3 or fields[:2] != [VIEW_SCOPE_TAG, core.MAP]
            or fields[2] not in {"world", "driver", "pawn", "dead", "downed", "loading", "menu", "local_hold", "camera", "replicated_core",
                                 "core_mesh", "cradle", "quiet_cradle", "mounted", "lease", "input", "aim", "bridge_call"}):
        raise ValueError("Invalid local Core carry scope report")
    return fields[2]


def parse_input(fields):
    if (len(fields) != 11 or fields[0] != "native-mindcore-carry-toggle" or fields[7] not in ("grab", "release")
            or fields[8] not in ("interact", "pickup")):
        raise ValueError("Invalid local Core carry input")
    pawn, session, epoch = _guest_scope(fields[2:7])
    return (serial(fields[1]), (pawn, session, epoch), fields[7], fields[8], angles(*fields[9:11]))


def parse_capability(fields):
    if fields != [CAPABILITY_TAG, core.MAP, core.CORE]:
        raise ValueError("Invalid stock Core carry capability")
    return core.CORE


STATUS_ROWS = {
    AUTHORITY_TAG: (AUTHORITY, "hosting", parse_authority),
    CAPABILITY_TAG: (CAPABILITY, "hosting", parse_capability),
    VIEW_TAG: (VIEW, "connected", parse_view),
    VIEW_SCOPE_TAG: (VIEW_SCOPE, "connected", parse_view_scope),
    AIM_TAG: (AIM, "connected", parse_aim),
}


async def approach_fixture(host, bridge, character):
    from .native_mosby_tape import _scope

    pawn = _scope(host, character, core.MAP, guest_only=True)
    member = host.native_recipients()[character]
    if not host.fixtures or not host.mindcore_carry or not host.mindcore_carry._available() or host.mindcore_carry.holder:
        raise party.RuleError("The isolated guest Core approach is unavailable")
    alias, binding = host.role_pawn(member.get("role", "guest"))
    if not alias:
        raise party.RuleError("The isolated guest Core approach has no exact roster seat")
    # A floor position qualified by the stock listen-host Core approach in Data Silo.
    location = [714.39, -1467, 134.85]
    frame = await bridge.restore(alias, {"location": location}, binding=binding)
    moved = frame.get("pawns", {}).get(alias, {})
    observed = moved.get("location", ())
    if (frame.get("map") != core.MAP or moved.get("name") != pawn or len(observed) != 3
            or not all(type(axis) in (int, float) and math.isfinite(axis) for axis in observed)
            or math.dist(observed, location) > 15):
        raise RuntimeError("The exact staged guest did not reach the loose Core")
    host.last_game, host.last_seen = frame, time.monotonic()
    return {"pawn": pawn, "map": core.MAP, "location": observed}


@dataclass(frozen=True)
class Request:
    epoch: str
    lease: str
    edge: str
    sequence: int
    aim: tuple[float, float] | None = None

    @classmethod
    def parse(cls, target):
        values = target.split("@") if isinstance(target, str) and len(target) <= 110 else []
        if len(values) not in (4, 6):
            raise ValueError("Invalid Core carry request")
        epoch, lease, edge, number, *aim = values
        if not token(epoch) or not token(lease) or not (edge == "drop" and not aim or edge in ("grab", "aim") and aim):
            raise ValueError("Invalid Core carry request")
        return cls(epoch, lease, edge, serial(number), angles(*aim) if aim else None)

    def target(self):
        values = (self.epoch, self.lease, self.edge, str(self.sequence))
        return "@".join((*values, *(f"{value:.3f}" for value in (self.aim or ()))))


@dataclass
class Holder:
    character: str
    pawn: str
    epoch: str
    token: str
    sequence: int
    renewed_at: float
    revoked: bool = False


class Authority:
    def __init__(self, host):
        self.host, self.bridge = host, None
        self.holder = None
        self.spent = {}
        self.closing = False

    def _available(self):
        host = self.host
        game = host.last_game
        scene = game.get(core.SCENE, {})
        return (self.bridge is not None and not (host.stopping or host.transitioning or host.native_checkpoint_unsafe
                                                 or host.party.get("wipe"))
                and (game.get("state"), game.get("map"), game.get(CAPABILITY)) == ("hosting", core.MAP, core.CORE)
                and game.get(core.REPLICATION, {}).get("state") == "active"
                and (scene.get("status"), scene.get("installed")) == (0, False)
                and time.monotonic() - host.last_seen < 2)

    def _pawn(self, character):
        if not self._available():
            return None
        player = self.host.party["players"].get(character, {})
        member = self.host.native_recipients().get(character, {})
        if (not player or player.get("downed") or character in self.host.party.get("pending_spawns", {})
                or not member.get("connected") or not valid_pawn(member.get("pawn"))):
            return None
        return member["pawn"]

    def public(self):
        if self.host.last_game.get("map") != core.MAP:
            return None
        holder = self.holder
        return {"state": "held" if holder else "ready" if self._available() else "unavailable",
                "holder": holder.character if holder else None}

    def _remove_aim(self):
        if self.bridge is not None:
            with suppress(OSError):
                (self.bridge.directory / AUTHORITY_AIM).unlink(missing_ok=True)

    def _forget(self):
        self.holder = None
        self._remove_aim()

    def close(self):
        self._forget()

    def maintain(self):
        holder = self.holder
        row = self.host.last_game.get(AUTHORITY, {})
        if row.get("state") == "failure":
            self.host.native_checkpoint_unsafe = self.host.stopping = True
            raise RuntimeError("The stock Core carry could not be released safely")
        if not holder or self.closing:
            return
        if row.get("lease") == holder.token and row.get("state") in {"released", "mount_released"}:
            self.host.control.event("native_mindcore_carry_finished", character_id=holder.character,
                                    state=row["state"], reason=row["reason"])
            self._forget()
        elif self.host.zone_epoch != holder.epoch or self.host.last_game.get("map") != core.MAP:
            self._forget()
        elif self._pawn(holder.character) != holder.pawn and not holder.revoked:
            holder.revoked = True
            # The native lease has its own deadline even if the coordinator stalls.
            self._remove_aim()

    def aim(self, character, request):
        from .runtime import write_text

        holder = self.holder
        if (not holder or holder.revoked or self.closing or (character, request.epoch, request.lease) !=
                (holder.character, holder.epoch, holder.token) or request.epoch != self.host.zone_epoch
                or self._pawn(character) != holder.pawn or request.sequence <= holder.sequence
                or time.monotonic() - holder.renewed_at >= 1.7):
            return {"ok": False, "error": "native_mindcore_carry_aim_expired"}
        pitch, yaw = request.aim
        try:
            write_text(self.bridge.directory / AUTHORITY_AIM,
                       f"1\t{holder.token}\t{request.sequence}\t{pitch:.3f}\t{yaw:.3f}\n", newline="\n")
        except OSError:
            return {"ok": False, "error": "native_mindcore_carry_aim_unavailable"}
        holder.sequence, holder.renewed_at = request.sequence, time.monotonic()
        return {"ok": True}

    async def _grab(self, holder, request):
        aim = tuple(f"{value:.3f}" for value in request.aim)
        try:
            frame = await self.bridge.command(ACTION, holder.pawn, holder.token, "grab", str(request.sequence), *aim,
                                              timeout=4)
        except RuntimeError as error:
            if str(error).startswith("mutation_unverified_"):
                self.host.native_checkpoint_unsafe = self.host.stopping = True
            raise
        self.host.last_game, self.host.last_seen = frame, time.monotonic()
        return self._receipt(frame, holder, request, {"held"})

    def _receipt(self, frame, holder, request, wanted):
        row = frame.get(AUTHORITY, {})
        if ((frame.get("state"), frame.get("map"), row.get("pawn"), row.get("lease"), row.get("sequence"))
                != ("hosting", core.MAP, holder.pawn, holder.token, request.sequence)
                or row.get("state") not in wanted):
            raise RuntimeError("Stock Core carry did not return the exact authoritative receipt")
        return row

    async def _drop(self, holder, request):
        from .runtime import write_text

        write_text(self.bridge.directory / AUTHORITY_AIM,
                   f"1\t{holder.token}\t{request.sequence}\tdrop\n", newline="\n")
        frame = await self.bridge.wait_for(
            lambda frame: ((row := frame.get(AUTHORITY, {})).get("lease"), row.get("pawn"))
            == (holder.token, holder.pawn) and row.get("state") in {"released", "mount_released", "failure"},
            timeout=2)
        row = frame[AUTHORITY]
        if row["state"] == "failure":
            self.host.native_checkpoint_unsafe = self.host.stopping = True
            raise RuntimeError(row["reason"])
        return self._receipt(frame, holder, request, {"released", "mount_released"})

    async def release_authenticated(self, character, request):
        try:
            return {"ok": True, **await self.operate(character, request)}
        except (OSError, ValueError, RuntimeError) as error:
            return {"ok": False, "error": str(error)}

    async def operate(self, character, request):
        if request.epoch != self.host.zone_epoch or self.closing:
            raise party.RuleError("native_mindcore_carry_scope_changed")
        if request.edge == "grab":
            pawn = self._pawn(character)
            if not pawn or self.holder or request.lease in self.spent:
                raise party.RuleError("native_mindcore_carry_unavailable")
            self.spent[request.lease] = None
            if len(self.spent) > 256:
                self.spent.pop(next(iter(self.spent)))
            holder = Holder(character, pawn, request.epoch, request.lease, request.sequence, time.monotonic())
            row = await self._grab(holder, request)
            holder.renewed_at = time.monotonic()
            self.holder = holder
        else:
            holder = self.holder
            if (not holder or (character, request.lease) != (holder.character, holder.token)
                    or holder.epoch != request.epoch):
                raise party.RuleError("native_mindcore_carry_not_holder")
            if request.sequence <= holder.sequence:
                raise party.RuleError("native_mindcore_carry_stale_sequence")
            self.closing = True
            try:
                row = await self._drop(holder, request)
            finally:
                self.closing = False
                self._forget()
        self.host.control.event("native_mindcore_carry_action", character_id=character, edge=request.edge,
                                sequence=row["sequence"], state=row["state"])
        return {PUBLIC: {"state": row["state"], "sequence": row["sequence"]}}


class GuestInput:
    def __init__(self, client):
        self.client = client
        self.scope = self.lease = None
        self.sequence = self.sample_sequence = self.input_sequence = self.offset = 0
        self.heartbeat_at = self.aim_at = 0.0
        self.held_since = self.last_renewal_at = 0.0
        self.holder_seen = False

    def close(self):
        if self.scope:
            with suppress(OSError):
                (self.client.bridge.directory / VIEW_LEASE).unlink(missing_ok=True)
        self.scope = self.lease = None
        self.heartbeat_at = self.last_renewal_at = 0.0

    def _events(self):
        try:
            with (self.client.bridge.directory / INPUT).open("rb") as stream:
                stream.seek(self.offset)
                batch = stream.read(4096)
        except OSError:
            return
        end = batch.rfind(b"\n") + 1
        self.offset += end or (len(batch) if len(batch) == 4096 else 0)
        for line in batch[:end].splitlines():
            try:
                event = parse_input(line.decode("ascii").split("\t"))
            except ValueError:
                continue
            if event[0] > self.input_sequence:
                self.input_sequence = event[0]
                yield event

    def _scope(self, active):
        client = self.client
        row = client.gameplay.get(PUBLIC, {})
        pawn = client.last_game.get("pawns", {}).get("local", {}).get("name")
        member = client.gameplay.get("players", {}).get(client.viewer_role, {})
        if (active and client.transport.connected and client.transport.game_access
                and (client.last_game.get("state"), client.last_game.get("map"), client.expected_zone)
                == ("connected", core.MAP, core.MAP)
                and valid_pawn(pawn) and token(client.host_session) and token(client.zone_epoch)
                and row.get("state") in {"ready", "held"} and not client.gameplay.get("wipe")
                and member.get("character_id") == client.invite["character_id"] and member.get("downed") is False
                and row.get("holder") in (None, client.invite["character_id"])):
            return pawn, client.host_session, client.zone_epoch
        return None

    def _heartbeat(self, now):
        from .runtime import write_text

        pawn, session, epoch = self.scope
        state = "held" if self.lease else "ready"
        write_text(self.client.bridge.directory / VIEW_LEASE,
                   f"1\t{int(time.time())}\t{session}\t{epoch}\t{pawn}\t{state}\n", newline="\n")
        self.heartbeat_at = now + .65

    async def _input(self, event):
        number, scope, edge, source, aim = event
        if scope != self.scope or self.lease and edge != "release" or not self.lease and edge != "grab":
            return
        client = self.client
        operation = uuid.uuid5(client.bridge.input_namespace, f"mindcore:{scope[1:]}:{number}").hex
        lease = self.lease or operation
        self.sequence = self.sequence + 1 if self.lease else 1
        request = Request(scope[2], lease, "drop" if self.lease else "grab", self.sequence, None if self.lease else aim)
        try:
            reply = await client.gameplay_action(ACTION, operation, request.target())
            result = reply.get(PUBLIC, {})
            if result.get("sequence") != self.sequence:
                raise RuntimeError("The host did not acknowledge the Core carry input")
            if request.edge == "grab" and result.get("state") == "held":
                self.lease = lease
                self.sample_sequence = client.last_game.get(AIM, {}).get("sequence", 0)
                self.held_since = self.last_renewal_at = time.monotonic()
                self.holder_seen = False
            elif request.edge == "drop" and result.get("state") in {"released", "mount_released"}:
                self.lease = None
            else:
                raise RuntimeError("The host did not verify the requested Core carry state")
            self.heartbeat_at = 0
            client.control.event("native_mindcore_guest_carry_input", edge=edge, source=source,
                                 native_sequence=number, result=result["state"])
        except (OSError, ValueError, RuntimeError) as error:
            if request.edge == "drop":
                self.lease = None
                self.heartbeat_at = 0
            client.control.event("native_mindcore_guest_carry_refused", edge=edge, source=source,
                                 native_sequence=number, reason=str(error))

    async def poll(self, active):
        game = self.client.last_game
        current = self._scope(active)
        now = time.monotonic()
        aim = game.get(AIM, {})
        aim_scope = (aim.get("pawn"), aim.get("session"), aim.get("epoch"))
        report = self.client.gameplay.get(PUBLIC, {})
        held = (report.get("state"), report.get("holder")) == ("held", self.client.invite["character_id"])
        gameplay_fresh = now - self.client.gameplay_seen < _GAMEPLAY_FRESH_FOR
        if not gameplay_fresh:
            if (current and current == self.scope and self.lease and self.holder_seen and held
                    and now - self.last_renewal_at < _NATIVE_HOLD_FOR):
                # Wait for fresh gameplay without extending the authority's native deadline.
                self.heartbeat_at = 0.0
                if current == aim_scope:
                    self.sample_sequence = max(self.sample_sequence, aim["sequence"])
                for _ in self._events():
                    pass
                return
            current = None
        if current != self.scope:
            if self.lease:
                self.client.control.event("native_mindcore_guest_carry_scope_ended", active=bool(active),
                                          view_scope=game.get(VIEW_SCOPE),
                                          gameplay_fresh=gameplay_fresh, public_state=report.get("state"))
            self.close()
            self.scope = current
        if self.lease:
            if (now - self.last_renewal_at >= _NATIVE_HOLD_FOR
                    or not held and (self.holder_seen or now - self.held_since > 1.5)):
                self.lease = None
                self.heartbeat_at = 0
            elif held:
                self.holder_seen = True
        try:
            if self.scope and self.heartbeat_at <= now:
                self._heartbeat(now)
            for event in self._events():
                await self._input(event)
            if (self.lease and self.scope == aim_scope
                    and aim["sequence"] > self.sample_sequence and now >= self.aim_at):
                self.sample_sequence, self.aim_at = aim["sequence"], now + .12
                self.sequence += 1
                request = Request(self.scope[2], self.lease, "aim", self.sequence, (aim["pitch"], aim["yaw"]))
                operation = uuid.uuid5(self.client.bridge.input_namespace, f"mindcore:{self.lease}:aim:{self.sequence}").hex
                await self.client.gameplay_action(ACTION, operation, request.target())
                self.last_renewal_at = time.monotonic()
            if self.scope and self.heartbeat_at <= now:
                self._heartbeat(now)
        except (OSError, ValueError, RuntimeError) as error:
            self.client.control.event("native_mindcore_guest_carry_link_lost", reason=str(error))
            self.close()
