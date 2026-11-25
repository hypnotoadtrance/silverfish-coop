"""Relay the installed game's stock call-outs from the speaking co-op pawn."""

from collections import deque
import math
import re
import time
import uuid

from .native_guest_drop_record import valid_id, valid_position


KEY = "native_voice"
COMMAND = "native-sound-voice"
EVENTS = {"hurt", "cough", "gasp", "drown", "death", "stunned"}
_PERSONALITY = re.compile(r"[A-Za-z0-9_]{1,30}")
RANGE = 5030  # Stock Vocal_Attenuation inner radius plus falloff.
LIMIT = 12


def speaker(personality, pitch):
    return (isinstance(personality, str) and _PERSONALITY.fullmatch(personality)
            and type(pitch) in (int, float) and 0 < pitch <= 3)


def client_events(game):
    if game.get("state") != "connected":
        return []
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    return [[event, kind] for event, kind, source in game.get("native_voice_events", []) if source == pawn]


class VoiceQueue:
    def __init__(self):
        self.scope = None
        self.events = deque(maxlen=LIMIT)
        self.seen = deque(maxlen=256)
        self.previous = {}

    def _qualify(self, game, epoch):
        if game.get("state") != "hosting" or not valid_id(epoch):
            return False
        scope = game["map"], epoch
        if self.scope != scope:
            self.scope = scope
            self.events.clear()
            self.seen.clear()
            self.previous.clear()
        return True

    def observed(self, game, epoch):
        if self._qualify(game, epoch):
            for event, kind, pawn in game.get("native_voice_events", []):
                self._add(game, pawn, event, kind, local=True)

    def received(self, game, epoch, pawn, events):
        if not self._qualify(game, epoch) or type(events) is not list or len(events) > LIMIT:
            return
        for entry in events:
            if type(entry) is list and len(entry) == 2:
                self._add(game, pawn, *entry, local=False)

    def _add(self, game, pawn, event, kind, *, local):
        if type(event) is not int or not 0 < event < 2**53 or not isinstance(kind, str) or kind not in EVENTS:
            return
        key = pawn, local, event
        if key in self.seen:
            return
        voice = game.get("native_voice_speakers", {}).get(pawn)
        if not voice:
            return
        origin = voice[2] if len(voice) == 3 else next(
            (entry.get("location") for entry in game.get("pawns", {}).values() if entry.get("name") == pawn), None)
        if not valid_position(origin):
            return
        self.seen.append(key)
        now = time.monotonic()
        if now - self.previous.get((pawn, kind), -10) < 0.55:
            return
        self.previous = {key: seen for key, seen in self.previous.items() if now - seen < 3}
        self.previous[pawn, kind] = now
        self.events.append((now + 2.5, pawn, local, [uuid.uuid4().hex, kind, *voice[:2], origin]))

    def frame(self, game, epoch, listener, *, host=False):
        if not listener or not self._qualify(game, epoch):
            return None
        now = time.monotonic()
        while self.events and self.events[0][0] <= now:
            self.events.popleft()
        cues = [cue for _, pawn, local, cue in self.events if pawn != listener and not (host and local)]
        return {"map": game["map"], "epoch": epoch, "cues": cues} if cues else None


class Playback:
    def __init__(self):
        self.scope = None
        self.seen = deque(maxlen=64)

    async def sync(self, bridge, control, game, frame, epoch):
        if not frame or not valid_id(epoch) or (frame.get("map"), frame.get("epoch")) != (game.get("map"), epoch):
            return game
        origin = game.get("pawns", {}).get("local", {}).get("location")
        cues = frame.get("cues")
        if not valid_position(origin) or not isinstance(cues, list) or len(cues) > LIMIT:
            return game
        if self.scope != epoch:
            self.scope = epoch
            self.seen.clear()
        arguments, selected = [], {}
        for cue in cues:
            if not isinstance(cue, list) or len(cue) != 5:
                continue
            nonce, kind, personality, pitch, location = cue
            if (not valid_id(nonce) or nonce in self.seen or nonce in selected or not isinstance(kind, str) or kind not in EVENTS
                    or not speaker(personality, pitch) or not valid_position(location)):
                continue
            if math.dist(origin, location) <= RANGE:
                selected[nonce] = (kind, personality)
                arguments.append("|".join((nonce, kind, personality, str(pitch), *map(str, location))))
            else:
                self.seen.append(nonce)
        if not arguments:
            return game
        try:
            result = await bridge.command(COMMAND, game["map"], epoch, *arguments, timeout=1.5)
        except (OSError, RuntimeError, TimeoutError):
            return game
        for nonce in result.get("ack", {}).get("message", "").split(","):
            if nonce in selected:
                self.seen.append(nonce)
                kind, personality = selected.pop(nonce)
                control.event("native_voice_played", cue=kind, personality=personality)
        return result
