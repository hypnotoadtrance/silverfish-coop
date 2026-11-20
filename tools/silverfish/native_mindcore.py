"""Preserve guest custody while the stock Data Silo compiles Drill Test Results."""

import asyncio
import time

from . import party
from .native_gold import valid_guid
from .native_mosby_tape import _scope
from .native_tape_profiles import DRILL, drill_download_unlocked


MAP = "/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P"
WORLD = MAP + ".DataSilo_01_P"
LEVEL = WORLD + ":PersistentLevel."
CRADLE = "MindCore_Cradle_BP_C " + LEVEL + "MindCore_Cradle_BP_C_1"
CORE = "MindCore_BP_C " + LEVEL + "MindCore_BP_C_1"
UPLOADER = "DataTape_Uploader_BP_C " + LEVEL + "DataTape_Uploader_BP_C_1"
FLAG = "MC_007_Console"
SCENE = "native_mindcore_scene"
PROOF = "native_mindcore_tape"
LEDGER = "native_mindcore_results_upload"
PREFLIGHT = "native-mindcore-preflight"
UPLOAD = "native-mindcore-upload"
VERIFY = "native-mindcore-verify"
LOCKED = "native_mindcore_refused_console_not_installed"


def parse_scene(fields: list[str]) -> dict:
    expected = ("native-mindcore-scene", MAP, CRADLE, UPLOADER, CORE, FLAG)
    if (len(fields) != 8 or tuple(fields[:6]) != expected or fields[6] not in ("0", "1", "2")
            or fields[7] not in ("0", "1")):
        raise ValueError("Invalid exact Data Silo console report")
    return dict(map=MAP, cradle=CRADLE, uploader=UPLOADER, core=CORE, flag=FLAG,
                status=int(fields[6]), installed=fields[7] == "1")


def parse_proof(fields: list[str]) -> dict:
    if len(fields) != 7:
        raise ValueError("Invalid exact Results tape report")
    tag, area, verb, pawn, guid, proof, status = fields
    if ((tag, area) != ("native-mindcore-tape", MAP) or not pawn.startswith(f"Player_FP_C {LEVEL}")
            or len(pawn) > 512 or not valid_guid(guid)
            or (verb, proof, status) not in ((PREFLIGHT, "owned", "1"), (PREFLIGHT, "owned", "2"),
                                          (UPLOAD, "dispatched", "1"), (VERIFY, "owned", "2"))):
        raise ValueError("Invalid exact Results tape report")
    return dict(map=area, operation=verb, pawn=pawn, guid=guid, proof=proof, status=int(status))


def _conversion(state, character, guid):
    rows = state.get(DRILL.ledger, {})
    if not isinstance(rows, dict) or not drill_download_unlocked(state):
        return False
    return sum(isinstance(row, dict) and (row.get("owner"), row.get("filled_guid"), row.get("map"),
               row.get("station"), row.get("class_path")) == (character, guid, DRILL.map, DRILL.station, DRILL.product)
               for row in rows.values()) == 1


def valid_receipt(state: dict, characters: dict) -> bool:
    if LEDGER not in state:
        return True
    row = state[LEDGER]
    return (isinstance(row, dict) and set(row) == {
        "owner", "guid", "operation_id", "map", "cradle", "uploader", "class_path", "flag", "stock_status",
    } and isinstance(row["owner"], str) and row["owner"] in characters and valid_guid(row["guid"])
        and isinstance(row["operation_id"], str) and party.REQUEST_ID.fullmatch(row["operation_id"]) is not None
        and type(row["stock_status"]) is int
        and (row["map"], row["cradle"], row["uploader"], row["class_path"], row["flag"], row["stock_status"]) ==
        (MAP, CRADLE, UPLOADER, DRILL.product, FLAG, 2)
        and _conversion(state, row["owner"], row["guid"]))


def _ready(snapshot, status):
    scene = snapshot.get(SCENE, {})
    return (snapshot.get("state"), snapshot.get("map"), scene.get("status"), scene.get("installed")) == (
        "hosting", MAP, status, True)


def _target(host, owner, guid):
    pawn = _scope(host, owner, MAP, guest_only=True)
    if guid is None:
        rows = host.party.get(DRILL.ledger, {})
        eligible = [record["filled_guid"] for record in rows.values()
                    if isinstance(record, dict) and record.get("owner") == owner and valid_guid(record.get("filled_guid"))]
        guid, = eligible if len(eligible) == 1 else (None,)
    if not valid_guid(guid) or not _conversion(host.party, owner, guid):
        raise party.RuleError("native_mindcore_refused_durable_results_owner")
    return pawn, guid


async def _command(host, bridge, verb, pawn, guid):
    snapshot = await bridge.command(verb, pawn, guid, timeout=7)
    proof = snapshot.get(PROOF, {})
    if (snapshot.get("state"), snapshot.get("map"), proof.get("operation"), proof.get("pawn"), proof.get("guid")) != (
        "hosting", MAP, verb, pawn, guid,
    ):
        raise RuntimeError("The MindCore proof did not match the current authoritative guest")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return proof


async def preflight(host, bridge, owner, guid):
    pawn, guid = _target(host, owner, guid)
    if not any(_ready(host.last_game, stage) for stage in (1, 2)):
        raise party.RuleError(LOCKED)
    return {PROOF: await _command(host, bridge, PREFLIGHT, pawn, guid)}


async def upload(host, bridge, owner, guid, operation):
    if not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation) or not valid_guid(guid):
        raise party.RuleError("native_mindcore_refused_request")
    pawn, guid = _target(host, owner, guid)
    previous = host.party.get(LEDGER)
    if previous:
        if (previous["owner"], previous["guid"]) != (owner, guid):
            raise party.RuleError("native_mindcore_already_compiled")
        await _command(host, bridge, VERIFY, pawn, guid)
        return {"effect": "native_mindcore_already_compiled", **previous}
    if _ready(host.last_game, 2):
        raise party.RuleError("native_mindcore_already_compiled")
    if not _ready(host.last_game, 1):
        raise party.RuleError(LOCKED)
    host.transitioning = True
    try:
        before = await _command(host, bridge, PREFLIGHT, pawn, guid)
        if before["status"] != 1:
            raise party.RuleError("native_mindcore_already_compiled")
        await host.checkpoint_native(bridge, require_guest_frames=frozenset({owner}))
        member = host.native_recipients().get(owner, {})
        if not (_ready(host.last_game, 1) and member.get("connected") and member.get("pawn") == pawn):
            raise party.RuleError("native_mindcore_exact_interactor_changed")
        host.native_checkpoint_unsafe = True
        try:
            dispatched = await _command(host, bridge, UPLOAD, pawn, guid)
        except RuntimeError as error:
            if str(error).startswith("native_mindcore_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        if (dispatched["status"], dispatched["proof"]) != (1, "dispatched"):
            raise RuntimeError("The stock uploader did not dispatch stage one")
        await bridge.wait_for(lambda snapshot: _ready(snapshot, 2), timeout=18)
        await _command(host, bridge, VERIFY, pawn, guid)

        record = dict(owner=owner, guid=guid, operation_id=operation, map=MAP, cradle=CRADLE, uploader=UPLOADER,
                      class_path=DRILL.product, flag=FLAG, stock_status=2)
        proposed = party.load_party(host.party)
        proposed[LEDGER] = record
        revision = await host.checkpoint_native(bridge, proposed, require_guest_frames=frozenset({owner}))
        host.party, host.native_checkpoint_unsafe = proposed, False
        host.control.event("native_mindcore_results_committed", revision=revision, **record)
        return {"effect": "native_mindcore_results_compiled", **record}
    except (OSError, RuntimeError, ValueError, KeyError, asyncio.CancelledError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
    finally:
        host.transitioning = False
