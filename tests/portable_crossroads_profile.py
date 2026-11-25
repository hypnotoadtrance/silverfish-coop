"""Time co-op work on isolated seats before and after stock travel to Crossroads."""

import asyncio
from pathlib import Path
import math
import re

from portable_guest_world_start import wait_resident_status
from tools.silverfish import native_package_handoff_journal
from tools.silverfish.control import current_status, submit
from tools.silverfish.travel import CROSSROADS


def instrument_profile(source):
    code = (Path(__file__).with_name("fixtures") / "portable_crossroads_profile.lua").read_text(encoding="utf-8")
    anchor = "local function write_status("
    before, separator, rest = source.partition(anchor)
    if not separator:
        raise ValueError("The status publication anchor changed")
    names = r"(?:native_[a-z_]+|players(?:\.critters)?)\.[\w]+"
    wrapper = "status_publication.profile.call"
    rest, protected = re.subn(r"\bpcall\((" + names + r"|tick)(?=[,)])",
                              lambda m: 'pcall(' + wrapper + ', "' + m[1] + '", ' + m[1], rest)
    rest, direct = re.subn(
        r"(?<!function )\b(" + names + r"|inspect|write_status|read_command|observe_session|current_world)\((?=[^)]*\))",
                           lambda m: wrapper + '("' + m[1] + '", ' + m[1]
                           + ("" if rest[m.end()] == ")" else ", "), rest)
    if protected < 10 or direct < 20:
        raise ValueError("The gameplay timing anchors changed")
    mailbox = 'io.open(command_path, "rb")'
    assert before.count(mailbox) == 1
    before = before.replace(mailbox, f'{wrapper}("command_mailbox", io.open, command_path, "rb")')
    viewport = 'FindFirstOf("GameViewportClient")'
    assert before.count(viewport) == 1
    before = before.replace(viewport, f'{wrapper}("viewport_discovery", FindFirstOf, "GameViewportClient")')
    player_query = "statics:GetAllActorsOfClass(world, class, out)"
    assert before.count(player_query) == 1
    before = before.replace(player_query, f'{wrapper}("player_actor_query", function() {player_query} end)')
    fallback_query = 'FindAllOf("Player_FP_C")'
    assert before.count(fallback_query) == 1
    before = before.replace(fallback_query, f'{wrapper}("player_actor_fallback", FindAllOf, "Player_FP_C")')
    return before + "status_publication.profile = (function()\n" + code + "\nend)()\n\n" + anchor + rest


def instrument_input_mappings(source):
    original = "local function observe_mapping("
    anchor = "local function verified_mapping("
    if source.count(original) != 1 or source.count(anchor) != 1:
        raise ValueError("The input mapping timing anchors changed")
    wrapper = (
        "local function observe_mapping(...)\n"
        '    return SilverfishProfileCrossroadsCall("players.observe_mapping", collect_mapping, ...)\n'
        "end\n\n"
    )
    return source.replace(original, "local function collect_mapping(").replace(anchor, wrapper + anchor)


def instrument_inline_crate_scan(source):
    probes = {
        "sample(state, phase, status_row, profiles)": (
            "local readings = ", "crate_inline_scan", "sample, state, phase, status_row, profiles"),
        "refresh_blocks(scan)": ("            ", "crate_snapshot_refresh", "refresh_blocks, scan"),
        "refresh_blocks(sampler)": ("if sampled then ", "crate_command_refresh", "refresh_blocks, sampler"),
    }
    for call, (prefix, name, arguments) in probes.items():
        anchor = prefix + call
        if source.count(anchor) != 1:
            raise ValueError(f"The crate status timing anchor changed: {name}")
        source = source.replace(anchor, f'{prefix}SilverfishProfileCrossroadsCall("{name}", {arguments})')
    return source


def instrument_loose_discovery(source):
    anchor = "SilverfishDiscoverLoosePickups(pawn:GetAddress(), current.host)"
    if source.count(anchor) != 1:
        raise ValueError("The loose-item discovery anchor changed")
    return source.replace(anchor, 'SilverfishProfileCrossroadsCall("loose_discovery", '
                          'SilverfishDiscoverLoosePickups, pawn:GetAddress(), current.host)')


def instrument_npc_class_discovery(source):
    anchor = "local class = StaticFindObject(path)"
    if source.count(anchor) != 1:
        raise ValueError("The NPC marker class lookup anchor changed")
    return source.replace(anchor, 'local class = SilverfishProfileCrossroadsCall('
                          '"npc_marker_class_lookup", StaticFindObject, path)')


def instrument_shop_discovery(source):
    anchor = "pcall(shop_actors, world)"
    if source.count(anchor) != 1:
        raise ValueError("The stock shop actor discovery anchor changed")
    return source.replace(anchor, 'pcall(SilverfishProfileCrossroadsCall, "stock_shop_discovery", shop_actors, world)')


def instrument_journal_context(source):
    gate = 'and os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
    call = "local context = host(state, phase)\n    if not context or not same(menu:GetOwningPlayer()"
    if source.count(gate) != 1 or source.count(call) != 1:
        raise ValueError("The stock journal timing anchors changed")
    return source.replace(gate, 'and (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
                          ' or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")').replace(
        call, call.replace("host(state, phase)", 'SilverfishProfileCrossroadsCall("journal_host_context", host, state, phase)'))


def instrument_host_drop_context(source):
    anchor = "host_save.canonical_host(state, phase)"
    if source.count(anchor) != 1:
        raise ValueError("The host drop context timing anchor changed")
    return source.replace(anchor, 'SilverfishProfileCrossroadsCall('
                          '"host_drop_context", host_save.canonical_host, state, phase)')


def instrument_anomaly_work(source):
    calls = (
        "pyrogeist.apply", "shadows.project", "lamps.project", "observers.target", "observers.publish_rewards",
        "effects.observe_spatial_cues", "canisters.rows", "drum_fire.rows", "fixed.rows", "destructibles.rows", "acid_pools.rows",
    )
    start, separator, rest = source.partition("function fields.rows(")
    if not separator:
        raise ValueError("The anomaly status timing anchor changed")
    for call in calls:
        anchor = call + "("
        if rest.count(anchor) != 1:
            raise ValueError(f"The anomaly status call changed: {call}")
        rest = rest.replace(anchor, f'SilverfishProfileCrossroadsCall("anomalies.{call}", {call}, ')
    return start + separator + rest


def instrument_vital_reads(source):
    anchor = "function scope.vitals_for_status(details, save)\n"
    if source.count(anchor) != 1:
        raise ValueError("The vitals timing anchor changed")
    fixture = """local vital_details, vital_save
function SilverfishProfileCrossroadsVitals()
    local details, save = vital_details, vital_save
    local function from_lua()
        local fatigue, intox, sickness = scope.personal_flags(save)
        return save.PlayerHealth, save.PlayerFood, save.PlayerRegen, save.PlayerStim,
            fatigue, intox, sickness, save.PlayerRads
    end
    local native = table.pack(scope.vitals_for_status(details, save))
    local stock = table.pack(from_lua())
    for index = 1, 8 do assert(native[index] == stock[index], "native vital read differs at field " .. index) end
    local name, pawn_address, save_address = details.name, details.pawn:GetAddress(), save:GetAddress()
    assert(type(SilverfishReadBoundNativeVitals(name, pawn_address, save_address)) == "number", "native vitals unavailable")
    assert(SilverfishReadBoundNativeVitals(name, pawn_address, save_address + 1) == nil, "mismatched save read accepted")
    assert(SilverfishReadBoundNativeVitals(name, pawn_address + 1, save_address) == nil, "mismatched pawn read accepted")
    local rounds = 512
    local function measure(fn, ...)
        local before = os.clock()
        for index = 1, rounds do fn(...) end
        return (os.clock() - before) * 1000
    end
    local native_first = measure(scope.vitals_for_status, details, save)
    local lua_first = measure(from_lua)
    local lua_second = measure(from_lua)
    local native_second = measure(scope.vitals_for_status, details, save)
    return ("%d,%.3f,%.3f"):format(rounds * 2, native_first + native_second, lua_first + lua_second)
end

"""
    wrapped = source.replace(anchor, fixture + anchor + "    vital_details, vital_save = details, save\n")
    baseline = "SilverfishReadBoundNativeVitals(details.name, details.pawn:GetAddress(), save:GetAddress())"
    if wrapped.count(baseline) != 1:
        raise ValueError("The native vitals call changed")
    return wrapped.replace(baseline, 'SilverfishProfileCrossroadsCall("vitals.native", SilverfishReadBoundNativeVitals, '
                           'details.name, details.pawn:GetAddress(), save:GetAddress())')


def read_comparison(raw):
    summary, slow_frame = raw.split("!")
    duration, *sections = summary.split(";")
    totals = {}
    for section in sections:
        name, count, p50, p95, p99, maximum, total, *cycles = section.split(",")
        cpu = dict(zip(("p50", "p95", "p99", "max", "sum"), map(int, cycles), strict=True))
        totals[name] = dict(count=int(count), p50_ms=float(p50), p95_ms=float(p95), p99_ms=float(p99),
                            max_ms=float(maximum), sum_ms=float(total), thread_cycles=cpu)
        assert all(value >= 0 for value in cpu.values()), (name, cpu)
    assert float(duration) >= 3 and totals["frame"]["count"] > 10 and totals["write_status"]["count"] > 3
    peak, *calls = slow_frame.split(";")
    elapsed, peak_cycles = peak.split(",")
    slow_calls, call_cycles = {}, {}
    for entry in calls:
        name, wall, cycles = entry.split(",")
        slow_calls[name], call_cycles[name] = float(wall), int(cycles)
    return {"seconds": float(duration), "timings": totals,
            "slowest_interval": {"elapsed_ms": float(elapsed), "inclusive_calls_ms": slow_calls,
                                 "thread_cycles": int(peak_cycles), "inclusive_call_cycles": call_cycles}}


async def profile_world(host, guest, area, probe):
    guest_run = guest.client / "run"
    seats = {"host": host, "guest": guest}
    runs = {"host": host.campaign / "run", "guest": guest_run}
    counters = ("quic_datagrams_sent", "quic_payload_bytes_sent", "game_packets_queued", "game_packets_delivered",
                "relay_queue_evicted_frames", "relay_queue_expired_frames", "snapshot_responses_encoded",
                "snapshot_encoded_bytes", "snapshot_plaintext_bytes", "snapshot_responses_decoded", "snapshot_received_bytes")

    async def transport():
        frames = await asyncio.gather(*(current_status(run) for run in runs.values()))
        return {seat: frame.get("transport", {}) for seat, frame in zip(runs, frames, strict=True)}

    def traffic(before, after):
        return {seat: {**{key: after[seat][key] - before[seat][key] for key in counters
                          if key in before[seat] and key in after[seat] and before[seat][key] <= after[seat][key]},
                       "keepalive_rtt_us": after[seat].get("keepalive_rtt_us"),
                       "relay_queue_wait_max_ns": after[seat].get("relay_queue_wait_max_ns")}
                for seat in runs}

    async def command(seat, action):
        response = await probe(seat.bridge, "crossroads-profile", action, reply_end="|end")
        return response.removesuffix("|end")

    async def arm():
        assert await asyncio.gather(*(command(seat, "arm") for seat in seats.values())) == ["armed", "armed"]

    async def read(seat):
        async with asyncio.timeout(7):
            while (raw := await command(seat, "read")) == "sampling":
                await asyncio.sleep(.16)
        return read_comparison(raw)

    async def collect():
        profiles = await asyncio.gather(*(read(seat) for seat in seats.values()))
        for seat, profile in zip(seats, profiles, strict=True):
            timings = profile["timings"]
            for operation in ("command_mailbox", "crate_inline_scan", "npc_marker_class_lookup", "journal_host_context",
                              "player_actor_fallback"):
                assert operation not in timings, (operation, timings.get(operation))
            discoveries = timings["loose_discovery"]["count"]
            assert 0 < discoveries <= math.ceil(profile["seconds"] / .125) + 1, profile
            lookups = timings.get("player_actor_query", {}).get("count", 0)
            if seat == "guest":
                assert 0 < lookups <= math.ceil(profile["seconds"]) + 1, (area, lookups, profile["seconds"])
            else:
                assert lookups == 0, (area, lookups)
                drop_checks = timings.get("host_drop_context", {}).get("count", 0)
                assert 0 < drop_checks <= timings["write_status"]["count"], (area, drop_checks)
        return dict(zip(seats, profiles, strict=True))

    async def compare_vitals(seat):
        calls, native_ms, lua_ms = map(float, (await command(seat, "vitals")).split(","))
        return {"reads_per_path": int(calls), "native_ms": native_ms, "lua_ms": lua_ms,
                "all_vitals_matched": True, "wrong_pawn_and_save_refused": True}

    async def compare_rows(seat):
        calls, native_ms, lua_ms, native_cycles, lua_cycles = (await command(seat, "rows")).split(",")
        return {"rows_per_path": int(calls), "native_ms": float(native_ms), "lua_ms": float(lua_ms),
                "native_cycles": int(native_cycles), "lua_cycles": int(lua_cycles), "all_rows_matched": True}

    def position(status):
        return status.get("game", {}).get("pawns", {}).get("local", {}).get("location")

    def pawns_here(frame):
        game = frame.get("game", {})
        pawns = game.get("pawns", {})
        return (game.get("map") == area and set(pawns) == {"local", "guest"}
                and all(row.get("name", "").startswith(f"Player_FP_C {area}.") and row.get("location")
                        for row in pawns.values())
                and pawns["local"]["name"] != pawns["guest"]["name"])

    baseline = await wait_resident_status(guest_run, pawns_here)
    wall_status = await asyncio.gather(*(wait_resident_status(
        run, lambda frame: frame.get("game", {}).get("map") == area
        and frame["game"].get("native_zone_wall", {}).get("state") == "ready"
        and not frame["game"]["native_zone_wall"]["failure"]) for run in runs.values()))
    before = await transport()
    await arm()
    idle = await collect()
    after_idle = await transport()
    await arm()
    # Use normal guest movement with short pulses near the stock arrival point.
    movement = []
    for x, y in ((1, 0), (0, 1), (-1, 0), (0, -1)):
        reply = await submit(guest_run, "walk", x=x, y=y, milliseconds=550, continuous=True)
        assert reply["ok"] and reply["movement_scheduled"], reply
        await asyncio.sleep(.58)
        point = position(await current_status(guest_run))
        if point:
            movement.append(math.dist(point, position(baseline)))
    moving = await collect()
    assert movement and max(movement) >= 40, movement
    after_movement = await transport()
    settled = await wait_resident_status(guest_run, pawns_here)
    comparison = await asyncio.gather(*(compare_vitals(seat) for seat in seats.values()))
    rows = await asyncio.gather(*(compare_rows(seat) for seat in seats.values()))
    journal = {}
    if area == CROSSROADS:
        async def journal_action(action, receipt):
            reply = await host.bridge.command("native-host-invite-fixture", action)
            assert reply["ack"]["message"] == receipt, reply

        await journal_action("open", "native_host_invite_journal_open")
        view = await wait_resident_status(runs["host"], lambda frame:
            frame["game"].get("native_host_invite_journal") == "journal"
            and frame["game"].get(native_package_handoff_journal.JOURNAL, {}).get("state") == "idle")
        await journal_action("close", "native_host_invite_journal_closed")
        await wait_resident_status(runs["host"], lambda frame: "native_host_invite_journal" not in frame["game"])
        journal = {"journal_open_and_close": view["game"][native_package_handoff_journal.JOURNAL]}
    return {"idle": idle, "guest_walking": moving, "guest_max_displacement_cm": max(movement),
            "transport_idle": traffic(before, after_idle), "transport_guest_walking": traffic(after_idle, after_movement),
            "row_format_comparison": dict(zip(seats, rows, strict=True)),
            "zone_walls": {seat: frame["game"]["native_zone_wall"]
                           for seat, frame in zip(seats, wall_status, strict=True)},
            "guest_roster": {slot: row["name"] for slot, row in settled["game"]["pawns"].items()},
            "vitals_comparison": dict(zip(seats, comparison, strict=True)), **journal}
