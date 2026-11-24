"""Exercise stock LightAR select-fire, reload, and sustained fire in the installed pair."""

import asyncio
import re

from live_multi_smoke import campaign_save_hashes, saved_hashes
from live_unified_title_smoke import start_pair
from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_host_save, native_smg_hold
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
        assert await command(host, "burst-watch") == "watching"
        first = float(await command(guest, "burst-aim-first"))
        # Allow the local camera to settle before invoking the stock input.
        await asyncio.sleep(.12)
        pressed = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=500)
        assert pressed["ok"], pressed
        stroke = pressed["native_guest_held_fire_fixture"]
        second = float(await command(guest, "burst-aim-second"))
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
                    angles = list(map(float, (await command(host, "burst-read")).split(",")))
                    delta = lambda left, right: abs((left - right + 180) % 360 - 180)
                    assert len(angles) == terminal["rounds"] and delta(angles[0], first) < 40, (angles, terminal)
                    assert delta(angles[-1], second) < 40, (angles, terminal)
                    return terminal, angles
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

    async def observing_pose(kind, *clips, previous_meshes=None):
        return await wait_resident_status(
            host_run, lambda frame: (avatar := frame.get("game", {}).get("native_player_avatars", {}).get(pawn, {}))
            and avatar.get("pose_clip") in clips and (avatar.get("weapon_socket_height") or 0) > 0
            and avatar.get("weapon_meshes") and avatar["weapon_meshes"] != previous_meshes
            and frame["game"].get("native_guest_equipped_weapons", {}).get(pawn, {}).get("kind") == kind, timeout=12)

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
        burst, burst_directions = await stock_burst(guest_frame, guid)
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
            aimed = await observing_pose("obrez", "Rifle_Aiming_Idle", "Rifle_Aiming_Low_Close1")
            obrez_meshes = aimed["game"]["native_player_avatars"][pawn]["weapon_meshes"]
            assert await command(guest, "put-away") == "changing"
            await wait_readback(guest, "other-ready", "other")
            await observing_pose("rusty-pistol", "HandGun_Idle_2", "Rifle_Aiming_Low_Close1", previous_meshes=obrez_meshes)
            obrez_switch = True
        assert campaign_save_hashes(host.campaign) == before
    finally:
        disarmed = await asyncio.gather(command(host, "disarm"), command(guest, "disarm"))
        assert disarmed == ["disarmed", "disarmed"], disarmed
    return {"stock_light_ar": {"weapon": profile.path, "ammo": FIVE_SIX_FMJ.item_path, "shot": shot,
                               "stock_projectile_calls": projectile_calls, "stock_fire_sounds": shot_sounds,
                               "stock_muzzle_flashes": muzzle_flashes, "reload": reloaded,
                               "semi_hold_blocked": True, "automatic_burst": burst, "burst_muzzle_yaws": burst_directions,
                               "host_stored_both_modes": True, "semi_after_re_equip": re_equip,
                               "observer_idle_grip": grip, "stock_switch_while_aiming": re_equip,
                               "guest_observes_host_nagant_grip": nagant_grip, "obrez_to_pistol_while_aiming": obrez_switch,
                               "skeletal_tail_gap_cm": tail_gap,
                               "both_magazines_verified": True, "host_reserve_unchanged": True,
                               "campaign_unchanged": True, "observer_scopes_disarmed": True}}


async def verify_fire_mode_reopen(first, second, probe, evidence):
    async def command(seat, action):
        return await probe(seat.bridge, "portable-dry-fire", action)

    async def wait_value(seat, action, expected):
        async with asyncio.timeout(10):
            while await command(seat, action) != expected:
                await asyncio.sleep(.05)

    async def armed(host, guest, pawn, *, reopen=False):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        assert await command(host, "arm-light-ar") == await command(guest, "arm-light-ar") == "armed"
        try:
            active = await wait_resident_status(host_run, lambda status: status.get("game", {}).get("save_scope"))
            save_scope = native_host_save.SaveScope(host.profile, active["game"]["save_scope"])
            native_before = saved_hashes(native_host_save.directory(save_scope))
            before = campaign_save_hashes(host.campaign)
            if reopen:
                guid = evidence["guid"]
                assert pawn != evidence["initial_pawn"], "Expected a newly loaded authoritative guest pawn"
            else:
                guid = await command(host, "seed-peer")
                assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
                evidence.update(guid=guid, initial_pawn=pawn)
            await wait_value(guest, "projected", guid)
            if await command(guest, "ready") == "waiting":
                assert await command(guest, "equip") == "selected"
            await wait_value(guest, "ready", f"{guid},1,1")
            await wait_value(host, "peer-ready", f"{guid},1,1")
            if not reopen:
                await wait_value(guest, "light-ar-read-mode", "auto")
                assert await command(guest, "light-ar-semi") == "light-ar-semi"
            host_frame = await wait_resident_status(host_run, lambda status: (
                (weapon := status.get("game", {}).get("native_guest_equipped_weapons", {}).get(pawn, {}))
                and (weapon.get("guid"), weapon.get("rounds"), weapon.get("fire_mode")) == (guid, 1, "semi")), timeout=10)
            guest_frame = await wait_resident_status(guest_run, lambda status: (
                (scope := status.get("game", {}).get("native_guest_primary_scope", {})) and scope.get("guid") == guid), timeout=10)
            await wait_value(guest, "light-ar-read-mode", "semi")
            evidence["reopened" if reopen else "initial"] = {
                "host_generation": host_frame["generation"], "guest_generation": guest_frame["generation"],
                "pawn": pawn, "guid": guid, "rounds": 1, "mode": "semi",
            }
            if reopen:
                assert all(evidence["reopened"][key] != evidence["initial"][key]
                           for key in ("host_generation", "guest_generation"))
                result = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press")
                if result["ok"]:
                    await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="release")
                assert not result["ok"] and "requires_automatic_mode" in result["error"], result
                evidence["reopened"]["automatic_hold_denied"] = True
            assert campaign_save_hashes(host.campaign) == before, "Checking or changing the selector wrote a campaign save"
            assert saved_hashes(native_host_save.directory(save_scope)) == native_before, (
                "Checking or changing the selector wrote a native Unreal save")
        finally:
            assert await command(host, "disarm") == await command(guest, "disarm") == "disarmed"

    async def check_saved(host, _stopped, checkpoint, character):
        frame = checkpoint["characters"][character]["state"]["native_guest_save"]
        profile = native_host_save.checkpoint_profile(host.profile, checkpoint)
        save = native_host_save.slot_path(profile, frame["slot"])
        payload = save.read_bytes()
        marker = "SFCoopFireMode_" + evidence["guid"]
        assert marker.encode("ascii") in payload or marker.encode("utf-16-le") in payload, frame
        evidence["saved"] = {"character": character, "revision": checkpoint["revision"], "sha256": frame["sha256"]}

    async def seed(host, guest, pawn):
        await armed(host, guest, pawn)
        return {}

    async def rejoin(host, guest, pawn):
        await armed(host, guest, pawn, reopen=True)
        return {}

    saved = await start_pair(first, second, "Selector scout", on_join=seed, after_exit=check_saved,
                             wait_status=wait_resident_status)
    reopened = await start_pair(first, second, "Selector scout", load_existing=True,
                                resume_character=saved["character_id"], on_join=rejoin, wait_status=wait_resident_status)
    assert all(reopened[key] == saved[key] for key in ("campaign_id", "character_id", "host_pid", "guest_pid"))
    assert reopened["revision"] > saved["revision"] == evidence["saved"]["revision"]
    return [saved, reopened]
