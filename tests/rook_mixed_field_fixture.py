"""Read both seats' unmodified stock Gravity/Vortex populations in The Rook."""

import asyncio
import json
import math
import uuid

MAP = "/Game/Classes/Maps/Dreams/TheRook_P"
LEVEL = f"{MAP}.TheRook_P:PersistentLevel"
FIELD_PATHS = {f"{LEVEL}.AnomalyField_BP_C_{index}" for index in (1, 5, 6, 7, 8)}
KINDS = {"gravity": "GravityAnomaly_BP", "vortex": "VortexTrap_BP"}
ANOMALY_CLASSES = {f"BlueprintGeneratedClass /Game/Classes/Anomalies/{asset}.{asset}_C" for asset in KINDS.values()}


def stage_rook_probe(work):
    if any(part.casefold() == "steamapps" for part in work.parts):
        raise ValueError("Use a private stage for the Rook mixed-field census")
    from live_stock_creator_smoke import installed_probe

    return installed_probe(work / "stage", mod="SilverfishRookMixedFieldProbe", fixture="rook_mixed_field_probe.lua")


def point(values):
    location = list(map(float, values))
    assert len(location) == 3 and all(math.isfinite(value) for value in location), values
    return location


def parse_census(lines, seat):
    label, actual, pawn, *location = lines[0].split("\t")
    assert (label, actual) == ("seat", seat) and pawn.startswith(f"Player_FP_C {LEVEL}."), lines[0]
    census = {"pawn": pawn, "pawn_position": point(location), "fields": {}, "actors": {}}
    for line in lines[1:]:
        label, *fields = line.split("\t")
        if label == "field":
            path, x, y, z, radius, minimum, maximum, forced, *classes = fields
            assert path in FIELD_PATHS and path not in census["fields"], line
            assert abs(float(radius) - 11082.602) < 1 and (minimum, maximum, forced) == ("6", "12", "true"), line
            assert len(classes) == 2 and set(classes) == ANOMALY_CLASSES, line
            census["fields"][path] = {"location": point((x, y, z)), "radius": float(radius),
                                      "classes": classes, "roll_bounds": [6, 12], "force_spawn": True}
        else:
            kind, path, role, tagged, replicated, collision, ticking, *location = fields
            assert label == "anomaly" and kind in KINDS and int(role) in {1, 3}, line
            assert path.startswith(f"{KINDS[kind]}_C {LEVEL}.") and path not in census["actors"], line
            assert all(flag in {"true", "false"} for flag in (tagged, replicated, collision, ticking)), line
            census["actors"][path] = {"kind": kind, "role": int(role), "stock_tag": tagged == "true",
                                      "replicates": replicated == "true", "collision": collision == "true",
                                      "ticking": ticking == "true", "location": point(location)}
    assert census["fields"].keys() == FIELD_PATHS and len(census["actors"]) <= 128, census
    return census


async def raw_census(run, seat):
    mailbox, = (run / "game-bridge").iterdir()
    nonce = uuid.uuid4().hex
    request = mailbox / "rook-mixed-field-request"
    pending = request.with_suffix(".pending")
    pending.write_text(nonce + "\n", encoding="ascii", newline="")
    pending.replace(request)
    async with asyncio.timeout(6):
        while True:
            try:
                with (mailbox / "rook-mixed-field-reply").open() as response:
                    message = response.read(65537)
            except (FileNotFoundError, PermissionError):
                message = ""
            assert len(message) <= 65536, "Rook mixed-field census exceeded its reply size"
            if message.endswith("\nend\n"):
                tag, result, *rows, _ = message.splitlines()
                if tag == nonce:
                    if result != "ok":
                        raise RuntimeError(f"Rook {seat} census failed: {rows}")
                    return parse_census(rows, seat)
            await asyncio.sleep(.05)


def field_regions(census):
    """Record proximity to authored spawn radius, which does not prove ownership."""
    for row in census["actors"].values():
        row["near_fields"] = [path for path, field in census["fields"].items()
                              if math.dist(row["location"][:2], field["location"][:2]) <= field["radius"]]


def compare(host, guest):
    for census in (host, guest):
        field_regions(census)
    host_stock = {name: row for name, row in host["actors"].items() if row["role"] == 3 and row["stock_tag"]}
    guest_local = {name: row for name, row in guest["actors"].items() if row["role"] == 3 and row["stock_tag"]}
    replicated = {name: row for name, row in guest["actors"].items() if row["role"] == 1}
    # A known role-1 actor needs only a nearby same-kind native candidate. Keep all
    # candidates when close spawns make the relationship ambiguous.
    matches = {peer: [{"host": source, "distance_cm": round(distance, 2)}
                      for source, authority in host_stock.items()
                      if authority["kind"] == replica["kind"]
                      and (distance := math.dist(authority["location"], replica["location"])) <= 150]
               for peer, replica in replicated.items()}
    return {"host_stock": sorted(host_stock), "guest_local_stock": sorted(guest_local),
            "guest_replica_candidates": matches,
            "guest_local_kinds": sorted({row["kind"] for row in guest_local.values()}),
            "guest_replica_kinds": sorted({row["kind"] for row in replicated.values()})}


async def verify_rook_mixed_fields(wait, note, host_run, guest_run):
    def ready(status):
        game = status.get("game", {})
        return (game.get("map") == MAP and game.get("state") in {"hosting", "connected"}
                and status.get("link_connected") and status.get("zone_epoch"))

    host_status, guest_status = await asyncio.gather(wait(host_run, ready), wait(guest_run, ready))
    assert host_status["zone_epoch"] == guest_status["zone_epoch"]
    host, guest = await asyncio.gather(raw_census(host_run, "host"), raw_census(guest_run, "guest"))
    for raw, status in ((host, host_status), (guest, guest_status)):
        assert raw["pawn"] == status["game"]["pawns"]["local"]["name"], raw
        raw["stock_telemetry"] = status["game"].get("native_anomaly_field")
        raw["status_sequence"] = status["game"]["sequence"]
    assert host["fields"] == guest["fields"], (host["fields"], guest["fields"])
    result = compare(host, guest)
    proof = {"map": MAP, "epoch": host_status["zone_epoch"], "host": host, "guest": guest, **result}
    path = host_run / "live-rook-mixed-field-probe.json"
    path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    assert {host["actors"][key]["kind"] for key in result["host_stock"]} == set(KINDS), path
    note("rook_mixed_stock_anomaly_census", {"file": str(path), "fields": len(host["fields"]),
        "host_stock": len(result["host_stock"]), "guest_local_stock": len(result["guest_local_stock"]),
        "guest_local_kinds": result["guest_local_kinds"], "guest_replica_kinds": result["guest_replica_kinds"]})
