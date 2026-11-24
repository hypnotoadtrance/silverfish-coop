"""Exercise stock LightAR select-fire, reload, and sustained fire in the installed pair."""

import asyncio
import re

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_smg_hold
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import FIREARMS, FIVE_SIX_FMJ
from tools.silverfish.travel import OBSERVATORY


async def verify_stock_light_ar(host, guest, pawn, probe, *, re_equip=False):
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

    async def stock_burst(frame, guid):
        pressed = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=350)
        assert pressed["ok"], pressed
        stroke = pressed["native_guest_held_fire_fixture"]
        async with asyncio.timeout(8):
            while True:
                guest_rows, host_rows = await asyncio.gather(
                    resident_events(guest_run, frame), resident_events(host_run, host_frame))
                edges = [row for row in guest_rows if row.get("stroke") == stroke["stroke"]
                         and row["event"].startswith("native_guest_held_fire_input")]
                assert all(row["event"] == "native_guest_held_fire_input" for row in edges), edges
                terminal = next((row for row in host_rows if row["event"] == "native_guest_smg_hold_accepted"
                                 and row.get("guid") == guid), None)
                if terminal and [row["edge"] for row in edges] == ["press", "release"]:
                    return terminal
                await asyncio.sleep(.06)

    async def persisted_mode(guid, mode):
        await wait_resident_status(
            host_run, lambda status: (weapon := status.get("game", {}).get("native_guest_equipped_weapons", {}).get(pawn, {}))
            and (weapon.get("guid"), weapon.get("fire_mode")) == (guid, mode))

    async def measured_grip(observer, stock_readback=None):
        action = "rifle-grip" + (f"\t{stock_readback}" if stock_readback else "")
        reading = None
        try:
            async with asyncio.timeout(8):
                while True:
                    reading = await command(observer, action)
                    if not reading.startswith("waiting:"):
                        baseline, anchor_gap, adjusted = map(float, reading.split(";")[0].split(","))
                        if adjusted <= baseline + .1 and adjusted < 16 and abs(adjusted - anchor_gap) < .5:
                            return {"stock_gap_cm": baseline, "adjusted_gap_cm": adjusted}
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"The observer had no settled stock foregrip: {reading}") from error

    async def observing_pose(*clips):
        return await wait_resident_status(
            host_run, lambda frame: (avatar := frame.get("game", {}).get("native_player_avatars", {}).get(pawn, {}))
            and avatar.get("pose_clip") in clips and (avatar.get("weapon_socket_height") or 0) > 0)

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
        assert await command(guest, "light-ar-semi") == "light-ar-semi"
        await persisted_mode(guid, "semi")
        grip, nagant_grip, tail_gap, obrez_switch = None, None, None, False
        if re_equip:
            assert await command(guest, "stand") == "standing"
            grip = await measured_grip(host)
            await wait_readback(guest, "aim", "aiming")
            assert await command(guest, "put-away") == "changing"
            await wait_readback(guest, "other-ready", "other")
            assert await command(guest, "equip") == "selected"
            await persisted_mode(guid, "semi")
            await wait_readback(guest, "light-ar-read-mode", "semi")
        denied = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press")
        if denied["ok"]:
            await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="release")
        assert denied["ok"] is False and "native_guest_held_fire_fixture_requires_automatic_mode" in denied["error"], denied
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
        assert await command(guest, "light-ar-auto") == "light-ar-auto"
        await persisted_mode(guid, "auto")
        assert await command(host, "clear") == "clear"
        burst = await stock_burst(guest_frame, guid)
        assert (burst["status"], burst["before"], burst["pawn"]) == ("released", units, pawn), burst
        assert 1 < burst["rounds"] == burst["projectiles"] == units - burst["after"] < units, burst
        for seat, action in ((host, "peer-ready"), (guest, "ready")):
            await wait_readback(seat, action, f"{guid},{burst['after']},{burst['after']}")
        await asyncio.sleep(.35)
        calls, *_ = (await command(host, "shot-read")).split(",")
        assert int(calls) == burst["projectiles"], (calls, burst)
        if re_equip:
            assert await command(host, "arm-nagant") == await command(guest, "arm-nagant") == "armed"
            long_rifle = await command(host, "seed-local")
            assert await command(host, "stand") == "standing"
            assert await command(host, "equip") == "selected"
            await wait_readback(host, "ready", f"{long_rifle},0,0")
            nagant_grip = await measured_grip(guest, await command(host, "rifle-witness"))
            assert await command(host, "host-tail") == "styled"
            async with asyncio.timeout(8):
                while tail_gap is None:
                    witness = await command(guest, "tail-witness")
                    if witness == "waiting":
                        await asyncio.sleep(.05)
                    else:
                        tail_gap = float(witness)
            assert tail_gap < .15, tail_gap
            assert await command(host, "arm-obrez") == await command(guest, "arm-obrez") == "armed"
            obrez = await command(host, "seed-peer")
            await wait_readback(guest, "projected", obrez)
            assert await command(guest, "equip") == "selected"
            await wait_readback(host, "peer-ready", f"{obrez},1,1")
            await wait_readback(guest, "aim", "aiming")
            await observing_pose("Rifle_Aiming_Idle")
            assert await command(guest, "put-away") == "changing"
            await wait_readback(guest, "other-ready", "other")
            await observing_pose("HandGun_Idle_2", "Rifle_Aiming_Low_Close1")
            obrez_switch = True
        assert campaign_save_hashes(host.campaign) == before
    finally:
        disarmed = await asyncio.gather(command(host, "disarm"), command(guest, "disarm"))
        assert disarmed == ["disarmed", "disarmed"], disarmed
    return {"stock_light_ar": {"weapon": profile.path, "ammo": FIVE_SIX_FMJ.item_path, "shot": shot,
                               "stock_projectile_calls": projectile_calls, "stock_fire_sounds": shot_sounds,
                               "stock_muzzle_flashes": muzzle_flashes, "reload": reloaded,
                               "semi_hold_blocked": True, "automatic_burst": burst,
                               "host_stored_both_modes": True, "semi_after_re_equip": re_equip,
                               "observer_idle_grip": grip, "stock_switch_while_aiming": re_equip,
                               "guest_observes_host_nagant_grip": nagant_grip, "obrez_to_pistol_while_aiming": obrez_switch,
                               "skeletal_tail_gap_cm": tail_gap,
                               "both_magazines_verified": True, "host_reserve_unchanged": True,
                               "campaign_unchanged": True, "observer_scopes_disarmed": True}}
