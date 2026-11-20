"""Verify the native editable co-op title widget in an isolated, headless game."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import native_title_entry as title, runtime
from tools.silverfish.title_handoff import DESCRIPTOR_FILE, REQUEST_FILE, _isolated_stock_title


FIXTURE = "native-title-settings-widget-fixture"
REPORT = "live-title-settings-widget-verification.json"


async def verify(work: Path, original: Path) -> None:
    if os.name != "nt":
        raise OSError("The editable title smoke requires a staged Windows game")
    stage_scripts = (work / "stage" / runtime.SHIPPING).parent / runtime.MOD_SCRIPTS
    if not (stage_scripts / "native_title_settings_widget.lua").is_file() or not runtime.staged_script(
        work, "native_title_panel.lua", FIXTURE,
    ):
        raise ValueError("The stage must contain the exact editable title widget fixture")
    initial = saved_hashes(original)
    if not initial:
        raise ValueError("Supply the original single-player Saved directory")
    case = work / "title-probes" / ("title-settings-widget-" + uuid.uuid4().hex[:8])
    case.mkdir(parents=True)
    print(case, flush=True)
    profile, mailbox = case / "game-user", case / "game-bridge"
    evidence = {"verified": False, "build": runtime.staged_build(work), "steps": []}
    process = None
    try:
        process = runtime.launch(work, profile, mailbox, headless=True, title_fixture=True, inspect_title_profile=True)
        bridge = runtime.Bridge(mailbox, process)
        stock = await bridge.wait_for(
            lambda frame: frame.get(title.KEY, {}).get("state") == "ready" and title.STARTUP_KEY in frame, timeout=35,
        )
        assert _isolated_stock_title(stock, profile / "Saved"), stock
        stock_title = stock[title.KEY]
        evidence["stock_title"] = stock_title

        async def invoke(operation, expected):
            frame = await bridge.command(FIXTURE, operation, timeout=10)
            message = frame["ack"]["message"]
            assert message in expected, (operation, message, expected)
            evidence["steps"].append(dict(operation=operation, message=message, sequence=frame["sequence"]))
            return frame

        async def refused(arguments, reason):
            try:
                await bridge.command(FIXTURE, *arguments, timeout=10)
            except RuntimeError as error:
                assert str(error) == reason, (arguments, str(error), reason)
                evidence["steps"].append(dict(operation=arguments[0], refusal=reason))
            else:
                raise AssertionError(f"Title fixture unexpectedly accepted {arguments[0]}")

        await invoke("mount", {"title_settings_fixture_mounted"})
        await invoke("observe", {"title_settings_fixture_port=7777"})
        await refused(("edit", "9999"), "title_settings_fixture_arguments")
        focused = await invoke("focus", {"title_settings_fixture_focus=0", "title_settings_fixture_focus=1"})
        evidence["focus_observed"] = focused["ack"]["message"].endswith("=1")
        await invoke("edit", {"title_settings_fixture_edited"})
        await invoke("observe", {"title_settings_fixture_port=28181"})
        removed = await invoke("remove", {"title_settings_fixture_removed"})
        assert removed[title.KEY] == stock_title, removed[title.KEY]
        await refused(("observe",), "title_settings_fixture_editor_unavailable")

        await invoke("mount", {"title_settings_fixture_mounted"})
        await invoke("observe", {"title_settings_fixture_port=7777"})
        await invoke("edit", {"title_settings_fixture_edited"})
        covered = await invoke("cover", {"title_settings_fixture_stale_read_write_blocked_removed"})
        shadowed = covered[title.KEY]
        assert (shadowed["state"], shadowed["reason"], shadowed["top"], shadowed["menu_stack"]) == (
            "waiting", "stock_menu_below_dialog", False, stock_title["menu_stack"] + 1,
        ), shadowed
        assert shadowed["widget"] == stock_title["widget"] and shadowed["controller"] == stock_title["controller"], shadowed
        evidence["covered_title"] = shadowed
        await refused(("mount",), "title_settings_fixture_title_unavailable")
        await invoke("uncover", {"title_settings_fixture_credits_closed"})
        returned = await bridge.wait_for(
            lambda frame: frame.get(title.KEY) == stock_title and title.STARTUP_KEY in frame, timeout=5,
        )
        assert _isolated_stock_title(returned, profile / "Saved"), returned
        assert returned["sequence"] > covered["sequence"], returned

        await invoke("mount", {"title_settings_fixture_mounted"})
        await invoke("observe", {"title_settings_fixture_port=7777"})
        ending = await invoke("remove", {"title_settings_fixture_removed"})
        assert ending[title.KEY] == stock_title
        assert all(not (mailbox / name).exists() for name in (DESCRIPTOR_FILE, REQUEST_FILE))
        saves = {path.relative_to(profile).as_posix() for path in profile.rglob("*.sav")}
        assert saves <= {"Saved/SaveGames/Config.sav"}, saves
        evidence.update(verified=True, isolated_title_files=sorted(saves), stale_title_blocked=True)
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        evidence["failure"] = str(error)
        raise
    finally:
        try:
            if process is not None:
                await runtime.stop_game(process)
        finally:
            evidence["original_saves_verified"] = saved_hashes(original) == initial
            evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
            (case / REPORT).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            assert evidence["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
