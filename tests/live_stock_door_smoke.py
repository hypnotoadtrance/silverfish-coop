"""Verify an authored hinged door on a staged Direct IP host and guest."""

import argparse
import asyncio
import json
import math
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import saved_hashes

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.silverfish import native_stock_door as doors
from tools.silverfish import native_host_save
from tools.silverfish.travel import CROSSROADS

WOOD_DOOR = f"Door_Wood_04_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Door_Wood_04_BP_C_1719"
METAL_DOOR = f"Door_Metal_03_BP_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Door_Metal_03_Window_BP_C_16"


async def exercise(work: Path, original: Path, actor: str, tool: str | None = None) -> None:
    name = f"stock-door-{tool or 'host'}-{uuid.uuid4().hex[:8]}"
    host, guest = ("--campaign", name), ("--profile", name)
    hrun = work / "campaigns" / name / "run"
    grun = work / "clients" / name / "run"
    hrun.mkdir(parents=True)
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    originals = saved_hashes(original)
    assert originals
    processes = []
    print(hrun, flush=True)

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        out, err = await asyncio.wait_for(process.communicate(), timeout=26)
        reply = json.loads(out) if out else {}
        if expected is not None and reply.get("ok") is not expected:
            raise RuntimeError(f"{args}: {reply or err.decode(errors='replace')}")
        return reply

    async def launch(label, controller, *args):
        with (hrun / f"{label}.txt").open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, stdout=stream, stderr=stream,
                creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, controller))

    def door(state):
        return state.get("game", {}).get(doors.KEY, {}).get(actor, {})

    def changed_angle(a, b):
        return max(abs((a[name] - b[name] + 180) % 360 - 180) for name in ("pitch", "yaw", "roll"))

    def trace_target(report, query):
        hx, hy, _ = report["panel_location"]
        kx, ky, kz = report["knob_location"]
        length = math.hypot(kx - hx, ky - hy)
        assert 40 < length < 200, report
        x, y = (hx + kx) / 2, (hy + ky) / 2
        dx, dy = 70 * (ky - hy) / length, -70 * (kx - hx) / length
        return f"{actor}@{query}@{x + dx},{y + dy},{kz}@{x - dx},{y - dy},{kz}"

    async def panel_blocks(controller, target):
        result = await command("action", *controller, doors.TRACE_FIXTURE, "--target", target)
        return result[doors.TRACE_RESULT]["panel"]

    async def approach_and_input(action):
        attempts = []
        for side in (0, 1, 2, 3):
            approach = await command("action", *host, doors.STAGE_FIXTURE, "--target", f"guest@{actor}", "--amount", str(side),
                                     expected=None)
            if not approach.get("ok"):
                attempts.append(approach)
                continue
            await asyncio.sleep(.7)
            result = await command("action", *guest, action, "--target", actor, expected=None)
            if result.get("ok"):
                return result
            attempts.append(result)
        raise RuntimeError(f"No fixture approach reached the stock door knob: {attempts}")

    def tool_event(sequence):
        events = grun / "events.jsonl"
        names = (f"native_guest_door_{tool}_input", f"native_guest_door_{tool}_input_rejected")
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("event") in names and row.get("sequence") == sequence:
                return row

    async def stop(process, controller):
        if process.returncode is None:
            try:
                await command("action", *controller, "stop", expected=None)
                await asyncio.wait_for(process.wait(), timeout=12)
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()

    try:
        await launch("host", host, "host", *host, "--port", "0", "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--native-expanded-zones", "--start-zone", CROSSROADS)
        original_host = await wait(hrun, lambda state: state.get("state") == "hosting" and door(state).get("locked") is True)
        invite = await command("invite", *host, "--name", "Door runner", "--character", "Door runner")
        guest_flags = ("--native-guest-primary-input",) if tool else ()
        await launch("guest", guest, "join", *guest, "--invite", invite["invite_file"], "--headless", *guest_flags)
        original_guest = await wait(grun, lambda state: state.get("state") == "connected" and door(state).get("locked") is True)
        assert doors.matches(door(original_guest), door(original_host))
        for query in (0, 2):
            trace = trace_target(door(original_host), query)
            if await panel_blocks(host, trace):
                break
        else:
            raise RuntimeError("The reference line did not hit the stock closed panel")
        assert await panel_blocks(guest, trace)
        await wait(hrun, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        await command("action", *guest, doors.FIXTURE, "--target", actor)
        await asyncio.sleep(1)
        probe = await command("status", *guest, expected=None)
        assert doors.matches(door(probe), door(original_host)), door(probe)
        locked = await command("action", *guest, doors.ACTION, "--target", actor, expected=False)
        assert locked["error"] == "stock_door_locked", locked

        pry_evidence = {}
        if tool:
            tool_guid = uuid.uuid4().hex.upper()
            before, after = (3, 2) if tool == "blowtorch" else (-1, -1)
            weapon = "native-guest-weapon"
            await command("action", *host, weapon + "-seed", "--target", "guest", "--item-guid", tool_guid, "--kind", tool)
            equipped = (await command("action", *guest, weapon + "-equip", "--target", tool_guid))["native_guest_weapon"]
            assert equipped["kind"] == tool and equipped["stored_ammo"] == equipped["actor_ammo"] == before, equipped
            out_of_range = await command("action", *guest, doors.PRY_ACTIONS[tool], "--target", f"{tool_guid}@{actor}", expected=False)
            assert out_of_range["error"] in ("stock_blowtorch_out_of_range", "stock_crowbar_out_of_reach"), out_of_range
            if tool == "crowbar" and actor == WOOD_DOOR:
                too_strong = await command("action", *guest, doors.PRY_ACTIONS[tool], "--target",
                                           f"{tool_guid}@{METAL_DOOR}", expected=False)
                assert too_strong["error"] == "stock_crowbar_lock_too_strong", too_strong
                pry_evidence["stronger_lock"] = too_strong["error"]
            await wait(grun, lambda state: state.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == tool_guid)
            stock = (await approach_and_input("native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
            await wait(grun, lambda _: tool_event(stock["sequence"]) is not None)
            applied = tool_event(stock["sequence"])
            assert applied["event"] == f"native_guest_door_{tool}_input", applied
            row = applied[doors.PRY_RESULT]
            assert (row["actor"], row["kind"], row["guid"], row["before"], row["after"]) == (actor, tool, tool_guid, before, after)
            assert row["broken"] and not row["locked"], row
            campaign_dir = hrun.parent
            snapshot = json.loads((campaign_dir / "save" / "campaign.json").read_bytes())
            native_host_save.verify_checkpoint(campaign_dir / "game-user", snapshot)
            receipts = snapshot["world"]["party"]["receipts"][invite["character_id"]]
            assert any(receipt["id"] == applied["operation_id"] and receipt["result"] == {doors.PRY_RESULT: row}
                       for receipt in receipts)
            pry_evidence.update(stock_tool=stock, tool_transaction=applied, out_of_range=out_of_range["error"])
        else:
            await command("action", *host, doors.FIXTURE, "--target", actor)
        unlocked = await wait(hrun, lambda state: door(state).get("broken") and door(state).get("locked") is False
                              and door(state).get("open") is True and changed_angle(door(state), door(original_host)) > 80)
        await wait(grun, lambda state: doors.matches(door(state), door(unlocked)))
        assert not await panel_blocks(host, trace) and not await panel_blocks(guest, trace)

        await approach_and_input(doors.INPUT_FIXTURE)

        closed = await wait(hrun, lambda state: door(state).get("open") is False
                            and changed_angle(door(state), door(original_host)) < .05)
        closed_peer = await wait(grun, lambda state: doors.matches(door(state), door(closed)))
        actions = (await command("events", *guest, "--tail", "100", expected=None))["events"]
        [receipt] = [event for event in actions if event.get(doors.RESULT, {}).get("actor") == actor
                     and event.get("event") == "local_interaction"]
        replay = await command("action", *guest, doors.ACTION, "--target", actor, "--operation-id", receipt["operation_id"])
        assert replay[doors.RESULT] == receipt[doors.RESULT] and replay[doors.RESULT]["open"] is False
        assert door(closed)["collision"] == door(closed_peer)["collision"] in (3, 5)
        assert math.dist(door(closed)["knob_location"], door(closed_peer)["knob_location"]) < 2
        assert await panel_blocks(host, trace) and await panel_blocks(guest, trace)
        port = original_host["endpoint"]["port"]
        await command("action", *host, "checkpoint")
        for process, controller in reversed(processes):
            await stop(process, controller)
        await launch("host-restart", host, "host", *host, "--port", str(port), "--headless", "--fixtures", "--native-events",
                     "--native-guest", "--native-zones", "--native-expanded-zones")
        restored = await wait(hrun, lambda state: state.get("state") == "hosting"
                              and state.get("generation") != original_host["generation"]
                              and doors.matches(door(state), door(closed)))
        next_invite = await command("action", *host, "refresh-invite", "--target", invite["guest_id"])
        await launch("guest-restart", guest, "join", *guest, "--invite", next_invite["invite_file"], "--headless", *guest_flags)
        restored_peer = await wait(grun, lambda state: state.get("state") == "connected"
                                   and state.get("host_session") == restored["generation"]
                                   and state.get("character_id") == invite["character_id"]
                                   and doors.matches(door(state), door(restored)))
        assert restored["endpoint"]["port"] == port
        if tool:
            again = await command("action", *guest, doors.PRY_ACTIONS[tool], "--target", f"{tool_guid}@{actor}",
                                  "--operation-id", applied["operation_id"])
            assert again[doors.PRY_RESULT] == row
            inventory = (await command("action", *guest, weapon + "-inspect", "--target", tool_guid))["native_guest_weapon"]
            assert inventory["kind"] == tool and inventory["stored_ammo"] == after, inventory
            pry_evidence["restored_tool"] = inventory
        assert saved_hashes(original) == originals
        (hrun / "stock-door-verification.json").write_text(json.dumps({
            "door": actor, "initial": door(original_host), "pried": door(unlocked), "closed_host": door(closed),
            "closed_guest": door(closed_peer), "guest_stock_input": receipt,
            "panel_blocks_trace": {"initial": [True, True], "open": [False, False], "closed": [True, True]},
            "restored_guest": door(restored_peer), "original_files_verified": len(originals), **pry_evidence,
        }, indent=2), encoding="utf-8")
    finally:
        for process, controller in reversed(processes):
            await stop(process, controller)
        assert saved_hashes(original) == originals, "The original single-player save directory changed during the run"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--door", choices=("wood", "metal"), default="wood")
    parser.add_argument("--tool", choices=("blowtorch", "crowbar"))
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(),
                         WOOD_DOOR if args.door == "wood" else METAL_DOOR, args.tool))
