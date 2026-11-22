"""Verify both remote avatar projections and capture an offscreen guest PNG."""

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_guest_held_fire_smoke import events
from live_multi_smoke import CROSSROADS, saved_hashes


REPO = Path(__file__).resolve().parents[1]
PROBE = "SilverfishAvatarProbe"


@contextmanager
def installed_probe(work):
    mod = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods" / PROBE
    mod.mkdir()
    scripts = mod / "Scripts"
    script, activation = scripts / "main.lua", mod / "enabled.txt"
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / "avatar_presentation_probe.lua", script)
        activation.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        activation.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        mod.rmdir()


async def avatar_probe(run, operation, *, allow_pending=False):
    ticket = uuid.uuid4().hex
    mailbox, = (run / "game-bridge").iterdir()
    pending = mailbox / "avatar-probe-command.pending"
    response = mailbox / "avatar-probe-reply.tsv"
    pending.write_text(f"{ticket}\t{operation}\n", encoding="ascii", newline="")
    async with asyncio.timeout(2):
        while True:
            try:
                pending.replace(mailbox / "avatar-probe-command.tsv")
                break
            except PermissionError:
                await asyncio.sleep(.025)
    async with asyncio.timeout(15):
        while True:
            try:
                fields = response.read_text(encoding="utf-8").rstrip().split("\t")
                if fields[0] == ticket and len(fields) >= 2:
                    if allow_pending and fields[1].endswith("avatar_probe_rig_pending"):
                        return
                    assert fields[1] == "ok", fields[1]
                    return fields[2:]
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.025 if operation == "rig" else .1)


async def verify_pose(host_run, guest_run):
    fields = ("base_cm", "body_cm", "neck_error_cm", "head_offset_error_cm", "head_roll", "head_yaw",
              "tail_x", "tail_z", "tail_yaw", "actor_yaw", "falling")

    async def observe(run, predicate=lambda _: True, timeout=5):
        async with asyncio.timeout(timeout):
            while True:
                values = await avatar_probe(run, "rig", allow_pending=True)
                if values:
                    row = dict(zip(fields, map(float, values), strict=True))
                    if predicate(row):
                        return row
                await asyncio.sleep(.025)

    def aligned(row, *, skeletal):
        assert row["base_cm"] < .01 and row["body_cm"] < 3, row
        assert row["neck_error_cm"] < .15 and row["head_offset_error_cm"] < .15 and abs(row["head_roll"]) < .1, row
        expected_tail = (21.374724, -7.291765, -10) if skeletal else (0, 0, 0)
        assert all(math.isclose(row[key], value, abs_tol=.02)
                   for key, value in zip(("tail_x", "tail_z", "tail_yaw"), expected_tail, strict=True)), row

    await avatar_probe(host_run, "style")
    initial = await observe(guest_run, lambda row: row["tail_x"] > 0 and row["falling"] == 0, timeout=15)
    stationary = await observe(host_run, lambda row: row["tail_x"] == 0 and row["falling"] == 0, timeout=15)
    aligned(initial, skeletal=True)
    aligned(stationary, skeletal=False)
    await avatar_probe(host_run, "turn")
    turned = await observe(guest_run, lambda row: abs((row["actor_yaw"] - initial["actor_yaw"] + 180) % 360 - 180) > 60
                          and row["body_cm"] < 3)
    aligned(turned, skeletal=True)
    await avatar_probe(host_run, "jump")
    airborne = await observe(guest_run, lambda row: row["falling"] == 1)
    aligned(airborne, skeletal=True)
    landed = await observe(guest_run, lambda row: row["falling"] == 0 and row["body_cm"] < 3)
    aligned(landed, skeletal=True)
    wrists = {}
    for index, clip in enumerate(("HandGun_Idle", "HandGun_Idle_2", "HandGun_Idle_3", "Rifle_Aiming_Idle"), 1):
        wrists[clip] = {}
        for sample in (0, 1):
            await avatar_probe(guest_run, f"clip{index}{sample}")
            points = await avatar_probe(guest_run, "wrists")
            wrists[clip][str(sample * .15)] = dict(zip(
                ("weapon_socket", "right_hand", "left_hand"),
                (list(map(float, point.split(","))) for point in points), strict=True))
    return {"guest_sees_host": initial, "host_sees_guest": stationary, "turned": turned,
            "airborne": airborne, "landed": landed, "aim_clip_wrist_positions": wrists}


async def capture_movement(host_run, guest_run, command, host, guest, with_trait, sprint_clips):
    fields = ("sprinting", "walk_cap", "base_walk", "base_sprint", "speed_modifier", "food",
              "speed", "x", "y", "z", "mesh_x", "mesh_y")

    async def read(run, operation):
        result = await avatar_probe(run, operation)
        return dict(zip(fields, map(float, result), strict=True))

    def remote_pose(state):
        game = state.get("game", {})
        pawn = game.get("pawns", {}).get("guest", {}).get("name")
        return game.get("native_player_avatars", {}).get(pawn, {})

    await avatar_probe(host_run, "preparemovement")
    await asyncio.sleep(.75)
    await avatar_probe(guest_run, "preparemovement")
    evidence = {}
    for role, runner, observer, target in (("guest", guest_run, host_run, guest), ("host", host_run, guest_run, host)):
        modes = {}
        for phase, pace in (("walk", "walk"), ("run", "run"), ("restart", "run")):
            await avatar_probe(host_run, "resetlane")
            await asyncio.sleep(.55)
            x, y = await avatar_probe(runner, pace)
            before = await read(runner, "localmovement")
            started = await command("action", *target, "walk", "--continuous", f"--x={x}", f"--y={y}", "--milliseconds", "2500")
            assert started["ok"], started
            await asyncio.sleep(.7)
            samples = []
            for _ in range(7):
                own, observed = await asyncio.gather(read(runner, "localmovement"), read(observer, "remotemovement"))
                state = json.loads((observer / "status.json").read_text(encoding="utf-8"))
                sample = {"runner": own, "observer": observed, "avatar": remote_pose(state)}
                samples.append(sample)
                await asyncio.sleep(.13)
            await avatar_probe(runner, "stopmovement")
            await asyncio.sleep(.7)
            stopped = await read(observer, "remotemovement")
            released = await wait(observer, lambda state: (avatar := remote_pose(state))
                                  and avatar.get("pose_clip") != sprint_clips[role], timeout=5)
            modes[phase] = {"start": before, "samples": samples, "observer_after_release": stopped,
                            "avatar_after_release": remote_pose(released)}
        evidence[role] = modes
    guest_movement = evidence["guest"]
    for pace, sprinting in (("walk", False), ("run", True), ("restart", True)):
        phase = guest_movement[pace]
        settled = phase["samples"][-3:]
        if with_trait:
            assert all(1.09 < row["runner"]["speed_modifier"] < 1.11
                       and 1.09 < row["observer"]["speed_modifier"] < 1.11 for row in settled), phase
        speeds = [median(row[peer]["speed"] for row in settled) for peer in ("runner", "observer")]
        assert all(row["runner"]["sprinting"] == row["observer"]["sprinting"] == sprinting
                   and abs(row["runner"]["walk_cap"] - row["observer"]["walk_cap"]) < 1 for row in settled), phase
        assert min(speeds) > 500 and abs(speeds[0] - speeds[1]) < 40, speeds
        assert phase["observer_after_release"]["sprinting"] == 0, phase
        for role, modes in evidence.items():
            samples = modes[pace]["samples"]
            clips = {row["avatar"].get("pose_clip") for row in samples}
            assert (sprint_clips[role] in clips) == sprinting, (
                role, pace, sprint_clips[role], clips,
                [(row["runner"]["sprinting"], row["runner"]["speed"], row["observer"]["speed"]) for row in samples],
            )
    if with_trait:
        assert all(abs(row["runner"]["speed_modifier"] - 1.0) < .01
                   for phase in evidence["host"].values() for row in phase["samples"]), evidence["host"]
    assert (guest_movement["run"]["samples"][-1]["observer"]["walk_cap"]
            > guest_movement["walk"]["samples"][-1]["observer"]["walk_cap"])
    (host_run / "stock-movement-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


async def verify_aim_movement(host_run, guest_run, command, host, guest, projections):
    async def stance(run, operation):
        crouched, grounded, x, y, yaw, clip, position, height = await avatar_probe(run, operation)
        return {"crouched": crouched == "1", "grounded": grounded == "1", "position": [float(x), float(y)],
                "yaw": float(yaw), "clip": clip, "clip_time": float(position), "socket_height": float(height)}

    async def observe(run, clip, weapon_mesh, crouched=False):
        def projected(state):
            return next((row for row in (projections(state) or {}).values()
                         if not row.get("diagnostic") and row.get("pose_clip") == clip
                         and row.get("animation_mode") == 1 and any(
                             mesh.endswith("." + weapon_mesh) for mesh in row.get("weapon_meshes", ()))), None)

        projection = projected(await wait(run, projected, timeout=8))
        async with asyncio.timeout(3):
            while True:
                observed = await stance(run, "remotestance")
                if (observed["clip"] == clip and observed["grounded"] and observed["crouched"] == crouched
                        and observed["clip_time"] > 0 and observed["socket_height"] > 0):
                    return {"avatar": projection, "rig": observed}
                await asyncio.sleep(.05)

    async def move(runner, observer, target, operation, clip, weapon_mesh, *, crouched=False):
        x, y = await avatar_probe(runner, operation)
        before = await stance(runner, "localstance")
        reply = await command("action", *target, "walk", "--continuous", f"--x={x}", f"--y={y}",
                              "--milliseconds", "2600")
        assert reply["ok"], reply
        observed = await observe(observer, clip, weapon_mesh, crouched)
        after = await stance(runner, "localstance")
        dx, dy = (end - start for start, end in zip(before["position"], after["position"], strict=True))
        distance = math.hypot(dx, dy)
        yaw = math.radians(after["yaw"])
        forward = dx * math.cos(yaw) + dy * math.sin(yaw)
        sideways = -dx * math.sin(yaw) + dy * math.cos(yaw)
        assert after["grounded"] and after["crouched"] == crouched and distance > 10, (before, after, observed)
        assert (sideways if operation == "aimstrafe" else forward) > .8 * distance, (before, after, observed)
        await avatar_probe(runner, "stopmovement")
        return {"start": before, "moving": after, "observed": observed}

    await avatar_probe(host_run, "equip")
    await avatar_probe(host_run, "preparemovement")
    await avatar_probe(guest_run, "preparemovement")
    evidence = {}
    for role, runner, observer, target, idle, moving, lowered, mesh in (
        ("host", host_run, guest_run, host, "HandGun_Idle_3", "HandGun_Walk", "HandGun_Idle", "9mmAuto_Frame"),
        ("guest", guest_run, host_run, guest, "Rifle_Aiming_Idle", "Rifle_Walk", "Rifle_Idle_1_", "BreakAction_BMG_Frame"),
    ):
        await avatar_probe(host_run, "resetlane")
        await observe(observer, lowered, mesh)
        await avatar_probe(runner, "aim")
        try:
            aimed = await observe(observer, idle, mesh)
            assert 130 <= aimed["rig"]["socket_height"] <= 143, aimed
            strafing = await move(runner, observer, target, "aimstrafe", moving, mesh)
            await observe(observer, idle, mesh)
            await avatar_probe(runner, "crouch")
            crouched = await observe(observer, "Idle_Crouching_Aiming", mesh, True)
            crouched_walking = await move(runner, observer, target, "aimwalk", "Walk_Crouching_Forward", mesh,
                                          crouched=True)
            await observe(observer, "Idle_Crouching_Aiming", mesh, True)
        finally:
            await avatar_probe(runner, "stopmovement")
            await avatar_probe(runner, "lower")
            await avatar_probe(runner, "stand")
        evidence[role] = {"aimed": aimed, "strafing": strafing, "crouched": crouched,
            "crouched_walking": crouched_walking, "released": await observe(observer, lowered, mesh)}
    return evidence


async def capture(work: Path, vanilla_saved: Path, lifecycle: bool, equipment: bool = False,
                  melee: bool = False, pose: bool = False, rifle: bool = False,
                  consumable: bool = False, movement: bool = False, movement_traits: bool = False,
                  aim_movement: bool = False) -> None:
    profile = "avatar-" + uuid.uuid4().hex[:8]
    host_dir = work / "campaigns" / profile
    guest_dir = work / "clients" / profile
    host_run, guest_run = host_dir / "run", guest_dir / "run"
    host_run.mkdir(parents=True)
    env = dict(os.environ, SILVERFISH_COOP_RENDER_CAPTURE="guest")
    if pose:
        env["SILVERFISH_COOP_RENDER_CAPTURE_POSE"] = "1"
    if rifle or aim_movement:
        env["SILVERFISH_COOP_RENDER_CAPTURE_RIFLE"] = "1"
    if equipment:
        env["SILVERFISH_COOP_RENDER_CAPTURE_EQUIPMENT"] = "1"
    if movement or aim_movement:
        env["SILVERFISH_COOP_MOVEMENT_PROBE"] = "1"
    if aim_movement:
        env["SILVERFISH_COOP_AIM_MOVEMENT_PROBE"] = "1"
    if movement_traits:
        env["SILVERFISH_COOP_MOVEMENT_TRAIT_PROBE"] = "1"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    before = saved_hashes(vanilla_saved)
    processes = []
    host = ("--campaign", profile)
    guest = ("--profile", profile)

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 25)
        if process.returncode:
            raise RuntimeError(f"{args} failed with {output!r} {error!r}")
        return json.loads(output)

    async def start(log, target, *args):
        process_env = dict(env)
        if (equipment or aim_movement) and target == host:
            process_env["SILVERFISH_COOP_RENDER_CAPTURE"] = "host"
        with (host_run / log).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=process_env,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, target))
        return process

    def projections(state):
        game = state.get("game", {})
        remote = game.get("pawns", {}).get("guest", {}).get("name")
        items = game.get("native_player_avatars", {})
        return items if remote and set(items) == {remote} else None

    print(host_run, flush=True)
    try:
        await start("host.txt", host, "host", *host, "--port", "0", "--headless", "--fixtures",
            "--native-events", "--native-guest", "--native-zones", "--start-zone", CROSSROADS)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await command("invite", *host, "--name", "Loopback avatar guest", "--character", "Avatar guest")
        join = ("join", *guest, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        if equipment or rifle or aim_movement:
            join += ("--native-guest-primary-input",)
        if rifle:
            join += ("--native-guest-reload-input",)
        guest_process = await start("guest.txt", guest, *join)
        evidence = {}
        for role, run in (("host", host_run), ("guest", guest_run)):
            state = await wait(run, projections)
            evidence[role] = projections(state)
            assert all(row["state"] == "default_body" for row in evidence[role].values()), evidence
        if lifecycle:
            identity = invitation["character_id"]
        if movement and not (equipment or rifle or aim_movement):
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            evidence["stock_movement"] = await capture_movement(host_run, guest_run, command, host, guest, movement_traits,
                {"host": "Running_Unarmed", "guest": "Running_Unarmed"})
        if consumable:
            await avatar_probe(host_run, "consume")
            async with asyncio.timeout(8):
                while not (cues := events(guest_run, "native_avatar_use_sound_played")):
                    await asyncio.sleep(.05)
            cue, = cues
            assert cue["item_class"] == "/Game/Classes/Items/Item_Bandage.Item_Bandage_C", cue
            def observed_use(state):
                return next((use for use in state.get("game", {}).get("native_avatar_use", {}).values()
                             if use["serial"] == cue["serial"] and use["item"] == cue["item_class"]), None)
            final = await wait(guest_run, lambda state: (use := observed_use(state))
                               and not use["active"] and use["hand_motion_cm"] > 20, timeout=8)
            visual, = events(guest_run, "native_avatar_use_visual_played")
            assert visual["serial"] == cue["serial"], visual
            prop = observed_use(final)
            assert prop["mesh"].endswith(".SM_Prop_Bandage_02"), prop
            evidence["stock_bandage_presentation"] = {"sound": cue, "visual": visual, "prop": prop}
        if pose:
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            evidence["pose"] = await verify_pose(host_run, guest_run)
        if rifle or aim_movement:
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            guid, = await avatar_probe(host_run, "seedrifle")
            async with asyncio.timeout(15):
                while not (local_profile := await avatar_probe(guest_run, "selectrifle", allow_pending=True)):
                    await asyncio.sleep(.1)
            mounted = await wait(host_run, lambda state: any(
                {"BreakAction_BMG_Frame", "BreakAction_BMG_Swing"} <= {path.rsplit(".", 1)[-1]
                    for path in row.get("weapon_meshes", ())}
                for row in (projections(state) or {}).values()), timeout=12)
            selected, = events(guest_run, "native_guest_equipment_selected")
            receipt = selected["native_guest_weapon"]
            assert tuple(receipt[key] for key in ("action", "status", "guid", "actor_ammo", "stored_ammo")) == (
                "select", "equipped", guid, 1, 1), receipt
            authority_profile = await avatar_probe(host_run, "heldrifle")
            assert local_profile == authority_profile, (local_profile, authority_profile)
            fields = ("magazine", "caliber", "fire_rate", "burst_length", "action_on_release", "single_reload", "projectile")
            evidence["guest_rifle_selection"] = {"receipt": receipt, "observer": projections(mounted),
                "stock_profile": dict(zip(fields, authority_profile, strict=True))}
            if movement and rifle:
                evidence["stock_movement"] = await capture_movement(host_run, guest_run, command, host, guest, movement_traits,
                    {"host": "Running_Unarmed", "guest": "Rifle_Run_Forward"})
            if aim_movement:
                evidence["stock_moving_aim"] = await verify_aim_movement(host_run, guest_run, command, host, guest, projections)
        if rifle:
            async with asyncio.timeout(8):
                while True:
                    await wait(guest_run, lambda state: state.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
                    try:
                        await command("action", *guest, "native-guest-primary-input-fixture")
                        break
                    except RuntimeError as error:
                        if "Equip an authenticated stock guest weapon before exercising primary input" not in str(error):
                            raise
                    await asyncio.sleep(.2)
            async with asyncio.timeout(10):
                while not (shots := events(guest_run, "native_guest_primary_input")):
                    await asyncio.sleep(.1)
            shot, = shots
            assert tuple(shot["native_guest_weapon"][key] for key in ("guid", "before", "after", "projectiles")) == (
                guid, 1, 0, 1), shot
            reserve_guid = uuid.uuid4().hex.upper()
            await command("action", *host, "native-guest-weapon-seed-ammo", "--target", "guest", "--item-guid", reserve_guid)
            await command("action", *guest, "native-guest-reload-input-fixture")
            async with asyncio.timeout(12):
                while not (reloads := events(guest_run, "native_guest_reload_input")):
                    await asyncio.sleep(.1)
            reloaded, = reloads
            ammo = reloaded["native_guest_weapon"]
            assert tuple(ammo[key] for key in ("guid", "before", "after", "reserve_before", "reserve_after")) == (
                guid, 0, 1, 8, 7), reloaded
            reload_clip = "BreakAction_Reload_50BMG.BreakAction_Reload_50BMG"
            await wait(host_run, lambda state: any(
                row.get("clip", "").endswith(reload_clip) and row.get("travel_cm", 0) > .5 and not row.get("active")
                for row in state.get("game", {}).get("native_avatar_cycle", {}).values()), timeout=10)
            evidence["guest_rifle_combat"] = {"fire": shot["native_guest_weapon"], "reload": ammo}
        if equipment:
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            await avatar_probe(host_run, "equip")
            guid = uuid.uuid4().hex.upper()
            await command("action", *host, "native-guest-weapon-seed", "--target", "guest", "--kind", "1911", "--item-guid", guid)
            await command("action", *guest, "native-guest-weapon-equip", "--target", guid)

            def equipped_projection(state, *, on_host):
                rows = projections(state)
                if not rows:
                    return
                remote, = rows.values()
                if remote.get("diagnostic") or abs(remote.get("head_roll", 90)) > .1:
                    return
                appearance = remote.get("appearance", {})
                expected_face = "SM_Head_UV_Fangs_01" if on_host else "SM_Head_UV_02_EarRing"
                expected_plumage = "SM_Plumage_012" if on_host else "SM_Plumage_007"
                expected_skin = [.8, .25, .33, 1] if on_host else [.2, .7, .35, 1]
                expected_weapon = {"M1911_Frame", "M1911_Mag", "M1911_Slide"} if on_host else {
                    "9mmAuto_Frame", "9mmAuto_Mag", "9mmAuto_Slide"}
                if (appearance.get("face", "").endswith("." + expected_face)
                        and appearance.get("plumage", "").endswith("." + expected_plumage)
                        and appearance.get("skin_color") == expected_skin
                        and remote.get("body", "").endswith(".Armor_HunterJacket_C")
                        and expected_weapon <= {path.rsplit(".", 1)[-1] for path in remote.get("weapon_meshes", ())}):
                    return remote

            evidence["held"] = {
                role: equipped_projection(await wait(run, lambda state: equipped_projection(state, on_host=(role == "host")), timeout=18),
                                          on_host=(role == "host"))
                for role, run in (("host", host_run), ("guest", guest_run))
            }
            if movement:
                evidence["stock_movement"] = await capture_movement(host_run, guest_run, command, host, guest, movement_traits,
                    {"host": "HandGun_Run", "guest": "HandGun_Run"})
                await avatar_probe(host_run, "faceteammate")
                await avatar_probe(guest_run, "faceteammate")
            def aimed_pistol(state, *, on_host):
                peer = equipped_projection(state, on_host=on_host)
                if (peer and peer.get("pose_clip") == "HandGun_Idle_3" and peer.get("animation_mode") == 1
                        and 130 <= (peer.get("weapon_socket_height") or 0) <= 143):
                    return peer

            await avatar_probe(host_run, "aim")
            try:
                aimed_host = await wait(guest_run, lambda state: aimed_pistol(state, on_host=False), timeout=10)
            except RuntimeError:
                if movement:
                    print("Host aim pose:", await avatar_probe(guest_run, "readpose"), flush=True)
                raise
            evidence["aimed_pistol"] = aimed_pistol(aimed_host, on_host=False)
            await avatar_probe(host_run, "lower")
            await wait(guest_run, lambda state: (peer := equipped_projection(state, on_host=False))
                       and peer.get("pose_clip") == "HandGun_Idle"
                       and 97 <= (peer.get("weapon_socket_height") or 0) <= 110, timeout=10)
            evidence["aim_release_verified"] = True
            await wait(guest_run, lambda state: state.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
            await avatar_probe(guest_run, "aim")
            await wait(guest_run, lambda state: state.get("game", {}).get("native_avatar_local_aim") is True, timeout=8)
            try:
                aimed_guest = await wait(host_run, lambda state: aimed_pistol(state, on_host=True), timeout=10)
            except RuntimeError:
                if movement:
                    print("Guest aim pose:", await avatar_probe(host_run, "readpose"), flush=True)
                    await asyncio.sleep(.45)
                    print("Guest aim pose:", await avatar_probe(host_run, "readpose"), flush=True)
                raise
            evidence["guest_aimed_pistol"] = aimed_pistol(aimed_guest, on_host=True)
            await avatar_probe(guest_run, "lower")
            await wait(guest_run, lambda state: state.get("game", {}).get("native_avatar_local_aim") is False, timeout=8)
            await wait(host_run, lambda state: (peer := equipped_projection(state, on_host=True))
                       and peer.get("pose_clip") == "HandGun_Idle"
                       and 97 <= (peer.get("weapon_socket_height") or 0) <= 110, timeout=10)
            evidence["guest_aim_release_verified"] = True
            await command("action", *guest, "drop-link")
            await wait(host_run, lambda state: any(lease["character_id"] == invitation["character_id"]
                       and lease["state"] == "reserved" for lease in state.get("campaign", {}).get("leases", ())))
            assert (await command("action", *guest, "reconnect"))["resumed"]
            for run, on_host in ((host_run, True), (guest_run, False)):
                await wait(run, lambda state: equipped_projection(state, on_host=on_host))
            evidence["brief_reconnect"] = True
        for _ in range(80):
            images = list((guest_dir / "game-user").rglob("*.png"))
            if images:
                screenshot = images[0]
                assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), screenshot
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("The isolated guest did not write a screenshot")
        evidence["screenshot"] = str(screenshot)
        if equipment:
            await avatar_probe(host_run, "cycle")
            def slide_cycle(frame):
                return next((row for row in frame.get("game", {}).get("native_avatar_cycle", {}).values()
                             if row["weapon"].endswith(".Wep_Pistol_C") and row["count"] == 1
                             and not row["active"] and row["travel_cm"] > .1), None)
            rendered = await wait(guest_run, slide_cycle, timeout=10)
            proof = slide_cycle(rendered)
            cue, = events(guest_run, "native_avatar_cycle_played")
            assert (cue["weapon"], cue["clip"], cue["serial"]) == (proof["weapon"], proof["clip"], proof["serial"])
            evidence["pistol_cycle"] = proof
            await avatar_probe(host_run, "holster")
            def unarmed_host(state):
                rows = projections(state)
                if rows:
                    remote, = rows.values()
                    return remote.get("weapon_meshes") == [] and remote.get("appearance", {}).get("face", "").endswith(
                        ".SM_Head_UV_02_EarRing")
            await wait(guest_run, unarmed_host)
            evidence["host_holster_cleared"] = True
        if melee:
            await avatar_probe(host_run, "bat")
            loaded = await wait(guest_run, lambda state: any(
                any(mesh.endswith(".SM_Wep_Bat_01") for mesh in row.get("weapon_meshes", ()))
                for row in (projections(state) or {}).values()))
            pawn, = projections(loaded)
            def authoritative_health(state):
                pawns = state.get("game", {}).get("pawns", {})
                health = {key: pawns.get(key, {}).get("health") for key in ("local", "guest")}
                return health if all(type(value) in (int, float) for value in health.values()) else None
            host_state = await wait(host_run, authoritative_health)
            health = authoritative_health(host_state)
            await avatar_probe(host_run, "swing")
            def played(state):
                row = state.get("game", {}).get("native_avatar_melee", {}).get(pawn, {})
                return row if row.get("weapon_hand_motion_cm", 0) > 10 else None
            observed = await wait(guest_run, played, timeout=10)
            proof = played(observed)
            cue, = events(guest_run, "native_avatar_melee_played")
            assert (cue["serial"], cue["clip"]) == (proof["serial"], proof["clip"]), (cue, proof)
            settled = await wait(guest_run, lambda state: (row := played(state)) and not row["active"], timeout=5)
            assert settled["game"]["native_player_avatars"][pawn]["animation_mode"] == 0
            after = await wait(host_run, lambda state: state["game"]["sequence"] > host_state["game"]["sequence"]
                               and authoritative_health(state), timeout=5)
            assert authoritative_health(after) == health
            evidence["bat_swing"] = {"cue": cue, "motion": played(settled), "health": health}
        if lifecycle:
            await wait(host_run, lambda state: any(lease["character_id"] == identity and lease["state"] == "active"
                       for lease in state.get("campaign", {}).get("leases", ())))
            departure = await command("action", *guest, "leave-session")
            assert departure["retirement_confirmed"], departure
            assert await asyncio.wait_for(guest_process.wait(), 18) == 0
            await wait(host_run, lambda state: state.get("game", {}).get("remote_clients") == 0
                       and not state["game"].get("native_player_avatars"))
            env.pop("SILVERFISH_COOP_RENDER_CAPTURE")
            await start("guest-returned.txt", guest, *join)
            def restored(state, role):
                rows = projections(state)
                if not rows or any(row.get("diagnostic") or row.get("head_roll") != 0 for row in rows.values()):
                    return
                if equipment and any(row.get("appearance") != evidence["held"][role]["appearance"] for row in rows.values()):
                    return
                return rows
            host_returned = await wait(host_run, lambda state: restored(state, "host")
                                       and set(restored(state, "host")).isdisjoint(evidence["host"]))
            guest_returned = await wait(guest_run, lambda state: state.get("character_id") == identity and restored(state, "guest"))
            evidence["returned"] = {"host": projections(host_returned), "guest": projections(guest_returned)}
            assert all(row["state"] == "default_body" for peer in evidence["returned"].values() for row in peer.values()), evidence
        print(json.dumps(evidence, indent=2), flush=True)
        (host_run / "live-avatar-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    finally:
        for process, target in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *target, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await process.wait()
        assert before == saved_hashes(vanilla_saved), "Original singleplayer save files changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--lifecycle", action="store_true", help="verify avatar removal upon leaving and restoration upon rejoin")
    parser.add_argument("--equipment", action="store_true", help="verify distinct saved faces and multipart held pistols on both peers")
    parser.add_argument("--melee", action="store_true", help="verify a visible stock bat swing after the equipment checks")
    parser.add_argument("--pose", action="store_true", help="verify the centered rig and stock attachments through a turn and jump")
    parser.add_argument("--rifle", action="store_true", help="verify a guest's stock break-action quick-slot selection")
    parser.add_argument("--consumable", action="store_true", help="verify stock host Bandage geometry and positional sound")
    parser.add_argument("--movement", action="store_true", help="sample stock walk and sprint on the runner and the observing peer")
    parser.add_argument("--movement-traits", action="store_true", help="give only the guest a stock movement-speed effect")
    parser.add_argument("--aim-movement", action="store_true",
                        help="verify pistol and break-action ADS while strafing, crouching, and crouch-walking")
    options = parser.parse_args()
    if options.melee and (not options.equipment or options.lifecycle):
        parser.error("--melee requires --equipment and runs independently of --lifecycle")
    if options.movement_traits:
        options.movement = True
    if options.pose and (options.equipment or options.lifecycle):
        parser.error("--pose verifies the rig independently of --equipment and --lifecycle")
    if options.rifle and (options.pose or options.equipment or options.lifecycle):
        parser.error("--rifle verifies stock guest equipment selection independently")
    if options.aim_movement and (options.pose or options.equipment or options.lifecycle
                                 or options.movement and not options.rifle):
        parser.error("--aim-movement can run alone or with --rifle")
    work = options.work.resolve(strict=True)
    with installed_probe(work) if any(
        (options.equipment, options.pose, options.rifle, options.consumable, options.movement, options.aim_movement)
    ) else nullcontext():
        asyncio.run(capture(work, options.vanilla_saved.resolve(strict=True),
                            options.lifecycle, options.equipment, options.melee, options.pose,
                            options.rifle, options.consumable, options.movement, options.movement_traits,
                            options.aim_movement))
