"""Exercise one installed guest LightAR trigger and reload through stock input."""

import asyncio
import re

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import FIREARMS, FIVE_SIX_FMJ
from tools.silverfish.travel import OBSERVATORY


async def verify_stock_light_ar(host, guest, pawn, probe):
    profile = next(weapon for weapon in FIREARMS if weapon.kind == "light-ar-01")
    assert profile.magazine.ammo == FIVE_SIX_FMJ
    units = FIVE_SIX_FMJ.fixture_units
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_frame = await wait_resident_status(host_run, lambda frame: frame.get("local_bridge") == "memory"
                                           and frame.get("game", {}).get("map") == OBSERVATORY)
    assert host_frame["game"]["native_guest_save"]["pawn"] == pawn
    before = campaign_save_hashes(host.campaign)

    async def command(seat, action):
        return await probe(seat.bridge, "portable-dry-fire", action)

    async def wait_readback(seat, action, expected):
        async with asyncio.timeout(8):
            while await command(seat, action) != expected:
                await asyncio.sleep(.05)

    async def stock_input(kind, frame, guid):
        event = f"native_guest_{kind}_input"
        result = await submit(guest_run, f"native-guest-{kind}-input-fixture")
        assert result["ok"], result
        press = result[f"native_guest_{kind}_fixture"]
        assert press["stock_wrappers"] == 2, press
        async with asyncio.timeout(8):
            while True:
                for row in await resident_events(guest_run, frame):
                    if row.get("sequence") == press["sequence"] and row["event"] in (event, event + "_rejected"):
                        assert row["event"] == event and row["native_guest_weapon"]["guid"] == guid, row
                        return row["native_guest_weapon"]
                await asyncio.sleep(.08)

    try:
        host_armed, guest_armed = await asyncio.gather(command(host, "arm-light-ar"), command(guest, "arm-light-ar"))
        assert host_armed == guest_armed == "armed", (host_armed, guest_armed)
        host_reserve = await command(host, "reserve")
        guid = await command(host, "seed-peer")
        assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
        await wait_readback(guest, "projected", guid)
        assert await command(guest, "equip") == "selected"
        await wait_readback(host, "peer-ready", f"{guid},1,1")
        await wait_readback(guest, "ready", f"{guid},1,1")
        guest_frame = await wait_resident_status(
            guest_run, lambda frame: frame.get("local_bridge") == "memory"
            and (scope := frame.get("game", {}).get("native_guest_primary_scope", {}))
            and (scope.get("guid"), scope.get("kind")) == (guid, profile.kind))
        assert await command(guest, "face-away") == "turned"
        assert await command(host, "clear") == "clear"
        shot = await stock_input("primary", guest_frame, guid)
        assert (shot["before"], shot["after"], shot["projectiles"]) == (1, 0, 1), shot
        await wait_readback(host, "peer-ready", f"{guid},0,0")
        await wait_readback(guest, "ready", f"{guid},0,0")
        projectile_calls, shot_sounds, muzzle_flashes, clicks = map(int, (await command(host, "shot-read")).split(","))
        assert (projectile_calls, clicks) == (1, 0), (projectile_calls, clicks)

        reserve = await command(host, "seed-reserve")
        assert re.fullmatch(rf"[0-9A-F]{{32}},{units}", reserve), reserve
        await wait_readback(guest, "reserve", reserve)
        reloaded = await stock_input("reload", guest_frame, guid)
        assert (reloaded["before"], reloaded["after"], reloaded["reserve_before"], reloaded["reserve_after"]) == (
            0, units, units, 0), reloaded
        await wait_readback(host, "peer-ready", f"{guid},{units},{units}")
        await wait_readback(guest, "ready", f"{guid},{units},{units}")
        await wait_readback(guest, "reserve", "")
        assert await command(host, "peer-reserve") == "" and await command(host, "reserve") == host_reserve
        assert campaign_save_hashes(host.campaign) == before
    finally:
        disarmed = await asyncio.gather(command(host, "disarm"), command(guest, "disarm"))
        assert disarmed == ["disarmed", "disarmed"], disarmed
    return {"stock_light_ar": {"weapon": profile.path, "ammo": FIVE_SIX_FMJ.item_path, "shot": shot,
                               "stock_projectile_calls": projectile_calls, "stock_fire_sounds": shot_sounds,
                               "stock_muzzle_flashes": muzzle_flashes, "reload": reloaded,
                               "both_magazines_verified": True, "host_reserve_unchanged": True,
                               "campaign_unchanged": True, "observer_scopes_disarmed": True}}
