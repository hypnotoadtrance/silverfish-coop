"""Accept, dig, and redeem the stock Missing Contact contract on loopback."""

import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_computer_board_smoke import stock_flags
from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.silverfish import native_computer_board as board, native_host_dig as dig, native_host_save
from tools.silverfish import native_host_dig_live as custody, native_host_dig_live_fixture as dig_input
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import submit
from tools.silverfish.travel import BLACK_MARKET, CROSSROADS, LAKE


SPEC = board.MISSING_CONTACT
BOARD = board.BOARDS[BLACK_MARKET]
BALANCES = ("Player_Cash", "Player_Xp", "Rep_Freelancer", "Rep_State", "Adv_Freelancer", dig.FLAG)
ROUTE = (BLACK_MARKET, LAKE, CROSSROADS, LAKE, BLACK_MARKET)
JOURNAL_FIXTURE = "native-package-handoff-journal-fixture"
JOURNAL_STATE = "native_package_handoff_journal"


async def exercise(work: Path, vanilla_saved: Path, *, guest_turnin: bool = False, journal_handoff: bool = False) -> None:
    guest_turnin = guest_turnin or journal_handoff
    originals = saved_hashes(vanilla_saved)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory")
    name = "missing-contact-" + uuid.uuid4().hex[:8]
    campaign = work / "campaigns" / name
    run, guest_run = campaign / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    host_options = ("--campaign", name, "--headless", "--fixtures", "--native-events", "--native-guest",
                    "--native-zones", "--native-expanded-zones", "--native-host-dig-live-fixtures")
    guest_options = ("--profile", name, "--headless", "--native-zones")
    jobs, evidence = [], {"verified": False, "guest_turnin": guest_turnin, "journal_handoff": journal_handoff,
                          "routes": [], "board_input": []}

    async def action(directory, verb, *, accepted=True, **request):
        reply = await submit(directory, verb, **request)
        assert reply.get("ok") is accepted, (verb, reply)
        return reply

    async def launch(directory, stage, *args):
        with (run / f"{stage}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((directory, process))
        return process

    async def stop(directory, process):
        if process.returncode is None:
            await action(directory, "stop")
            await asyncio.wait_for(process.wait(), 20)
        assert process.returncode == 0

    async def paired(area, character, previous=None):
        host = await wait(run, lambda state: state.get("state") == "hosting" and state.get("link_connected") is True
                          and state.get("game", {}).get("map") == area
                          and (state.get("generation"), state.get("zone_epoch")) != previous
                          and state["game"].get("native_guest_save", {}).get("state") == "bound", timeout=75)
        await wait(guest_run, lambda state: state.get("state") == "connected" and state.get("link_connected") is True
                   and state.get("character_id") == character and state.get("host_session") == host["generation"]
                   and state.get("zone_epoch") == host["zone_epoch"] and state.get("game", {}).get("map") == area, timeout=75)
        return host

    async def restart_pair(host, guest, current, invite, stage):
        await stop(run, host)
        await stop(guest_run, guest)
        port = current["endpoint"]["port"]
        prior = current["generation"], current["zone_epoch"]
        host = await launch(run, f"host-{stage}", "host", *host_options, "--port", str(port))
        await wait(run, lambda doc: doc.get("state") == "hosting" and doc.get("generation") != prior[0])
        renewed = await action(run, "refresh-invite", target=invite["guest_id"])
        assert (renewed["guest_id"], renewed["character_id"]) == (invite["guest_id"], invite["character_id"]), renewed
        guest = await launch(guest_run, f"guest-{stage}", "join", *guest_options, "--invite", renewed["invite_file"])
        restored = await paired(BLACK_MARKET, invite["character_id"], prior)
        assert restored["endpoint"]["port"] == port, restored["endpoint"]
        return host, guest, restored

    async def follow(source, destination, previous, character):
        route, = (route for route in AUTHORED_ROUTES if (route.source, route.destination) == (source, destination))
        evidence["pending_route"] = (source, destination, route.entrance, route.actor)
        if route.kind == "door":
            for actor in ("host", "guest"):
                await action(run, "native-door-position", target=actor, exit_position="near", entrance=route.entrance)
            await action(run, "native-door-probe", target="host", entrance=route.entrance)
        else:
            args = {"entrance": route.entrance, "exit_actor": route.actor}
            for actor in ("host", "guest"):
                await action(run, "native-exit-position", target=actor, exit_position="near", **args)
            await action(run, "native-exit-position", target="host", exit_position="inside", **args)
        current = await paired(destination, character, (previous["generation"], previous["zone_epoch"]))
        evidence["routes"].append(evidence.pop("pending_route"))
        return current

    async def board_state(directory, *states):
        await wait(directory, lambda doc: (row := doc.get("game", {}).get("native_wire_board", {}))
                   and row.get("map") == BLACK_MARKET and row.get("board") == BOARD and row.get("state") in states)

    async def press(directory, state):
        row = (await action(directory, "native-wire-board-fixture", target=SPEC.quest_id))["native_wire_board"]
        assert (row["map"], row["board"], row["quest"], row["state"]) == (BLACK_MARKET, BOARD, SPEC.quest_id, state), row
        evidence["board_input"].append(row)

    def party_state(doc):
        return doc.get("campaign", {}).get("world", {}).get("party", {})

    async def committed(step):
        status = await wait(run, lambda doc: party_state(doc).get("native_quests", {}).get(SPEC.quest_id, {}).get("step") == step)
        return party_state(status)["native_quests"][SPEC.quest_id]

    async def inventory(directory):
        page = (await action(directory, "native-inventory-list"))["native_inventory"]
        assert page["next_offset"] is None, page
        return {item["guid"]: {key: value for key, value in item.items() if key != "index"} for item in page["items"]}

    async def journal_transfer(owned, host_items, guest_items):
        async def press_journal(operation):
            result = await action(run, JOURNAL_FIXTURE, target=operation)
            assert result["native_package_handoff_journal_fixture"] == operation, result

        async def journal_status(*states):
            def shown(status):
                game = status.get("game", {})
                row = game.get(JOURNAL_STATE, {})
                pawn = game.get("pawns", {}).get("local", {}).get("name")
                return (game.get("native_host_invite_journal") == "journal"
                        and row.get("state") in states and pawn and row.get("pawn") == pawn)

            return await wait(run, shown)

        proof = evidence["stock_journal"] = {}
        await press_journal("open")
        await journal_status("idle")
        await press_journal("handoff-open")
        choosing = (await journal_status("choosing"))["game"][JOURNAL_STATE]
        assert choosing["offer_id"], choosing
        proof["choosing"] = choosing

        await press_journal("handoff-back")
        canceled = await journal_status("idle")
        assert party_state(canceled)[custody.CUSTODY] == owned, canceled
        assert await inventory(run) == host_items and await inventory(guest_run) == guest_items
        proof["canceled"] = canceled["game"][JOURNAL_STATE]

        await press_journal("handoff-open")
        reopened = (await journal_status("choosing"))["game"][JOURNAL_STATE]
        assert reopened == choosing, (choosing, reopened)
        await press_journal("handoff-confirm")
        settled = await journal_status("transferred", "refused")
        result = settled["game"][JOURNAL_STATE]
        proof["result"] = result
        assert (result["state"], result["offer_id"]) == ("transferred", None), result
        handoff = party_state(settled)[custody.CUSTODY]

        await press_journal("close")
        await wait(run, lambda status: "native_host_invite_journal" not in status.get("game", {}))
        proof["closed"] = True
        return handoff

    async def settlement_replay(directory, receipt):
        replay = await action(directory, "native-quest-turnin", target=SPEC.quest_id)
        assert replay["effect"] == "native_quest_already_settled", replay
        assert {field: replay[field] for field in receipt} == receipt, replay
        return replay

    async def balances(character):
        await action(run, "checkpoint")
        saved = json.loads((campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
        profile = campaign / "game-user"
        native_host_save.verify_checkpoint(profile, saved)
        authority = saved["world"]["native_host_save"]
        guest = saved["characters"][character]["state"]["native_guest_save"]
        assert authority["slot"] != guest["slot"] and guest["character_id"] == character, (authority, guest)
        entries = [stock_flags(native_host_save.directory(profile), row, BLACK_MARKET) for row in (authority, guest)]
        return [{name: entry.get(name, 0) for name in BALANCES} for entry in entries]

    try:
        host = await launch(run, "host", "host", *host_options, "--port", "0", "--start-zone", BLACK_MARKET)
        await wait(run, lambda doc: doc.get("state") == "hosting")
        invite = await action(run, "invite", name="Missing Contact loopback", character="Courier")
        character = invite["character_id"]
        guest = await launch(guest_run, "guest", "join", *guest_options, "--invite", invite["invite_file"])
        current = await paired(BLACK_MARKET, character)
        await action(run, SPEC.command("approach"), target=character)
        offer = (await action(guest_run, SPEC.command("preflight")))[SPEC.key]
        assert (offer["step_after"], offer["xp_before"], offer["board"], offer["objective_qualified"]) == (None, 0, BOARD, False), offer
        evidence["stock_availability"] = offer
        offset = 0
        while offset is not None:
            page = (await action(guest_run, "native-bounty-board-list", target=str(offset)))["native_bounty_board"]
            assert (page["board"], page["offset"]) == (BOARD, offset), page
            if row := next((row for row in page["quests"] if row["id"] == SPEC.quest_id), None):
                assert row["co_op"] == "experimental", row
                evidence["stock_offer"] = row
                break
            offset = page["next_offset"]
        assert "stock_offer" in evidence, "The stock board did not advertise Missing Contact"
        await board_state(guest_run, "armed", "focused")
        await press(guest_run, "loading")
        await board_state(guest_run, "offer")
        await press(guest_run, "starting")
        accepted = await committed(1)
        assert (accepted["actor"], accepted["quest_id"]) == (character, SPEC.quest_id), accepted
        evidence["accepted"] = accepted
        await action(run, SPEC.command("approach"), target="host")
        missing = await action(run, "native-quest-turnin", accepted=False, target=SPEC.quest_id)
        assert missing["error"] == "stock_missing_contact_objective_required", missing
        evidence["empty_host_refusal"] = missing["error"]

        for source, destination in zip(ROUTE, ROUTE[1:]):
            current = await follow(source, destination, current, character)
            if destination == CROSSROADS:
                await wait(run, lambda doc: doc.get("game", {}).get(custody.KEY, {}).get("state") == "ready")
                equipped = (await action(run, dig_input.ACTION, target="acquire"))[dig.KEY]
                assert equipped["state"] == "equipped" and equipped["stock_f"] > 0, equipped
                dug = await action(run, dig_input.ACTION, target="press")
                escrow = dug[custody.CUSTODY]
                assert (escrow["state"], escrow["source"], escrow["flag_value"], escrow["spade_guid"]) == (
                    "escrowed", dig.SOURCE, 1, equipped["guid"]), escrow
                collected = await action(run, dig_input.ACTION, target="collect")
                owned = collected[custody.CUSTODY]
                assert (owned["state"], owned["source"], owned["spade_guid"], collected[custody.KEY]["escrow_guid"]) == (
                    "owned", dig.SOURCE, equipped["guid"], escrow["package_guid"]), collected
                package = owned["package_guid"]
                stock = (await inventory(run))[package]
                assert (stock["source"], stock["class_path"], stock["quantity"], stock["stackable"]) == (
                    "backpack", dig.PACKAGE_CLASS, 1, False), stock
                assert package not in await inventory(guest_run), dug
                evidence["stock_dig"] = {"escrow": escrow, "collected": owned}

        await action(run, SPEC.command("approach"), target=character)
        wrong_owner = await action(guest_run, "native-quest-turnin", accepted=False, target=SPEC.quest_id)
        assert wrong_owner["error"] == "stock_missing_contact_objective_required", wrong_owner
        evidence["wrong_owner_refusal"] = wrong_owner["error"]
        await action(run, SPEC.command("approach"), target="host")
        redeemer, redeem_run, owner_kind = (character, guest_run, "guest") if guest_turnin else ("host", run, "host")
        if guest_turnin:
            host_items, guest_items = await inventory(run), await inventory(guest_run)
            assert package not in guest_items, guest_items
            if journal_handoff:
                handoff = await journal_transfer(owned, host_items, guest_items)
            else:
                handoff = (await action(run, custody.HANDOFF, target=character))[custody.CUSTODY]
            guest_items[package] = host_items.pop(package)
            assert (handoff["owner"], handoff["package_guid"]) == (character, package), handoff
            assert await inventory(run) == host_items and await inventory(guest_run) == guest_items, handoff
            same_handoff = (await action(run, custody.HANDOFF, target=character))[custody.CUSTODY]
            assert same_handoff == handoff, (handoff, same_handoff)
            former_owner = await action(run, "native-quest-turnin", accepted=False, target=SPEC.quest_id)
            assert former_owner["error"] == "stock_missing_contact_objective_required", former_owner
            assert await inventory(run) == host_items and await inventory(guest_run) == guest_items, former_owner
            evidence.update(stock_handoff=handoff, handoff_replay=same_handoff, former_owner_refusal=former_owner["error"])

            unredeemed_balances = await balances(character)
            host, guest, current = await restart_pair(host, guest, current, invite, "handoff-restart")
            restored_party = party_state(current)
            assert restored_party[custody.CUSTODY] == handoff, restored_party[custody.CUSTODY]
            assert restored_party["native_quests"][SPEC.quest_id] == accepted, restored_party["native_quests"]
            await action(run, SPEC.command("approach"), target="host")
            await action(run, SPEC.command("approach"), target=character)
            restored_refusal = await action(run, "native-quest-turnin", accepted=False, target=SPEC.quest_id)
            assert restored_refusal["error"] == "stock_missing_contact_objective_required", restored_refusal
            restored_inventory = await inventory(guest_run)
            assert await inventory(run) == host_items and restored_inventory == guest_items, restored_inventory
            evidence["handoff_restart"] = dict(generation=current["generation"], port=current["endpoint"]["port"],
                                               custody=restored_party[custody.CUSTODY], package=restored_inventory[package],
                                               former_owner_refusal=restored_refusal["error"])

        active = (await action(redeem_run, SPEC.command("preflight")))[SPEC.key]
        assert (active["step_after"], active["objective_guid"], active["objective_owner"], active["objective_qualified"]) == (
            1, package, owner_kind, True), active
        before = await balances(character)
        assert before[0][dig.FLAG] == 1 and before[1][dig.FLAG] == 0
        if guest_turnin:
            assert before == unredeemed_balances, (unredeemed_balances, before)
            evidence["handoff_restart"]["stock_balances"] = before
        effective_reputation = {key: active["reputation"][key]["before"] for key in ("8", "4")}
        evidence["effective_reputation_before"] = effective_reputation
        await board_state(redeem_run, "armed", "focused")
        await press(redeem_run, "loading")
        await board_state(redeem_run, "ready")
        await press(redeem_run, "redeeming")
        done = await committed(-10)
        assert (done["cash"], done["xp"], done[SPEC.consumed_field], done["objective_owner"], done["actor"]) == (
            450, 1, package, redeemer, redeemer), done
        assert set(done["recipients"]) == {"host", character}, done
        assert done["wallet"] == before[0]["Player_Cash"] + 450, done
        expected_reputation = {"8": effective_reputation["8"] + 40, "4": effective_reputation["4"] - 5}
        assert done["reputation"] == expected_reputation, done
        if guest_turnin:
            completed = await action(run, custody.HANDOFF, accepted=False, target=character)
            assert completed["error"] == "stock_missing_contact_handoff_quest_already_completed", completed
            evidence.update(completed_handoff_refusal=completed["error"], duplicate_turn_in=await settlement_replay(redeem_run, done))
        assert package not in await inventory(run) and package not in await inventory(guest_run), done

        def guest_reward_applied(status):
            players = status.get("gameplay", {}).get("players", {}).values()
            return any(player.get("character_id") == character and player.get("quest_xp") == 1
                       and not player.get("native_quest_xp_pending") for player in players)

        await wait(run, guest_reward_applied)
        expected_host = dict(before[0], Player_Cash=done["wallet"], Player_Xp=before[0]["Player_Xp"] + 1,
                             Rep_Freelancer=expected_reputation["8"], Rep_State=expected_reputation["4"])
        expected_guest = dict(before[1], Player_Xp=before[1]["Player_Xp"] + 1)
        after = await balances(character)
        assert after == [expected_host, expected_guest], (before, after)
        evidence.update(stock_turn_in=done, stock_balances=after)

        await restart_pair(host, guest, current, invite, "restart")
        replay = await settlement_replay(redeem_run, done)
        assert await balances(character) == after
        assert package not in await inventory(run) and package not in await inventory(guest_run)
        evidence.update(verified=True, restart=replay)
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for directory, process in reversed(jobs):
            if process.returncode is None:
                try:
                    await stop(directory, process)
                except (OSError, RuntimeError, AssertionError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                        await process.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (run / "live-missing-contact-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--guest-turnin", action="store_true", help="Hand the stock package to the guest for board redemption")
    parser.add_argument("--journal-handoff", action="store_true", help="Use the stock Journal to hand off the guest's package")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), guest_turnin=args.guest_turnin,
                         journal_handoff=args.journal_handoff))
