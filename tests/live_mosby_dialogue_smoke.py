"""Verify stock Mosby dialogue, independent guest choices, and durable Stone Maze issuance."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_dialogue as dialogue


async def exercise(work: Path, vanilla_saved: Path | None) -> None:
    name = "mosby-dialogue-" + uuid.uuid4().hex[:8]
    host = ("--campaign", name)
    guests = [("--profile", f"{name}-{index}") for index in (1, 2)]
    run = work / "campaigns" / name / "run"
    guest_runs = [work / "clients" / guest[1] / "run" for guest in guests]
    run.mkdir(parents=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--max-guests", "2")
    guest_flags = ("--headless", "--native-zones")
    processes, evidence = [], {"stock_input": []}

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 45)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        result = json.loads(output) if output else {}
        if not result or (process.returncode == 0) != expected:
            raise RuntimeError(f"Unexpected {args[0]} {args[3] if len(args) > 3 else ''} response: {result.get('error', '-')}")
        return result

    async def action(owner, verb, target=None, *, expected=True, operation=None):
        selection = ("--target", target) if target is not None else ()
        replay = ("--operation-id", operation) if operation else ()
        return await command("action", *owner, verb, *selection, *replay, expected=expected)

    async def launch(kind, owner, label, *args):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, kind, *owner, *args, cwd=REPO, stdout=log, stderr=log,
                                                             creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def stop(process, owner):
        if process.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(process.wait(), 16) == 0

    def hosting(state):
        return state.get("state") == "hosting" and state.get("game", {}).get("map") == dialogue.SCIENTIST_SHOP

    def view(state):
        return state.get("game", {}).get("native_dialogue_view", {})

    async def enroll(index, invite, authority, label, *, role=None):
        process = await launch("join", guests[index], label, "--invite", invite["invite_file"], *guest_flags)
        character = invite["character_id"]
        role = role or ("guest", "guest2")[index]

        def connected(state):
            return (state.get("state") == "connected" and state.get("link_connected") is True
                    and (state.get("character_id"), state.get("host_session"), state.get("zone_epoch"), state.get("game", {}).get("map"))
                    == (character, authority["generation"], authority["zone_epoch"], dialogue.SCIENTIST_SHOP))

        local = await wait(guest_runs[index], connected, timeout=70)
        def bound(state):
            gameplay = state.get("gameplay", {})
            pawn = gameplay.get("native_pawns", {}).get(role)
            scope = state.get("game", {}).get("native_guest_save_members", {}).get(pawn, {})
            return (hosting(state) and gameplay.get("players", {}).get(role, {}).get("character_id") == character
                    and scope.get("state") == "bound")

        status = await wait(run, bound, timeout=60)
        pawn = local["game"]["pawns"]["local"]["name"]
        receipt = (await action(host, dialogue.APPROACH, role))["native_dialogue_approach"]
        assert receipt == {"pawn": status["gameplay"]["native_pawns"][role], "npc": dialogue.MOSBY_NPC}, receipt
        await wait(guest_runs[index], lambda state: connected(state) and state.get("generation") == local["generation"]
                   and view(state).get("state") == "armed" and view(state).get("pawn") == pawn, timeout=20)
        return process, local["generation"], pawn

    async def press(target, expected_row):
        pending = (await action(guests[0], dialogue.FIXTURE, target))["native_dialogue_view"]
        assert (pending["state"], pending["pawn"]) == ("pending", left[2]), pending["state"]
        expected = ("open" if expected_row else "closed", expected_row or "-", pending["sequence"], left[2])
        state = await wait(guest_runs[0], lambda status: status.get("generation") == left[1] and tuple(view(status).get(key) for key in (
            "state", "row", "sequence", "pawn")) == expected, timeout=26)
        result = view(state)
        evidence["stock_input"].append({key: result[key] for key in ("sequence", "row", "state")})
        return result

    async def open_mosby(index):
        report = (await action(guests[index], dialogue.OPEN, dialogue.MOSBY))["native_dialogue"]
        assert (report["profile"], report["npc"], report["row"], report["lines"]) == (
            dialogue.MOSBY, dialogue.MOSBY_NPC, dialogue.GREET, ["How can I help you today?"]), report["row"]
        return report

    async def choose(index, reply, ordinal, **options):
        result = await action(guests[index], dialogue.CHOOSE, f"{reply['token']}@{ordinal}", **options)
        return result.get("native_dialogue", result)

    def choices(report):
        return [option["ordinal"] for option in report["choices"]]

    try:
        initial_host = await launch("host", host, "host", "--port", "0", "--start-zone", dialogue.SCIENTIST_SHOP, *host_flags)
        initial = await wait(run, hosting, timeout=70)
        port = initial["endpoint"]["port"]
        invitations = []
        for index in range(2):
            invite = await command("invite", *host, "--name", f"Mosby guest {index + 1}", "--character", f"Research Scout {index + 1}")
            invitations.append(invite)
            enrolled = await enroll(index, invite, initial, f"guest-{index + 1}")
            if index == 0:
                left = enrolled
            else:
                right = enrolled
        assert invitations[0]["character_id"] != invitations[1]["character_id"] and left[:2] != right[:2]

        before_dialogue = campaign_save_hashes(run.parent)
        assert str(Path("save/campaign.json")) in before_dialogue, before_dialogue
        left_root = await press("open", dialogue.GREET)
        before = await open_mosby(1)
        assert left_root["token"] != before["token"]
        initial_progress = before["progress"]
        assert tuple(initial_progress[key] for key in dialogue.PROGRESS_KEYS[:4]) == (0, 0, 0, None), initial_progress
        other_questions = await choose(1, before, 4)
        assert choices(other_questions) == [1, 3], choices(other_questions)
        assert (await choose(1, other_questions, 6, expected=False))["error"] == "native_dialogue_choice_unavailable"
        await press("4", dialogue.QUESTIONS)
        await press("3", dialogue.ANOMALIES)
        assert (await choose(1, other_questions, 5, expected=False))["error"] == "native_dialogue_choice_unavailable"
        other_anomalies = await choose(1, other_questions, 3)
        assert other_anomalies["effect"] == "topic_already_known" and other_anomalies["progress"]["topic_eather_drill"] == 1
        other_questions = await choose(1, other_anomalies, 1)
        assert choices(other_questions) == [1, 3, 5], choices(other_questions)

        await press("1", dialogue.QUESTIONS)
        await press("5", dialogue.DRILL)
        other_drill = await choose(1, other_questions, 5)
        assert other_drill["effect"] == "topic_already_known" and other_drill["progress"]["topic_drill_data"] == 1
        other_questions = await choose(1, other_drill, 1)
        assert choices(other_questions) == [1, 3, 5, 6], choices(other_questions)
        await press("1", dialogue.QUESTIONS)
        left_offer = await press("6", dialogue.OFFER)
        other_offer = await choose(1, other_questions, 6)
        assert other_offer["row"] == dialogue.OFFER and len(other_offer["lines"]) == 4
        assert (await choose(1, left_offer, 2, expected=False))["error"] == "native_dialogue_choice_expired"
        await press("2", None)

        operation = uuid.uuid4().hex
        settlement = await choose(1, other_offer, 2, operation=operation)
        assert (settlement["effect"], settlement["state"], settlement["revision"]) == ("stone_maze_already_present", "closed", None)
        assert await choose(1, other_offer, 2, operation=operation) == settlement
        progress = {**initial_progress, "topic_eather_drill": 1, "topic_drill_data": 1, "quest_step": 1}
        assert settlement["progress"] == progress, settlement["progress"]
        primary_actor = invitations[0]["character_id"]
        record = {"quest_id": dialogue.MOSBY_QUEST, "step": 1, "actor": primary_actor}
        await wait(run, lambda state: state.get("gameplay", {}).get("native_quests", {}).get(dialogue.MOSBY_QUEST) == record)
        lines = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        writes = [row for row in lines if row.get("event") == "native_dialogue_committed"]
        effects = ("topic_eather_drill", "topic_drill_data", "stone_maze_issued")
        assert [(row["effect"], row["character"], row["revision"]) for row in writes] == [
            (effect, primary_actor, None) for effect in effects
        ], writes
        assert campaign_save_hashes(run.parent) == before_dialogue, "A topic or quest offer wrote a save"
        checkpoint = await action(host, "checkpoint")
        assert checkpoint.get("revision") and campaign_save_hashes(run.parent) != before_dialogue
        evidence["deferred_save"] = {"unchanged_files": len(before_dialogue), "checkpoint": checkpoint["revision"]}
        serials = [row["sequence"] for row in evidence["stock_input"]]
        assert serials == sorted(set(serials)), serials
        evidence.update(quest=record, canonical_progress=progress, writes=writes, sibling_replay=settlement["effect"])

        await stop(initial_host, host)
        await stop(left[0], guests[0])
        await stop(right[0], guests[1])
        await launch("host", host, "host-restart", "--port", str(port), *host_flags)
        restarted = await wait(run, lambda state: hosting(state) and state.get("generation") != initial["generation"], timeout=75)
        assert restarted["endpoint"]["port"] == port
        invited = invitations[0]
        renewed = await action(host, "refresh-invite", invited["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invited["guest_id"], invited["character_id"])
        await enroll(0, renewed, restarted, "guest-restart")
        assert (await choose(0, left_offer, 2, expected=False))["error"] == "native_dialogue_choice_expired"
        restored = await open_mosby(0)
        assert restored["progress"] == progress, restored["progress"]
        await wait(run, lambda state: state.get("gameplay", {}).get("native_quests", {}).get(dialogue.MOSBY_QUEST) == record)
        evidence.update(same_port_restart={"port": port, "generation": restarted["generation"], "progress": restored["progress"]}, verified=True)
    except BaseException as error:
        evidence.update(verified=False, failure=str(error))
        raise
    finally:
        try:
            for process, owner in reversed(processes):
                if process.returncode is None:
                    try:
                        await stop(process, owner)
                    except (OSError, RuntimeError, TimeoutError, AssertionError):
                        if process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 12)
        finally:
            if original is not None:
                evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
            (run / "live-mosby-dialogue-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original Saved directory changed during the smoke"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="prepared isolated stage workspace")
    parser.add_argument("--vanilla-saved", type=Path, help="original Saved directory to hash before and after")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved))
