"""Prove one stock resident SMG stroke uses the process-local input bridge."""

import asyncio
import re
import uuid

from tools.silverfish import native_smg_hold
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY

from portable_guest_world_start import resident_events, wait_resident_status


KIND = "smg-standard"
ROUNDS = 7
FIXTURE = "native_guest_held_fire_fixture"
PUBLICATION = "native_guest_primary_publication"
TERMINAL = "native_guest_smg_hold_accepted"
GUEST_EDGE = "native_guest_held_fire_input"


async def verify_stock_held_queue(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_frame = await wait_resident_status(host_run, lambda status:
        status.get("local_bridge") == "memory" and status.get("game", {}).get("map") == OBSERVATORY)
    character = host_frame["gameplay"]["players"]["guest"]["character_id"]
    assert host_frame["gameplay"]["native_pawns"]["guest"] == pawn, host_frame["gameplay"]["native_pawns"]
    guest_frame = await wait_resident_status(guest_run, lambda status:
        status.get("local_bridge") == "memory" and status.get("gameplay_fresh")
        and status.get("game", {}).get("native_guest_primary_scope", {}).get("map") == OBSERVATORY)
    previous = guest_frame["game"]["native_guest_primary_scope"]

    async def control(run, action, **arguments):
        result = await submit(run, action, **arguments)
        assert result["ok"], result
        return result

    async def scoped(guid, kind):
        return await wait_resident_status(guest_run, lambda status:
            status.get("state") == "connected" and status.get("gameplay_fresh") and status.get("local_bridge") == "memory"
            and (scope := status.get("game", {}).get("native_guest_primary_scope", {})).get("guid") == guid
            and (scope.get("map"), scope.get("pawn"), scope.get("kind")) == (OBSERVATORY, previous["pawn"], kind), timeout=10)

    async def matching_events():
        async with asyncio.timeout(8):
            while True:
                guest_rows, host_rows = await asyncio.gather(resident_events(guest_run, guest_frame),
                                                             resident_events(host_run, host_frame))
                edges = [row for row in guest_rows if row.get("stroke") == pressed["stroke"]
                         and row["event"] in (GUEST_EDGE, GUEST_EDGE + "_rejected")]
                terminals = [row for row in host_rows if row["event"] == TERMINAL and row.get("guid") == guid]
                assert all(row["event"] == GUEST_EDGE for row in edges), edges
                if terminals and any(row.get("edge") == "release" for row in edges):
                    assert [row["edge"] for row in edges] == ["press", "release"] and len(terminals) == 1, (edges, terminals)
                    return edges, terminals[0]
                await asyncio.sleep(.04)

    guid = uuid.uuid4().hex.upper()
    seeded = await submit(host_run, "native-guest-weapon-seed", target="guest", item_guid=guid, kind=KIND)
    if seeded["ok"]:
        item = seeded["native_guest_weapon"]
        assert (item["guid"], item["pawn"], item["stored_ammo"]) == (guid, pawn, ROUNDS), item
    else:
        assert seeded["error"] == "Action unavailable on the host", seeded
        guid = await probe(host.bridge, "portable-held-queue", f"seed\t{pawn}")
        assert re.fullmatch(r"[0-9A-F]{32}", guid), guid

    equipped = (await control(guest_run, "native-guest-weapon-equip", target=guid))["native_guest_weapon"]
    try:
        assert (equipped["guid"], equipped["pawn"], equipped["actor_ammo"], equipped["stored_ammo"]) == (guid, pawn, ROUNDS, ROUNDS), equipped
        await scoped(guid, KIND)
        pressed = (await control(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=400))[FIXTURE]
        assert (pressed["map"], pressed["pawn"], pressed["guid"], pressed["edge"], pressed["sequence"]) == (
            OBSERVATORY, previous["pawn"], guid, "press", pressed["stroke"]), pressed

        released_frame = await wait_resident_status(guest_run, lambda status:
            (release := status.get("game", {}).get(FIXTURE, {})).get("stroke") == pressed["stroke"]
            and release.get("edge") == "release", timeout=6)
        released = released_frame["game"][FIXTURE]
        assert released["sequence"] > pressed["sequence"], released
        expected = {field: released[field] for field in ("map", "pawn", "guid", "sequence")}
        expected["lane"] = "memory"
        assert released_frame["game"].get(PUBLICATION, {}).get("held") == expected, released_frame["game"].get(PUBLICATION)
        edges, terminal = await matching_events()
        started = edges[0][native_smg_hold.KEY]
        assert (started["map"], started["pawn"], started["guid"], started["serial"]) == (
            OBSERVATORY, pawn, guid, terminal["serial"]), started
        assert (terminal["map"], terminal["pawn"], terminal["character_id"], terminal["status"], terminal["before"]) == (
            OBSERVATORY, pawn, character, "released", ROUNDS), terminal
        assert 0 < terminal["rounds"] == terminal["projectiles"] == ROUNDS - terminal["after"] < ROUNDS, terminal
        assert terminal["revision"] is None, terminal
        held = (await control(guest_run, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert held["actor_ammo"] == held["stored_ammo"] == terminal["after"], held
        for filename in ("native-guest-primary-events", "native-guest-held-fire-events"):
            assert not (guest.bridge.directory / filename).exists(), filename
        # The press fixture returns only after publishing its exact sequence. The accepted host edge and
        # absent mailbox files prove that press used the same in-process bridge as the release receipt.
        return {"stock_held_queue": {"press": pressed, "release": released, "release_publication": expected,
                                     "guest_edges": edges, "host_terminal": terminal}}
    finally:
        await control(guest_run, "native-guest-weapon-equip", target=previous["guid"])
        await scoped(previous["guid"], previous["kind"])
