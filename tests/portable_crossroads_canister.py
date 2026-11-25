"""Exercise placed canister sampling and gameplay on the resident Crossroads pair."""

import asyncio
import math
import time

from portable_guest_world_start import wait_resident_status
from tools.silverfish.native_anomaly_effects import CANISTERS
from tools.silverfish.travel import CROSSROADS


TARGET = f"{CROSSROADS}.CrossRoads_P:PersistentLevel.Prop_PropaneTank_Small_C_12"


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
    fixture = """    if args[2] == "native-canister-sample-fixture" or args[2] == "native-canister-shove-fixture" then
        local path = args[3]
        assert(#args == 3 and state.map == "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
            and path and #path <= 512 and (phase == "hosting" or phase == "connected"))
        local actor = StaticFindObject(path)
        local address = valid(actor) and actor:GetAddress()
        local entry = address and active[address]
        assert(entry and entry.path == path and same(actor:GetWorld(), world) and same(state.world, world))
        entry.sample_position = true
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
    return source.replace(anchor, anchor + fixture)


async def verify_crossroads_canister(host, guest, _pawn, resident_events, campaign_save_hashes):
    host_run = host.campaign / "run"
    guest_run = guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    def stock(frame):
        return frame["game"].get(CANISTERS, {}).get(TARGET, {})

    async def command(seat, verb):
        return await seat.bridge.command(f"native-canister-{verb}-fixture", TARGET, timeout=5)

    async def sample(seat):
        result = await command(seat, "sample")
        count, native_ms, lua_ms = map(float, result["ack"]["message"].split(","))
        return {"reads_per_path": int(count), "native_ms": native_ms, "lua_ms": lua_ms,
                "sample_matched": True, "wrong_world_refused": True}

    await wait_resident_status(host_run, lambda frame: stock(frame).get("phase") == 0)
    guest_initial = await wait_resident_status(guest_run, lambda frame: stock(frame).get("phase") == 0)
    proof = {"host": await sample(host), "guest": await sample(guest)}
    blocked = guest_initial["game"].get("native_canister_blocked", 0)
    await command(guest, "guest-guard")
    guarded = await wait_resident_status(guest_run, lambda frame: frame["game"].get("native_canister_blocked", 0) >= blocked + 2)
    assert stock(guarded)["phase"] == 0
    await command(host, "prepare")
    initial = stock(await wait_resident_status(host_run, lambda frame: stock(frame).get("location")))
    await command(host, "shove")
    moved = stock(await wait_resident_status(host_run, lambda frame: stock(frame).get("location")
                 and math.dist(stock(frame)["location"], initial["location"]) > 30))
    guest_moved = stock(await wait_resident_status(guest_run, lambda frame: stock(frame).get("location")
                       and math.dist(stock(frame)["location"], initial["location"]) > 30))
    proof["host_motion_cm"] = math.dist(initial["location"], moved["location"])
    proof["guest_motion_cm"] = math.dist(initial["location"], guest_moved["location"])

    started = time.time()
    await command(host, "ignite")
    lit = await wait_resident_status(guest_run, lambda frame:
                                    stock(frame).get("phase") == 1 and stock(frame).get("particles_active"))
    proof["guest_fire"] = stock(lit)
    for _ in range(5):
        await command(host, "shoot")
        await asyncio.sleep(.35)
        if stock(await wait_resident_status(host_run, lambda frame: stock(frame)))["phase"] == 2:
            break
    await wait_resident_status(host_run, lambda frame: stock(frame).get("phase") == 2, timeout=15)
    finished = await wait_resident_status(guest_run, lambda frame: stock(frame).get("phase") == 2)
    async with asyncio.timeout(7):
        while not (blasts := [entry for entry in await resident_events(guest_run, finished)
                              if entry["event"] == "native_canister_effect_played" and entry["time_unix"] >= started]):
            await asyncio.sleep(.12)
    assert len(blasts) == 1 and blasts[0]["particles"], blasts
    assert campaign_save_hashes(host.campaign) == checkpoint
    proof["guest_blast"] = blasts[0]
    proof["removed_on_both_seats"] = True
    return {"stock_canister": proof}
