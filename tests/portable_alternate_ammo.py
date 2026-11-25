"""Exercise stock guest round selection, firing, and Take Ammo in one campaign."""

import asyncio
import re
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_inventory
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import (
    FIFTY_CAL_FMJ, FIFTY_CAL_HEIAP, FIVE_SIX_AP, FIVE_SIX_FMJ, FORTY_FIVE_BALL, FORTY_FIVE_HOLLOW_POINT,
    NINE_MM_AP, NINE_MM_BALL, NINE_MM_VAMP, MAGNUM_338_AP, MAGNUM_338_FMJ, MAGNUM_338_OP,
)


async def verify_alternate_ammo(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    async def command(seat, action):
        return await probe(seat.bridge, "portable-dry-fire", action)

    async def read(seat, action, expected):
        last = None
        try:
            async with asyncio.timeout(10):
                while True:
                    last = await command(seat, action)
                    if last == expected:
                        return
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"{action} returned {last!r}, expected {expected!r}") from error

    async def stock_input(kind, frame, guid):
        event = f"native_guest_{kind}_input"
        result = await submit(guest_run, f"native-guest-{kind}-input-fixture")
        assert result["ok"], result
        press = result[f"native_guest_{kind}_fixture"]
        async with asyncio.timeout(12):
            while True:
                for row in await resident_events(guest_run, frame):
                    if row.get("sequence") == press["sequence"] and row["event"] in (event, event + "_rejected"):
                        assert row["event"] == event and row["native_guest_weapon"]["guid"] == guid, row
                        return row["native_guest_weapon"]
                await asyncio.sleep(.08)

    async def verify_round(arm, base, alternate, rounds, second=None, earlier=None):
        selected, reserve_before = earlier or (alternate, alternate.fixture_units)
        seats = host, guest
        assert await asyncio.gather(*(command(seat, arm) for seat in seats)) == ["armed", "armed"]
        host_reserve = await command(host, "reserve")
        slot, guid = (await command(host, "seed-alternate")).split(",")
        assert await command(guest, f"select-probe-slot\t{slot}") == "scoped"
        assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
        await read(guest, "projected", guid)
        assert await command(guest, "equip") == "selected"
        await asyncio.gather(read(host, "peer-round", base.item_path), read(guest, "round", base.item_path))
        frame = await wait_resident_status(guest_run, lambda view: view.get("state") == "connected"
            and view.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        reserve = await command(host, "seed-reserve")
        assert re.fullmatch(rf"[0-9A-F]{{32}},{alternate.fixture_units}", reserve), reserve
        await read(guest, "reserve", reserve)
        if second:
            following = await command(host, "seed-second-reserve")
            assert re.fullmatch(rf"[0-9A-F]{{32}},{second.fixture_units}", following), following
            await read(guest, "second-reserve", following)
        reloaded = await stock_input("reload", frame, guid)
        assert (reloaded["before"], reloaded["after"], reloaded["reserve_before"], reloaded["reserve_after"]) == (
            0, rounds, reserve_before, reserve_before - rounds), reloaded
        await asyncio.gather(read(host, "peer-round", selected.item_path), read(guest, "round", selected.item_path))
        await asyncio.gather(read(host, "peer-ready", f"{guid},{rounds},{rounds}"),
                             read(guest, "ready", f"{guid},{rounds},{rounds}"))

        assert await command(guest, "face-away") == "turned"
        shot = await stock_input("primary", frame, guid)
        remaining = rounds - 1
        assert (shot["before"], shot["after"], shot["projectiles"]) == (rounds, remaining, 1), shot
        await asyncio.gather(read(host, "peer-cycle-complete", f"{guid},{remaining},{remaining}"),
                             read(guest, "ready", f"{guid},{remaining},{remaining}"))

        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await wait_resident_status(guest_run, lambda view: view.get("game", {}).get(
            "native_inventory_view", {}).get("state") == "stock")
        pressed = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=f"{guid}|take-ammo")
        assert pressed["ok"] and pressed["stock_action"]["guid"] == guid, pressed
        unloaded = None
        async with asyncio.timeout(12):
            while unloaded is None:
                for entry in await resident_events(guest_run, frame):
                    result = entry.get(native_inventory.TAKE_AMMO_KEY, {})
                    if result.get("source_guid") == guid:
                        unloaded = result
                        break
                await asyncio.sleep(.08)
        assert (unloaded["class_path"], unloaded["split"], unloaded["remaining"]) == (
            selected.item_path, remaining, 0), unloaded
        await asyncio.gather(read(host, "peer-ready", f"{guid},0,0"), read(guest, "ready", f"{guid},0,0"))
        if second:
            await read(guest, "second-reserve", following)
        if earlier:
            await read(guest, "reserve", reserve)
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await wait_resident_status(guest_run, lambda view: view.get("game", {}).get(
            "native_inventory_view", {}).get("state") == "closed")
        assert await command(host, "reserve") == host_reserve and campaign_save_hashes(host.campaign) == checkpoint
        switched = None
        if arm == "arm-ammo-order":
            def units(stacks):
                return sum(int(stack.split(",")[1]) for stack in stacks.split("|") if stack)

            loaded = await stock_input("reload", frame, guid)
            await read(guest, "ready", f"{guid},{loaded['after']},{loaded['after']}")
            await read(guest, "round", NINE_MM_VAMP.item_path)
            old_reserve = units(await command(host, "peer-reserve"))
            new_reserve = units(await command(host, "peer-second-reserve"))
            pressed = time.time()
            assert await command(guest, f"ammo-selector\t{NINE_MM_AP.item_path}") == "stock alternate ammo selected"
            async with asyncio.timeout(12):
                while switched is None:
                    for row in await resident_events(guest_run, frame):
                        if row["time_unix"] < pressed:
                            continue
                        assert row["event"] != "native_guest_reload_input_rejected", row
                        proof = row.get("native_guest_weapon", {})
                        if row["event"] == "native_guest_reload_input" and proof.get("ammo_class") == NINE_MM_AP.item_path:
                            switched = proof
                            break
                    if switched is None:
                        await asyncio.sleep(.08)
            assert switched["guid"] == guid and (switched["before"], switched["after"]) == (0, rounds), switched
            await asyncio.gather(read(host, "peer-round", NINE_MM_AP.item_path), read(guest, "round", NINE_MM_AP.item_path))
            assert units(await command(host, "peer-reserve")) == old_reserve + loaded["after"]
            assert units(await command(host, "peer-second-reserve")) == new_reserve - switched["after"]
            assert campaign_save_hashes(host.campaign) == checkpoint
        untouched = alternate if earlier else second
        return {"class_path": selected.item_path, "reload": reloaded, "shot": shot, "take_ammo": unloaded,
                **({"stock_selector": switched} if switched else {}),
                **({"untouched_following_reserve": untouched.item_path} if untouched else {})}

    cases = (
        ("arm-alternate", FORTY_FIVE_BALL, FORTY_FIVE_HOLLOW_POINT, 8),
        ("arm-ammo-order", NINE_MM_BALL, NINE_MM_VAMP, 12, NINE_MM_AP),
        ("arm-intermediate-alt", FIVE_SIX_FMJ, FIVE_SIX_AP, 16),
        ("arm-fifty-alt", FIFTY_CAL_FMJ, FIFTY_CAL_HEIAP, 4),
        ("arm-csr-ap", MAGNUM_338_FMJ, MAGNUM_338_AP, 12),
    )

    async def run_case(*case, **options):
        try:
            return await verify_round(*case, **options)
        finally:
            await asyncio.gather(command(host, "disarm"), command(guest, "disarm"), return_exceptions=True)

    results = [await run_case(*case) for case in cases]
    # The CSR's unloaded AP stack occupies an earlier Backpack slot than the new OP reserve.
    results.append(await run_case("arm-sniper-op", MAGNUM_338_FMJ, MAGNUM_338_OP, 10,
                                  earlier=(MAGNUM_338_AP, results[-1]["take_ammo"]["split"])))
    return {"stock_alternate_ammo": {"pawn": pawn, "rounds": results}}
