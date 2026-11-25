"""Exercise placed canister sampling and gameplay on the resident Crossroads pair."""

import asyncio
import math
import time

from portable_guest_world_start import wait_resident_status
from tools.silverfish.native_anomaly_effects import CANISTERS
from tools.silverfish.travel import CROSSROADS


PREFIX = f"{CROSSROADS}.CrossRoads_P:PersistentLevel."
CANISTER = PREFIX + "Prop_PropaneTank_Small_C_12"
DRUM = PREFIX + "Prop_FeulDrum_C_33"


def allow_portable_canister_fixture(source):
    gate = 'local host_enabled = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
    if source.count(gate) != 1:
        raise ValueError("The stock canister fixture gate changed")
    return source.replace(gate, gate + '\n    or (os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
                          + '\n        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")')


def instrument_canisters(source):
    anchor = "function canisters.command(args, state, phase)\n"
    if source.count(anchor) != 1:
        raise ValueError("The stock canister command handler changed")
    fixture = """    if args[2] == "native-canister-observed-fixture" then
        local witness = canister_witness[args[3]] or {frames = 0, active = 0, apply_lit = 0, apply_cold = 0, apply_removed = 0,
            applied_lit = 0, failed_lit = 0}
        return ("%d,%d,%d,%d,%d,%d,%d"):format(witness.frames, witness.active, witness.apply_lit, witness.apply_cold,
            witness.apply_removed, witness.applied_lit, witness.failed_lit)
    end
    if args[2] == "native-canister-sample-fixture" or args[2] == "native-canister-shove-fixture"
        or args[2] == "native-canister-approach-fixture" then
        local path = args[3]
        assert(#args == 3 and state.map == "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
            and path and #path <= 512 and (phase == "hosting" or phase == "connected"))
        local actor = StaticFindObject(path)
        local address = valid(actor) and actor:GetAddress()
        local entry = address and active[address]
        assert(entry and entry.path == path and same(actor:GetWorld(), world) and same(state.world, world))
        entry.sample_position = true
        if args[2] == "native-canister-approach-fixture" then
            local pawn = #state.guests == 1 and state.guests[1].pawn
            assert(phase == "hosting" and valid(pawn) and pawn:HasAuthority() and not pawn:IsLocallyControlled())
            local trace = StaticFindObject("/Script/Engine.Default__KismetSystemLibrary")
            local center, view = actor:K2_GetActorLocation(), pawn:K2_GetActorRotation()
            local blank = {R = 0, G = 0, B = 0, A = 0}
            for _, direction in ipairs({{0, -1}, {1, 0}, {-1, 0}, {0, 1}}) do
                local x, y = center.X + direction[1] * 3000, center.Y + direction[2] * 3000
                local hit = {}
                if trace:LineTraceSingle(pawn, {X = x, Y = y, Z = center.Z + 1200},
                    {X = x, Y = y, Z = center.Z - 2200}, 0, false, {pawn, actor}, 0, hit, true, blank, blank, 0) then
                    local floor = (hit.OutHit or hit).ImpactPoint
                    if floor and pawn:K2_TeleportTo({X = floor.X, Y = floor.Y, Z = floor.Z + 100}, view) then
                        pawn:ForceNetUpdate()
                        return "Placed the guest within stock canister replication range"
                    end
                end
            end
            error("No safe stock floor near the fixture canister", 0)
        end
        if args[2] == "native-canister-shove-fixture" then
            assert(phase == "hosting" and actor:HasAuthority() and mesh(actor))
            actor.PropMesh:AddImpulse({X = 0, Y = 310, Z = 420}, FName("None"), true)
            return "Applied a stock physics impulse to the canister"
        end
        local world_address, hosting = world:GetAddress(), phase == "hosting"
        local function from_native()
            return SilverfishReadNativePlacedCanister(address, world_address, hosting, entry.lit == true, true)
        end
        local native, reflected = table.pack(from_native()), table.pack(sample_lua(entry, hosting))
        assert(native.n == 6 and reflected.n == native.n, "The native canister sample was unavailable")
        for index = 1, 6 do assert(native[index] == reflected[index], "Canister sample differs at field " .. index) end
        assert(SilverfishReadNativePlacedCanister(address, world_address + 1, hosting, false, true) == nil,
            "The sampler accepted a different world")
        local saved_sample, calls = entry.sample, 0
        local function count_rows(...)
            calls = calls + 1
            return ...
        end
        local _, burning, health, x, y, z = table.unpack(native, 1, native.n)
        observed_row(entry, count_rows, burning, false, health, x, y, z)
        observed_row(entry, count_rows, burning, false, health, x, y, z)
        assert(calls == 1, "An unchanged status sample was formatted twice")
        observed_row(entry, count_rows, burning, false, health, x + 1, y, z)
        observed_row(entry, count_rows, not burning, false, health, x + 1, y, z)
        observed_row(entry, count_rows, not burning, true, health, x + 1, y, z)
        observed_row(entry, count_rows, not burning, true, health - 1, x + 1, y, z)
        assert(calls == 5, "A changed canister sample reused a stale row")
        entry.sample = saved_sample
        local count = 512
        local function time_reads(read, ...)
            local start = os.clock()
            for index = 1, count do read(...) end
            return (os.clock() - start) * 1000
        end
        local native_ms = time_reads(from_native)
        local lua_ms = time_reads(sample_lua, entry, hosting) + time_reads(sample_lua, entry, hosting)
        native_ms = native_ms + time_reads(from_native)
        return ("%d,%.3f,%.3f"):format(count * 2, native_ms, lua_ms)
    end
"""
    source = source.replace("local canisters = {}", """local canisters = {}
local canister_witness = {}
local function observe_canister(path)
    if not canister_witness[path] then
        canister_witness[path] = {frames = 0, active = 0, apply_lit = 0, apply_cold = 0, apply_removed = 0,
            applied_lit = 0, failed_lit = 0}
    end
    return canister_witness[path]
end""")
    for line, observation in (
        ('                rows[#rows + 1] = observed_row(entry, row, burning, particles_active, health, x, y, z)',
         '                local witness = observe_canister(entry.path)\n'
         '                if burning then witness.frames = witness.frames + 1 end\n'
         '                if particles_active then witness.active = witness.active + 1 end\n'),
        ('        local path, mode = args[index]:match("^([^|]+)|([012])$")',
         '        local witness = path and observe_canister(path)\n'
         '        if mode == "1" then witness.apply_lit = witness.apply_lit + 1 end\n'
         '        if mode == "0" then witness.apply_cold = witness.apply_cold + 1 end\n'
         '        if mode == "2" then witness.apply_removed = witness.apply_removed + 1 end'),
        ('                fire_view(entry, mode == "1")',
         '                if mode == "1" then\n'
         '                    if entry.lit then witness.applied_lit = witness.applied_lit + 1\n'
         '                    else witness.failed_lit = witness.failed_lit + 1 end\n'
         '                end'),
    ):
        if source.count(line) != 1:
            raise ValueError("The canister observation anchor changed")
        source = source.replace(line, observation + line if "rows[#rows" in line else line + "\n" + observation)
    return source.replace(anchor, anchor + fixture)


async def verify_crossroads_canister(host, guest, _pawn, resident_events, campaign_save_hashes):
    host_run = host.campaign / "run"
    guest_run = guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    def stock(frame, target):
        return frame["game"].get(CANISTERS, {}).get(target, {})

    async def command(seat, verb, target):
        return await seat.bridge.command(f"native-canister-{verb}-fixture", target, timeout=5)

    async def sample(seat, target):
        result = await command(seat, "sample", target)
        count, native_ms, lua_ms = map(float, result["ack"]["message"].split(","))
        return {"reads_per_path": int(count), "native_ms": native_ms, "lua_ms": lua_ms,
                "sample_matched": True, "wrong_world_refused": True, "unchanged_row_reused": True,
                "row_refreshed_for_movement_fire_particles_and_health": True}

    async def stage(target):
        await wait_resident_status(host_run, lambda frame: stock(frame, target).get("phase") == 0)
        await wait_resident_status(guest_run, lambda frame: stock(frame, target).get("phase") == 0)
        reads = {"host": await sample(host, target), "guest": await sample(guest, target)}
        initial = stock(await wait_resident_status(host_run, lambda frame: stock(frame, target).get("location")), target)
        await command(host, "approach", target)
        await wait_resident_status(host_run, lambda frame: 2500 <= math.dist(
            frame["game"]["pawns"]["guest"]["location"], initial["location"]) <= 4000)
        guest_frame = await wait_resident_status(guest_run, lambda frame: 2500 <= math.dist(
            frame["game"]["pawns"]["local"]["location"], initial["location"]) <= 4000)
        return reads, initial, guest_frame

    canister, initial, guest_initial = await stage(CANISTER)
    blocked = guest_initial["game"].get("native_canister_blocked", 0)
    await command(guest, "guest-guard", CANISTER)
    guarded = await wait_resident_status(guest_run, lambda frame: frame["game"].get("native_canister_blocked", 0) >= blocked + 2)
    assert stock(guarded, CANISTER)["phase"] == 0
    await command(host, "shove", CANISTER)
    moved = stock(await wait_resident_status(host_run, lambda frame: stock(frame, CANISTER).get("location")
                 and math.dist(stock(frame, CANISTER)["location"], initial["location"]) > 30), CANISTER)
    guest_moved = stock(await wait_resident_status(guest_run, lambda frame: stock(frame, CANISTER).get("location")
                       and math.dist(stock(frame, CANISTER)["location"], initial["location"]) > 30), CANISTER)
    canister["host_motion_cm"] = math.dist(initial["location"], moved["location"])
    canister["guest_motion_cm"] = math.dist(initial["location"], guest_moved["location"])

    drum, original, _ = await stage(DRUM)
    await command(host, "prepare", DRUM)
    started = time.time()
    await command(host, "shoot", DRUM)
    ignited = await wait_resident_status(host_run, lambda frame: stock(frame, DRUM).get("phase") == 1)
    assert stock(ignited, DRUM)["health"] < original["health"], stock(ignited, DRUM)
    lit = await wait_resident_status(guest_run, lambda frame: stock(frame, DRUM).get("phase") == 1)
    drum["guest_fire"] = stock(lit, DRUM)
    observed = await command(guest, "observed", DRUM)
    fire_frames, particle_frames, ignition_requests, _, _, activation_calls, rejected_activations = map(
        int, observed["ack"]["message"].split(","))
    assert fire_frames and ignition_requests and activation_calls == 1 and rejected_activations == 0, observed
    drum["guest_fire_activation_calls"] = activation_calls
    drum["guest_continuous_particles_observed"] = particle_frames > 0
    await wait_resident_status(host_run, lambda frame: stock(frame, DRUM).get("phase") == 2, timeout=24)
    finished = await wait_resident_status(guest_run, lambda frame: stock(frame, DRUM).get("phase") == 2)
    async with asyncio.timeout(7):
        while not (blasts := [entry for entry in await resident_events(guest_run, finished)
                              if entry["event"] == "native_fuel_drum_effect_played" and entry["time_unix"] >= started]):
            await asyncio.sleep(.12)
    # The muted run verifies cue dispatch and reports particle creation separately.
    assert len(blasts) == 1, blasts
    assert campaign_save_hashes(host.campaign) == checkpoint
    drum["guest_blast"] = blasts[0]
    drum["removed_on_both_seats"] = True
    return {"stock_canister": {"propane": canister, "fuel_drum": drum}}
