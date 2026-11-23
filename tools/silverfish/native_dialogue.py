"""Read scoped stock dialogue and authorize each guest's own choices."""

import asyncio
from dataclasses import dataclass
import time
import uuid

from . import native_sewer_barter, party
from .travel import BOROUGH, CISTERN, OBSERVATORY, SCIENTIST_SHOP, SHOPKEEP


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
READINGS, EQUIPMENT = "Mosby_EatherReadings_01", "Mosby_DrillQuest_02"
READINGS_OPEN = "native-dialogue-mosby-readings-open"
PROTOTYPE_QUEST = "Clt_DrillHead"
MOSBY_ROWS = {GREET: (1, {1: None, 4: QUESTIONS}),
              QUESTIONS: (1, {1: GREET, 3: ANOMALIES, 5: DRILL, 6: OFFER, 7: EQUIPMENT}),
              ANOMALIES: (4, {1: QUESTIONS}), DRILL: (3, {1: QUESTIONS}), OFFER: (4, {1: None, 2: None}),
              READINGS: (6, {1: EQUIPMENT, 2: GREET}), EQUIPMENT: (5, {1: QUESTIONS, 2: None})}
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
MAMA = "mama"
MAMA_LEVEL = BOROUGH + ".Borough:PersistentLevel."
MAMA_NPC = "NPC_Mama_C " + MAMA_LEVEL + "NPC_Mama_C_1"
MAMA_CONTROLLER = "Dialogue_Controler_BP_C " + MAMA_LEVEL + "Dialogue_Controler_BP_C_1"
MAMA_FIRST, MAMA_QUESTIONS, MAMA_ROOT = "Momma_Intro", "Questions_Mamma", "Momma_Root"
MAMA_ROWS = {MAMA_FIRST: (2, {3: MAMA_QUESTIONS}), MAMA_QUESTIONS: (1, {1: MAMA_ROOT}),
             MAMA_ROOT: (1, {1: None, 3: MAMA_QUESTIONS, 5: None})}
MAMA_OPEN, MAMA_TRADE = "native-dialogue-mama-open", "native-dialogue-mama-trade"
SEWER = "sewer"
SEWER_LEVEL = CISTERN + ".Cistern_P:PersistentLevel."
SEWER_NPC = "NPC_SewerTrader_C " + SEWER_LEVEL + "NPC_SewerTrader_C_1"
SEWER_CONTROLLER = "Dialogue_Controler_BP_C " + SEWER_LEVEL + "Dialogue_Controler_BP_C_1"
SEWER_FIRST, SEWER_ROOT, SEWER_OFFERS = "FirstMeet", "Greet", "Sewer_Trade"
SEWER_ROWS = {SEWER_FIRST: (3, {1: None, 2: None}), SEWER_ROOT: (1, {1: None, 2: None})}
SEWER_OPEN, SEWER_TRADE = "native-dialogue-sewer-open", "native-dialogue-sewer-trade"
SEWER_VIEW = "native-dialogue-sewer-offers"
SEWER_FIXTURE = "native-dialogue-sewer-host-fixture"
SEWER_ORDINALS = tuple(map(str, range(27)))
MEET_PROFILES = {SHOPKEEP_PROFILE: (SHOPKEEP_OPEN, SHOPKEEP_FIRST), MAMA: (MAMA_OPEN, MAMA_FIRST),
                 SEWER: (SEWER_OPEN, SEWER_FIRST)}
MAMA_PROGRESS = (*SHOPKEEP_PROGRESS, "rev_orders")
PROGRESS_KEYS = ("topic_eather_drill", "topic_drill_data", "stone_maze_readings", "quest_step", "cash", "xp")
PROTOTYPE_KEYS = ("topic_micro_drill", "mosby_drill_test", "topic_drill_lab", "prototype_step")
EFFECTS = {(QUESTIONS, 3): "topic_eather_drill", (QUESTIONS, 5): "topic_drill_data", (OFFER, 2): "stone_maze_issued",
           (EQUIPMENT, 1): "topic_drill_lab", (EQUIPMENT, 2): "prototype_drill_issued"}
ISSUES = {"stone_maze_issued": (MOSBY_QUEST, "quest_step", "stone_maze_already_present"),
          "prototype_drill_issued": (PROTOTYPE_QUEST, "prototype_step", "prototype_drill_already_present")}
ORDINALS = tuple(str(index) for index in sorted({
    index for _, choices in (*ROWS.values(), *MOSBY_ROWS.values(), *SHOPKEEP_ROWS.values(), *MAMA_ROWS.values()) for index in choices
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
            SHOPKEEP: Profile(SHOPKEEP_PROFILE, SHOPKEEP, SHOPKEEP_NPC, SHOPKEEP_CONTROLLER, SHOPKEEP_ROOT, SHOPKEEP_ROWS),
            BOROUGH: Profile(MAMA, BOROUGH, MAMA_NPC, MAMA_CONTROLLER, MAMA_ROOT, MAMA_ROWS),
            CISTERN: Profile(SEWER, CISTERN, SEWER_NPC, SEWER_CONTROLLER, SEWER_ROOT, SEWER_ROWS)}


def supported_maps(mosby: bool, shopkeep: bool, mama: bool, sewer: bool = False) -> frozenset:
    return frozenset(area for area, enabled in (
        (OBSERVATORY, True), (SCIENTIST_SHOP, mosby), (SHOPKEEP, shopkeep), (BOROUGH, mama), (CISTERN, sewer),
    ) if enabled)


def valid_token(value) -> bool:
    return isinstance(value, str) and party.REQUEST_ID.fullmatch(value) is not None


def view_row(profile: Profile, row: str) -> bool:
    return row in profile.rows or (profile.id, row) == (SEWER, SEWER_OFFERS)


def sewer_menu_choices(frame: dict) -> list[dict]:
    if not isinstance(frame, dict) or frame.get("map") != CISTERN or not isinstance(frame.get("offers"), list):
        raise RuntimeError("The host did not verify the Sewer Thing's trade offers")
    choices = []
    previous = 0
    for offer in frame["offers"]:
        if not isinstance(offer, dict):
            raise RuntimeError("The host did not verify an exact Sewer Thing offer")
        ordinal, label = offer.get("ordinal"), offer.get("label")
        if (type(ordinal) is not int or not previous < ordinal <= 26 or not isinstance(label, str)
                or not 0 < len(label.encode()) <= 900 or any(char in label for char in "\r\n\t\0")
                or type(offer.get("affordable")) is not bool or type(offer.get("hidden")) is not bool):
            raise RuntimeError("The host did not verify an exact Sewer Thing offer")
        previous = ordinal
        if offer.get("hidden") and not offer["affordable"]:
            continue
        if not offer["affordable"]:
            label = f"{label} (missing items)"
        choices.append({"ordinal": ordinal, "text": label})
    return [*choices, {"ordinal": 0, "text": "Close"}]


def available_ordinals(profile, row: str, progress: dict) -> tuple[int, ...]:
    ordinals = tuple(profile.rows[row][1])
    if profile.id == MAMA and row == MAMA_FIRST and progress["rev_orders"] >= 1:
        return ()
    if profile.id != MOSBY or row != QUESTIONS:
        return ordinals
    return tuple(ordinal for ordinal in ordinals if (ordinal != 5 or progress["topic_eather_drill"] >= 1)
                 and (ordinal != 6 or progress["topic_drill_data"] >= 1 and progress["stone_maze_readings"] < 1)
                 and (ordinal != 7 or progress.get("topic_micro_drill", 0) >= 1 and progress.get("mosby_drill_test", 0) < 1))


def stock_rows(profile: Profile, progress: dict | None) -> dict:
    if profile.id == MOSBY and "prototype_step" not in progress:
        return {name: row for name, row in profile.rows.items() if name not in (READINGS, EQUIPMENT)}
    return profile.rows


def parse_progress(fields, pawn: str) -> dict:
    if (not fields or len(fields) not in (10, 14) or tuple(fields[1:3]) != (SCIENTIST_SHOP, pawn)
            or fields[-1] not in ("read", *EFFECTS.values(), "readings_opened")
            or len(fields) == 10 and fields[-1] in ("readings_opened", "topic_drill_lab", "prototype_drill_issued")):
        raise ValueError("Invalid stock Mosby progress frame")
    steps = ("quest_step", "prototype_step")
    result = {name: None if name in steps and value == "-" else int(value)
              for name, value in zip((*PROGRESS_KEYS, *PROTOTYPE_KEYS), fields[3:-1])}
    if (any(result.get(step) not in (None, 1, 2, -10) for step in steps)
            or any(value is not None and not -(2**31) <= value < 2**31 for value in result.values())):
        raise ValueError("Invalid stock Mosby save state")
    return {**result, "effect": fields[-1]}


def parse_meet_progress(fields, pawn: str, profile: Profile) -> dict:
    grant = "cash-granted" if profile.id == SHOPKEEP_PROFILE else "trade-granted"
    keys = MAMA_PROGRESS if profile.id == MAMA else SHOPKEEP_PROGRESS
    if (not fields or len(fields) != 4 + len(keys) or tuple(fields[1:3]) != (profile.map, pawn)
            or fields[-1] not in ("read", "opened-first", "opened-repeat", grant)):
        raise ValueError("Invalid stock first-meeting progress frame")
    result = dict(zip(keys, map(int, fields[3:-1])))
    if (any(not 0 <= result[k] < 2**31 for k in SHOPKEEP_PROGRESS)
            or not -(2**31) <= result.get("rev_orders", 0) < 2**31
            or fields[-1] == "opened-first" and result["meet"] != 1
            or fields[-1] == "opened-repeat" and result["meet"] < 1):
        raise ValueError("Invalid stock first-meeting state")
    return {**result, "effect": fields[-1]}


def parse_stock(frame, lines, choices, progress_fields=None) -> dict:
    profile = PROFILES.get(frame[1]) if len(frame) == 6 else None
    if (not profile or tuple(frame[2:4]) != (profile.npc, profile.controller) or not frame[4].startswith("Player_FP_C ")
            or not frame[5] or len(frame[5]) > 512):
        raise ValueError("Invalid stock dialogue identity")
    progress = (parse_progress(progress_fields, frame[4]) if profile.id == MOSBY
                else parse_meet_progress(progress_fields, frame[4], profile) if profile.id in MEET_PROFILES else None)
    if progress is None and progress_fields:
        raise ValueError("Unexpected stock dialogue progress")
    rows = stock_rows(profile, progress)
    result = {name: {"lines": {}, "choices": {}} for name in rows}
    for entries, field in ((lines, "lines"), (choices, "choices")):
        for fields in entries:
            if (len(fields) != 4 or fields[1] not in rows or fields[2] not in ("1", "2", "3", "4", "5", "6", "7")
                    or not 0 < len(fields[3]) <= 1024):
                raise ValueError("Invalid stock dialogue text row")
            index = int(fields[2])
            target = result[fields[1]][field]
            if index in target:
                raise ValueError("Duplicate stock dialogue text row")
            target[index] = fields[3]
    for name, (count, _) in rows.items():
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
    if profile.id == MOSBY and (row not in stock_rows(profile, progress)
            or "prototype_step" in progress and any(type(progress.get(key)) is not int for key in PROTOTYPE_KEYS[:3])):
        raise ValueError("The host did not provide the authoritative stock Prototype Drill state")
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
    def __init__(self, *, mosby=True, shopkeep=False, mama=False, sewer=False):
        self.sessions: dict[str, Session] = {}
        self.receipts: dict[str, Receipt] = {}
        self.maps = supported_maps(mosby, shopkeep, mama, sewer)

    def forget(self, character: str) -> None:
        self.sessions.pop(character, None)
        self.receipts.pop(character, None)

    def member_scope(self, host, character: str) -> tuple:
        return self._scope(host, character, time.monotonic())

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
        if (profile.id == MOSBY or profile.id in MEET_PROFILES) and (
            not stock.get("progress") or command == READ and stock["progress"].get("effect") != "read"
        ):
            raise RuntimeError("The stock dialogue did not verify its canonical host state")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return stock

    async def approach(self, host, bridge, character: str) -> dict:
        if not host.fixtures or not isinstance(character, str):
            raise party.RuleError("native_dialogue_approach_unavailable")
        character, separator, destination = character.partition("@")
        scope = self._scope(host, character, time.monotonic())
        profile = PROFILES[scope[2]]
        if separator and (profile.map != CISTERN or destination != "exit"):
            raise party.RuleError("native_dialogue_approach_unavailable")
        arguments = (scope[-1], "exit") if separator else (scope[-1],)
        snapshot = await bridge.command(APPROACH, *arguments, timeout=5)
        if (snapshot.get("state"), snapshot.get("map")) != ("hosting", profile.map):
            raise RuntimeError("The stock dialogue approach left its qualified zone")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        return {"native_dialogue_approach": {"pawn": scope[-1], "npc": profile.npc}}

    async def _commit(self, host, bridge, character, scope, stock, row, ordinal):
        effect = EFFECTS[(row, ordinal)]
        before = stock["progress"]
        issue = ISSUES.get(effect)
        if effect == "stone_maze_issued":
            if (before["topic_eather_drill"] < 1 or before["topic_drill_data"] < 1 or before["stone_maze_readings"] >= 1):
                raise party.RuleError("native_dialogue_choice_unavailable")
        elif row == EQUIPMENT and (before.get("topic_micro_drill", 0) < 1 or before.get("mosby_drill_test", 0) >= 1):
            raise party.RuleError("native_dialogue_choice_unavailable")
        if issue:
            quest, step, replay = issue
            if before[step] is not None:
                return stock, replay, None
            if quest in host.party.get("native_quests", {}):
                raise RuntimeError("The checkpointed Mosby contract has no matching stock host quest")
        elif before[effect] >= 1:
            return stock, "topic_already_known", None
        expected = {**before, "effect": effect, (step if issue else effect): 1}
        return await self._mutate_mosby(host, bridge, character, scope, before, COMMIT,
                                        (row, str(ordinal)), expected, issue[0] if issue else None)

    async def _mutate_mosby(self, host, bridge, character, scope, before, command, arguments, expected, quest=None):
        host.transitioning = True
        try:
            await host.checkpoint_native(bridge)
            member = host.native_recipients().get(character, {})
            if (not member.get("connected") or member.get("pawn") != scope[3] or host.stopping
                    or (host.control.generation, host.zone_epoch, host.last_game.get("map")) != scope[:3]):
                raise party.RuleError("native_dialogue_scope_changed")
            host.native_checkpoint_unsafe = True
            fields = ("-" if before[key] is None else str(before[key])
                      for key in (*PROGRESS_KEYS, *PROTOTYPE_KEYS) if key in before)
            try:
                observed = await self._read(host, bridge, PROFILES[SCIENTIST_SHOP], scope[3], command=command,
                                            arguments=(*arguments, scope[4], *fields))
            except RuntimeError as error:
                if str(error).startswith(("native_dialogue_", "stock_wire_quest_")):
                    host.native_checkpoint_unsafe = False
                    raise party.RuleError(str(error)) from error
                raise
            if observed["connection"] != scope[4] or observed["progress"] != expected:
                raise RuntimeError("The canonical host save did not verify the exact stock dialogue change")
            candidate = None
            if quest:
                candidate = party.load_party(host.party)
                candidate.setdefault("native_quests", {})[quest] = {"quest_id": quest, "step": 1, "actor": character}
            revision = await host.checkpoint_native(bridge, candidate)
            if candidate is not None:
                host.party = candidate
            host.native_checkpoint_unsafe = False
            effect = expected["effect"]
            host.control.event("native_dialogue_committed", revision=revision, effect=effect, character=character)
            return observed, effect, revision
        except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
            if host.native_checkpoint_unsafe:
                host.stopping = True
            raise
        finally:
            host.transitioning = False

    async def _open_meet(self, host, bridge, character, scope, stock, profile):
        before = stock["progress"]
        first = before["meet"] == 0
        marker = "opened-first" if first else "opened-repeat"
        command, first_row = MEET_PROFILES[profile.id]
        keys = MAMA_PROGRESS if profile.id == MAMA else SHOPKEEP_PROGRESS
        if first and profile.id == MAMA and not available_ordinals(profile, MAMA_FIRST, before):
            raise party.RuleError("native_dialogue_mama_intro_questions_hidden")
        host.transitioning = True
        try:
            member = host.native_recipients().get(character, {})
            if (not member.get("connected") or member.get("pawn") != scope[3] or host.stopping
                    or (host.control.generation, host.zone_epoch, host.last_game.get("map")) != scope[:3]):
                raise party.RuleError("native_dialogue_scope_changed")
            host.native_checkpoint_unsafe = True
            try:
                observed = await self._read(host, bridge, profile, scope[3], command=command,
                                            arguments=(scope[4], *(str(before[key]) for key in keys)))
            except RuntimeError as error:
                if str(error).startswith("native_dialogue_"):
                    host.native_checkpoint_unsafe = False
                    raise party.RuleError(str(error)) from error
                raise
            if (observed["connection"] != scope[4]
                    or observed["progress"] != dict(before, meet=max(before["meet"], 1), effect=marker)):
                raise RuntimeError("The stock first-meet flag did not verify")
            if first:
                host.pending_host_save = bool(host.pending_host_save)
            host.native_checkpoint_unsafe = False
            host.control.event("native_dialogue_accepted", effect=marker, character=character)
            return observed, first_row if first else profile.root, marker
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
        sewer_offers = None
        barter_result = {}
        if action == OPEN:
            if target != profile.id:
                raise party.RuleError("native_dialogue_profile_unavailable")
            destination = profile.root
            if profile.id in MEET_PROFILES:
                stock, destination, effect = await self._open_meet(host, bridge, character, scope, stock, profile)
                now = time.monotonic()
            elif profile.id == MOSBY:
                before = stock["progress"]
                if ("prototype_step" in before and before["quest_step"] == -10
                        and before["stone_maze_readings"] == 1 and before["mosby_drill_test"] < 1):
                    expected = dict(before, stone_maze_readings=2, topic_micro_drill=1, effect="readings_opened")
                    stock, effect, revision = await self._mutate_mosby(
                        host, bridge, character, scope, before, READINGS_OPEN, (), expected)
                    destination, now = READINGS, time.monotonic()
        else:
            token, separator, choice = target.partition("@")
            session = self.sessions.get(character)
            if (not separator or not valid_token(token) or not session or session.token != token
                    or session.scope != scope or session.expires <= now):
                raise party.RuleError("native_dialogue_choice_expired")
            if session.row == SEWER_OFFERS and profile.id == SEWER:
                if choice not in SEWER_ORDINALS or int(choice) not in session.allowed:
                    raise party.RuleError("native_dialogue_choice_unavailable")
                destination = None
                if choice != "0":
                    barter_result = await native_sewer_barter.exchange(host, bridge, character, choice, operation_id)
                    sewer_offers = (await native_sewer_barter.offers(host, bridge, character))["native_sewer_offers"]
                    destination = SEWER_OFFERS
            elif (choice not in ORDINALS or int(choice) not in session.allowed
                    or int(choice) not in available_ordinals(profile, session.row, stock.get("progress"))):
                raise party.RuleError("native_dialogue_choice_unavailable")
            else:
                destination = profile.rows[session.row][1][int(choice)]
            ordinal = int(choice)
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
            elif profile.id == MAMA and (session.row, ordinal) == (MAMA_ROOT, 5):
                observed = await self._read(host, bridge, profile, scope[-2], command=MAMA_TRADE,
                                            arguments=(session.row, str(ordinal), scope[-1]))
                if (observed["connection"] != scope[-1]
                        or observed["progress"] != dict(stock["progress"], effect="trade-granted")
                        or self._scope(host, character, time.monotonic()) != scope[:4]):
                    raise RuntimeError("Mama's stock trade dialogue did not verify")
                stock, effect = observed, "trade-granted"
                host.control.event("native_borough_mama_trade_granted", character_id=character, row=session.row, ordinal=ordinal)
            elif profile.id == SEWER and session.row in SEWER_ROWS and ordinal == 1:
                observed = await self._read(host, bridge, profile, scope[-2], command=SEWER_TRADE,
                                            arguments=(session.row, str(ordinal), scope[-1]))
                if (observed["connection"] != scope[-1]
                        or observed["progress"] != dict(stock["progress"], effect="trade-granted")
                        or self._scope(host, character, time.monotonic()) != scope[:4]):
                    raise RuntimeError("The Sewer Thing's trade dialogue did not verify")
                stock, effect, destination = observed, "trade-granted", SEWER_OFFERS
                sewer_offers = (await native_sewer_barter.offers(host, bridge, character))["native_sewer_offers"]
        report = {"profile": profile.id, "map": profile.map, "npc": profile.npc, "row": destination, "token": None,
                  "state": "closed" if destination is None else "open", "lines": [], "choices": []}
        if profile.id == MOSBY:
            report["progress"] = {key: value for key, value in stock["progress"].items() if key != "effect"}
        elif profile.id in MEET_PROFILES:
            keys = MAMA_PROGRESS if profile.id == MAMA else SHOPKEEP_PROGRESS
            report["progress"] = {key: stock["progress"][key] for key in keys}
        if effect:
            report.update(effect=effect, revision=revision)
        expires = now + SESSION_SECONDS
        if destination is None:
            self.sessions.pop(character, None)
        else:
            token = uuid.uuid4().hex
            if destination == SEWER_OFFERS:
                report.update(token=token, choices=sewer_menu_choices(sewer_offers), offers=sewer_offers["offers"])
            else:
                report.update(stock["rows"][destination], token=token)
            self.sessions[character] = Session(scope, token, destination,
                                               tuple(choice["ordinal"] for choice in report["choices"]), expires)
        result = {"native_dialogue": report, **barter_result}
        self.receipts[character] = Receipt(operation_id, (action, target), scope, expires, result)
        return result


class DialogueView:
    def __init__(self, bridge, control, *, mosby=True, shopkeep=False, shopkeep_view=False, mama=False, sewer=False):
        self.bridge, self.control = bridge, control
        self.latest = 0
        self.pending = None
        self.maps = supported_maps(mosby, shopkeep, mama, sewer)
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
            result = await action(verb, operation, target)
            reply = result.get("native_dialogue", {})
            row, next_token = reply.get("row"), reply.get("token")
            if (tuple(reply.get(field) for field in ("profile", "map", "npc")) != (profile.id, profile.map, profile.npc)
                    or (row is None and (next_token is not None or reply.get("state") != "closed"))
                    or (row is not None and (not view_row(profile, row) or not valid_token(next_token) or reply.get("state") != "open"))):
                raise RuntimeError("The host did not verify the current stock dialogue response")
            text = () if row == SEWER_OFFERS else render_text(reply, profile, row)
            cash_handoff = (self.shopkeep_view and profile.id == SHOPKEEP_PROFILE and verb == CHOOSE
                            and reply.get("effect") == "cash-granted" and row == SHOPKEEP_ROOT)
            if cash_handoff:
                rendered = await self.bridge.command(SHOPKEEP_VIEW, str(sequence), token, timeout=3)
                proof = rendered.get("native_shop_view", {})
                if tuple(proof.get(field) for field in ("pawn", "sequence", "map", "actor", "state")) != (
                        pawn, sequence, SHOPKEEP, SHOPKEEP_COUNTER, "pending"):
                    raise RuntimeError("The ShopKeep dialogue did not open its exact local merchant")
            else:
                if row == SEWER_OFFERS:
                    offers = reply.get("offers")
                    choices = sewer_menu_choices({"map": CISTERN, "offers": offers})
                    message = tuple(value for choice in choices for value in (str(choice["ordinal"]), choice["text"]))
                    recipes = native_sewer_barter.projection_arguments(offers)
                    rendered = await self.bridge.command(SEWER_VIEW, str(sequence), next_token, *recipes, *message, timeout=3)
                else:
                    rendered = await self.bridge.command(RENDER, str(sequence), row or "-", next_token or "-", *text, timeout=2)
                proof = rendered.get("native_dialogue_view", {})
                if tuple(proof.get(field) for field in ("pawn", "sequence", "row", "token", "state")) != (
                        pawn, sequence, row or "-", next_token or "-", "open" if row else "closed"):
                    raise RuntimeError("The client did not verify its exact stock dialogue menu")
            status = {"profile": profile.id, "sequence": sequence, "action": verb, "row": row}
            if profile.id in MEET_PROFILES and reply.get("effect") in ("opened-first", "opened-repeat", "cash-granted", "trade-granted"):
                status["effect"] = reply["effect"]
            self.control.event("native_dialogue_interaction", **status)
            if profile.id == SEWER and "native_sewer_trade" in result:
                self.control.event("native_sewer_trade_interaction", sequence=sequence, operation_id=operation,
                                   native_sewer_trade=result["native_sewer_trade"])
            return rendered
        except (OSError, ValueError, RuntimeError) as error:
            self.control.event("native_dialogue_rejected", sequence=sequence, reason=str(error))
            try:
                return await self.bridge.command(RENDER, str(sequence), "refused", "-", timeout=2)
            except (OSError, ValueError, RuntimeError):
                pass
        return snapshot
