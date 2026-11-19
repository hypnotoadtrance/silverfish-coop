"""Verify the stock Wanderer start, detached guest inventory, and same-port host restart."""

import argparse
import asyncio
from hashlib import file_digest
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes
from live_reconnect_grace_smoke import OBSERVATORY, manifest_digest, native_guest, status


REPO = Path(__file__).resolve().parents[1]
SEED = 142857
LEVEL = "Observitory_P"
ARMOR = "/Game/Classes/Armor_Items/Armor_ExpiditionLight.Armor_ExpiditionLight_C"
BAG = "/Game/Classes/Items/Random_GearBag_Starter.Random_GearBag_Starter_C"
NOTE = "/Game/Classes/Items/Key_Items/Note_Wanderer.Note_Wanderer_C"
STORM = "/Game/Classes/Weather/Storms/LightningStorm_BP.LightningStorm_BP_C"
SLOT = re.compile(r"SFCoop_[0-9a-f]{32}\Z")
WORKING_SLOT = re.compile(r"SFCoopLive_[0-9a-f]{32}\Z")
LOADOUT = {(ARMOR, "armor"), (BAG, "backpack"), (NOTE, "backpack")}
MERCY_RULES = {"extraction_mode": 0, "permadeath": 0, "replenish_loot": 1, "save_on_level_transition": 1,
               "can_save_in_menu": 0, "fail_contracts_on_death": 0, "items_kept_on_death": 0,
               "lose_slot_per_death": 0, "randomize_loot": 0, "randomize_npcs": 0, "drop_bone_pile": 0}


def verified_slot(save_directory: Path, frame: dict, *, character=None) -> dict:
    slot = frame.get("slot", "")
    if not SLOT.fullmatch(slot) or frame.get("map") != OBSERVATORY or frame.get("character_id") != character:
        raise AssertionError(frame)
    path = save_directory / f"{slot}.sav"
    if path.resolve() != path:
        raise AssertionError("The staged stock generation resolves outside its exact slot")
    with path.open("rb") as stream:
        if stream.read(4) != b"GVAS":
            raise AssertionError("The staged stock generation has an invalid GVAS header")
        stream.seek(0)
        size = os.fstat(stream.fileno()).st_size
        actual_hash = file_digest(stream, "sha256").hexdigest()
    if frame.get("size") != size or frame.get("sha256") != actual_hash:
        raise AssertionError({"frame": frame, "observed_size": size, "observed_sha256": actual_hash})
    return frame


def check_inventory(frame: dict, owner: str, expected):
    rows = frame.get("items", [])
    actual = {(item["class_path"], item["source"]) for item in rows}
    ids = {item["guid"] for item in rows}
    if (frame.get("owner") != owner or actual != expected
            or frame.get("offset") != 0 or frame.get("next_offset") is not None
            or frame.get("total") != len(expected) or len(rows) != len(expected) or len(ids) != len(rows)
            or any(not re.fullmatch(r"[0-9A-F]{32}", value) or value == "0" * 32 for value in ids)):
        raise AssertionError({"expected_owner": owner, "inventory": frame})
    return sorted(rows, key=lambda row: row["guid"])


async def verify(work: Path, vanilla_saved: Path) -> None:
    if os.name != "nt":
        raise OSError("The live Wanderer start test requires the staged Windows game")
    name = "wanderer-preset-" + uuid.uuid4().hex[:8]
    host_directory = work / "campaigns" / name
    guest_directory = work / "clients" / name
    host_run, guest_run = host_directory / "run", guest_directory / "run"
    host_saves = host_directory / "game-user" / "Saved" / "SaveGames"
    guest_saves = guest_directory / "game-user" / "Saved" / "SaveGames"
    host_run.mkdir(parents=True)
    guest_run.mkdir(parents=True)
    print(host_run, flush=True)
    primary, secondary = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--native-zones", "--native-events", "--native-guest")
    baseline = saved_hashes(vanilla_saved)
    if not baseline:
        raise ValueError("The original Saved directory must contain files")
    evidence = {"original_files": len(baseline), "original_manifest_sha256": manifest_digest(baseline)}
    processes = []

    async def spawn(kind, target, run, log, *arguments):
        with (run / log).open("wb") as output:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *target, *arguments, stdout=output, stderr=output, cwd=REPO,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        processes.append((process, target))
        return process

    async def command(*args):
        process = await asyncio.create_subprocess_exec(
            *cli, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=REPO,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode or not output:
            raise RuntimeError(f"{args[0]} failed: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is not True:
            raise RuntimeError(f"{args[0]} returned {reply}")
        return reply

    async def action(owner, verb, *args):
        return await command("action", *owner, verb, *args)

    async def wait(label, predicate, timeout=50):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            server, client = status(host_run), status(guest_run)
            if predicate(server, client):
                return server, client
            if "failed" in (server.get("state"), client.get("state")):
                break
            await asyncio.sleep(0.2)
        raise RuntimeError(label)

    def verify_world(server):
        game = server["game"]
        scene, seed = game["native_scene"], game["native_seed"]
        if (game.get("map") != OBSERVATORY or scene.get("active_map") != OBSERVATORY
                or scene.get("saved_level") != LEVEL or scene.get("rest_level") != LEVEL or scene.get("bed") != ""
                or scene.get("entrance") != "MainMenu" or scene.get("storm_class") != STORM
                or scene.get("instance_seed") != SEED or seed.get("state") != "verified"
                or seed.get("expected") != SEED or seed.get("observed") != SEED or seed.get("stock_stream") != SEED
                or game.get("native_wallet") != 23 or server.get("gameplay", {}).get("shared_wallet") != 23
                or any(game.get("native_rules", {}).get(key) != expected for key, expected in MERCY_RULES.items())):
            raise AssertionError({"game": game, "shared_wallet": server.get("gameplay", {}).get("shared_wallet")})
        return {"scene": scene, "seed": seed, "rules": game["native_rules"], "shared_wallet": game["native_wallet"]}

    def no_working_slot_writes():
        generated = [path.name for saves in (host_saves, guest_saves) for path in saves.glob("SFCoopLive_*.sav")]
        if generated:
            raise AssertionError({"staged_working_slot_files": generated})

    def checkpoints():
        for filename in ("campaign.json", "campaign.previous.json"):
            path = host_directory / "save" / filename
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            yield saved

    def paired(server, client):
        return (server.get("state") == "hosting" and client.get("state") == "connected"
                and server.get("link_connected") is True and client.get("link_connected") is True
                and client.get("gameplay_fresh") is True and client.get("host_session") == server.get("generation")
                and client.get("character_id") == character and client.get("zone_epoch") == server.get("zone_epoch")
                and native_guest(server, character))

    try:
        host = await spawn("host", primary, host_run, "host-first.txt", "--port", "0", *host_flags,
                           "--native-start-preset", "wanderer", "--native-world-seed", str(SEED))
        first, _ = await wait("The Wanderer host never exposed its verified startup checkpoint",
            lambda server, _: server.get("state") == "hosting"
            and server.get("game", {}).get("native_seed", {}).get("state") == "verified"
            and server.get("game", {}).get("native_fresh_campaign", {}).get("state") == "verified")
        first_generation, port = first["generation"], first["endpoint"]["port"]
        preset = first["game"]["native_fresh_campaign"]
        expected = {"state": "verified", "preset": "wanderer", "difficulty": "Mercy", "map": OBSERVATORY,
                    "instance_seed": SEED, "cash": 23, "debt": 3015220, "xp": 0, "backpack_count": 2,
                    "armor": ARMOR, "rest_level": LEVEL, "proof": "authoritative_pawn_adopted_stock_save"}
        if (any(preset.get(key) != value for key, value in expected.items())
                or not WORKING_SLOT.fullmatch(preset.get("working_slot", ""))
                or any(type(preset.get(key)) is not int or not 0 < preset[key] < 2 ** 31
                       for key in ("world_seed", "artifact_seed", "storm_seed"))
                or len({preset[key] for key in ("instance_seed", "world_seed", "artifact_seed", "storm_seed")}) != 4):
            raise AssertionError(preset)
        campaign = first["campaign"]
        durable_preset = {key: value for key, value in preset.items() if key not in ("state", "working_slot")}
        if (campaign["world"].get("native_start_preset") != durable_preset or campaign["characters"]
                or campaign["leases"] or first["game"].get("remote_clients") != 0):
            raise AssertionError({"preset": durable_preset, "campaign": campaign, "game": first["game"]})
        public_frame = verified_slot(host_saves, campaign["world"]["native_host_save"])
        startup_checkpoints = {saved["revision"]: saved for saved in checkpoints()}
        events = (await command("events", *primary, "--tail", "25"))["events"]
        names = [event.get("event") for event in events]
        if (names.count("native_start_preset_committed") != 1 or "listening" not in names
                or names.index("native_start_preset_committed") >= names.index("listening")):
            raise AssertionError({"startup_events": names})
        committed = events[names.index("native_start_preset_committed")]
        committed_revision = committed.get("revision")
        committed_checkpoint = startup_checkpoints.get(committed_revision, {})
        if (type(committed_revision) is not int or not 0 < committed_revision <= campaign["revision"]
                or committed.get("seed") != SEED or committed_checkpoint.get("characters") != {}
                or committed_checkpoint.get("world", {}).get("native_start_preset") != durable_preset):
            raise AssertionError({"event": committed, "campaign_revision": campaign["revision"]})
        initial_frame = verified_slot(host_saves, committed_checkpoint["world"]["native_host_save"])
        no_working_slot_writes()
        evidence["before_invite"] = {"preset": preset, "world": verify_world(first), "frame": initial_frame,
                                     "revision": committed_revision, "committed_before_listen": True}

        host_before = (await action(primary, "native-inventory-list"))["native_inventory"]
        actual_loadout = check_inventory(host_before, "host", LOADOUT)
        if (host_saves / f"{public_frame['slot']}.sav").is_file():
            verified_slot(host_saves, public_frame)
        invitation = await command("invite", *primary, "--name", "Wanderer smoke guest", "--character", "Guest defaults")
        character = invitation["character_id"]
        await spawn("join", secondary, guest_run, "guest.txt", "--headless", "--native-zones",
                    "--invite", invitation["invite_file"])
        joined, _ = await wait("An authenticated guest did not bind its independent native save", paired)
        if check_inventory((await action(secondary, "native-inventory-list"))["native_inventory"], character, set()):
            raise AssertionError("The guest inherited the host's starter inventory")
        if check_inventory((await action(primary, "native-inventory-list"))["native_inventory"], "host", LOADOUT) != actual_loadout:
            raise AssertionError("The first guest changed an exact stock starter item")
        evidence["first_party"] = {"character_id": character, "guest": native_guest(joined, character),
                                   "starter_items": actual_loadout, "guest_default_items": []}
        no_working_slot_writes()

        await action(primary, "stop")
        await asyncio.wait_for(host.wait(), 15)
        stopped = status(host_run)["campaign"]
        restart_frame = verified_slot(host_saves, stopped["world"]["native_host_save"])
        guest_state, = (entry["state"] for entry in stopped["characters"] if entry["character_id"] == character)
        guest_frame = verified_slot(host_saves, guest_state["native_guest_save"], character=character)
        if guest_frame["slot"] == restart_frame["slot"]:
            raise AssertionError("The stopped campaign shared one immutable slot between both owners")
        no_working_slot_writes()
        await spawn("host", primary, host_run, "host-restart.txt", "--port", str(port), *host_flags)
        restarted, _ = await wait("The restarted host did not verify the stock stream on its original port",
            lambda server, _: server.get("state") == "hosting" and server.get("generation") != first_generation
            and server.get("game", {}).get("native_seed", {}).get("state") == "verified")
        verified_slot(host_saves, restart_frame)
        verified_slot(host_saves, guest_frame, character=character)
        events = (await command("events", *primary, "--tail", "30"))["events"]
        restores = [event for event in events if event.get("event") == "native_host_save_restored"
                    and event.get("slot") == restart_frame["slot"]]
        if len(restores) != 1:
            raise AssertionError({"expected_restore_slot": restart_frame, "restore_events": restores})
        restored = restores[0]
        if (restarted["endpoint"]["port"] != port or restored.get("state") != "restored"
                or restored.get("seed") != SEED or restored.get("backpack_count") != 2
                or restarted["campaign"]["world"].get("native_start_preset") != durable_preset
                or restarted["game"].get("native_fresh_campaign")):
            raise AssertionError({"expected_port": port, "restoration": restored, "restart": restarted})
        returned, _ = await wait("The authenticated guest did not rejoin the restored native campaign", paired)
        if check_inventory((await action(primary, "native-inventory-list"))["native_inventory"], "host", LOADOUT) != actual_loadout:
            raise AssertionError("The same-port restart changed the exact stock starter GUIDs or rows")
        check_inventory((await action(secondary, "native-inventory-list"))["native_inventory"], character, set())
        no_working_slot_writes()
        evidence["same_port_restart"] = {"port": port, "fresh_generation": returned["generation"],
                                         "world": verify_world(returned), "native_host_save": restored,
                                         "restored_host_frame": restart_frame, "previous_guest_frame": guest_frame,
                                         "guest_inventory_independent": True}
        evidence["verified"] = True
    except BaseException as error:
        evidence["verified"] = False
        evidence["failure"] = str(error)
        host_status = status(host_run)
        game = host_status.get("game", {})
        evidence["last_frame"] = {"host": host_status.get("state"), "guest": status(guest_run).get("state"),
                                  "preset": game.get("native_fresh_campaign"), "seed": game.get("native_seed"),
                                  "saved": game.get("native_host_save"), "scene": game.get("native_scene")}
        raise
    finally:
        for process, target in reversed(processes):
            if process.returncode is not None:
                continue
            try:
                await action(target, "stop")
                await asyncio.wait_for(process.wait(), 15)
            except (OSError, RuntimeError, TimeoutError):
                if process.returncode is None:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await asyncio.wait_for(killer.wait(), 12)
                    await asyncio.wait_for(process.wait(), 12)
        unchanged = saved_hashes(vanilla_saved) == baseline
        stray_slots = [path.name for saves in (host_saves, guest_saves) for path in saves.glob("SFCoopLive_*.sav")]
        evidence["original_saves_verified"] = unchanged
        evidence["working_slot_files"] = stray_slots
        evidence["verified"] = unchanged and not stray_slots and evidence.get("verified", False)
        (host_run / "live-wanderer-preset-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if not unchanged or stray_slots:
            raise AssertionError({"original_saves_verified": unchanged, "working_slot_files": stray_slots})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="directory containing a prepared stage")
    parser.add_argument("--vanilla-saved", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "SilverFish" / "Saved")
    options = parser.parse_args()
    if not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
