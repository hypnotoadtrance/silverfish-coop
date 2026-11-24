"""Verify both remote avatar projections and capture an offscreen guest PNG."""

import argparse
import asyncio
from collections import deque
from contextlib import contextmanager, nullcontext, suppress
import json
import math
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
import time
import uuid

from live_ai_smoke import status, wait
from live_guest_held_fire_smoke import events
from live_multi_smoke import CROSSROADS, saved_hashes


REPO = Path(__file__).resolve().parents[1]
PROBE = "SilverfishAvatarProbe"


@contextmanager
def installed_probe(work, fixture="avatar_presentation_probe.lua"):
    mod = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods" / PROBE
    mod.mkdir()
    scripts = mod / "Scripts"
    script, activation = scripts / "main.lua", mod / "enabled.txt"
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / fixture, script)
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
                    if allow_pending and fields[1] == "error" and fields[-1].endswith("avatar_probe_rig_pending"):
                        return
                    assert fields[1] == "ok", fields[-1]
                    return fields[2:]
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.025 if operation == "rig" else .1)


def flashlight_owner(values):
    player, source, role, replicated, powered, visible, *data = values
    x, y, z, pitch, yaw, roll, intensity, radius, inner, outer, cookie, calls, clicks = data
    return dict(player=int(player), source=source, role=role, replicated=replicated == "true",
                powered=powered == "true", visible=visible == "true", location=list(map(float, (x, y, z))),
                rotation=list(map(float, (pitch, yaw, roll))), intensity=float(intensity), radius=float(radius),
                inner=float(inner), outer=float(outer), light_cookie=cookie, power_calls=int(calls), click_calls=int(clicks))


def flashlight_observer(values):
    remote, sources, powered, spotlights, pawn_lights, held_sources, display_meshes = values
    return dict(remote=remote, sources=int(sources), powered_sources=int(powered), visible_source_spotlights=int(spotlights),
                visible_pawn_spotlights=int(pawn_lights), stock_held_source_count=int(held_sources),
                displayed_flashlight_mesh_count=int(display_meshes))


def beam_geometry(axes):
    x, y, z, pitch, yaw, roll, sx, sy, sz, r, g, b = map(float, axes)
    return dict(relative_position=[x, y, z], relative_rotation=[pitch, yaw, roll], relative_scale=[sx, sy, sz], color=[r, g, b])


def torch_geometry(values):
    mesh, profile, *axes = values
    return dict(mesh=mesh, stock_profile=profile, **beam_geometry(axes))


def projected_geometry(values):
    if values == ["-"]:
        return None
    mesh, spotlight, cookie, inner, outer, intensity, radius, *axes = values
    return dict(mesh=mesh, spotlight=spotlight, light_cookie=cookie, inner=float(inner), outer=float(outer),
                intensity=float(intensity), radius=float(radius), **beam_geometry(axes))


def verify_heavy_geometry(observer_states, owner, proof):
    before, projected, after = (state["projected_beam"] for state in observer_states)
    assert before is None and after is None and projected, proof
    assert all(projected[key] == owner[key] for key in ("mesh", "light_cookie", "inner", "outer", "intensity", "radius", "color")), proof
    assert all(math.isclose(a, b, abs_tol=.05) for key in ("relative_position", "relative_rotation", "relative_scale")
               for a, b in zip(projected[key], owner[key], strict=True)), proof


def offhand_transform(values):
    x, y, z, pitch, yaw, roll, sx, sy, sz = map(float, values)
    return {"position": [x, y, z], "rotation": [pitch, yaw, roll], "scale": [sx, sy, sz]}


def offhand_owner(values):
    player, primary, primary_slot, primary_guid, torch_slot, torch_guid, offhand, item, *light = values
    result = {"player": int(player), "primary": primary, "primary_slot": int(primary_slot),
              "primary_guid": primary_guid, "torch_slot": int(torch_slot), "torch_guid": torch_guid,
              "offhand": offhand == "true", "item": None if item == "-" else item}
    if light:
        replicates, camera, item_owner, *sample = light
        result.update(replicates=replicates == "true", camera=camera, item_owner=item_owner,
                      box_camera_relative=offhand_transform(sample[:9]), stock_offhand_transform=offhand_transform(sample[9:18]),
                      torch_pawn_relative=offhand_transform(sample[18:27]),
                      light=dict(flashlight_owner(sample[27:-14]), **torch_geometry(sample[-14:])))
    return result


def offhand_beam(values):
    if values == ["-"]:
        return None
    palm_error, lx, ly, lz, *angles = values
    return dict(palm_error_cm=float(palm_error), lens=[float(lx), float(ly), float(lz)], pawn_relative=offhand_transform(angles))


def verify_offhand_beam(beam, receipt, phase):
    assert beam["palm_error_cm"] <= 3 and math.dist(beam["lens"], (0, 21.66062, 0)) < .1, phase
    assert all(abs(math.remainder(a - b, 360)) <= 5
               for a, b in zip(receipt["aim"], beam["pawn_relative"]["rotation"], strict=True)), phase


async def observe_stock_offhand_flashlight(host_run, guest_run, *, verify_view=False):
    proof_path = host_run / ("live-offhand-flashlight-view-probe.json" if verify_view
                             else "live-offhand-flashlight-source-probe.json")
    proof = {"verified": False, "phases": {}}

    def record():
        proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    async def phase(name, row, *, after_aim=None):
        proof["phases"][name] = table = {"owner": row}
        record()
        if verify_view:
            if row["offhand"]:
                frame = await wait(guest_run, lambda frame: (
                    (view := frame.get("game", {}).get("native_flashlight_view", {})).get("profile") == "offhand"
                    and view.get("player") == row["player"] and view.get("visible") is True
                    and (after_aim is None or math.dist(view["aim"], after_aim) > 4)
                ), timeout=9)
                table["receipt"] = frame["game"]["native_flashlight_view"]
                await asyncio.sleep(.30)
            else:
                await wait(guest_run, lambda frame: "native_flashlight_view" not in frame.get("game", {}), timeout=8)
        samples = await avatar_probe(guest_run, f"observe{row['player']}")
        table["guest"] = observer = flashlight_observer(samples[:7])
        bones = samples[7:]
        if verify_view:
            boundary = bones.index("beam")
            bones, beam = bones[:boundary], bones[boundary+1:]
            table["beam"] = offhand_beam(beam)
        table["guest_rig_pawn_relative"] = {}
        if bones != ["-"]:
            rig = table["guest_rig_pawn_relative"] = {
                bones[i]: offhand_transform(bones[i + 1:i + 10]) for i in range(0, len(bones), 10)
            }
            if "LeftHand" in rig and "torch_pawn_relative" in row:
                table["torch_left_hand_separation_cm"] = math.dist(
                    rig["LeftHand"]["position"], row["torch_pawn_relative"]["position"],
                )
        host_game, guest_game = status(host_run).get("game", {}), status(guest_run).get("game", {})
        table["host_flashlight_frame"] = host_game.get("native_flashlight")
        table["guest_flashlight_view"] = guest_game.get("native_flashlight_view")
        table["guest_primary_meshes"] = guest_game["native_player_avatars"][observer["remote"]]["weapon_meshes"]
        if verify_view and row["offhand"]:
            table["receipt"] = table["guest_flashlight_view"]
        record()
        return table

    async def stock_clears(operation):
        async with asyncio.timeout(8):
            while not (ready := await avatar_probe(host_run, operation, allow_pending=True)):
                await asyncio.sleep(.1)
        return ready

    seeded = offhand_owner(await avatar_probe(host_run, "seedoffhand"))
    try:
        await wait(guest_run, lambda status: any(row.get("player_id") == seeded["player"] and row.get("weapon_meshes")
                   for row in status.get("game", {}).get("native_player_avatars", {}).values()), timeout=8)
        await phase("primary_only", seeded)
        started = offhand_owner(await avatar_probe(host_run, "offhandon"))
        started_view = await phase("offhand_started", started)
        await asyncio.sleep(.4)
        later = offhand_owner(await avatar_probe(host_run, "turnoffhand" if verify_view else "readoffhand"))
        await phase("offhand_later", later, after_aim=started_view["receipt"]["aim"] if verify_view else None)
        clock = asyncio.get_running_loop().time
        released_at = clock()
        lowering = offhand_owner(await avatar_probe(host_run, "offhandoff"))
        await phase("offhand_lowering", lowering)
        cleared = lowering if verify_view else offhand_owner(await stock_clears("waitoffhand"))
        await phase("offhand_cleared", cleared)
        proof["offhand_clear_observed_ms"] = round((clock() - released_at) * 1000)
        assert not seeded["offhand"] and started["offhand"] and later["offhand"] and not cleared["offhand"], proof
        assert not lowering["offhand"] and (verify_view or cleared["item"] is None), proof
        assert seeded["primary_slot"] != seeded["torch_slot"] and seeded["primary_guid"] != seeded["torch_guid"], proof
        assert all(row[key] == seeded[key] for row in (started, later, lowering, cleared) for key in (
            "player", "primary", "primary_slot", "primary_guid", "torch_slot", "torch_guid",
        )), proof
        assert started["item"] == later["item"] and started["light"]["source"] == later["light"]["source"], proof
        assert all(row["light"]["powered"] and row["light"]["visible"] and row["light"]["player"] == seeded["player"]
                   and (row["light"]["inner"], row["light"]["outer"], row["light"]["intensity"], row["light"]["radius"]) ==
                   (15, 40, 17, 5000) for row in (started, later)), proof
        first = proof["phases"]["primary_only"]["guest_primary_meshes"]
        assert first and all(phase["guest_primary_meshes"] == first for phase in proof["phases"].values()), proof
        if verify_view:
            for phase in proof["phases"].values():
                on = phase["owner"]["offhand"]
                census = phase["guest"]
                assert census["powered_sources"] == census["visible_source_spotlights"] == 0, phase
                assert census["visible_pawn_spotlights"] == census["displayed_flashlight_mesh_count"] == int(on), phase
                if on:
                    beam, receipt, source = phase["beam"], phase["receipt"], phase["host_flashlight_frame"]
                    assert source["profile"] == receipt["profile"] == "offhand" and source["player"] == receipt["player"], phase
                    verify_offhand_beam(beam, receipt, phase)
                else:
                    assert phase["beam"] is None and phase["guest_flashlight_view"] is None, phase
        else:
            assert all(phase["host_flashlight_frame"] is None and phase["guest_flashlight_view"] is None
                       and all(phase["guest"][key] == 0 for key in ("powered_sources", "visible_source_spotlights",
                               "visible_pawn_spotlights", "displayed_flashlight_mesh_count")) for phase in proof["phases"].values()), proof
        proof["verified"] = True
    finally:
        try:
            await avatar_probe(host_run, "offhandoff")
            if verify_view:
                proof["stock_cleanup"] = ("restored" if await avatar_probe(host_run, "clearoffhand", allow_pending=True)
                                          else "private_session_shutdown")
            else:
                proof["stock_cleanup"] = await stock_clears("clearoffhand") == ["restored"]
        finally:
            record()
        assert proof["stock_cleanup"], proof
    return proof


async def observe_guest_offhand_flashlight(host_run, guest_run):
    proof_path = host_run / "live-guest-offhand-flashlight-view-probe.json"
    proof = {"verified": False, "phases": {}, "stock_c_inputs": {}}
    view_key = "native_guest_flashlight_view"
    authority_key = "native_guest_flashlight_authority"
    source_key = "native_guest_flashlight_source"
    clock = asyncio.get_running_loop().time
    source_frames = deque(maxlen=64)
    watcher = None
    trace = None

    def record():
        proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    async def command_when_ready(operation):
        async with asyncio.timeout(15):
            while not (row := await avatar_probe(guest_run, operation, allow_pending=True)):
                await asyncio.sleep(.1)
        return row

    async def hold_c(name, active):
        track = proof["stock_c_inputs"][name] = []
        for command, label, stock_flag in (
            ("coffhandstart", "ongoing_start", not active),
            ("coffhandhold", "ongoing_hold", active),
            ("coffhandrelease", "completed_release", active),
        ):
            edge, elapsed, powered = await command_when_ready(command)
            track.append({"event": edge, "elapsed_seconds": float(elapsed), "stock_offhand": powered == "true"})
            record()
            assert edge == label and (powered == "true") is stock_flag, track
        assert track[0]["elapsed_seconds"] == 0 and track[2]["elapsed_seconds"] >= track[1]["elapsed_seconds"] >= .32, track

    pristine = await avatar_probe(host_run, "guestoffhandboundary")
    authority_pawn, gun_guid, torch_guid = await avatar_probe(host_run, "guestoffhandseed")
    proof["private_guest_slots"] = {"authority_pawn": authority_pawn, "pistol": gun_guid, "torch": torch_guid}
    record()
    try:
        seeded = offhand_owner(await command_when_ready(f"selectoffhandgun{gun_guid}{torch_guid}"))
        if os.getenv("SILVERFISH_COOP_GUEST_OFFHAND_TRACE") == "1":
            start = time.time()
            lua_start, = await avatar_probe(guest_run, "traceoffhandstart")
            trace = proof["stock_offhand_call_trace"] = {
                "lua_started": float(lua_start), "unix_started_bounds": [start, time.time()],
            }
        player = str(seeded["player"])
        authority = {"pawn": authority_pawn, "profile": "offhand"}
        await wait(host_run, lambda frame: frame.get("game", {}).get(authority_key, {}).get(player) == authority, timeout=10)
        async with asyncio.timeout(5):
            while not (selections := [row["native_guest_weapon"] for row in events(guest_run, "native_guest_equipment_selected")
                                      if row.get("native_guest_weapon", {}).get("guid", "").lower() == gun_guid]):
                await asyncio.sleep(.1)
        selection, = selections
        assert selection["pawn"] == authority_pawn and seeded["primary_guid"] == gun_guid and seeded["torch_guid"] == torch_guid
        local = await wait(guest_run, lambda frame: (
            frame.get("game", {}).get("state") == "connected" and frame.get("zone_epoch")
            and frame["game"].get("pawns", {}).get("local", {}).get("name")
        ), timeout=5)
        local_pawn = local["game"]["pawns"]["local"]["name"]
        local_map = local["game"]["map"]
        epoch = local["zone_epoch"]
        proof["guest_source_scope"] = {"pawn": local_pawn, "map": local_map, "epoch": epoch}

        def guest_frame():
            frame = status(guest_run)
            game = frame.get("game", {})
            if (game.get("state"), game.get("map"), game.get("pawns", {}).get("local", {}).get("name"),
                    frame.get("zone_epoch")) == ("connected", local_map, local_pawn, epoch):
                return game

        async def watch_source():
            previous_seq = None
            while True:
                game = guest_frame()
                if game and game.get("sequence") != previous_seq:
                    previous_seq = game.get("sequence")
                    source = game.get(source_key)
                    sample = (source or {}).get("sample")
                    if (source and source.get("pawn") == local_pawn and isinstance(sample, list)
                            and len(sample) == 7 and sample[2:4] == [True, "offhand"]):
                        source_frames.append((clock(), previous_seq, source))
                await asyncio.sleep(.05)

        watcher = asyncio.create_task(watch_source())

        async def diagnose_turn(label, since):
            item = {"phase": label, "seconds_after_turn": round(clock() - since, 3)}
            try:
                async with asyncio.timeout(4):
                    gate, *values = await avatar_probe(guest_run, "diagnoseoffhand")
                item["first_rejected_check"] = None if gate == "qualified" else gate
                item["stock_values"] = dict(zip(values[::2], values[1::2], strict=True))
            except (TimeoutError, AssertionError, OSError) as error:
                item["probe_error"] = str(error) or type(error).__name__
            guest_status = guest_frame() or {}
            item["guest_source_sequence"] = guest_status.get("sequence")
            item["guest_source"] = guest_status.get(source_key)
            item["host_receipt"] = status(host_run).get("game", {}).get(view_key, {}).get(player)
            proof.setdefault("stock_turn_diagnostics", []).append(item)
            record()
            return item

        def aligned_source(receipt, since, turn):
            if (receipt is None or (receipt.get("epoch"), receipt.get("profile"), receipt.get("power"),
                                    receipt.get("visible")) != (epoch, "offhand", True, True)):
                return None
            identity = [receipt["instance"], receipt["switch"], True, "offhand"]
            if turn is not None and identity != turn["identity"]:
                return None
            for stamp, seq, source in reversed(source_frames):
                sample = source["sample"]
                if stamp < since or sample[:4] != identity:
                    continue
                if turn is not None and (seq <= turn["sequence"] or math.hypot(*(
                        math.remainder(a - b, 360)
                        for a, b in zip(sample[4:], turn["aim"], strict=True))) <= 4):
                    continue
                difference = max(abs(math.remainder(a - b, 360))
                                 for a, b in zip(sample[4:], receipt["aim"], strict=True))
                if difference <= 2:
                    return seq, source, difference
            return None

        async def phase(name, owner, *, since=None, turn=None):
            table = proof["phases"][name] = {"owner": owner}
            record()
            try:
                async with asyncio.timeout(9 if owner["offhand"] else 8):
                    while True:
                        prior = status(host_run).get("game", {}).get(view_key, {}).get(player)
                        aligned = aligned_source(prior, since, turn) if owner["offhand"] else None
                        if (owner["offhand"] and not aligned) or (not owner["offhand"] and prior is not None):
                            await asyncio.sleep(.10)
                            continue
                        values = await avatar_probe(host_run, f"observe{player}")
                        local = status(host_run)["game"]
                        receipt = local.get(view_key, {}).get(player)
                        beam = offhand_beam(values[values.index("beam")+1:])
                        table["host"] = census = flashlight_observer(values[:7])
                        table["beam"], table["receipt"] = beam, receipt
                        record()
                        if not owner["offhand"] and receipt is None and beam is None:
                            break
                        if (aligned and beam and receipt == prior
                                and all(abs(math.remainder(a - b, 360)) <= 5 for a, b in zip(
                                    receipt["aim"], beam["pawn_relative"]["rotation"], strict=True))):
                            (table["guest_source_sequence"], table["guest_source"],
                             table["guest_source_aim_error_degrees"]) = aligned
                            break
                        await asyncio.sleep(.15)
            except TimeoutError:
                table["last_host_receipt"] = status(host_run).get("game", {}).get(view_key, {}).get(player)
                if since is not None:
                    table["guest_source_candidates"] = [
                        {"sequence": seq, "source": source}
                        for stamp, seq, source in source_frames if stamp >= since
                    ][-8:]
                record()
                raise AssertionError(f"No aligned stock guest source and observer readback for {name} in {proof_path}") from None
            table["host_primary_meshes"] = local["native_player_avatars"][authority_pawn]["weapon_meshes"]
            guest_status = guest_frame() or {}
            table["guest_source_at_observer_read"] = guest_status.get(source_key)
            record()
            assert census["remote"] == authority_pawn and local[authority_key][player] == authority, table
            assert census["visible_pawn_spotlights"] == census["displayed_flashlight_mesh_count"] == int(owner["offhand"]), table
            assert census["powered_sources"] == census["visible_source_spotlights"] == 0, table
            assert [owner[key] for key in ("primary", "primary_guid", "torch_guid")] == [
                seeded["primary"], gun_guid, torch_guid,
            ], table
            if owner["offhand"]:
                assert owner["light"]["powered"] and owner["light"]["visible"] and owner["replicates"] is False, table
                verify_offhand_beam(beam, receipt, table)
            else:
                assert beam is None and receipt is None, table
                await wait(guest_run, lambda _: (game := guest_frame()) is not None and source_key not in game, timeout=6)
            record()
            return table

        baseline = await phase("primary_only", seeded)
        equipped_at = clock()
        await hold_c("equip", True)
        await phase("offhand_started", offhand_owner(await avatar_probe(guest_run, "readoffhand")), since=equipped_at)
        before = await diagnose_turn("before_turn", clock())
        assert before.get("first_rejected_check", "-") is None and before.get("guest_source"), before
        turn = {"sequence": before["guest_source_sequence"], "identity": before["guest_source"]["sample"][:4],
                "aim": [float(before["stock_values"][f"aim_{axis}_raw"]) for axis in ("pitch", "yaw", "roll")]}
        turned_at = clock()
        later = offhand_owner(await avatar_probe(guest_run, "turnoffhand"))
        for delay in (.20, .65):
            await asyncio.sleep(max(0., turned_at + delay - clock()))
            await diagnose_turn("after_turn", turned_at)
        try:
            await phase("offhand_turned", later, since=turned_at, turn=turn)
        except (RuntimeError, AssertionError):
            await diagnose_turn("after_observer_failure", turned_at)
            raise
        await hold_c("holster", False)
        await phase("offhand_cleared", offhand_owner(await avatar_probe(guest_run, "readoffhand")))
        assert all(row["host_primary_meshes"] == baseline["host_primary_meshes"] for row in proof["phases"].values()), proof
        assert await avatar_probe(host_run, "guestoffhandboundary") == pristine
        proof["host_save_full_name"] = pristine[0]
        proof["host_equipment_and_offhand_flag_unchanged"] = True
        proof["stock_guest_selection"] = selection
        proof["verified"] = True
    finally:
        if watcher is not None:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher
        if trace is not None:
            try:
                async with asyncio.timeout(5):
                    count, *values = await avatar_probe(guest_run, "traceoffhandstop")
                columns = ("event", "lua_time", "argument", "seat", "pawn", "pawn_role", "save", "shares_local_save",
                           "local_offhand", "bound_save", "offhand", "force_lower",
                           "equip_index", "held_class", "held_type", "two_handed", "held_actor", "offhand_actor",
                           "pistol_slot_class", "pistol_slot_guid", "torch_slot_class", "torch_slot_guid")
                end = int(count) * len(columns)
                trace["calls"] = [dict(zip(columns, values[i:i + len(columns)], strict=True))
                                  for i in range(0, end, len(columns))]
                trace["hooks"] = dict(zip(values[end::2], values[end + 1::2], strict=True))
                trace["guest_ui_projections"] = events(guest_run, "native_guest_ui_projected")
            except (TimeoutError, AssertionError, OSError, ValueError) as error:
                trace["probe_error"] = str(error) or type(error).__name__
        record()
    return proof


async def observe_guest_stock_flashlight(host_run, guest_run, *, verify_view=False, heavy=False):
    proof_path = host_run / ("live-guest-heavy-flashlight-source-probe.json" if heavy
                             else "live-guest-flashlight-source-probe.json")
    proof = {"verified": False, "phases": {}}
    selection_events = ("native_guest_equipment_selected", "native_guest_equipment_selection_rejected")
    heavy_asset = "/Game/Classes/Equipment/Eqp_Heavy_Flashlight.Eqp_Heavy_Flashlight_C"
    heavy_mesh = ("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/"
                  "SM_Prop_Flashlight_02.SM_Prop_Flashlight_02")

    def record():
        proof["selection_events"] = {key: events(guest_run, key)[-4:] for key in selection_events}
        proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    def owner(values):
        if heavy:
            values, geometry = values[:-14], values[-14:]
        guid, stacks, mode = values[-3:]
        row = dict(flashlight_owner(values[:-3]), slot_guid=guid, stacks=stacks, stock_mode=mode)
        if heavy:
            row.update(torch_geometry(geometry))
        return row

    async def phase(name, row, *, switch=None):
        table = proof["phases"][name] = {"owner": row}
        record()
        if verify_view:
            id_text = str(row["player"])
            try:
                hosted = await wait(host_run, lambda frame: (
                    (local := frame.get("game", {}).get("native_guest_flashlight_view", {}).get(id_text))
                    and local["power"] is row["powered"] and local["visible"] is row["visible"]
                    and local.get("profile") == ("heavy" if heavy else None)
                    and (local["heard"] is None if switch is None else local["heard"] == switch and local["switch"] == switch)
                ), timeout=8)
                table["receipt"] = hosted["game"]["native_guest_flashlight_view"][id_text]
                expected = [table["receipt"]["instance"], table["receipt"]["switch"], row["powered"]]
                if heavy:
                    expected.append("heavy")
                source = await wait(guest_run, lambda frame: (
                    frame.get("game", {}).get("native_guest_flashlight_source", {}).get("sample") == expected
                ), timeout=5)
                table["source"] = source["game"]["native_guest_flashlight_source"]
            finally:
                host_game = status(host_run).get("game", {})
                guest_game = status(guest_run).get("game", {})
                table["last_host_authority"] = host_game.get("native_guest_flashlight_authority")
                table["last_host_view"] = host_game.get("native_guest_flashlight_view")
                table["last_guest_source"] = guest_game.get("native_guest_flashlight_source")
                record()
        async with asyncio.timeout(6):
            while True:
                values = await avatar_probe(host_run, f"observe{row['player']}")
                if heavy and verify_view:
                    values, projected = values[:11], values[11:]
                guid, held_class, mode, power = values[-4:]
                mirrored = table["host"] = dict(flashlight_observer(values[:-4]), slot_guid=guid,
                                                authoritative_equipment=held_class, authoritative_mode=mode,
                                                authoritative_power=power)
                if heavy and verify_view:
                    mirrored["projected_beam"] = projected_geometry(projected)
                if not heavy or name != "off" or mirrored["displayed_flashlight_mesh_count"] == 1:
                    break
                await asyncio.sleep(.2)
        record()
        host_guest = status(host_run)["game"]["pawns"]["guest"]["name"]
        assert table["host"]["remote"] == host_guest, (table["host"], host_guest)

    guid, = await avatar_probe(host_run, "seedguest")
    proof["seeded_guid"] = guid
    if heavy:
        proof["stock_item"] = {"class": heavy_asset, "stock_factory": "FL_Inventory.MakeDefaultItem", "slot_index": 0,
                               "storage": "private host detached guest Save.PlayerEquipment", "guid": guid,
                               "observer_mesh": heavy_mesh}
    record()
    try:
        async with asyncio.timeout(15):
            while not (selected := await avatar_probe(guest_run, f"selectlight{guid}", allow_pending=True)):
                await asyncio.sleep(.1)
        equipped = owner(selected)
        baseline = owner(await avatar_probe(guest_run, "lightoff")) if equipped["powered"] else equipped
        # Give the normal quickslot relay one bounded window to accept or refuse the selected GUID.
        # The stock owner read remains useful when the capture build has no selection relay.
        try:
            async with asyncio.timeout(4):
                while not any(events(guest_run, key) for key in selection_events):
                    await asyncio.sleep(.1)
        except TimeoutError:
            pass
        await phase("off", baseline)
        switch = proof["phases"]["off"].get("receipt", {}).get("switch", 0)
        lit = owner(await avatar_probe(guest_run, "lighton"))
        await phase("on", lit, switch=switch + 1)
        unlit = owner(await avatar_probe(guest_run, "lightoff"))
        await phase("off_again", unlit, switch=switch + 2)
        host_rows = [phase["host"] for phase in proof["phases"].values()]
        assert all(row["slot_guid"] == guid and row["player"] == equipped["player"]
                   and row["source"] == equipped["source"] for row in (baseline, lit, unlit)), proof
        assert all(row["slot_guid"] == guid and row["remote"] == host_rows[0]["remote"] for row in host_rows), proof
        assert [(row["powered"], row["visible"]) for row in (baseline, lit, unlit)] == [
            (False, False), (True, True), (False, False),
        ], proof
        assert (lit["click_calls"] - baseline["click_calls"], unlit["click_calls"] - lit["click_calls"]) == (1, 1), proof
        if heavy:
            owners = (baseline, lit, unlit)
            cookie = "Material /Game/Assets/Materials/Mat_Misc/M_LightCookie.M_LightCookie"
            assert all((row["mesh"], row["role"], row["replicated"], row["light_cookie"]) ==
                       ("StaticMesh " + heavy_mesh, "3", False, cookie) for row in owners), proof
            assert all((row["inner"], row["outer"], row["intensity"], row["radius"], row["color"])
                       == (30, 60, 20, 6000, [1, 1, 1]) for row in owners), proof
            assert all(math.isclose(a, b, abs_tol=.05) for row in owners for field in
                       ("relative_position", "relative_rotation", "relative_scale")
                       for a, b in zip(row[field], lit[field], strict=True)), proof
            assert (lit["power_calls"] - baseline["power_calls"], unlit["power_calls"] - lit["power_calls"]) == (1, 1), proof
            assert all((row["authoritative_equipment"], row["authoritative_power"], row["authoritative_mode"],
                        row["stock_held_source_count"], row["displayed_flashlight_mesh_count"])
                       == ("BlueprintGeneratedClass " + heavy_asset, "false", "false", 1, 1) for row in host_rows), proof
            selected_receipts = [row["native_guest_weapon"] for row in events(guest_run, selection_events[0])
                                 if row.get("native_guest_weapon", {}).get("guid", "").lower() == guid]
            assert len(selected_receipts) == 1 and selected_receipts[0]["pawn"] == host_rows[0]["remote"], proof
            assert selected_receipts[0]["actor_ammo"] == selected_receipts[0]["stored_ammo"] == -1, selected_receipts
        if verify_view:
            assert [row["visible_pawn_spotlights"] for row in host_rows] == [0, 1, 0], proof
            assert all(row["powered_sources"] == row["visible_source_spotlights"] == 0
                       and row["displayed_flashlight_mesh_count"] == 1 for row in host_rows), proof
            assert len({phase["receipt"]["instance"] for phase in proof["phases"].values()}) == 1, proof
            if heavy:
                verify_heavy_geometry(host_rows, lit, proof)
            assert await avatar_probe(guest_run, "clearlight") == ["restored"]
            await wait(guest_run, lambda state: "native_guest_flashlight_source" not in state.get("game", {}), timeout=6)
            await wait(host_run, lambda state: "native_guest_flashlight_view" not in state.get("game", {}), timeout=8)
            proof["owner_holstered_and_view_cleared"] = True
        proof["verified"] = True
    finally:
        record()
    return proof


async def observe_stock_flashlight(host_run, guest_run, *, verify_view=False, heavy=False):
    proof_path = host_run / ("live-heavy-flashlight-projection-probe.json" if heavy else "live-flashlight-projection-probe.json")
    diagnostic = {"verified": False, "phases": {}}

    def record():
        if verify_view:
            proof_path.write_text(json.dumps(diagnostic, indent=2) + "\n", encoding="utf-8")

    async def owner(operation):
        values = await avatar_probe(host_run, operation)
        if heavy:
            return dict(flashlight_owner(values[:-14]), **torch_geometry(values[-14:]))
        return flashlight_owner(values)

    async def observer(player):
        values = await avatar_probe(guest_run, f"observe{player}")
        if heavy:
            return dict(flashlight_observer(values[:7]), projected_beam=projected_geometry(values[7:]))
        return flashlight_observer(values)

    async def view_receipt(phase, owner_state, *, click):
        player, powered = owner_state["player"], owner_state["powered"]
        proof = diagnostic["phases"].setdefault(phase, {"owner": owner_state, "samples": []})
        mailbox, = (guest_run / "game-bridge").iterdir()
        command_file = mailbox / "command.tsv"
        record()
        expected = ("host_capture", "guest_delivery", "guest_accept", "guest_beam", "guest_cue")
        witnessed = {}
        view = None
        try:
            async with asyncio.timeout(8):
                while len(witnessed) != len(expected):
                    host, guest = status(host_run), status(guest_run)
                    host_game, guest_game = host.get("game", {}), guest.get("game", {})
                    source = host_game.get("native_flashlight") or {}
                    delivery = (guest.get("gameplay") or {}).get("native_flashlight") or {}
                    view = guest_game.get("native_flashlight_view") or {}
                    try:
                        nonce, action, *_ = command_file.read_text(encoding="utf-8").strip().split("\t")
                    except (OSError, ValueError):
                        nonce, action = None, None
                    ack = guest_game.get("ack", {})
                    if action == "native-flashlight-view" and ack.get("id") == nonce:
                        proof["local_ack"] = ack
                    sample = {"host_sequence": host_game.get("sequence"), "guest_sequence": guest_game.get("sequence"),
                              "source": source, "delivery": delivery, "view": view}
                    if sample not in proof["samples"]:
                        proof["samples"] = (proof["samples"] + [sample])[-6:]
                    if (source.get("player") == player and source.get("power") is powered
                            and source.get("profile") == ("heavy" if heavy else None)):
                        witnessed.setdefault("host_capture", sample["host_sequence"])
                        identity = (player, source["instance"], source["switch"], powered, source.get("profile"))
                        key = ("player", "instance", "switch", "power", "profile")
                        if (tuple(delivery.get(field) for field in key) == identity
                                and delivery.get("epoch") == guest.get("zone_epoch")):
                            witnessed.setdefault("guest_delivery", sample["guest_sequence"])
                        if (tuple(view.get(field) for field in key) == identity
                                and view.get("epoch") == guest.get("zone_epoch")):
                            witnessed.setdefault("guest_accept", sample["guest_sequence"])
                            if view.get("visible") is powered:
                                witnessed.setdefault("guest_beam", sample["guest_sequence"])
                            if view.get("heard") == (view["switch"] if click else None):
                                witnessed.setdefault("guest_cue", sample["guest_sequence"])
                    if len(witnessed) != len(expected):
                        await asyncio.sleep(.1)
        except TimeoutError:
            pass
        proof["witnessed"] = witnessed
        proof["missing"] = [name for name in expected if name not in witnessed]
        record()
        return view

    seeded = await owner("seedlight")
    try:
        equipped_mesh = "SM_Prop_Flashlight_02.SM_Prop_Flashlight_02" if heavy else "SM_Prop_Flashlight_01.SM_Prop_Flashlight_01"

        def shows_flashlight(state):
            avatars = state.get("game", {}).get("native_player_avatars", {}).values()
            return any(row.get("player_id") == seeded["player"] and any(mesh.endswith(equipped_mesh)
                       for mesh in row.get("weapon_meshes", ())) for row in avatars)

        mounted = await wait(guest_run, shows_flashlight)
        baseline = await owner("lightoff") if seeded["powered"] else seeded
        off_view = await view_receipt("off", baseline, click=False) if verify_view else None
        before = await observer(seeded["player"])
        if verify_view:
            diagnostic["phases"]["off"]["observer_census"] = before
            record()
        lit = await owner("lighton")
        on_view = await view_receipt("on", lit, click=True) if verify_view else None
        seen_lit = await observer(seeded["player"])
        if verify_view:
            diagnostic["phases"]["on"]["observer_census"] = seen_lit
            record()
        unlit = await owner("lightoff")
        reset_view = await view_receipt("off_again", unlit, click=True) if verify_view else None
        seen_unlit = await observer(seeded["player"])
        if verify_view:
            diagnostic["phases"]["off_again"]["observer_census"] = seen_unlit
            record()
            missing = {phase: proof["missing"] for phase, proof in diagnostic["phases"].items() if proof["missing"]}
            assert not missing, f"Incomplete flashlight observer receipts {missing}. Evidence saved to {proof_path}"
        assert seeded["source"] == lit["source"] == unlit["source"], (seeded, lit, unlit)
        assert not baseline["visible"] and lit["powered"] and lit["visible"] and not (unlit["powered"] or unlit["visible"])
        assert lit["intensity"] > 0 and lit["radius"] > 0 and 0 <= lit["inner"] <= lit["outer"] <= 90, lit
        assert before["remote"] == seen_lit["remote"] == seen_unlit["remote"]
        evidence = {"equipped_mesh": equipped_mesh, "observer_display": mounted["game"]["native_player_avatars"][before["remote"]],
                    "owner": {"off": baseline, "on": lit, "off_again": unlit},
                    "observer": {"off": before, "on": seen_lit, "off_again": seen_unlit},
                    "stock_switch_cue_observed": unlit["click_calls"] - baseline["click_calls"]}
        if heavy:
            assert (lit["intensity"], lit["radius"], lit["inner"], lit["outer"], lit["color"], lit["stock_profile"]) == (
                20, 6000, 30, 60, [1, 1, 1], "5",
            ), evidence
            assert (lit["click_calls"] - baseline["click_calls"], unlit["click_calls"] - lit["click_calls"]) == (1, 1), evidence
            verify_heavy_geometry((before, seen_lit, seen_unlit), lit, evidence)
        if verify_view:
            assert on_view["switch"] == off_view["switch"] + 1 and reset_view["switch"] == on_view["switch"] + 1
            assert on_view["instance"] == off_view["instance"] == reset_view["instance"]
            assert [row["visible_source_spotlights"] + row["visible_pawn_spotlights"]
                    for row in (before, seen_lit, seen_unlit)] == [0, 1, 0], evidence
            assert all(row["powered_sources"] == 0 for row in (before, seen_lit, seen_unlit)), evidence
            evidence["observer_view"] = {"off": off_view, "on": on_view, "off_again": reset_view}
    finally:
        restored = await avatar_probe(host_run, "clearlight") == ["restored"]
        diagnostic["owner_restored"] = restored
        record()
        assert restored
    if verify_view:
        await wait(guest_run, lambda state: "native_flashlight_view" not in state.get("game", {})
                   and (not heavy or not shows_flashlight(state)), timeout=8)
        if heavy:
            holstered = await observer(seeded["player"])
            assert holstered["projected_beam"] is None and holstered["displayed_flashlight_mesh_count"] == 0, holstered
            diagnostic["observer_after_holster"] = holstered
            evidence["observer_after_holster"] = holstered
        diagnostic["verified"] = True
        record()
    return evidence


async def verify_pose(host_run, guest_run):
    fields = ("base_cm", "body_cm", "neck_error_cm", "head_offset_error_cm", "head_roll", "head_yaw",
              "tail_x", "tail_z", "tail_yaw", "actor_yaw", "falling", "skeletal_tail", "tail_gap_cm")

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
        assert row["skeletal_tail"] == int(skeletal) and row["tail_gap_cm"] < .15, row
        assert math.isclose(row["tail_yaw"], -10 if skeletal else 0, abs_tol=.02), row

    await avatar_probe(host_run, "style")
    initial = await observe(guest_run, lambda row: row["skeletal_tail"] == 1 and row["tail_yaw"] < -9
                            and row["falling"] == 0, timeout=15)
    stationary = await observe(host_run, lambda row: row["skeletal_tail"] == 0 and row["falling"] == 0, timeout=15)
    aligned(initial, skeletal=True)
    aligned(stationary, skeletal=False)
    def angle_between(first, second):
        return abs((first - second + 180) % 360 - 180)

    turn_samples = []

    def head_caught_up(row):
        if angle_between(row["actor_yaw"], initial["actor_yaw"]) <= 60:
            return False
        turn_samples.append(row)
        return angle_between(row["head_yaw"], row["actor_yaw"]) < 4 and row["body_cm"] < 3

    turned, _ = await asyncio.gather(observe(guest_run, head_caught_up), avatar_probe(host_run, "turn"))
    assert any(5 < angle_between(row["head_yaw"], initial["head_yaw"]) < 80 for row in turn_samples), turn_samples
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
    return {"guest_sees_host": initial, "host_sees_guest": stationary, "turned": turned, "turn_samples": turn_samples,
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


async def verify_aim_movement(host_run, guest_run, command, host, guest, projections, shotgun=False):
    async def stance(run, operation):
        crouched, grounded, x, y, yaw, clip, position, height = await avatar_probe(run, operation)
        return {"crouched": crouched == "1", "grounded": grounded == "1", "position": [float(x), float(y)],
                "yaw": float(yaw), "clip": clip, "clip_time": float(position), "socket_height": float(height)}

    async def observe(run, clip, weapon_mesh, crouched=False, *, blend_space=None):
        def projected(state):
            return next((row for row in (projections(state) or {}).values()
                         if not row.get("diagnostic") and row.get("pose_clip") == clip
                         and row.get("animation_mode") == 1 and any(
                             mesh.endswith("." + weapon_mesh) for mesh in row.get("weapon_meshes", ()))), None)

        projection = projected(await wait(run, projected, timeout=8))
        async with asyncio.timeout(3):
            while True:
                observed = await stance(run, "remotestance")
                if (observed["clip"] == (blend_space or clip) and observed["grounded"] and observed["crouched"] == crouched
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
    guest_mesh = "BoltShotgun_Frame_Suppressed" if shotgun else "BreakAction_BMG_Frame"
    for role, runner, observer, target, idle, moving, lowered, blend, mesh in (
        ("host", host_run, guest_run, host, "Rifle_Aiming_Low_Close1", "Walk_Forward", "HandGun_Idle_2",
         "HandGun_Lowered_BlendSpace", "9mmAuto_Frame"),
        ("guest", guest_run, host_run, guest, "Rifle_Aiming_Idle", "Rifle_Walk", "Rifle_Idle_3",
         "Rifle_Lowered_BlendSpace", guest_mesh),
    ):
        await avatar_probe(host_run, "resetlane")
        await observe(observer, lowered, mesh, blend_space=blend)
        await avatar_probe(runner, "aim")
        try:
            aimed = await observe(observer, idle, mesh)
            if role == "guest":
                assert 130 <= aimed["rig"]["socket_height"] <= 143, aimed
            if shotgun and role == "guest":
                state = await wait(runner, lambda row: row.get("game", {}).get("native_avatar_local_aim") is True)
                assert not state["game"].get("native_guest_primary_scope"), state["game"]["native_guest_primary_scope"]
                projected, stock, owner_only = map(int, await avatar_probe(observer, "weaponcopies"))
                assert (projected, stock, owner_only) == (1, 0, 1), (projected, stock, owner_only)
                evidence["shotgun_visible_copies"] = {"projected": projected, "stock_duplicate": stock}
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
            "crouched_walking": crouched_walking, "released": await observe(observer, lowered, mesh, blend_space=blend)}
    return evidence


async def capture(work: Path, vanilla_saved: Path, lifecycle: bool, equipment: bool = False,
                  melee: bool = False, pose: bool = False, rifle: bool = False,
                  consumable: bool = False, movement: bool = False, movement_traits: bool = False,
                  aim_movement: bool = False, stock_shotgun_aim: bool = False, flashlight_source: bool = False,
                  flashlight_view: bool = False, guest_flashlight_source: bool = False,
                  guest_flashlight_view: bool = False, guest_heavy_flashlight_source: bool = False,
                  heavy_flashlight_view: bool = False, offhand_flashlight_source: bool = False,
                  offhand_flashlight_view: bool = False,
                  guest_offhand_flashlight_view: bool = False) -> None:
    profile = "avatar-" + uuid.uuid4().hex[:8]
    host_dir = work / "campaigns" / profile
    guest_dir = work / "clients" / profile
    host_run, guest_run = host_dir / "run", guest_dir / "run"
    host_run.mkdir(parents=True)
    env = dict(os.environ, SILVERFISH_COOP_RENDER_CAPTURE="host" if guest_offhand_flashlight_view else "guest")
    if flashlight_source or guest_flashlight_source or guest_offhand_flashlight_view:
        env["SILVERFISH_COOP_FLASHLIGHT_PROBE"] = "1"
    if guest_offhand_flashlight_view:
        env["SILVERFISH_COOP_GUEST_OFFHAND_PROBE"] = "1"
    if offhand_flashlight_source:
        env["SILVERFISH_COOP_OFFHAND_FLASHLIGHT_PROBE"] = "1"
    if offhand_flashlight_view:
        env["SILVERFISH_COOP_OFFHAND_FLASHLIGHT_VIEW_PROBE"] = "1"
    if guest_flashlight_source:
        env["SILVERFISH_COOP_GUEST_FLASHLIGHT_PROBE"] = "1"
    if guest_heavy_flashlight_source or heavy_flashlight_view:
        env["SILVERFISH_COOP_HEAVY_FLASHLIGHT_PROBE"] = "1"
        if guest_flashlight_view or heavy_flashlight_view:
            env["SILVERFISH_COOP_HEAVY_FLASHLIGHT_VIEW_PROBE"] = "1"
    else:
        env.pop("SILVERFISH_COOP_HEAVY_FLASHLIGHT_PROBE", None)
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
    if stock_shotgun_aim:
        env["SILVERFISH_COOP_STOCK_SHOTGUN_PROBE"] = "1"
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
        if equipment or rifle or aim_movement or guest_offhand_flashlight_view:
            join += ("--native-guest-primary-input",)
        if rifle:
            join += ("--native-guest-reload-input",)
        guest_process = await start("guest.txt", guest, *join)
        evidence = {}
        for role, run in (("host", host_run), ("guest", guest_run)):
            state = await wait(run, projections)
            evidence[role] = projections(state)
            assert all(row["state"] == "default_body" for row in evidence[role].values()), evidence
        if flashlight_source or guest_flashlight_source or guest_offhand_flashlight_view:
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            if guest_offhand_flashlight_view:
                evidence["guest_offhand_flashlight_observation"] = await observe_guest_offhand_flashlight(host_run, guest_run)
            elif offhand_flashlight_source:
                evidence["stock_offhand_flashlight_observation"] = await observe_stock_offhand_flashlight(
                    host_run, guest_run, verify_view=offhand_flashlight_view)
            elif guest_flashlight_source:
                receipt = await observe_guest_stock_flashlight(host_run, guest_run, verify_view=guest_flashlight_view,
                                                              heavy=guest_heavy_flashlight_source)
                if guest_flashlight_view:
                    await command("action", *guest, "stop")
                    await wait(host_run, lambda state: "native_guest_flashlight_view" not in state.get("game", {}), timeout=8)
                    receipt["observer_cleared_after_guest_left"] = True
                evidence["guest_stock_flashlight_source_observation"] = receipt
            else:
                evidence["stock_flashlight_source_observation"] = await observe_stock_flashlight(
                    host_run, guest_run, verify_view=flashlight_view, heavy=heavy_flashlight_view)
            (host_run / "live-avatar-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            return
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
            expected_meshes = ({"BoltShotgun_Frame_Suppressed"} if stock_shotgun_aim
                               else {"BreakAction_BMG_Frame", "BreakAction_BMG_Swing"})
            mounted = await wait(host_run, lambda state: any(
                expected_meshes <= {path.rsplit(".", 1)[-1]
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
                    {"host": "Running_Unarmed", "guest": "Rifle_Run"})
            if aim_movement:
                evidence["stock_moving_aim"] = await verify_aim_movement(
                    host_run, guest_run, command, host, guest, projections, stock_shotgun_aim)
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
                if (peer and peer.get("pose_clip") == "Rifle_Aiming_Low_Close1" and peer.get("animation_mode") == 1
                        and (peer.get("weapon_socket_height") or 0) > 0):
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
                       and peer.get("pose_clip") == "HandGun_Idle_2"
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
                       and peer.get("pose_clip") == "HandGun_Idle_2"
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
    parser.add_argument("--stock-shotgun-aim", action="store_true",
                        help="exercise the suppressed shotgun's native ADS without a primary-fire scope")
    light = parser.add_mutually_exclusive_group()
    light.add_argument("--flashlight-source", action="store_true",
                       help="observe a stock flashlight switch, beam source, and remote visibility in a private pair")
    light.add_argument("--flashlight-view", action="store_true", help="also require the observer's flashlight beam and switch receipt")
    light.add_argument("--heavy-flashlight-view", action="store_true",
                       help="verify a stock heavy flashlight held by the host and measured by the observing guest")
    light.add_argument("--offhand-flashlight-source", action="store_true",
                       help="measure the host's stock offhand torch and the observing guest's primary gear")
    light.add_argument("--offhand-flashlight-view", action="store_true",
                       help="verify host offhand torch placement, aim and cleanup on the guest")
    light.add_argument("--guest-offhand-flashlight-view", action="store_true",
                       help="exercise the guest's stock C hold and verify the offhand view on the host")
    light.add_argument("--guest-flashlight-source", action="store_true",
                       help="compare an equipped guest's stock beam with its host authority pawn")
    light.add_argument("--guest-flashlight-view", action="store_true",
                       help="also verify host projection of the guest's beam and switch receipts")
    light.add_argument("--guest-heavy-flashlight-source", action="store_true",
                       help="measure the stock heavy flashlight beam relative to the guest's actual torch mesh")
    light.add_argument("--guest-heavy-flashlight-view", action="store_true",
                       help="also measure the host's projected heavy beam on the corresponding stock avatar mesh")
    options = parser.parse_args()
    options.offhand_flashlight_source |= options.offhand_flashlight_view
    options.flashlight_view |= options.heavy_flashlight_view
    options.flashlight_source |= options.flashlight_view or options.offhand_flashlight_source
    options.guest_heavy_flashlight_source |= options.guest_heavy_flashlight_view
    options.guest_flashlight_view |= options.guest_heavy_flashlight_view
    options.guest_flashlight_source |= options.guest_flashlight_view or options.guest_heavy_flashlight_source
    use_flashlight_fixture = options.flashlight_source or options.guest_flashlight_source or options.guest_offhand_flashlight_view
    if use_flashlight_fixture and any((options.lifecycle, options.equipment, options.melee, options.pose, options.rifle,
                                         options.consumable, options.movement, options.movement_traits, options.aim_movement,
                                         options.stock_shotgun_aim)):
        parser.error("Use --flashlight-source without other avatar fixtures")
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
    if options.stock_shotgun_aim and (not options.aim_movement or options.rifle):
        parser.error("--stock-shotgun-aim requires --aim-movement and replaces the break-action rifle")
    work = options.work.resolve(strict=True)
    original = options.vanilla_saved.resolve(strict=True)
    if use_flashlight_fixture and (any(part.casefold() == "steamapps" for part in work.parts)
                                     or work.is_relative_to(original) or original.is_relative_to(work)):
        parser.error("Use a private flashlight replica separate from the comparison saves")
    fixture = "flashlight_source_probe.lua" if use_flashlight_fixture else "avatar_presentation_probe.lua"
    with installed_probe(work, fixture) if any(
        (options.equipment, options.pose, options.rifle, options.consumable, options.movement, options.aim_movement)
    ) or use_flashlight_fixture else nullcontext():
        asyncio.run(capture(work, original,
                            options.lifecycle, options.equipment, options.melee, options.pose,
                            options.rifle, options.consumable, options.movement, options.movement_traits,
                            options.aim_movement, options.stock_shotgun_aim, options.flashlight_source, options.flashlight_view,
                            options.guest_flashlight_source, options.guest_flashlight_view,
                            options.guest_heavy_flashlight_source, options.heavy_flashlight_view,
                            options.offhand_flashlight_source, options.offhand_flashlight_view,
                            options.guest_offhand_flashlight_view))
