"""Exercise stock Interact and independent authenticated Castaway story choices on a staged game."""

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
sys.path.insert(0, str(REPO))

from tools.silverfish import native_dialogue as dialogue


CASTAWAY_PLACEMENT = (-2662.4421, 385.7958, 87.9999)


async def exercise(work: Path, vanilla_saved: Path | None) -> None:
    name = "castaway-dialogue-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    profiles = [("--profile", f"{name}-{index}") for index in (1, 2)]
    host_run = work / "campaigns" / name / "run"
    guest_runs = [work / "clients" / profile[1] / "run" for profile in profiles]
    host_run.mkdir(parents=True)
    print(host_run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved is not None else None
    processes = []
    evidence = {"stock_input": [], "approaches": []}
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"Unexpected response for {args[0]} {args[3] if len(args) > 3 else ''}")
        return json.loads(output)

    async def launch(role, owner, label, *args):
        with (host_run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, role, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def action(owner, verb, target=None, expected=True):
        select = ("--target", target) if target is not None else ()
        return await command("action", *owner, verb, *select, expected=expected)

    def active(status, count):
        game = status.get("game", {})
        saves = game.get("native_guest_save_members", {})
        return (status.get("state") == "hosting" and game.get("map") == dialogue.OBSERVATORY
                and game.get("remote_clients") == count and len(saves) == count
                and all(row.get("state") == "bound" for row in saves.values())
                and len(status.get("gameplay", {}).get("players", {})) == count + 1)

    def view(status):
        return status.get("game", {}).get("native_dialogue_view", {})

    def exact_position(status, pawn, *, local=False):
        pawns = status.get("game", {}).get("pawns", {})
        rows = (pawns.get("local", {}),) if local else pawns.values()
        return next((row["location"] for row in rows if row.get("name") == pawn
                     and len(row.get("location", ())) == 3
                     and all(type(axis) in (int, float) and math.isfinite(axis) for axis in row["location"])), None)

    def connected(status, character):
        return (status.get("state") == "connected" and status.get("link_connected") is True
                and status.get("character_id") == character and status.get("host_session") == authority["generation"]
                and status.get("zone_epoch") == authority["zone_epoch"]
                and status.get("game", {}).get("map") == dialogue.OBSERVATORY)

    async def stock_press(target, expected_row):
        client = clients[0]
        pending = (await action(profiles[0], dialogue.FIXTURE, target))["native_dialogue_view"]
        assert pending["pawn"] == client["pawn"] and pending["state"] == "pending", pending
        sequence = pending["sequence"]
        expected_state = "open" if expected_row else "closed"
        current = await wait(guest_runs[0], lambda status: connected(status, client["character"])
                             and status.get("generation") == client["generation"]
                             and (view(status).get("state"), view(status).get("sequence"), view(status).get("row"),
                                  view(status).get("pawn")) == (expected_state, sequence, expected_row or "-", client["pawn"]),
                             timeout=14)
        result = view(current)
        evidence["stock_input"].append({key: result[key] for key in ("pawn", "state", "sequence", "row")})
        return result

    async def effects():
        state = await wait(host_run, lambda status: active(status, 2) and type(status["game"].get("native_wallet")) is int)
        quests = (await action(host, "native-quest-list", "active"))["native_quest_page"]
        return {"quests": quests["quests"], "total": quests["total"], "cash": state["game"]["native_wallet"],
                "reputation": state["game"].get("native_reputation")}

    clients = []
    try:
        await launch("host", host, "host", "--port", "0", "--start-zone", dialogue.OBSERVATORY,
                     "--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--max-guests", "2")
        authority = await wait(host_run, lambda status: active(status, 0), timeout=55)
        for index, profile in enumerate(profiles):
            invite = await command("invite", *host, "--name", f"Castaway visitor {index + 1}",
                                   "--character", f"Castaway player {index + 1}")
            process = await launch("join", profile, f"guest-{index + 1}", "--invite", invite["invite_file"], "--headless", "--native-zones")
            character = invite["character_id"]
            joined = await wait(guest_runs[index], lambda status: connected(status, character) and status.get("generation"), timeout=65)
            clients.append({"character": character, "generation": joined["generation"], "pid": process.pid,
                            "pawn": joined["game"]["pawns"]["local"]["name"]})
            await wait(host_run, lambda status: active(status, index + 1), timeout=55)
        for identity in ("character", "generation", "pid"):
            assert len({client[identity] for client in clients}) == 2, f"Expected two distinct authenticated client {identity} values"
        before = await effects()
        roster = await wait(host_run, lambda status: active(status, 2)
                            and all(status.get("gameplay", {}).get("native_pawns", {}).get(role) for role in ("guest", "guest2")))
        authority_pawns = roster["gameplay"]["native_pawns"]
        assert len({authority_pawns["guest"], authority_pawns["guest2"]}) == 2
        for run, role, client in zip(guest_runs, ("guest", "guest2"), clients, strict=True):
            pawn, character = client["pawn"], client["character"]
            assert roster["gameplay"]["players"][role]["character_id"] == character
            authority_pawn = authority_pawns[role]
            receipt = (await action(host, dialogue.APPROACH, role))["native_dialogue_approach"]
            assert receipt == {"pawn": authority_pawn, "npc": dialogue.NPC}, receipt
            approached = await wait(host_run, lambda status: (point := exact_position(status, authority_pawn)) is not None
                                    and math.dist(point, CASTAWAY_PLACEMENT) <= 160)
            position = exact_position(approached, authority_pawn)
            armed = await wait(run, lambda status: connected(status, character) and status.get("generation") == client["generation"]
                               and view(status).get("state") == "armed" and view(status).get("pawn") == pawn
                               and (point := exact_position(status, pawn, local=True)) is not None and math.dist(point, position) <= 60)
            evidence["approaches"].append({"character": character, "client_generation": client["generation"],
                                           "authority_pawn": authority_pawn, "client_pawn": pawn,
                                           "authority_location": position, "client_location": exact_position(armed, pawn, local=True)})

        first = await stock_press("open", dialogue.ROOT)
        other = (await action(profiles[1], dialogue.OPEN, dialogue.PROFILE))["native_dialogue"]
        assert (other["profile"], other["npc"], other["row"], other["lines"]) == (
            dialogue.PROFILE, dialogue.NPC, dialogue.ROOT, ["Looking for something?"]), other["row"]
        assert first["token"] != other["token"] and [choice["ordinal"] for choice in other["choices"]] == [1, 3]
        sibling = await action(profiles[1], dialogue.CHOOSE, first["token"] + "@3", expected=False)
        assert sibling["error"] == "native_dialogue_choice_expired", sibling
        trade = await action(profiles[1], dialogue.CHOOSE, other["token"] + "@2", expected=False)
        assert trade["error"] == "native_dialogue_choice_unavailable", trade
        story = await stock_press("3", dialogue.STORY)
        assert story["token"] != first["token"]
        other_story = (await action(profiles[1], dialogue.CHOOSE, other["token"] + "@3"))["native_dialogue"]
        assert other_story["row"] == dialogue.STORY and len(other_story["lines"]) == 3
        assert other_story["lines"][0] == "After I washed up here in the Zone, Simmi took me on board."
        stale = await action(profiles[1], dialogue.CHOOSE, other["token"] + "@1", expected=False)
        assert stale["error"] == "native_dialogue_choice_expired", stale
        await stock_press("1", dialogue.ROOT)
        await stock_press("1", None)
        serials = [receipt["sequence"] for receipt in evidence["stock_input"]]
        assert serials == sorted(set(serials)), serials
        after = await effects()
        assert after == before, {"before": before, "after": after}
        evidence.update(characters=[client["character"] for client in clients], story=other_story["lines"], authority_state=after, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await action(owner, "stop")
                        await asyncio.wait_for(process.wait(), 15)
                    except (OSError, RuntimeError, TimeoutError):
                        if process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 12)
        finally:
            if original is not None:
                evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
            (host_run / "live-castaway-dialogue-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original single-player Saved directory changed during the run"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    arguments = parser.parse_args()
    asyncio.run(exercise(arguments.work.resolve(), arguments.vanilla_saved))
