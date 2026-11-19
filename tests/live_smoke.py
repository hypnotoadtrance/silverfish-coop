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
options.add_argument("--native-fall", action="store_true", help="exercise the stock authoritative guest landing path")
options.add_argument("--native-zones", action="store_true", help="exercise a stock overlap at the staged exit")
options.add_argument("--native-walls", action="store_true", help="verify swept physical exit collision")
options.add_argument("--authored-zone", action="store_true", help="start from CrossRoads and take the stock DeadBridge exit")
options.add_argument("--native-door", action="store_true", help="exercise the stock CrossRoads to Cistern transition door")
options.add_argument("--observatory-zone", action="store_true", help="exercise both stock CrossRoads and Observatory exits")
options.add_argument("--native-transport", action="store_true", help="verify pre-consumption guards for Stardust and four cubes")
options.add_argument("--native-gold", action="store_true", help="exercise a Golden Cube round trip and its durable charge")
options.add_argument("--native-blue", action="store_true", help="exercise Blue Cube fallback and same-map stock rest travel")
options.add_argument("--native-red", action="store_true", help="exercise Red Cube party travel and its durable random destination")
options.add_argument("--native-green", action="store_true", help="exercise Green Cube reputation, party arrival and restart")
options.add_argument("--native-stardust", action="store_true", help="exercise the stock Stardust visit and timed return")
options.add_argument("--dream-early", action="store_true", help="send the stock Exchange power signal during a Stardust visit")
options.add_argument("--native-seed", action="store_true", help="verify the saved seed reaches the stock day cycle on restart")
options.add_argument("--native-host-save", action="store_true", help="verify the whole host-save generation and native inventory")
options.add_argument("--native-guest-save", action="store_true", help="verify a distinct guest stock save and native inventory")
options.add_argument("--native-roster", action="store_true", help="verify the host's native connection and pawn identity")
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
if args.native_fall and not args.native_pair:
    options.error("--native-fall requires --native-pair")
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
if args.native_gold and (not args.native_zones or args.authored_zone or args.native_door or args.observatory_zone):
    options.error("--native-gold requires --native-zones and its own authored route")
if args.native_blue and (not args.native_zones or args.authored_zone or args.native_door
                         or args.observatory_zone or args.native_gold):
    options.error("--native-blue requires --native-zones and its own authored route")
random_cube = "red" if args.native_red else "green" if args.native_green else None
if random_cube and (not args.native_zones or args.native_red and args.native_green
                    or any((args.authored_zone, args.native_door, args.observatory_zone,
                            args.native_gold, args.native_blue, args.native_stardust))):
    options.error(f"--native-{random_cube} requires --native-zones and its own authored route")
if args.observatory_zone and (not args.native_zones or args.authored_zone or args.native_door):
    options.error("--observatory-zone requires --native-zones and its own authored route")
if args.native_stardust and (not args.native_zones or args.authored_zone or args.native_door
                             or args.observatory_zone or args.native_gold or args.native_blue):
    options.error("--native-stardust requires --native-zones and its own authored route")
if args.dream_early and not args.native_stardust:
    options.error("--dream-early requires --native-stardust")
if args.native_host_save and not args.native_stardust:
    options.error("--native-host-save uses the Stardust stock inventory fixture")
if args.native_guest_save and not (args.native_guest and args.native_pickup):
    options.error("--native-guest-save uses the native guest stock pickup fixture")
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
observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
observatory_bed = "SavePoint_BP_C_3-Observitory_P-"
gold_nexus = "/Game/Classes/Maps/Dreams/GoldNexus_P"
exchange = "/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P"

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


async def wait_campaign_event(event_name, guid, *, after=None):
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
            if (event.get("event") == event_name and event.get("guid") == guid
                    and (after is None or event.get("sequence", 0) > after)):
                return event
        await asyncio.sleep(0.2)
    raise TimeoutError(f"No {event_name} outcome for {guid} after sequence {after}")


async def position_for_native_exit(target, where, *, entrance=None):
    selection = ["--entrance", entrance] if entrance else []
    return await command("action", "--campaign", campaign, "native-exit-position",
                         "--target", target, "--exit-position", where, *selection)


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
    denied = await wait_campaign_event("native_gold_request", guid)
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
    request = await gold_action("action-probe", guid)
    assert request["state"] == "requested" and request["pawn"] == host_pawn, request
    approved = await wait_campaign_event("native_gold_request", guid, after=denied["sequence"])
    outcome = approved["result"]
    assert approved["source"] == crossroads and approved["entry"] == "action_use", approved
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


def blue_ledger(status):
    return status.get("campaign", {}).get("world", {}).get("party", {}).get("blue_transport") or {}


async def blue_action(operation, guid, map_name, state, pawn=None):
    reply = await command("action", "--campaign", campaign, f"native-blue-{operation}", "--target", guid)
    report = reply["native_blue"]
    assert report["operation"] == operation and report["guid"] == guid, report
    assert report["map"] == map_name and report["state"] == state, report
    if pawn is not None:
        assert report["pawn"] == pawn, report
    return report


def random_cube_ledger(status, cube):
    return status.get("campaign", {}).get("world", {}).get("party", {}).get(f"{cube}_transport") or {}


async def random_cube_action(cube, operation, guid, map_name, state, pawn):
    reply = await command("action", "--campaign", campaign, f"native-{cube}-{operation}", "--target", guid)
    report = reply[f"native_{cube}"]
    assert report == {"operation": operation, "state": state, "map": map_name, "pawn": pawn, "guid": guid}, report
    return report


def verify_green_options(proof, pawn):
    from tools.silverfish.native_green import ALWAYS, REPUTATION_LEVELS

    ranks = proof["reputation"]
    assert proof["source"] == crossroads and proof["pawn"] == pawn, proof
    assert set(ranks) == {"2", "4", "5", "8"} and all(type(rank) is int and 0 <= rank <= 6 for rank in ranks.values()), proof
    expected = ALWAYS + tuple(map_name for map_name, faction in REPUTATION_LEVELS if ranks[str(faction)] in (0, 1, 2))
    assert tuple(proof["destinations"]) == expected, proof
    return expected


def verify_green_arrival(proof, destination):
    markers = {
        "Shop_ScavBar_P": {"arrow": "SavePoint_BP_C_1"},
        "Shop_ShopKeep_P": {"start": "PlayerStart_1"},
        "Borough": {"arrow": "SavePoint_BP_C_1", "alternate": "LevelExit_Spawn_BP_C_1"},
        "Shop_BlackMarket_01": {"start": "PlayerStart_1"},
        "Observitory_P": {"arrow": "SavePoint_BP_C_3", "alternate": "LevelExit_Spawn_BP_C_1"},
        "Shop_Scientist_01": {"start": "PlayerStart_2"},
        "DeadBridge_P": {"arrow": "SavePoint_BP_C_1", "alternate": "LevelExit_Spawn_BP_C_4"},
        "Forest_01_P": {"start": "PlayerStart_2"},
    }
    basename = destination.rsplit("/", 1)[1]
    placement = proof["placement"]
    actor_class = {"arrow": "SavePoint_BP_C", "alternate": "LevelExit_Spawn_BP_C", "start": "PlayerStart"}
    assert proof["destination"] == destination and placement in markers[basename], proof
    actor = markers[basename][placement]
    assert proof["actor"] == f"{actor_class[placement]} {destination}.{basename}:PersistentLevel.{actor}", proof
    assert len(proof["location"]) == 3 and all(math.isfinite(axis) for axis in proof["location"]), proof


async def random_cube_travel(cube, character_id, host_session):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.silverfish.travel import RED_DESTINATIONS, TRAVEL_RADIUS_UU

    def ready(status, map_name):
        return (party_in_zone(status, map_name)
                and status["game"].get("native_zone", {}).get("state") == "ready"
                and status["game"].get("native_transport", {}).get("state") == "ready")

    def separation(status):
        pawns = status["game"]["pawns"]
        return math.dist(pawns["local"]["location"], pawns["guest"]["location"])

    initial = await wait(run, lambda status: ready(status, crossroads))
    assert initial["generation"] == host_session, initial
    pawn = initial["game"]["pawns"]["local"]["name"]
    guid = uuid.uuid4().hex.upper()
    await random_cube_action(cube, "seed", guid, crossroads, "seeded", pawn)

    entrance = "Obsv_Etr"
    await position_for_native_exit("host", "near", entrance=entrance)
    await position_for_native_exit("guest", "far", entrance=entrance)
    distant = await wait(run, lambda status: ready(status, crossroads) and separation(status) > TRAVEL_RADIUS_UU)
    await random_cube_action(cube, "use-probe", guid, crossroads, "requested", pawn)
    denied = await wait_campaign_event(f"native_{cube}_request", guid)
    refusal = denied["result"]
    assert denied["source"] == crossroads and denied["entry"] == "use_held_item", denied
    assert (not refusal["ok"] and refusal["error"] == "travel_blocked"
            and {"id": character_id, "reason": "too_far"} in refusal["blockers"]), denied
    await random_cube_action(cube, "inspect", guid, crossroads, "present", pawn)
    untouched = await wait(run, lambda status: ready(status, crossroads)
                           and (cube == "red" or status["game"].get("native_green_options")))
    assert untouched["zone_epoch"] == distant["zone_epoch"], untouched
    assert random_cube_ledger(untouched, cube) == random_cube_ledger(initial, cube), untouched
    refusal_note = {"guid": guid, "blockers": refusal["blockers"]}
    destinations, arrival_tag = RED_DESTINATIONS, "Red_Tes"
    if cube == "green":
        options_proof = untouched["game"]["native_green_options"]
        destinations, arrival_tag = verify_green_options(options_proof, pawn), "Bed"
        refusal_note["options"] = options_proof
    note(f"{cube}_cube_rejection_preserves_stock_item", refusal_note)

    await position_for_native_exit("guest", "near", entrance=entrance)
    await wait(run, lambda status: ready(status, crossroads) and separation(status) <= TRAVEL_RADIUS_UU)
    await random_cube_action(cube, "use-probe", guid, crossroads, "requested", pawn)
    approved = await wait_campaign_event(f"native_{cube}_request", guid, after=denied["sequence"])
    result = approved["result"]
    assert approved["source"] == crossroads and approved["entry"] == "use_held_item", approved
    assert result["ok"] and result["map"] in destinations and result["entrance"] == arrival_tag, approved
    destination = result["map"]
    completed = await wait_campaign_event(f"{cube}_travel_completed", guid)
    assert (completed["ok"] and completed["map"] == destination and completed["entrance"] == arrival_tag
            and completed["debit"] == "removed"
            and completed["travel_epoch"] == result["travel_epoch"] != distant["zone_epoch"]), completed
    settled = {"pending": None, "spent": {guid: "arrived"}}
    arrived = await wait(run, lambda status: ready(status, destination) and random_cube_ledger(status, cube) == settled
                         and (cube == "red" or status["game"].get("native_green_arrival")))
    assert arrived["zone_epoch"] == result["travel_epoch"], arrived
    peer = await wait(client_run, lambda status: status.get("state") == "connected"
                      and status.get("game", {}).get("map") == destination)
    assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
    await random_cube_action(cube, "inspect", guid, destination, "absent", arrived["game"]["pawns"]["local"]["name"])
    arrival_note = {
        "guid": guid, "map": destination, "entrance": completed["entrance"], "debit": completed["debit"],
        "character_id": character_id, "host_session": host_session,
    }
    if cube == "green":
        arrival = arrived["game"]["native_green_arrival"]
        verify_green_arrival(arrival, destination)
        arrival_note["arrival"] = arrival
    note(f"{cube}_cube_moves_party_to_rolled_stock_destination", arrival_note)
    return guid, destination


async def verify_random_cube_restart(cube, guid, destination, character_id, host_session):
    resumed = await wait(run, lambda status: party_in_zone(status, destination)
                         and status.get("generation") == host_session
                         and status["game"].get("native_transport", {}).get("state") == "ready")
    assert random_cube_ledger(resumed, cube) == {"pending": None, "spent": {guid: "arrived"}}, resumed
    await random_cube_action(cube, "inspect", guid, destination, "absent", resumed["game"]["pawns"]["local"]["name"])
    peer = await wait(client_run, lambda status: status.get("state") == "connected"
                      and status.get("game", {}).get("map") == destination)
    assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
    report = await command("events", "--campaign", campaign, "--tail", "100")
    completions = [event for event in report["events"] if event.get("guid") == guid
                   and event["event"] in (f"{cube}_travel_completed", f"{cube}_travel_recovered")]
    assert len(completions) == 1 and completions[0]["event"] == f"{cube}_travel_completed", completions
    note(f"{cube}_cube_destination_and_single_debit_survive_restart", {
        "guid": guid, "map": destination, "character_id": character_id, "host_session": host_session,
    })


def verify_observatory_rest(proof, pawns):
    actor = f"SavePoint_BP_C {observatory}.Observitory_P:PersistentLevel.SavePoint_BP_C_3"
    assert proof["destination"] == observatory and proof["bed"] == observatory_bed and proof["actor"] == actor, proof
    assert proof["min_safe_rep"] == -90, proof
    assert proof["placement"] == ("arrow" if proof["points"] >= -90 else "alternate"), proof
    assert len(proof["location"]) == 3 and all(math.isfinite(axis) for axis in proof["location"]), proof
    if proof["placement"] == "alternate":
        assert math.dist(proof["location"], (-5531.339, 7142.031, -755.404)) <= 350, proof
    host, guest = pawns["local"]["location"], pawns["guest"]["location"]
    assert math.dist(host, proof["location"]) <= 250 and math.dist(host, guest) <= 250, pawns


async def blue_cube_travel(character_id, host_session):
    entrance = "Obsv_Etr"
    bed = observatory_bed

    async def ready(map_name, *, receipts=None):
        return await wait(run, lambda status: party_in_zone(status, map_name)
                          and status["game"].get("native_zone", {}).get("state") == "ready"
                          and status["game"].get("native_transport", {}).get("state") == "ready"
                          and (receipts is None or blue_ledger(status) == {"pending": None, "spent": receipts}))

    async def use(guid, source, pawn, *, after=0):
        await blue_action("use-probe", guid, source, "requested", pawn)
        event = await wait_campaign_event("native_blue_request", guid, after=after)
        assert event["source"] == source and event["entry"] == "use_held_item", event
        return event

    async def verify_arrival(guid, receipts):
        completed = await wait_campaign_event("blue_travel_completed", guid)
        assert completed["ok"] and completed["map"] == observatory and completed["entrance"] == "Bed", completed
        assert completed["debit"] == "removed", completed
        proof = completed["arrival"]
        arrived = await ready(observatory, receipts=receipts)
        positions = arrived["game"]["pawns"]
        verify_observatory_rest(proof, positions)
        peer = await wait(client_run, lambda status: status.get("state") == "connected"
                          and status.get("game", {}).get("map") == observatory)
        assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
        await blue_action("inspect", guid, observatory, "absent", positions["local"]["name"])
        return arrived, proof

    initial = await ready(crossroads)
    source_pawn = initial["game"]["pawns"]["local"]["name"]
    fallback_guid = uuid.uuid4().hex.upper()
    await blue_action("seed", fallback_guid, crossroads, "seeded", source_pawn)

    await position_for_native_exit("host", "near", entrance=entrance)
    await position_for_native_exit("guest", "far", entrance=entrance)
    distant = await wait(run, lambda status: party_in_zone(status, crossroads)
                         and math.dist(status["game"]["pawns"]["local"]["location"],
                                       status["game"]["pawns"]["guest"]["location"]) > 1500)
    denied = await use(fallback_guid, crossroads, source_pawn)
    rejection = denied["result"]
    assert (not rejection["ok"] and rejection["error"] == "travel_blocked"
            and {"id": character_id, "reason": "too_far"} in rejection["blockers"]), denied
    await blue_action("inspect", fallback_guid, crossroads, "present", source_pawn)
    untouched = await wait(run, lambda status: party_in_zone(status, crossroads)
                           and status["game"].get("native_blue_route", {}).get("destination") == observatory)
    assert untouched["game"]["native_blue_route"] == {
        "source": crossroads, "destination": observatory, "bed": "", "pawn": source_pawn,
    }, untouched
    assert untouched["zone_epoch"] == distant["zone_epoch"], untouched
    assert fallback_guid not in blue_ledger(untouched).get("spent", {}), untouched
    note("blue_cube_rejection_preserves_stock_item", {"guid": fallback_guid, "blockers": rejection["blockers"]})

    await position_for_native_exit("guest", "near", entrance=entrance)
    await wait(run, lambda status: party_in_zone(status, crossroads)
               and math.dist(status["game"]["pawns"]["local"]["location"],
                             status["game"]["pawns"]["guest"]["location"]) <= 1500)
    approved = await use(fallback_guid, crossroads, source_pawn, after=denied["sequence"])
    result = approved["result"]
    assert result["ok"] and result["map"] == observatory and result["entrance"] == "Bed", approved
    receipts = {fallback_guid: "arrived"}
    fallback, proof = await verify_arrival(fallback_guid, receipts)
    assert fallback["zone_epoch"] == result["travel_epoch"] != distant["zone_epoch"], fallback
    note("blue_cube_empty_rest_reaches_stock_observatory", {"guid": fallback_guid, "arrival": proof,
                                                             "same_character": character_id})

    host_pawn = fallback["game"]["pawns"]["local"]["name"]
    chosen_rest = await command("action", "--campaign", campaign, "native-blue-rest-fixture")
    assert chosen_rest["native_blue_route"] == {
        "source": observatory, "destination": observatory, "bed": bed, "pawn": host_pawn,
    }, chosen_rest

    await position_for_native_exit("host", "near", entrance=entrance)
    await position_for_native_exit("guest", "near", entrance=entrance)
    previous = await wait(run, lambda status: party_in_zone(status, observatory)
                          and math.dist(status["game"]["pawns"]["local"]["location"],
                                        status["game"]["pawns"]["guest"]["location"]) <= 1500
                          and all(math.dist(pawn["location"], proof["location"]) > 500
                                  for pawn in status["game"]["pawns"].values()))
    previous_pawns = previous["game"]["pawns"]
    rest_guid = uuid.uuid4().hex.upper()
    await blue_action("seed", rest_guid, observatory, "seeded", host_pawn)
    approved = await use(rest_guid, observatory, host_pawn)
    result = approved["result"]
    assert (result["ok"] and result["map"] == observatory and result["entrance"] == "Bed"
            and result["in_zone"] is True), approved
    receipts[rest_guid] = "arrived"
    restored, proof = await verify_arrival(rest_guid, receipts)
    assert restored["zone_epoch"] == result["travel_epoch"] == previous["zone_epoch"], restored
    for slot, pawn in restored["game"]["pawns"].items():
        assert pawn["name"] == previous_pawns[slot]["name"], (slot, pawn)
        assert math.dist(pawn["location"], previous_pawns[slot]["location"]) > 500, (slot, pawn)
    note("blue_cube_moves_same_pawns_to_saved_stock_rest", {
        "guid": rest_guid, "arrival": proof, "epoch": restored["zone_epoch"],
        "pawns": {slot: pawn["name"] for slot, pawn in restored["game"]["pawns"].items()},
    })
    return receipts


def campaign_party(status):
    return status.get("campaign", {}).get("world", {}).get("party", {})


async def stardust_visit(character_id, host_session):
    actor = "Dream_CountDown_BP_C_1"
    guid = uuid.uuid4().hex.upper()

    def isolation_ready(status):
        isolation = status.get("game", {}).get("native_dream_isolation", {})
        return isolation.get("state") == "ready" and isolation.get("map") == exchange and isolation.get("actor") == actor

    async def peer_in(map_name):
        return await wait(client_run, lambda status: status.get("state") == "connected"
                          and status.get("game", {}).get("map") == map_name
                          and status.get("character_id") == character_id and status.get("host_session") == host_session
                          and (map_name != exchange or isolation_ready(status)))

    async def dust(operation, map_name, pawn, state, quantity):
        amount = ["--amount", str(quantity)] if operation == "seed" else []
        reply = await command("action", "--campaign", campaign, f"native-stardust-{operation}",
                              "--target", guid, *amount)
        report = reply["native_stardust"]
        assert report["operation"] == operation and report["guid"] == guid, report
        assert report["map"] == map_name and report["pawn"] == pawn, report
        assert report["state"] == state and report["quantity"] == quantity, report
        if operation == "inspect":
            assert report["payload"] == "stock-default-v1", report
        return report

    async def use(pawn, after=0):
        await dust("use-probe", crossroads, pawn, "requested", 0)
        event = await wait_campaign_event("native_stardust_request", guid, after=after)
        assert event["source"] == crossroads and event["entry"] == "use_held_item", event
        return event

    initial = await wait(run, lambda status: party_in_zone(status, crossroads)
                         and status["game"].get("native_zone", {}).get("state") == "ready"
                         and status["game"].get("native_transport", {}).get("state") == "ready")
    host_pawn = initial["game"]["pawns"]["local"]["name"]
    await dust("seed", crossroads, host_pawn, "seeded", 2)
    await position_for_native_exit("host", "near", entrance="Obsv_Etr")
    await position_for_native_exit("guest", "far", entrance="Obsv_Etr")
    distant = await wait(run, lambda status: party_in_zone(status, crossroads)
                         and math.dist(status["game"]["pawns"]["local"]["location"],
                                       status["game"]["pawns"]["guest"]["location"]) > 1500)
    denied = await use(host_pawn)
    refusal = denied["result"]
    assert (not refusal["ok"] and refusal["error"] == "travel_blocked"
            and {"id": character_id, "reason": "too_far"} in refusal["blockers"]), denied
    await dust("inspect", crossroads, host_pawn, "present", 2)
    unchanged = await wait(run, lambda status: party_in_zone(status, crossroads)
                           and status["zone_epoch"] == distant["zone_epoch"])
    assert not campaign_party(unchanged).get("stardust_visit"), unchanged
    note("stardust_denied_without_consuming_stack", {"guid": guid, "quantity": 2, "blockers": refusal["blockers"]})

    await position_for_native_exit("guest", "near", entrance="Obsv_Etr")
    await wait(run, lambda status: party_in_zone(status, crossroads)
               and math.dist(status["game"]["pawns"]["local"]["location"],
                             status["game"]["pawns"]["guest"]["location"]) <= 1500)
    triggered = time.time()
    allowed = await use(host_pawn, after=denied["sequence"])
    result = allowed["result"]
    assert result["ok"] and result["map"] == exchange and result["entrance"] == "PlayerStart_1", allowed
    at_exchange = await wait(run, lambda status: party_in_zone(status, exchange) and isolation_ready(status)
                             and (campaign_party(status).get("stardust_visit") or {}).get("phase") == "visiting")
    assert at_exchange["zone_epoch"] == result["travel_epoch"] != distant["zone_epoch"], at_exchange
    assert not at_exchange["game"]["native_dream_isolation"]["early_requested"], at_exchange
    active = campaign_party(at_exchange)["stardust_visit"]
    assert (active["guid"] == guid and active["before"] == 2 and active["payload"] == "stock-default-v1"
            and active["source"] == crossroads and active["destination"] == exchange
            and active["return_map"] == observatory and active["return_bed"] == ""), active
    original_deadline = active["deadline"]
    assert math.isfinite(original_deadline) and original_deadline >= triggered + 90, active
    arrival = at_exchange["game"]["native_dream_arrival"]
    assert arrival["destination"] == exchange and arrival["actor"] == "PlayerStart_1", arrival
    pawns = at_exchange["game"]["pawns"]
    assert math.dist(pawns["local"]["location"], arrival["location"]) <= 250, pawns
    assert math.dist(pawns["local"]["location"], pawns["guest"]["location"]) <= 250, pawns
    await peer_in(exchange)
    await dust("inspect", exchange, pawns["local"]["name"], "present", 1)
    note("stardust_opens_isolated_exchange_and_consumes_one", {
        "guid": guid, "quantity": 1, "arrival": arrival, "deadline": original_deadline, "return_map": observatory,
    })

    return_deadline = original_deadline
    if args.dream_early:
        power = await command("action", "--campaign", campaign, "native-dream-power-fixture")
        assert power["native_dream_isolation"]["state"] == "ready", power
        assert power["native_dream_isolation"]["early_requested"] is True, power

        def shortened(status):
            visit = campaign_party(status).get("stardust_visit") or {}
            return (status.get("game", {}).get("map") == exchange and visit.get("id") == active["id"]
                    and visit.get("deadline", original_deadline) < original_deadline)

        accelerated = await wait(run, shortened, timeout=8)
        return_deadline = campaign_party(accelerated)["stardust_visit"]["deadline"]
        note("stock_exchange_power_shortens_durable_countdown", {
            "visit_id": active["id"], "before": original_deadline, "after": return_deadline,
        })

    receipt = {"id": active["id"], "guid": guid, "remaining": 1, "return_map": observatory}
    returned = await wait(run, lambda status: party_in_zone(status, observatory)
                          and campaign_party(status).get("last_stardust_visit") == receipt
                          and not campaign_party(status).get("stardust_visit"),
                          timeout=30 if args.dream_early else 110)
    assert time.time() >= return_deadline + 3, returned
    if args.dream_early:
        assert time.time() < original_deadline, returned
    assert returned["zone_epoch"] != at_exchange["zone_epoch"], returned
    completed = await wait_campaign_event("stardust_return_completed", guid)
    assert (completed["ok"] and completed["id"] == receipt["id"] and completed["map"] == observatory
            and completed["entrance"] == "Bed" and completed["travel_epoch"] == returned["zone_epoch"]), completed
    proof = completed["arrival"]
    assert proof == returned["game"]["native_blue_arrival"], completed
    verify_observatory_rest(proof, returned["game"]["pawns"])
    await peer_in(observatory)
    await dust("inspect", observatory, returned["game"]["pawns"]["local"]["name"], "present", 1)
    note("stardust_returns_same_party_to_stock_rest", {"mode": "power" if args.dream_early else "timer",
                                                      "receipt": receipt, "arrival": proof, "character_id": character_id})
    return receipt


async def observatory_round_trip(character_id, host_session):
    entrance = "Obsv_Etr"

    async def sweep(target):
        response = await command("action", "--campaign", campaign, "native-exit-wall-probe",
                                 "--target", target, "--entrance", entrance)
        return response["native_zone_wall"]["outcome"]

    directions = (
        (crossroads, observatory, "LevelExit_Spawn_BP_C_1", (-5531.339, 7142.031, -655.404)),
        (observatory, crossroads, "LevelExit_Spawn_BP_C_2", (-21300, -2250, -50)),
    )
    for source, destination, marker, location in directions:
        ready = await wait(run, lambda status: party_in_zone(status, source)
                           and status["game"].get("native_zone", {}).get("state") == "ready")
        denied_count = ready["game"]["native_zone"]["guest_denied"]
        await position_for_native_exit("guest", "near", entrance=entrance)
        if args.native_walls:
            await wait(run, lambda status: status["game"].get("native_zone_wall", {}).get("state") == "ready")
            assert await sweep("guest") == "wall"
            blocked_guest = await wait(run, lambda status: party_in_zone(status, source))
        else:
            await position_for_native_exit("guest", "inside", entrance=entrance)
            blocked_guest = await wait(run, lambda status: party_in_zone(status, source)
                                       and status["game"]["native_zone"]["guest_denied"] > denied_count)
        assert blocked_guest["zone_epoch"] == ready["zone_epoch"]

        await position_for_native_exit("guest", "far", entrance=entrance)
        await position_for_native_exit("host", "near", entrance=entrance)
        distant = await wait(run, lambda status: party_in_zone(status, source)
                             and math.dist(status["game"]["pawns"]["local"]["location"],
                                           status["game"]["pawns"]["guest"]["location"]) > 1500)
        if args.native_walls:
            assert await sweep("host") == "wall"
            blocked_party = await wait(run, lambda status: party_in_zone(status, source))
        else:
            far_count = distant["game"]["native_zone"]["too_far"]
            await position_for_native_exit("host", "inside", entrance=entrance)
            blocked_party = await wait(run, lambda status: party_in_zone(status, source)
                                       and status["game"]["native_zone"]["too_far"] > far_count)
        assert blocked_party["zone_epoch"] == ready["zone_epoch"]

        await position_for_native_exit("host", "near", entrance=entrance)
        await position_for_native_exit("guest", "near", entrance=entrance)
        await wait(run, lambda status: party_in_zone(status, source)
                   and math.dist(status["game"]["pawns"]["local"]["location"],
                                 status["game"]["pawns"]["guest"]["location"]) <= 1500)
        if args.native_walls:
            await wait(run, lambda status: status["game"].get("native_zone_wall", {}).get("host_open"))
            assert await sweep("host") == "clear"
        else:
            await position_for_native_exit("host", "inside", entrance=entrance)
        arrived = await wait(run, lambda status: party_in_zone(status, destination)
                             and status.get("zone_epoch") != ready["zone_epoch"])
        host_location = arrived["game"]["pawns"]["local"]["location"]
        assert math.dist(host_location[:2], location[:2]) <= 350, (marker, host_location)
        assert abs(host_location[2] - location[2]) <= 600, (marker, host_location)
        peer = await wait(client_run, lambda status: status.get("state") == "connected"
                          and status.get("game", {}).get("map") == destination)
        assert peer["character_id"] == character_id and peer["host_session"] == host_session, peer
        events = await command("events", "--campaign", campaign)
        assert any(event["event"] == "zone_travel_ready" and event.get("source") == source
                   and event.get("destination") == destination and event.get("entrance") == entrance
                   for event in events["events"]), events
        note("observatory_stock_exit_matches_destination_arrow", {
            "source": source, "map": destination, "entrance": entrance, "marker": marker,
            "host": host_location, "guest_blocked": True, "distant_party_blocked": True,
        })


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
        authored_route = any((args.authored_zone, args.native_door, args.native_gold,
                              args.native_blue, random_cube, args.native_stardust, args.observatory_zone))
        initial_zone = ["--start-zone", crossroads] if authored_route else []
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
        if args.native_roster:
            census = await wait(run, lambda status: status.get("game", {}).get("native_roster", {}).get("state")
                                == "qualified" and status["game"]["native_roster"]["count"] == 1
                                and status["game"]["native_roster"]["connections"][0]["state"] == "ready")
            connection = census["game"]["native_roster"]["connections"][0]
            assert connection["pawn"] == census["game"]["pawns"]["guest"]["name"], connection
            assert connection["controller"] == census["game"]["pawns"]["guest"]["controller"], connection
            assert not guest["game"].get("native_roster"), guest["game"]
            note("host_verifies_native_connection_controller_and_pawn", connection)
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
            if args.native_fall:
                await wait(run, lambda state: state["game"].get("native_fall", {}).get("state") == "ready")
                authority = ("action", "--campaign", campaign)
                lift = await command(*authority, "native-fall-position")
                landed = await wait(run, lambda state: state["game"].get("native_fall", {}).get("landings", 0)
                                    > lift["before"]["landings"] and state["gameplay"]["players"]["guest"]["downed"],
                                    timeout=20)
                proof = landed["game"]["native_fall"]
                assert proof["pawn"] == original_guest == lift["pawn"], proof
                assert proof["forwarded"] == lift["before"]["forwarded"] + 1, proof
                assert proof["elapsed"] > 0.5 and proof["vertical_speed"] < -1200 and not proof["actor_tick"], proof
                assert landed["game"]["pawns"]["local"]["health"] == hit["host_before"]
                assert not landed["game"]["native_dead_flags"]["guest"]
                await command(*authority, "native-fall-return")
                assert (await command(*authority, "interact", "--target", chosen))["effect"] == "revive"
                await wait(run, lambda state: state["game"]["pawns"]["guest"]["health"] == native_health)
                note("stock_guest_fall_downs_only_the_enrolled_pawn", proof)
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
        if args.native_roster:
            assert after["game"]["native_roster"]["connections"] == [connection], after["game"]["native_roster"]
            note("native_connection_identity_survives_relay_reconnect", {"id": connection["id"], "pawn": original_guest})
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
        elif args.native_blue:
            blue_receipts = await blue_cube_travel(chosen, guest["host_session"])
            expected_map = observatory
        elif random_cube:
            cube_guid, expected_map = await random_cube_travel(random_cube, chosen, guest["host_session"])
        elif args.native_stardust:
            stardust_receipt = await stardust_visit(chosen, guest["host_session"])
            expected_map = observatory
        elif args.observatory_zone:
            await observatory_round_trip(chosen, guest["host_session"])
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
        checkpoint_path = run.parent / "save" / "campaign.json"
        snapshot = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        saved_character = snapshot["characters"][chosen]
        assert len(saved_character["state"]["location"]) == 3
        assert saved_character["state"]["map"] == expected_map
        if args.native_gold:
            assert snapshot["world"]["map"] == gold_nexus
            assert snapshot["world"]["party"]["gold_transport"] == {"pending": None, "spent": {gold_guid: "arrived"}}
        if args.native_blue:
            assert snapshot["world"]["map"] == observatory
            assert snapshot["world"]["party"]["blue_transport"] == {"pending": None, "spent": blue_receipts}
            assert snapshot["world"]["native_rest"] == {"destination": observatory, "bed": observatory_bed}
        if random_cube:
            assert snapshot["world"]["map"] == expected_map
            assert snapshot["world"]["party"][f"{random_cube}_transport"] == {
                "pending": None, "spent": {cube_guid: "arrived"},
            }
        if args.native_stardust:
            assert snapshot["world"]["map"] == observatory
            assert snapshot["world"]["party"]["last_stardust_visit"] == stardust_receipt
            assert not snapshot["world"]["party"].get("stardust_visit")
        if args.native_guest:
            assert saved_character["state"]["health"] == native_health
        note("isolated_checkpoint", {"revision": saved["revision"],
                                     "character_location": saved_character["state"]["location"]})
        await stop(host, "--campaign", campaign)
        if args.native_seed or args.native_host_save or args.native_guest_save:
            shutdown_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if args.native_seed:
            seed = shutdown_checkpoint["world"]["native_observation"]["native_scene"]["instance_seed"]
            assert type(seed) in (int, float) and -2147483648 <= seed <= 2147483647 and seed == int(seed), seed
        if args.native_host_save:
            frame = shutdown_checkpoint["world"]["native_host_save"]
            assert frame["map"] == expected_map, frame
            saved_path = run.parent / "game-user" / "Saved" / "SaveGames" / (frame["slot"] + ".sav")
            contents = saved_path.read_bytes()
            assert contents.startswith(b"GVAS") and len(contents) == frame["size"], frame
            assert hashlib.sha256(contents).hexdigest() == frame["sha256"], frame
        if args.native_guest_save:
            detached_frame = shutdown_checkpoint["characters"][chosen]["state"]["native_guest_save"]
            host_frame = shutdown_checkpoint["world"]["native_host_save"]
            assert detached_frame["character_id"] == chosen and detached_frame["map"] == expected_map, detached_frame
            assert detached_frame["slot"] != host_frame["slot"], (host_frame, detached_frame)
            path = run.parent / "game-user" / "Saved" / "SaveGames" / (detached_frame["slot"] + ".sav")
            contents = path.read_bytes()
            assert contents.startswith(b"GVAS") and len(contents) == detached_frame["size"], detached_frame
            assert hashlib.sha256(contents).hexdigest() == detached_frame["sha256"], detached_frame
        host_restart = await start("host", "--campaign", campaign, "--port", str(port), "--headless", *fixtures)
        restored = await wait(run, lambda state: state.get("state") == "hosting" and
                              state.get("generation") != host_status["generation"])
        assert restored["campaign"]["campaign_id"] == host_status["campaign"]["campaign_id"]
        assert any(item["character_id"] == chosen for item in restored["campaign"]["characters"])
        if args.native_seed:
            proof_status = await wait(run, lambda state: state.get("generation") == restored["generation"]
                                      and state.get("game", {}).get("native_seed", {}).get("state")
                                      in {"verified", "changed", "save_only", "unavailable"})
            proof = proof_status["game"]["native_seed"]
            assert proof["state"] == "verified" and proof["destination"] == expected_map, proof
            assert proof["expected"] == proof["observed"] == proof["stock_stream"] == seed, proof
            note("stock_day_cycle_uses_checkpoint_seed_on_restart", {"seed": seed, "proof": proof})
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
            if args.native_guest_save:
                events = await command("events", "--campaign", campaign, "--tail", "100")
                proof = next(entry for entry in events["events"] if entry["event"] == "native_guest_save_restored"
                             and entry["generation"] == restored["generation"])
                assert proof["slot"] == detached_frame["slot"] and proof["character_id"] == chosen, proof
                assert proof["pawn"] == restored_item["game"]["pawns"]["guest"]["name"], proof
                assert proof["state"] == "restored" and proof["backpack_count"] >= 1, proof
                note("detached_guest_native_inventory_survives_in_its_own_save", {
                    "frame": detached_frame, "proof": proof, "item_guid": awarded["guid"],
                })
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
        if random_cube:
            await verify_random_cube_restart(random_cube, cube_guid, expected_map, chosen, restored["generation"])
        if args.native_blue:
            resumed = await wait(run, lambda status: party_in_zone(status, observatory)
                                 and status["game"].get("native_transport", {}).get("state") == "ready")
            assert blue_ledger(resumed) == {"pending": None, "spent": blue_receipts}, resumed
            assert resumed["game"]["native_blue_route"]["bed"] == observatory_bed, resumed
            resumed_peer = await wait(client_run, lambda status: status.get("state") == "connected"
                                      and status.get("game", {}).get("map") == observatory)
            assert resumed_peer["host_session"] == restored["generation"] and resumed_peer["character_id"] == chosen
            for guid in blue_receipts:
                await blue_action("inspect", guid, observatory, "absent", resumed["game"]["pawns"]["local"]["name"])
            note("blue_cube_destination_and_charges_survive_restart", {
                "map": observatory, "bed": observatory_bed, "guids": list(blue_receipts),
            })
        if args.native_stardust:
            resumed = await wait(run, lambda status: party_in_zone(status, observatory))
            assert campaign_party(resumed).get("last_stardust_visit") == stardust_receipt, resumed
            assert not campaign_party(resumed).get("stardust_visit"), resumed
            note("stardust_return_receipt_survives_restart", {"receipt": stardust_receipt})
            if args.native_host_save:
                events = await command("events", "--campaign", campaign, "--tail", "100")
                proof = next(entry for entry in events["events"] if entry["event"] == "native_host_save_restored"
                             and entry["generation"] == restored["generation"])
                assert proof["slot"] == frame["slot"] and proof["map"] == expected_map, proof
                assert proof["state"] == "restored" and proof["backpack_count"] >= 1, proof
                inventory = await command("action", "--campaign", campaign, "native-stardust-inspect",
                                          "--target", stardust_receipt["guid"])
                stack = inventory["native_stardust"]
                assert stack["state"] == "present" and stack["quantity"] == 1, stack
                assert stack["payload"] == "stock-default-v1" and stack["guid"] == stardust_receipt["guid"], stack
                note("stock_host_inventory_survives_restart_in_verified_native_save", {
                    "frame": frame, "restored": proof, "stack": stack,
                })
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
