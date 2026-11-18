"""Optional Windows integration test. Launches two headless staged games on loopback."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

options = argparse.ArgumentParser(description=__doc__)
options.add_argument("--work", type=Path, required=True, help="root of a prepared stage workspace")
options.add_argument("--vanilla-saved", type=Path, help="optional original Saved directory to hash before and after")
options.add_argument("--gameplay", action="store_true", help="test replicated movement, pickup, revive and economy")
options.add_argument("--zones", action="store_true", help="test host-led travel and guest reconnection between test maps")
options.add_argument("--native-probe", action="store_true", help="exercise transient native stock Save ownership")
options.add_argument("--native-quest", action="store_true", help="exercise the allowlisted native quest start")
options.add_argument("--native-death", action="store_true", help="explicitly invoke the staged native death wrapper")
options.add_argument("--native-damage", action="store_true", help="exercise stock direct damage and its death handoff")
options.add_argument("--wipe", action="store_true", help="exercise wipe, item retention and backpack ownership")
args = options.parse_args()
work = args.work.resolve()
profile = "validation-" + uuid.uuid4().hex[:8]
campaign = profile
run = work / "campaigns" / campaign / "run"
client_run = work / "clients" / profile / "run"
base = [sys.executable, "-u", "-m", "tools.silverfish", "--work", str(work)]
records = []
processes = []
deadline = 40

def note(label, summary):
    message = {"check": label, **summary}
    records.append(message)
    print(json.dumps(message), flush=True)

def snapshot_hashes(directory):
    hashes = {}
    for file in directory.rglob("*"):
        if file.is_file():
            with file.open("rb") as stream:
                hashes[str(file.relative_to(directory))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


class InterruptibleRelay(asyncio.DatagramProtocol):
    """Model loss of the network path without changing any machine network settings."""

    def __init__(self, server):
        self.server = server
        self.client = None
        self.offline = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, source):
        if self.offline:
            return
        if source == self.server:
            if self.client:
                self.transport.sendto(data, self.client)
        else:
            self.client = source
            self.transport.sendto(data, self.server)

async def command(*args, check=True):
    process = await asyncio.create_subprocess_exec(*base, *args, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(process.communicate(), timeout=45)
    if check and process.returncode:
        raise RuntimeError(f"{args}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
    return json.loads(output)

async def start(*args):
    path = run / (args[0] + "-" + str(len(processes)) + ".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        process = await asyncio.create_subprocess_exec(*base, *args, stdout=output, stderr=output)
    processes.append((process, path))
    return process

async def wait(directory, condition):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        try:
            last = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if condition(last):
                return last
            if last.get("state") == "failed":
                raise RuntimeError(f"session failed: {last}")
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.2)
    last = last or {}
    summary = {key: last.get(key) for key in ("state", "link_connected", "zone_epoch")}
    summary["game"] = {key: last.get("game", {}).get(key) for key in ("state", "map", "diagnostic")}
    raise TimeoutError(f"Status did not reach the expected state: {summary}")

async def stop(process, flag, value):
    if process.returncode is None:
        await command("action", flag, value, "stop", check=False)
        await asyncio.wait_for(process.wait(), timeout=12)
    assert process.returncode == 0

async def gameplay_slice(host_status, character_id):
    marker = await wait(client_run, lambda state: len(state.get("game", {}).get("loot_actors", [])) == 1)
    host_marker = await wait(run, lambda state: len(state.get("game", {}).get("loot_actors", [])) == 1)
    assert host_marker["game"]["loot_actors"][0]["id"] == "playtest-toy"
    guest_marker = marker["game"]["loot_actors"][0]
    assert all(abs(a - b) < 2 for a, b in zip(
        guest_marker["location"], host_marker["game"]["loot_actors"][0]["location"], strict=True,
    ))
    note("replicated_loot_marker", {"location": guest_marker["location"]})

    previous = host_status["game"]["pawns"]["guest"]["location"]
    await command("action", "--profile", profile, "walk", "--x", "0", "--y", "1", "--milliseconds", "300")
    moved = await wait(run, lambda state: state["game"]["pawns"]["guest"]["location"][1] > previous[1] + 1.5)
    note("guest_movement_replicates_to_host", {
        "before": previous, "after": moved["game"]["pawns"]["guest"]["location"],
    })

    participants = (("--campaign", campaign, "host"), ("--profile", profile, "guest"))
    operation_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    attempts = [("action", flag, actor, "interact", "--target", "playtest-toy",
                 "--operation-id", operation_ids[index])
                for index, (flag, actor, _) in enumerate(participants)]
    results = await asyncio.gather(*(command(*attempt, check=False) for attempt in attempts))
    accepted = [index for index, result in enumerate(results) if result.get("ok")]
    assert len(accepted) == 1, results
    index = accepted[0]
    winner = participants[index][2]
    assert results[1 - index]["error"] == "already_claimed", results
    replay = await command(*attempts[index])
    assert replay["effect"] == "pickup" and replay["quantity"] == 1
    await wait(run, lambda status: not status.get("game", {}).get("loot_actors", []))
    observed = await wait(client_run, lambda status: not status.get("game", {}).get("loot_actors", [])
                          and (status.get("gameplay", {}).get("loot") or {}).get("claimed_by"))
    assert observed["gameplay"]["players"][winner]["inventory"] == {"toy-robot": 1}
    assert not observed["gameplay"]["players"][participants[1 - index][2]]["inventory"]
    note("pickup_commits_once_and_despawns", {"winner": winner, "loser": "already_claimed",
                                              "duplicate_result": replay["effect"]})

    down = await command("action", "--campaign", campaign, "test-damage", "--target", "guest", "--amount", "100")
    assert down["effect"] == "downed" and down["target"] == character_id
    effects = await wait(run, lambda status: status.get("game", {}).get("player_effects", {})
                         .get("guest", {}).get("crouched", False))
    target = effects["game"]["player_effects"]["guest"]
    assert target["jump_velocity"] == 0 and target["state"] == "downed"
    blocked = await command("action", "--profile", profile, "interact", check=False)
    assert not blocked["ok"] and blocked["error"] == "actor_downed", blocked
    note("downed_effects", {"host_view": target, "guest_interaction_blocked": True})

    revived = await command("action", "--campaign", campaign, "interact", "--target", character_id)
    assert revived["effect"] == "revive" and revived["health"] == 10
    await wait(client_run, lambda status: status.get("gameplay", {}).get("players", {}).get("guest", {})
               .get("health") == 10
               and status["game"]["player_effects"]["local"]["state"] == "standing")
    note("revive_restores_ten_percent", {"health": revived["health"]})
    return winner


async def smoke():
    original = args.vanilla_saved
    baseline = snapshot_hashes(original) if original else None
    relay = None
    try:
        fixture_test = any((args.gameplay, args.native_probe, args.native_quest, args.wipe,
                            args.native_death, args.native_damage))
        fixtures = ["--fixtures"] if fixture_test else []
        if args.wipe:
            fixtures += ["--fixture-keep-slots", "1", "--fixture-backpacks"]
        host = await start("host", "--campaign", campaign, "--port", "0", "--headless", *fixtures)
        host_status = await wait(run, lambda state: state.get("state") == "hosting")
        port = host_status["endpoint"]["port"]
        note("host_listening", {"udp_port": port, "map": host_status["game"]["map"],
                                "campaign_id": host_status["campaign"]["campaign_id"]})
        issued = await command("invite", "--campaign", campaign, "--name", "Validation guest",
                               "--character", "Validation character")
        loop = asyncio.get_running_loop()
        relay, interruption = await loop.create_datagram_endpoint(
            lambda: InterruptibleRelay(("127.0.0.1", port)), local_addr=("127.0.0.1", 0),
        )
        invitation = Path(issued["invite_file"])
        invitation_data = json.loads(invitation.read_text(encoding="utf-8"))
        invitation_data["port"] = relay.get_extra_info("sockname")[1]
        fixture = invitation.with_name("loss-test.invite.json")
        fixture.write_text(json.dumps(invitation_data), encoding="utf-8")
        chosen = issued["character_id"]
        if args.gameplay:
            catalog = await command("characters", "--invite", issued["invite_file"])
            assert {item["character_id"] for item in catalog["characters"]} == {chosen}
            operation_id = uuid.uuid4().hex
            creation = ("create-character", "--invite", issued["invite_file"], "--name", "Playtest Scout",
                        "--operation-id", operation_id)
            created = await command(*creation)
            replayed = await command(*creation)
            assert created["character"] == replayed["character"]
            chosen = created["character"]["character_id"]
            assert chosen != issued["character_id"]
            catalog = await command("characters", "--invite", issued["invite_file"])
            assert len(catalog["characters"]) == 2
            note("character_catalog_and_idempotent_create", {"selected": chosen, "characters": 2})
        client = await start("join", "--profile", profile, "--invite", str(fixture), "--headless",
                             "--character", chosen)
        guest = await wait(client_run, lambda state: state.get("state") == "connected"
                           and len(state.get("game", {}).get("pawns", {})) == 2)
        host_status = await wait(run, lambda state: state.get("game", {}).get("remote_clients") == 1
                                 and len(state["game"]["pawns"]) == 2)
        assert guest["character_id"] == chosen
        assert len(guest["game"]["pawns"]) == 2
        original_guest = host_status["game"]["pawns"]["guest"]["name"]
        assert original_guest != host_status["game"]["pawns"]["local"]["name"]
        note("two_players", {"host_and_client_pawns": len(host_status["game"]["pawns"]),
                             "client_can_see": len(guest["game"]["pawns"]),
                             "host_pawn": host_status["game"]["pawns"]["local"],
                             "guest_pawn": host_status["game"]["pawns"]["guest"],
                             "character_id": chosen})
        if args.native_probe:
            detached = await command("action", "--campaign", campaign, "vitals-probe")
            result = detached["native_vitals_probe"]
            assert result["mode"] == "detached" and result["host_uses_game_slot"] and not result["shared_save"]
            assert 0 < result["guest_health"] < result["host_health"]
            reset = await command("action", "--campaign", campaign, "vitals-reset")
            assert reset["native_vitals_probe"]["shared_save"]
            note("native_save_probe_detaches_guest", result)
        if args.native_quest:
            native = await command("action", "--campaign", campaign, "quest-probe")
            quest = native["native_quest"]
            assert quest["state"] == "active" and quest["step"] == "1", quest
            shops = await wait(run, lambda state: len(state.get("game", {}).get("native_shops", [])) == 2)
            note("native_quest_starts_and_shops_visible", {
                "quest": quest, "shops": len(shops["game"]["native_shops"]),
                "offers": len(shops["game"].get("native_offers", [])),
            })
        if args.native_death or args.native_damage:
            await wait(run, lambda state: state.get("game", {}).get("native_death_hook", {}).get("state") == "ready")
            probe = "native-damage-probe" if args.native_damage else "native-death-probe"
            invoked = await command("action", "--campaign", campaign, probe, "--target", "guest")
            if args.native_damage:
                canary = invoked["native_damage_canary"]
                assert canary["guest_before"] > 0 >= canary["guest_after"]
                assert canary["host_before"] == canary["host_after"]
                assert canary["binding_restored"]
                note("stock_direct_damage_crosses_lethal_threshold", canary)
            assert invoked["native_death_hook"]["sequence"] >= 1
            down = await wait(run, lambda state: state["gameplay"]["players"].get("guest", {}).get("downed"))
            assert down["game"]["native_dead_flags"]["guest"] is False
            assert down["game"]["pawns"]["guest"]["name"] == original_guest
            await asyncio.sleep(2)
            still_present = await command("status", "--campaign", campaign)
            assert still_present["game"]["native_dead_flags"]["guest"] is False
            assert still_present["game"]["pawns"]["guest"]["name"] == original_guest
            revived = await command("action", "--campaign", campaign, "interact", "--target", chosen)
            assert revived["effect"] == "revive"
            note("native_death_wrapper_routes_to_coop", {"stock_dead": False, "same_pawn": True,
                                                        "revived_health": revived["health"]})
        winner = await gameplay_slice(host_status, chosen) if args.gameplay else None
        dropped = await command("action", "--profile", profile, "drop-link")
        assert dropped["link_connected"] is False
        reserved = await wait(run, lambda state: not state.get("link_connected")
                              and bool(state.get("campaign", {}).get("leases"))
                              and state["campaign"]["leases"][0]["state"] == "reserved")
        assert reserved["game"]["pawns"]["guest"]["name"] == original_guest
        note("link_dropped", {"active_native_pawn_retained": True,
                              "character_reserved": reserved["campaign"]["leases"][0]["character_id"]})
        if args.gameplay:
            reward = await command("action", "--campaign", campaign, "test-quest-reward")
            assert set(reward["recipients"]) == {"host", chosen} and reward["wallet"] == 10
            await wait(run, lambda state: state.get("game", {}).get("native_wallet") == 10)
            note("reward_includes_reserved_guest_and_projects_native_cash", {"cash": 10, "recipients": 2})
        await asyncio.sleep(2)
        reconnected = await command("action", "--profile", profile, "reconnect")
        assert reconnected["link_connected"] and reconnected["resumed"]
        after = await wait(run, lambda state: state.get("link_connected") and state.get("game", {}).get("remote_clients") == 1)
        assert after["game"]["pawns"]["guest"]["name"] == original_guest
        note("same_pawn_reconnected", {"resumed": reconnected["resumed"], "pawn": original_guest})
        if args.gameplay:
            attempts = await asyncio.gather(
                command("action", "--campaign", campaign, "buy", "--target", "training-supplies", check=False),
                command("action", "--profile", profile, "buy", "--target", "training-supplies", check=False),
            )
            assert sum(result.get("ok", False) for result in attempts) == 1, attempts
            assert {result.get("error") for result in attempts} == {None, "insufficient_funds"}, attempts
            await wait(run, lambda state: state.get("game", {}).get("native_wallet") == 0)
            observed = await wait(client_run, lambda state:
                                  (state.get("gameplay", {}).get("economy") or {}).get("wallet") == 0)
            assert observed["gameplay"]["economy"]["stock"] == 1
            assert all(player["inventory"].get("field-token") == 1
                       for player in observed["gameplay"]["players"].values())
            note("shared_cash_serializes_purchases", {"wallet": 0, "remaining_stock": 1})
        if args.wipe:
            await command("action", "--campaign", campaign, "rest")
            await command("action", "--campaign", campaign, "test-damage", "--target", "guest", "--amount", "100")
            wiped = await command("action", "--campaign", campaign, "test-damage", "--target", "host", "--amount", "100")
            assert wiped["effect"] == "party_wipe"
            assert (await command("action", "--campaign", campaign, "interact", check=False))["error"] == "wipe_in_progress"
            dual = await wait(client_run, lambda state: all(
                state.get("game", {}).get("player_effects", {}).get(slot, {}).get("state") == "downed"
                for slot in ("local", "guest")
            ))
            note("dual_downed_guest_stays_live", {"guest_visual": dual["game"].get("player_visuals", {})})
            await asyncio.gather(
                command("action", "--campaign", campaign, "keep", "--keep", "field-token=1"),
                command("action", "--profile", profile, "keep", "--keep", "field-token=1"),
            )
            respawned = await wait(run, lambda state: state.get("gameplay", {}).get("backpacks")
                                   and not state["gameplay"]["pending_spawns"])
            assert respawned["gameplay"]["wipe"] is None
            assert all(not player["downed"] and player["health"] == player["max_health"]
                       for player in respawned["gameplay"]["players"].values())
            bags = respawned["gameplay"]["backpacks"]
            for bag_id, backpack in bags.items():
                guest_owned = backpack["owner_id"] != "host"
                owner = ("--profile", profile) if guest_owned else ("--campaign", campaign)
                outsider = ("--campaign", campaign) if guest_owned else ("--profile", profile)
                denied = await command("action", *outsider, "interact", "--target", bag_id, check=False)
                assert denied["error"] == "backpack_not_owned", denied
                recovered = await command("action", *owner, "interact", "--target", bag_id)
                assert recovered["items"] == backpack["inventory"]
            note("wipe_respawn_and_backpack_ownership", {"backpacks": len(bags), "health": 100})
        interruption.offline = True
        try:
            await wait(run, lambda state: not state.get("link_connected")
                       and state["campaign"]["leases"][0]["state"] == "reserved")
        finally:
            interruption.offline = False
        automatic = await wait(run, lambda state: state.get("link_connected") and
                               state.get("game", {}).get("remote_clients") == 1)
        assert automatic["game"]["pawns"]["guest"]["name"] == original_guest
        note("packet_loss_recovers_automatically", {"pawn": original_guest})
        expected_map = host_status["game"]["map"]
        if args.zones:
            destination = "/Game/Classes/Maps/PlaytestMap_02"
            forbidden = await command("action", "--profile", profile, "travel", "--target", destination, check=False)
            assert not forbidden["ok"]
            result = await command("action", "--campaign", campaign, "travel", "--target", destination)
            expected_map = result["map"]
            await wait(run, lambda state: state.get("game", {}).get("map") == destination
                       and len(state["game"]["pawns"]) == 2)
            client_map = await wait(client_run, lambda state: state.get("game", {}).get("map") == destination
                                    and state.get("state") == "connected")
            assert client_map["character_id"] == chosen and client_map["host_session"] == guest["host_session"]
            note("party_travels_with_same_authenticated_session", {"map": destination, "character_id": chosen})
        saved = await command("action", "--campaign", campaign, "checkpoint")
        assert saved["revision"] > 0
        snapshot = json.loads((run.parent / "save" / "campaign.json").read_text(encoding="utf-8"))
        saved_character = snapshot["characters"][chosen]
        assert len(saved_character["state"]["location"]) == 3
        assert saved_character["state"]["map"] == expected_map
        note("isolated_checkpoint", {"revision": saved["revision"],
                                     "character_location": saved_character["state"]["location"]})
        await stop(host, "--campaign", campaign)
        host_restart = await start("host", "--campaign", campaign, "--port", str(port), "--headless", *fixtures)
        restored = await wait(run, lambda state: state.get("state") == "hosting" and
                              state.get("generation") != host_status["generation"])
        assert restored["campaign"]["campaign_id"] == host_status["campaign"]["campaign_id"]
        assert any(item["character_id"] == chosen for item in restored["campaign"]["characters"])
        waiting = await wait(run, lambda state: state.get("link_connected")
                             and state.get("game", {}).get("remote_clients") == 1
                             and len(state.get("game", {}).get("pawns", {})) == 2)
        await wait(client_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("state") == "connected"
                   and state.get("host_session") == restored["generation"])
        restored_location = waiting["game"]["pawns"]["guest"]["location"]
        assert all(abs(new - old) < 2 for new, old in zip(
            restored_location, saved_character["state"]["location"], strict=True,
        ))
        note("restart_and_original_invite", {"same_campaign_id": restored["campaign"]["campaign_id"],
                                             "same_character_id": chosen,
                                             "restored_location": restored_location})
        if args.gameplay:
            restored_party = await wait(client_run, lambda state: state.get("gameplay", {}).get("loot", {})
                                        .get("claimed_by") and state["host_session"] == restored["generation"]
                                        and "guest" in state["gameplay"]["players"])
            assert restored_party["gameplay"]["players"][winner]["inventory"].get("toy-robot") == 1
            expected_health = 100 if args.wipe else 10
            assert restored_party["gameplay"]["players"]["guest"]["health"] == expected_health
            assert not restored_party["game"].get("loot_actors", [])
            note("gameplay_survives_host_restart", {"winner": winner, "guest_health": expected_health})
        await stop(client, "--profile", profile)
        await stop(host_restart, "--campaign", campaign)
        if original:
            assert snapshot_hashes(original) == baseline
            note("singleplayer_hashes_match", {"files": len(baseline)})
    finally:
        if relay:
            relay.close()
        for kind, name in (("--profile", profile), ("--campaign", campaign)):
            try:
                await command("action", kind, name, "stop", check=False)
            except (OSError, RuntimeError, TimeoutError):
                pass
        for process, path in reversed(processes):
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            text = path.read_text(encoding="utf-8", errors="replace")
            if text:
                print(path.name, text.strip(), flush=True)
        (run / "live-verification.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(run, flush=True)

asyncio.run(smoke())
