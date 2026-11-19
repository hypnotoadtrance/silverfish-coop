"""Read scoped stock dialogue and authorize each guest's own choices."""

import asyncio
from dataclasses import dataclass
import time
import uuid

from . import party
from .travel import OBSERVATORY, SCIENTIST_SHOP, SHOPKEEP


PROFILE = "castaway"
LEVEL = OBSERVATORY + ".Observitory_P:PersistentLevel."
NPC = "NPC_Castaway_C " + LEVEL + "NPC_Castaway_C_1"
CONTROLLER = "Dialogue_Controler_BP_C " + LEVEL + "Dialogue_Controler_BP_C_3"
OPEN, CHOOSE = "native-dialogue-open", "native-dialogue-choose"
READ, RENDER, COMMIT = "native-dialogue-read", "native-dialogue-render", "native-dialogue-commit"
APPROACH, FIXTURE = "native-dialogue-approach", "native-dialogue-stock-fixture"
HEARTBEAT = "native-dialogue-heartbeat"
ROOT, STORY = "Cast_Root", "Cast_Origin"
ROWS = {ROOT: (1, {1: None, 3: STORY}), STORY: (3, {1: ROOT})}
MOSBY = "mosby"
MOSBY_LEVEL = SCIENTIST_SHOP + ".Shop_Scientist_01:PersistentLevel."
MOSBY_NPC = "NPC_DrMosby_C " + MOSBY_LEVEL + "NPC_DrMosby_C_1"
MOSBY_CONTROLLER = "Dialogue_Controler_BP_C " + MOSBY_LEVEL + "Dialogue_Controler_BP_C_1"
GREET, QUESTIONS, ANOMALIES, DRILL, OFFER = ("Mosby_Greet", "Mosby_Questions", "Mosby_Anomalies", "Mosby_EatherDrill", "Mosby_DrillQuest_01")
MOSBY_QUEST = "Clt_Tape_StoneMaze_01"
MOSBY_ROWS = {GREET: (1, {1: None, 4: QUESTIONS}), QUESTIONS: (1, {1: GREET, 3: ANOMALIES, 5: DRILL, 6: OFFER}),
              ANOMALIES: (4, {1: QUESTIONS}), DRILL: (3, {1: QUESTIONS}), OFFER: (4, {1: None, 2: None})}
SHOPKEEP_PROFILE = "shopkeep"
SHOPKEEP_LEVEL = SHOPKEEP + ".Shop_ShopKeep_P:PersistentLevel."
SHOPKEEP_NPC = "NPC_ShopKeep_C " + SHOPKEEP_LEVEL + "NPC_ShopKeep_C_1"
SHOPKEEP_CONTROLLER = "Dialogue_Controler_BP_C " + SHOPKEEP_LEVEL + "Dialogue_Controler_BP_C_1"
SHOPKEEP_FIRST, SHOPKEEP_ROOT = "ShopKeep_FirstMeet", "ShopKeep_Default"
SHOPKEEP_ROWS = {SHOPKEEP_FIRST: (6, {1: SHOPKEEP_ROOT}), SHOPKEEP_ROOT: (1, {2: SHOPKEEP_ROOT})}
SHOPKEEP_PROGRESS = ("meet", "cash", "xp")
SHOPKEEP_OPEN = "native-dialogue-shopkeep-open"
SHOPKEEP_CASH = "native-dialogue-shopkeep-cash"
SHOPKEEP_VIEW = "native-dialogue-shopkeep-view"
SHOPKEEP_COUNTER = "Shop_BP_C " + SHOPKEEP_LEVEL + "Shop_BP_C_1"
PROGRESS_KEYS = ("topic_eather_drill", "topic_drill_data", "stone_maze_readings", "quest_step", "cash", "xp")
EFFECTS = {(QUESTIONS, 3): "topic_eather_drill", (QUESTIONS, 5): "topic_drill_data", (OFFER, 2): "stone_maze_issued"}
ORDINALS = tuple(str(index) for index in sorted({
    index for _, choices in (*ROWS.values(), *MOSBY_ROWS.values(), *SHOPKEEP_ROWS.values()) for index in choices
}))
SESSION_SECONDS = 30


@dataclass(frozen=True)
class Profile:
    id: str
    map: str
    npc: str
    controller: str
    root: str
    rows: dict


PROFILES = {OBSERVATORY: Profile(PROFILE, OBSERVATORY, NPC, CONTROLLER, ROOT, ROWS),
            SCIENTIST_SHOP: Profile(MOSBY, SCIENTIST_SHOP, MOSBY_NPC, MOSBY_CONTROLLER, GREET, MOSBY_ROWS),
            SHOPKEEP: Profile(SHOPKEEP_PROFILE, SHOPKEEP, SHOPKEEP_NPC, SHOPKEEP_CONTROLLER, SHOPKEEP_ROOT, SHOPKEEP_ROWS)}


def supported_maps(mosby: bool, shopkeep: bool) -> frozenset:
    return frozenset(area for area, enabled in ((OBSERVATORY, True), (SCIENTIST_SHOP, mosby), (SHOPKEEP, shopkeep)) if enabled)


def valid_token(value) -> bool:
    return isinstance(value, str) and party.REQUEST_ID.fullmatch(value) is not None


def available_ordinals(profile, row: str, progress: dict) -> tuple[int, ...]:
    ordinals = tuple(profile.rows[row][1])
    if profile.id != MOSBY or row != QUESTIONS:
        return ordinals
    return tuple(ordinal for ordinal in ordinals if (ordinal != 5 or progress["topic_eather_drill"] >= 1)
                 and (ordinal != 6 or progress["topic_drill_data"] >= 1 and progress["stone_maze_readings"] < 1))


def parse_progress(fields, pawn: str) -> dict:
    if not fields or len(fields) != 10 or tuple(fields[1:3]) != (SCIENTIST_SHOP, pawn) or fields[-1] not in ("read", *EFFECTS.values()):
        raise ValueError("Invalid stock Mosby progress frame")
    result = {name: None if name == "quest_step" and value == "-" else int(value) for name, value in zip(PROGRESS_KEYS, fields[3:9])}
    if (result["quest_step"] not in (None, 1, 2, -10)
            or any(value is not None and not -(2**31) <= value < 2**31 for value in result.values())):
        raise ValueError("Invalid stock Mosby save state")
    return {**result, "effect": fields[-1]}


def parse_shopkeep_progress(fields, pawn: str) -> dict:
    if (not fields or len(fields) != 7 or tuple(fields[1:3]) != (SHOPKEEP, pawn)
            or fields[-1] not in ("read", "opened-first", "opened-repeat", "cash-granted")):
        raise ValueError("Invalid stock ShopKeep progress frame")
    result = dict(zip(SHOPKEEP_PROGRESS, map(int, fields[3:6])))
    if (any(not 0 <= value < 2**31 for value in result.values())
            or fields[-1] == "opened-first" and result["meet"] != 1
            or fields[-1] == "opened-repeat" and result["meet"] < 1):
        raise ValueError("Invalid stock ShopKeep reception state")
    return {**result, "effect": fields[-1]}


def parse_stock(frame, lines, choices, progress_fields=None) -> dict:
    profile = PROFILES.get(frame[1]) if len(frame) == 6 else None
    if (not profile or tuple(frame[2:4]) != (profile.npc, profile.controller) or not frame[4].startswith("Player_FP_C ")
            or not frame[5] or len(frame[5]) > 512):
        raise ValueError("Invalid stock dialogue identity")
    progress = (parse_progress(progress_fields, frame[4]) if profile.id == MOSBY
                else parse_shopkeep_progress(progress_fields, frame[4]) if profile.id == SHOPKEEP_PROFILE else None)
    if progress is None and progress_fields:
        raise ValueError("Unexpected stock dialogue progress")
    result = {name: {"lines": {}, "choices": {}} for name in profile.rows}
    for entries, field in ((lines, "lines"), (choices, "choices")):
        for fields in entries:
            if (len(fields) != 4 or fields[1] not in profile.rows or fields[2] not in ("1", "2", "3", "4", "5", "6")
                    or not 0 < len(fields[3]) <= 1024):
                raise ValueError("Invalid stock dialogue text row")
            index = int(fields[2])
            target = result[fields[1]][field]
            if index in target:
                raise ValueError("Duplicate stock dialogue text row")
            target[index] = fields[3]
    for name, (count, _) in profile.rows.items():
        row = result[name]
        ordinals = available_ordinals(profile, name, progress)
        if row["lines"].keys() != set(range(1, count + 1)) or row["choices"].keys() != set(ordinals):
            raise ValueError("Incomplete stock dialogue row")
        row["lines"] = [row["lines"][line] for line in range(1, count + 1)]
        row["choices"] = [{"ordinal": ordinal, "text": row["choices"][ordinal]} for ordinal in ordinals]
    stock = {"map": profile.map, "npc": profile.npc, "controller": profile.controller, "pawn": frame[4], "connection": frame[5], "rows": result}
    if progress is not None:
        stock["progress"] = progress
    return stock


def render_text(reply, profile: Profile, row: str | None) -> tuple[str, ...]:
    lines, choices = reply.get("lines"), reply.get("choices")
    if row is None and lines == choices == []:
        return ()
    if row not in profile.rows or not isinstance(lines, list) or not isinstance(choices, list):
        raise ValueError("The host did not provide verified stock dialogue text")
    line_count, _ = profile.rows[row]
    progress = reply.get("progress", {})
    if profile.id == MOSBY and (not isinstance(progress, dict)
            or any(type(progress.get(key)) is not int for key in PROGRESS_KEYS[:3])):
        raise ValueError("The host did not provide the authoritative stock topic state")
    allowed = available_ordinals(profile, row, progress)
    if (len(lines) != line_count or not all(isinstance(choice, dict) for choice in choices)
            or [choice.get("ordinal") for choice in choices] != list(allowed)):
        raise ValueError("The host did not provide verified stock dialogue choices")
    result = (*lines, *(choice.get("text") for choice in choices))
    if any(not isinstance(value, str) or not 0 < len(value.encode()) <= 1024 or any(char in value for char in "\r\n\t\0")
           for value in result):
        raise ValueError("The host did not provide bounded stock dialogue text")
    return (",".join(map(str, allowed)), *result) if profile.id == MOSBY else result


@dataclass(frozen=True)
class Session:
    scope: tuple
    token: str
    row: str
    allowed: tuple[int, ...]
    expires: float


@dataclass(frozen=True)
class Receipt:
    operation: str
    request: tuple[str, str]
    scope: tuple
    expires: float
    reply: dict


class DialogueSessions:
    def __init__(self, *, mosby=True, shopkeep=False):
        self.sessions: dict[str, Session] = {}
        self.receipts: dict[str, Receipt] = {}
        self.maps = supported_maps(mosby, shopkeep)

    def forget(self, character: str) -> None:
        self.sessions.pop(character, None)
        self.receipts.pop(character, None)

    def _scope(self, host, character: str, now: float) -> tuple:
        recipients = host.native_recipients()
        for previous in self.sessions.keys() | self.receipts.keys():
            if not recipients.get(previous, {}).get("connected"):
                self.forget(previous)
        player, member = host.party["players"].get(character), recipients.get(character)
        area = host.last_game.get("map")
        if (not host.native_profile or not host.native_zones or not host.native_guest or host.transitioning or host.stopping
                or host.native_checkpoint_unsafe or host.party["wipe"] or character in host.party["pending_spawns"]
                or not host.zone_epoch or host.last_game.get("state") != "hosting" or area not in self.maps
                or now - host.last_seen > 3 or character == "host" or not player or player["downed"]
                or not member or not member.get("connected") or not member.get("pawn")):
            raise party.RuleError("native_dialogue_guest_unavailable")
        return host.control.generation, host.zone_epoch, area, member["pawn"]

    @staticmethod
    async def _read(host, bridge, profile: Profile, pawn: str, *, command=READ, arguments=()) -> dict:
        snapshot = await bridge.command(command, pawn, *arguments, timeout=4 if command == READ else 7)
        stock = snapshot.get("native_dialogue_stock", {})
        if (snapshot.get("state"), snapshot.get("map"), stock.get("map"), stock.get("npc"),
                stock.get("controller"), stock.get("pawn")) != (
                "hosting", profile.map, profile.map, profile.npc, profile.controller, pawn):
            raise RuntimeError("The stock dialogue did not verify the exact local NPC and enrolled pawn")
        if profile.id in (MOSBY, SHOPKEEP_PROFILE) and (
            not stock.get("progress") or command == READ and stock["progress"].get("effect") != "read"
        ):
            raise RuntimeError("The stock dialogue did not verify its canonical host state")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return stock

    async def approach(self, host, bridge, character: str) -> dict:
        if not host.fixtures:
            raise party.RuleError("native_dialogue_approach_unavailable")
        scope = self._scope(host, character, time.monotonic())
        profile = PROFILES[scope[2]]
        snapshot = await bridge.command(APPROACH, scope[-1], timeout=5)
        if (snapshot.get("state"), snapshot.get("map")) != ("hosting", profile.map):
            raise RuntimeError("The stock dialogue approach left its qualified zone")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return {"native_dialogue_approach": {"pawn": scope[-1], "npc": profile.npc}}

    async def _commit(self, host, bridge, character, scope, stock, row, ordinal):
        effect = EFFECTS[(row, ordinal)]
        before = stock["progress"]
        issue = effect == "stone_maze_issued"
        if issue:
            if (before["topic_eather_drill"] < 1 or before["topic_drill_data"] < 1 or before["stone_maze_readings"] >= 1):
                raise party.RuleError("native_dialogue_choice_unavailable")
            if before["quest_step"] is not None:
                return stock, "stone_maze_already_present", None
            if MOSBY_QUEST in host.party.get("native_quests", {}):
                raise RuntimeError("The checkpointed Stone Maze contract has no matching stock host quest")
        elif before[effect] >= 1:
            return stock, "topic_already_known", None
        host.transitioning = True
        try:
            await host.checkpoint_native(bridge)
            member = host.native_recipients().get(character, {})
            if (not member.get("connected") or member.get("pawn") != scope[3] or host.stopping
                    or (host.control.generation, host.zone_epoch, host.last_game.get("map")) != scope[:3]):
                raise party.RuleError("native_dialogue_scope_changed")
            host.native_checkpoint_unsafe = True
            expected = ("-" if before[key] is None else str(before[key]) for key in PROGRESS_KEYS)
            try:
                observed = await self._read(host, bridge, PROFILES[SCIENTIST_SHOP], scope[3], command=COMMIT,
                                            arguments=(row, str(ordinal), scope[4], *expected))
            except RuntimeError as error:
                if str(error).startswith(("native_dialogue_", "stock_wire_quest_")):
                    host.native_checkpoint_unsafe = False
                    raise party.RuleError(str(error)) from error
                raise
            after = observed["progress"]
            expected = {**before, "effect": effect, "quest_step" if issue else effect: 1}
            if observed["connection"] != scope[4] or after != expected:
                raise RuntimeError("The canonical host save did not verify the exact stock dialogue change")
            candidate = None
            if issue:
                candidate = party.load_party(host.party)
                candidate.setdefault("native_quests", {})[MOSBY_QUEST] = {"quest_id": MOSBY_QUEST, "step": 1, "actor": character}
            revision = await host.checkpoint_native(bridge, candidate)
            if candidate is not None:
                host.party = candidate
            host.native_checkpoint_unsafe = False
            host.control.event("native_dialogue_committed", revision=revision, effect=effect, character=character)
            return observed, effect, revision
        except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
            if host.native_checkpoint_unsafe:
                host.stopping = True
            raise
        finally:
            host.transitioning = False

    async def _open_shopkeep(self, host, bridge, character, scope, stock):
        before = stock["progress"]
        first = before["meet"] == 0
        marker = "opened-first" if first else "opened-repeat"
        host.transitioning = True
        try:
            await host.checkpoint_native(bridge)
            member = host.native_recipients().get(character, {})
            if (not member.get("connected") or member.get("pawn") != scope[3] or host.stopping
                    or (host.control.generation, host.zone_epoch, host.last_game.get("map")) != scope[:3]):
                raise party.RuleError("native_dialogue_scope_changed")
            host.native_checkpoint_unsafe = True
            try:
                observed = await self._read(host, bridge, PROFILES[SHOPKEEP], scope[3], command=SHOPKEEP_OPEN,
                                            arguments=(scope[4], *(str(before[key]) for key in SHOPKEEP_PROGRESS)))
            except RuntimeError as error:
                if str(error).startswith("native_dialogue_"):
                    host.native_checkpoint_unsafe = False
                    raise party.RuleError(str(error)) from error
                raise
            if (observed["connection"] != scope[4]
                    or observed["progress"] != dict(before, meet=max(before["meet"], 1), effect=marker)):
                raise RuntimeError("The stock ShopKeep first-meet flag did not verify")
            revision = await host.checkpoint_native(bridge)
            host.native_checkpoint_unsafe = False
            host.control.event("native_dialogue_committed", revision=revision, effect=marker, character=character)
            return observed, SHOPKEEP_FIRST if first else SHOPKEEP_ROOT, marker, revision
        except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
            if host.native_checkpoint_unsafe:
                host.stopping = True
            raise
        finally:
            host.transitioning = False

    async def act(self, host, bridge, character: str, action: str, target: str | None, operation_id: str) -> dict:
        if action not in (OPEN, CHOOSE) or not isinstance(target, str) or not valid_token(operation_id):
            raise party.RuleError("native_dialogue_request_invalid")
        now = time.monotonic()
        scope = self._scope(host, character, now)
        profile = PROFILES[scope[2]]
        stock = await self._read(host, bridge, profile, scope[-1])
        now = time.monotonic()
        if self._scope(host, character, now) != scope:
            raise party.RuleError("native_dialogue_scope_changed")
        scope = (*scope, stock["connection"])
        previous = self.receipts.get(character)
        if previous and previous.operation == operation_id and previous.scope == scope and previous.expires > now:
            if previous.request != (action, target):
                raise party.RuleError("operation_id_conflict")
            return previous.reply
        effect, revision = None, None
        if action == OPEN:
            if target != profile.id:
                raise party.RuleError("native_dialogue_profile_unavailable")
            destination = profile.root
            if profile.id == SHOPKEEP_PROFILE:
                stock, destination, effect, revision = await self._open_shopkeep(host, bridge, character, scope, stock)
                now = time.monotonic()
        else:
            token, separator, choice = target.partition("@")
            session = self.sessions.get(character)
            if (not separator or not valid_token(token) or not session or session.token != token
                    or session.scope != scope or session.expires <= now):
                raise party.RuleError("native_dialogue_choice_expired")
            if (choice not in ORDINALS or int(choice) not in session.allowed
                    or int(choice) not in available_ordinals(profile, session.row, stock.get("progress"))):
                raise party.RuleError("native_dialogue_choice_unavailable")
            ordinal = int(choice)
            destination = profile.rows[session.row][1][ordinal]
            if profile.id == MOSBY and (session.row, ordinal) in EFFECTS:
                stock, effect, revision = await self._commit(host, bridge, character, scope, stock, session.row, ordinal)
                now = time.monotonic()
            elif profile.id == SHOPKEEP_PROFILE:
                observed = await self._read(host, bridge, profile, scope[-2], command=SHOPKEEP_CASH,
                                            arguments=(session.row, str(ordinal), scope[-1]))
                if (observed["connection"] != scope[-1] or observed["progress"] != dict(stock["progress"], effect="cash-granted")
                        or self._scope(host, character, time.monotonic()) != scope[:4]):
                    raise RuntimeError("The stock ShopKeep cash reception did not verify")
                stock, effect = observed, "cash-granted"
                host.control.event("native_shopkeep_cash_granted", character_id=character, row=session.row, ordinal=ordinal)
        report = {"profile": profile.id, "map": profile.map, "npc": profile.npc, "row": destination, "token": None,
                  "state": "closed" if destination is None else "open", "lines": [], "choices": []}
        if profile.id == MOSBY:
            report["progress"] = {key: stock["progress"][key] for key in PROGRESS_KEYS}
        elif profile.id == SHOPKEEP_PROFILE:
            report["progress"] = {key: stock["progress"][key] for key in SHOPKEEP_PROGRESS}
        if effect:
            report.update(effect=effect, revision=revision)
        expires = now + SESSION_SECONDS
        if destination is None:
            self.sessions.pop(character, None)
        else:
            token = uuid.uuid4().hex
            report.update(stock["rows"][destination], token=token)
            self.sessions[character] = Session(scope, token, destination,
                                               tuple(choice["ordinal"] for choice in report["choices"]), expires)
        result = {"native_dialogue": report}
        self.receipts[character] = Receipt(operation_id, (action, target), scope, expires, result)
        return result


class DialogueView:
    def __init__(self, bridge, control, *, mosby=True, shopkeep=False, shopkeep_view=False):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.pending = None
        self.maps = supported_maps(mosby, shopkeep)
        self.shopkeep_view = shopkeep and shopkeep_view

    async def sync(self, snapshot, epoch, active, action):
        events = self.bridge.native_dialogue_requests()
        area = snapshot.get("map")
        if not active or not epoch or area not in self.maps:
            self.pending = None
            return snapshot
        profile = PROFILES[area]
        now = time.monotonic()
        if events:
            self.pending = (epoch, area, now, events[-1])
        if not self.pending:
            return snapshot
        if self.pending[:2] != (epoch, area) or now - self.pending[2] > 2:
            self.pending = None
            return snapshot
        sequence, pawn, token, choice = self.pending[3]
        shown = snapshot.get("native_dialogue_view", {})
        if (sequence <= self.latest or pawn != snapshot.get("pawns", {}).get("local", {}).get("name")
                or (shown.get("state"), shown.get("sequence"), shown.get("pawn"), shown.get("token")) !=
                ("pending", sequence, pawn, token)):
            return snapshot
        self.pending = None
        self.latest = sequence
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:{profile.id}:{sequence}").hex
        try:
            verb, target = (OPEN, profile.id) if token == "-" else (CHOOSE, f"{token}@{choice}")
            reply = (await action(verb, operation, target)).get("native_dialogue", {})
            row, next_token = reply.get("row"), reply.get("token")
            if (tuple(reply.get(field) for field in ("profile", "map", "npc")) != (profile.id, profile.map, profile.npc)
                    or (row is None and (next_token is not None or reply.get("state") != "closed"))
                    or (row is not None and (row not in profile.rows or not valid_token(next_token) or reply.get("state") != "open"))):
                raise RuntimeError("The host did not verify the current stock dialogue response")
            text = render_text(reply, profile, row)
            cash_handoff = (self.shopkeep_view and profile.id == SHOPKEEP_PROFILE and verb == CHOOSE
                            and reply.get("effect") == "cash-granted" and row == SHOPKEEP_ROOT)
            if cash_handoff:
                rendered = await self.bridge.command(SHOPKEEP_VIEW, str(sequence), token, timeout=3)
                proof = rendered.get("native_shop_view", {})
                if tuple(proof.get(field) for field in ("pawn", "sequence", "map", "actor", "state")) != (
                        pawn, sequence, SHOPKEEP, SHOPKEEP_COUNTER, "pending"):
                    raise RuntimeError("The ShopKeep dialogue did not open its exact local merchant")
            else:
                rendered = await self.bridge.command(RENDER, str(sequence), row or "-", next_token or "-", *text, timeout=2)
                proof = rendered.get("native_dialogue_view", {})
                if tuple(proof.get(field) for field in ("pawn", "sequence", "row", "token", "state")) != (
                        pawn, sequence, row or "-", next_token or "-", "open" if row else "closed"):
                    raise RuntimeError("The client did not verify its exact stock dialogue menu")
            status = {"profile": profile.id, "sequence": sequence, "action": verb, "row": row}
            if profile.id == SHOPKEEP_PROFILE and reply.get("effect") in ("opened-first", "opened-repeat", "cash-granted"):
                status["effect"] = reply["effect"]
            self.control.event("native_dialogue_interaction", **status)
            return rendered
        except (OSError, ValueError, RuntimeError) as error:
            self.control.event("native_dialogue_rejected", sequence=sequence, reason=str(error))
            try:
                return await self.bridge.command(RENDER, str(sequence), "refused", "-", timeout=2)
            except (OSError, ValueError, RuntimeError):
                pass
        return snapshot
