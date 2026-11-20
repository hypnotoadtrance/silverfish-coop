"""Verify the actual mounted stock title menu in a fresh, isolated stage."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import native_title_entry, runtime
from live_smoke import saved_hashes


async def verify(work: Path, original: Path) -> None:
    if os.name != "nt":
        raise OSError("The stock title proof requires a staged Windows game")
    case = work / "title-probes" / ("title-" + uuid.uuid4().hex[:8])
    case.mkdir(parents=True)
    print(case, flush=True)
    profile, mailbox = case / "game-user", case / "game-bridge"
    initial = saved_hashes(original)
    evidence = {"verified": False, "build": runtime.staged_build(work)}
    process = None
    try:
        process = runtime.launch(work, profile, mailbox, headless=True)
        bridge = runtime.Bridge(mailbox, process)
        first = await bridge.wait_for(
            lambda game: game.get(native_title_entry.KEY, {}).get("state") == "ready",
            timeout=35,
        )
        proof = first[native_title_entry.KEY]
        second = await bridge.wait_for(lambda game: game.get("sequence", 0) >= first["sequence"] + 3, timeout=5)
        assert second.get(native_title_entry.KEY) == proof, (proof, second.get(native_title_entry.KEY))
        assert (second["state"], second["map"], second["remote_clients"], second["server_connection"]) == (
            "ready", native_title_entry.MAP, 0, False), second.get(native_title_entry.KEY)
        saves = list(profile.rglob("*.sav"))
        assert not saves, saves
        evidence.update(verified=True, stock_title=proof, observed_sequences=[first["sequence"], second["sequence"]])
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        evidence["failure"] = str(error)
        raise
    finally:
        if process is not None:
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == initial
        (case / "live-title-entry-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        assert evidence["original_saves_verified"], "The original stock save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(verify(options.work.resolve(strict=True), options.vanilla_saved.resolve(strict=True)))
