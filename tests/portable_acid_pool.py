"""Witness the stock authored Lab East Wing pool on both installed co-op seats."""

import asyncio
import json
import math
from pathlib import Path

from live_multi_smoke import campaign_save_hashes
from portable_crossroads_profile import compare_acid_reader
from portable_guest_world_start import wait_resident_status
from tools.silverfish.native_anomaly_effects import ACID_POOL_VIEW, ACID_POOLS
from tools.silverfish.travel import CROSSROADS, EAST_WING, OBSERVATORY, RUIN_FIELDS

_POOL = "AcidPool_BP_C_1"
_CONTROL = "AcidPool_BP_C_9"


def acid_pool_overlay(game, portable_script_overlay):
    fixture = (Path(__file__).with_name("fixtures") / "portable_acid_pool_probe.lua").read_text(encoding="utf-8")
    declarations = "function pools.rows(state, phase, row)\n"
    publication = "    if not current or not current.complete then return end\n"

    def add_fixture(script):
        if script.count(declarations) != 1 or script.count(publication) != 1:
            raise ValueError("The private package lacks the authored acid pool lifecycle module")
        return (script.replace(declarations, fixture + "\n" + declarations, 1)
                .replace(publication, "    poll_acid_fixture(state, phase, current)\n" + publication, 1))

    return portable_script_overlay(game, "native_acid_pools.lua", add_fixture)


async def cross_to_east_wing(host, guest, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    visits = []

    def ready(frame, area):
        game = frame.get("game", {})
        return (frame.get("state") == "hosting" and frame.get("local_bridge") == "memory"
                and game.get("map") == area and not frame.get("awaiting_zone_guest")
                and game.get("native_zone", {}).get("state") == "ready"
                and game.get("native_guest_save", {}).get("state") == "bound")

    for source, destination, tag, actor in (
        (OBSERVATORY, CROSSROADS, "Obsv_Etr", "Level_Exit_BP_C_1"),
        (CROSSROADS, RUIN_FIELDS, "CR_RF", "Level_Exit_BP_C_273"),
        (RUIN_FIELDS, EAST_WING, "RgEast_Flood", "Dungeon_TransitionDoor_BP_C_1"),
    ):
        start = await wait_resident_status(host_run, lambda f: ready(f, source))
        pawn = start["game"]["native_guest_save"]["pawn"]
        if source == CROSSROADS:
            expected = f"Level_Exit_BP_C {source}.CrossRoads_P:PersistentLevel.{actor}"
            for step in ("guest-near", "host-near"):
                answer = await probe(host.bridge, "portable-ruin-exit", f"{step}\t{pawn}")
                assert answer in (expected, expected + "0"), answer
        elif source == RUIN_FIELDS:
            for player in (pawn, "local"):
                await host.bridge.command("native-door-position", player, "near", tag)
        else:
            for player in (pawn, "local"):
                await host.bridge.command("native-exit-position", player, "near", tag, actor)
        if source != RUIN_FIELDS:
            await wait_resident_status(host_run, lambda f: ready(f, source)
                                       and f["game"].get("native_zone_wall", {}).get("host_open") is True)
        if source == CROSSROADS:
            answer = await probe(host.bridge, "portable-ruin-exit", f"host-inside\t{pawn}")
            assert answer in (expected, expected + "0"), answer
        elif source == RUIN_FIELDS:
            await host.bridge.command("native-door-probe", "local", tag)
        else:
            await host.bridge.command("native-exit-position", "local", "inside", tag, actor)
        reached = await wait_resident_status(host_run, lambda f: ready(f, destination)
                                             and f.get("zone_epoch") != start["zone_epoch"], timeout=60)
        epoch = reached["zone_epoch"]
        await wait_resident_status(guest_run, lambda f: f.get("state") == "connected"
                                   and f.get("local_bridge") == "memory" and f.get("gameplay_fresh") is True
                                   and f.get("link_connected") is True and f.get("game", {}).get("map") == destination
                                   and f.get("zone_epoch") == epoch, timeout=35)
        visits.append({"source": source, "destination": destination, "epoch": epoch, "entrance": tag, "actor": actor})
    return visits


def pool_readback(raw):
    def parse(value):
        phase, dot, hidden, collision, present = value.split(",")
        assert phase in {"a", "f", "e", "gone"} and {dot, hidden, collision, present} <= {"0", "1"}, value
        return {"phase": phase, "dot_active": dot == "1", "hidden": hidden == "1", "collision": collision == "1",
                "present": present == "1"}

    selected, control = raw.split(";")
    return parse(selected), parse(control)


async def verify_dynamic_pool(host, guest, operate, profile_probe):
    assert await operate(guest, "spawn-guest") == "suppressed"
    identity = await operate(host, "spawn-host")
    read = f"read-dynamic\t{identity}"
    source = await operate(host, read)
    summary, pose = source.split(";")
    assert summary == "a,1,0,1,1", source

    async def guest_state(phase):
        async with asyncio.timeout(9):
            while True:
                observed = await operate(guest, read)
                if observed.startswith(phase + ","):
                    state, actual = observed.split(";")
                    assert state == f"{phase},0,0,0,1", observed
                    assert all(math.dist(tuple(map(float, left.split(","))), tuple(map(float, right.split(",")))) < .02
                               for left, right in zip(pose.split(":"), actual.split(":"), strict=True)), observed
                    return
                await asyncio.sleep(.09)

    await guest_state("a")
    comparison = await compare_acid_reader(host, profile_probe, dynamic=True) if profile_probe else None
    assert await operate(host, f"fizzle-dynamic\t{identity}") == "f"
    await guest_state("f")
    await guest_state("e")
    assert await operate(host, f"remove-dynamic\t{identity}") == "removed"
    async with asyncio.timeout(8):
        while await operate(guest, read) != "absent":
            await asyncio.sleep(.09)
    assert await operate(host, read) == "absent"
    return {"identity": identity, "pose": pose, "guest_phases": ["a", "f", "e"], "guest_only_pool_suppressed": True,
            "retired_on_both_seats": True, "guest_damage_component_inactive": True, "reader_comparison": comparison}


async def verify_acid_pool(host, guest, probe, *, profile=False):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    visits = await cross_to_east_wing(host, guest, probe)
    epoch = visits[-1]["epoch"]

    async def operate(seat, action):
        return await probe(seat.bridge, "portable-acid-pool", action)

    qualification = await asyncio.gather(*(operate(seat, "qualify") for seat in (host, guest)), return_exceptions=True)
    evidence = dict(zip(("host", "guest"), map(str, qualification), strict=True))
    (host_run / "portable-acid-pool-qualification.json").write_text(
        json.dumps({"stock_route": visits, "qualification": evidence}, indent=2) + "\n", encoding="utf-8")
    for result in qualification:
        if isinstance(result, BaseException):
            raise result
    await wait_resident_status(host_run, lambda f: {_POOL + ":a", _CONTROL + ":a"} <=
                               set(f.get("game", {}).get(ACID_POOLS, ())), timeout=12)
    await wait_resident_status(guest_run, lambda f: f.get("game", {}).get(ACID_POOL_VIEW) ==
                               {"map": EAST_WING, "epoch": epoch})
    before = campaign_save_hashes(host.campaign)

    authority, observer = await asyncio.gather(*(operate(seat, "read") for seat in (host, guest)))
    host_initial, host_control = pool_readback(authority)
    guest_initial, guest_control = pool_readback(observer)
    assert all(value["phase"] == "a" and not value["hidden"]
               for value in (host_initial, host_control, guest_initial, guest_control)), (authority, observer)
    assert host_initial["dot_active"] and not guest_initial["dot_active"], (authority, observer)
    assert await operate(guest, "guest-extinguish") == "blocked"
    placed_comparison = await compare_acid_reader(host, probe, dynamic=False) if profile else None

    center = [float(value) for value in (await operate(host, "begin")).split(",")]
    try:
        assert len(center) == 4 and all(math.isfinite(value) for value in center), center
        assert math.dist(center[:3], [17086.80, -8693.64, -1345.25]) < 1000 and 10 <= center[3] <= 500, center
        async with asyncio.timeout(10):
            while True:
                hits, _, hp_before, hp_after, _, _ = map(float, (await operate(host, "damage")).split(","))
                if hits and hp_after < hp_before:
                    break
                await asyncio.sleep(.12)
    finally:
        damage = [float(value) for value in (await operate(host, "return")).split(",")]
    hits, host_hits, hp_before, hp_after, host_before, host_after = damage
    assert hits >= 1 and host_hits == 0 and 0 < hp_after < hp_before and host_after >= host_before, damage

    loop = asyncio.get_running_loop()
    extinguish_started = loop.time()
    assert await operate(host, "fizzle") == "f"
    phases = {"host": ["a", "f"], "guest": ["a"]}

    async def read_pair():
        readings = await asyncio.gather(*(operate(seat, "read") for seat in (host, guest)))
        selected_pools = []
        for raw, unchanged in zip(readings, (host_control, guest_control), strict=True):
            selected, control = pool_readback(raw)
            assert control == unchanged, (raw, unchanged)
            selected_pools.append(selected)
        assert not selected_pools[1]["dot_active"], readings
        return selected_pools

    async with asyncio.timeout(12):
        while True:
            readings = await read_pair()
            for key, selected in zip(phases, readings, strict=True):
                if selected["phase"] != phases[key][-1]:
                    phases[key].append(selected["phase"])
            if all(selected["phase"] == "e" and not selected["dot_active"] for selected in readings):
                break
            await asyncio.sleep(.12)
    assert all(values == ["a", "f", "e"] for values in phases.values()), phases
    dynamic = await verify_dynamic_pool(host, guest, operate, probe if profile else None)

    def roster_retired(frame):
        game = frame.get("game", {})
        roster = game.get(ACID_POOLS, ())
        return (frame.get("zone_epoch") == epoch and game.get("map") == EAST_WING and _CONTROL + ":a" in roster
                and not any(row.startswith(_POOL + ":") for row in roster))

    def noninteractive(selected):
        return (selected["phase"] == "gone" and selected["hidden"] and not selected["collision"]
                and not selected["dot_active"])

    # Stock waits 60 seconds at extinguished before its separate two-second fade and destruction.
    host_removed_after = None
    async with asyncio.timeout_at(extinguish_started + 80):
        while True:
            readings = await read_pair()
            if not readings[0]["present"] and noninteractive(readings[0]):
                if host_removed_after is None:
                    host_removed_after = loop.time() - extinguish_started
                if noninteractive(readings[1]) and (not readings[1]["present"]
                        or loop.time() - extinguish_started >= host_removed_after + 4):
                    break
            await asyncio.sleep(.5)
    await wait_resident_status(host_run, roster_retired, timeout=8)
    assert campaign_save_hashes(host.campaign) == before, "The stock pool trial unexpectedly changed the saved checkpoint"
    return {"stock_acid_pool": {"source": _POOL, "untouched": _CONTROL, "stock_route": visits,
                                "sphere": center, "host_received_no_acid": host_hits == 0,
                                "stock_damage_calls": int(hits), "guest_health": [hp_before, hp_after],
                                "guest_extinguish_veto": True, "phases": phases,
                                "retirement": {"trigger": "stock_timer", "host_roster_omitted_pool": True,
                                               "host_removed_after_seconds": round(host_removed_after, 3),
                                               "host": readings[0], "guest": readings[1]},
                                "dynamic": dynamic, "placed_reader_comparison": placed_comparison,
                                "checkpoint_unchanged": True}}
