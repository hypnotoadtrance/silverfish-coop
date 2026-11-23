"""Cold-start two private replicas with the drag-and-drop co-op release."""

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install_stage
from tools.silverfish import (
    direct_ip, embedded_startup, native_finite_shop, native_guest_armor, native_guest_consumable, native_guest_drop,
    native_host_save, native_inventory, native_loose_pickup,
    native_lantern, native_loot, native_prop_carry, native_stash, native_world_loot, runtime,
)
from tools.silverfish.authored_loot import GUARANTEED_CRATES
from tools.silverfish.control import NAME, submit
from tools.silverfish.service import PROTOCOL, menu_host_checkpoint
from tools.silverfish.steam_cloud import FOLDER
from tools.silverfish.win_job import GameProcess
from live_installed_title_smoke import boot_states, native_mailbox
from live_loose_pickup_smoke import DEFAULT_ACTOR
from live_ai_smoke import wait
from live_cold_cabinet_pickup_smoke import copy_resident_checkpoint, instrument, verify_resident_pickup, verify_saved_pickup
from live_multi_smoke import campaign_save_hashes, saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_stock_creator_smoke import creator_action, installed_probe
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, offered, start_pair
from portable_guest_world_start import GuestWorldStartProof, resident_events, wait_resident_status
from portable_world_view_receipt import patch_world_view_receipts, verify_world_view_receipt
from tools.silverfish.title_session import STATUS_FILE as TITLE_STATUS_FILE


CABINET_RELEASE_SHA256 = "e35f410f7231f2c510b4ec43c34fb59ff7e9574187e2cd32fe66a61b3e87c14c"


@dataclass(frozen=True)
class PortableRelease:
    package: Path
    manifest_sha256: str
    title_directory: Path
    selection: dict
    activation: dict[Path, str | None]


def portable_main_overlay(game: Path, patch_script):
    return portable_script_overlay(game, "main.lua", patch_script)


@contextmanager
def portable_script_overlay(game: Path, script_name: str, patch_script):
    """Qualify and restore an isolated Lua overlay on a versioned portable replica."""
    if any(part.casefold() == "steamapps" for part in game.parts):
        raise ValueError("Use only a private portable replica for the test overlay")
    script = (game / runtime.SHIPPING).parent / runtime.MOD_SCRIPTS / script_name
    manifest = game / runtime.PORTABLE_STAGE_MANIFEST
    for file in (script, manifest):
        install_stage._ordinary(file)
    original_script, original_manifest = script.read_bytes(), manifest.read_bytes()
    inventory = json.loads(original_manifest)
    normalized_before = sha256(original_script.replace(b"\r\n", b"\n")).hexdigest()
    if inventory.get("lua_files", {}).get(script_name) != normalized_before:
        raise ValueError(f"The portable replica must start with its exact released {script_name}")

    newline = "\r\n" if b"\r\n" in original_script else "\n"
    original = original_script.decode("utf-8").replace("\r\n", "\n")
    patched = patch_script(original).replace("\n", newline)
    normalized_after = sha256(patched.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    inventory["lua_files"][script_name] = normalized_after
    patched_manifest = json.dumps(inventory, ensure_ascii=True, allow_nan=False) + "\n"
    provenance = {
        "script_source_before": normalized_before,
        "script_source_overlay": normalized_after,
        "manifest_before_sha256": sha256(original_manifest).hexdigest(),
        "manifest_overlay_sha256": sha256(patched_manifest.encode("utf-8")).hexdigest(),
    }
    def restore(path, original, overlay):
        install_stage._ordinary(path)
        current = path.read_bytes()
        if current not in (original, overlay.encode("utf-8")):
            raise RuntimeError(f"The private portable overlay changed unexpectedly at {path}")
        if current != original:
            runtime.write_text(path, original.decode("utf-8"), newline="")

    try:
        # Atomic replacement avoids editing a shared hard link in place.
        runtime.write_text(script, patched, newline="")
        runtime.write_text(manifest, patched_manifest, newline="")
        yield provenance
    finally:
        try:
            restore(script, original_script, patched)
        finally:
            restore(manifest, original_manifest, patched_manifest)
        provenance["original_bytes_restored"] = script.read_bytes() == original_script and manifest.read_bytes() == original_manifest
        assert provenance["original_bytes_restored"], "The private portable overlay failed to restore its release files"


def shop_lease_overlay(game: Path):
    def allow_isolated_approach(text):
        gate = 'local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        if text.count(gate) != 1:
            raise ValueError("The replica lacks the known stock merchant approach gate")
        return text.replace(gate, gate + '\n    or (os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
                            + '\n        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")', 1)

    return portable_script_overlay(game, "native_finite_shop.lua", allow_isolated_approach)


def stash_memory_overlays(probes: ExitStack, game: Path, *, large: bool = False) -> dict:
    def replace(text, original, updated):
        if text.count(original) != 1:
            raise ValueError("The replica lacks the exact released stash fixture")
        return text.replace(original, updated, 1)

    def approach(text):
        original = '            or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then'
        text = replace(text, original,
            '            or (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"\n'
            '                and (fields[2] ~= "native-stash-approach-fixture"\n'
            '                    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1")) then')
        if not large:
            return text
        seed = (Path(__file__).with_name("fixtures") / "portable_large_stash_authority.lua").read_text(encoding="utf-8")
        command = "function stash.command(fields, state, phase)"
        return replace(text, command, seed + "\n" + command + """
    if fields[2] == "portable-large-stash-seed" or fields[2] == "portable-large-stash-clear" then
        return portable_large_stash(fields, state, phase)
    end""")

    def stock_view(text):
        gate = 'local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"'
        text = replace(text, gate,
            'local fixtures = headless and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"')
        start = """                local consumed, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                dispatch = status
                if consumed ~= true or status ~= "stock_started_consumed" then break end"""
        stock = """                local input = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
                local action = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact")
                if not valid(input) or not valid(action) or pawn.Holding_Interact ~= false then break end
                local started = input:MakeInputActionValueOfType(1, 0, 0, 0)
                local completed = input:MakeInputActionValueOfType(0, 0, 0, 0)
                pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(started, 0, 0, action)
                pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(completed, .05, .05, action)
                dispatch = "stock Interact Started and Completed"
"""
        text = replace(text, start, stock.removesuffix("\n"))
        text = replace(text, '    current.state = "rendered"',
                       '    current.state, current.portable_memory_rendered = "rendered", resident'
                       + ('\n    current.portable_memory_bytes = resident and #raw or 0' if large else ''))
        dispatch = '    local verb = fields[2]'
        text = replace(text, dispatch, dispatch + """
    if verb == "portable-stash-memory-proof" and fixtures and #fields == 4 then
        local binding = report(state, phase)
        if not binding or not current or current.state ~= "rendered" or not current.portable_memory_rendered
            or tostring(current.sequence) ~= fields[3] or current.id ~= fields[4]
            or not same(binding.pawn, current.binding.pawn) or not visible(binding.controller, current.screen, true) then
            error("portable stash memory readback is unavailable", 0)
        end
        local cards = current.screen.StashPanel.Cards
        local count = cards:GetArrayNum()
        local first = count > 0 and entry(cards, 1)
        local item = valid(first) and first.Item
        return string.format("portable stock stash memory %s %d %s %d", fields[3], count,
            item and identifier(item[item_guid]) or "-", item and item[item_ammo] or 0)
    end""")
        if not large:
            return text
        text = replace(text, '    if verb == "portable-stash-memory-proof" and fixtures and #fields == 4 then',
            '    if verb == "portable-stash-memory-proof" and fixtures and (#fields == 4 or #fields == 5 and fields[5] == "large") then')
        return replace(text, "        local first = count > 0 and entry(cards, 1)", """
        if fields[5] == "large" then
            assert(view.memory_body_limit() == 2500000 and current.portable_memory_bytes > 32768 and count == 384,
                "portable large stash render did not cross the native ticket boundary")
            local guids = {}
            for index = 1, count do
                local card = entry(cards, index)
                local item = valid(card) and card.Item
                local class = item and item[item_class]
                local guid = item and identifier(item[item_guid])
                assert(guid and valid(class)
                    and class:GetFullName() == "BlueprintGeneratedClass /Game/Classes/Items/Item_Bandage.Item_Bandage_C"
                    and item[item_ammo] == 1, "portable large stash stock card is invalid")
                guids[index] = guid
            end
            return ("portable large stash memory %s %d %d %s"):format(
                fields[3], count, current.portable_memory_bytes, table.concat(guids, ","))
        end
        local first = count > 0 and entry(cards, 1)""".lstrip("\n"))

    return {name: probes.enter_context(portable_script_overlay(game, name, patch_script)) for name, patch_script in (
        ("native_stash.lua", approach), ("native_stash_view.lua", stock_view))}


def compact_crate_status_overlay(game: Path):
    """Exercise installed memory publication without disabling any portable isolation flags."""
    def patch_script(text):
        original_gate = ("    local compact_crates = resident_title and status_publication.installed\n"
                         "        and not status_publication.mirror and status_publication.memory_live")
        original_mirror = "if status_publication.memory_live and not status_publication.mirror and not file_command then return end"
        if text.count(original_gate) != 1 or text.count(original_mirror) != 1:
            raise ValueError("The replica lacks the exact installed crate diagnostic gate")
        # Keep the real HEADLESS environment and the full title/exit mirror.
        override = ("    local mirror_crate_status = status_publication.mirror\n"
                    '        and current_phase ~= "hosting" and current_phase ~= "connected"')
        text = text.replace(original_gate, override + "\n" + original_gate.replace("status_publication.mirror", "mirror_crate_status"))
        return text.replace(original_mirror, original_mirror.replace("status_publication.mirror", "mirror_crate_status"))

    return portable_main_overlay(game, patch_script)


def nonmirrored_startup_overlay(game: Path):
    def patch_script(text):
        gate = "if status_publication.memory_live and not status_publication.mirror and not file_command then return end"
        if text.count(gate) != 1:
            raise ValueError("The replica lacks the exact installed status publication guard")
        guard = ("if status_publication.memory_live and not file_command\n"
                 '        and (not status_publication.mirror or current_phase == "hosting" or current_phase == "connected")'
                 " then return end")
        return text.replace(gate, guard)

    return portable_main_overlay(game, patch_script)


async def verify_nonmirrored_startup(host, guest, proof):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    roles = {host_run: (host, "host", "hosting"), guest_run: (guest, "join", "connected")}

    async def prove_ready(run, seat, action, phase):
        file = seat.bridge.snapshot()
        assert file, "No original filesystem title receipt"

        def memory_only(frame):
            live = frame.get("game", {})
            return (frame.get("local_bridge") == frame.get("local_status_source") == "memory"
                    and frame.get("state") == phase and live.get("state") == phase
                    and live.get("sequence", 0) > file["sequence"])

        live = await wait_resident_status(run, memory_only)
        assert (file["state"], file["map"]) != (phase, live["game"]["map"]), (
            "The filesystem receipt is already eligible for gameplay readiness", file["state"], file["map"])
        status = seat.bridge.directory / TITLE_STATUS_FILE
        title = {}
        async with asyncio.timeout(8):
            while title.get("state") != "ready" or title.get("attempt", {}).get("action") != action:
                try:
                    title = runtime.read_json(status)
                except (FileNotFoundError, PermissionError):
                    pass
                if title.get("state") in {"ready", "failed", "cleanup_unverified"}:
                    break
                await asyncio.sleep(.1)
        assert (title["state"], title.get("progress"), title.get("attempt", {}).get("action")) == ("ready", phase, action), title
        assert seat.bridge.snapshot() == file, "A file command refreshed the title frame before readiness"
        return {"title_attempt": title["attempt"], "title_progress": title["progress"],
                "file_sequence": file["sequence"], "file_phase": file["state"],
                "memory_sequence": live["game"]["sequence"], "memory_generation": live["generation"],
                "game_save_scope": live["game"]["save_scope"]}

    async def wait_with_initial_readiness(run, predicate):
        frame = await wait_resident_status(run, predicate)
        seat, action, phase = roles[run]
        if frame.get("state") == phase and action not in proof:
            proof[action] = await prove_ready(run, seat, action, phase)
        return frame

    async def stock_character(guest_seat, join, name):
        assert proof.get("host")
        await create_stock_bag_character(guest_seat, join, name)
        creator_host = await prove_ready(host_run, host, "host", "hosting")
        assert creator_host["title_attempt"] == proof["host"]["title_attempt"]
        assert creator_host["memory_generation"] == proof["host"]["memory_generation"]
        assert creator_host["file_sequence"] == proof["host"]["file_sequence"]
        proof["host_stayed_ready_during_guest_stock_creation"] = True

    async def pause_from_memory(run):
        return await wait_resident_status(run, lambda frame: frame.get("game", {}).get("stock_pause_exit", {}).get("state")
                                          == "armed")

    return await start_pair(host, guest, "Unmirrored title scout", wait_status=wait_with_initial_readiness,
                            create_character=stock_character, wait_for_host_pause=pause_from_memory)


def launch_portable(game: Path, profile: Path) -> GameProcess:
    image = game / runtime.SHIPPING
    install_stage._ordinary(image)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("SILVERFISH_COOP_")}
    arguments = subprocess.list2cmdline([str(image)])
    arguments += (f' -SilverfishCoopIsolated -userdir="{profile}" -unattended -NoSplash -windowed'
                  " -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
                  " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                  "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=image, cwd=image.parent, env=env, startupinfo=info)


def fingerprint(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    install_stage._ordinary(path)
    return runtime._file_sha256(path)


def qualify_release(game: Path, legacy_work: Path | None) -> PortableRelease:
    for name in runtime.STOCK_PACKAGES:
        package = game / runtime.PAKS / name
        if not package.is_file() or not install_stage._ordinary(package).st_size:
            raise ValueError(f"Stock content is missing from the portable test replica: {package}")
    mods = game / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    embedded = mods / install_stage.MOD
    marker = embedded / "resident-portable.json"
    descriptor = embedded / "resident-install.json"
    stage = game / runtime.PORTABLE_STAGE_MANIFEST
    activation = {
        mods / "mods.txt": fingerprint(mods / "mods.txt"),
        descriptor: fingerprint(descriptor),
        marker: fingerprint(marker),
        stage: fingerprint(stage),
    }
    if activation[marker] is None or activation[stage] is None:
        raise ValueError("Apply the release ZIP over the stock replica before running this test")
    for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        enabled = mods / module / "enabled.txt"
        install_stage._ordinary(enabled)
        activation[enabled] = fingerprint(enabled)
    metadata = runtime.read_json(marker, limit=8192)
    if metadata["schema"] != 1 or runtime._file_sha256(game / runtime.SHIPPING) != metadata["shipping_sha256"]:
        raise ValueError("The portable release does not match the shipping executable")

    addon = embedded / "dlls" / "main.dll"
    install_stage._ordinary(addon)
    image = addon.read_bytes()
    pinned = []
    for candidate in embedded.glob("runtime-*"):
        if re.fullmatch(r"runtime-[0-9a-f]{16}", candidate.name) and candidate.is_dir():
            digest = fingerprint(candidate / "runtime-manifest.json")
            if digest is not None and digest.encode("ascii") in image:
                manifest, _, _ = install_stage._manifest(candidate, addon)
                if candidate.name == "runtime-" + manifest[:16]:
                    pinned.append((candidate, manifest))
    if len(pinned) != 1:
        raise ValueError("The DLL must pin exactly one qualified versioned embedded runtime")
    package, digest = pinned[0]

    boot = {"schema": 1, "campaign": "default", "bind": "127.0.0.1", "port": 7777}
    seat_name = "local"
    if legacy_work is None:
        if activation[descriptor] is not None:
            raise ValueError("A clean portable replica must have no resident-install.json")
    else:
        if activation[descriptor] is None:
            raise ValueError("The upgrade replica needs its original isolated resident-install.json")
        legacy = runtime.read_json(descriptor, limit=8192)
        if (legacy.get("schema"), legacy.get("isolated"), legacy.get("shipping_sha256")) != (
                1, True, metadata["shipping_sha256"]
        ) or Path(legacy["work"]) != legacy_work or not NAME.fullmatch(legacy["seat"]):
            raise ValueError("The original upgrade descriptor must point at the supplied private isolated workspace")
        seat_name = legacy["seat"]
        boot.update({key: legacy[key] for key in ("campaign", "bind", "port")})
    return PortableRelease(package, digest, Path("titles") / seat_name, boot, activation)


def cabinet_provenance(archive: Path, games: tuple[Path, Path], releases: tuple[PortableRelease, PortableRelease],
                       candidate_sha256: str | None) -> dict:
    expected = CABINET_RELEASE_SHA256 if candidate_sha256 is None else candidate_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or runtime._file_sha256(archive) != expected:
        raise ValueError("The cabinet archive differs from its expected SHA-256 digest")
    if candidate_sha256 == CABINET_RELEASE_SHA256:
        raise ValueError("The published cabinet baseline cannot stand in for the joint presentation candidate")
    with ZipFile(archive) as bundle:
        for game, release in zip(games, releases, strict=True):
            # qualify_release has already verified all installed files pinned by this manifest.
            manifest = release.package / install_stage.MANIFEST
            embedded = release.package.parent
            gameplay = game / runtime.SHIPPING.parent / runtime.CPP_ADDON_DLL
            for path in (manifest, embedded / "resident-portable.json", embedded / "dlls/main.dll", gameplay):
                data = bundle.read(path.relative_to(game).as_posix())
                if sha256(data).hexdigest() != fingerprint(path):
                    raise ValueError(f"The portable cabinet replica differs from the pinned archive: {path}")
            revision = json.loads(manifest.read_bytes()).get("app_revision", "")
            if not re.fullmatch(r"[0-9a-f]{40}", revision) or (not candidate_sha256 and not revision.startswith("2abe7b2")):
                raise ValueError("The cabinet release does not identify its packed source revision")
        if candidate_sha256:
            projection = runtime.SHIPPING.parent / runtime.MOD_SCRIPTS / "native_guest_drop_projection.lua"
            if b"native-world-loot-present-host-claim" not in bundle.read(projection.as_posix()):
                raise ValueError("The pinned candidate does not include the joint pickup command")
    return {"release_source": revision[:7], "release_revision": revision, "release_archive_sha256": expected,
            "native_sha256": fingerprint(gameplay), "runtime_manifest_sha256": release.manifest_sha256,
            "cabinet_presentation": "combined" if candidate_sha256 else "separate"}


async def discover_mailbox(root: Path, process: GameProcess) -> Path:
    async with asyncio.timeout(30):
        while process.poll() is None and not root.is_dir():
            await asyncio.sleep(.1)
        if process.poll() is not None:
            raise RuntimeError(f"The shipping executable exited before creating its portable mailbox ({process.returncode})")
        return await native_mailbox(root, process)


async def portable_probe_command(bridge, prefix, request, *, reply_end=None):
    mailbox = bridge.directory
    ticket = uuid.uuid4().hex
    runtime.write_text(mailbox / f"{prefix}-command.tsv", f"{ticket}\t{request}")
    reply = mailbox / f"{prefix}-reply.tsv"
    async with asyncio.timeout(8):
        while bridge.process.poll() is None:
            try:
                fields = reply.read_text(encoding="utf-8").split("\t", 2)
            except (FileNotFoundError, PermissionError):
                fields = ()
            if len(fields) == 3 and fields[0] == ticket:
                assert fields[1] == "ok", fields[2]
                if reply_end is None or fields[2].endswith(reply_end):
                    return fields[2]
            await asyncio.sleep(.05)
    raise RuntimeError(f"The {prefix} process exited before replying")


def weapon_cue_files(host, guest):
    return [str(path) for seat in (host, guest) for name in ("native-weapon-shots", "native-weapon-shots.tmp")
            if os.path.lexists(path := seat.bridge.directory / name)]


async def verify_resident_footsteps(host: Seat, guest: Seat) -> list[dict]:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-footstep", action)

    for seat in (host, guest):
        assert await command(seat, "arm") == "armed"
        async with asyncio.timeout(5):
            while await command(seat, "ready") != "true":
                await asyncio.sleep(.05)

    receipts = []
    for direction, sender, observer in (("host_to_guest", host, guest), ("guest_to_host", guest, host)):
        for seat in (sender, observer):
            assert await command(seat, "clear") == "clear"
        original = await command(sender, "step")
        assert original.startswith("SoundCue /Game/Assets/Sounds/CUEs/") and "|" not in original, original
        async with asyncio.timeout(5):
            while True:
                local, remote = (await command(observer, "read")).split(";")
                if remote:
                    break
                await asyncio.sleep(.05)
        await asyncio.sleep(.2)
        assert await command(sender, "read") == f"{original};", "footstep echoed to sender"
        local, remote = (await command(observer, "read")).split(";")
        assert not local and "|" not in remote, (local, remote)
        cue, gain, pitch, offset, distance = remote.split(",")
        assert ",".join((cue, gain, pitch)) == original, (original, remote)
        assert float(offset) <= 2 and 0 < float(distance) <= 3000, remote
        receipts.append({"direction": direction, "cue": cue, "gain": float(gain), "pitch": float(pitch),
                         "offset_from_emitter_feet_cm": float(offset), "listener_distance_cm": float(distance),
                         "observer_replays": 1, "sender_echoes": 0})
    return receipts


async def verify_resident_pistol(host: Seat, guest: Seat) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-dry-fire", action)

    async def wait_for(seat, action, expected):
        async with asyncio.timeout(8):
            while await command(seat, action) != expected:
                await asyncio.sleep(.05)

    async def read_single_click(seat, source):
        async with asyncio.timeout(6):
            while True:
                projectiles, clicks = (await command(seat, "read")).split(";")
                assert projectiles == "0", (source, projectiles)
                if clicks:
                    break
                await asyncio.sleep(.05)
        await asyncio.sleep(.12)
        assert await command(seat, "read") == f"0;{clicks}", "trigger click repeated or spawned a projectile"
        kind, *position = clicks.split(",")
        assert kind == source and len(position) == 3, clicks
        return [float(axis) for axis in position]

    async def input_result(event, sequence):
        async with asyncio.timeout(6):
            while True:
                for entry in await resident_events(guest_run, guest_frame):
                    if entry.get("sequence") == sequence and entry["event"] in (event, event + "_rejected"):
                        assert entry["event"] == event, entry
                        return entry["native_guest_weapon"]
                await asyncio.sleep(.08)

    for seat in (host, guest):
        assert await command(seat, "arm") == "armed"
    host_guid = await command(host, "seed-local")
    guest_guid = await command(host, "seed-peer")
    assert all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in (host_guid, guest_guid)) and host_guid != guest_guid
    await wait_for(guest, "projected", guest_guid)
    for seat, guid, rounds in ((host, host_guid, 0), (guest, guest_guid, 1)):
        assert await command(seat, "equip") == "selected"
        await wait_for(seat, "ready", f"{guid},{rounds},{rounds}")
    await wait_for(host, "peer-ready", f"{guest_guid},1,1")

    guest_run = guest.client / "run"
    guest_frame = await wait_resident_status(guest_run, lambda frame: (scope := frame.get("game", {}).get("native_guest_primary_scope", {}))
                                            and (scope.get("guid"), scope.get("kind")) == (guest_guid, "pistol"), timeout=12)
    tracked = {"native_weapon_shot_played", "native_weapon_dry_fire_played"}
    before = [entry for entry in await resident_events(guest_run, guest_frame) if entry["event"] in tracked]
    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    assert await command(host, "host-press") == "pressed"
    host_click, guest_heard = await asyncio.gather(read_single_click(host, "stock-weapon"), read_single_click(guest, "observer"))
    assert math.dist(host_click, guest_heard) <= .03, (host_click, guest_heard)

    assert await command(guest, "face-away") == "turned"
    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    loaded_press = await submit(guest_run, "native-guest-primary-input-fixture")
    assert loaded_press["ok"], loaded_press
    shot = await input_result("native_guest_primary_input", loaded_press["native_guest_primary_fixture"]["sequence"])
    assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (guest_guid, 1, 0, 1), shot
    await wait_for(host, "peer-ready", f"{guest_guid},0,0")
    await wait_for(guest, "ready", f"{guest_guid},0,0")
    host_shot = tuple(map(int, (await command(host, "shot-read")).split(",")))
    assert host_shot == (1, 1, 1, 0), host_shot
    after_round = [entry for entry in await resident_events(guest_run, guest_frame) if entry["event"] in tracked]

    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    press = await submit(guest_run, "native-guest-primary-input-fixture")
    assert press["ok"] and press["native_guest_primary_fixture"]["sequence"] > 0, press
    host_heard, guest_click = await asyncio.gather(read_single_click(host, "observer"), read_single_click(guest, "observer"))
    assert math.dist(host_heard, guest_click) <= .03, (host_heard, guest_click)
    assert await command(host, "ready") == f"{host_guid},0,0" and await command(guest, "ready") == f"{guest_guid},0,0"
    assert await command(host, "peer-ready") == f"{guest_guid},0,0"

    since = await resident_events(guest_run, guest_frame)
    host_dry_events = [entry for entry in after_round if entry["event"] == "native_weapon_dry_fire_played" and entry not in before]
    guest_dry_events = [entry for entry in since if entry["event"] in tracked and entry not in after_round]
    assert len(host_dry_events) == 1 and [entry["event"] for entry in guest_dry_events] == ["native_weapon_dry_fire_played"], since
    events = host_dry_events + guest_dry_events
    assert len({entry["serial"] for entry in events}) == 2, events
    guest_input, = [entry for entry in since if entry["event"] == "native_guest_primary_input_rejected"
                    and entry.get("sequence") == press["native_guest_primary_fixture"]["sequence"]]
    assert guest_input["reason"] == "guest_weapon_preloaded_magazine_required", guest_input

    reserve = await command(host, "seed-reserve")
    assert re.fullmatch(r"[0-9A-F]{32},9", reserve), reserve
    await wait_for(guest, "reserve", reserve)
    reloaded = await submit(guest_run, "native-guest-reload-input-fixture")
    assert reloaded["ok"] and reloaded["native_guest_reload_fixture"]["stock_wrappers"] == 2, reloaded
    reload = await input_result("native_guest_reload_input", reloaded["native_guest_reload_fixture"]["sequence"])
    assert (reload["guid"], reload["before"], reload["after"], reload["reserve_before"], reload["reserve_after"]) == (
        guest_guid, 0, 9, 9, 0), reload
    await wait_for(host, "peer-ready", f"{guest_guid},9,9")
    await wait_for(guest, "ready", f"{guest_guid},9,9")
    await wait_for(guest, "reserve", "")
    assert await command(host, "peer-reserve") == "" and await command(host, "reserve") == ""
    assert await command(host, "ready") == f"{host_guid},0,0"
    return {"host_stock_click": host_click, "guest_heard_host": guest_heard, "guest_primary_input": press,
            "host_heard_guest": host_heard, "guest_click": guest_click, "guest_cue_receipts": events,
            "guest_live_round": shot, "host_guest_shot_dispatch": {"projectile_calls": host_shot[0],
                "shot_sounds": host_shot[1], "muzzle_flashes": host_shot[2]},
            "guest_empty_magazine_receipt": guest_input,
            "pistols_still_empty_after_dry_fire": True, "projectiles_spawned": 0, "observers_played_shot_effects": 0,
            "guest_reload_input": reloaded, "guest_reload": reload, "retired_reserve": reserve,
            "both_games_projected_reloaded_magazine": True, "host_magazine_and_reserve_unchanged": True}


async def verify_resident_weapon_cues(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_frame, guest_frame = await asyncio.gather(
        wait_resident_status(host_run, lambda frame: frame.get("local_bridge") == "memory"),
        wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory"
                             and any(avatar["state"] in {"stock_body", "default_body"} and avatar.get("player_id") is not None
                                     for avatar in frame.get("game", {}).get("native_player_avatars", {}).values())),
    )
    assert host_frame["game"]["native_guest_save"]["pawn"] == pawn
    item = native_guest_consumable.BANDAGE_CLASS
    event_names = ("native_avatar_use_sound_played", "native_avatar_use_visual_played")
    campaign = host.campaign / "save"
    cloud = host.profile / "Saved/SaveGames" / FOLDER

    def save_state():
        native = (path for seat in (host, guest) for path in (seat.profile / "Saved/SaveGames").rglob("*.sav")
                  if not path.is_relative_to(seat.profile / "Saved/SaveGames" / FOLDER))
        return {path: (fingerprint(path), path.stat().st_mtime_ns) for path in (*campaign.glob("campaign*.json"), *native)}

    def use_events(events):
        return [row for row in events if row["event"] in event_names and row["generation"] == guest_frame["generation"]
                and row["item_class"] == item]

    assert not weapon_cue_files(host, guest), "A weapon cue mailbox already exists"
    assert not use_events(await resident_events(guest_run, guest_frame)), "The observer already has a Bandage cue"
    before = save_state()
    assert campaign / "campaign.json" in before
    backup_before = {path: fingerprint(path) for path in cloud.glob("SFCoopBackup-*.sav")}
    checkpoint = json.loads((campaign / "campaign.json").read_bytes())
    result = await portable_probe_command(host.bridge, "avatar-probe", "consume\n")
    player_id, class_name = result.strip().split("\t")
    player_id = int(player_id)
    assert class_name == f"BlueprintGeneratedClass {item}", result

    def heard_use(frame):
        game = frame.get("game", {})
        for remote, avatar in game.get("native_player_avatars", {}).items():
            if avatar.get("player_id") == player_id:
                use = game.get("native_avatar_use", {}).get(remote, {})
                if use.get("item") == item and use.get("active") is False and use["hand_motion_cm"] > 20:
                    return use
        return None

    observed = await wait_resident_status(guest_run, heard_use, timeout=12)
    prop = heard_use(observed)
    assert prop["mesh"].endswith(".SM_Prop_Bandage_02"), prop
    cues = use_events(await resident_events(guest_run, observed))
    assert {entry["event"] for entry in cues} == set(event_names) and len(cues) == 2, cues
    assert {entry["serial"] for entry in cues} == {prop["serial"]}, (cues, prop)
    visual, = [entry for entry in cues if entry["event"] == event_names[1]]
    assert visual["player_id"] == player_id, visual
    footsteps = await verify_resident_footsteps(host, guest)
    pistol = await verify_resident_pistol(host, guest)
    assert not weapon_cue_files(host, guest), "Stock player cues wrote a legacy weapon cue mailbox"
    after = save_state()
    changed = [path for path in before.keys() | after.keys() if before.get(path) != after.get(path)]
    assert not changed, f"Campaign saves changed during stock player actions: {changed}"
    # An asynchronous Auto-Cloud copy may catch up with the preceding enrollment checkpoint.
    backup_revisions = []
    for path in cloud.glob("SFCoopBackup-*.sav"):
        if fingerprint(path) != backup_before.get(path):
            with ZipFile(path) as archive:
                backed_up = json.loads(archive.read("campaign.json"))
            assert backed_up == checkpoint, "The cloud mirror backed up a new checkpoint during stock player actions"
            backup_revisions.append(backed_up["revision"])
    return {"resident_weapon_cues": {"stock_player_id": player_id, "source": class_name, "observer_cues": cues,
                                     "prop": prop, "local_bridge_both_peers": "memory",
                                     "no_cue_mailbox": True, "saves_unchanged_before_checkpoint": True,
                                     "previous_checkpoint_cloud_copies": backup_revisions,
                                     "stock_footsteps": footsteps, "stock_pistol": pistol}}


async def verify_resident_guest_food(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    expected = native_guest_consumable.MEALS[native_guest_consumable.MRE]
    grouped = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    checkpoint_hashes = campaign_save_hashes(host.campaign)

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-guest-food", action)

    async def read_host():
        return tuple(map(float, (await command(host, "host-read")).split(",")))

    async def read_guest(minimum):
        async with asyncio.timeout(8):
            while True:
                food, regen, widget = (await command(guest, "guest-read")).split(",", 2)
                if float(food) > minimum:
                    # NullRHI may leave the stock label on the preceding HUD sample.
                    food, regen, widget = (await command(guest, "guest-tick")).split(",", 2)
                    match = re.search(r"\d+(?:[.,]\d+)?", widget)
                    assert match and abs((amount := float(match[0].replace(",", "."))) - float(food)) <= .2, (food, regen, widget)
                    return {"food": float(food), "regen": float(regen), "widget": widget, "displayed_food": amount}
                await asyncio.sleep(.08)

    seed_started = time.time()
    guid = await command(host, "seed")
    assert re.fullmatch(r"[0-9A-F]{32}", guid) and guid != "0" * 32, guid
    host_before = await read_host()
    assert 37 < host_before[2] <= 38 and host_before[3] == 230, host_before
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory")
    inv_before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    meal, = [row for row in inv_before["items"] if row["guid"] == guid and row["source"] == "backpack"]
    assert meal["class_path"] == expected.class_path and meal["quantity"] == 1, meal
    async with asyncio.timeout(8):
        while not any(entry["event"] == "native_guest_ui_projected" and entry["items"] == len(inv_before["items"])
                      and entry["time_unix"] >= seed_started
                      for entry in await resident_events(guest_run, guest_frame)):
            await asyncio.sleep(.08)
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    before = await read_guest(35)
    assert before["food"] <= 38 and before["regen"] == 230, before

    press = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=guid)
    assert press["ok"] and press["stock_action"]["guid"] == guid, press
    key = native_guest_consumable.RESULT[native_guest_consumable.MRE]
    async with asyncio.timeout(12):
        while True:
            rows = await resident_events(guest_run, guest_frame)
            refused = [entry for entry in rows if entry["event"] == "native_stock_inventory_rejected"]
            assert not refused, refused
            results = [entry[key] for entry in rows if entry["event"] == "native_stock_inventory_action"
                       and entry.get(key, {}).get("guid") == guid]
            if results:
                used, = results
                break
            await asyncio.sleep(.12)
    assert (used["pawn"], used["quantity_before"], used["quantity_after"]) == (pawn, 1, 0), used
    assert used["food_after"] > used["food_before"] + 20 and used["regen_after"] > used["regen_before"], used
    after = await read_guest(used["food_after"] - 1)
    host_after = await read_host()
    assert abs(after["food"] - host_after[2]) <= 1 and after["regen"] == host_after[3] == used["regen_after"], (after, host_after, used)
    assert 0 <= host_before[0] - host_after[0] < 2 and host_before[1] == host_after[1], (host_before, host_after)
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    assert guid not in {row["guid"] for row in inventory["items"]}, inventory
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == grouped
    assert campaign_save_hashes(host.campaign) == checkpoint_hashes, "A stock meal caused an early campaign checkpoint"
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"resident_guest_food": {"receipt": used, "before": before, "after": after,
                                    "host_food_before": host_before[0], "host_food_after": host_after[0],
                                    "host_regen_unchanged": True, "campaign_checkpoint_unchanged": True,
                                    "stock_widget_tick_invoked_in_muted_fixture": True}}


async def verify_resident_board_offers(host: Seat, guest: Seat, _pawn: str) -> dict:
    from tools.silverfish.native_flood_jammer import OBSERVATORY_BOUNTY_BOARD
    from tools.silverfish.native_quest import AUTHORED_BOARD_ACTIONS
    from tools.silverfish.native_wire_board import catalog_specialists
    from tools.silverfish.travel import OBSERVATORY

    async def command(seat, action, quest=None):
        request = f"{action}\t{quest}" if quest is not None else action
        return await portable_probe_command(seat.bridge, "portable-board-offer", request)

    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    target = tuple(map(float, (await command(host, "approach")).split(",")))
    await wait_resident_status(guest_run, lambda frame: (
        (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
        and math.dist(point, target) <= 25))
    catalog, extended = (await command(host, "catalog")).split(";")
    offered = set(filter(None, catalog.split(",")))
    advanced = set(filter(None, extended.split(","))) - AUTHORED_BOARD_ACTIONS
    other_boards = offered & (AUTHORED_BOARD_ACTIONS - catalog_specialists(OBSERVATORY, OBSERVATORY_BOUNTY_BOARD))
    previously_filtered = advanced | other_boards
    assert previously_filtered <= offered and previously_filtered, (offered, previously_filtered)
    quest = min(previously_filtered)

    opened = await submit(guest_run, "native-wire-board-stock-f-fixture", target=OBSERVATORY_BOUNTY_BOARD)
    assert opened["ok"], opened
    guest_frame = await wait_resident_status(guest_run, lambda frame: (
        (board := frame.get("game", {}).get("native_wire_board_stock", {}))
        and board.get("state") == "visible" and board.get("board") == OBSERVATORY_BOUNTY_BOARD))
    visible = set(filter(None, (await command(guest, "menu")).split(",")))
    assert visible == offered, (visible, offered)
    accepted = await submit(guest_run, "native-wire-board-stock-fixture", target=f"{quest}|start")
    assert accepted["ok"], accepted

    async with asyncio.timeout(8):
        while True:
            observed = await asyncio.gather(*(command(seat, "journal", quest) for seat in (host, guest)))
            if observed[0] == observed[1] and observed[0] != "-":
                break
            await asyncio.sleep(.1)
    entries = await resident_events(guest_run, guest_frame)
    assert any(entry.get("quest") == quest and entry.get("state") == "accepted" for entry in entries), entries
    assert campaign_save_hashes(host.campaign) == checkpoint, "Accepting a stock board offer caused an early save"
    return {"resident_board_offers": {"stock_offers": sorted(offered), "guest_offers": sorted(visible),
                                      "previously_filtered_offers": sorted(previously_filtered),
                                      "accepted": quest, "journal_step": int(observed[0]),
                                      "campaign_checkpoint_unchanged": True}}


async def verify_resident_npc_override(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def command(seat, action, actor=None):
        request = f"{action}\t{actor}" if actor is not None else action
        return await portable_probe_command(seat.bridge, "portable-npc-override", request)

    checkpoint = campaign_save_hashes(host.campaign)
    assert await command(guest, "census") == "empty", "A drone already existed on the guest before the test spawn"
    drone = await command(host, "spawn")
    async with asyncio.timeout(8):
        while (observed := await command(guest, "observe", drone)) == "absent":
            await asyncio.sleep(.1)
    before = float(await command(host, "observe", drone))
    assert float(observed) == before > 0, (before, observed)
    assert await command(guest, "guest-damage", drone) == "damage denied"
    guest_drone = await command(guest, "census")
    assert guest_drone.startswith("NPC_Drone_C /Game/"), guest_drone
    assert float(await command(host, "observe", drone)) == before
    total = float(await command(host, "host-damage", drone))
    assert total >= before
    result = None
    try:
        async with asyncio.timeout(8):
            while True:
                result = await asyncio.gather(*(command(seat, "death", drone) for seat in (host, guest)))
                if result == ["false,true,1"] * 2:
                    break
                await asyncio.sleep(.1)
    except TimeoutError as error:
        raise AssertionError(f"The host and guest drone death states were {result}") from error
    assert campaign_save_hashes(host.campaign) == checkpoint, "Drone damage caused an early checkpoint"
    return {"resident_npc_override": {"host_stock_drone": drone, "guest_stock_drone": guest_drone,
                                      "hit_points_before": before,
                                      "guest_local_damage_denied": True, "authority_damage": total,
                                      "ragdoll_on_both": True, "campaign_checkpoint_unchanged": True}}


async def verify_resident_campfire(host: Seat, guest: Seat, pawn: str) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-campfire", action)

    async def sample(seat):
        own, remote, overlay, audio, muted, fire, peer_fire, voices, offset, inside, peer_inside, own_regen, remote_regen = (
            await command(seat, "read")).split(",")
        return {"local_health": float(own), "remote_health": float(remote), "overlays": int(overlay), "burn_hits": int(audio),
                "muted_remote_burn_hits": int(muted), "local_fire_pulses": int(fire), "remote_fire_pulses": int(peer_fire),
                "hurt_voices": int(voices), "remote_hit_offset": float(offset),
                "local_overlap": inside == "true", "remote_overlap": peer_inside == "true",
                "local_regen": float(own_regen), "remote_regen": float(remote_regen)}

    async def wait_for(seat, predicate, timeout=7):
        result = None
        try:
            async with asyncio.timeout(timeout):
                while True:
                    result = await sample(seat)
                    if predicate(result):
                        return result
                    await asyncio.sleep(.1)
        except TimeoutError as error:
            raise AssertionError(f"Campfire observation timed out after {result}") from error

    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory")
    assert all(result == "armed" for result in await asyncio.gather(*(command(seat, "arm") for seat in (host, guest))))
    baseline = await sample(host)
    assert not baseline["local_overlap"] and not baseline["remote_overlap"], baseline
    sickness, coefficient, floor, maximum = map(float, (await command(host, "regen-policy")).split(","))
    assert 0 <= sickness <= 100 and 0 < coefficient < 1 and 0 <= floor < 1 and maximum > 0
    assert baseline["remote_regen"] == baseline["remote_health"] == maximum, baseline

    assert all(result == "moved" for result in await asyncio.gather(command(host, "enter-peer"), command(guest, "enter")))
    try:
        remote_hit = await wait_for(host, lambda hit: hit["remote_fire_pulses"] > 0 and hit["remote_health"] < baseline["remote_health"])
    finally:
        await asyncio.gather(command(guest, "leave"), command(host, "leave-peer"))
    host_observed = await sample(host)
    assert remote_hit["remote_hit_offset"] > 1, remote_hit
    guest_heard = await wait_for(guest, lambda hit: hit["overlays"] > 0 and hit["burn_hits"] > 0)
    assert remote_hit["local_health"] == host_observed["local_health"] == baseline["local_health"], (baseline, host_observed)
    assert host_observed["overlays"] == host_observed["burn_hits"] == 0 and host_observed["muted_remote_burn_hits"] > 0, host_observed
    assert guest_heard["burn_hits"] > 0 and guest_heard["local_health"] < baseline["remote_health"], guest_heard
    received = [entry for entry in await resident_events(guest_run, guest_frame)
                if entry["event"] == "native_guest_hurt_played" and entry["sound_mask"] & 4]
    assert received, guest_heard
    damage = baseline["remote_health"] - remote_hit["remote_health"]
    expected_ceiling = max(baseline["remote_regen"] - damage * (coefficient + (1 - coefficient) * sickness / 100),
                           maximum * floor)
    assert remote_hit["remote_health"] < expected_ceiling < maximum, (remote_hit, expected_ceiling)
    assert abs(remote_hit["remote_regen"] - expected_ceiling) < .01, (remote_hit, expected_ceiling)
    assert host_observed["local_regen"] == baseline["local_regen"], (baseline, host_observed)
    guest_recovered = await wait_for(
        guest, lambda hit: abs(hit["local_health"] - expected_ceiling) <= .2
        and abs(hit["local_regen"] - expected_ceiling) < .01, timeout=25)
    remote_recovered = await sample(host)
    assert abs(remote_recovered["remote_health"] - expected_ceiling) <= .2, remote_recovered
    assert abs(remote_recovered["remote_regen"] - expected_ceiling) < .01, remote_recovered

    await asyncio.sleep(1)
    assert all(result == "cleared" for result in await asyncio.gather(*(command(seat, "clear") for seat in (host, guest))))
    baseline = await sample(host)
    assert not baseline["local_overlap"] and not baseline["remote_overlap"], baseline
    assert await command(host, "enter") == "moved"
    try:
        local_hit = await wait_for(host, lambda hit: hit["local_fire_pulses"] > 0 and hit["local_health"] < baseline["local_health"])
    finally:
        await command(host, "leave")
    assert local_hit["overlays"] > 0 and local_hit["burn_hits"] > 0, local_hit
    assert local_hit["remote_health"] >= baseline["remote_health"] and local_hit["remote_regen"] == baseline["remote_regen"], (
        baseline, local_hit)
    guest_observed = await sample(guest)
    assert guest_observed["overlays"] == guest_observed["burn_hits"] == 0, guest_observed
    assert campaign_save_hashes(host.campaign) == checkpoint, "Campfire damage triggered an early save"
    return {"resident_campfire": {"guest_authority_damage": remote_hit, "host_observer": host_observed,
                                  "guest_feedback": guest_heard, "guest_feedback_events": received,
                                  "stock_guest_regeneration_ceiling": expected_ceiling,
                                  "guest_recovered": guest_recovered, "authoritative_guest_recovered": remote_recovered,
                                  "host_feedback": local_hit, "guest_observer": guest_observed,
                                  "campaign_checkpoint_unchanged": True}}


def unequip_rows(raw):
    result = []
    for entry in raw.split("|"):
        guid, equipped, backpack, rounds, tag = entry.split(",", 4)
        assert re.fullmatch(r"[0-9A-F]{32}", guid) and (equipped, backpack) in {("1", "0"), ("0", "1")}
        result.append({"guid": guid, "source": "equipment" if equipped == "1" else "backpack",
                       "rounds": int(rounds), "data_tag": tag})
    assert len(result) == len({row["guid"] for row in result}) == 3
    return result


def unequip_receipt(raw):
    before, inactive, after, selected, held, retired, live = raw.split(";")
    assert retired in {"true", "false"} and held in {"pistol", "fallback", "other"}
    return {"before": unequip_rows(before), "after_inactive": unequip_rows(inactive), "after_selected": unequip_rows(after),
            "selected_slot": int(selected), "held": held, "original_pistol_retired": retired == "true",
            "live_rounds": int(live)}


def unequip_seat(raw):
    rows, selected, held, retired, ammo = raw.split(";")
    assert retired in {"true", "false"} and held in {"pistol", "fallback", "other"}
    return {"rows": unequip_rows(rows), "slot": int(selected), "held": held, "retired": retired == "true",
            "ammo": int(ammo) if ammo != "-" else None}


async def verify_stock_unequip(first, second, evidence):
    probe = "portable-unequip"

    def saves():
        return (campaign_save_hashes(first.campaign), saved_hashes(first.profile / "Saved/SaveGames"),
                saved_hashes(second.profile / "Saved/SaveGames"))

    async def await_current_mirror(host):
        campaign = menu_host_checkpoint(host.campaign, profile=host.profile, require_scoped=True)
        assert campaign is not None
        campaign_id = campaign["campaign_id"]
        backup = host.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{uuid.UUID(campaign_id).hex}.sav"
        async with asyncio.timeout(25):
            while True:
                try:
                    with ZipFile(backup) as archive:
                        mirrored = json.loads(archive.read("campaign.json"))
                    if (mirrored["campaign_id"], mirrored["revision"]) == (campaign_id, campaign["revision"]):
                        return
                except (FileNotFoundError, PermissionError):
                    pass
                await asyncio.sleep(.15)

    async def solo_baseline(host, _guest, _character):
        await await_current_mirror(host)
        before = saves()
        result = unequip_receipt(await portable_probe_command(host.bridge, probe, "baseline"))
        evidence["solo_stock_baseline"] = result
        pistol, fallback, control = result["before"]
        assert (pistol["rounds"], result["live_rounds"]) == (5, 3), result
        moved_control = dict(control, source="backpack")
        assert result["after_inactive"] == [pistol, fallback, moved_control], result
        packed, returned_fallback, returned_control = result["after_selected"]
        assert packed == dict(pistol, source="backpack", rounds=packed["rounds"]) and packed["rounds"] in (3, 5)
        assert (returned_fallback, returned_control) == (fallback, moved_control)
        assert (result["selected_slot"], result["held"]) == (4, "fallback"), result
        result["saved_live_rounds"] = packed["rounds"] == result["live_rounds"]
        assert saves() == before, "The solo stock Unequip wrote a save before a checkpoint"

    async def guest_stock_actions(host, guest, pawn):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        for run in (host_run, guest_run):
            await wait_resident_status(run, lambda frame: frame.get("local_bridge") == "memory")

        async def witness(event, field, guid):
            async with asyncio.timeout(15):
                while True:
                    frame = await wait_resident_status(guest_run, lambda row: row.get("state") == "connected")
                    entries = await resident_events(guest_run, frame)
                    matches = [row for row in entries if row.get("event") == event and row.get(field, {}).get("guid") == guid]
                    assert len(matches) <= 1, matches
                    if matches:
                        return matches[0]
                    rejected = [row for row in entries if row.get("event") == "native_stock_inventory_rejected"]
                    if event == "native_stock_inventory_action" and rejected:
                        raise RuntimeError(rejected[-1]["reason"])
                    await asyncio.sleep(.15)

        async def leaves_stock_open():
            await wait_resident_status(guest_run, lambda frame:
                frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")

        async def close_stock():
            assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
            await wait_resident_status(guest_run, lambda row:
                row.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")

        await await_current_mirror(host)
        before = saves()
        ids = (await portable_probe_command(host.bridge, probe, f"remote-seed\t{pawn}")).split(",")
        assert len(ids) == len(set(ids)) == 3 and all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in ids)
        authority = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))["rows"]
        assert [row["guid"] for row in authority] == ids
        # TAB can reuse the authenticated join view before the next ordinary inventory poll.
        # Wait for the newly seeded exact slots before the screen builds its quick-slot cards.
        projection = {"expected": ids}
        evidence["guest_initial_projection"] = projection
        async with asyncio.timeout(15):
            while True:
                seen = (await portable_probe_command(guest.bridge, probe, "guest-projection")).split(",")
                projection["observed"] = seen
                if seen == ids:
                    break
                await asyncio.sleep(.25)
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await leaves_stock_open()
        guest_before = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-ready\t" + ",".join(ids)))
        assert guest_before["rows"] == authority
        selection = await witness("native_guest_equipment_selected", "native_guest_weapon", ids[0])
        host_before = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-ready\t{pawn}"))
        assert host_before == guest_before and host_before["held"] == "pistol" and host_before["ammo"] == 5
        evidence["guest_before"] = {"guest": guest_before, "host": host_before, "selection": selection}

        canceled = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-cancel-inactive"))
        assert canceled == host_before
        current = await wait_resident_status(guest_run, lambda row: row.get("state") == "connected")
        entries = await resident_events(guest_run, current)
        assert all(row.get("event") != "native_stock_inventory_action" for row in entries), entries
        await portable_probe_command(guest.bridge, probe, "guest-press-inactive")
        inactive = await witness("native_stock_inventory_action", "native_inventory_unequip", ids[2])
        assert (inactive["native_inventory_unequip"]["slot"], inactive["stock_inventory_proof"]) == (
            5, "verified stock unequip from slot 5"), inactive
        rows = [*authority[:2], dict(authority[2], source="backpack")]
        guest_inactive = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read"))
        host_inactive = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))
        assert guest_inactive == host_inactive == dict(host_before, rows=rows), (guest_inactive, host_inactive)
        evidence["guest_inactive"] = {"guest": guest_inactive, "host": host_inactive, "receipt": inactive,
                                      "canceled_drag_kept_slot": True}
        await leaves_stock_open()

        changed = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-ammo\t{pawn}"))
        assert changed == dict(host_inactive, ammo=3), changed
        stock_press = await portable_probe_command(guest.bridge, probe, "guest-press-selected")
        before_press, held_actor = stock_press.rsplit(";", 1)
        locally_pressed = unequip_seat(before_press)
        assert locally_pressed == dict(guest_inactive, ammo=2), locally_pressed
        evidence["guest_selected_press"] = {"state": locally_pressed, "stock_actor": dict(zip(
            ("authority", "instigated_by_pawn", "held_by_player", "being_destroyed"), held_actor.split(","), strict=True))}
        try:
            selected = await witness("native_stock_inventory_action", "native_inventory_unequip", ids[0])
        except (RuntimeError, TimeoutError):
            evidence["guest_selected_after_timeout"] = {
                "guest": unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read")),
                "host": unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}")),
            }
            raise
        assert (selected["native_inventory_unequip"]["slot"], selected["native_inventory_unequip"]["ammo"],
                selected["stock_inventory_proof"]) == (1, 3, "verified stock unequip from slot 1"), selected
        guest_after = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read"))
        host_after = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))
        expected_rows = [dict(rows[0], source="backpack", rounds=3), *rows[1:]]
        for state in (guest_after, host_after):
            assert state["rows"] == expected_rows and (state["slot"], state["held"], state["retired"]) == (
                4, "fallback", True), state
        evidence["guest_selected"] = {"guest": guest_after, "host": host_after, "receipt": selected,
                                      "host_live_rounds": changed["ammo"], "guest_live_rounds": locally_pressed["ammo"]}
        await close_stock()

        worn = ((native_guest_armor.HELMET_ACTION, 8, "guest-remove-helmet"),
                (native_guest_armor.ACTION, 9, "guest-drag-armor"))
        worn_guids = (await portable_probe_command(host.bridge, probe, f"remote-worn-seed\t{pawn}")).split(",")
        assert len(worn_guids) == len(set(worn_guids)) == 2 and all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in worn_guids)
        for (action, _, _), guid in zip(worn, worn_guids, strict=True):
            equipped = await submit(guest_run, action, target=guid)
            assert equipped["ok"], equipped

        current_worn = list(worn_guids)
        async with asyncio.timeout(15):
            while (await portable_probe_command(guest.bridge, probe, "guest-worn-state")).split(",") != current_worn:
                await asyncio.sleep(.25)
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await leaves_stock_open()
        evidence["guest_worn"] = []
        for index, ((_, slot, action), guid) in enumerate(zip(worn, worn_guids, strict=True)):
            assert (await portable_probe_command(guest.bridge, probe, f"{action}\t{guid}")).split(",") == current_worn
            receipt = await witness("native_stock_inventory_action", "native_inventory_unequip", guid)
            assert (receipt["native_inventory_unequip"]["slot"], receipt["stock_inventory_proof"]) == (
                slot, f"verified stock unequip from slot {slot}"), receipt
            current_worn[index] = "-"
            assert (await portable_probe_command(guest.bridge, probe, "guest-worn-state")).split(",") == current_worn
            authoritative = await submit(guest_run, "native-inventory-list")
            assert authoritative["ok"], authoritative
            rows = [row for row in authoritative["native_inventory"]["items"] if row["guid"] == guid]
            assert len(rows) == 1 and rows[0]["source"] == "backpack", authoritative
            evidence["guest_worn"].append({"receipt": receipt, "backpack_row": rows[0]})

        assert saves() == before, "The guest stock Unequip wrote a save before a checkpoint"
        await leaves_stock_open()
        await close_stock()
        return {"selected_and_inactive_slots_checked": True, "guest_duplicate_clicks_rejected": True,
                "guest_worn_slots_checked": True, "saves_unchanged_before_checkpoint": True}

    return await start_pair(first, second, "Unequip probe scout", before_join=solo_baseline, on_join=guest_stock_actions)


async def verify_stock_prop_grip(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory"
             and state.get("game", {}).get(native_prop_carry.FILTER_READY) is True),
    )
    approach = [float(value) for value in (await portable_probe_command(
        host.bridge, "portable-lantern", f"approach\t{pawn}")).split()]
    await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
               and math.dist(point, approach) < 100)
    await asyncio.sleep(1.25)
    lantern_class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
    grabbed = await submit(guest_run, native_prop_carry.FIXTURE, target=f"pickup@{lantern_class}|{native_lantern.ACTOR}")
    assert grabbed["ok"], grabbed

    async def edge(name, lease=None):
        observed = await wait(host_run, lambda state: (row := state.get("game", {}).get(native_prop_carry.PROOF, {}))
                              and row["edge"] == name and row["actor"] == native_lantern.ACTOR and row["pawn"] == pawn
                              and (lease is None or row["lease"] == lease))
        return observed["game"][native_prop_carry.PROOF]

    async def sample():
        held, *position = (await portable_probe_command(host.bridge, "portable-lantern", f"sample\t{pawn}")).split()
        return held == "1", [float(value) for value in position]

    grab = await edge("grab")

    def active_counters(state):
        counters = state.get("game", {}).get(native_prop_carry.FILTER, {})
        return counters if counters.get("lease") == grab["lease"] else {}

    holding_state = await wait(guest_run, lambda state: (row := active_counters(state))
                               and row["active"] and row["suppressed"] > 0)
    held_filter = active_counters(holding_state)
    held, held_position = await sample()
    assert held, held_position
    before = (await wait(host_run, lambda state: state.get("transport", {}).get("prop_pose_datagrams_received", 0) > 0))
    prior_count = before["transport"]["prop_pose_datagrams_received"]
    local_pawn = holding_state["game"]["pawns"]["local"]["name"]
    assert await portable_probe_command(guest.bridge, "portable-lantern", f"sweep\t{local_pawn}") == "sweeping"
    await asyncio.sleep(1.35)
    moved, swept_position = await sample()
    after = await wait(host_run, lambda state: state.get("updated_unix", 0) > before["updated_unix"] + 1.2)
    streamed = after["transport"]["prop_pose_datagrams_received"] - prior_count
    assert moved and math.dist(held_position, swept_position) > 25 and streamed > 0, (swept_position, streamed)
    dropped = await submit(guest_run, native_prop_carry.FIXTURE, target="drop")
    assert dropped["ok"], dropped
    drop = await edge("drop", grab["lease"])
    assert drop["sequence"] > grab["sequence"] and drop.get("release_pose_applied") is True, drop
    resumed_state = await wait(guest_run, lambda state: (row := active_counters(state))
                               and not row["active"] and row["passed"] > held_filter["passed"])
    async with asyncio.timeout(8):
        while True:
            held, released_position = await sample()
            if not held and math.dist(held_position, released_position) > 25:
                break
            await asyncio.sleep(.1)
    assert not (host.bridge.directory / "native-prop-carry-poses").exists()
    return {
        "local_bridge": {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]},
        "authored_prop": native_lantern.ACTOR, "grab": grab, "drop": drop,
        "filter_while_held": held_filter, "filter_after_drop": active_counters(resumed_state),
        "held_position": held_position, "released_position": released_position,
        "stock_sweep": {"target_seconds": 1.2, "received": streamed,
                        "host_displacement_cm": math.dist(held_position, swept_position),
                        "swept_position": swept_position},
    }


def lantern_angle_distance(before, after):
    return max(abs((first - second + 180) % 360 - 180) for first, second in zip(before, after, strict=True))


def lantern_observer_trace(receipt, role):
    header, *lines, terminator = receipt.split("|")
    assert terminator == "end", "Incomplete lantern observation receipt"
    seen_role, sweep_end, frequency = header.split()
    end, configured_hz = int(sweep_end), float(frequency)
    samples = [tuple(map(float, line.split())) for line in lines]
    assert int(seen_role) == role and math.isfinite(configured_hz)
    assert 2 <= end < len(samples) <= 240
    assert all(len(row) == 9 and all(map(math.isfinite, row)) for row in samples)
    assert all(after[0] > before[0] for before, after in zip(samples, samples[1:]))
    hidden_at = next((row[0] for row in samples if row[7] != 1), None)
    assert hidden_at is None, ("The exact stock lantern became hidden during the trace", role, hidden_at)
    sweep = samples[:end]
    assert all(row[8] == (1 if role == 3 else 0) for row in sweep) and samples[-1][8] == 0
    if role == 1:
        assert all(row[8] == 0 for row in samples), "The guest must observe without grabbing the lantern"
    steps = [math.dist(before[1:4], after[1:4]) for before, after in zip(sweep, sweep[1:])]
    max_rotation_step = max(lantern_angle_distance(before[4:7], after[4:7]) for before, after in zip(sweep, sweep[1:]))
    changes = [after[0] for after, step in zip(sweep[1:], steps, strict=True) if step >= .25]
    intervals_ms = [1000 * (after - before) for before, after in zip(changes, changes[1:])]
    displacement = max(math.dist(sweep[0][1:4], row[1:4]) for row in sweep)
    assert displacement > 25, ("The stock lantern failed to move during the host's sweep", role, displacement)
    end_pose = samples[-1]
    tail = [row for row in samples[end:] if end_pose[0] - row[0] <= .25]
    settled = (len(tail) >= 3 and end_pose[0] - tail[0][0] >= .15 and all(
        math.dist(row[1:4], end_pose[1:4]) <= 2 and lantern_angle_distance(row[4:7], end_pose[4:7]) <= 5
        for row in tail))
    return {"role": role, "configured_net_update_hz": configured_hz, "sweep_end_sample": end,
            "sweep_displacement_cm": displacement, "position_changes_over_quarter_cm": len(changes),
            "longest_change_interval_ms": max(intervals_ms, default=None), "largest_sample_step_cm": max(steps),
            "longest_sample_interval_ms": 1000 * max(after[0] - before[0] for before, after in zip(sweep, sweep[1:])),
            "largest_sample_rotation_step_degrees": max_rotation_step, "settled": settled, "samples": samples}


async def verify_stock_host_prop_observer(host: Seat, guest: Seat, pawn: str, evidence: dict) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(*(
        wait(run, lambda state: state.get("local_bridge") == state.get("local_status_source") == "memory"
             and state.get("game", {}).get("pawns", {}).get("local", {}).get("name"))
        for run in (host_run, guest_run)
    ))
    host_pawn, guest_pawn = (state["game"]["pawns"]["local"]["name"] for state in (host_status, guest_status))
    assert host_pawn != pawn and host_status["game"]["pawns"]["guest"]["name"] == pawn
    evidence["local_bridge"] = {seat: state["local_bridge"]
                                for seat, state in (("host", host_status), ("guest", guest_status))}

    async def call(seat, action, *, reply_end=None):
        owner = host_pawn if seat is host else guest_pawn
        return await portable_probe_command(seat.bridge, "portable-lantern", f"{action}\t{owner}", reply_end=reply_end)

    evidence["authored_prop"] = native_lantern.ACTOR
    assert await call(host, "hostgrab") == "grabbed"
    await asyncio.sleep(.35)
    assert await asyncio.gather(call(host, "tracearm"), call(guest, "tracearm")) == ["armed", "armed"]
    assert await call(host, "hostsweep") == "sweeping"
    await asyncio.sleep(1.35)
    async with asyncio.timeout(3):
        while True:
            marked = await call(host, "tracemark")
            if marked == "marked":
                break
            assert marked == "sweeping", marked
            await asyncio.sleep(.05)
    assert await call(guest, "tracemark") == "marked"
    assert await call(host, "hostdrop") == "dropped"
    await asyncio.sleep(2)
    authority, observer = await asyncio.gather(
        call(host, "traceread", reply_end="|end"), call(guest, "traceread", reply_end="|end"))
    evidence["raw_sample_receipts"] = {"host": authority, "guest": observer}
    data = {name: lantern_observer_trace(receipt, role)
            for name, receipt, role in (("host", authority, 3), ("guest", observer, 1))}
    del evidence["raw_sample_receipts"]
    gap = math.dist(*(row["samples"][-1][1:4] for row in data.values()))
    evidence.update(stock_host_grab_and_drop=True,
                    sample_columns=["elapsed_s", "x", "y", "z", "pitch", "yaw", "roll", "visible", "local_grip"],
                    end_position_gap_cm=gap,
                    settled_position_gap_cm=gap if all(row["settled"] for row in data.values()) else None,
                    seats=data)
    return {"stock_host_prop_observer": True}


async def verify_guest_shop_lease(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory" and state.get("gameplay_fresh") is True),
    )
    area, actor = native_finite_shop.OBSERVATORY, native_finite_shop.ACTOR
    local_pawn = guest_status["game"]["pawns"]["local"]["name"]
    assert host_status["game"]["map"] == guest_status["game"]["map"] == area and local_pawn

    paths = tuple(guest.bridge.directory / name for name in (
        "native-shop-view-heartbeat", "native-broker-input-heartbeat",
        "native-dialogue-heartbeat", "native-mosby-station-heartbeat",
    ))

    def require_memory_only():
        files = [path.name for path in paths if os.path.lexists(path)]
        assert not files, files

    save_roots = ((host.campaign / "save", "campaign*.json"),
                  (host.profile / "Saved" / "SaveGames", "*.sav"),
                  (guest.profile / "Saved" / "SaveGames", "*.sav"))

    def saves():
        return tuple({path.name: (runtime._file_sha256(path), path.stat().st_mtime_ns) for path in root.glob(pattern)}
                     for root, pattern in save_roots)

    baseline = saves()
    assert all(baseline)
    require_memory_only()
    approached = await host.bridge.command("native-finite-shop-approach", pawn, timeout=5)
    assert approached["ack"]["message"] == "exact stock merchant approach verified", approached["ack"]
    position, = [row["location"] for row in approached["pawns"].values() if row.get("name") == pawn]

    def arrived(state):
        local = state.get("game", {}).get("pawns", {}).get("local", {})
        point = local.get("location")
        return local.get("name") == local_pawn and point and math.dist(point, position) < 50

    await wait(guest_run, arrived)
    opened = await submit(guest_run, "native-shop-view-fixture", target="open")
    assert opened["ok"], opened
    pending = opened["native_shop_view"]
    assert (pending["state"], pending["map"], pending["actor"], pending["pawn"]) == (
        "pending", area, actor, local_pawn), pending

    def stock_ready(state):
        panel = state.get("game", {}).get("native_shop_view", {})
        return (state.get("state") == "connected" and state.get("local_bridge") == "memory"
                and panel.get("state") == "ready" and panel.get("stock") is True and panel.get("visible", 0) > 0
                and all(panel.get(key) == pending[key] for key in ("sequence", "map", "actor", "pawn")))

    ready = await wait(guest_run, stock_ready)
    started = time.monotonic()
    while time.monotonic() - started < 3.2:
        require_memory_only()
        await asyncio.sleep(.1)
    require_memory_only()
    checked_seconds = round(time.monotonic() - started, 3)
    # A single integer-second timestamp expires in at most three wall-clock seconds.
    current = await wait(guest_run, lambda state: stock_ready(state)
                         and state["game"]["sequence"] > ready["game"]["sequence"], timeout=5)
    closed = await submit(guest_run, "native-shop-view-fixture", target="back")
    assert closed["ok"], closed
    await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") in (None, "closed"))
    require_memory_only()
    assert saves() == baseline, "Opening the stock shop changed a campaign or stock save"
    return {
        "local_bridge": {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]},
        "host_authority_pawn": pawn, "guest_local_pawn": local_pawn, "stock_menu": current["game"]["native_shop_view"],
        "native_game_sequence_before": ready["game"]["sequence"],
        "native_game_sequence_after": current["game"]["sequence"],
        "absent_heartbeat_files": [path.name for path in paths],
        "heartbeat_check_seconds": checked_seconds,
        "saves_unchanged_before_checkpoint": True,
    }


async def verify_guest_stash_memory(host: Seat, guest: Seat, pawn: str, *, large: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    area = native_finite_shop.OBSERVATORY
    home, joined = await asyncio.gather(
        wait_resident_status(host_run, lambda row: row.get("local_bridge") == "memory"
                             and row.get("game", {}).get(native_stash.SITES)),
        wait_resident_status(guest_run, lambda row: row.get("local_bridge") == "memory"
                             and row.get("gameplay_fresh") and row.get("game", {}).get(native_stash.MEMORY_VIEW)),
    )
    assert home["game"]["map"] == joined["game"]["map"] == area
    if large:
        assert joined["game"].get(native_stash.LARGE_MEMORY_VIEW) is True, "The installed bridge lacks the large stash capability"
        assert home["local_status_source"] == joined["local_status_source"] == "memory"
    local_pawn = joined["game"]["pawns"]["local"]["name"]
    site, authored = min(home["game"][native_stash.SITES].items(), key=lambda pair: pair[1]["actor"])
    assert authored["map"] == area and home["game"][native_stash.LIVE_CENSUS] and home["game"][native_stash.LIVE_MOVE]
    approached = await host.bridge.command("native-stash-approach-fixture", pawn, site)
    assert approached["ack"]["message"] == "stock stash fixture is within interaction range", approached["ack"]
    position, = [row["location"] for row in approached["pawns"].values() if row.get("name") == pawn]

    def arrived(frame):
        local = frame.get("game", {}).get("pawns", {}).get("local", {})
        point = local.get("location")
        return local.get("name") == local_pawn and point and math.dist(point, position) < 50

    await wait_resident_status(guest_run, arrived)

    # Let the installed worker mirror the already committed guest creation
    # before measuring writes during the interactive stash window.
    checkpoint = menu_host_checkpoint(host.campaign, profile=host.profile, require_scoped=True)
    assert checkpoint is not None, "The installed host has no committed campaign to baseline"
    campaign_hex = uuid.UUID(checkpoint["campaign_id"]).hex
    backup = host.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{campaign_hex}.sav"
    async with asyncio.timeout(25):
        while True:
            try:
                with ZipFile(backup) as archive:
                    mirrored = json.loads(archive.read("campaign.json"))
                if (mirrored["campaign_id"], mirrored["revision"]) == (checkpoint["campaign_id"], checkpoint["revision"]):
                    break
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.15)

    def saves():
        roots = ((host.campaign / "save", "campaign*.json"),
                 (host.profile / "Saved/SaveGames", "*.sav"), (guest.profile / "Saved/SaveGames", "*.sav"))
        return tuple({str(path.relative_to(root)): (fingerprint(path), path.stat().st_mtime_ns)
                      for path in root.rglob(pattern) if path.is_file()}
                     for root, pattern in roots)

    def require_no_page_file():
        for pattern in ("native-stash-screen-*.tsv", ".native-stash-screen-*.tsv.*.tmp"):
            assert not list(guest.bridge.directory.glob(pattern)), pattern

    async def wait_for_stash_render(pending):
        def rendered(frame):
            require_no_page_file()
            return (frame.get("local_bridge") == "memory" and
                    frame.get("game", {}).get(native_stash.VIEW) == {**pending, "state": "rendered"})

        return (await wait_resident_status(guest_run, rendered))["game"][native_stash.VIEW]

    async def readback(pending, count, item="-"):
        observed = await wait_for_stash_render(pending)
        ack = await guest.bridge.command("portable-stash-memory-proof", str(pending["sequence"]), site)
        message = ack["ack"]["message"]
        proof = re.fullmatch(rf"portable stock stash memory {pending['sequence']} {count} {item} (-?[0-9]+)", message)
        assert proof, ack
        return observed, message, int(proof[1])

    async def close():
        assert (await submit(guest_run, "native-stash-view-click-fixture", target="close"))["ok"]
        await wait_resident_status(guest_run, lambda row: native_stash.VIEW not in row.get("game", {}))
        require_no_page_file()

    def require_unchanged_saves(before):
        changed = [(group, path) for group, (start, end) in enumerate(zip(before, saves(), strict=True))
                   for path in sorted(start.keys() | end.keys()) if start.get(path) != end.get(path)]
        assert not changed, ("The memory-backed stash view or transfer changed a campaign save", changed)

    before = saves()
    assert all(before)
    require_no_page_file()
    initial = await submit(guest_run, native_stash.LIST, target=site)
    assert initial["ok"] and initial[native_stash.RESULT]["owner"] == joined["character_id"]
    assert initial[native_stash.RESULT]["items"] == []

    if large:
        seed = await host.bridge.command("portable-large-stash-seed", pawn, site, joined["character_id"], timeout=12)
        expected = re.fullmatch(r"portable large stash seeded ([0-9A-F,]+)", seed["ack"]["message"])
        assert expected, seed["ack"]
        guids = expected[1].split(",")
        assert len(guids) == len(set(guids)) == 384 and all(native_stash.valid_guid(guid) for guid in guids)
        require_unchanged_saves(before)
    else:
        listed = await submit(guest_run, "native-inventory-list")
        assert listed["ok"], listed
        item, = [row for row in listed["native_inventory"]["items"]
                 if row["source"] == "backpack" and row["class_path"] == native_guest_consumable.STARTER_BAGS["hyperrandom"]]

    opened = await submit(guest_run, "native-stash-view-fixture", target=site)
    assert opened["ok"], opened
    pending = opened[native_stash.VIEW]
    assert (pending["state"], pending["pawn"], pending["site"], pending["mode"], pending["item"]) == (
        "pending", local_pawn, site, "stash", None)
    if large:
        rendered = await wait_for_stash_render(pending)
        receipt = await guest.bridge.command("portable-stash-memory-proof", str(pending["sequence"]), site, "large")
        matched = re.fullmatch(rf"portable large stash memory {pending['sequence']} 384 (\d+) ([0-9A-F,]+)",
                               receipt["ack"]["message"])
        assert matched, receipt["ack"]
        payload_bytes = int(matched[1])
        visible_guids = matched[2].split(",")
        assert visible_guids == guids and 32 * 1024 < payload_bytes <= native_stash.LARGE_MEMORY_VIEW_MAX_BYTES
        require_unchanged_saves(before)
        await close()
        cleared = await host.bridge.command("portable-large-stash-clear", pawn, site, joined["character_id"])
        assert cleared["ack"]["message"] == "portable large stash cleared without save", cleared["ack"]
        require_unchanged_saves(before)
        return {"stash_actor": authored["actor"], "site": site, "stock_view": rendered, "large_native_ticket": True,
                "memory_payload_bytes": payload_bytes, "stock_card_count": len(guids),
                "first_guid": guids[0], "last_guid": guids[-1], "ordered_card_guids": guids,
                "local_bridge": {"host": home["local_bridge"], "guest": joined["local_bridge"]},
                "stash_page_files_seen": False, "saves_unchanged_before_checkpoint": True,
                "fixture_inventory_cleared": True}

    empty, empty_proof, empty_ammo = await readback(pending, 0)
    assert empty_ammo == 0
    moved = await submit(guest_run, "native-stash-view-click-fixture", target=f"drag-backpack:{item['guid']}")
    assert moved["ok"], moved
    pending = moved[native_stash.VIEW]
    assert (pending["state"], pending["pawn"], pending["site"], pending["mode"], pending["item"]) == (
        "pending", local_pawn, site, "backpack", item["guid"]) and pending["sequence"] > empty["sequence"]
    filled, filled_proof, ammo = await readback(pending, 1, item["guid"])
    page = await submit(guest_run, native_stash.LIST, target=site)
    assert page["ok"] and page[native_stash.RESULT]["owner"] == joined["character_id"]
    stored, = page[native_stash.RESULT]["items"]
    assert all(stored[key] == item[key] for key in ("guid", "class_path", "quantity")), stored
    assert stored["ammo"] == ammo, stored
    backpack = await submit(guest_run, "native-inventory-list")
    assert backpack["ok"] and all(row["guid"] != item["guid"] for row in backpack["native_inventory"]["items"])
    await close()
    require_unchanged_saves(before)
    return {"stash_actor": authored["actor"], "site": site, "stashed_guid": item["guid"],
            "stock_empty": empty, "stock_filled": filled, "memory_readbacks": [empty_proof, filled_proof],
            "local_bridge": {"host": home["local_bridge"], "guest": joined["local_bridge"]},
            "stash_page_files_seen": False, "saves_unchanged_before_checkpoint": True}


async def verify_stock_crate_ring(host: Seat, guest: Seat, pawn: str, *, memory_positions: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_state, guest_state = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory"),
    )
    area = host_state["game"]["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1")]
    approached = await host.bridge.command("native-loot-approach", pawn, crate, timeout=5)
    position, = [row["location"] for row in approached["pawns"].values() if row["name"] == pawn]
    if memory_positions:
        await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
                   and math.dist(point, position) < 50)
    else:
        await guest.bridge.wait_for(lambda state: math.dist(state["pawns"]["local"]["location"], position) < 50)

    def save_fingerprints():
        return campaign_save_hashes(host.campaign), saved_hashes(host.profile / "Saved/SaveGames")

    before = save_fingerprints()
    assert all(before)
    request_at = time.monotonic()
    guest_actor = await portable_probe_command(guest.bridge, "portable-crate", "press")
    pressed_at = time.monotonic()
    # The guest's stock actor can have a different Unreal instance suffix.
    prefix = crate.rsplit("_", 1)[0] + "_"
    assert guest_actor.startswith(prefix) and guest_actor[len(prefix):].isdigit(), guest_actor
    opened = await wait(guest_run, lambda state: crate in state.get("gameplay", {}).get(native_loot.STATE_KEY, {}))
    visible_at = time.monotonic()
    roll = opened["gameplay"][native_loot.STATE_KEY][crate]
    await asyncio.sleep(.65)  # Let the compatible status copies arrive after the ring receipt.

    def events(run, name):
        rows = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        return [row for line in rows if (row := json.loads(line))["event"] == name and row.get("crate") == crate]

    received, = events(guest_run, "native_stock_crate_interaction")
    applied, = events(host_run, "native_loot_roll_accepted")
    assert received["input_source"] == "memory_input" and received["operation_id"] == applied["id"] == roll["id"]
    assert save_fingerprints() == before
    return {
        "local_bridge": {"host": host_state["local_bridge"], "guest": guest_state["local_bridge"]},
        "stock_crate": crate, "guest_actor": guest_actor,
        "input_source": received["input_source"], "sequence": received["sequence"],
        "roll_id": roll["id"], "roll_count": 1, "saves_unchanged_after_open": True,
        "probe_request_to_press_reply_ms": round(1000 * (pressed_at - request_at), 2),
        "probe_request_to_visible_roll_ms": round(1000 * (visible_at - request_at), 2),
    }


async def verify_compact_crate_status(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    before = await wait(host_run, lambda state: state.get("local_status_source") == "memory")
    area = before["game"]["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1")]
    details_key = native_loot.CRATE_DIAGNOSTICS
    diagnostic_key = "native_loot_crate_diagnostic"

    def ordinary(frame, phase, state, *, scope=None, after=0):
        game = frame.get("game", {})
        return (frame.get("state") == phase and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and game.get("map") == area
                and game.get("sequence", 0) > after and
                game.get("native_loot_crates", {}).get(crate) == {"map": area, "crate": crate, "state": state}
                and (scope is None or game.get("pawns", {}).get("local", {}).get("name") == scope)
                and details_key not in game and diagnostic_key not in game)

    async def seat_check(seat, run, phase):
        initial = await wait(run, lambda frame: ordinary(frame, phase, "ready"))
        local_pawn = initial["game"]["pawns"]["local"]["name"]
        tsv = seat.bridge.directory / "status.tsv"
        # Explicit file commands still refresh their real fallback status TSV.
        file_frame = await seat.bridge.command(native_loot.CRATE_DIAGNOSTIC, area, local_pawn, crate, timeout=5)
        raw = tsv.read_bytes()
        raw_rows = [line for line in raw.splitlines() if line.startswith(b"native-loot-crate-diagnostic\t")]
        file_row = file_frame[details_key][crate]
        assert len(raw_rows) == len(file_frame[details_key]) > 0
        assert file_frame["pawns"]["local"]["name"] == local_pawn
        assert file_frame["native_loot_crates"][crate]["state"] == "ready"
        assert (file_row["map"], file_row["crate"], file_row["phase"], file_row["scope"], file_row["looted"]) == (
            area, crate, phase, "active", False), file_row

        prior = await wait(run, lambda frame: ordinary(frame, phase, "ready", scope=local_pawn, after=file_frame["sequence"]))
        result = await submit(run, native_loot.CRATE_DIAGNOSTIC, target=crate)
        assert result["ok"] and result[diagnostic_key] == file_row, result
        invalid_area = await submit(run, native_loot.CRATE_DIAGNOSTIC, target=native_loot.CRATE)
        assert not invalid_area["ok"] and "exact authored crate" in invalid_area["error"], invalid_area
        later = await wait(run, lambda frame: ordinary(frame, phase, "ready", scope=local_pawn,
                                                      after=prior["game"]["sequence"]))
        assert tsv.read_bytes() == raw, "A resident memory query unexpectedly refreshed its fallback TSV"
        assert (prior["generation"], prior["zone_epoch"]) == (later["generation"], later["zone_epoch"])
        return {"pawn": local_pawn, "phase": phase, "sequence_before": prior["game"]["sequence"],
                "sequence_after": later["game"]["sequence"], "ready_before_and_after": True,
                "generation": later["generation"], "zone_epoch": later["zone_epoch"],
                "host_session": later.get("host_session"),
                "fallback_tsv": {"sha256": sha256(raw).hexdigest(), "bytes": len(raw), "diagnostic_rows": len(raw_rows),
                                 "sequence": file_frame["sequence"], "unchanged_across_memory_query": True},
                "memory_detail_fields_absent_before_and_after": True, "result": result[diagnostic_key],
                "different_map_target_refused": True}

    inspections = await asyncio.gather(seat_check(host, host_run, "hosting"), seat_check(guest, guest_run, "connected"))
    assert inspections[0]["generation"] == inspections[1]["host_session"]
    assert inspections[0]["zone_epoch"] == inspections[1]["zone_epoch"]
    stock_open = await verify_stock_crate_ring(host, guest, pawn, memory_positions=True)
    assert stock_open["stock_crate"] == crate and stock_open["roll_count"] == 1
    exhausted = await asyncio.gather(
        wait(host_run, lambda frame: ordinary(frame, "hosting", "exhausted", scope=inspections[0]["pawn"])),
        wait(guest_run, lambda frame: ordinary(frame, "connected", "exhausted", scope=inspections[1]["pawn"])),
    )
    for inspection, observed in zip(inspections, exhausted, strict=True):
        inspection["exhausted_sequence"] = observed["game"]["sequence"]
    return {"stock_crate": crate, "host": inspections[0], "guest": inspections[1], "stock_open": stock_open}


async def verify_gameplay(host: Seat, guest: Seat, pawn: str, *, stock_drop: bool = False,
                          stock_split: bool = False, stock_equip: bool = False, stock_nearby: bool = False,
                          projected_nearby: bool = False) -> dict:
    def qualified(game):
        return (game.get("native_enemy_loot_cqc_version") == 3
                and game.get("native_guest_regen", {}).get(pawn, {}).get("state") in {"applied", "anchored", "paused_game_clock"}
                and "guest_sickness" in game.get("native_guest_sickness", {}).get(pawn, {}))

    game = await host.bridge.wait_for(qualified)
    await guest.bridge.wait_for(lambda frame: frame.get("native_inventory_view_binding") == "ready")
    guest_run = guest.client / "run"
    async with asyncio.timeout(12):
        while True:
            try:
                events = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if any(json.loads(line).get("event") == "native_guest_ui_projected" for line in events):
                    break
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                pass
            await asyncio.sleep(.15)
    preview = await submit(guest_run, "native-inventory-view-fixture", target="stock")
    assert preview["ok"] and re.fullmatch(r"verified stock guest inventory with \d+ backpack cards",
                                          preview.get("stock_inventory_proof", "")), preview
    stock_drop_proof, stock_sort_proof, stock_use_proofs = None, None, {}
    if stock_drop:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        sorted_pack = await submit(guest_run, native_guest_drop.STOCK_SORT_FIXTURE)
        assert sorted_pack["ok"], sorted_pack
        stock_sort_proof = sorted_pack[native_inventory.SORT_KEY]
        assert re.fullmatch(r"verified open stock backpack sort with \d+ cards",
                            sorted_pack.get("stock_inventory_proof", "")), sorted_pack
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"], contents
        original_rows, sorted_rows = (part["native_inventory"]["items"] for part in (before, contents))
        assert stock_sort_proof["count"] == sum(row["source"] == "backpack" for row in sorted_rows)
        original_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in original_rows}
        sorted_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in sorted_rows}
        assert sorted_payload == original_payload
        assert (original_rows != sorted_rows) == (stock_sort_proof["state"] == "sorted")
        host_after = await submit(host_run, "native-inventory-list")
        assert host_after["ok"] and host_before["native_inventory"] == host_after["native_inventory"], host_after
        selected = next((row for row in sorted_rows if row["class_path"] not in native_guest_consumable.COMMANDS
                         and native_guest_drop.droppable(row, generic=True)), None)
        assert selected is not None, contents
        dropped = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=selected["guid"])
        assert dropped["ok"], dropped
        assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                            dropped.get("stock_inventory_proof", "")), dropped
        stock_drop_proof = dropped[native_guest_drop.RESULT]
        assert (stock_drop_proof["guid"], stock_drop_proof["class_path"], stock_drop_proof["quantity"]) == (
            selected["guid"], selected["class_path"], selected["quantity"],
        ), dropped
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        for seat in (host, guest):
            await seat.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"] and selected["guid"] not in {
            row["guid"] for row in contents["native_inventory"]["items"]
        }, contents
        for class_path in (native_guest_consumable.BANDAGE_CLASS, native_guest_consumable.MRE_CLASS):
            selected = next((row for row in contents["native_inventory"]["items"]
                             if row["class_path"] == class_path and row["source"] == "backpack"), None)
            assert selected is not None, contents
            used = await submit(guest_run, native_guest_drop.STOCK_USE_FIXTURE, target=selected["guid"])
            assert used["ok"], used
            assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                                used.get("stock_inventory_proof", "")), used
            key = native_guest_consumable.RESULT[native_guest_consumable.COMMANDS[class_path]]
            proof = stock_use_proofs[key] = used[key]
            assert (proof["guid"], proof["state"], proof["quantity_before"], proof["quantity_after"]) == (
                selected["guid"], "used", selected["quantity"], selected["quantity"] - 1,
            ), used
            contents = await submit(guest_run, "native-inventory-list")
            assert contents["ok"], contents
            remaining = [row["quantity"] for row in contents["native_inventory"]["items"]
                         if row["guid"] == selected["guid"]]
            assert remaining == ([proof["quantity_after"]] if proof["quantity_after"] else []), contents
    split_proof = None
    if stock_split:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        inventory = before["native_inventory"]
        stack = next((row for row in inventory["items"] if native_inventory.splittable(row)), None)
        assert stack is not None, inventory
        guid, quantity = stack["guid"], stack["quantity"]
        take = min(2, quantity - 1)
        stale = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity + 1}")
        assert not stale["ok"] and "stack changed" in stale["error"], stale
        assert (await submit(guest_run, "native-inventory-list"))["native_inventory"] == inventory
        split = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity}")
        assert split["ok"] and re.fullmatch(r"verified two stock stacks in an open backpack with \d+ cards",
                                           split.get("stock_inventory_proof", "")), split
        split_proof = split[native_inventory.SPLIT_KEY]
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        by_guid = {row["guid"]: row for row in after["items"]}
        new_guid = split_proof["new_guid"]
        assert (by_guid[guid]["quantity"], by_guid[new_guid]["quantity"]) == (quantity - take, take), after
        assert by_guid[guid]["class_path"] == by_guid[new_guid]["class_path"] == stack["class_path"], after
        assert len(after["items"]) == len(inventory["items"]) + 1, after
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    equip_proof = None
    if stock_equip:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        inventory = before["native_inventory"]
        shotgun = "/Game/Classes/Weapons/Wep_BoltAction_Shotgun.Wep_BoltAction_Shotgun_C"
        item = next((row for row in inventory["items"] if row["class_path"] == shotgun and row["source"] == "backpack"), None)
        assert item is not None, inventory
        equipped = await submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=item["guid"])
        assert equipped["ok"], equipped
        equip_proof = equipped[native_inventory.STORE_KEY]
        assert equipped["stock_inventory_proof"] == f"verified stock equipment slot {equip_proof['slot']}"
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        row, = (row for row in after["items"] if row["guid"] == item["guid"])
        assert row == dict(item, source="equipment", index=equip_proof["slot"], droppable=False), (row, item)
        assert {row["guid"] for row in after["items"]} == {row["guid"] for row in inventory["items"]}
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    if stock_nearby:
        host_run = host.campaign / "run"
        target = f"loose:{DEFAULT_ACTOR}"
        host_pawn = game["pawns"]["local"]["name"]
        for participant in (host_pawn, pawn):
            approached = await host.bridge.command(native_loose_pickup.APPROACH, DEFAULT_ACTOR, participant, timeout=5)
            assert approached.get("ack", {}).get("message") == "shared level pickup approach verified", approached["ack"]
        def bolt_in_reach(state):
            game = state.get("game", {})
            item = game.get(native_loose_pickup.KEY, {}).get(DEFAULT_ACTOR, {})
            origin = game.get("pawns", {}).get("local", {}).get("location")
            return item.get("state") == "available" and origin and math.dist(item["location"], origin) <= 250
        for run in (host_run, guest_run):
            await wait(run, bolt_in_reach)
        host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    dropped_actor = None
    if projected_nearby:
        if stock_split:
            picked = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=split_proof["source_guid"])
            assert picked["ok"], picked
            stock_drop_proof = picked[native_guest_drop.RESULT]
            assert stock_drop_proof["quantity"] == split_proof["remaining"] > 1, picked
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        await host.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {}))
        approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
        assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
        frame = await guest.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {})
            and math.dist(state["pawns"]["local"]["location"], stock_drop_proof["location"]) < 225)
        dropped_actor = frame[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    nearby_proof = None
    if stock_nearby:
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=target)
        assert clicked["ok"] and clicked["proof"] == "stock Nearby Items card requested", clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: state.get("game", {}).get(native_loose_pickup.KEY, {})
                       .get(DEFAULT_ACTOR, {}).get("state") == "claimed")
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        nearby_proof, = [event[native_loose_pickup.PROOF] for event in events
                         if event.get(native_loose_pickup.PROOF, {}).get("actor") == DEFAULT_ACTOR]
        inventory_guid = nearby_proof.get("inventory_guid", nearby_proof["guid"])
        quantity = nearby_proof.get("stack_merge", {}).get("after", 1)
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{target}@{inventory_guid}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert inventory["owner"] == nearby_proof["owner"], nearby_proof
        matching = [row for row in inventory["items"] if row["guid"] == inventory_guid]
        assert [(row["class_path"], row["quantity"]) for row in matching] == [(nearby_proof["class_path"], quantity)], matching
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
        duplicate = await submit(host_run, native_loose_pickup.CLAIM, target=target)
        assert duplicate.get("error") == "loose_pickup_already_claimed", duplicate
    drop_reclaim_proof = None
    if projected_nearby:
        before_reclaim = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=dropped_actor)
        count = stock_drop_proof["quantity"]
        expected = "stock Nearby Items card requested" + (f" x{count}" if count > 1 else "")
        assert clicked["ok"] and clicked["proof"] == expected, clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: identity not in state.get("game", {}).get(native_world_loot.KEY, {})
                and stock_drop_proof["drop_id"] not in state.get("gameplay", {}).get("native_guest_stack_drops", {}))
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        drop_reclaim_proof, = [row for event in events if (row := event.get(native_guest_drop.RESULT))
                               and row.get("state") == "reclaimed" and row.get("drop_id") == stock_drop_proof["drop_id"]]
        assert (drop_reclaim_proof["guid"], drop_reclaim_proof["quantity"]) == (stock_drop_proof["guid"], count)
        inventory_guid = drop_reclaim_proof.get("inventory_guid", stock_drop_proof["guid"])
        merge = drop_reclaim_proof.get("stack_merge", {})
        if stock_split:
            existing, = [row for row in before_reclaim["items"] if row["guid"] == inventory_guid]
            assert existing["class_path"] == stock_drop_proof["class_path"], existing
            assert merge == {"amount": count, "before": existing["quantity"], "after": existing["quantity"] + count}, drop_reclaim_proof
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE,
                               target=f"{dropped_actor}@{inventory_guid}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        guest_inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert drop_reclaim_proof["claimed_by"] == guest_inventory["owner"]
        returned = [row["quantity"] for row in guest_inventory["items"] if row["guid"] == inventory_guid]
        assert returned == [merge.get("after", count)], guest_inventory
        if merge:
            assert all(row["guid"] != stock_drop_proof["guid"] for row in guest_inventory["items"]), guest_inventory
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    normal_tab_proof = None
    if stock_drop:
        before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        selected = next((row for row in before["items"] if row["source"] == "backpack"
                         and row["class_path"] == native_guest_consumable.BANDAGE_CLASS), None)
        assert selected is not None, before
        clicked = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=selected["guid"])
        assert clicked["ok"] and clicked["stock_action"]["guid"] == selected["guid"], clicked
        await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock"
                   and "native_inventory_stock_action" not in state.get("game", {}))
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        remaining = [row["quantity"] for row in after["items"] if row["guid"] == selected["guid"]]
        assert remaining == ([selected["quantity"] - 1] if selected["quantity"] > 1 else []), after
        normal_tab_proof = {"guid": selected["guid"], "remaining": selected["quantity"] - 1}
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"placed_enemy_loot_bridge": game["native_enemy_loot_cqc_version"],
            "guest_regeneration_and_sickness_bound": True, "guest_shop_heartbeat": True,
            "guest_inventory_action_verified": True, "guest_inventory_projection_verified": True,
            **({"guest_stock_nearby_pickup": nearby_proof} if nearby_proof else {}),
            **({"guest_stock_nearby_drop_reclaim": drop_reclaim_proof} if drop_reclaim_proof else {}),
            **({"normal_tab_use": normal_tab_proof} if normal_tab_proof else {}),
            **({"guest_stock_menu_split": split_proof} if split_proof else {}),
            **({"guest_stock_menu_equip": equip_proof} if equip_proof else {}),
            **({"guest_stock_menu_drop": stock_drop_proof, "guest_stock_menu_use": stock_use_proofs,
                "guest_stock_menu_sort": stock_sort_proof}
               if stock_drop_proof else {})}


async def verify_container_pickup(host: Seat, guest: Seat, pawn: str, *, memory_ui: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    game = await host.bridge.wait_for(lambda frame: frame.get("state") == "hosting")
    area = game["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.startswith("LootCrate_Small_BP_C ")]
    host_pawn = game["pawns"]["local"]["name"]
    await host.bridge.command("native-loot-approach", host_pawn, crate, timeout=5)
    opened = await submit(host_run, "native-loot-open", target=crate)
    assert opened["ok"], opened
    roll = opened["native_loot_roll"]
    item = next((row for row in roll["items"] if row["class_path"] not in native_loot.CASH_RANGES), None)
    assert item is not None and "ammo" in item, roll
    identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll['id']}:{item['guid']}").hex
    await host.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
    approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
    seen = await guest.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {})
        and math.dist(frame["pawns"]["local"]["location"], item["transform"]["position"]) < 225)
    actor = seen[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]

    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    def save_state():
        return (campaign_save_hashes(host.campaign), saved_hashes(host.profile / "Saved/SaveGames"),
                saved_hashes(guest.profile / "Saved/SaveGames"))

    def projections():
        rows = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        return [row for line in rows if (row := json.loads(line))["event"] == "native_guest_ui_projected"]

    if memory_ui:
        for run in (host_run, guest_run):
            await wait(run, lambda state: state.get("local_bridge") == "memory"
                       and state.get("game", {}).get(runtime.GUEST_UI_MEMORY) is True)
        before = save_state()
        projections_before = len(projections())
        assert before[0] and projections_before

    clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=actor)
    assert clicked["ok"] and clicked["proof"].startswith("stock Nearby Items card requested"), clicked

    def settled(state):
        rows = state.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate, {}).get("items", ())
        return identity not in state.get("game", {}).get(native_world_loot.KEY, {}) and any(
            row["guid"] == item["guid"] and row.get("claimed_by") for row in rows)

    await wait(host_run, settled)
    await wait(guest_run, settled)
    events = (json.loads(line) for line in (host_run / "events.jsonl").read_text(encoding="utf-8").splitlines())
    claim, = [event for event in events if event["event"] == "native_loot_claim_accepted" and event.get("guid") == item["guid"]]
    inventory_guid = claim.get("inventory_guid", item["guid"])
    display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{actor}@{inventory_guid}")
    assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    received, = [row for row in inventory["items"] if row["guid"] == inventory_guid]
    assert received["class_path"] == item["class_path"], received
    if received["stackable"]:
        assert received["quantity"] == claim.get("stack_merge", {}).get("after", item["ammo"]), received
        if item["ammo"] > 1:
            assert clicked["proof"] == f"stock Nearby Items card requested x{item['ammo']}", clicked
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
    duplicate = await submit(host_run, "native-loot-claim", target=f"{item['guid']}@{crate}")
    assert duplicate.get("error") == "native_loot_already_claimed", duplicate
    memory_proof = {}
    if memory_ui:
        refreshed = projections()[projections_before:]
        assert refreshed and refreshed[-1]["items"] >= 1, refreshed
        assert save_state() == before
        memory_proof = {"resident_ui_projection": refreshed[-1], "saves_unchanged_before_checkpoint": True}
    checkpoint = await submit(host_run, "checkpoint")
    assert checkpoint["ok"], checkpoint
    saved = json.loads((host.campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
    native_host_save.verify_checkpoint(host.profile, saved)
    durable = saved["world"]["party"][native_loot.STATE_KEY][crate]
    committed, = [row for row in durable["items"] if row["guid"] == item["guid"]]
    assert committed == {**item, "claimed_by": inventory["owner"]}, committed
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"crate": crate, "roll": roll["id"], "item": committed, "rendered": clicked["proof"], "received": received,
            "duplicate": duplicate["error"], **memory_proof}


async def verify_stock_use(host: Seat, guest: Seat, pawn: str, *, starter: bool) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    campaign_file = host.campaign / "save" / "campaign.json"

    def save_hashes():
        paths = [campaign_file, *host.profile.rglob("*.sav")]
        return {path: sha256(path.read_bytes()).hexdigest() for path in paths}

    before_saves = save_hashes()
    before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    classes = ({native_guest_consumable.STARTER_BAGS["hyperrandom"]} if starter
               else {native_guest_consumable.BANDAGE_CLASS})
    candidates = [row for row in before["items"] if row["source"] == "backpack" and row["class_path"] in classes]
    assert candidates and (not starter or len(candidates) == 1), before
    item = min(candidates, key=lambda row: row["guid"])
    previous_use = None
    if not starter:
        observer = await wait(host_run, lambda state: pawn in state.get("game", {}).get("native_player_avatars", {}))
        previous_use = observer["game"].get("native_avatar_use", {}).get(pawn, {}).get("serial")
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    gesture = "drag" if starter else "context"
    target = item["guid"] + ("|drag" if starter else "")
    selected = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=target)
    assert selected["ok"] and selected["stock_action"]["guid"] == item["guid"], selected
    action = native_guest_consumable.COMMANDS[item["class_path"]]
    key = native_guest_consumable.RESULT[action]
    async with asyncio.timeout(15):
        while True:
            events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            refused = next((entry for entry in events if entry["event"] == "native_stock_inventory_rejected"), None)
            if refused:
                raise RuntimeError(refused["reason"])
            if succeeded := [entry for entry in events if entry["event"] == "native_stock_inventory_action"
                             and entry.get(key, {}).get("guid") == item["guid"]]:
                event, = succeeded
                break
            await asyncio.sleep(.1)
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock"
               and "native_inventory_stock_action" not in state.get("game", {}))
    receipt = event[key]
    assert (receipt["pawn"], receipt["quantity_before"], receipt["quantity_after"]) == (
        pawn, item["quantity"], item["quantity"] - 1), receipt
    after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    contents = {row["guid"]: row for row in after["items"]}
    added = set(contents) - {row["guid"] for row in before["items"]}
    if starter:
        assert added and added == set(receipt["awarded_guids"]), (after, receipt)
        assert all(contents[guid]["source"] == "backpack" for guid in added), after
    else:
        assert not added, after
    assert contents.get(item["guid"], {}).get("quantity", 0) == receipt["quantity_after"], after
    remaining = {row["guid"]: {key: value for key, value in row.items() if key != "index"}
                 for row in after["items"] if row["guid"] not in added and row["guid"] != item["guid"]}
    assert remaining == {row["guid"]: {key: value for key, value in row.items() if key != "index"}
                         for row in before["items"] if row["guid"] != item["guid"]}, after
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
    replay = await submit(guest_run, action, target=item["guid"], operation_id=event["operation_id"])
    assert replay["ok"] and replay[key] == receipt, replay
    assert (await submit(guest_run, "native-inventory-list"))["native_inventory"] == after
    assert save_hashes() == before_saves, "Using a stock item wrote a campaign save"
    saved = await submit(host_run, "checkpoint")
    assert saved["ok"], saved
    native_host_save.verify_checkpoint(host.profile, json.loads(campaign_file.read_text(encoding="utf-8")))
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    evidence = {"gesture": gesture, "receipt": receipt}
    if not starter:
        def observed_use(state):
            use = state.get("game", {}).get("native_avatar_use", {}).get(pawn, {})
            return (use.get("serial") != previous_use and use.get("item") == item["class_path"]
                    and not use["active"] and use["hand_motion_cm"] > 20)
        host_state = await wait(host_run, observed_use, timeout=8)
        presentation = host_state["game"]["native_avatar_use"][pawn]
        assert presentation["mesh"].endswith(".SM_Prop_Bandage_02"), presentation
        evidence["host_observed"] = presentation
    return evidence


async def create_stock_drop_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    await creator_action(guest, "guestkit")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def create_stock_bag_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    await creator_action(guest, "guesthyperkit")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def create_stock_split_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    assert await creator_action(guest, "preset") == "page=4"
    assert (await creator_action(guest, "veteran")).startswith("class=Veteran;")
    assert await creator_action(guest, "next") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def verify_cloud_restore(first, second, session, game, processes):
    campaign_id = uuid.UUID(session["campaign_id"]).hex
    source = first.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{campaign_id}.sav"
    assert source.is_file(), "The installed title did not back up the committed host campaign"
    name = f"campaign-{campaign_id}"
    directory = second.campaign.parent / name
    destination = second.profile / "Saved/SaveGames" / FOLDER / source.name
    destination.parent.mkdir(exist_ok=True)
    assert not directory.exists() and not destination.exists()
    solo = stock_save_hashes(second.profile)
    solo.pop("Config.sav", None)
    preferences = await stock_profile(second.bridge)
    form = "native-title-host-form-fixture"

    async def simulate_steam_download():
        await runtime.stop_game(second.bridge.process)
        # Auto-Cloud downloads the file before launching the game on the other PC.
        shutil.copy2(source, destination)
        root = second.bridge.directory.parent
        previous = set(root.iterdir())
        process = launch_portable(game, second.profile)
        processes.append((game, process))
        async with asyncio.timeout(30):
            while process.poll() is None:
                fresh = [path for path in root.iterdir() if path not in previous and path.is_dir()]
                if fresh:
                    mailbox, = fresh
                    second.bridge = runtime.Bridge(mailbox, process)
                    await offered(second)
                    return
                await asyncio.sleep(.1)
        raise RuntimeError("The second replica exited before its next Steam-style launch")

    async def pick_restored():
        await second.bridge.command(form, "open")
        async with asyncio.timeout(12):
            while not (second.bridge.directory / "title-host-campaigns.tsv").is_file():
                await asyncio.sleep(.1)
        await second.bridge.command(form, "browse")
        for _ in range(3):
            observed = (await second.bridge.command(form, "saved-state"))["ack"]["message"]
            if observed.endswith(":" + name):
                return
            await second.bridge.command(form, "next")
        raise AssertionError("The downloaded backup is missing from Host Load Game")

    def checkpoint():
        return menu_host_checkpoint(directory, profile=second.profile, require_scoped=True)

    await simulate_steam_download()
    await pick_restored()
    restored = checkpoint()
    assert restored["campaign_id"] == session["campaign_id"] and restored["revision"] == session["revision"]
    character = restored["characters"][session["character_id"]]
    assert character["state"]["native_guest_save"]
    await second.bridge.command(form, "choose-saved")
    await second.bridge.command(form, "port", str(second.port))
    await second.bridge.command(form, "submit")
    hosted = await wait(directory / "run", lambda frame: frame.get("state") == "hosting")
    assert hosted["campaign"]["campaign_id"] == restored["campaign_id"]
    assert hosted["game"]["save_scope"] == campaign_id
    identity = direct_ip.read_resident_identity(first.campaign.parent.parent, first.campaign)
    work = second.campaign.parent.parent
    with runtime.installed_content(work, game):
        build = runtime.staged_build(work)
    pin = sha256(identity["certificate"].encode("utf-8")).hexdigest()
    guest_connection = await direct_ip.connect(second.client, "127.0.0.1", second.port, "", build, PROTOCOL,
                                               verified_fingerprint=pin)
    assert guest_connection.details["guest_id"] == character["guest_id"]
    await second.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
    await stock_pause(second.bridge, "open")
    await stock_pause(second.bridge, "save_and_quit")
    await wait(directory / "run", lambda frame: frame.get("state") == "stopped")
    await offered(second)
    newer = checkpoint()
    assert newer["revision"] > restored["revision"]
    assert destination.read_bytes() != source.read_bytes(), "The resumed host's backup was not refreshed"

    await simulate_steam_download()
    await pick_restored()
    assert checkpoint() == newer, "The old cloud backup replaced a newer local campaign"
    await second.bridge.command(form, "cancel")
    await second.bridge.command(form, "cancel")
    modes = await second.bridge.command("native-title-button-fixture", "modes")
    await second.bridge.wait_for(lambda frame: frame["sequence"] > modes["sequence"])
    observed_solo = stock_save_hashes(second.profile)
    observed_solo.pop("Config.sav", None)
    assert observed_solo == solo and await stock_profile(second.bridge) == preferences
    return {"restored_revision": restored["revision"], "retained_revision": newer["revision"],
            "guest_character_restored": session["character_id"], "host_identity_restored": True,
            "singleplayer_unchanged": True,
            "steam_download_simulated": True}


async def verify(work: Path, first_game: Path, second_game: Path, original: Path,
                 second_legacy_work: Path | None = None, *, stock_drop: bool = False, stock_split: bool = False,
                 stock_equip: bool = False, stock_nearby: bool = False, projected_nearby: bool = False,
                 container_nearby: bool = False, steam_cloud: bool = False, memory_bridge: bool = False,
                 stock_use: bool = False, stock_food: bool = False, stock_campfire: bool = False,
                 stock_npc_override: bool = False, stock_board_offers: bool = False,
                 memory_status: bool = False, stock_prop_grip: bool = False,
                 stock_unequip: bool = False, stock_host_prop_observer: bool = False, resident_weapon_cues: bool = False,
                 stock_crate_ring: bool = False, guest_world_start: bool = False,
                 guest_world_start_travel: bool = False, guest_ui_memory: bool = False, nonmirrored_startup: bool = False,
                 compact_crate_status: bool = False, compact_world_view: bool = False,
                 guest_shop_lease: bool = False, guest_stash_memory: bool = False, guest_stash_large_memory: bool = False,
                 zone_lease_preflight: bool = False,
                 resident_cabinet_archive: Path | None = None,
                 resident_cabinet_checkpoint: Path | None = None,
                 resident_cabinet_candidate_sha256: str | None = None) -> None:
    guest_world_start = guest_world_start or guest_world_start_travel
    guest_stash_memory = guest_stash_memory or guest_stash_large_memory
    container_nearby = container_nearby or guest_ui_memory
    roots = (work, first_game, second_game, original)
    if second_legacy_work is not None:
        roots += (second_legacy_work,)
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in path.parts} for path in roots if path != original)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use separate private replica and work directories outside the original saves")
    before = saved_hashes(original)
    if ((resident_cabinet_archive is None) != (resident_cabinet_checkpoint is None)
            or resident_cabinet_candidate_sha256 and not resident_cabinet_archive):
        raise ValueError("The cabinet profile needs the exact release archive and cold checkpoint")
    combined_presentation = resident_cabinet_candidate_sha256 is not None
    source_before = campaign_save_hashes(resident_cabinet_checkpoint) if resident_cabinet_checkpoint else None
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if not before or len(stock_slots) < 2 or not any(path.name == "Config.sav" for path in stock_slots):
        raise ValueError("Supply the original Saved directory with a stock configuration and at least one campaign slot")
    qualified = (qualify_release(first_game, None), qualify_release(second_game, second_legacy_work))
    if qualified[0].manifest_sha256 != qualified[1].manifest_sha256:
        raise ValueError("Both replicas must use the same portable release")
    if qualified[0].activation[first_game / runtime.SHIPPING.parent / "ue4ss/Mods/mods.txt"] is not None:
        raise ValueError("The first replica must have no mods.txt")
    if os.path.lexists(first_game / runtime.STAGE_MANIFEST):
        raise ValueError("The clean replica must start from the contents of the SilverFish folder alone")

    case = work / ("portable-title-" + uuid.uuid4().hex[:8])
    case.mkdir()
    print(case, flush=True)
    workspaces = case / "first", second_legacy_work or case / "second"
    games = first_game, second_game
    proof = {"verified": False, "native_bridges": [], "sessions": []}
    processes, seats, baselines = [], [], []
    probes = ExitStack()
    seeded = None

    # Campfire measures a fresh regeneration ceiling. Food resets the guest's
    # vitals for its own test and therefore runs after the other checks.
    resident_checks = tuple((name, check) for enabled, name, check in (
        (stock_campfire, "stock_campfire", verify_resident_campfire),
        (stock_npc_override, "stock_npc_override", verify_resident_npc_override),
        (stock_board_offers, "stock_board_offers", verify_resident_board_offers),
        (resident_weapon_cues, "resident_weapon_cues", verify_resident_weapon_cues),
        (stock_food, "stock_food", verify_resident_guest_food),
    ) if enabled)
    single_scenario = (bool(resident_checks) or stock_prop_grip or stock_host_prop_observer
                       or stock_crate_ring or guest_world_start
                       or guest_ui_memory or compact_crate_status or compact_world_view or guest_shop_lease
                       or guest_stash_memory or nonmirrored_startup or stock_unequip or resident_cabinet_archive is not None)
    try:
        if resident_cabinet_archive:
            proof.update(cabinet_provenance(resident_cabinet_archive, games, qualified, resident_cabinet_candidate_sha256),
                         test_only_timing_probes=True)
            for game in games:
                probes.enter_context(instrument(game, release_archive=resident_cabinet_archive,
                                                combined=combined_presentation))
        if guest_world_start:
            probes.enter_context(installed_probe(second_game, mod="SilverfishPortableGuestWorldStartProbe",
                                                 fixture="portable_guest_world_start_probe.lua"))
        if guest_world_start_travel:
            probes.enter_context(installed_probe(first_game, mod="SilverfishPortableRuinExitProbe",
                                                 fixture="portable_ruin_exit_probe.lua"))
        if stock_food:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableGuestFoodProbe",
                                                     fixture="portable_guest_food_probe.lua"))
        if stock_board_offers:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableBoardOfferProbe",
                                                     fixture="portable_board_offer_probe.lua"))
        if stock_npc_override:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableNpcOverrideProbe",
                                                     fixture="portable_npc_override_probe.lua"))
        if stock_campfire:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableCampfireProbe",
                                                     fixture="portable_campfire_probe.lua"))
        if resident_weapon_cues:
            probes.enter_context(installed_probe(first_game, mod="SilverfishPortableWeaponCueProbe",
                                                 fixture="avatar_presentation_probe.lua"))
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableFootstepProbe",
                                                     fixture="portable_footstep_probe.lua"))
                probes.enter_context(installed_probe(game, mod="SilverfishPortableDryFireProbe",
                                                     fixture="portable_dry_fire_probe.lua"))
        if stock_unequip:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableUnequipProbe",
                                                     fixture="portable_unequip_probe.lua"))
        if stock_prop_grip or stock_host_prop_observer:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableLanternProbe",
                                                     fixture="portable_lantern_probe.lua"))
        if compact_crate_status:
            proof["crate_status_overlay"] = {str(game): probes.enter_context(compact_crate_status_overlay(game)) for game in games}
        if nonmirrored_startup:
            proof["nonmirrored_startup_overlay"] = {
                str(game): probes.enter_context(nonmirrored_startup_overlay(game)) for game in games
            }
        if guest_shop_lease:
            overlays = [probes.enter_context(shop_lease_overlay(game)) for game in games]
            proof["shop_lease_overlay"] = dict(zip(map(str, games), overlays, strict=True))
            for key in ("script_source_overlay", "manifest_overlay_sha256"):
                assert overlays[0][key] == overlays[1][key], "The enrolled seats need identical overlay source"
        if guest_stash_memory:
            overlays = [stash_memory_overlays(probes, game, large=guest_stash_large_memory) for game in games]
            proof["stash_memory_overlay"] = dict(zip(map(str, games), overlays, strict=True))
            for name in overlays[0]:
                for key in ("script_source_overlay", "manifest_overlay_sha256"):
                    assert overlays[0][name][key] == overlays[1][name][key], "Stash overlays differ between seats"
        if compact_world_view:
            proof["world_view_overlay"] = {
                str(game): probes.enter_context(portable_main_overlay(game, patch_world_view_receipts)) for game in games
            }
        if stock_crate_ring or compact_crate_status:
            probes.enter_context(installed_probe(second_game, mod="SilverfishPortableCrateProbe",
                                                 fixture="portable_crate_input_probe.lua"))
        if (stock_use or stock_drop or stock_split or stock_equip or stock_nearby or container_nearby
                or guest_stash_memory or nonmirrored_startup):
            for game in games:
                probes.enter_context(installed_probe(game))
        boot_started = time.perf_counter()
        for game, workspace, release, port in zip(games, workspaces, qualified, choose_ports(), strict=True):
            title = workspace / release.title_directory
            profile, mailbox_root = title / "game-user", title / "run/game-bridge"
            if mailbox_root.is_dir() and any(mailbox_root.iterdir()):
                raise ValueError("Use an unused isolated title configuration for the upgrade test")
            if os.path.lexists(mailbox_root):
                install_stage._ordinary(mailbox_root, directory=True)
            stock = profile / "Saved/SaveGames"
            stock.mkdir(parents=True, exist_ok=True)
            install_stage._ordinary(stock, directory=True)
            for source in stock_slots:
                destination = stock / source.name
                if not os.path.lexists(destination):
                    shutil.copy2(source, destination)
                install_stage._ordinary(destination)
            hashes = stock_save_hashes(profile)
            del hashes["Config.sav"]

            campaign = workspace / "campaigns" / ("title-host-settings-" + uuid.uuid4().hex[:8])
            if resident_cabinet_checkpoint and game == first_game:
                seeded = copy_resident_checkpoint(resident_cabinet_checkpoint, campaign, profile)
            process = launch_portable(game, profile)
            processes.append((game, process))
            mailbox = await discover_mailbox(mailbox_root, process)
            assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
            seat = Seat(profile, campaign, workspace / "clients" / title.name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            proof["native_bridges"].append({
                "seat": title.name, "pid": process.pid, "bridge": mailbox.name,
                "runtime": release.package.name, "runtime_manifest_sha256": release.manifest_sha256,
                "workspace": str(workspace), "legacy_descriptor_selected": workspace == second_legacy_work,
            })
            baselines.append((hashes, await stock_profile(seat.bridge)))

        first, second = seats
        proof["boot_seconds"] = round(time.perf_counter() - boot_started, 3)

        async def check_gameplay(host, guest, pawn):
            evidence = await verify_gameplay(host, guest, pawn, stock_drop=stock_drop, stock_split=stock_split,
                                             stock_equip=stock_equip, stock_nearby=stock_nearby, projected_nearby=projected_nearby)
            if stock_use:
                evidence["guest_stock_use"] = await verify_stock_use(host, guest, pawn, starter=guest is second)
            if memory_bridge:
                host_status = await wait(host.campaign / "run", lambda state: state.get("local_bridge") == "memory")
                guest_status = await wait(guest.client / "run", lambda state: state.get("local_bridge") == "memory")
                assert host_status["transport"]["schema_version"] == guest_status["transport"]["schema_version"] == 1
                evidence["local_bridge"] = {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]}
            if memory_status:
                for run in (host.campaign / "run", guest.client / "run"):
                    await wait(run, lambda state: state.get("local_status_source") == "memory")
                evidence["memory_status_both_peers"] = True
            if container_nearby:
                evidence["guest_stock_nearby_container_pickup"] = await verify_container_pickup(
                    host, guest, pawn, memory_ui=guest_ui_memory)
            return evidence
        if nonmirrored_startup:
            startup = proof["nonmirrored_startup"] = {}
            proof["sessions"].append(await verify_nonmirrored_startup(first, second, startup))
        elif guest_world_start:
            startup = GuestWorldStartProof(second)
            character_name = "Observatory startup scout"

            async def check_reconnect(guest, started):
                await startup.reconnected(guest, started)
                if guest_world_start_travel:
                    await startup.travel(first, guest, portable_probe_command,
                                         blocker_name=character_name if zone_lease_preflight else None)

            exit_options = {"host_exit_actions": ("prompt", "confirm"), "after_exit": startup.verify_travel_exit} \
                if guest_world_start_travel else {}
            proof["sessions"].append(await start_pair(first, second, character_name,
                                                      before_join=startup.arm, on_join=startup.joined,
                                                      after_reconnect=check_reconnect,
                                                      wait_status=wait_resident_status, **exit_options))
        elif stock_unequip:
            proof["sessions"].append(await verify_stock_unequip(first, second, proof.setdefault("stock_unequip", {})))
        elif stock_prop_grip:
            proof["sessions"].append(await start_pair(first, second, "Lantern scout", on_join=verify_stock_prop_grip))
        elif stock_host_prop_observer:
            lantern_evidence = proof["host_prop_observer"] = {}

            async def observe_host_lantern(host, guest, pawn):
                return await verify_stock_host_prop_observer(host, guest, pawn, lantern_evidence)

            proof["sessions"].append(await start_pair(first, second, "Lantern observer", on_join=observe_host_lantern))
        elif resident_checks:
            results = proof["resident_check_results"] = {}
            campaign_started = time.perf_counter()

            async def run_resident_checks(host, guest, pawn):
                proof["join_seconds"] = round(time.perf_counter() - campaign_started, 3)
                gameplay = {}
                for name, check in resident_checks:
                    started = time.perf_counter()
                    try:
                        gameplay.update(await check(host, guest, pawn))
                    except Exception as error:
                        results[name] = {"passed": False, "seconds": round(time.perf_counter() - started, 3),
                                         "error": str(error) or type(error).__name__}
                        raise
                    results[name] = {"passed": True, "seconds": round(time.perf_counter() - started, 3)}
                return gameplay

            proof["sessions"].append(await start_pair(first, second, "Gameplay scout", on_join=run_resident_checks,
                                                      wait_status=wait_resident_status))
            if resident_weapon_cues:
                assert not weapon_cue_files(first, second), "A legacy weapon cue mailbox appeared before Save and Quit"
        elif compact_world_view:
            proof["sessions"].append(await start_pair(first, second, "Weather receipt scout", on_join=verify_world_view_receipt,
                                                      wait_status=wait_resident_status))
        elif compact_crate_status:
            async def refresh_host_pause(_guest, _started):
                # The generic title helper reads Pause readiness over its file bridge.
                armed = await wait(first.campaign / "run",
                                   lambda frame: frame.get("game", {}).get("stock_pause_exit", {}).get("state") == "armed")
                game = armed["game"]
                crate = proof["crate_status_trial"]["stock_crate"]
                await first.bridge.command(native_loot.CRATE_DIAGNOSTIC, game["map"], game["pawns"]["local"]["name"], crate)

            async def check_crates(host, guest, pawn):
                result = await verify_compact_crate_status(host, guest, pawn)
                proof["crate_status_trial"] = result
                return {"compact_crate_status": True}

            proof["sessions"].append(await start_pair(first, second, "Crate diagnostics scout", on_join=check_crates,
                                                      after_reconnect=refresh_host_pause))
        elif stock_crate_ring:
            proof["sessions"].append(await start_pair(first, second, "Crate scout", on_join=verify_stock_crate_ring))
        elif guest_shop_lease:
            proof["sessions"].append(await start_pair(first, second, "Merchant scout", on_join=verify_guest_shop_lease))
        elif guest_stash_memory:
            async def check_stash(host, guest, pawn):
                return await verify_guest_stash_memory(host, guest, pawn, large=guest_stash_large_memory)

            proof["sessions"].append(await start_pair(first, second, "Stash scout", on_join=check_stash,
                                                      create_character=create_stock_bag_character,
                                                      wait_status=wait_resident_status))
        elif guest_ui_memory:
            proof["sessions"].append(await start_pair(first, second, "Inventory scout", on_join=check_gameplay))
        elif resident_cabinet_checkpoint:
            assert seeded is not None
            cabinet_evidence = {}
            proof["cabinet_pickup"] = cabinet_evidence

            async def check_cabinet(host, guest, pawn):
                cabinet_evidence.update(await verify_resident_pickup(host, guest, pawn, seeded, combined=combined_presentation))
                return cabinet_evidence

            async def check_cabinet_saved(host, stopped, saved, character):
                cabinet_evidence["stock_save_and_quit"] = await verify_saved_pickup(host, stopped, saved, seeded)

            proof["sessions"].append(await start_pair(first, second, "Cabinet scout", on_join=check_cabinet,
                                                      load_existing=True, wait_status=wait_resident_status,
                                                      after_exit=check_cabinet_saved))
        else:
            create = create_stock_split_character if stock_split or stock_equip else create_stock_drop_character if stock_drop else None
            proof["sessions"].append(await start_pair(first, second, "Second portable scout", on_join=check_gameplay,
                                                      create_character=create_stock_bag_character if stock_use else create))
            proof["sessions"].append(await start_pair(second, first, "First portable scout", on_join=check_gameplay,
                                                      create_character=create_stock_drop_character if stock_use else create))
            assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
            assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
            assert len({leg["campaign_id"] for leg in proof["sessions"]}) == 2
        if steam_cloud:
            proof["steam_cloud"] = await verify_cloud_restore(first, second, proof["sessions"][0], second_game, processes)
        for seat, (hashes, preferences) in zip(seats, baselines, strict=True):
            for key in ("singleplayer", "modes", "multiplayer", "modes", "singleplayer"):
                button = await seat.bridge.command("native-title-button-fixture", key)
                await seat.bridge.wait_for(lambda frame: frame["sequence"] > button["sequence"])
            cached = await seat.bridge.command("native-title-stock-profile-fixture", "cache")
            assert cached["ack"]["message"] == preferences
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == hashes and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        proof.update(verified=True, solo_slots_and_settings_preserved=True,
                     both_titles_reused=not single_scenario,
                     launched_without_coop_environment=True)
    except Exception as error:
        if nonmirrored_startup:
            proof["nonmirrored_startup_failure"] = str(error) or type(error).__name__
        if stock_host_prop_observer:
            proof["stock_host_prop_observer_failure"] = str(error) or type(error).__name__
        if resident_checks and not any(not row["passed"] for row in proof.get("resident_check_results", {}).values()):
            proof["resident_session_failure"] = str(error) or type(error).__name__
        if compact_crate_status:
            proof["crate_status_failure"] = str(error)
        if compact_world_view:
            proof["world_view_receipt_failure"] = str(error) or type(error).__name__
        if guest_shop_lease:
            proof["shop_lease_failure"] = str(error) or type(error).__name__
        if guest_stash_memory:
            proof["stash_memory_failure"] = str(error) or type(error).__name__
        if resident_cabinet_checkpoint:
            proof["cabinet_pickup_failure"] = str(error) or type(error).__name__
        if guest_world_start_travel:
            proof["stock_travel_failure"] = str(error)
        raise
    finally:
        proof["native_boot_states"] = {str(game): boot_states(game) for game, _ in processes}
        for _, process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        if single_scenario:
            proof["processes_closed"] = len(processes) == 2 and all(process.poll() is not None for _, process in processes)
            proof["verified"] &= proof["processes_closed"]
        probes.close()
        proof["original_saves_verified"] = saved_hashes(original) == before
        if resident_cabinet_checkpoint:
            proof["resident_source_preserved"] = campaign_save_hashes(resident_cabinet_checkpoint) == source_before
            proof["verified"] &= proof["resident_source_preserved"]
        proof["portable_files_and_existing_activation_preserved"] = all(
            fingerprint(path) == digest for release in qualified for path, digest in release.activation.items()
        )
        proof["verified"] &= proof["original_saves_verified"] and proof["portable_files_and_existing_activation_preserved"]
        runtime.write_json(case / "portable-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"
        assert proof.get("resident_source_preserved", True), "The source cabinet checkpoint changed"
        assert proof["portable_files_and_existing_activation_preserved"], "The portable marker or existing activation changed"
        if guest_world_start or guest_shop_lease or guest_stash_memory or nonmirrored_startup or resident_weapon_cues:
            assert all(process.poll() is not None for _, process in processes), "A portable test process is still running"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Existing private parent for new evidence and profile directories")
    parser.add_argument("--first-game", type=Path, required=True, help="Clean replica with the release ZIP applied and no mods.txt")
    parser.add_argument("--second-game", type=Path, required=True, help="Separate replica with the same release ZIP applied")
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--stock-drop", action="store_true", help="test stock guest backpack Sort, Drop, and Use actions")
    parser.add_argument("--stock-use", action="store_true", help="open a starter bag by stock drag and use a Bandage by stock click")
    parser.add_argument("--stock-food", action="store_true", help="verify the guest's Food stat after a meal, combinable with campfire and weapon cues")
    parser.add_argument("--stock-campfire", action="store_true", help="verify stock campfire damage and feedback, combinable with food and weapon cues")
    parser.add_argument("--stock-board-offers", action="store_true",
                        help="compare the stock Observatory board on both seats and accept a complex contract")
    parser.add_argument("--stock-npc-override", action="store_true",
                        help="verify guest damage rejection and host death on a stock drone")
    parser.add_argument("--stock-split", action="store_true", help="test the stock guest stack-split popup with a Veteran loadout")
    parser.add_argument("--stock-equip", action="store_true", help="test the stock guest Equip action with a Veteran loadout")
    parser.add_argument("--stock-unequip", action="store_true",
                        help="verify stock solo and host-authorized guest quick-slot Unequip")
    parser.add_argument("--stock-nearby", action="store_true", help="pick up a loose actor from the guest's stock Nearby Items panel")
    parser.add_argument("--projected-nearby", action="store_true", help="reclaim a player drop through stock Nearby Items")
    parser.add_argument("--container-nearby", action="store_true", help="pick up live stock container loot through Nearby Items")
    parser.add_argument("--steam-cloud", action="store_true", help="test a copied cloud backup through Host Load Game")
    parser.add_argument("--memory-bridge", action="store_true", help="verify both embedded workers use the native bridge")
    parser.add_argument("--memory-status", action="store_true", help="verify both workers consume live frames through memory")
    parser.add_argument("--resident-weapon-cues", action="store_true",
                        help="verify Bandage, footsteps, firing and reload, combinable with campfire and food")
    parser.add_argument("--guest-ui-memory", action="store_true",
                        help="verify the resident guest UI refresh on one stock Nearby Items container pickup")
    parser.add_argument("--stock-prop-grip", action="store_true", help="verify default guest carry filtering in one portable lantern session")
    parser.add_argument("--stock-host-prop-observer", action="store_true",
                        help="sample the held stock lantern on the host and a passive guest during one sweep")
    parser.add_argument("--stock-crate-ring", action="store_true", help="verify one guest stock F crate open through the in-process ring")
    parser.add_argument("--compact-crate-status", action="store_true",
                        help="test normal crate status routing with one private muted portable pair and a temporary Lua overlay")
    parser.add_argument("--guest-shop-lease", action="store_true",
                        help="verify the Observatory stock shop remains open while guest interaction leases stay in memory")
    stash_cases = parser.add_mutually_exclusive_group()
    stash_cases.add_argument("--guest-stash-memory", action="store_true",
                            help="verify empty and filled stock Observatory stash renders through the resident bridge")
    stash_cases.add_argument("--guest-stash-large-memory", action="store_true",
                            help="verify a stock Observatory stash larger than one ordinary resident command ticket")
    parser.add_argument("--compact-world-view", action="store_true",
                        help="verify one compact resident stock weather receipt in a private muted portable pair")
    parser.add_argument("--guest-world-start", action="store_true",
                        help="verify first stock guest map startup and short reconnect in one portable session")
    parser.add_argument("--nonmirrored-startup", action="store_true",
                        help="verify installed host/guest title readiness without live game status file mirroring")
    parser.add_argument("--guest-world-start-travel", action="store_true",
                        help="also verify stock guest startup through Crossroads and Ruin Fields")
    parser.add_argument("--zone-lease-preflight", action="store_true",
                        help="check the named Observatory wall blocker and file-free lease before the stock travel")
    parser.add_argument("--resident-cabinet-archive", type=Path,
                        help="profile one stock C_3 pickup using the published baseline or pinned candidate ZIP")
    parser.add_argument("--resident-cabinet-candidate-sha256",
                        help="expected archive SHA-256 for the joint pickup candidate, omitted for the published baseline")
    parser.add_argument("--resident-cabinet-checkpoint", type=Path,
                        help="closed standalone Observatory C_3 checkpoint whose stock escrow will be copied")
    parser.add_argument("--second-legacy-work", type=Path,
                        help="Unused isolated workspace from the second replica's retained legacy descriptor")
    args = parser.parse_args()
    if args.nonmirrored_startup and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip, args.stock_host_prop_observer,
            args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory,
            args.compact_crate_status, args.compact_world_view, args.guest_shop_lease, args.resident_cabinet_archive,
            args.second_legacy_work)):
        parser.error("Use --nonmirrored-startup with two fresh private replicas and no other gameplay fixtures")
    if args.zone_lease_preflight and not args.guest_world_start_travel:
        parser.error("Use --zone-lease-preflight with --guest-world-start-travel")
    if (args.guest_stash_memory or args.guest_stash_large_memory) and any((
                                       args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                       args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                       args.stock_host_prop_observer, args.nonmirrored_startup,
                                       args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                       args.guest_ui_memory, args.guest_shop_lease, args.compact_crate_status,
                                       args.compact_world_view, args.steam_cloud, args.resident_cabinet_archive,
                                       args.second_legacy_work)):
        parser.error("Use the guest stash checks with two fresh private replicas and no other gameplay fixtures")
    if args.guest_shop_lease and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                     args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                     args.guest_ui_memory, args.compact_crate_status, args.compact_world_view,
                                     args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --guest-shop-lease with two fresh private replicas and no other gameplay fixtures")
    if args.compact_world_view and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                        args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                        args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                        args.guest_ui_memory, args.compact_crate_status, args.steam_cloud,
                                        args.second_legacy_work)):
        parser.error("Use --compact-world-view with two fresh private replicas and no other gameplay fixtures")
    if args.compact_crate_status and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                          args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                          args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                          args.guest_ui_memory, args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --compact-crate-status with two fresh private replicas and no other gameplay fixtures")
    if args.guest_ui_memory and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                     args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                     args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --guest-ui-memory without other gameplay fixtures")
    if args.projected_nearby and (args.stock_drop == args.stock_split or args.stock_nearby):
        parser.error("Use --projected-nearby with either --stock-drop or --stock-split and without --stock-nearby")
    if args.stock_use and (args.stock_drop or args.stock_split or args.stock_equip):
        parser.error("Use --stock-use without other stock backpack character fixtures")
    if args.stock_prop_grip and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                    args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                    args.stock_crate_ring)):
        parser.error("Use --stock-prop-grip without other gameplay fixtures")
    if args.stock_host_prop_observer and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip, args.stock_crate_ring,
            args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory, args.compact_crate_status,
            args.compact_world_view, args.guest_shop_lease, args.resident_cabinet_archive, args.second_legacy_work)):
        parser.error("Use --stock-host-prop-observer with two fresh private replicas and no other gameplay fixtures")
    resident_check_options = {"stock_food", "stock_campfire", "stock_npc_override", "stock_board_offers", "resident_weapon_cues"}
    if (args.stock_food or args.stock_campfire or args.stock_npc_override or args.stock_board_offers) and (
            args.second_legacy_work or args.resident_cabinet_archive or any(
                value is True for key, value in vars(args).items() if key not in resident_check_options)):
        parser.error("Run the resident gameplay checks without other gameplay fixtures")
    if args.resident_weapon_cues and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip, args.stock_host_prop_observer,
            args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory,
            args.compact_crate_status, args.compact_world_view, args.guest_shop_lease, args.guest_stash_memory,
            args.guest_stash_large_memory, args.nonmirrored_startup, args.resident_cabinet_archive, args.second_legacy_work)):
        parser.error("Use --resident-weapon-cues with two fresh private replicas and no other gameplay fixtures")
    if args.stock_crate_ring and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                     args.second_legacy_work)):
        parser.error("Use --stock-crate-ring with two fresh private replicas and no other gameplay fixtures")
    if (args.guest_world_start or args.guest_world_start_travel) and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.stock_prop_grip, args.stock_crate_ring, args.steam_cloud)):
        parser.error("Use the guest world-start checks without other gameplay fixtures")
    if ((args.resident_cabinet_archive is None) != (args.resident_cabinet_checkpoint is None)
            or args.resident_cabinet_candidate_sha256 and not args.resident_cabinet_archive):
        parser.error("Use --resident-cabinet-archive and --resident-cabinet-checkpoint together")
    if args.resident_cabinet_archive and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                              args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                              args.stock_prop_grip, args.stock_crate_ring, args.guest_world_start,
                                              args.guest_world_start_travel, args.guest_ui_memory,
                                              args.compact_crate_status, args.compact_world_view, args.guest_shop_lease,
                                              args.second_legacy_work)):
        parser.error("Use --resident-cabinet-archive with two fresh private replicas and no other gameplay fixtures")
    cabinet_archive = args.resident_cabinet_archive.resolve(strict=True) if args.resident_cabinet_archive else None
    cabinet_checkpoint = args.resident_cabinet_checkpoint.resolve(strict=True) if args.resident_cabinet_checkpoint else None
    legacy = args.second_legacy_work.resolve(strict=True) if args.second_legacy_work else None
    asyncio.run(verify(args.work.resolve(strict=True), args.first_game.resolve(strict=True),
                       args.second_game.resolve(strict=True), args.vanilla_saved.resolve(strict=True), legacy,
                       stock_drop=args.stock_drop, stock_split=args.stock_split, stock_equip=args.stock_equip,
                       stock_unequip=args.stock_unequip,
                       stock_nearby=args.stock_nearby, projected_nearby=args.projected_nearby,
                       container_nearby=args.container_nearby, steam_cloud=args.steam_cloud, memory_bridge=args.memory_bridge,
                       stock_use=args.stock_use, stock_food=args.stock_food, stock_campfire=args.stock_campfire,
                       stock_npc_override=args.stock_npc_override, stock_board_offers=args.stock_board_offers,
                       memory_status=args.memory_status, stock_prop_grip=args.stock_prop_grip,
                       stock_host_prop_observer=args.stock_host_prop_observer, resident_weapon_cues=args.resident_weapon_cues,
                       stock_crate_ring=args.stock_crate_ring, guest_world_start=args.guest_world_start,
                       guest_world_start_travel=args.guest_world_start_travel, guest_ui_memory=args.guest_ui_memory,
                       nonmirrored_startup=args.nonmirrored_startup,
                       compact_crate_status=args.compact_crate_status, compact_world_view=args.compact_world_view,
                       guest_shop_lease=args.guest_shop_lease, guest_stash_memory=args.guest_stash_memory,
                       guest_stash_large_memory=args.guest_stash_large_memory,
                       zone_lease_preflight=args.zone_lease_preflight,
                       resident_cabinet_archive=cabinet_archive, resident_cabinet_checkpoint=cabinet_checkpoint,
                       resident_cabinet_candidate_sha256=args.resident_cabinet_candidate_sha256))
