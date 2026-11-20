"""Start real Direct IP host and guest sessions through the staged title panel."""

import argparse
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import native_title_entry as title, runtime
from tools.silverfish.control import select_run, submit
from tools.silverfish.title_session import STATUS_FILE, TitleSession, host_plan, join_plan
from tools.silverfish.travel import OBSERVATORY
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes


async def title_choice(work, case, action, plan, processes, tasks):
    home = case / action
    process = runtime.launch(work, home / "game-user", home / "game-bridge", headless=True, title_fixture=True)
    processes.append(process)
    bridge = runtime.Bridge(home / "game-bridge", process)
    stock = await bridge.wait_for(lambda game: game.get(title.KEY, {}).get("state") == "ready", timeout=30)
    retired = []

    async def retire_title():
        assert process.poll() is None
        assert bridge.snapshot()[title.KEY] == stock[title.KEY]
        retired.append(plan.read_status()["state"])
        await runtime.stop_game(process)

    supervisor = TitleSession(bridge, {action: plan}, retire_title=retire_title)
    tasks.append(asyncio.create_task(supervisor.run()))
    shown = await bridge.wait_for(lambda game: game.get(title.PANEL_KEY, {}).get("state") == "ready", timeout=8)
    panel = shown[title.PANEL_KEY]
    assert panel["actions"] == [action] and panel["mounted"], panel
    different = "join" if action == "host" else "host"
    try:
        await bridge.command("native-title-panel-press-fixture", different, timeout=4)
    except RuntimeError as error:
        assert str(error) == "title_action_not_offered", error
    else:
        raise AssertionError("An unoffered title action was accepted")
    accepted = await bridge.command("native-title-panel-press-fixture", action, timeout=4)
    assert accepted["ack"]["message"] == "qualified staged title control dispatched", accepted["ack"]
    expected = "hosting" if action == "host" else "connected"
    async with asyncio.timeout(65):
        while True:
            report = runtime.read_json(bridge.directory / STATUS_FILE)
            assert not report.get("last_failure"), report
            if report.get("state") == "ready":
                assert report["attempt"]["action"] == action and report["progress"] == expected, report
                assert retired == [expected] and process.poll() is not None, retired
                return {"stock_title": stock[title.KEY], "panel": panel, "action": action, "handoff": report["progress"]}
            await asyncio.sleep(.1)


async def verify(work: Path, original: Path) -> None:
    if os.name != "nt":
        raise OSError("The co-op title handoff requires a staged Windows game")
    name = "title-handoff-" + uuid.uuid4().hex[:8]
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    campaign, profile = select_run(work, "campaigns", name), select_run(work, "clients", name)
    host_run, guest_run = campaign / "run", profile / "run"
    initial = saved_hashes(original)
    processes, tasks = [], []
    evidence = {"verified": False, "build": runtime.staged_build(work)}
    try:
        port = runtime.spare_udp_port()
        host = host_plan(work, campaign, "127.0.0.1", port, headless=True,
            native_events=True, native_guest=True, native_zones=True, start_zone=OBSERVATORY,
        )
        evidence["host_title"] = await title_choice(work, case, "host", host, processes, tasks)
        hosting = await wait(host_run, lambda state: state.get("state") == "hosting"
                             and state.get("game", {}).get("map") == OBSERVATORY)
        assert hosting["endpoint"] == {"address": "127.0.0.1", "port": port}, hosting["endpoint"]
        evidence["host"] = {"map": OBSERVATORY, "port": port, "title_survived_until_host_ready": True}

        invitation = await submit(host_run, "invite", name="Title guest", character="Title Scout")
        assert invitation["ok"] is True
        guest = join_plan(work, profile, Path(invitation["invite_file"]), headless=True)
        evidence["guest_title"] = await title_choice(work, case, "join", guest, processes, tasks)
        joined = await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected")
                            and state.get("game", {}).get("map") == OBSERVATORY)
        paired = await wait(host_run, lambda state: state.get("game", {}).get("remote_clients") == 1
                            and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
        assert joined["character_id"] == invitation["character_id"]
        evidence["guest"] = {"map": OBSERVATORY, "character_id": joined["character_id"],
                             "native_save": paired["game"]["native_guest_save"]["state"],
                             "title_survived_until_guest_ready": True}
        pawn = paired["game"]["native_guest_save"]["pawn"]
        dropped = await submit(guest_run, "drop-link")
        assert dropped["ok"] and dropped.get("link_connected") is False, dropped
        reserved = await wait(host_run, lambda state: any(
            row.get("character_id") == invitation["character_id"] and row.get("state") == "reserved"
            for row in state.get("campaign", {}).get("leases", ())))
        resumed = await submit(guest_run, "reconnect")
        assert resumed["ok"] and resumed.get("resumed") is True and resumed.get("link_connected") is True, resumed
        recovered_guest = await wait(guest_run, lambda state: state.get("state") == "connected"
                                     and state.get("link_connected") is True)
        recovered_host = await wait(host_run, lambda state:
                                    state.get("game", {}).get("sequence", 0) > reserved["game"]["sequence"]
                                    and state["game"].get("native_guest_save", {}).get("pawn") == pawn
                                    and any(row.get("character_id") == invitation["character_id"] and row.get("state") == "active"
                                            for row in state.get("campaign", {}).get("leases", ())))
        assert recovered_guest["character_id"] == invitation["character_id"] and recovered_host["endpoint"]["port"] == port
        evidence["reconnect"] = {"character_id": recovered_guest["character_id"], "pawn": pawn, "port": port}
        checkpoint = await submit(host_run, "checkpoint")
        assert checkpoint["ok"] is True and checkpoint.get("revision", 0) > 0, checkpoint
        evidence["revision"] = checkpoint["revision"]
        assert (await submit(guest_run, "stop"))["ok"]
        await asyncio.wait_for(tasks[1], 15)
        assert (await submit(host_run, "stop"))["ok"]
        await asyncio.wait_for(tasks[0], 15)
        evidence["verified"] = True
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        for task in reversed(tasks):
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, OSError, RuntimeError, ValueError):
                await task
        evidence["original_saves_verified"] = saved_hashes(original) == initial
        (case / "live-title-handoff-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original stock save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
