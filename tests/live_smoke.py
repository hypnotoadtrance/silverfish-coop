"""Optional Windows integration test. Launches two headless staged games on loopback."""

import argparse
import asyncio
import hashlib
import json
import math
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
options.add_argument("--native-inventory", action="store_true", help="exercise native inventory copy ownership")
options.add_argument("--native-guest", action="store_true", help="exercise persistent scoped native guest damage and revive")
options.add_argument("--native-pair", action="store_true", help="also down and revive the native host")
options.add_argument("--native-hazard", action="store_true", help="exercise a real stock poison gas hazard")
options.add_argument("--native-zones", action="store_true", help="exercise a stock overlap at the staged exit")
options.add_argument("--native-walls", action="store_true", help="verify swept physical exit collision")
options.add_argument("--authored-zone", action="store_true", help="start from CrossRoads and take the stock DeadBridge exit")
options.add_argument("--native-door", action="store_true", help="exercise the stock CrossRoads to Cistern transition door")
options.add_argument("--native-transport", action="store_true", help="verify pre-consumption guards for Stardust and four cubes")
options.add_argument("--native-gold", action="store_true", help="exercise a Golden Cube round trip and its durable charge")
options.add_argument("--native-pickup", action="store_true", help="claim and restore a stock Bandage")
options.add_argument("--native-weapon", action="store_true", help="round-trip a stock weapon and its native typed payload")
options.add_argument("--native-quest-flow", action="store_true", help="exercise stock quest turn-in and party rewards")
options.add_argument("--native-shop", action="store_true", help="buy a stock merchant item with the party wallet")
options.add_argument("--native-wipe", action="store_true", help="wipe both native pawns and recover a native item")
options.add_argument("--native-backend", choices=("cpp", "cpp-scoped", "overlay"),
                     help="require the specified death probe backend")
options.add_argument("--wipe", action="store_true", help="exercise wipe, item retention and backpack ownership")
args = options.parse_args()
if args.native_backend and not (args.native_death or args.native_damage):
    options.error("--native-backend requires a native death or damage probe")
if args.native_guest and any((args.native_death, args.native_damage, args.native_inventory, args.native_probe)):
    options.error("--native-guest requires its own run without transient save probes")
if args.native_pair and not args.native_guest:
    options.error("--native-pair requires --native-guest")
if args.native_hazard and not args.native_pair:
    options.error("--native-hazard requires --native-pair")
if args.native_pickup and not args.native_guest:
    options.error("--native-pickup requires --native-guest")
if args.native_weapon and not args.native_guest:
    options.error("--native-weapon requires --native-guest")
if args.native_shop and not (args.native_guest and args.native_quest_flow):
    options.error("--native-shop requires --native-guest and --native-quest-flow")
if args.native_wipe and not (args.native_pair and args.native_pickup):
    options.error("--native-wipe requires --native-pair and --native-pickup")
if args.native_zones and args.zones:
    options.error("Choose either --native-zones or --zones")
if args.native_walls and not args.native_zones:
    options.error("--native-walls requires --native-zones")
if args.authored_zone and not args.native_zones:
    options.error("--authored-zone requires --native-zones")
if args.native_door and (not args.native_zones or args.authored_zone):
    options.error("--native-door requires --native-zones and its own run")
if args.native_transport and not args.native_zones:
    options.error("--native-transport requires --native-zones")
if args.native_gold and (not args.native_zones or args.authored_zone or args.native_door):
    options.error("--native-gold requires --native-zones and its own authored route")
work = args.work.resolve()
profile = "validation-" + uuid.uuid4().hex[:8]
campaign = profile
run = work / "campaigns" / campaign / "run"
client_run = work / "clients" / profile / "run"
base = [sys.executable, "-u", "-m", "tools.silverfish", "--work", str(work)]
records = []
processes = []
deadline = 40
crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
gold_nexus = "/Game/Classes/Maps/Dreams/GoldNexus_P"

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

async def wait(directory, condition, *, timeout=None):
    end = time.monotonic() + (deadline if timeout is None else timeout)
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


def party_in_zone(status, map_name):
    game = status.get("game", {})
    return (game.get("map") == map_name and game.get("remote_clients") == 1
            and set(game.get("pawns", {})) == {"local", "guest"}
            and not status.get("awaiting_zone_guest"))


def gold_ledger(status):
    return status.get("campaign", {}).get("world", {}).get("party", {}).get("gold_transport") or {}


async def gold_action(operation, guid):
    reply = await command("action", "--campaign", campaign, f"native-gold-{operation}", "--target", guid)
    report = reply["native_gold"]
    assert report["operation"] == operation and report["guid"] == guid, report
    return report


async def wait_gold_event(guid, *, after=0):
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        try:
            lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, PermissionError):
            lines = []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (event.get("event") == "native_gold_request" and event.get("guid") == guid
                    and event.get("sequence", 0) > after):
                return event
        await asyncio.sleep(0.2)
    raise TimeoutError(f"No Golden Cube request outcome for {guid} after sequence {after}")


async def position_for_native_exit(target, where):
    return await command("action", "--campaign", campaign, "native-exit-position",
                         "--target", target, "--exit-position", where)


async def golden_cube_departure(character_id, host_session):
    initial = await wait(run, lambda status: party_in_zone(status, crossroads)
                         and status["game"].get("native_zone", {}).get("state") == "ready"
                         and status["game"].get("native_transport", {}).get("state") == "ready")
    host_pawn = initial["game"]["pawns"]["local"]["name"]
    guid = uuid.uuid4().hex.upper()
    seeded = await gold_action("seed", guid)
    assert seeded["state"] == "seeded" and seeded["map"] == crossroads and seeded["pawn"] == host_pawn, seeded

    await position_for_native_exit("host", "near")
    await position_for_native_exit("guest", "far")
    distant = await wait(run, lambda status: party_in_zone(status, crossroads)
                         and math.dist(status["game"]["pawns"]["local"]["location"],
                                       status["game"]["pawns"]["guest"]["location"]) > 1500)
    request = await gold_action("use-probe", guid)
    assert request["state"] == "requested" and request["pawn"] == host_pawn, request
    denied = await wait_gold_event(guid)
    rejection = denied["result"]
    assert denied["source"] == crossroads and denied["entry"] == "use_held_item", denied
    assert (not rejection["ok"] and rejection["error"] == "travel_blocked"
            and {"id": character_id, "reason": "too_far"} in rejection["blockers"]), denied
    present = await gold_action("inspect", guid)
    assert present["state"] == "present" and present["map"] == crossroads and present["pawn"] == host_pawn, present
    untouched = await wait(run, lambda status: party_in_zone(status, crossroads)
                           and status.get("zone_epoch") == distant["zone_epoch"])
    assert guid not in gold_ledger(untouched).get("spent", {}), untouched
    note("golden_cube_rejection_preserves_stock_item", {"guid": guid, "blockers": rejection["blockers"]})

    await position_for_native_exit("guest", "near")
    await wait(run, lambda status: party_in_zone(status, crossroads)
               and math.dist(status["game"]["pawns"]["local"]["location"],
                             status["game"]["pawns"]["guest"]["location"]) <= 1500)
    request = await gold_action("use-probe", guid)
    assert request["state"] == "requested" and request["pawn"] == host_pawn, request
    approved = await wait_gold_event(guid, after=denied["sequence"])
    outcome = approved["result"]
    assert approved["source"] == crossroads and approved["entry"] == "use_held_item", approved
    assert outcome["ok"] and outcome["map"] == gold_nexus and outcome["entrance"] == "PlayerStart_1", outcome
    arrived = await wait(run, lambda status: party_in_zone(status, gold_nexus)
                         and gold_ledger(status) == {"pending": None, "spent": {guid: "arrived"}})
    location = arrived["game"]["pawns"]["local"]["location"]
    assert math.dist(location, (0, 1050, 150)) <= 350, location
    peer = await wait(client_run, lambda status: status.get("state") == "connected"
                      and status.get("game", {}).get("map") == gold_nexus)
    assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
    absent = await gold_action("inspect", guid)
    assert absent["state"] == "absent" and absent["map"] == gold_nexus, absent
    note("golden_cube_moves_party_to_stock_player_start", {
        "guid": guid, "map": gold_nexus, "entrance": outcome["entrance"], "host": location,
        "same_character": character_id, "item": absent["state"],
    })
    return guid


async def golden_cube_return(guid, character_id, host_session):
    resumed = await wait(run, lambda status: party_in_zone(status, gold_nexus)
                         and status["game"].get("native_zone", {}).get("state") == "ready")
    assert gold_ledger(resumed) == {"pending": None, "spent": {guid: "arrived"}}, resumed
    absent = await gold_action("inspect", guid)
    assert absent["state"] == "absent" and absent["map"] == gold_nexus, absent
    note("golden_cube_destination_and_charge_survive_restart", {"guid": guid, "map": gold_nexus})

    await position_for_native_exit("host", "near")
    await position_for_native_exit("guest", "near")
    if args.native_walls:
        await wait(run, lambda status: status["game"].get("native_zone_wall", {}).get("host_open"))
        sweep = await command("action", "--campaign", campaign, "native-exit-wall-probe", "--target", "host")
        assert sweep["native_zone_wall"]["outcome"] == "clear", sweep
    else:
        await position_for_native_exit("host", "inside")
    arrived = await wait(run, lambda status: party_in_zone(status, crossroads))
    assert gold_ledger(arrived) == {"pending": None, "spent": {guid: "arrived"}}, arrived
    peer = await wait(client_run, lambda status: status.get("state") == "connected"
                      and status.get("game", {}).get("map") == crossroads)
    assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
    events = await command("events", "--campaign", campaign)
    assert any(event["event"] == "zone_travel_ready" and event.get("source") == gold_nexus
               and event.get("destination") == crossroads and event.get("entrance") == "Nexus_CrossRoads"
               for event in events["events"]), events
    assert (await gold_action("inspect", guid))["state"] == "absent"
    note("golden_cube_stock_exit_returns_party", {"map": crossroads, "entrance": "Nexus_CrossRoads", "guid": guid})


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
                            args.native_death, args.native_damage, args.native_inventory, args.native_guest,
                            args.native_zones, args.native_quest_flow))
        fixtures = ["--fixtures"] if fixture_test else []
        if args.native_backend == "cpp-scoped" or args.native_guest:
            fixtures += ["--native-events"]
        if args.native_guest:
            fixtures += ["--native-guest"]
        if args.native_zones:
            fixtures += ["--native-zones"]
        if args.native_walls:
            fixtures += ["--native-zone-walls"]
        if args.wipe or args.native_wipe:
            fixtures += ["--fixture-keep-slots", "1", "--fixture-backpacks"]
        initial_zone = ["--start-zone", crossroads] if args.authored_zone or args.native_door or args.native_gold else []
        host = await start("host", "--campaign", campaign, "--port", "0", "--headless", *fixtures, *initial_zone)
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
                             "--character", chosen, *(["--native-zones"] if args.native_zones else []),
                             *(["--native-zone-walls"] if args.native_walls else []))
        guest = await wait(client_run, lambda state: state.get("state") == "connected"
                           and len(state.get("game", {}).get("pawns", {})) == 2)
        host_status = await wait(run, lambda state: state.get("game", {}).get("remote_clients") == 1
                                 and len(state["game"]["pawns"]) == 2)
        assert guest["character_id"] == chosen
        assert len(guest["game"]["pawns"]) == 2
        original_guest = host_status["game"]["pawns"]["guest"]["name"]
        assert original_guest != host_status["game"]["pawns"]["local"]["name"]
        if args.native_transport:
            role_cases = (
                ("host", ("--campaign", campaign), run, "coordinated_route_required"),
                ("guest", ("--profile", profile), client_run, "host_activation_required"),
            )
            for role, arguments, directory, reason in role_cases:
                await wait(directory, lambda state: state.get("game", {}).get("native_transport", {}).get("state") == "ready")
                rows = []
                for item in ("stardust", "green", "blue", "red", "gold"):
                    reply = await command("action", *arguments, "native-transport-probe", "--target", item)
                    row = reply["native_transport"]
                    assert row["role"] == role and row["reason"] == reason and row["item"] == item, row
                    assert row["entry"] == "use_held_item"
                    rows.append(item)
                note("stock_transport_use_guard", {"role": role, "guarded": rows, "reason": reason})
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
        if args.native_inventory:
            copied_inventory = await command("action", "--campaign", campaign, "native-inventory-probe")
            inventory = copied_inventory["native_inventory_canary"]
            assert inventory["state"] == "copied" and inventory["binding_restored"], inventory
            note("native_inventory_storage_is_independent", inventory)
        if args.native_death or args.native_damage:
            ready = await wait(
                run, lambda state: state.get("game", {}).get("native_death_hook", {}).get("state") == "ready",
            )
            backend = ready["game"]["native_death_hook"].get("backend")
            if args.native_backend and backend != args.native_backend:
                raise RuntimeError(f"Expected native backend {args.native_backend}, got {backend}")
            probe = "native-damage-probe" if args.native_damage else "native-death-probe"
            invoked = await command("action", "--campaign", campaign, probe, "--target", "guest")
            if args.native_damage:
                canary = invoked["native_damage_canary"]
                assert canary["guest_before"] > 0 >= canary["guest_after"]
                assert canary["host_before"] == canary["host_after"]
                assert canary["binding_restored"]
                note("stock_direct_damage_crosses_lethal_threshold", canary)
            assert invoked["native_death_hook"]["sequence"] >= 1
            assert invoked["native_death_hook"].get("backend") == backend
            down = await wait(run, lambda state: state["gameplay"]["players"].get("guest", {}).get("downed"))
            assert down["game"]["native_dead_flags"]["guest"] is False
            assert down["game"]["pawns"]["guest"]["name"] == original_guest
            await asyncio.sleep(2)
            still_present = await command("status", "--campaign", campaign)
            assert still_present["game"]["native_dead_flags"]["guest"] is False
            assert still_present["game"]["pawns"]["guest"]["name"] == original_guest
            revived = await command("action", "--campaign", campaign, "interact", "--target", chosen)
            assert revived["effect"] == "revive"
            note("native_death_wrapper_routes_to_coop", {
                "backend": backend, "stock_dead": False, "same_pawn": True,
                "revived_health": revived["health"],
            })
        if args.native_guest:
            async def enrolled():
                return await wait(run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state")
                                  == "bound" and state["game"].get("native_death_hook", {}).get("scope")
                                  in (("party",) if args.native_pair else ("guest", "party")))

            await enrolled()
            await asyncio.sleep(2)
            before = (await enrolled())["game"]
            assert before["native_guest_save"]["pawn"] == original_guest
            assert before["native_death_hook"]["sequence"] == 0
            if args.native_weapon:
                weapon = (await command("action", "--campaign", campaign, "native-weapon-roundtrip")
                          )["native_weapon_roundtrip"]
                assert weapon["state"] == "verified" and weapon["payload_type"].endswith("WeaponData_Struct")
                assert weapon["item_text_chars"] > weapon["payload_text_chars"] > 0
                note("stock_weapon_typed_payload_round_trip", weapon)
            if args.native_pickup:
                spawned = await command("action", "--campaign", campaign, "native-pickup-spawn")
                source = spawned["native_pickup"]
                pickup_arguments = ("action", "--profile", profile, "native-pickup", "--target", source["actor"],
                                    "--operation-id", uuid.uuid4().hex)
                awarded = await command(*pickup_arguments)
                assert awarded["guid"] == source["guid"] and awarded["effect"] == "native_pickup"
                repeated = await command(*pickup_arguments)
                assert all(repeated[field] == awarded[field] for field in ("guid", "item", "effect"))
                note("stock_bandage_claimed_exactly_once", {"guid": awarded["guid"], "item": awarded["item"]})
            if args.native_pair:
                friendly = await command("action", "--campaign", campaign, "native-friendly-fire")
                assert friendly["native_friendly_fire"]["blocked"] >= 1
                note("stock_friendly_fire_blocked", friendly["native_friendly_fire"])
            invoked = await command("action", "--campaign", campaign, "native-guest-damage", "--target", "guest")
            hit = invoked["native_damage_canary"]
            assert hit["guest_before"] > 0 >= hit["guest_after"] and not hit["binding_restored"]
            assert hit["host_before"] == hit["host_after"]
            down = await wait(run, lambda state: state["gameplay"]["players"].get("guest", {}).get("downed")
                              and state["game"].get("native_death_hook", {}).get("sequence") == 1
                              and state["game"].get("player_effects", {}).get("guest", {}).get("state") == "downed")
            assert down["game"]["native_dead_flags"]["guest"] is False
            assert down["game"]["pawns"]["guest"]["name"] == original_guest
            assert down["game"]["native_guest_save"]["pawn"] == original_guest
            note("resident_scope_catches_stock_lethal_damage", hit)
            revive = await command("action", "--campaign", campaign, "interact", "--target", chosen)
            assert revive["effect"] == "revive"
            revived = await wait(run, lambda state: (state["game"].get("pawns", {}).get("guest", {}).get("health") or 0) > 0
                                 and state["game"].get("player_effects", {}).get("guest", {}).get("state") == "standing")
            native_health = revived["game"]["pawns"]["guest"]["health"]
            assert revived["game"]["pawns"]["guest"]["name"] == original_guest
            assert revived["game"]["native_guest_save"]["pawn"] == original_guest
            assert revived["game"]["pawns"]["local"]["health"] == hit["host_before"]
            assert abs(native_health - hit["guest_before"] * 0.10) < 1, revived["game"]["pawns"]
            note("same_native_pawn_revived_in_place", {"guest_health": native_health,
                                                      "host_health": hit["host_before"], "pawn": original_guest})
            if args.native_hazard:
                authority = ("action", "--campaign", campaign)
                await command(*authority, "native-hazard-position")
                await command(*authority, "native-hazard-start")
                hazard = await wait(run, lambda state: state["game"].get("native_hazard", {}).get("state")
                                    in ("downed", "failed", "timeout", "stopped"), timeout=38)
                proof = hazard["game"]["native_hazard"]
                assert proof["state"] == "downed" and proof["guest_health"] <= 0, proof
                assert proof["host_health"] == proof["host_before"]
                assert hazard["gameplay"]["players"]["guest"]["downed"] and not hazard["game"]["native_dead_flags"]["guest"]
                await command(*authority, "native-hazard-return")
                result = await command(*authority, "interact", "--target", chosen)
                assert result["effect"] == "revive"
                await wait(run, lambda state: state["game"]["pawns"]["guest"]["health"] == native_health)
                note("real_stock_poison_gas_downs_only_guest", proof)
            if args.native_pair:
                host_before = revived["game"]["pawns"]["local"]
                damage = await command("action", "--campaign", campaign, "native-host-damage", "--target", "host")
                assert damage["native_damage_canary"]["slot"] == "local"
                await wait(run, lambda state: state["gameplay"]["players"]["host"]["downed"]
                           and state["game"].get("player_effects", {}).get("local", {}).get("state") == "downed")
                host_revive = await command("action", "--profile", profile, "interact", "--target", "host")
                assert host_revive["effect"] == "revive"
                standing = await wait(run, lambda state: (state["game"]["pawns"]["local"].get("health") or 0) > 0
                                      and state["game"].get("player_effects", {}).get("local", {}).get("state")
                                      == "standing")
                assert standing["game"]["pawns"]["local"]["name"] == host_before["name"]
                assert standing["game"]["native_host_revived_health"] == host_before["health"] * 0.1
                assert standing["game"]["pawns"]["guest"]["health"] == native_health
                note("same_native_host_pawn_revived_by_guest", {"health": standing["game"]["native_host_revived_health"]})
                if args.native_wipe:
                    await command("action", "--campaign", campaign, "rest")
                    await command("action", "--campaign", campaign, "native-guest-damage", "--target", "guest")
                    await wait(run, lambda state: state["gameplay"]["players"]["guest"]["downed"])
                    await command("action", "--campaign", campaign, "native-host-damage", "--target", "host")
                    await wait(run, lambda state: state["gameplay"].get("wipe"))
                    await command("action", "--campaign", campaign, "keep")
                    await command("action", "--profile", profile, "keep")
                    emerged = await wait(run, lambda state: not state["gameplay"]["pending_spawns"]
                                         and not state["gameplay"]["wipe"]
                                         and awarded["guid"] not in state["gameplay"].get("native_inventory_projected", []))
                    for slot in ("local", "guest"):
                        assert emerged["game"]["pawns"][slot]["health"] == emerged["game"]["pawns"][slot]["max_health"]
                    native_health = emerged["game"]["pawns"]["guest"]["health"]
                    bag = next(key for key, value in emerged["gameplay"]["backpacks"].items()
                               if awarded["guid"] in value.get("native_inventory", {}))
                    denied = await command("action", "--campaign", campaign, "interact", "--target", bag, check=False)
                    assert denied["error"] == "backpack_not_owned", denied
                    recovered = await command("action", "--profile", profile, "interact", "--target", bag)
                    assert recovered["native_items"] == [awarded["guid"]]
                    await wait(run, lambda state: awarded["guid"] in state["gameplay"].get("native_inventory_projected", []))
                    note("native_wipe_removes_and_recovers_the_owner_item", {"bag": bag, "item": awarded["guid"],
                                                                             "native_health": native_health})
        if args.native_quest_flow:
            quest_target = ("--target", "Jmr_CrossRoads")
            player = ("action", "--profile", profile)
            authority = ("action", "--campaign", campaign)
            started = await command(*player, "native-quest-start", *quest_target)
            assert started["step"] == 1 and started["actor"] == chosen
            refused = await command(*player, "native-quest-turnin", *quest_target, check=False)
            assert refused["error"] == "native_quest_objective_incomplete", refused
            progressed = await command(*authority, "native-quest-objective", *quest_target)
            assert progressed["step"] == 2
            settled = await command(*player, "native-quest-turnin", *quest_target)
            assert settled["step"] == -10 and settled["wallet"] == 200
            assert set(settled["recipients"]) == {"host", chosen}
            duplicate = await command(*player, "native-quest-turnin", *quest_target)
            assert duplicate["effect"] == "native_quest_already_settled" and duplicate["wallet"] == 200
            await wait(client_run, lambda state: (state.get("gameplay", {}).get("native_quests", {})
                                                  .get("Jmr_CrossRoads", {}).get("step")) == -10)
            note("stock_quest_settled_once_for_whole_party", settled)
            expected_wallet = 200
            if args.native_shop:
                shop_id = "Shop_BP_C_1"
                await command(*authority, "native-shop-approach", "--target", shop_id)
                purchase = (*player, "native-shop-buy", "--target", shop_id, "--operation-id", uuid.uuid4().hex)
                bought = await command(*purchase)
                assert bought["wallet"] == expected_wallet - bought["price"] and bought["item"]["ammo"] == 1
                replay = await command(*purchase)
                assert replay["guid"] == bought["guid"] and replay["wallet"] == bought["wallet"]
                expected_wallet = bought["wallet"]
                note("stock_merchant_checkout_uses_shared_wallet", bought)
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
        if args.native_guest:
            assert after["game"]["native_guest_save"]["pawn"] == original_guest
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
        if args.native_gold:
            gold_guid = await golden_cube_departure(chosen, guest["host_session"])
            expected_map = gold_nexus
        elif args.native_door:
            destination = "/Game/Classes/Maps/Maps_Dungeons/Cistern_P"
            expected_map = destination
            authority = ("action", "--campaign", campaign)

            async def position_at_door(target, distance):
                return await command(*authority, "native-door-position", "--target", target,
                                     "--exit-position", distance)

            await wait(run, lambda state: state["game"].get("native_zone", {}).get("state") == "ready")
            await position_at_door("guest", "near")
            await command(*authority, "native-door-probe", "--target", "guest")
            await wait(run, lambda state: state["game"].get("native_door", {}).get("decision") == "guest_denied")
            await position_at_door("guest", "far")
            await position_at_door("host", "near")
            await command(*authority, "native-door-probe", "--target", "host")
            await wait(run, lambda state: state["game"].get("native_door", {}).get("decision") == "host_out_of_range")
            await position_at_door("guest", "near")
            await command(*authority, "native-door-probe", "--target", "host")
            await wait(run, lambda state: state["game"].get("map") == destination
                       and not state.get("awaiting_zone_guest") and len(state["game"]["pawns"]) == 2)
            await wait(client_run, lambda state: state.get("state") == "connected" and state["game"]["map"] == destination)
            events = await command("events", "--campaign", campaign)
            arrived = next(entry for entry in events["events"] if entry["event"] == "zone_travel_ready")
            assert arrived["entrance"] == "CR_Cstrn", arrived
            note("stock_transition_door_is_host_only", {"map": destination, "entrance": arrived["entrance"]})
            expected_map = host_status["game"]["map"]
            await wait(run, lambda state: state["game"].get("native_zone", {}).get("state") == "ready")
            await command(*authority, "native-door-probe", "--target", "host")
            await wait(run, lambda state: state["game"].get("map") == expected_map
                       and not state.get("awaiting_zone_guest") and len(state["game"]["pawns"]) == 2)
            await wait(client_run, lambda state: state.get("state") == "connected" and state["game"]["map"] == expected_map)
            note("stock_transition_door_returns_party", {"map": expected_map, "entrance": "CR_Cstrn"})
        elif args.zones or args.native_zones:
            destination = ("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P" if args.authored_zone
                           else "/Game/Classes/Maps/PlaytestMap_02")
            forbidden = await command("action", "--profile", profile, "travel", "--target", destination, check=False)
            assert not forbidden["ok"]
            if args.native_zones:
                async def position(target, where):
                    return await command("action", "--campaign", campaign, "native-exit-position",
                                         "--target", target, "--exit-position", where)

                await wait(run, lambda state: state["game"].get("native_zone", {}).get("state") == "ready")
                if args.native_walls:
                    async def sweep(target):
                        result = await command("action", "--campaign", campaign, "native-exit-wall-probe",
                                               "--target", target)
                        return result["native_zone_wall"]

                    await wait(run, lambda state: state["game"].get("native_zone_wall", {}).get("state") == "ready")
                    await position("guest", "near")
                    guest_wall = await sweep("guest")
                    assert guest_wall["outcome"] == "wall", guest_wall
                    await position("guest", "far")
                    await position("host", "near")
                    host_wall = await sweep("host")
                    assert host_wall["outcome"] == "wall", host_wall
                    await position("host", "near")
                    await position("guest", "near")
                    await wait(run, lambda state: state["game"].get("native_zone_wall", {}).get("host_open"))
                    admitted = await sweep("host")
                    assert admitted["outcome"] == "clear", admitted
                    note("physical_wall_only_admits_nearby_party_host", {
                        "guest_blocked": guest_wall["remaining_uu"], "distant_host_blocked": host_wall["remaining_uu"],
                    })
                else:
                    await position("guest", "near")
                    await position("guest", "inside")
                    await wait(run, lambda state: state["game"].get("native_zone", {}).get("guest_denied", 0) > 0)
                    await position("guest", "far")
                    await position("host", "near")
                    await position("host", "inside")
                    await wait(run, lambda state: state["game"].get("native_zone", {}).get("too_far", 0) > 0)
                    await position("host", "near")
                    await position("guest", "near")
                    await position("host", "inside")
                expected_map = destination
                note("stock_exit_blocks_guest_and_distant_party", {"map": host_status["game"]["map"]})
            else:
                result = await command("action", "--campaign", campaign, "travel", "--target", destination)
                expected_map = result["map"]
            await wait(run, lambda state: state.get("game", {}).get("map") == destination
                       and len(state["game"]["pawns"]) == 2)
            client_map = await wait(client_run, lambda state: state.get("game", {}).get("map") == destination
                                    and state.get("state") == "connected")
            assert client_map["character_id"] == chosen and client_map["host_session"] == guest["host_session"]
            note("party_travels_with_same_authenticated_session", {"map": destination, "character_id": chosen})
            if args.authored_zone:
                arrival = await wait(run, lambda state: abs(state["game"]["pawns"]["local"]["location"][0] + 17790) < 350)
                note("authored_exit_uses_destination_marker", {
                    "map": destination, "entrance": "DB_CR", "host": arrival["game"]["pawns"]["local"]["location"],
                })
            if args.native_guest:
                traveled = (await enrolled())["game"]
                assert traveled["map"] == destination and traveled["pawns"]["guest"]["health"] == native_health
                assert traveled["native_guest_save"]["pawn"] != original_guest
                note("native_guest_health_preserved_across_travel", {
                    "guest_health": native_health, "map": destination, "pawn": traveled["native_guest_save"]["pawn"],
                })
            if args.authored_zone:
                expected_map = host_status["game"]["map"]
                await wait(run, lambda state: state["game"].get("native_zone", {}).get("state") == "ready")
                await position("host", "near")
                await position("guest", "near")
                if args.native_walls:
                    await wait(run, lambda state: state["game"].get("native_zone_wall", {}).get("host_open"))
                    assert (await sweep("host"))["outcome"] == "clear"
                else:
                    await position("host", "inside")
                returned = await wait(run, lambda state: state["game"].get("map") == expected_map
                                      and abs(state["game"]["pawns"]["local"]["location"][0] - 13850) < 350
                                      and len(state["game"]["pawns"]) == 2
                                      and not state.get("awaiting_zone_guest"))
                await wait(client_run, lambda state: state.get("state") == "connected"
                           and state["game"].get("map") == expected_map)
                note("authored_return_exit_uses_destination_marker", {
                    "map": expected_map, "entrance": "DB_CR", "host": returned["game"]["pawns"]["local"]["location"],
                })
        saved = await command("action", "--campaign", campaign, "checkpoint")
        assert saved["revision"] > 0
        snapshot = json.loads((run.parent / "save" / "campaign.json").read_text(encoding="utf-8"))
        saved_character = snapshot["characters"][chosen]
        assert len(saved_character["state"]["location"]) == 3
        assert saved_character["state"]["map"] == expected_map
        if args.native_gold:
            assert snapshot["world"]["map"] == gold_nexus
            assert snapshot["world"]["party"]["gold_transport"] == {"pending": None, "spent": {gold_guid: "arrived"}}
        if args.native_guest:
            assert saved_character["state"]["health"] == native_health
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
        if args.native_guest:
            rebound = (await enrolled())["game"]
            assert rebound["native_guest_save"]["pawn"] == rebound["pawns"]["guest"]["name"]
            assert rebound["pawns"]["guest"]["health"] == saved_character["state"]["health"]
            note("native_guest_health_restored_after_host_restart", {
                "guest_health": rebound["pawns"]["guest"]["health"], "same_character_id": chosen,
            })
        if args.native_pickup:
            restored_item = await wait(run, lambda state: awarded["guid"] in state.get("gameplay", {})
                                       .get("native_inventory_projected", []))
            note("stock_bandage_restored_after_restart", {"guid": awarded["guid"],
                                                          "pawn": restored_item["game"]["native_guest_save"]["pawn"]})
        if args.native_quest_flow:
            restored_quest = await wait(run, lambda state: state["game"].get("native_quest", {})
                                        .get("state") == "completed" and state["game"].get("native_wallet") == expected_wallet)
            assert restored_quest["gameplay"]["players"]["guest"]["quest_xp"] == 1
            note("stock_quest_and_rewards_restored_after_restart", restored_quest["game"]["native_quest"])
            if args.native_shop:
                assert bought["guid"] in restored_quest["gameplay"]["players"]["guest"]["native_inventory"]
                await wait(run, lambda state: bought["guid"] in state["gameplay"].get("native_inventory_projected", []))
        if args.gameplay:
            restored_party = await wait(client_run, lambda state: state.get("gameplay", {}).get("loot", {})
                                        .get("claimed_by") and state["host_session"] == restored["generation"]
                                        and "guest" in state["gameplay"]["players"])
            assert restored_party["gameplay"]["players"][winner]["inventory"].get("toy-robot") == 1
            expected_health = 100 if args.wipe else 10
            assert restored_party["gameplay"]["players"]["guest"]["health"] == expected_health
            assert not restored_party["game"].get("loot_actors", [])
            note("gameplay_survives_host_restart", {"winner": winner, "guest_health": expected_health})
        if args.native_gold:
            await golden_cube_return(gold_guid, chosen, restored["generation"])
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
