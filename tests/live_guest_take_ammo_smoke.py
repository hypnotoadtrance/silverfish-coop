"""Exercise Take Ammo across registered magazines in an isolated game."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_guest_held_fire_smoke import wait_event
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from tools.silverfish import native_host_save, native_inventory
from tools.silverfish.control import submit
from tools.silverfish.guest_weapon_profiles import FIREARMS
from tools.silverfish.travel import OBSERVATORY


async def verify(work: Path, original: Path):
    name = "take-ammo-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    host, guest = campaign / "run", work / "clients" / name / "run"
    host.mkdir(parents=True)
    print(host, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    hashes = saved_hashes(original)
    jobs, proof = [], {"verified": False, "weapons": []}

    async def launch(owner, *arguments):
        with (host / f"{arguments[0]}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(
                *cli, *arguments, cwd=REPO, stdout=stream, stderr=stream, creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, process))

    async def action(owner, verb, **values):
        reply = await submit(owner, verb, **values)
        if not reply.get("ok"):
            raise RuntimeError(f"{verb} failed with {reply.get('error')}")
        return reply

    try:
        await launch(host, "host", "--campaign", name, "--port", "0", "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--native-expanded-zones", "--native-shop-view",
                     "--start-zone", OBSERVATORY)
        await wait(host, lambda state: state.get("state") == "hosting")
        invitation = await asyncio.create_subprocess_exec(
            *cli, "invite", "--campaign", name, "--name", "Stock Ammo Tester", "--character", "Guest",
            cwd=REPO, stdout=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, _ = await invitation.communicate()
        if invitation.returncode:
            raise RuntimeError("Could not create a local guest invitation")
        invite = json.loads(output)
        await launch(guest, "join", "--profile", name, "--invite", invite["invite_file"], "--headless",
                     "--native-zones", "--native-guest-inventory-view")
        await wait(guest, lambda state: state.get("state") == "connected" and state.get("game", {}).get("save_scope"))
        await wait(host, lambda state: any(lease.get("state") == "active" and lease.get("character_id") == invite["character_id"]
                   for lease in state.get("campaign", {}).get("leases", ())))
        host_inventory = (await action(host, "native-inventory-list"))["native_inventory"]
        profiles = {weapon.kind: weapon.magazine for weapon in FIREARMS}
        cases = ("smg-standard", "pistol", "break-action-50", "1911", "sg-71-a", "fennec-57")
        for kind in cases:
            profile = profiles[kind]
            prior_items = (await action(guest, "native-inventory-list"))["native_inventory"]["total"]
            guid = uuid.uuid4().hex.upper()
            fixture = "smg-standard-full" if kind == "smg-standard" else kind
            await action(host, "native-guest-weapon-seed", target="guest", item_guid=guid, kind=fixture)
            initial = (await action(guest, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
            assert initial["status"] == "backpack" and initial["stored_ammo"] > 0, initial
            before = initial["stored_ammo"]
            if kind == "smg-standard":
                assert before == profile.capacity == 30, initial
            ammo = profile.ammo.item_path
            await wait_event(guest, "native_guest_ui_projected", lambda row: row.get("items", 0) >= prior_items + 1)
            await action(guest, "native-inventory-view-fixture")
            await wait(guest, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
            clicked = await action(guest, "native-inventory-stock-use-click-fixture", target=guid + "|take-ammo")
            assert (clicked["stock_action"]["guid"], clicked["stock_action"]["count"]) == (guid, before), clicked
            key = native_inventory.TAKE_AMMO_KEY
            event = await wait_event(guest, "native_stock_inventory_action", lambda row: row.get(key, {}).get("source_guid") == guid)
            received = event[key]
            assert (received["owner"], received["source_guid"], received["remaining"], received["split"], received["class_path"]) == (
                invite["character_id"], guid, 0, before, ammo), received
            assert "empty weapon and recovered ammo" in event["stock_inventory_proof"], event
            items = (await action(guest, "native-inventory-list"))["native_inventory"]["items"]
            extra, = (row for row in items if row["guid"] == received["new_guid"])
            assert (extra["quantity"], extra["class_path"], extra["source"]) == (before, ammo, "backpack"), extra
            after = (await action(guest, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
            assert (after["guid"], after["stored_ammo"], after["status"]) == (guid, 0, "backpack"), after
            replay = await action(guest, native_inventory.TAKE_AMMO, target=f"{guid}|{before}", operation_id=event["operation_id"])
            assert replay[key] == received
            repeated = await submit(guest, native_inventory.TAKE_AMMO, target=f"{guid}|{before}")
            assert not repeated["ok"] and repeated["error"] == "native_inventory_take_ammo_refused_loaded_state", repeated
            assert (await action(guest, "native-inventory-list"))["native_inventory"]["items"] == items
            assert (await action(host, "native-inventory-list"))["native_inventory"] == host_inventory
            native_host_save.verify_checkpoint(campaign / "game-user",
                json.loads((campaign / "save/campaign.json").read_text(encoding="utf-8")))
            await action(guest, "native-inventory-view-fixture")
            proof["weapons"].append({"kind": kind, "loaded_before": before, "receipt": received,
                                     "duplicate_refusal": repeated["error"], "view": event["stock_inventory_proof"]})
        underbarrel = uuid.uuid4().hex.upper()
        await action(host, "native-guest-weapon-seed", target="guest", item_guid=underbarrel)
        inventory_before = (await action(guest, "native-inventory-list"))["native_inventory"]
        loaded = (await action(guest, "native-guest-weapon-inspect", target=underbarrel))["native_guest_weapon"]
        assert loaded["status"] == "backpack" and loaded["stored_ammo"] > 0, loaded
        denied = await submit(guest, native_inventory.TAKE_AMMO, target=f"{underbarrel}|{loaded['stored_ammo']}")
        assert not denied["ok"] and denied["error"] == "native_inventory_take_ammo_refused_loaded_state", denied
        assert (await action(guest, "native-inventory-list"))["native_inventory"] == inventory_before
        proof["underbarrel_refusal"] = denied["error"]
        proof["verified"] = True
    finally:
        for owner, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await action(owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 10)
        proof["original_saves_verified"] = saved_hashes(original) == hashes
        (host / "live-guest-take-ammo-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        assert proof["original_saves_verified"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    arguments = parser.parse_args()
    asyncio.run(verify(arguments.work.resolve(strict=True), arguments.vanilla_saved.resolve(strict=True)))
