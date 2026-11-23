"""Read the same exact stock Dead Bridge CQC on both headless seats."""

import asyncio
import math
from pathlib import Path
import uuid

from tools.silverfish import runtime


async def _sample(run: Path, actor: str) -> dict:
    mailbox, = (run / "game-bridge").glob("*")
    request, reply = (mailbox / f"cqc-corpse-{tag}" for tag in ("probe", "reply"))
    ticket = uuid.uuid4().hex
    runtime.write_text(request, ticket + "\n", newline="\n")
    async with asyncio.timeout(4):
        while True:
            try:
                fields = reply.read_text(encoding="utf-8").rstrip("\n").split("\t", 2)
            except (FileNotFoundError, PermissionError):
                fields = ()
            if len(fields) == 3 and fields[0] == ticket:
                assert fields[1] == "ok", fields[2]
                break
            await asyncio.sleep(.06)
    if fields[2] == "missing_actor":
        return {"actor": actor, "present": False, "marker": None, "hidden": None, "physics": None}
    (name, address, mesh, role, alive, hidden, controller, collision, capsule, visible,
     mesh_collision, physics, marker, anchor, bone, simulating_bone, position) = fields[2].split("\t")
    assert name == actor and int(address, 16) and int(mesh, 16)
    flags = [alive, hidden, controller, collision, visible, physics, simulating_bone]
    assert all(flag in ("0", "1") for flag in flags)
    coordinates = [float(value) for value in position.split(",")]
    assert len(coordinates) == 3 and all(math.isfinite(value) for value in coordinates)
    return {"actor": name, "present": True, "address": address, "mesh": mesh, "role": int(role),
            "alive": alive == "1", "hidden": hidden == "1", "controller": controller == "1",
            "collision": collision == "1", "capsule_collision": int(capsule), "mesh_visible": visible == "1",
            "mesh_collision": int(mesh_collision), "physics": physics == "1", "marker": int(marker),
            "anchor": int(anchor), "bone": bone, "simulating_bone": simulating_bone == "1", "position": coordinates}


async def observe_cqc_pair(host, guest, actor):
    host_body, guest_body = await asyncio.gather(_sample(host, actor), _sample(guest, actor))
    return {"host": host_body, "guest": guest_body}


def expect_live(row):
    assert (row["host"]["role"], row["guest"]["role"]) == (3, 1), row
    assert not row["guest"]["controller"], row
    for body in row.values():
        assert body["alive"] and not body["hidden"] and body["collision"] and body["mesh_visible"], row


def _physical(body):
    return (body["present"] and not body["alive"] and not body["hidden"] and body["mesh_visible"] and body["collision"]
            and body["capsule_collision"] == 0 and body["mesh_collision"] in (2, 3)
            and body["physics"] and body["marker"] > 0 and body["anchor"] >= 0 and body["simulating_bone"])


async def observe_cqc_death(host, guest, actor, known):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 4
    first = None
    while True:
        bodies = await observe_cqc_pair(host, guest, actor)
        if first is None:
            first = bodies
        authority, counterpart = bodies["host"], bodies["guest"]
        distance = None
        if authority["present"] and counterpart["present"]:
            for seat, body in bodies.items():
                previous = known.get(seat)
                if previous:
                    assert (body["address"], body["mesh"]) == (previous["address"], previous["mesh"]), (seat, known, bodies)
            same_marker_and_anchor = all(authority[key] == counterpart[key] for key in ("marker", "anchor", "bone"))
            distance = math.dist(authority["position"], counterpart["position"])
            qualified_roles = (authority["role"], counterpart["role"]) == (3, 1) and not counterpart["controller"]
            if qualified_roles and _physical(authority) and _physical(counterpart) and same_marker_and_anchor and distance <= 65:
                outcome = "physical"
                break
        if loop.time() >= deadline:
            if not counterpart["present"]:
                outcome = "missing_actor"
            elif counterpart["role"] != 1 or counterpart["controller"]:
                outcome = "unqualified_counterpart"
            elif _physical(authority) and counterpart["hidden"] and not counterpart["collision"]:
                outcome = "hidden_fallback"
            else:
                outcome = "physical_mismatch"
            break
        await asyncio.sleep(.2)
    return {"outcome": outcome, "bodies": bodies, "first_sample": first,
            "anchor_distance_cm": round(distance, 3) if distance is not None else None}


def require_same_physical_corpse(before, after):
    assert before["outcome"] == after["outcome"] == "physical", (
        f"CQC presentation changed from {before['outcome']} to {after['outcome']}"
    )
    for role in ("host", "guest"):
        assert all(before["bodies"][role][key] == after["bodies"][role][key]
                   for key in ("actor", "address", "mesh", "marker", "anchor", "bone")), (role, before, after)
