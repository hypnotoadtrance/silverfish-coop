"""Verify in-game Host settings and local title control against a real private game."""

import argparse
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import native_title_entry as title, runtime
from tools.silverfish.__main__ import execute, parser, staged_title_menu
from tools.silverfish.control import status, submit
from tools.silverfish.title_handoff import REQUEST_FILE, _isolated_stock_title
from tools.silverfish.travel import OBSERVATORY
from tools.silverfish.udp_ownership import owns_udp_port


FIXTURE = "native-title-host-form-fixture"
REPORT = "live-title-host-settings-verification.json"


async def until(read, check, timeout=65):
    async with asyncio.timeout(timeout):
        while True:
            try:
                observed = read()
            except (FileNotFoundError, PermissionError):
                observed = {}
            if check(observed):
                return observed
            await asyncio.sleep(.1)


async def verify(work: Path, original: Path, *, embedded: bool):
    if not runtime.staged_script(work, "native_title_panel.lua", FIXTURE):
        raise ValueError("Stage the matched Host settings title panel before running this trial")
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    suffix = uuid.uuid4().hex[:8]
    case = work / "title-probes" / f"title-host-settings-{suffix}"
    case.mkdir(parents=True)
    print(case, flush=True)
    proof = {"verified": False, "embedded": embedded, "build": runtime.staged_build(work)}

    async def trial(source):
        profile_name = f"title-host-{source}-{suffix}"
        directory = work / "titles" / profile_name
        profile = directory / "game-user"
        title_run = directory / "run"
        configured_name = f"title-default-{source}-{suffix}"
        campaign = f"title-host-settings-{suffix}" if source == "form" else configured_name
        configured_port = runtime.spare_udp_port()
        port = runtime.spare_udp_port() if source == "form" else configured_port
        while source == "form" and port == configured_port:
            port = runtime.spare_udp_port()
        args = SimpleNamespace(work=work, title_profile=profile_name, campaign=configured_name,
                               bind="127.0.0.1", port=configured_port, invite=None,
                               profile="guest", character=None)
        title_live = asyncio.Event()
        bridges = []
        launch = runtime.launch

        def track(workspace, user_profile, mailbox, *arguments, **kwargs):
            process = launch(workspace, user_profile, mailbox, *arguments, **kwargs)
            if user_profile == profile:
                bridges.append(runtime.Bridge(mailbox, process))
            return process

        def title_status():
            return status(title_run)

        def cli(operation, *values):
            parsed = parser().parse_args(["--work", str(work), operation, "--title-profile", profile_name, *values])
            return execute(parsed)

        with patch.object(runtime, "launch", side_effect=track):
            task = asyncio.create_task(staged_title_menu(args, on_title_ready=title_live.set,
                                                         require_embedded=embedded, headless=True))
            try:
                await asyncio.wait_for(title_live.wait(), 48)
                bridge, = bridges
                stock = bridge.snapshot()
                assert stock[title.PANEL_KEY]["actions"] == ["host"] and _isolated_stock_title(stock, profile / "Saved")
                selected = work / "campaigns" / campaign / "run"
                configured = work / "campaigns" / configured_name
                assert not configured.exists() and not selected.exists()
                offered = await cli("status")
                assert (offered["role"], offered["state"], offered["actions"], offered["stale"]) == (
                    "title", "offered", ["host"], False)
                initial_offer = offered["handoff_generation"]
                stale_id = uuid.uuid4().hex
                stale = await cli("title-select", "--handoff-generation", stale_id, "--role", "host")
                assert stale.get("ok") is False and stale.get("error") == "title_stale_generation", stale

                async def input_field(operation, *values, message):
                    result = await bridge.command(FIXTURE, operation, *values, timeout=10)
                    assert result["ack"]["message"] == message, result["ack"]

                if source == "form":
                    await input_field("open", message="title_host_form_open")
                    await input_field("campaign", campaign, message="title_host_form_campaign_entered")
                    await input_field("port", "0", message="title_host_form_port_entered")
                    try:
                        await bridge.command(FIXTURE, "submit", timeout=10)
                    except RuntimeError as error:
                        assert str(error) == "title_host_form_invalid_port", error
                    else:
                        raise AssertionError("The Host form accepted UDP port zero")
                    await input_field("cancel", message="title_host_form_cancelled")
                    assert bridge.process.poll() is None and not selected.exists() and not configured.exists()
                    assert not (bridge.directory / REQUEST_FILE).exists()
                    await input_field("open", message="title_host_form_open")
                    await input_field("campaign", campaign, message="title_host_form_campaign_entered")
                    await input_field("port", str(port), message="title_host_form_port_entered")
                    await input_field("submit", message="title_host_form_submitted")
                else:
                    offered = await cli("status")
                    assert offered["handoff_generation"] == initial_offer
                    accepted = await cli("title-select", "--handoff-generation", initial_offer, "--role", "host")
                    assert accepted["ok"] is True and accepted["state"] == "accepted", accepted

                hosting = await until(lambda: status(selected), lambda state: state.get("state") == "hosting"
                                      and state.get("game", {}).get("map") == OBSERVATORY)
                assert hosting["endpoint"] == {"address": "127.0.0.1", "port": port}
                assert owns_udp_port(hosting["pid"], port)
                actually_ready = await until(title_status, lambda state: state.get("state") == "ready")
                assert actually_ready["attempt"]["action"] == "host" and bridge.process.poll() is not None
                if source == "form":
                    assert not configured.exists(), "In-game Host settings accidentally launched the configured default"
                else:
                    assert actually_ready["attempt"] == accepted["attempt"]
                checkpoint = await submit(selected, "checkpoint")
                assert checkpoint.get("ok") is True and checkpoint["revision"] >= 1
                assert (await submit(selected, "stop"))["ok"] is True
                await asyncio.wait_for(asyncio.shield(task), 30)
                stopped = await cli("status")
                assert stopped["state"] == "stopped" and stopped["active"] is False and not stopped["last_failure"]
                assert not owns_udp_port(hosting["pid"], port)
                events = (await cli("events"))["events"]
                selections = [entry for entry in events if entry.get("event") == "title_selection_accepted"]
                assert len(selections) == 1 and selections[0]["attempt"] == actually_ready["attempt"]
                proof[source] = {
                    "campaign": campaign, "selected_port": port, "default_port": configured_port,
                    "checkpoint_revision": checkpoint["revision"], "attempt": selections[0]["attempt"],
                    "stale_request_refused": True, "stopped": True,
                }
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    try:
        await trial("form")
        await trial("ai")
        proof["verified"] = True
    except (OSError, RuntimeError, ValueError, AssertionError, TimeoutError) as error:
        proof["failure"] = str(error)
        raise
    finally:
        proof["original_saves_verified"] = saved_hashes(original) == originals
        proof["verified"] = proof["verified"] and proof["original_saves_verified"]
        runtime.write_json(case / REPORT, proof)
        assert proof["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--work", required=True, type=Path)
    cli.add_argument("--vanilla-saved", required=True, type=Path)
    cli.add_argument("--embedded", action="store_true")
    options = cli.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True),
                       embedded=options.embedded))
