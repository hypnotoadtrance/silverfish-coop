"""Durable Regression Matrix anchors and exact native Eather debit receipts."""

from hashlib import sha256
import json
import re
from dataclasses import dataclass

from . import party
from .authored_routes import MAPS as EXPANDED_MAPS
from .native_gold import valid_guid
from .travel import AUTHORED_MAPS, DREAM_MAPS


STATE_KEY = "matrix_transport"
NOMINAL_COST = 100
MAPS = (AUTHORED_MAPS | EXPANDED_MAPS) - DREAM_MAPS
MAX_POSITION = 100_000_000
MAX_ROTATION = 360
MAX_INT32 = 2_147_483_647
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REPORT_PREFIX = "native-matrix"
_REPORT_DETAILS = {
    "native-matrix-anchor", "native-matrix-eather", "native-matrix-canister",
    "native-matrix-charge", "native-matrix-quote",
}


@dataclass(frozen=True)
class MatrixRequest:
    sequence: int
    source: str
    pawn: str
    guid: str
    action: str


def parse_request(fields: list[str]) -> MatrixRequest | None:
    if (len(fields) != 6 or fields[0] != "matrix-request" or not fields[1].isascii()
            or not fields[1].isdigit() or not 0 < len(fields[1]) <= 12
            or not _qualified_map(fields[2]) or not _pawn_name(fields[3])
            or not valid_guid(fields[4]) or fields[5] not in ("record", "activate")):
        return None
    return MatrixRequest(int(fields[1]), *fields[2:])


def _pawn_name(name: str) -> bool:
    return 12 < len(name) <= 512 and name.isprintable() and name.startswith("Player_FP_C ")


def parse_report(rows: list[list[str]]) -> dict | None:
    if not rows:
        return None
    headers = [row for row in rows if row[0] == _REPORT_PREFIX]
    if len(headers) != 1 or len(headers[0]) != 7:
        raise ValueError("Invalid native Matrix report header")
    role, source, pawn, guid, operation, result = headers[0][1:]
    if role != "host" or not _qualified_map(source) or not _pawn_name(pawn) or not valid_guid(guid):
        raise ValueError("The native Matrix report has the wrong owner")
    scope = [role, source, pawn, guid]
    data = {"role": role, "source": source, "pawn": pawn, "guid": guid, "operation": operation, "result": result}
    snapshots: dict[str, dict] = {}
    for row in rows:
        tag, values = row[0], row[5:]
        if row == headers[0]:
            continue
        if tag not in _REPORT_DETAILS or row[1:5] != scope:
            raise ValueError("The native Matrix report contains a different scope")
        if tag == "native-matrix-anchor" and len(values) == 6 and "anchor" not in data:
            data["anchor"] = {"position": _vector(tuple(map(float, values[:3])), MAX_POSITION),
                              "rotation": _vector(tuple(map(float, values[3:])), MAX_ROTATION)}
        elif tag == "native-matrix-eather" and len(values) == 2:
            phase, balance = values
            frame = snapshots.setdefault(phase, {"canisters": {}})
            if phase not in ("current", "before", "after") or "remainder" in frame:
                raise ValueError("The native Matrix report repeats or mislabels an Eather snapshot")
            frame["remainder"] = float(balance)
        elif tag == "native-matrix-canister" and len(values) == 4:
            phase, canister, ammo, value = values
            bag = snapshots.setdefault(phase, {"canisters": {}})["canisters"]
            if phase not in ("current", "before", "after") or canister in bag:
                raise ValueError("The native Matrix report repeats or mislabels a canister")
            bag[canister] = {"ammo": int(ammo), "value": int(value)}
        elif tag == "native-matrix-quote" and len(values) == 5 and "quote" not in data:
            data["quote"] = dict(zip(("nominal_cost", "charged", "available", "affinity_instances"),
                                     map(int, values[:4]), strict=True), after_remainder=float(values[4]))
        elif tag == "native-matrix-charge" and len(values) == 2 and "charge" not in data:
            data["charge"] = {"nominal_cost": int(values[0]), "charged": int(values[1])}
        else:
            raise ValueError("The native Matrix report contains an invalid row")
    data["eather"] = {phase: stock_eather(frame) for phase, frame in snapshots.items()}
    return data


def _qualified_map(value: object) -> bool:
    return type(value) is str and value in MAPS


def _int32(value: object, minimum: int = 0, maximum: int = MAX_INT32) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _vector(values: object, limit: int) -> list[float]:
    if (type(values) not in (list, tuple) or len(values) != 3
            or any(type(axis) not in (int, float) or not -limit <= axis <= limit for axis in values)):
        raise ValueError("Invalid Regression Matrix anchor transform")
    return [float(axis) if axis else 0.0 for axis in values]


def stock_eather(snapshot: dict) -> dict:
    """Validate exact native canister quantities and the fractional carry."""
    if type(snapshot) is not dict or set(snapshot) != {"remainder", "canisters"}:
        raise ValueError("Invalid stock Eather snapshot")
    remainder, canisters = snapshot["remainder"], snapshot["canisters"]
    if (type(remainder) not in (int, float) or not 0 <= remainder < 1
            or type(canisters) is not dict or any(not valid_guid(guid) for guid in canisters)):
        raise ValueError("Invalid stock Eather snapshot")
    normalized = {}
    for guid, canister in sorted(canisters.items()):
        if (type(canister) is not dict or set(canister) != {"ammo", "value"} or not _int32(canister["ammo"])
                or not _int32(canister["value"], -2_147_483_648)):
            raise ValueError("Invalid stock Eather canister")
        normalized[guid] = dict(canister)
    return {"remainder": float(remainder) if remainder else 0.0, "canisters": normalized}


def _debit_proof(debit: dict, *, awaiting_native_result: bool = False) -> dict:
    if (type(debit) is not dict or set(debit) != {"nominal_cost", "charged", "before", "after"}
            or not _int32(debit["nominal_cost"], NOMINAL_COST, NOMINAL_COST)
            or not _int32(debit["charged"], maximum=NOMINAL_COST)):
        raise ValueError("Invalid Regression Matrix debit amount")
    before = stock_eather(debit["before"])
    if awaiting_native_result and debit["after"] is None:
        return {"nominal_cost": NOMINAL_COST, "charged": debit["charged"], "before": before, "after": None}
    after = stock_eather(debit["after"])
    old, new = before["canisters"], after["canisters"]
    if old.keys() != new.keys():
        raise ValueError("A stock Matrix debit must preserve every canister")
    differences = [old[guid]["ammo"] - new[guid]["ammo"] for guid in old]
    if any(difference < 0 for difference in differences) or sum(differences) != debit["charged"]:
        raise ValueError("The Matrix debit does not match the stock canister quantities")
    return {"nominal_cost": NOMINAL_COST, "charged": debit["charged"], "before": before, "after": after}


def _eather_digest(snapshot: dict) -> str:
    encoded = json.dumps(stock_eather(snapshot), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("ascii")).hexdigest()


def _receipt(trip: dict) -> dict:
    debit = trip["debit"]
    return {
        "guid": trip["guid"], "source": trip["source"], "destination": trip["destination"],
        "anchor_revision": trip["anchor_revision"], "charged": debit["charged"],
        "before_sha256": _eather_digest(debit["before"]), "after_sha256": _eather_digest(debit["after"]),
        "in_zone": trip["source"] == trip["destination"],
    }


def ledger(state: dict) -> dict:
    saved = state.get(STATE_KEY, {"anchors": {}, "pending": None, "completed": {}})
    if (type(saved) is not dict or set(saved) != {"anchors", "pending", "completed"}
            or type(saved["anchors"]) is not dict or type(saved["completed"]) is not dict):
        raise ValueError("Invalid saved Regression Matrix state")

    for guid, marker in saved["anchors"].items():
        if (not valid_guid(guid) or type(marker) is not dict or set(marker) != {"map", "position", "rotation", "revision"}
                or not _qualified_map(marker["map"]) or not _int32(marker["revision"], 1)):
            raise ValueError("Invalid saved Regression Matrix anchor")
        _vector(marker["position"], MAX_POSITION)
        _vector(marker["rotation"], MAX_ROTATION)

    for operation_id, receipt in saved["completed"].items():
        if (not valid_guid(operation_id) or type(receipt) is not dict
                or set(receipt) != {"guid", "source", "destination", "anchor_revision", "charged",
                                    "before_sha256", "after_sha256", "in_zone"}
                or not valid_guid(receipt["guid"]) or not _qualified_map(receipt["source"])
                or not _qualified_map(receipt["destination"]) or not _int32(receipt["anchor_revision"], 1)
                or not _int32(receipt["charged"], maximum=NOMINAL_COST)
                or type(receipt["in_zone"]) is not bool or receipt["in_zone"] != (receipt["source"] == receipt["destination"])
                or any(type(receipt[field]) is not str or not _SHA256.fullmatch(receipt[field])
                       for field in ("before_sha256", "after_sha256"))):
            raise ValueError("Invalid saved Regression Matrix receipt")
        marker = saved["anchors"].get(receipt["guid"])
        if not marker or marker["revision"] < receipt["anchor_revision"]:
            raise ValueError("The saved Regression Matrix receipt has no corresponding anchor")
        if marker["revision"] == receipt["anchor_revision"] and marker["map"] != receipt["destination"]:
            raise ValueError("The saved Regression Matrix receipt does not match its anchor")

    trip = saved["pending"]
    if trip is None:
        return saved
    if (type(trip) is not dict or set(trip) != {"id", "guid", "source", "destination", "anchor_revision", "phase", "debit"}
            or not valid_guid(trip["id"]) or trip["id"] in saved["completed"]
            or not valid_guid(trip["guid"]) or not _qualified_map(trip["source"])
            or not _qualified_map(trip["destination"]) or not _int32(trip["anchor_revision"], 1)
            or trip["phase"] not in ("planned", "debited")):
        raise ValueError("Invalid pending Regression Matrix trip")
    marker = saved["anchors"].get(trip["guid"])
    if not marker or marker["revision"] != trip["anchor_revision"] or marker["map"] != trip["destination"]:
        raise ValueError("The pending Regression Matrix trip does not match its anchor")
    _debit_proof(trip["debit"], awaiting_native_result=trip["phase"] == "planned")
    return saved


def pending(state: dict) -> dict | None:
    return ledger(state)["pending"]


def anchor(state: dict, guid: str) -> dict | None:
    if not valid_guid(guid):
        raise ValueError("Invalid Regression Matrix item GUID")
    return ledger(state)["anchors"].get(guid)


def record(state: dict, guid: str, map_name: str, position: list[float], rotation: list[float]) -> dict:
    if not valid_guid(guid) or not _qualified_map(map_name):
        raise ValueError("A stock Matrix GUID and qualified host map are required")
    saved = ledger(state)
    previous = saved["anchors"].get(guid)
    pose = {"map": map_name, "position": _vector(position, MAX_POSITION), "rotation": _vector(rotation, MAX_ROTATION)}
    if previous and all(previous[key] == value for key, value in pose.items()):
        return state
    if saved["pending"] and saved["pending"]["guid"] == guid:
        raise ValueError("The Regression Matrix anchor is in use by a pending trip")
    revision = previous["revision"] + 1 if previous else 1
    if not _int32(revision, 1):
        raise ValueError("Invalid Regression Matrix anchor revision")
    candidate = party.load_party(state)
    journal = candidate.setdefault(STATE_KEY, {"anchors": {}, "pending": None, "completed": {}})
    journal["anchors"][guid] = {
        **pose, "revision": revision,
    }
    return candidate


def begin(state: dict, operation_id: str, guid: str, source: str, anchor_revision: int, debit: dict) -> dict:
    if (not valid_guid(operation_id) or not valid_guid(guid) or not _qualified_map(source)
            or not _int32(anchor_revision, 1)):
        raise ValueError("Invalid Regression Matrix trip identity")
    saved = ledger(state)
    proof = _debit_proof(debit, awaiting_native_result=True)
    if receipt := saved["completed"].get(operation_id):
        if (guid != receipt["guid"] or source != receipt["source"] or anchor_revision != receipt["anchor_revision"]
                or proof["charged"] != receipt["charged"] or _eather_digest(proof["before"]) != receipt["before_sha256"]
                or (proof["after"] is not None and _eather_digest(proof["after"]) != receipt["after_sha256"])):
            raise ValueError("Regression Matrix operation already has a different receipt")
        return state
    marker = saved["anchors"].get(guid)
    if not marker or marker["revision"] != anchor_revision:
        raise ValueError("The Regression Matrix anchor revision changed before travel")

    trip = {"id": operation_id, "guid": guid, "source": source, "destination": marker["map"],
            "anchor_revision": anchor_revision, "phase": "planned", "debit": proof}
    existing = saved["pending"]
    if existing:
        prior = existing["debit"]
        comparison = dict(existing, phase="planned", debit=dict(prior, after=None) if proof["after"] is None else prior)
        if comparison == trip:
            return state
        raise ValueError("A different Regression Matrix trip is already pending")
    candidate = party.load_party(state)
    candidate[STATE_KEY]["pending"] = trip
    return candidate


def debit_status(state: dict, operation_id: str, observed: dict) -> str:
    """Classify a restart as needing the pending debit or already carrying it."""
    if not valid_guid(operation_id):
        raise ValueError("Invalid Regression Matrix operation ID")
    saved = ledger(state)
    live = stock_eather(observed)
    if receipt := saved["completed"].get(operation_id):
        if _eather_digest(live) == receipt["after_sha256"]:
            return "after"
        raise ValueError("A completed Regression Matrix debit cannot be reapplied")
    trip = saved["pending"]
    if not trip or trip["id"] != operation_id:
        raise ValueError("No matching pending Regression Matrix trip")
    proof = trip["debit"]
    if live == proof["after"]:
        return "after"
    if live == proof["before"] and trip["phase"] == "planned":
        return "before"
    raise ValueError("Live Eather does not match the pending Matrix debit")


def commit_debit(state: dict, operation_id: str, observed: dict) -> dict:
    if debit_status(state, operation_id, observed) != "after":
        raise ValueError("The Regression Matrix debit has not been applied")
    trip = pending(state)
    if not trip or trip["id"] != operation_id or trip["phase"] == "debited":
        return state
    candidate = party.load_party(state)
    candidate[STATE_KEY]["pending"]["phase"] = "debited"
    return candidate


def commit_native_debit(state: dict, operation_id: str, verified: dict) -> dict:
    """Commit the exact stock result from the same verified game-thread debit."""
    trip = pending(state)
    proof = _debit_proof(verified)
    if not trip or trip["id"] != operation_id:
        raise ValueError("No matching pending Regression Matrix trip")
    expected = trip["debit"]
    if (expected["before"] != proof["before"] or expected["charged"] != proof["charged"]
            or expected["after"] not in (None, proof["after"])):
        raise ValueError("Native Matrix debit does not match the committed intent")
    if trip["phase"] == "debited":
        return state
    candidate = party.load_party(state)
    candidate[STATE_KEY]["pending"].update(phase="debited", debit=proof)
    return candidate


def arrive(state: dict, operation_id: str, destination: str) -> dict:
    if not valid_guid(operation_id) or not _qualified_map(destination):
        raise ValueError("Invalid Regression Matrix arrival identity")
    saved = ledger(state)
    if receipt := saved["completed"].get(operation_id):
        if receipt["destination"] != destination:
            raise ValueError("Regression Matrix operation already settled elsewhere")
        return state
    trip = saved["pending"]
    if not trip or trip["id"] != operation_id or trip["phase"] != "debited" or trip["destination"] != destination:
        raise ValueError("Regression Matrix arrival does not match a paid trip")
    candidate = party.load_party(state)
    journal = candidate[STATE_KEY]
    journal["pending"] = None
    journal["completed"][operation_id] = _receipt(trip)
    return candidate
