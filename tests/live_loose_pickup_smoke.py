"""Verify a level item that has no placement entry in the co-op catalog."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.silverfish import native_authored_pickup, native_host_save, native_loose_pickup as loose
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY

DEFAULT_ACTOR = f"{OBSERVATORY}.Observitory_P:PersistentLevel.Eqp_Bolt_C_1"


async def verify(work, original, actor, restart=False, held_pistol=False, nearby=False, memory_escrow=False):
    area = loose.actor_map(actor)
    assert area and all(actor != row[1].partition(" ")[2] for row in native_authored_pickup.SOURCES.values())
    target = f"loose:{actor}"
    profile = f"loose-stock-item-{uuid.uuid4().hex[:8]}"
    host = ("--campaign", profile)
    guest = ("--profile", profile)
    hrun = work / "campaigns" / profile / "run"
    grun = work / "clients" / profile / "run"
    hrun.mkdir(parents=True)
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(original)
    processes = []
    print(hrun, flush=True)

    async def start(label, controller, *args):
        with (hrun / f"{label}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, err = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 0, err.decode()
        return json.loads(out)

    async def action(run, opcode, **kwargs):
        response = await submit(run, opcode, **kwargs)
        if not response.get("ok"):
            raise RuntimeError(f"{opcode}: {response}")
        return response

    async def stop_all():
        for process, run in reversed(processes):
            if process.returncode is None:
                try:
                    await submit(run, "stop", timeout=15)
                    await asyncio.wait_for(process.wait(), 12)
                except (OSError, TimeoutError, ValueError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()

    def item(frame):
        return frame.get("game", {}).get(loose.KEY, {}).get(actor, {})

    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    try:
        await start("host", hrun, "host", *host, "--port", "0", *host_flags, "--start-zone", area)
        await wait(hrun, lambda state: state.get("state") == "hosting" and state.get("game", {}).get(loose.CAPABILITY) == 1)
        invitation = await command("invite", *host, "--name", "Loose item tester", "--character", "Loose item tester")
        await start("guest", grun, "join", *guest, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        await wait(grun, lambda state: state.get("state") == "connected" and state.get("game", {}).get(loose.CAPABILITY) == 1)
        await wait(hrun, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        opening = status(hrun)
        location = item(opening).get("location")
        origin = opening["game"]["pawns"]["local"]["location"]
        out_of_reach = None
        if not location or math.dist(location, origin) > 250:
            out_of_reach = await submit(hrun, loose.CLAIM, target=target)
            assert out_of_reach.get("ok") is False, out_of_reach
        for role in ("host", "guest"):
            await action(hrun, loose.APPROACH, target=f"{role}@{target}")
        if out_of_reach:
            assert out_of_reach.get("error") == "authored_pickup_exact_authority_sight_required", out_of_reach
        if held_pistol:
            weapon = uuid.uuid4().hex.upper()
            await command("action", *host, "native-guest-weapon-seed", "--target", "guest",
                          "--kind", "1911", "--item-guid", weapon)
            await command("action", *guest, "native-guest-weapon-equip", "--target", weapon)
            await wait(hrun, lambda state: any(
                entry.get("weapon_meshes") for entry in state.get("game", {}).get("native_player_avatars", {}).values()))
        visible = [item(await wait(run, lambda state: item(state).get("state") == "available")) for run in (hrun, grun)]
        assert visible[0]["class_path"] == visible[1]["class_path"]
        claimant, other = (hrun, grun) if nearby else (grun, hrun)
        owner = "host" if nearby else invitation["character_id"]
        await action(claimant, loose.NEARBY_FIXTURE if nearby else loose.FIXTURE, target=target)
        await wait(hrun, lambda state: item(state).get("state") == "claimed", timeout=10)
        await wait(grun, lambda state: item(state).get("state") == "claimed", timeout=10)
        events = [json.loads(line) for line in (hrun / "events.jsonl").read_text().splitlines()]
        applied, = [event for event in events if event["event"] == "native_loose_pickup_claimed" and event["actor"] == actor]
        assert applied["owner"] == owner
        if memory_escrow:
            assert applied.get("escrow") == "memory", applied.get("escrow")
        replay = await action(claimant, loose.CLAIM, target=target, operation_id=applied["operation_id"])
        assert replay[loose.PROOF]["guid"] == applied["guid"]
        second = await submit(other, loose.CLAIM, target=target)
        assert second.get("error") == "loose_pickup_already_claimed", second
        inventory = await action(claimant, "native-inventory-list")
        row, = [item for item in inventory["native_inventory"]["items"] if item["guid"] == applied["guid"]]
        assert row["class_path"] == visible[0]["class_path"], row
        campaign = hrun.parent
        saved = json.loads((campaign / "save" / "campaign.json").read_bytes())
        native_host_save.verify_checkpoint(campaign / "game-user", saved)
        assert saved["world"]["party"][loose.STATE][actor] == replay[loose.PROOF]
        assert saved_hashes(original) == originals
        proof = dict(visible=visible, ownership=replay[loose.PROOF], duplicate=second["error"], backpack=row,
                     original_files_verified=len(originals), out_of_reach=out_of_reach, held_pistol=held_pistol,
                     nearby=nearby, escrow=applied.get("escrow"))
        if restart:
            assert applied["mode"] in (1, 2)
            old = status(hrun)
            port = old["endpoint"]["port"]
            await stop_all()
            await start("host-restart", hrun, "host", *host, "--port", str(port), *host_flags)
            restored = await wait(hrun, lambda state: state.get("state") == "hosting"
                                  and state.get("generation") != old["generation"])
            assert restored["endpoint"]["port"] == port
            assert restored["game"]["map"] == area and actor in restored["gameplay"].get(loose.STATE, ())
            renewed = await command("action", *host, "refresh-invite", "--target", invitation["guest_id"])
            assert renewed["character_id"] == invitation["character_id"]
            await start("guest-restart", grun, "join", *guest, "--invite", renewed["invite_file"], "--headless", "--native-zones")
            resumed = await wait(grun, lambda state: state.get("gameplay_fresh") and state.get("host_session") == restored["generation"]
                                 and actor in state.get("gameplay", {}).get(loose.STATE, ())
                                 and item(state).get("state") != "available")
            assert resumed["character_id"] == invitation["character_id"]
            await wait(hrun, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            backpack = await action(grun, "native-inventory-list")
            assert [entry for entry in backpack["native_inventory"]["items"] if entry["guid"] == row["guid"]] == [row]
            after = await action(grun, loose.CLAIM, target=target, operation_id=applied["operation_id"])
            assert after[loose.PROOF] == proof["ownership"]
            proof["restart"] = {"port": port, "character": resumed["character_id"], "retained_item": row["guid"]}
        (hrun / "loose-pickup-verification.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print(json.dumps(proof))
    finally:
        await stop_all()
        assert saved_hashes(original) == originals


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--actor", default=DEFAULT_ACTOR)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--memory-escrow", action="store_true", help="require an in-memory transfer and durable stock readback")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--held-pistol", action="store_true", help="claim the loose item while holding a stock pistol")
    modes.add_argument("--nearby", action="store_true", help="claim the item through the host's stock Nearby Items menu")
    args = parser.parse_args()
    if args.nearby and args.restart:
        parser.error("The restart check follows the guest claim")
    asyncio.run(verify(args.work.resolve(), args.vanilla_saved, args.actor, args.restart, args.held_pistol, args.nearby,
                       args.memory_escrow))
