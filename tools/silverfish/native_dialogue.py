"""Read the Castaway's stock dialogue and authorize each guest's own choices."""

from dataclasses import dataclass
import time
import uuid

from . import party
from .travel import OBSERVATORY


PROFILE = "castaway"
LEVEL = OBSERVATORY + ".Observitory_P:PersistentLevel."
NPC = "NPC_Castaway_C " + LEVEL + "NPC_Castaway_C_1"
CONTROLLER = "Dialogue_Controler_BP_C " + LEVEL + "Dialogue_Controler_BP_C_3"
OPEN, CHOOSE = "native-dialogue-open", "native-dialogue-choose"
READ, RENDER = "native-dialogue-read", "native-dialogue-render"
APPROACH, FIXTURE = "native-dialogue-approach", "native-dialogue-stock-fixture"
HEARTBEAT = "native-dialogue-heartbeat"
ROOT, STORY = "Cast_Root", "Cast_Origin"
ROWS = {ROOT: (1, {1: None, 3: STORY}), STORY: (3, {1: ROOT})}
SESSION_SECONDS = 30


def valid_token(value) -> bool:
    return isinstance(value, str) and party.REQUEST_ID.fullmatch(value) is not None


def parse_stock(frame, lines, choices) -> dict:
    if (len(frame) != 6 or tuple(frame[1:4]) != (OBSERVATORY, NPC, CONTROLLER) or not frame[4].startswith("Player_FP_C ")
            or not frame[5] or len(frame[5]) > 512):
        raise ValueError("Invalid stock Castaway identity")
    result = {name: {"lines": {}, "choices": {}} for name in ROWS}
    for entries, field in ((lines, "lines"), (choices, "choices")):
        for fields in entries:
            if (len(fields) != 4 or fields[1] not in ROWS or fields[2] not in ("1", "2", "3")
                    or not 0 < len(fields[3]) <= 1024):
                raise ValueError("Invalid stock Castaway text row")
            index = int(fields[2])
            target = result[fields[1]][field]
            if index in target:
                raise ValueError("Duplicate stock Castaway text row")
            target[index] = fields[3]
    for name, (count, transitions) in ROWS.items():
        row = result[name]
        if row["lines"].keys() != set(range(1, count + 1)) or row["choices"].keys() != transitions.keys():
            raise ValueError("Incomplete stock Castaway row")
        row["lines"] = [row["lines"][line] for line in range(1, count + 1)]
        row["choices"] = [{"ordinal": ordinal, "text": row["choices"][ordinal]} for ordinal in transitions]
    return {"map": OBSERVATORY, "npc": NPC, "controller": CONTROLLER, "pawn": frame[4], "connection": frame[5], "rows": result}


def render_text(reply, row: str | None) -> tuple[str, ...]:
    lines, choices = reply.get("lines"), reply.get("choices")
    if row is None and lines == choices == []:
        return ()
    if row not in ROWS or not isinstance(lines, list) or not isinstance(choices, list):
        raise ValueError("The host did not provide the verified Castaway dialogue text")
    line_count, allowed = ROWS[row]
    if (len(lines) != line_count or not all(isinstance(choice, dict) for choice in choices)
            or [choice.get("ordinal") for choice in choices] != list(allowed)):
        raise ValueError("The host did not provide the verified Castaway dialogue choices")
    result = (*lines, *(choice.get("text") for choice in choices))
    if any(not isinstance(value, str) or not 0 < len(value.encode()) <= 1024 or any(char in value for char in "\r\n\t\0")
           for value in result):
        raise ValueError("The host did not provide bounded stock Castaway dialogue text")
    return result


@dataclass(frozen=True)
class Session:
    scope: tuple
    token: str
    row: str
    expires: float


@dataclass(frozen=True)
class Receipt:
    operation: str
    request: tuple[str, str]
    scope: tuple
    expires: float
    reply: dict


class DialogueSessions:
    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self.receipts: dict[str, Receipt] = {}

    def forget(self, character: str) -> None:
        self.sessions.pop(character, None)
        self.receipts.pop(character, None)

    def _scope(self, host, character: str, now: float) -> tuple:
        recipients = host.native_recipients()
        for previous in self.sessions.keys() | self.receipts.keys():
            if not recipients.get(previous, {}).get("connected"):
                self.forget(previous)
        player, member = host.party["players"].get(character), recipients.get(character)
        if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning or host.stopping
                or host.native_checkpoint_unsafe or host.party["wipe"] or character in host.party["pending_spawns"]
                or not host.zone_epoch or host.last_game.get("state") != "hosting" or host.last_game.get("map") != OBSERVATORY
                or now - host.last_seen > 3 or character == "host" or not player or player["downed"]
                or not member or not member.get("connected") or not member.get("pawn")):
            raise party.RuleError("native_dialogue_guest_unavailable")
        return host.control.generation, host.zone_epoch, member["pawn"]

    @staticmethod
    async def _read(host, bridge, pawn: str) -> dict:
        snapshot = await bridge.command(READ, pawn, timeout=4)
        stock = snapshot.get("native_dialogue_stock", {})
        if (snapshot.get("state"), snapshot.get("map"), stock.get("map"), stock.get("npc"),
                stock.get("controller"), stock.get("pawn")) != ("hosting", OBSERVATORY, OBSERVATORY, NPC, CONTROLLER, pawn):
            raise RuntimeError("The stock dialogue did not verify the exact local NPC and enrolled pawn")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return stock

    async def approach(self, host, bridge, character: str) -> dict:
        if not host.fixtures:
            raise party.RuleError("native_dialogue_approach_unavailable")
        scope = self._scope(host, character, time.monotonic())
        snapshot = await bridge.command(APPROACH, scope[-1], timeout=5)
        if (snapshot.get("state"), snapshot.get("map")) != ("hosting", OBSERVATORY):
            raise RuntimeError("The stock dialogue approach left Observatory")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return {"native_dialogue_approach": {"pawn": scope[-1], "npc": NPC}}

    async def act(self, host, bridge, character: str, action: str, target: str | None, operation_id: str) -> dict:
        if action not in (OPEN, CHOOSE) or not isinstance(target, str) or not valid_token(operation_id):
            raise party.RuleError("native_dialogue_request_invalid")
        now = time.monotonic()
        scope = self._scope(host, character, now)
        stock = await self._read(host, bridge, scope[-1])
        now = time.monotonic()
        if self._scope(host, character, now) != scope:
            raise party.RuleError("native_dialogue_scope_changed")
        scope = (*scope, stock["connection"])
        previous = self.receipts.get(character)
        if previous and previous.operation == operation_id and previous.scope == scope and previous.expires > now:
            if previous.request != (action, target):
                raise party.RuleError("operation_id_conflict")
            return previous.reply
        if action == OPEN:
            if target != PROFILE:
                raise party.RuleError("native_dialogue_profile_unavailable")
            destination = ROOT
        else:
            token, separator, choice = target.partition("@")
            session = self.sessions.get(character)
            if (not separator or not valid_token(token) or not session or session.token != token
                    or session.scope != scope or session.expires <= now):
                raise party.RuleError("native_dialogue_choice_expired")
            if choice not in ("1", "3") or int(choice) not in ROWS[session.row][1]:
                raise party.RuleError("native_dialogue_choice_unavailable")
            destination = ROWS[session.row][1][int(choice)]
        report = {"profile": PROFILE, "map": OBSERVATORY, "npc": NPC, "row": destination, "token": None,
                  "state": "closed" if destination is None else "open", "lines": [], "choices": []}
        expires = now + SESSION_SECONDS
        if destination is None:
            self.sessions.pop(character, None)
        else:
            token = uuid.uuid4().hex
            self.sessions[character] = Session(scope, token, destination, expires)
            report.update(stock["rows"][destination], token=token)
        result = {"native_dialogue": report}
        self.receipts[character] = Receipt(operation_id, (action, target), scope, expires, result)
        return result


class DialogueView:
    def __init__(self, bridge, control):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.pending = None

    async def sync(self, snapshot, epoch, active, action):
        events = self.bridge.native_dialogue_requests()
        if not active or not epoch or snapshot.get("map") != OBSERVATORY:
            self.pending = None
            return snapshot
        now = time.monotonic()
        if events:
            self.pending = (epoch, now, events[-1])
        if not self.pending:
            return snapshot
        if self.pending[0] != epoch or now - self.pending[1] > 2:
            self.pending = None
            return snapshot
        sequence, pawn, token, choice = self.pending[2]
        shown = snapshot.get("native_dialogue_view", {})
        if (sequence <= self.latest or pawn != snapshot.get("pawns", {}).get("local", {}).get("name")
                or (shown.get("state"), shown.get("sequence"), shown.get("pawn"), shown.get("token")) !=
                ("pending", sequence, pawn, token)):
            return snapshot
        self.pending = None
        self.latest = sequence
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:castaway:{sequence}").hex
        try:
            verb, target = (OPEN, PROFILE) if token == "-" else (CHOOSE, f"{token}@{choice}")
            reply = (await action(verb, operation, target)).get("native_dialogue", {})
            row, next_token = reply.get("row"), reply.get("token")
            if (tuple(reply.get(field) for field in ("profile", "map", "npc")) != (PROFILE, OBSERVATORY, NPC)
                    or (row is None and (next_token is not None or reply.get("state") != "closed"))
                    or (row is not None and (row not in ROWS or not valid_token(next_token) or reply.get("state") != "open"))):
                raise RuntimeError("The host did not verify the current Castaway response")
            rendered = await self.bridge.command(RENDER, str(sequence), row or "-", next_token or "-",
                                                 *render_text(reply, row), timeout=2)
            proof = rendered.get("native_dialogue_view", {})
            if tuple(proof.get(field) for field in ("pawn", "sequence", "row", "token", "state")) != (
                    pawn, sequence, row or "-", next_token or "-", "open" if row else "closed"):
                raise RuntimeError("The client did not verify its exact Castaway menu")
            self.control.event("native_dialogue_interaction", sequence=sequence, action=verb, row=row)
            return rendered
        except (OSError, ValueError, RuntimeError) as error:
            self.control.event("native_dialogue_rejected", sequence=sequence, reason=str(error))
            try:
                return await self.bridge.command(RENDER, str(sequence), "refused", "-", timeout=2)
            except (OSError, ValueError, RuntimeError):
                pass
        return snapshot
