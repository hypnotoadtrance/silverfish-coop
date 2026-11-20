"""Verify stock co-op Pause exits, final native saves and reusable titles."""

import argparse
import asyncio
from contextlib import suppress
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_ai_smoke import wait
from live_multi_smoke import saved_hashes
from tools.silverfish import embedded_title_plan, native_host_save, runtime, service
from tools.silverfish.__main__ import staged_title_menu
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY


FIXTURE = "native-coop-pause-fixture"


async def choose(run, choice, previous=None):
    offered = await wait(run, lambda state: state.get("state") == "offered"
                         and state.get("handoff_generation") not in (None, previous))
    generation = offered["handoff_generation"]
    selected = await submit(run, "title-select", role=choice, handoff_generation=generation)
    assert selected["ok"], selected
    return generation


def paired_checkpoint(directory, character):
    profile = directory / "game-user"
    checkpoint = CampaignStore.read_existing(
        directory / "save", verify_world=service.verify_travel_checkpoint,
        verify_checkpoint=lambda data: native_host_save.verify_checkpoint(profile, data),
    )
    saved = checkpoint["characters"][character]["state"]["native_guest_save"]
    assert saved["map"] == OBSERVATORY and saved["character_id"] == character
    return {"revision": checkpoint["revision"], "guest_save": saved}


async def stock_pause(bridge, action):
    operation = uuid.uuid4().hex
    expected = f"1\t{operation}\t"
    request = bridge.directory / f"{FIXTURE}-command.tsv"
    reply = bridge.directory / f"{FIXTURE}-reply.tsv"
    runtime.write_text(request, expected + action + "\n", newline="\n")
    try:
        async with asyncio.timeout(12):
            while bridge.process.poll() is None:
                with suppress(OSError):
                    result = reply.read_text(encoding="utf-8")
                    if result.startswith(expected) and result.endswith("\n"):
                        status, message = result[len(expected):-1].split("\t", 1)
                        if status == "error":
                            raise RuntimeError(message)
                        assert (status, message) == ("ok", "exact stock co-op pause " + action), result
                        return
                await asyncio.sleep(.1)
    except TimeoutError as error:
        raise TimeoutError(f"The stock Pause fixture did not acknowledge {action}") from error
    raise RuntimeError(f"The staged game exited while performing stock Pause {action}")


async def verify(work: Path, original: Path):
    if (not embedded_title_plan.installed(work)
            or not runtime.staged_script(work, "native_coop_pause.lua", FIXTURE)):
        raise ValueError("Use the matched staged native runtime with the stock Pause exit")
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    name = "stock-pause-" + uuid.uuid4().hex[:8]
    output = work / "title-probes" / name
    output.mkdir(parents=True)
    print(output, flush=True)
    campaign, guest = work / "campaigns" / name, work / "clients" / name
    host_run, guest_run = campaign / "run", guest / "run"
    host_title, guest_title = (work / "titles" / f"{name}-{role}" / "run" for role in ("host", "guest"))
    port = runtime.spare_udp_port()
    evidence = {"verified": False, "build": runtime.staged_build(work), "port": port}
    launched, jobs = {}, []
    launch = runtime.launch

    def track(workspace, profile, mailbox, *args, **kwargs):
        process = launch(workspace, profile, mailbox, *args, **kwargs)
        launched[profile.parent] = runtime.Bridge(mailbox, process)
        return process

    def open_title(path, *, hosting=False, invite=None):
        plan = SimpleNamespace(work=work, campaign=name if hosting else None, bind="127.0.0.1", port=port,
                               invite=invite, character=None, profile=name, title_profile=path.parent.name)
        ready = []
        job = asyncio.create_task(staged_title_menu(plan, on_title_ready=lambda: ready.append(True),
                                                    headless=True, require_embedded=True, pause_fixture=True))
        jobs.append(job)
        return job, ready

    def active(phase, state):
        return (state.get("state") == phase and state.get("game", {}).get("map") == OBSERVATORY
                and state["game"].get("stock_pause_exit", {}).get("state") == "armed")

    with patch.object(runtime, "launch", side_effect=track):
        try:
            host_task, host_ready = open_title(host_title, hosting=True)
            host_generation = await choose(host_title, "host")
            host = await wait(host_run, lambda state: active("hosting", state))
            invitation = await submit(host_run, "invite", name="Pause guest", character="Pause scout")
            assert invitation["ok"], invitation
            character = invitation["character_id"]
            guest_task, guest_ready = open_title(guest_title, invite=Path(invitation["invite_file"]))
            guest_generation = await choose(guest_title, "join")
            joined = await wait(guest_run, lambda state: active("connected", state))
            assert joined["character_id"] == character
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

            guest_bridge = launched[guest]
            for action in ("open", "prompt", "cancel"):
                await stock_pause(guest_bridge, action)
            assert not (guest_bridge.directory / "native-coop-pause-response.tsv").exists()
            evidence["stock_cancel_kept_guest_connected"] = (await wait(guest_run, lambda state:
                state.get("state") == "connected" and state.get("pid") == joined["pid"]))["link_connected"]
            for action in ("prompt", "confirm"):
                await stock_pause(guest_bridge, action)
            stopped = await wait(guest_run, lambda state: state.get("state") == "stopped")
            assert stopped.get("stop_reason") == "guest_left", stopped
            await wait(host_run, lambda state: state.get("state") == "hosting" and state["pid"] == host["pid"]
                       and not state.get("campaign", {}).get("leases"))
            after_leave = paired_checkpoint(campaign, character)
            evidence["guest_leave"] = after_leave
            guest_next = await choose(guest_title, "join", guest_generation)
            assert guest_next != guest_generation and guest_bridge.process.poll() is not None
            joined_again = await wait(guest_run, lambda state: active("connected", state)
                                      and state.get("generation") != joined["generation"])
            assert joined_again["character_id"] == character
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

            host_bridge = launched[campaign]
            for action in ("open", "save_and_quit"):
                await stock_pause(host_bridge, action)
            host_final, guest_final = await asyncio.gather(
                wait(host_run, lambda state: state.get("state") == "stopped"),
                wait(guest_run, lambda state: state.get("state") == "stopped"),
            )
            assert host_final.get("stop_reason") == guest_final.get("stop_reason") == "host_session_ended"
            final = paired_checkpoint(campaign, character)
            assert final["revision"] > after_leave["revision"] and final["guest_save"] != after_leave["guest_save"]
            evidence["host_end"] = final
            new_host = await choose(host_title, "host", host_generation)
            assert new_host != host_generation and host_bridge.process.poll() is not None
            resumed = await wait(host_run, lambda state: active("hosting", state)
                                 and state.get("generation") != host["generation"])
            assert resumed["endpoint"] == host["endpoint"]
            fresh_guest_title = await wait(guest_title, lambda state: state.get("state") == "offered"
                                           and state.get("handoff_generation") not in (None, guest_next))
            assert host_ready == guest_ready == [True]
            evidence["fresh_titles"] = {"host": new_host, "guest": fresh_guest_title["handoff_generation"],
                                        "resumed_host": resumed["generation"]}
            assert (await submit(host_run, "stop"))["ok"]
            await asyncio.wait_for(host_task, 18)
            await runtime.stop_game(launched[guest_title.parent].process)
            await asyncio.wait_for(guest_task, 18)
            evidence["verified"] = True
        except BaseException as error:
            evidence["failure"] = str(error)
            raise
        finally:
            for job in reversed(jobs):
                if not job.done():
                    job.cancel()
                with suppress(asyncio.CancelledError, OSError, ValueError, RuntimeError, AssertionError):
                    await job
            evidence["original_saves_verified"] = saved_hashes(original) == originals
            evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
            (output / "live-stock-pause-exit-verification.json").write_text(
                json.dumps(evidence, indent=2) + "\n", encoding="utf-8",
            )
            assert evidence["original_saves_verified"], "The original stock save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
