"""Route local stock MindCore button presses to the authenticated Results transaction."""

from dataclasses import dataclass
import time
import uuid

from . import native_mindcore as core
from .native_gold import valid_guid
from .native_tape_profiles import DRILL


KEY = "native_mindcore_view"
TAG = "native-mindcore-view"
HEARTBEAT = "native-mindcore-upload-heartbeat"
FIXTURE = "native-mindcore-interact-fixture"
RENDER = "native-mindcore-render"
WAITING = {"checking", "submitting"}
IDLE = {"pass-through", "armed", "focused"}


def valid_pawn(name):
    return (isinstance(name, str) and name.startswith(f"Player_FP_C {core.LEVEL}Player_FP_C_")
            and len(name) <= 512 and name.isprintable())


def parse(fields):
    if len(fields) != 8:
        raise ValueError("Invalid local MindCore input row")
    tag, area, pawn, cradle, state, serial, begin, guid = fields
    if (tag, area, cradle) != (TAG, core.MAP, core.CRADLE) or not valid_pawn(pawn):
        raise ValueError("Invalid local MindCore target")
    if not all(value.isascii() and value.isdigit() and len(value) <= 16 for value in (serial, begin)):
        raise ValueError("Invalid local MindCore input sequence")
    sequence, opened = int(serial), int(begin)
    idle = state in IDLE and (sequence, opened, guid) == (0, 0, "-")
    view = state in ("checking", "missing") and 0 < opened == sequence < 2**53 and guid == "-"
    offer = state == "offer" and 0 < opened == sequence < 2**53 and valid_guid(guid)
    confirm = state in ("submitting", "compiled") and 0 < opened < sequence < 2**53 and valid_guid(guid)
    refused = state == "unavailable" and 0 < opened <= sequence < 2**53 and guid == "-"
    if not (idle or view or offer or confirm or refused):
        raise ValueError("Invalid local MindCore input state")
    return dict(map=area, pawn=pawn, cradle=cradle, state=state, sequence=sequence, opened=opened,
                guid=None if guid == "-" else guid)


def qualified_stage(gameplay):
    scene = gameplay.get("host_world", {}).get(core.SCENE, {})
    return tuple(scene.get(key) for key in ("map", "cradle", "uploader", "core", "flag", "status", "installed")) == (
        core.MAP, core.CRADLE, core.UPLOADER, core.CORE, core.FLAG, 1, True)


@dataclass(frozen=True)
class Offer:
    identity: tuple[str, str, int]
    guid: str
    expires: float


class MindCoreView:
    def __init__(self, bridge, control, character):
        self.bridge, self.control, self.character = bridge, control, character
        self.latest = 0
        self.offer = None

    async def sync(self, snapshot, epoch, active, action):
        proof = snapshot.get(KEY, {})
        state, sequence = proof.get("state"), proof.get("sequence")
        if not active or not epoch or snapshot.get("map") != core.MAP:
            self.offer = None
            if state in WAITING and type(sequence) is int:
                self.latest = max(self.latest, sequence)
            return snapshot
        if state not in WAITING or type(sequence) is not int or not self.latest < sequence < 2**53:
            return snapshot
        self.latest = sequence
        pawn, opened = proof.get("pawn"), proof.get("opened")
        if (snapshot.get("state"), proof.get("map"), proof.get("cradle"), pawn) != (
                "connected", core.MAP, core.CRADLE, snapshot.get("pawns", {}).get("local", {}).get("name")) or not pawn:
            self.offer = None
            return snapshot
        identity = epoch, pawn, opened
        operation = uuid.uuid5(self.bridge.input_namespace, f"{epoch}:{pawn}:mindcore:{sequence}").hex
        previous, self.offer = self.offer, None
        guid = proof.get("guid")
        now = time.monotonic()
        try:
            if state == "checking":
                if opened != sequence or guid is not None:
                    raise ValueError("The stock upload inspection expired")
                answer = (await action(core.PREFLIGHT, operation, None)).get(core.PROOF, {})
                guid = answer.get("guid")
                if (answer.get("map"), answer.get("operation"), answer.get("status"), answer.get("proof")) != (
                        core.MAP, core.PREFLIGHT, 1, "owned") or not valid_guid(guid):
                    raise RuntimeError("The host did not verify the exact guest Results tape")
                outcome = "offer"
            else:
                if not previous or previous.identity != identity or previous.guid != guid or previous.expires <= now:
                    raise ValueError("The stock upload confirmation expired")
                answer = await action(core.UPLOAD, operation, guid)
                if tuple(answer.get(key) for key in ("effect", "map", "cradle", "uploader", "flag", "class_path",
                                                         "stock_status", "owner", "guid", "operation_id")) != (
                        "native_mindcore_results_compiled", core.MAP, core.CRADLE, core.UPLOADER, core.FLAG,
                        DRILL.product, 2, self.character, guid, operation):
                    raise RuntimeError("The host did not verify the scoped Results upload")
                outcome = "compiled"
        except (OSError, RuntimeError, ValueError) as error:
            outcome = "missing" if state == "checking" and str(error) in (
                "native_mindcore_refused_durable_results_owner", "native_mindcore_refused_guest_results_unverified",
            ) else "unavailable"
            guid = None
            self.control.event("native_mindcore_input_rejected", sequence=sequence, reason=str(error))
        try:
            rendered = await self.bridge.command(RENDER, str(sequence), str(opened), outcome, guid or "-", timeout=3)
            expected = dict(proof, state=outcome, guid=guid)
            if rendered.get(KEY) != expected:
                raise RuntimeError("The local guest did not verify the exact MindCore response")
            if outcome == "offer":
                self.offer = Offer(identity, guid, time.monotonic() + 22)
            self.control.event("native_mindcore_input", sequence=sequence, opened=opened, operation_id=operation, state=outcome)
            return rendered
        except (OSError, RuntimeError, ValueError) as error:
            self.control.event("native_mindcore_render_rejected", sequence=sequence, reason=str(error))
            return snapshot
