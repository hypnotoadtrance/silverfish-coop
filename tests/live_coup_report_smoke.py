"""Verify the placed Dead End note and its stock Borough contract on loopback."""

import argparse
import asyncio
from dataclasses import dataclass
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

from tools.silverfish import native_host_save
from tools.silverfish import native_computer_board as collections
from tools.silverfish.authored_routes import AUTHORED_ROUTES, AuthoredRoute
from tools.silverfish.native_authored_pickup import DEAD_END, SOURCES, STATE_KEY as PICKUPS
from tools.silverfish.travel import BOROUGH, CITY, CROSSROADS, NORTHEAST_RAIL, RAIL_YARD


@dataclass(frozen=True)
class CollectionSmoke:
    name: str
    spec: collections.CollectionContract
    maps: tuple[str, ...]
    reputation: dict[str, tuple[str, int]]
    accept_before_pickup: bool = False
    stock_pickup: bool = False
    stackable: bool = False

    def routes(self) -> tuple[AuthoredRoute, ...]:
        routes = []
        for source, destination in zip(self.maps, self.maps[1:]):
            matches = [route for route in AUTHORED_ROUTES if (route.source, route.destination) == (source, destination)]
            if len(matches) != 1:
                raise ValueError(f"Choose one exact stock departure from {source} to {destination}")
            routes.append(matches[0])
        return tuple(routes)


NORTHERN_PASSAGE = CollectionSmoke(
    "coup-report", collections.COUP_REPORT, (DEAD_END, NORTHEAST_RAIL, CROSSROADS, RAIL_YARD, CITY, BOROUGH),
    {"2": ("Rep_Commune", 60), "5": ("Rep_Coup", -100)},
)


async def exercise(work, vanilla_saved, *, board_input=False, plan=NORTHERN_PASSAGE):
    spec = plan.spec
    quest, redemption, source = spec.quest_id, spec.redeem_map, spec.pickup_source
    pickup_map, pickup_actor, pickup_class = SOURCES[source]
    board = collections.BOARDS[redemption]
    routes = plan.routes()
    name = plan.name + "-" + uuid.uuid4().hex[:8]
    root = work / "campaigns" / name
    run, client_run = root / "run", work / "clients" / name / "run"
    run.mkdir(parents=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved)
    if not original:
        raise ValueError("Supply the original single-player Saved directory")
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host, guest = ("--campaign", name), ("--profile", name)
    host_flags = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones", "--native-expanded-zones")
    evidence = {"routes": [], "stock_board_input": board_input, "verified": False}
    jobs = []

    async def command(*args, accepted=True):
        proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), 45)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if not out or (proc.returncode == 0) != accepted:
            raise RuntimeError(f"{args}: {out.decode(errors='replace')} {err.decode(errors='replace')}")
        return json.loads(out)

    async def action(owner, verb, *args, accepted=True):
        return await command("action", *owner, verb, *args, accepted=accepted)

    async def launch(owner, label, *args):
        with (run / f"{label}.txt").open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*cli, *args, cwd=REPO, stdout=log, stderr=log,
                                                        creationflags=subprocess.CREATE_NO_WINDOW)
        jobs.append((owner, proc))
        return proc

    async def stop(owner, proc):
        if proc.returncode is None:
            await action(owner, "stop")
        assert await asyncio.wait_for(proc.wait(), 20) == 0

    async def paired(area, prior=None):
        authority = await wait(run, lambda s: s.get("state") == "hosting" and s.get("link_connected") is True
                               and s.get("game", {}).get("map") == area
                               and s["game"].get("native_guest_save", {}).get("state") == "bound"
                               and (s.get("generation"), s.get("zone_epoch")) != prior, timeout=70)
        await wait(client_run, lambda s: s.get("state") == "connected" and s.get("character_id") == character
                   and s.get("host_session") == authority["generation"] and s.get("zone_epoch") == authority["zone_epoch"]
                   and s.get("game", {}).get("map") == area, timeout=70)
        return authority

    async def inventory(owner):
        page = (await action(owner, "native-inventory-list"))["native_inventory"]
        assert page["next_offset"] is None, page
        return {row["guid"]: row for row in page["items"]}

    async def stock_balances():
        await action(host, "checkpoint")
        save = json.loads((root / "save" / "campaign.json").read_text(encoding="utf-8"))
        owner = save["world"]["native_host_save"]
        member = save["characters"][character]["state"]["native_guest_save"]
        assert owner["slot"] != member["slot"] and member["character_id"] == character, member
        directory = native_host_save.directory(root / "game-user")
        wanted = ("Player_Cash", "Player_Xp", *(flag for flag, _ in plan.reputation.values()))
        balances = {}
        for role, frame in (("host", owner), ("guest", member)):
            flags = stock_flags(directory, frame, redemption)
            keys = wanted if plan.reputation else (*wanted, *(key for key in flags if key.startswith("Rep_")))
            balances[role] = {key: flags.get(key, 0) for key in keys}
        return balances

    async def board_state(*states):
        saved = await wait(client_run, lambda s: (row := s.get("game", {}).get("native_wire_board", {}))
                           and row.get("map") == redemption and row.get("board") == board and row.get("state") in states)
        return saved["game"]["native_wire_board"]

    async def board_press(expected):
        pressed = (await action(guest, "native-wire-board-fixture", "--target", quest))["native_wire_board"]
        assert (pressed["map"], pressed["board"], pressed["quest"], pressed["state"]) == (redemption, board, quest, expected), pressed
        evidence.setdefault("board_presses", []).append(pressed)

    async def committed_quest(step):
        def record(state):
            return state.get("campaign", {}).get("world", {}).get("party", {}).get("native_quests", {}).get(quest, {})

        saved = await wait(run, lambda state: record(state).get("step") == step)
        return record(saved)

    async def accept():
        await action(host, spec.command("approach"), "--target", character)
        if start_area != BOROUGH and redemption == BOROUGH:
            refused = await action(guest, "native-quest-start", "--target", quest, accepted=False)
            assert "not_offered" in refused["error"], refused
            evidence["before_host_xp"] = refused["error"]
            await action(host, "native-turret-photo-xp-fixture")
        offered = (await action(guest, spec.command("preflight")))[spec.key]
        assert offered["board"] == board and offered["step_after"] is None, offered
        host_xp = offered["xp_before"]
        assert (host_xp == 0 if plan.accept_before_pickup else host_xp >= 3), offered
        offset = 0
        while offset is not None:
            catalog = (await action(guest, "native-bounty-board-list", "--target", str(offset)))["native_bounty_board"]
            assert (catalog["board"], catalog["offset"]) == (board, offset), catalog
            offers = {row["id"]: row["co_op"] for row in catalog["quests"]}
            if quest in offers:
                assert offers[quest] in ("experimental", "verified"), catalog
                evidence["stock_catalog"] = {"board": catalog["board"], "co_op": offers[quest], "host_xp": offered["xp_before"]}
                break
            next_offset = catalog["next_offset"]
            assert next_offset is None or offset < next_offset <= 128, catalog
            offset = next_offset
        assert "stock_catalog" in evidence, catalog
        if board_input:
            await board_state("armed", "focused")
            await board_press("loading")
            await board_state("offer")
            await board_press("starting")
            accepted = await committed_quest(1)
        else:
            accepted = await action(guest, "native-quest-start", "--target", quest)
        assert (accepted["quest_id"], accepted["step"], accepted["actor"]) == (quest, 1, character), accepted
        evidence["accepted"] = accepted

    try:
        start_area = plan.maps[0]
        authority = await launch(host, "host", "host", *host, "--port", "0", "--start-zone", start_area, *host_flags)
        ready = await wait(run, lambda s: s.get("state") == "hosting")
        invitation = await command("invite", *host, "--name", f"{spec.title} tester", "--character", "Courier")
        character = invitation["character_id"]
        peer = await launch(guest, "guest", "join", *guest, "--headless", "--native-zones", "--invite", invitation["invite_file"])
        current = await paired(start_area)
        if start_area == BOROUGH:
            evidence["stock_xp_setup"] = await action(host, "native-turret-photo-xp-fixture")

        async def collect_pickup():
            selected = ("--pickup-source", source)
            await action(host, "native-authored-pickup-approach", "--target", "guest", *selected)
            if plan.stock_pickup:
                await wait(client_run, lambda state: state.get("game", {}).get("native_authored_pickup_sources", {})
                           .get(source, {}).get("state") == "available")
                interaction = (await action(guest, "native-authored-pickup-stock-fixture", *selected))["native_authored_pickup_interaction"]
                assert interaction["source"] == source and interaction["entry"] == "stock", interaction
                status = await wait(run, lambda state: source in state.get("gameplay", {}).get(PICKUPS, {}))
                claim = status["gameplay"][PICKUPS][source]
            else:
                interaction = (await action(guest, "native-authored-pickup-fixture", *selected))["native_authored_pickup_interaction"]
                assert interaction["source"] == source and interaction["entry"] == "fixture", interaction
                claim = (await action(guest, "native-authored-pickup-claim", "--target", source))["native_authored_pickup_claim"]
            assert (claim["source"], claim["map"], claim["actor"], claim["owner"]) == (source, pickup_map, pickup_actor, character), claim
            guid = claim["guid"]
            row = (await inventory(guest))[guid]
            assert (row["class_path"], row["quantity"], row["source"]) == (pickup_class, 1, "backpack"), row
            assert row["stackable"] is plan.stackable, row
            assert guid not in await inventory(host)
            evidence["stock_pickup"] = {"interaction": interaction, "claim": claim, "inventory": row}
            return guid

        if plan.accept_before_pickup:
            assert start_area == redemption and pickup_map != start_area
            await accept()
            refused = await action(guest, "native-quest-turnin", "--target", quest, accepted=False)
            assert "objective_required" in refused["error"], refused
            evidence["turn_in_without_item"] = refused["error"]
        guid = await collect_pickup() if start_area == pickup_map else None
        for route in routes:
            evidence["pending_route"] = (route.source, route.destination, route.entrance, route.actor)
            endpoint = ("--entrance", route.entrance)
            if route.kind == "door":
                for role in ("host", "guest"):
                    await action(host, "native-door-position", "--target", role, "--exit-position", "near", *endpoint)
                await action(host, "native-door-probe", "--target", "host", *endpoint)
            else:
                endpoint += ("--exit-actor", route.actor)
                for role in ("host", "guest"):
                    await action(host, "native-exit-position", "--target", role, "--exit-position", "near", *endpoint)
                await action(host, "native-exit-position", "--target", "host", "--exit-position", "inside", *endpoint)
            current = await paired(route.destination, (current["generation"], current["zone_epoch"]))
            evidence["routes"].append(evidence.pop("pending_route"))
            if route.destination == pickup_map and guid is None:
                guid = await collect_pickup()

        assert guid is not None and current["game"]["map"] == redemption
        if plan.accept_before_pickup:
            await action(host, spec.command("approach"), "--target", character)
            if board_input:
                await board_state("armed", "focused")
                await board_press("loading")
        else:
            await accept()
        active = (await action(guest, spec.command("preflight")))[spec.key]
        assert (active["step_after"], active["objective_guid"], active["objective_owner"]) == (1, guid, "guest"), active
        reputation = active["reputation"]
        evidence["effective_stock_reputation_before"] = {faction: row["before"] for faction, row in reputation.items()}
        before = await stock_balances()
        evidence["stock_before"] = before
        if board_input:
            await board_state("ready")
            await board_press("redeeming")
            redeemed = await committed_quest(-10)
        else:
            redeemed = await action(guest, "native-quest-turnin", "--target", quest)
        assert (redeemed["step"], redeemed["cash"], redeemed["xp"]) == (-10, spec.cash, 1), redeemed
        assert set(redeemed["recipients"]) == {"host", character}, redeemed
        assert (redeemed[spec.consumed_field], redeemed["objective_owner"]) == (guid, character), redeemed
        assert guid not in await inventory(guest) and guid not in await inventory(host)
        after = await stock_balances()
        expected_reputation = {flag: reputation[faction]["before"] + delta
                               for faction, (flag, delta) in plan.reputation.items()}
        assert after["host"] == dict(before["host"], Player_Cash=before["host"]["Player_Cash"] + spec.cash,
                                     Player_Xp=before["host"]["Player_Xp"] + 1, **expected_reputation), after
        assert after["guest"] == dict(before["guest"], Player_Xp=before["guest"]["Player_Xp"] + 1), after
        evidence.update(stock_turn_in=redeemed, stock_after=after)
        assert (await action(guest, "native-quest-turnin", "--target", quest))["effect"] == "native_quest_already_settled"

        await stop(host, authority)
        await stop(guest, peer)
        await launch(host, "host-restart", "host", *host, "--port", str(ready["endpoint"]["port"]), *host_flags)
        await wait(run, lambda s: s.get("state") == "hosting" and s.get("generation") != ready["generation"])
        renewed = await action(host, "refresh-invite", "--target", invitation["guest_id"])
        await launch(guest, "guest-restart", "join", *guest, "--headless", "--native-zones", "--invite", renewed["invite_file"])
        restarted = await paired(redemption)
        assert guid not in await inventory(guest) and await stock_balances() == after
        assert (await action(guest, "native-quest-turnin", "--target", quest))["effect"] == "native_quest_already_settled"
        evidence.update(verified=True, restarted_generation=restarted["generation"])
    except BaseException as error:
        evidence["failure"] = str(error)
        raise
    finally:
        for owner, proc in reversed(jobs):
            if proc.returncode is None:
                try:
                    await stop(owner, proc)
                except (OSError, RuntimeError, TimeoutError):
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
        evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        (run / f"live-{plan.name}-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        assert evidence["original_saves_verified"], "Original single-player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--board-input", action="store_true", help="use the original guest Interact binding at the stock board")
    args = parser.parse_args()
    asyncio.run(exercise(args.work.resolve(), args.vanilla_saved.resolve(), board_input=args.board_input))
