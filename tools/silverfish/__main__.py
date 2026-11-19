"""Command-line interface for the two-player Project Silverfish prototype."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import json
from pathlib import Path

from .control import select_run, status, submit
from .authored_routes import MAPS as EXPANDED_MAPS
from .native_guest_weapon import SEED_KINDS
from .native_host_save import STOCK_START_MODES
from .runtime import REPO, prepare_game
from .travel import AUTHORED_MAPS, DREAM_MAPS, GOLD_NEXUS, PLAYTEST_MAPS

def _workspace(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("expected a UDP port from 0 to 65535")
    return port


def _bounded_count(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 1000:
        raise argparse.ArgumentTypeError("expected a count from 1 to 1000")
    return count


def _target(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--campaign")
    target.add_argument("--profile")


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--work", type=_workspace, default=REPO / ".work",
                     help="isolated local staging and save root")
    commands = cli.add_subparsers(dest="operation", required=True)

    stage = commands.add_parser("stage", help="copy stock game files and stage UE4SS")
    stage.add_argument("--source", type=Path, required=True, help="original Steam installation")
    stage.add_argument("--ue4ss", type=Path, required=True, help="extracted UE4SS v4.0.0 release root")
    stage.add_argument("--signatures", type=Path, help="extracted Silverfish UE4SS_Signatures folder")
    stage.add_argument("--cpp-runtime", type=Path, help="locally built UE4SS v4.0.0 runtime DLL")
    stage.add_argument("--cpp-addon", type=Path, help="Silverfish native add-on from the same build")

    host = commands.add_parser("host", help="stage a listen session and wait for the guest")
    host.add_argument("--campaign", required=True)
    host.add_argument("--bind", default="127.0.0.1", help="local IP to bind for authenticated Direct IP")
    host.add_argument("--port", type=_port, default=7777)
    host.add_argument("--max-guests", type=int, choices=range(1, 5), default=1,
                      help="maximum simultaneous guests in the staged session")
    host.add_argument("--headless", action="store_true")
    host.add_argument("--native-events", action="store_true",
                      help="enable supervised host-side native event scope in the C++ lab")
    host.add_argument("--native-guest", action="store_true",
                      help="detach the authenticated guest's native save for the C++ playtest")
    host.add_argument("--native-guest-regen", action="store_true", help="enable qualified stock passive guest regeneration")
    host.add_argument("--native-guest-sickness", action="store_true", help="keep stock infection and recovery in the guest save")
    host.add_argument("--native-guest-melee-canary", action="store_true", help="observe one fenced stock Old Pipe swing in a headless fixture")
    host.add_argument("--native-guest-mre", action="store_true", help="enable guest MRE and Canned Peaches use with personal intoxication")
    host.add_argument("--native-guest-held-fire", action="store_true", help="enable experimental stock standard SMG holds")
    host.add_argument("--native-authored-enemy-loot", "--native-dead-bridge-enemy-loot",
                      dest="native_dead_bridge_enemy_loot", action="store_true",
                      help="share stock shotgun-shell rolls from exact Dead Bridge and Refinery CQC")
    host.add_argument("--native-zones", action="store_true", help="enable natural exit handling in the C++ playtest")
    host.add_argument("--native-zone-walls", action="store_true", help="enable physical blockers at the staged exit")
    host.add_argument("--native-expanded-zones", action="store_true", help="enable the curated stock exit catalog")
    host.add_argument("--start-zone", choices=sorted((AUTHORED_MAPS | EXPANDED_MAPS | PLAYTEST_MAPS) - DREAM_MAPS - {GOLD_NEXUS}),
                      help="start a new campaign in a qualified zone")
    host.add_argument("--native-start-preset", choices=("wanderer",), help="start a new campaign with a qualified stock character preset")
    host.add_argument("--native-difficulty", choices=STOCK_START_MODES, help="select the stock rules for a new Wanderer campaign")
    host.add_argument("--native-world-seed", type=int, help="choose the initial stock preset seed from 1 to 999999")
    host.add_argument("--fixtures", action="store_true", help="enable the playtest loot marker and controlled damage")
    host.add_argument("--native-shop-view", action="store_true", help="enable the qualified native guest merchant overlay")
    host.add_argument("--native-extraction-keep", type=int, choices=range(11),
                      help="enable stock extraction mode in a new campaign with this item retention limit")
    host.add_argument("--native-extraction-backpacks", action="store_true", help="drop owner-only backpacks on party wipes")
    host.add_argument("--native-extraction-lose-slot", action="store_true", help="reduce stock item retention after each wipe")
    host.add_argument("--fixture-keep-slots", type=int, help="test how many inventory stacks survive a wipe")
    host.add_argument("--fixture-backpacks", action="store_true", help="drop unretained test inventory in owner-only bags")

    invite = commands.add_parser("invite", help="create a private guest invitation file")
    invite.add_argument("--campaign", required=True)
    invite.add_argument("--name", required=True)
    invite.add_argument("--character", required=True)
    invite.add_argument("--address", help="address to put in the invite when different from the bind address")

    join = commands.add_parser("join", help="use a private invitation file")
    join.add_argument("--profile", required=True)
    join.add_argument("--invite", type=Path, required=True)
    join.add_argument("--headless", action="store_true")
    join.add_argument("--native-zones", action="store_true", help="enable natural exit handling in the C++ playtest")
    join.add_argument("--native-zone-walls", action="store_true", help="enable physical blockers at the staged exit")
    join.add_argument("--native-guest-reload-input", action="store_true",
                      help="enable stock guest reload input explicitly for an older invitation")
    join.add_argument("--native-guest-primary-input", action="store_true",
                      help="enable stock guest primary input explicitly for an older invitation")
    join.add_argument("--native-guest-inventory-view", action="store_true",
                      help="enable the stock guest inventory overlay explicitly for an older invitation")
    join.add_argument("--character", help="character ID selected from the guest's catalog")

    catalog = commands.add_parser("characters", help="list your host-owned characters before joining")
    catalog.add_argument("--invite", type=Path, required=True)
    create = commands.add_parser("create-character", help="create a character before joining")
    create.add_argument("--invite", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID on retry")

    inspect = commands.add_parser("status", help="read the latest structured session status")
    _target(inspect)
    events = commands.add_parser("events", help="read recent structured session events")
    _target(events)
    events.add_argument("--tail", type=_bounded_count, default=30)

    action = commands.add_parser("action", help="perform a supported local action")
    _target(action)
    action.add_argument("action", choices=[
        "checkpoint", "drop-link", "reconnect", "stop", "walk", "interact", "test-damage", "travel",
        "buy", "test-quest-reward", "vitals-probe", "vitals-reset", "quest-probe", "rest", "keep",
        "native-death-probe", "native-damage-probe", "native-inventory-probe", "native-guest-damage",
        "native-exit-position", "native-exit-wall-probe", "native-host-damage", "native-friendly-fire",
        "native-host-downed-inventory-fixture",
        "native-gold-seed", "native-gold-inspect", "native-gold-use-probe", "native-gold-action-probe",
        "native-blue-seed", "native-blue-inspect", "native-blue-use-probe", "native-blue-rest-fixture",
        "native-red-seed", "native-red-inspect", "native-red-use-probe",
        "native-green-seed", "native-green-inspect", "native-green-use-probe",
        "native-purple-seed", "native-purple-inspect", "native-purple-use-probe",
        "native-stardust-seed", "native-stardust-inspect", "native-stardust-use-probe", "native-dream-power-fixture",
        "native-fruiting-stardust-seed", "native-fruiting-stardust-inspect", "native-fruiting-stardust-use-probe",
        "native-dream-tarot-fixture",
        "native-dream-stone-position", "native-dream-stone-fixture",
        "native-return-to-rest", "native-dream-nothing-position",
        "native-pickup-spawn", "native-pickup", "native-quest-start", "native-quest-objective", "native-quest-turnin",
        "native-authored-pickup-claim", "native-authored-pickup-fixture", "native-authored-pickup-stock-fixture", "native-authored-pickup-approach",
        "native-dust-cabinet-stock-fixture", "native-molasses-cabinet-stock-fixture", "native-savlo-locker-stock-fixture",
        "native-quest-catalog", "native-broker-issue-fixture", "native-broker-position-fixture",
        "native-broker-grant-manifest",
        "native-broker-drill-issue-fixture", "native-broker-drill-seed-fixture", "native-broker-drill-inspect",
        "native-weapon-roundtrip",
        "native-guest-armor-equip", "native-guest-armor-fixture", "native-guest-helmet-equip",
        "native-guest-weapon-seed", "native-guest-weapon-inspect", "native-guest-weapon-equip", "native-guest-weapon-fire",
        "native-guest-weapon-seed-ammo", "native-guest-weapon-reload",
        "native-guest-smg-hold-fixture", "native-guest-held-fire-fixture",
        "native-guest-melee-canary-equip", "native-guest-melee-canary",
        "native-guest-reload-input-fixture", "native-guest-primary-input-fixture", "native-guest-weapon-seed-light-pistol",
        "native-guest-bandage", "native-guest-mre", "native-guest-canned-peaches", "native-guest-mre-fixture",
        "native-inventory-view-key-fixture", "native-guest-clock-fixture",
        "native-enemy-loot-watch", "native-enemy-loot-clear", "native-bandit-aim-fixture", "native-bandit-shot-fixture", "native-quest-list",
        "native-enemy-loot-bank", "native-enemy-loot-claim", "native-enemy-loot-approach", "native-enemy-shell-input-fixture",
        "native-quest-interact", "native-broker-input-fixture", "native-wipe-view-key-fixture",
        "native-dialogue-open", "native-dialogue-choose", "native-dialogue-approach", "native-dialogue-stock-fixture", "native-borough-mama-trade",
        "native-mosby-tape-preflight", "native-mosby-tape-convert", "native-mosby-tape-approach", "native-mosby-readings-preflight",
        "native-pump-tape-preflight", "native-pump-tape-convert", "native-pump-tape-approach",
        "native-pump-room-preflight", "native-pump-room-approach", "native-pump-room-xp-fixture",
        "native-mosby-station-fixture",
        "native-campaign-npc-approach", "native-campaign-npc-refresh", "native-guest-sickness-fixture",
        "native-inventory-view-fixture",
        "native-rest-inspect", "native-rest", "native-rest-approach", "native-rest-ui-fixture", "native-rest-insomnia-fixture",
        "native-rest-dead-bridge-rep-fixture",
        "native-rest-guest-interaction-fixture", "native-wipe-inventory-fixture", "native-wipe-items", "native-wipe-recover",
        "native-bag-projection-fixture",
        "native-loot-inspect", "native-loot-open", "native-loot-approach", "native-loot-seed",
        "native-loot-claim", "native-loot-open-fixture", "native-loot-crate-fixture", "native-loot-view-fixture",
        "native-wire-quest-preflight", "native-wire-quest-approach", "native-wire-board-fixture",
        "native-turret-photo-preflight", "native-turret-photo-capture", "native-turret-photo-capture-fixture", "native-turret-photo-shutter",
        "native-turret-photo-xp-fixture", "native-bounty-board-list", "native-host-camera-input-fixture",
        "native-host-camera-aim-fixture", "native-host-camera-equip-fixture", "refresh-invite",
        "native-computer-board-preflight", "native-computer-board-approach",
        "native-coup-report-preflight", "native-coup-report-approach",
        "native-depot-documents-preflight", "native-depot-documents-approach",
        "native-brandy-preflight", "native-brandy-approach",
        "native-pocket-watch-preflight", "native-pocket-watch-approach",
        "native-savlo-ring-preflight", "native-savlo-ring-approach", "native-savlo-ring-xp-fixture",
        "native-molasses-preflight", "native-molasses-approach",
        "native-savlo-keycard-preflight", "native-savlo-keycard-approach", "native-savlo-keycard-xp-fixture",
        "native-meet-broker-preflight", "native-meet-broker-approach",
        "native-flood-jammer-preflight", "native-flood-jammer-approach", "native-flood-jammer-aim",
        "native-lake-jammer-preflight", "native-lake-jammer-approach", "native-lake-jammer-aim",
        "native-lenny-preflight", "native-lenny-approach", "native-lenny-aim", "native-lenny-revive-approach",
        "native-inventory-list", "native-inventory-split",
        "native-bandit-spawn", "native-bandit-position", "native-bandit-client-spawner-probe",
        "native-shop-approach", "native-shop-buy", "native-finite-shop-list", "native-finite-shop-buy",
        "native-finite-shop-sale-quote", "native-finite-shop-sell",
        "native-finite-shop-approach", "native-finite-shop-wallet-fixture", "native-interaction-fixture",
        "native-shop-view-fixture",
        "native-hazard-position", "native-hazard-start", "native-hazard-return",
        "native-fall-position", "native-fall-return",
        "native-door-position", "native-door-probe", "native-cistern-keypad",
        "native-transport-probe",
        "native-matrix-inspect", "native-matrix-seed", "native-matrix-equip", "native-matrix-fuel", "native-matrix-probe",
    ])
    action.add_argument("--target", help="character ID, loot ID, destination map path, or quoted GUID|AMOUNT|EXPECTED for an ammo split")
    action.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID to retry an interaction")
    action.add_argument("--amount", type=int, help="quantity or lane index for the selected fixture")
    action.add_argument("--kind", choices=sorted(SEED_KINDS), help="stock weapon class for a headless weapon seed")
    action.add_argument("--x", type=float, help="normalized world X movement")
    action.add_argument("--y", type=float, help="normalized world Y movement")
    action.add_argument("--milliseconds", type=int, help="controlled movement or SMG hold duration")
    action.add_argument("--keep", action="append", metavar="ITEM=COUNT", help="inventory stack to retain after a wipe")
    action.add_argument("--exit-position", choices=("near", "inside", "far"), help="position relative to the staged exit")
    action.add_argument("--entrance", help="select an authored entrance tag for the local exit position fixture")
    action.add_argument("--exit-actor", help="select the exact stock actor when an entrance has multiple pads")
    action.add_argument("--pickup-source", help="select the exact placed pickup for a headless pickup fixture")
    action.add_argument("--item-guid", help="exact secondary item GUID for a fixture")
    return cli


def _repository_run(opts) -> Path:
    if opts.campaign is not None:
        return select_run(opts.work, "campaigns", opts.campaign)
    return select_run(opts.work, "clients", opts.profile)


def _events(directory: Path, limit: int) -> dict:
    with (directory / "events.jsonl").open(encoding="utf-8") as file:
        rows = deque(file, maxlen=limit)
    return {"schema": 1, "events": [json.loads(line) for line in rows if line.endswith("\n")]}


async def execute(opts):
    operation = opts.operation
    if operation == "stage":
        return prepare_game(opts.work, opts.source, opts.ue4ss, opts.signatures,
                            cpp_runtime=opts.cpp_runtime, cpp_addon=opts.cpp_addon)
    if operation == "host":
        from .service import host_game
        rules = None
        if opts.fixture_keep_slots is not None or opts.fixture_backpacks:
            if not opts.fixtures or (opts.fixture_keep_slots is not None and opts.fixture_keep_slots < 0):
                raise ValueError("Wipe fixture settings require --fixtures and a nonnegative slot count")
            rules = {"keep_slots": opts.fixture_keep_slots, "drop_backpacks": opts.fixture_backpacks}
        if opts.native_extraction_keep is None and (opts.native_extraction_backpacks or opts.native_extraction_lose_slot):
            raise ValueError("Set --native-extraction-keep to enable the stock extraction options")
        if rules is not None and opts.native_extraction_keep is not None:
            raise ValueError("Choose the stock extraction options or the wipe fixture settings")
        await host_game(opts.work, select_run(opts.work, "campaigns", opts.campaign),
                        opts.bind, opts.port, opts.headless, opts.fixtures, rules,
                        native_events=opts.native_events, native_guest=opts.native_guest, native_zones=opts.native_zones,
                        native_zone_walls=opts.native_zone_walls, start_zone=opts.start_zone, max_guests=opts.max_guests,
                        expanded_zones=opts.native_expanded_zones, native_shop_view=opts.native_shop_view,
                        guest_regen=opts.native_guest_regen, guest_sickness=opts.native_guest_sickness, guest_mre=opts.native_guest_mre,
                        guest_held_fire=opts.native_guest_held_fire,
                        guest_melee_canary=opts.native_guest_melee_canary,
                        native_dead_bridge_enemy_loot=opts.native_dead_bridge_enemy_loot,
                        start_preset=opts.native_start_preset, difficulty=opts.native_difficulty,
                        world_seed=opts.native_world_seed,
                        extraction=None if opts.native_extraction_keep is None else (
                            opts.native_extraction_keep, opts.native_extraction_backpacks, opts.native_extraction_lose_slot))
        return {"ok": True, "state": "stopped"}
    if operation == "join":
        from .service import join_game
        await join_game(opts.work, select_run(opts.work, "clients", opts.profile),
                        opts.invite, opts.headless, opts.character, native_zones=opts.native_zones,
                        native_zone_walls=opts.native_zone_walls, guest_reload_input=opts.native_guest_reload_input,
                        guest_inventory_view=opts.native_guest_inventory_view, guest_primary_input=opts.native_guest_primary_input)
        return {"ok": True, "state": "stopped"}
    if operation in {"characters", "create-character"}:
        from .service import character_catalog
        return await character_catalog(opts.work, opts.invite, getattr(opts, "name", None),
                                       getattr(opts, "operation_id", None))
    if operation == "invite":
        directory = select_run(opts.work, "campaigns", opts.campaign) / "run"
        return await submit(directory, "invite", name=opts.name, character=opts.character,
                            address=opts.address)

    directory = _repository_run(opts) / "run"
    if operation == "status":
        return status(directory)
    if operation == "events":
        return _events(directory, opts.tail)
    if opts.kind is not None and opts.action != "native-guest-weapon-seed":
        raise ValueError("--kind applies only to the headless native guest weapon seed")
    if opts.exit_actor is not None and (not opts.entrance or opts.action not in ("native-exit-position", "native-exit-wall-probe")):
        raise ValueError("--exit-actor requires --entrance and a headless exit position or wall probe")
    if opts.pickup_source is not None and opts.action not in ("native-authored-pickup-approach", "native-authored-pickup-fixture",
                                                             "native-authored-pickup-stock-fixture"):
        raise ValueError("--pickup-source applies only to the headless placed pickup fixtures")
    retained = {}
    for value in opts.keep or ():
        item, separator, count = value.partition("=")
        if not item or not separator or item in retained or not count.isdecimal() or int(count) < 1:
            raise ValueError("Each retained item needs a distinct ID and positive count")
        retained[item] = int(count)
    return await submit(directory, opts.action, target=opts.target, operation_id=opts.operation_id,
                        amount=opts.amount, x=opts.x, y=opts.y, milliseconds=opts.milliseconds,
                        retained=retained if opts.action == "keep" else None,
                        exit_position=opts.exit_position, entrance=opts.entrance, exit_actor=opts.exit_actor,
                        item_guid=opts.item_guid, kind=opts.kind, pickup_source=opts.pickup_source)


def main() -> int:
    options = parser().parse_args()
    try:
        reply = asyncio.run(execute(options))
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        reply = {"ok": False, "error": str(error), "error_type": type(error).__name__}
    print(json.dumps(reply, ensure_ascii=True, allow_nan=False))
    return 1 if reply.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
