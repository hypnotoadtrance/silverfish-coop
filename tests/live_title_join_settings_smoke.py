"""Verify invite import, host-owned characters, Join and reconnect through the game title."""

import argparse
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from live_title_host_settings_smoke import until
from tools.silverfish import native_title_entry as title, runtime, service
from tools.silverfish.__main__ import staged_title_menu
from tools.silverfish.control import status, submit
from tools.silverfish.title_handoff import _isolated_stock_title
from tools.silverfish.travel import OBSERVATORY


FIXTURE = "native-title-join-form-fixture"
REPORT = "live-title-join-settings-verification.json"


async def verify(work: Path, original: Path, *, embedded=False):
    if not runtime.staged_script(work, "native_title_panel.lua", FIXTURE):
        raise ValueError("Install the matched Join form before running this trial")
    before = saved_hashes(original)
    if not before:
        raise ValueError("Supply the original single-player Saved directory")
    suffix = uuid.uuid4().hex[:8]
    name = f"title-join-settings-{suffix}"
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    proof = {"verified": False, "embedded": embedded, "build": runtime.staged_build(work)}
    campaign = work / "campaigns" / name
    host_run = campaign / "run"
    guest_name = f"title-joining-guest-{suffix}"
    guest_run = work / "clients" / guest_name / "run"
    title_run = work / "titles" / name / "run"
    title_profile = title_run.parent / "game-user"
    port = runtime.spare_udp_port()
    title_ready = asyncio.Event()
    jobs, titles = [], []
    launch = runtime.launch

    def track(workspace, profile, mailbox, *arguments, **kwargs):
        process = launch(workspace, profile, mailbox, *arguments, **kwargs)
        if profile == title_profile:
            titles.append(runtime.Bridge(mailbox, process))
        return process

    with patch.object(runtime, "launch", side_effect=track):
        try:
            host = asyncio.create_task(service.host_game(work, campaign, "127.0.0.1", port, True,
                                                        start_preset="wanderer", **service.MENU_HOST_OPTIONS))
            jobs.append(host)
            await until(lambda: status(host_run), lambda current: current.get("state") == "hosting")
            first = await submit(host_run, "invite", name="First title guest", character="First scout")
            second = await submit(host_run, "invite", name="Second title guest", character="Second scout")
            assert first["ok"] is True and second["ok"] is True
            invite_path = Path(first["invite_file"])
            accepted_invite = runtime.read_json(invite_path)

            args = SimpleNamespace(work=work, title_profile=name, campaign=None, bind="127.0.0.1", port=port,
                                   invite=None, interactive_join=True, profile="configured-guest", character=None)
            menu = asyncio.create_task(staged_title_menu(args, require_embedded=embedded, headless=True,
                                                         on_title_ready=title_ready.set))
            jobs.append(menu)
            await asyncio.wait_for(title_ready.wait(), 48)
            bridge, = titles
            snapshot = bridge.snapshot()
            assert snapshot[title.PANEL_KEY]["actions"] == ["join"] and _isolated_stock_title(snapshot, title_profile / "Saved")
            fixture_file = bridge.directory / "title-join-fixture.invite.json"

            async def choose(action, *values, expected=None):
                response = await bridge.command(FIXTURE, action, *values, timeout=8)
                actual = response["ack"]["message"]
                if expected is not None:
                    assert actual == expected, (action, actual)
                return actual

            async def screen(expected):
                async with asyncio.timeout(25):
                    while await choose("observe") != "title_join_form_state=" + expected:
                        await asyncio.sleep(.1)

            await choose("open", expected="title_join_form_open")
            runtime.write_json(fixture_file, dict(accepted_invite, game_build="0" * 64))
            await choose("import", expected="title_join_form_import_accepted")
            await screen("invite;error=invalid_invite")
            await choose("cancel", expected="title_join_form_cancelled")
            assert not guest_run.exists() and bridge.process.poll() is None

            await choose("open", expected="title_join_form_open")
            runtime.write_json(fixture_file, accepted_invite)
            await choose("import", expected="title_join_form_import_accepted")
            await screen("catalog;count=1")
            try:
                await choose("select", second["character_id"])
            except RuntimeError as error:
                assert str(error) == "title_join_form_fixture_character_unavailable", error
            else:
                raise AssertionError("The Join form offered another guest's character")

            await choose("create", "New title scout", expected="title_join_form_create_accepted")
            await screen("catalog;count=2")
            catalog = await service.character_catalog(work, invite_path, None, None)
            characters = {row["name"]: row["character_id"] for row in catalog["characters"]}
            assert set(characters) == {"First scout", "New title scout"} and characters["First scout"] == first["character_id"]
            character = characters["New title scout"]
            assert not guest_run.exists() and not status(host_run)["campaign"].get("leases")
            await choose("select", character, expected="title_join_form_select_accepted")
            await choose("profile", guest_name, expected="title_join_form_profile_accepted")
            fixture_file.unlink()
            await choose("submit", expected="title_join_form_submitted")

            connected = await until(lambda: status(guest_run), lambda current: current.get("state") == "connected"
                                    and current.get("character_id") == character and current.get("gameplay_fresh") is True)
            assert connected["game"]["map"] == OBSERVATORY
            await until(lambda: status(title_run), lambda panel: panel.get("state") == "ready")
            assert bridge.process.poll() is not None
            assert not (work / "clients" / "configured-guest" / "run").exists()
            paired = await until(lambda: status(host_run), lambda current: current.get("game", {})
                                 .get("native_guest_save", {}).get("state") == "bound")
            pawn = paired["game"]["native_guest_save"]["pawn"]
            assert (await submit(guest_run, "drop-link"))["ok"] is True
            await until(lambda: status(host_run), lambda current: any(
                row.get("state") == "reserved" and row.get("character_id") == character
                for row in current.get("campaign", {}).get("leases", ())))
            restored = await submit(guest_run, "reconnect")
            assert restored["ok"] is True and restored.get("resumed") is True
            await until(lambda: status(host_run), lambda current: any(
                row.get("state") == "active" and row.get("character_id") == character
                for row in current.get("campaign", {}).get("leases", ()))
                and current.get("game", {}).get("native_guest_save", {}).get("pawn") == pawn)
            checkpoint = await submit(host_run, "checkpoint")
            assert checkpoint["ok"] is True and checkpoint["revision"] >= 1
            assert (await submit(guest_run, "stop"))["ok"]
            await asyncio.wait_for(asyncio.shield(menu), 25)
            assert (await submit(host_run, "stop"))["ok"]
            await asyncio.wait_for(asyncio.shield(host), 25)
            assert status(title_run)["state"] == "stopped"
            assert not tuple(bridge.directory.rglob("*.invite.json")), "Private imported data survived title cleanup"
            proof.update(verified=True, port=port, character_id=character, same_pawn=pawn,
                         wrong_build_refused=True, wrong_owner_refused=True, no_guest_before_selection=True,
                         checkpoint_revision=checkpoint["revision"])
        except (OSError, RuntimeError, ValueError, KeyError, AssertionError, TimeoutError) as error:
            proof["failure"] = str(error)
            raise
        finally:
            for task in reversed(jobs):
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            proof["original_saves_verified"] = saved_hashes(original) == before
            proof["verified"] = proof["verified"] and proof["original_saves_verified"]
            runtime.write_json(case / REPORT, proof)
            assert proof["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--work", type=Path, required=True)
    cli.add_argument("--vanilla-saved", type=Path, required=True)
    cli.add_argument("--embedded", action="store_true")
    args = cli.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True), embedded=args.embedded))
