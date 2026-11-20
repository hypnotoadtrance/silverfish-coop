"""Offer the exact Missing Contact handoff on the host's stock Journal."""

from contextlib import suppress
from dataclasses import dataclass
import re
import time
import uuid

from . import native_host_dig_live as dig, party
from .native_computer_board import MISSING_CONTACT
from .native_downed_markers import character_id
from .native_gold import valid_guid


JOURNAL = "native_package_handoff_journal"
INTENT = "native_package_handoff_intent"
JOURNAL_ROW = "native-package-handoff-journal"
INTENT_ROW = "native-package-handoff-intent"
FIXTURE = "native-package-handoff-journal-fixture"
_OFFER = "native-package-handoff-offer"
_CLEAR = "native-package-handoff-clear"
_RESULT = "native-package-handoff-result"
_LEASE = 5.0
_PHASES = {"idle", "choosing", "submitting", "transferred", "refused"}
_FIXTURES = {"open", "close", "handoff-open", "handoff-next", "handoff-confirm", "handoff-back"}


def parse(fields: list[str], game: dict) -> None:
    tag = fields[0]
    area = game.get("map")
    if (len(fields) != 5 or game.get("mode") != "host" or game.get("state") != "hosting"
            or area not in dig._HANDOFF_MAPS or fields[1] != area):
        raise ValueError("Invalid package handoff Journal scope")
    pawn = fields[2]
    leaf = area.rsplit("/", 1)[1]
    prefix = f"Player_FP_C {area}.{leaf}:PersistentLevel.Player_FP_C_"
    if not re.fullmatch(re.escape(prefix) + r"[0-9]{1,10}", pawn):
        raise ValueError("Invalid package handoff Journal pawn")
    if tag == JOURNAL_ROW:
        state, token = fields[3:]
        if (JOURNAL in game or state not in _PHASES or token != "-" and not party.REQUEST_ID.fullmatch(token)
                or token == "-" and state in ("choosing", "submitting")):
            raise ValueError("Invalid package handoff Journal state")
        game[JOURNAL] = dict(map=area, pawn=pawn, state=state, offer_id=None if token == "-" else token)
    else:
        token, recipient = fields[3:]
        view = game.get(JOURNAL, {})
        if (tag != INTENT_ROW or INTENT in game or not character_id(recipient) or recipient == "host"
                or (view.get("pawn"), view.get("state"), view.get("offer_id")) != (pawn, "submitting", token)):
            raise ValueError("Invalid package handoff Journal intent")
        game[INTENT] = dict(map=area, pawn=pawn, offer_id=token, character_id=recipient)


def _choices(host, view):
    package = host.party.get(dig.CUSTODY, {})
    guid = package.get("package_guid")
    if (package.get("state") != "owned" or package.get("owner", "host") != "host" or not valid_guid(guid)
            or host.party.get("native_quests", {}).get(MISSING_CONTACT.quest_id, {}).get("step") != 1):
        return None, ()
    scope = (host.control.generation, host.zone_epoch, view["map"], view["pawn"], guid)
    roles = host.roles()
    names = host.store.character_names(set(roles.values()) - {"host"})
    choices = []
    for role, character in roles.items():
        name = names.get(character)
        if not character_id(character) or character == "host" or not name:
            continue
        try:
            sender, recipient = dig._handoff_pawns(host, character)
        except party.RuleError:
            continue
        if sender["name"] == view["pawn"] and dig._handoff_near(sender, recipient):
            index = role.removeprefix("guest") or "1"
            choices.append((character, recipient["name"], f"{name} (Guest {index})"))
    return scope, tuple(choices)


@dataclass(frozen=True)
class Offer:
    token: str
    scope: tuple[str, ...]
    choices: tuple[tuple[str, str, str], ...]
    expires: float


async def _command(host, bridge, command, *arguments):
    snapshot = await bridge.command(command, *arguments, timeout=3)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return snapshot


class Journal:
    def __init__(self):
        self.offer: Offer | None = None
        self.reply: tuple[str, str] | None = None

    async def poll(self, host, bridge):
        view = host.last_game.get(JOURNAL)
        if (not view or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party["wipe"]
                or view["pawn"] != host.last_game.get("pawns", {}).get("local", {}).get("name")):
            self.offer = self.reply = None
            return
        if time.monotonic() - host.last_seen > 1.5:
            return

        token = view["offer_id"]
        if view["state"] == "submitting":
            intent = host.last_game.get(INTENT)
            if not token or not intent or intent["offer_id"] != token:
                return
            if not self.reply or self.reply[0] != token:
                self.reply = (token, await self._submit(host, bridge, view, intent))
            if not host.stopping:
                with suppress(OSError, RuntimeError):
                    await _command(host, bridge, _RESULT, *self.reply)
            return

        self.reply = None
        if view["state"] not in ("idle", "choosing"):
            self.offer = None
            return
        scope, choices = _choices(host, view)
        if not choices:
            self.offer = None
            if token:
                with suppress(OSError, RuntimeError):
                    await _command(host, bridge, _CLEAR)
            return

        now = time.monotonic()
        same = self.offer is not None and (self.offer.scope, self.offer.choices) == (scope, choices)
        if same and token == self.offer.token and self.offer.expires - now > _LEASE / 2:
            return
        # A new native Journal or changed roster requires a fresh confirmation.
        keep_token = same and token == self.offer.token and now <= self.offer.expires
        offer = Offer(self.offer.token if keep_token else uuid.uuid4().hex, scope, choices, now + _LEASE)
        self.offer = offer
        arguments = tuple(field for character, _, label in choices for field in (character, label))
        with suppress(OSError, RuntimeError):
            await _command(host, bridge, _OFFER, offer.token, scope[-1], *arguments)

    async def _submit(self, host, bridge, view, intent):
        offer, self.offer = self.offer, None
        scope, current = _choices(host, view)
        selected = intent["character_id"]
        if (not offer or offer.token != intent["offer_id"] or offer.scope != scope or time.monotonic() > offer.expires
                or not any(row[0] == selected and row in current for row in offer.choices)):
            return "refused"
        try:
            await dig.handoff(host, bridge, selected, journal_intent=intent)
        except (OSError, RuntimeError, ValueError) as error:
            host.control.event("native_package_handoff_journal_refused", character_id=selected, reason=str(error))
            return "refused"
        return "transferred"


async def fixture(host, bridge, operation):
    if (not host.fixtures or not host.native_host_dig_live or not isinstance(operation, str) or operation not in _FIXTURES
            or host.last_game.get("state") != "hosting" or host.last_game.get("map") not in dig._HANDOFF_MAPS):
        raise party.RuleError("native_package_handoff_journal_fixture_unavailable")
    await _command(host, bridge, "native-host-invite-fixture", operation)
    return {"native_package_handoff_journal_fixture": operation}
