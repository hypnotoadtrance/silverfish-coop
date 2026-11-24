"""Verify original primary and reload inputs with qualified stock firearm profiles."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.silverfish.guest_weapon_profiles import (
    FIREARMS, FIVE_SEVEN_AP, FIVE_SIX_FMJ, FORTY_FIVE_BALL, NINE_MM_BALL, TWELVE_GAUGE_BUCK,
)


OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
WEAPONS = {profile.kind: profile for profile in FIREARMS if profile.magazine and profile.headless_seed}
DEFAULT_WEAPONS = ("light-pistol-suppressed", "smg-standard")


async def verify(work, vanilla_saved, kinds=DEFAULT_WEAPONS):
    families = {WEAPONS[kind].magazine.ammo for kind in kinds}
    only_9mm = families == {NINE_MM_BALL}
    artifact_prefixes = {TWELVE_GAUGE_BUCK: "stock-buckshot", FIVE_SEVEN_AP: "stock-57-ap",
                         FIVE_SIX_FMJ: "stock-56-fmj", FORTY_FIVE_BALL: "stock-45-ball"}
    prefix = next((label for ammo, label in artifact_prefixes.items() if ammo in families), "stock-9mm")
    name = prefix + "-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    client = ("--profile", name)
    run = work / "campaigns" / name / "run"
    client_run = work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(vanilla_saved) if vanilla_saved else None
    evidence = {"weapons": {}}
    jobs = []

    async def ask(*arguments, success=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        raw, errors = await asyncio.wait_for(process.communicate(), 25)
        if not raw or (process.returncode == 0) != success:
            raise RuntimeError(f"{arguments}: {raw.decode(errors='replace')} {errors.decode(errors='replace')}")
        return json.loads(raw)

    async def action(owner, name, *arguments):
        return await ask("action", *owner, name, *arguments)

    async def launch(owner, label, *arguments):
        with (run / (label + ".txt")).open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                         creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, proc))
        return proc

    async def enroll():
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("game", {}).get("map") == OBSERVATORY)
        return await wait(run, lambda s: s.get("state") == "hosting" and s.get("game", {}).get("map") == OBSERVATORY
                          and s.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def receipt(event, sequence):
        for _ in range(35):
            rows = (await ask("events", *client, "--tail", "40"))["events"]
            match = next((row for row in rows if row.get("sequence") == sequence
                          and row["event"] in (event, event + "_rejected")), None)
            if match:
                assert match["event"] == event, match
                return match["native_guest_weapon"]
            await asyncio.sleep(.2)
        raise RuntimeError(f"The stock input did not produce {event} for sequence {sequence}")

    async def stop(owner, proc):
        if proc.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(proc.wait(), 15) == 0, proc.returncode

    async def seed_reserve():
        guid = uuid.uuid4().hex.upper()
        seeded = await action(host, "native-guest-weapon-seed-ammo", "--item-guid", guid)
        assert seeded["native_guest_weapon"]["guid"] == guid, seeded
        return guid

    async def reload_input():
        pressed = (await action(client, "native-guest-reload-input-fixture"))["native_guest_reload_fixture"]
        return await receipt("native_guest_reload_input", pressed["sequence"])

    async def read_reserve(owner, ammo, expected):
        rows = []
        offset = 0
        while offset is not None:
            inventory = (await action(client, "native-inventory-list", "--target", str(offset)))["native_inventory"]
            assert (inventory["owner"], inventory["offset"]) == (owner, offset), inventory
            rows.extend(row for row in inventory["items"] if row["guid"] in expected or row["class_path"] == ammo.item_path)
            next_offset = inventory["next_offset"]
            assert next_offset is None or next_offset > offset, inventory
            offset = next_offset
        assert all(row["class_path"] == ammo.item_path and row["source"] == "backpack" and row["stackable"] is True for row in rows), rows
        assert [(row["guid"], row["quantity"]) for row in rows] == [
            (guid, remaining) for guid, remaining in expected.items() if remaining
        ], rows
        return rows

    async def reload_first_stacks(owner, guid, capacity, ammo, reserve):
        steps = []
        loaded = 0
        rows = await read_reserve(owner, ammo, reserve)
        while loaded < capacity and rows:
            first = rows[0]
            before = sum(reserve.values())
            transferred = min(first["quantity"], capacity - loaded)
            refilled = await reload_input()
            assert (refilled["guid"], refilled["before"], refilled["after"],
                    refilled["reserve_before"], refilled["reserve_after"]) == (
                guid, loaded, loaded + transferred, before, before - transferred
            ), (first, refilled)
            loaded += transferred
            reserve[first["guid"]] -= transferred
            remaining = await read_reserve(owner, ammo, reserve)
            steps.append({"receipt": refilled, "stacks_before": rows, "stacks_after": remaining})
            rows = remaining
        assert steps, reserve
        return steps

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    client_flags = ("--headless", "--native-zones", "--native-guest-primary-input", "--native-guest-reload-input")
    try:
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await ask("invite", *host, "--name", "Stock weapon tester", "--character", "Gunsmith")
        guest = await launch(client, "guest", "join", *client, "--invite", invitation["invite_file"], *client_flags)
        first = await enroll()
        tracked = {family: {} for family in families if family != NINE_MM_BALL}
        wrong_keys = {FORTY_FIVE_BALL: "nonmatching_reserve", TWELVE_GAUGE_BUCK: "buckshot_nonmatching_reserve",
                      FIVE_SEVEN_AP: "57_ap_nonmatching_reserve", FIVE_SIX_FMJ: "56_fmj_nonmatching_reserve"}
        held_ammo = NINE_MM_BALL
        for kind in kinds:
            profile = WEAPONS[kind]
            capacity = profile.magazine.capacity
            ammo = profile.magazine.ammo
            wrong_key = wrong_keys.get(ammo)
            check_wrong_ammo = wrong_key is not None and wrong_key not in evidence
            if check_wrong_ammo:
                assert held_ammo != ammo
                nonmatching_guid = await seed_reserve()
                if held_ammo in tracked:
                    tracked[held_ammo][nonmatching_guid] = held_ammo.fixture_units
            initially_loaded = min(capacity, 7)
            guid = uuid.uuid4().hex.upper()
            seeded = (await action(host, "native-guest-weapon-seed", "--kind", kind, "--item-guid", guid))["native_guest_weapon"]
            assert (seeded["guid"], seeded["stored_ammo"]) == (guid, initially_loaded), seeded
            equipped = (await action(client, "native-guest-weapon-equip", "--target", guid))["native_guest_weapon"]
            assert (equipped["guid"], equipped["stored_ammo"], equipped["actor_ammo"]) == (guid, initially_loaded, initially_loaded), equipped
            await wait(client_run, lambda s: (scope := s.get("game", {}).get("native_guest_primary_scope", {}))
                       and (scope["guid"], scope["kind"]) == (guid, kind))
            shots = []
            for loaded in range(initially_loaded, 0, -1):
                press = (await action(client, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
                assert press["guid"] == guid and press["stock_wrappers"] == 2, press
                shot = await receipt("native_guest_primary_input", press["sequence"])
                assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                    guid, loaded, loaded - 1, ammo.projectile_count
                ), shot
                shots.append({"press": press, "stock_shot": shot})
            empty = (await action(client, "native-guest-weapon-inspect", "--target", guid))["native_guest_weapon"]
            assert (empty["stored_ammo"], empty["actor_ammo"]) == (0, 0), empty
            refused = await ask("action", *client, "native-guest-weapon-fire", "--target", guid, success=False)
            assert refused["error"] == "guest_weapon_preloaded_magazine_required", refused

            if check_wrong_ammo:
                wrong_ammo = await ask("action", *client, "native-guest-weapon-reload", "--target", guid, success=False)
                assert wrong_ammo["error"] == "no_matching_guest_ammo", wrong_ammo
                evidence[wrong_key] = {"guid": nonmatching_guid, "refused": wrong_ammo["error"]}

            reserve_guid = await seed_reserve()
            second_reserve = await seed_reserve() if ammo in (TWELVE_GAUGE_BUCK, FIVE_SEVEN_AP, FIVE_SIX_FMJ) else None
            steps = None
            if ammo in tracked:
                reserve = tracked[ammo]
                reserve[reserve_guid] = ammo.fixture_units
                if second_reserve:
                    reserve[second_reserve] = ammo.fixture_units
                steps = await reload_first_stacks(invitation["character_id"], guid, capacity, ammo, reserve)
                refilled = steps[-1]["receipt"]
            else:
                refilled = await reload_input()
                assert (refilled["guid"], refilled["before"]) == (guid, 0), refilled
                assert 0 < refilled["after"] <= capacity, refilled
                assert refilled["after"] == refilled["reserve_before"] - refilled["reserve_after"], refilled
                if only_9mm and profile.magazine.reload_first_stack:
                    assert (refilled["after"], refilled["reserve_before"], refilled["reserve_after"]) == (
                        ammo.fixture_units, ammo.fixture_units, 0
                    ), refilled
            evidence["weapons"][kind] = {"class_path": profile.path,
                                         "guid": guid, "scope": kind, "shots": shots, "empty": empty,
                                         "stock_reload": refilled, "reserve_guid": reserve_guid,
                                         "ammo_row": ammo.row, "ammo_item": ammo.item_path,
                                         "projectile": ammo.projectile_path}
            if steps:
                evidence["weapons"][kind]["stock_reload_steps"] = steps
            if second_reserve:
                evidence["weapons"][kind].update(second_reserve_guid=second_reserve, first_stack_reload=steps[0]["receipt"])
                if initially_loaded == capacity:
                    assert len(steps) == 1 and refilled["after"] == capacity
                    assert any(row["guid"] == second_reserve and row["quantity"] == ammo.fixture_units
                               for row in steps[0]["stacks_after"]), steps
            held_ammo = ammo
        await stop(host, authority)
        await stop(client, guest)
        await launch(host, "host-restart", "host", *host, "--port", str(first["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != first["generation"])
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(client, "guest-restart", "join", *client, "--invite", renewed["invite_file"], *client_flags)
        await enroll()
        for item in evidence["weapons"].values():
            saved = (await action(client, "native-guest-weapon-inspect", "--target", item["guid"]))["native_guest_weapon"]
            assert (saved["guid"], saved["stored_ammo"]) == (item["guid"], item["stock_reload"]["after"]), saved
            item["stock_restart"] = saved
        restart_keys = {FORTY_FIVE_BALL: "45_reserve_after_restart", TWELVE_GAUGE_BUCK: "buckshot_reserve_after_restart",
                        FIVE_SEVEN_AP: "57_ap_reserve_after_restart"}
        for ammo, expected in tracked.items():
            key = restart_keys.get(ammo, f"{ammo.symbol}_reserve_after_restart")
            evidence[key] = await read_reserve(invitation["character_id"], ammo, expected)
        evidence["verified"] = True
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(owner, process)
                except (OSError, RuntimeError, TimeoutError, AssertionError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        if originals is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        (run / f"live-{prefix}-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if originals is not None:
            assert evidence["original_saves_verified"], "The original single-player directory changed during the test"


if __name__ == "__main__":
    cli_parser = argparse.ArgumentParser(description=__doc__)
    cli_parser.add_argument("--work", type=Path, required=True)
    cli_parser.add_argument("--vanilla-saved", type=Path)
    cli_parser.add_argument("--weapon", action="append", choices=sorted(WEAPONS),
                            help="verify this authored weapon profile, repeat for multiple profiles")
    settings = cli_parser.parse_args()
    asyncio.run(verify(settings.work.resolve(), settings.vanilla_saved,
                       tuple(dict.fromkeys(settings.weapon)) if settings.weapon else DEFAULT_WEAPONS))
