"""Verify the stock LookTrace and Interact path on three authored crate maps."""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.silverfish import native_host_save, native_loot
from tools.silverfish.authored_loot import CRATES, full_name
from tools.silverfish.travel import CISTERN, CITY, RAIL_YARD


SCENES = {
    "city": (CITY, ("LootCrate_Small_BP_C_5", "LootCrate_Small_BP_C_7")),
    "rail-yard": (RAIL_YARD, ("LootCrate_Small_BP_C_3",)),
    "cistern": (CISTERN, ("LootCrate_Small_BP_C_7",)),
}


def clone_baseline(work: Path, source: str, campaign: Path, area: str, targets: list[str]) -> int:
    campaigns = (work / "campaigns").resolve(strict=True)
    origin = (campaigns / source).resolve(strict=True)
    if origin.parent != campaigns or origin.name != source or not source.startswith("crate-"):
        raise ValueError("Baseline must be an existing crate smoke campaign in this lab")
    save = (origin / "save" / "campaign.json").resolve(strict=True)
    profile = (origin / "game-user").resolve(strict=True)
    if not (save.is_relative_to(origin) and profile.parent == origin):
        raise ValueError("Baseline save paths must remain inside the crate smoke campaign")
    data = json.loads(save.read_text(encoding="utf-8"))
    world = data["world"]
    seed = world.get("native_observation", {}).get("native_scene", {}).get("instance_seed")
    rolls = world["party"].get(native_loot.STATE_KEY, {})
    if (world.get("map") != area or not world.get("native_host_save") or type(seed) not in (int, float)
            or not 1 <= seed <= 999_999 or int(seed) != seed or any(target in rolls for target in targets)):
        raise ValueError("Baseline must have a verified stock save on this map before the target rolls")
    native_host_save.verify_checkpoint(profile, data)
    destination = campaign / "game-user"
    for frame in native_host_save.references(data):
        slot = frame["slot"]
        src = native_host_save.slot_path(profile, slot)
        dst = native_host_save.slot_path(destination, slot)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    native_host_save.verify_checkpoint(destination, data)
    (campaign / "save").mkdir()
    shutil.copyfile(save, campaign / "save" / "campaign.json")
    return int(seed)


def stock_input_result(run: Path, crate: str, sequence: int) -> dict | None:
    path = run / "events.jsonl"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (row.get("crate") == crate and row.get("sequence") == sequence and row.get("event") in
                ("native_stock_crate_interaction", "native_stock_crate_interaction_rejected")):
            return row
    return None


async def exercise(work: Path, name: str, original_save: Path | None, baseline: str | None = None) -> None:
    area, actors = SCENES[name]
    targets = [full_name(area, actor) for actor in actors]
    assert all(CRATES.get(crate) == area for crate in targets)
    key = f"crate-{name}-{uuid.uuid4().hex[:8]}"
    campaign = work / "campaigns" / key
    run = campaign / "run"
    guest_run = work / "clients" / key / "run"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host, guest = ("--campaign", key), ("--profile", key)
    originals = saved_hashes(original_save) if original_save else None
    evidence = {"map": area, "stock_interactions": []}
    processes = []
    print(run, flush=True)

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, error = await asyncio.wait_for(process.communicate(), timeout=40)
        reply = json.loads(out) if out else {}
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{arguments}: {reply or error.decode(errors='replace')}")
        return reply

    async def start(label, controller, *arguments):
        run.mkdir(parents=True, exist_ok=True)
        with (run / (label + ".txt")).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *arguments, cwd=REPO, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))

    def scene(status):
        game = status.get("game", {})
        return game.get("native_loot_crates", {}) if game.get("map") == area else {}

    def status_of(status, crate):
        return scene(status).get(crate, {}).get("state")

    def public_roll(status, crate):
        return status.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate)

    try:
        seed = clone_baseline(work, baseline, campaign, area, targets) if baseline else None
        if baseline:
            evidence["baseline_seed"] = seed
        start_zone = () if baseline else ("--start-zone", area)
        await start("host", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                    "--native-guest", "--native-zones", "--native-expanded-zones", *start_zone)
        opening = await wait(run, lambda state: state.get("state") == "hosting"
                             and all(status_of(state, crate) == "ready" for crate in targets))
        if baseline:
            live_seed = opening["game"]["native_scene"]["instance_seed"]
            assert live_seed == seed, (seed, live_seed)
        invitation = await command("invite", *host, "--name", f"{name} crate runner", "--character", "Crate runner")
        await start("guest", guest, "join", *guest, "--headless", "--invite", invitation["invite_file"])
        ready = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("gameplay_fresh")
                           and all(status_of(state, crate) == "ready" for crate in targets))
        pawn = ready["game"]["pawns"]["local"]["name"]
        await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        character = invitation["character_id"]
        await command("action", *host, "native-loot-seed", "--target", targets[0])

        for position, crate in enumerate(targets):
            await command("action", *host, "native-loot-approach", "--target", crate)
            pressed = (await command("action", *guest, "native-loot-crate-fixture", "--target", crate))["native_loot_crate_interaction"]
            assert (pressed["map"], pressed["crate"], pressed["entry"], pressed["pawn"]) == (area, crate, "fixture", pawn), pressed
            await wait(guest_run, lambda _: stock_input_result(guest_run, crate, pressed["sequence"]) is not None)
            result = stock_input_result(guest_run, crate, pressed["sequence"])
            if result["event"] != "native_stock_crate_interaction":
                evidence["failure"] = result
                raise RuntimeError(result["reason"])
            committed = await wait(run, lambda state: public_roll(state, crate) and status_of(state, crate) == "exhausted")
            replicated = await wait(guest_run, lambda state: public_roll(state, crate) and status_of(state, crate) == "exhausted")
            roll = public_roll(committed, crate)
            assert public_roll(replicated, crate) == roll and roll["opener"] == character, roll
            assert roll["lucky_looter"] and roll["trait_calls"] >= 1, roll
            for sibling in targets[position + 1:]:
                assert status_of(committed, sibling) == status_of(replicated, sibling) == "ready", sibling
                assert public_roll(committed, sibling) is None, sibling

            claim = None
            if roll["items"]:
                target = f"{roll['items'][0]['guid']}@{crate}"
                request = ("action", *guest, "native-loot-claim", "--target", target, "--operation-id", uuid.uuid4().hex)
                claim = (await command(*request))["native_loot_claim"]
                assert claim["claimed_by"] == character and claim["guid"] == roll["items"][0]["guid"], claim
                assert (await command(*request))["native_loot_claim"] == claim
                denied = await command("action", *host, "native-loot-claim", "--target", target, expected=False)
                assert denied["error"] == "native_loot_already_claimed", denied
                roll["items"][0]["claimed_by"] = character
            replay = (await command("action", *host, "native-loot-open", "--target", crate))["native_loot_roll"]
            assert replay == roll, replay
            saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
            native_host_save.verify_checkpoint(campaign / "game-user", saved)
            durable = saved["world"]["party"][native_loot.STATE_KEY][crate]
            assert native_loot.public_roll(durable) == roll, durable
            evidence["stock_interactions"].append({"receipt": pressed, "roll": roll, "claim": claim})

        rolls = evidence["stock_interactions"]
        assert len({entry["roll"]["id"] for entry in rolls}) == len(targets)
    finally:
        for process, controller in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *controller, "stop")
                    await asyncio.wait_for(process.wait(), timeout=10)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=10)
        if originals is not None:
            evidence["original_files_verified"] = len(originals)
            assert saved_hashes(original_save) == originals
        run.mkdir(parents=True, exist_ok=True)
        (run / "live-authored-crate-expansion-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


async def main(arguments):
    for name in arguments.area or SCENES:
        await exercise(arguments.work.resolve(), name, arguments.vanilla_saved, arguments.baseline)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--area", action="append", choices=SCENES, help="play this scene instead of running all three")
    parser.add_argument("--vanilla-saved", type=Path)
    parser.add_argument("--baseline", help="resume a copy of a matching crate smoke checkpoint in this lab")
    arguments = parser.parse_args()
    if arguments.baseline and (not arguments.area or len(arguments.area) != 1):
        parser.error("--baseline requires exactly one --area")
    asyncio.run(main(arguments))
