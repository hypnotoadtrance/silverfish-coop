"""Verify stock solo and co-op save routing in one isolated Windows title process."""

import argparse
import asyncio
from hashlib import file_digest
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import native_host_save, native_title_entry, runtime, udp_ownership
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.win_job import GameProcess


MOD = "SameProcessSaveProof"
SLOT = "SameProcessCanary.sav"
SCOPE = "a" * 32
OTHER_SCOPE = "b" * 32


def _install_fixture(game: Path) -> None:
    mods = game.parent / "ue4ss/Mods"
    mods.mkdir(exist_ok=True)
    if mods.resolve(strict=True) != mods or any(path.name not in {MOD, "mods.txt"} for path in mods.iterdir()):
        raise ValueError("Use only the dedicated stock replica for this fixture")
    script = mods / MOD / "Scripts/main.lua"
    activation = mods / "mods.txt"
    tests = Path(__file__).resolve().parent
    expected = (tests / "fixtures/same_process_save.lua").read_bytes()
    modules = tests.parent / "mod/SilverfishCoop/Scripts"
    dependencies = ("native_save_scope.lua", "world_seed.lua", "zone_maps.lua", "authored_zone_maps.lua")
    for path in (script, activation, *(script.with_name(name) for name in dependencies)):
        if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("The fixture activation and script must be ordinary files")
    if activation.exists() and activation.read_bytes() != b"SameProcessSaveProof : 1\n":
        raise ValueError("The stock replica already has a different mods list")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(expected)
    for name in dependencies:
        script.with_name(name).write_bytes((modules / name).read_bytes())
    activation.write_bytes(b"SameProcessSaveProof : 1\n")


def _read_proof(path: Path) -> dict:
    header, *lines = path.read_text(encoding="utf-8").splitlines()
    state, _, message = header.partition("\t")
    result = {"fixture_state": state, "fixture_message": message, "calls": {}}
    for line in lines:
        mode, call, quantity = line.split("\t")
        result["calls"].setdefault(mode, {})[call] = int(quantity)
    return result


def same_process_save_overlay(source: str) -> str:
    """Arm the scoped save fixture from the private installed Lua state."""
    anchor = '    if command == "native-save-scope-select" or command == "native-save-scope-release" then\n'
    if source.count(anchor) != 1:
        raise ValueError("The private save scope command anchor changed")
    action = '''    if command == "native-save-lifecycle-proof" then
        local context = role == "idle" and native_title_entry.probe(state, current_phase)
        if #fields ~= 3 or not context or type(SilverfishReadSameProcessSaveLifecycle) ~= "function" then
            error("Read the private native save lifecycle at an unscoped stock title", 0)
        end
        return SilverfishReadSameProcessSaveLifecycle(context.world, fields[3])
    end

    if command == "native-same-process-save-proof" then
        if #fields ~= 6 or role ~= "idle" or not native_title_entry.probe(state, current_phase)
            or type(SilverfishStartSameProcessSaveProof) ~= "function" then
            error("Arm the private save proof at an unscoped stock title", 0)
        end
        return SilverfishStartSameProcessSaveProof(fields[3], fields[4], fields[5], fields[6])
    end

'''
    fixture = Path(__file__).with_name("fixtures").joinpath("same_process_save.lua").read_text(encoding="utf-8")
    return source.replace(anchor, action + anchor) + "\n;(function()\n" + fixture + "\nend)()\n"


async def _wait_proof(proc, case):
    proof = case / "proof.tsv"
    async with asyncio.timeout(45):
        while proc.poll() is None and not proof.is_file():
            await asyncio.sleep(.1)
    if proc.poll() is not None:
        raise RuntimeError(f"The stock title exited during the save proof ({proc.returncode})")
    evidence = _read_proof(proof)
    if evidence["fixture_state"] != "verified":
        raise RuntimeError(evidence["fixture_message"])
    return evidence


def _verify_stock_calls(calls):
    assert calls["solo"]["save"] == calls["coop"]["save"] == calls["coop"]["delete"] == 1, calls
    assert calls["solo"]["delete"] == 0 and calls["coop"]["find"] >= 4, calls
    assert calls["solo"]["load"] >= 2 and calls["coop"]["load"] >= 1, calls
    assert calls["solo"]["exists"] >= 2 and calls["coop"]["exists"] >= 1, calls
    assert calls["solo"]["async_load"] == 0 and calls["coop"]["async_load"] == 1, calls
    assert calls["other"]["async_save"] == 1 and calls["coop"]["async_save"] == 0, calls


def _proof_hashes(case):
    hashes = {}
    for name in ("solo-before", "coop-before-delete", "other-after-async-save", "config-before", "config-after"):
        with (case / f"{name}.sav").open("rb") as item:
            hashes[name] = file_digest(item, "sha256").hexdigest()
    return hashes


async def verify_installed_save_lifecycle(host, guest, session) -> dict:
    """Check ordinary native scope release and stock Continue on both portable seats."""
    keys = ("guest_view_installed", "guest_restored_while_scoped", "save", "slot", "autosave",
            "travel_step", "travel_step_remembered", "previous_save_rebound", "previous_slot_rebound",
            "previous_travel_rebound", "scope_released", "native_continue", "continue_cache", "stale_scope_refused")

    async def verify_seat(seat, role, identifier, pid):
        assert seat.bridge.process.pid == pid
        scope = uuid.UUID(identifier).hex
        response = await seat.bridge.command("native-save-lifecycle-proof", scope)
        observed_role, *flags = response["ack"]["message"].split(":")
        assert observed_role == role and all(flag in {"0", "1"} for flag in flags), response["ack"]
        report = dict(zip(keys, (flag == "1" for flag in flags), strict=True))
        assert all(report[key] for key in keys[-4:]), report
        if role == "guest":
            assert all(report[key] for key in keys[:6]), report
        else:
            assert not any(report[key] for key in keys[:-4]), report
        cache = await seat.bridge.command("native-title-stock-profile-fixture", "cache")
        assert cache["ack"]["message"], cache["ack"]
        return {"pid": pid, "scope_id": scope, "stock_profile_cache": cache["ack"]["message"], **report}

    first, second = await asyncio.gather(
        verify_seat(host, "host", session["campaign_id"], session["host_pid"]),
        verify_seat(guest, "guest", session["character_id"], session["guest_pid"]),
    )
    return {"host": first, "guest": second}


async def verify_installed_save_scope(seat) -> dict:
    """Exercise native save routing after a portable seat returns to stock title."""
    bridge = seat.bridge
    if (os.name != "nt" or "steamapps" in {p.casefold() for p in seat.profile.parts}
            or not any(p.startswith("portable-title-") for p in bridge.directory.parts)
            or bridge.directory.resolve(strict=True) != bridge.directory):
        raise ValueError("The native save proof requires an existing private portable seat")
    initial = await bridge.wait_for(lambda frame: frame.get(native_title_entry.KEY, {}).get("state") == "ready"
                                    and frame.get("mode") == "idle" and frame.get("save_scope", False) is None)
    saves = seat.profile / "Saved" / "SaveGames"
    if saves.resolve(strict=True) != saves:
        raise ValueError("The portable SaveGames directory is redirected")
    original = saved_hashes(saves)
    token, scope, other_scope = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    slot = f"SFSaveCanary_{token}.sav"
    if os.path.lexists(saves / slot):
        raise ValueError("The private canary save slot already exists")
    case = bridge.directory / f"same-save-{token}"
    private = saves / "SilverfishCoop"
    if private.resolve() != private:
        raise ValueError("The private co-op save directory is redirected")
    case.mkdir()
    coop, other = private / scope, private / other_scope
    owned = []
    submitted = False
    try:
        for path in (coop, other):
            path.mkdir(parents=True)
            owned.append(path)
        submitted = True
        armed = await bridge.command("native-same-process-save-proof", token, scope, other_scope, str(saves))
        assert armed["ack"]["message"] == "armed native stock save proof", armed
        evidence = await _wait_proof(bridge.process, case)
        _verify_stock_calls(evidence["calls"])
        assert evidence["calls"]["native"] == {"select": 3, "release": 3, "wrong_release": 1}, evidence["calls"]
        assert not (saves / slot).exists() and not list(coop.iterdir()), "The stock cleanup kept a canary save"
        other_bytes = (other / slot).read_bytes()
        assert other_bytes == (case / "other-after-async-save.sav").read_bytes()
        assert not list(seat.profile.rglob(f"SFSaveCanary_{token}_Escape.sav")), "A rejected save path escaped the scope"
        hashes = _proof_hashes(case)
        assert len({hashes["solo-before"], hashes["coop-before-delete"], hashes["other-after-async-save"]}) == 3, hashes
        assert hashes["config-before"] == hashes["config-after"], "Native save routing changed Config"
        title = await bridge.wait_for(lambda frame: frame["sequence"] > initial["sequence"]
                                      and frame.get(native_title_entry.KEY, {}).get("state") == "ready"
                                      and frame.get("mode") == "idle" and frame.get("save_scope", False) is None)
    finally:
        if not submitted or (case / "proof.tsv").is_file() or bridge.process.poll() is not None:
            for directory in owned:
                snapshot = directory / slot
                if snapshot.is_file() and snapshot.resolve(strict=True) == snapshot:
                    snapshot.unlink()
                directory.rmdir()
    assert saved_hashes(saves) == original, "The native proof changed a pre-existing save"
    evidence.update(verified=True, pid=bridge.process.pid, title_sequence=title["sequence"],
                    existing_saves_verified=True, save_hashes=hashes,
                    native_routing=True, scope_released=True)
    return evidence


async def verify(work: Path, original: Path) -> None:
    if (os.name != "nt" or "steamapps" in {part.casefold() for part in work.parts}
            or work.is_relative_to(original) or original.is_relative_to(work)):
        raise ValueError("Use a dedicated Windows replica and a disjoint original save tree")
    game = (work / "stage" / runtime.SHIPPING).resolve(strict=True)
    if game != work / "stage" / runtime.SHIPPING:
        raise ValueError("The stock replica executable must be an ordinary path")
    _install_fixture(game)
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory for hash comparison")

    case = work / "title-probes" / f"same-save-{uuid.uuid4().hex[:12]}"
    profile = case / "game-user"
    solo = profile / "Saved/SaveGames"
    coop = solo / "SilverfishCoop" / SCOPE
    other = solo / "SilverfishCoop" / OTHER_SCOPE
    coop.mkdir(parents=True)
    other.mkdir()
    print(case, flush=True)
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("SILVERFISH_COOP_", "SILVERFISH_SAME_PROCESS_", "UE4SS_"))}
    environment.update(SILVERFISH_COOP_HEADLESS="1", SILVERFISH_SAME_PROCESS_SAVE_PROOF=str(case))
    arguments = subprocess.list2cmdline([str(game)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed'
                  ' -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480'
                  ' -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,'
                  '[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    evidence = {"verified": False}
    process = None
    try:
        process = GameProcess(arguments, executable=game, cwd=game.parent, env=environment, startupinfo=startup)
        evidence["pid"] = process.pid
        evidence.update(await _wait_proof(process, case))
        _verify_stock_calls(evidence["calls"])
        assert (solo / SLOT).read_bytes() == (case / "solo-before.sav").read_bytes()
        assert (solo / "Config.sav").read_bytes() == (case / "config-after.sav").read_bytes()
        assert not list(coop.iterdir()), "Stock delete did not remove the co-op file"
        assert (other / SLOT).read_bytes() == (case / "other-after-async-save.sav").read_bytes()
        assert (other / SLOT).read_bytes() != (solo / SLOT).read_bytes()
        assert not list(profile.rglob("ScopeEscape.sav")), "An invalid save slot escaped its campaign directory"
        evidence["save_hashes"] = _proof_hashes(case)
        evidence["verified"] = evidence["save_hashes"]["solo-before"] != evidence["save_hashes"]["coop-before-delete"]
    except (OSError, RuntimeError, ValueError, TimeoutError, AssertionError) as error:
        evidence["failure"] = repr(error)
        raise
    finally:
        if process is not None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        runtime.write_json(case / "live-same-process-save-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original single-player save hashes changed"


async def verify_bridge(work: Path, original: Path) -> None:
    if (os.name != "nt" or "steamapps" in {part.casefold() for part in work.parts}
            or work.is_relative_to(original) or original.is_relative_to(work)
            or not runtime.qualified_authored_routes(work)):
        raise ValueError("Use a separate Windows replica with the native gameplay add-on")
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory for hash comparison")
    case = work / "title-probes" / f"save-scope-bridge-{uuid.uuid4().hex[:8]}"
    profile, mailbox = case / "game-user", case / "game-bridge"
    scope = uuid.uuid4().hex
    saves = profile / "Saved" / "SaveGames"
    private = saves / "SilverfishCoop" / scope
    private.mkdir(parents=True)
    port = runtime.spare_udp_port()
    process = runtime.launch(work, profile, mailbox, True, port, runtime_listener=True, native_zones=True)
    bridge = runtime.Bridge(mailbox, process)
    evidence = {"verified": False, "pid": process.pid}
    print(case, flush=True)

    async def ready_title():
        return await bridge.wait_for(lambda frame: frame.get(native_title_entry.KEY, {}).get("state") == "ready")

    async def refused(message, action, argument):
        try:
            await bridge.command(action, argument)
        except RuntimeError as error:
            assert str(error) == message, str(error)
        else:
            raise AssertionError(f"Accepted {action} when it should be unavailable")

    try:
        initial = await ready_title()
        assert initial["save_scope"] is None
        await refused("The private co-op save directory is unavailable", "native-save-scope-select", uuid.uuid4().hex)
        selected = await bridge.command("native-save-scope-select", scope)
        assert selected["save_scope"] == scope
        await bridge.command("host", OBSERVATORY, f"listener={port}")
        await bridge.wait_for(lambda frame: frame["state"] == "hosting" and frame["map"] == OBSERVATORY)
        runtime.verify_loopback_socket(process.pid, port)
        await refused("Switch co-op save scope at the stock title", "native-save-scope-release", scope)
        slot = native_host_save.new_slot()
        await bridge.command("native-host-save-capture", slot)
        capture = await bridge.wait_for(lambda frame: frame.get("native_host_save", {}).get("state") == "captured")
        captured = capture["native_host_save"]
        assert captured["slot"] == slot and capture["save_scope"] == scope
        path = private / f"{slot}.sav"
        assert path.read_bytes()[:4] == b"GVAS" and not (saves / path.name).exists()
        with path.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()

        await bridge.command("leave")
        assert (await ready_title())["save_scope"] == scope
        assert not udp_ownership.owns_udp_port(process.pid, port)
        await refused("The co-op save scope did not match", "native-save-scope-release", uuid.uuid4().hex)
        assert (await bridge.command("native-save-scope-release", scope))["save_scope"] is None
        assert (await bridge.command("native-save-scope-select", scope))["save_scope"] == scope

        await bridge.command("host", OBSERVATORY, "native-save", slot, f"listener={port}")
        resume = await bridge.wait_for(lambda frame: frame.get("native_host_save", {}).get("state") == "restored")
        restored = resume["native_host_save"]
        for key in ("slot", "map", "seed", "backpack_count", "equipment_count"):
            assert captured[key] == restored[key], restored
        runtime.verify_loopback_socket(process.pid, port)
        await bridge.command("leave")
        await ready_title()
        assert (await bridge.command("native-save-scope-release", scope))["save_scope"] is None
        assert not udp_ownership.owns_udp_port(process.pid, port)
        with path.open("rb") as stream:
            assert file_digest(stream, "sha256").hexdigest() == digest
        evidence.update(verified=True, captures=1, resumes=1, checkpoint_sha256=digest, scope_released=True)
    finally:
        await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        runtime.write_json(case / "live-save-scope-bridge-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--bridge", action="store_true", help="use the staged gameplay bridge to capture and resume a campaign")
    options = parser.parse_args()
    smoke = verify_bridge if options.bridge else verify
    asyncio.run(smoke(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
