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
    scope_module = script.with_name("native_save_scope.lua")
    tests = Path(__file__).resolve().parent
    expected = (tests / "fixtures/same_process_save.lua").read_bytes()
    module = tests.parent / "mod/SilverfishCoop/Scripts/native_save_scope.lua"
    for path in (script, activation, scope_module):
        if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("The fixture activation and script must be ordinary files")
    if activation.exists() and activation.read_bytes() != b"SameProcessSaveProof : 1\n":
        raise ValueError("The stock replica already has a different mods list")
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_bytes(expected)
    scope_module.write_bytes(module.read_bytes())
    activation.write_bytes(b"SameProcessSaveProof : 1\n")


def _read_proof(path: Path) -> dict:
    header, *lines = path.read_text(encoding="utf-8").splitlines()
    state, _, message = header.partition("\t")
    result = {"fixture_state": state, "fixture_message": message, "calls": {}}
    for line in lines:
        mode, call, quantity = line.split("\t")
        result["calls"].setdefault(mode, {})[call] = int(quantity)
    return result


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
        proof_file = case / "proof.tsv"
        async with asyncio.timeout(45):
            while process.poll() is None and not proof_file.is_file():
                await asyncio.sleep(.1)
        if process.poll() is not None:
            raise RuntimeError(f"The stock title exited during the save proof ({process.returncode})")
        evidence.update(_read_proof(proof_file))
        if evidence["fixture_state"] != "verified":
            raise RuntimeError(evidence["fixture_message"])
        calls = evidence["calls"]
        assert calls["solo"]["save"] == calls["coop"]["save"] == calls["coop"]["delete"] == 1, calls
        assert calls["solo"]["delete"] == 0 and calls["coop"]["find"] >= 4, calls
        assert calls["solo"]["load"] >= 2 and calls["coop"]["load"] >= 1, calls
        assert calls["solo"]["exists"] >= 2 and calls["coop"]["exists"] >= 1, calls
        assert calls["solo"]["async_load"] == 0 and calls["coop"]["async_load"] == 1, calls
        assert calls["other"]["async_save"] == 1 and calls["coop"]["async_save"] == 0, calls
        assert (solo / SLOT).read_bytes() == (case / "solo-before.sav").read_bytes()
        assert (solo / "Config.sav").read_bytes() == (case / "config-after.sav").read_bytes()
        assert not list(coop.iterdir()), "Stock delete did not remove the co-op file"
        assert (other / SLOT).read_bytes() == (case / "other-after-async-save.sav").read_bytes()
        assert (other / SLOT).read_bytes() != (solo / SLOT).read_bytes()
        assert not list(profile.rglob("ScopeEscape.sav")), "An invalid save slot escaped its campaign directory"
        evidence["save_hashes"] = {}
        for name in ("solo-before", "coop-before-delete", "other-after-async-save", "config-before", "config-after"):
            with (case / f"{name}.sav").open("rb") as item:
                evidence["save_hashes"][name] = file_digest(item, "sha256").hexdigest()
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
