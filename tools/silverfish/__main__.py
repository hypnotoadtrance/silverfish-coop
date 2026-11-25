"""Command-line interface for the two-player Project Silverfish prototype."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import asdict, replace
from getpass import getpass
from ipaddress import ip_address
import json
from pathlib import Path
import uuid

from .control import Control, current_status, select_run, submit
from .authored_routes import AUTHORED_ROUTES, MAPS as EXPANDED_MAPS
from .native_guest_armor import SEED_PROFILES as ARMOR_SEED_PROFILES
from .native_guest_consumable import RESULT as GUEST_CONSUMABLE_ACTIONS
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


def _target(parser: argparse.ArgumentParser, *, include_title: bool = False) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--campaign")
    target.add_argument("--profile")
    if include_title:
        target.add_argument("--title-profile", help="isolated co-op title profile")

def _join_target(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--invite", type=Path, help="private invitation file")
    target.add_argument("--host", help="numeric Direct IP address")
    parser.add_argument("--port", type=_port, help="host UDP port, defaults to 7777")
    parser.add_argument("--password", action="store_true", help="prompt for the Direct IP password")


def _direct_input(opts) -> tuple[int, str]:
    if opts.host is None and (opts.port is not None or opts.password):
        raise ValueError("--port and --password require --host")
    port = 7777 if opts.port is None else opts.port
    if port == 0:
        raise ValueError("Enter the UDP port the host is listening on")
    return port, getpass("Direct IP password: ") if opts.password else ""


def _handoff_generation(value: str) -> str:
    try:
        if value == "0" * 32 or uuid.UUID(hex=value).hex != value:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("expected the exact nonzero lowercase title handoff generation") from None
    return value


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
    stage.add_argument("--stock-cache", type=Path,
                       help="private cache for hard-linking stock game data on the same volume")

    menu = commands.add_parser("menu", help="open a staged game with the in-game Host and Join panel")
    menu.add_argument("--title-profile", default="co-op-menu")
    menu.add_argument("--campaign", help="campaign the title screen can host")
    menu.add_argument("--bind", default="127.0.0.1", help="local address for Direct IP")
    menu.add_argument("--port", type=_port, default=7777)
    menu.add_argument("--invite", type=Path, help="invitation the title screen can join")
    menu.add_argument("--interactive-join", action="store_true", help="offer Join before importing an invitation")
    menu.add_argument("--profile", default="guest", help="local guest profile")
    menu.add_argument("--character", help="host-owned character ID from the invitation's catalog")

    host = commands.add_parser("host", help="stage a listen session and wait for the guest")
    host.add_argument("--campaign", required=True)
    host.add_argument("--bind", default="127.0.0.1", help="local IP to bind for authenticated Direct IP")
    host.add_argument("--port", type=_port, default=7777)
    admission = host.add_mutually_exclusive_group()
    admission.add_argument("--direct", action="store_true", help="accept Direct IP guests without a password")
    admission.add_argument("--direct-password", action="store_true", help="prompt for a Direct IP password")
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
    host.add_argument("--native-host-dig-live-fixtures", action="store_true",
                      help="test ordinary stock Spade and Interact through the headless input wrappers")
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

    join = commands.add_parser("join", help="join by Direct IP or a private invitation")
    join.add_argument("--profile", required=True)
    _join_target(join)
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
    _join_target(catalog)
    catalog.add_argument("--profile", default="guest")
    create = commands.add_parser("create-character", help="create a character before joining")
    _join_target(create)
    create.add_argument("--profile", default="guest")
    create.add_argument("--name", required=True)
    create.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID on retry")

    inspect = commands.add_parser("status", help="read the latest structured session status")
    _target(inspect, include_title=True)
    routes = commands.add_parser("routes", help="list the current map's stock exit catalog")
    _target(routes)
    events = commands.add_parser("events", help="read recent structured session events")
    _target(events, include_title=True)
    events.add_argument("--tail", type=_bounded_count, default=30)

    title_select = commands.add_parser("title-select", help="choose an offered Host or Join action")
    title_select.add_argument("--title-profile", required=True)
    title_select.add_argument("--handoff-generation", type=_handoff_generation, required=True)
    title_select.add_argument("--role", choices=("host", "join"), required=True)
    title_cancel = commands.add_parser("title-cancel", help="cancel an in-progress title handoff")
    title_cancel.add_argument("--title-profile", required=True)
    title_cancel.add_argument("--handoff-generation", type=_handoff_generation, required=True)

    action = commands.add_parser("action", help="perform a supported local action")
    _target(action)
    action.add_argument("action", choices=[
        "checkpoint", "drop-link", "reconnect", "stop", "leave-session", "walk", "interact", "test-damage", "travel",
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
        "native-gold-bag-quest-fixture", "native-authored-pickup-claim", "native-authored-pickup-fixture", "native-authored-pickup-stock-fixture", "native-authored-pickup-approach",
        "native-dust-cabinet-stock-fixture", "native-molasses-cabinet-stock-fixture", "native-savlo-locker-stock-fixture",
        "native-dust-cabinet-guest-approach", "native-molasses-cabinet-guest-approach",
        "native-dust-cabinet-guest-stock-fixture", "native-molasses-cabinet-guest-stock-fixture",
        "native-quest-catalog", "native-broker-issue-fixture", "native-broker-position-fixture",
        "native-broker-grant-manifest",
        "native-broker-drill-issue-fixture", "native-broker-drill-seed-fixture", "native-broker-drill-inspect",
        "native-weapon-roundtrip",
        "native-guest-armor-equip", "native-guest-armor-fixture", "native-guest-helmet-equip", "native-guest-disguise-probe-fixture",
        "native-guest-weapon-seed", "native-guest-weapon-inspect", "native-guest-weapon-equip", "native-guest-weapon-fire",
        "native-guest-weapon-seed-ammo", "native-guest-weapon-reload",
        "native-guest-smg-hold-fixture", "native-guest-held-fire-fixture",
        "native-guest-melee-canary-equip", "native-guest-melee-canary", "native-guest-melee-damage-canary",
        "native-guest-melee-aim-fixture", "native-guest-melee-swing",
        "native-guest-reload-input-fixture", "native-guest-primary-input-fixture", "native-guest-melee-input-fixture",
        "native-guest-weapon-seed-light-pistol",
        *GUEST_CONSUMABLE_ACTIONS, "native-guest-mre-fixture",
        "native-inventory-view-key-fixture", "native-guest-clock-fixture",
        "native-enemy-loot-watch", "native-enemy-loot-clear", "native-bandit-aim-fixture", "native-bandit-shot-fixture", "native-quest-list",
        "native-enemy-loot-bank", "native-enemy-loot-claim", "native-enemy-loot-approach", "native-enemy-shell-input-fixture",
        "native-quest-interact", "native-broker-input-fixture", "native-wipe-view-key-fixture",
        "native-dialogue-open", "native-dialogue-choose", "native-dialogue-approach", "native-dialogue-stock-fixture", "native-borough-mama-trade",
        "native-dialogue-sewer-host-fixture", "native-sewer-mushroom-fixture",
        "native-crafting-bench", "native-crafting-bench-preflight", "native-crafting-bench-approach-fixture", "native-crafting-bench-scrap-fixture",
        "native-mosby-tape-preflight", "native-mosby-tape-convert", "native-mosby-tape-approach", "native-mosby-readings-preflight",
        "native-pump-tape-preflight", "native-pump-tape-convert", "native-pump-tape-approach",
        "native-drill-test-results-preflight", "native-drill-test-results-convert", "native-drill-test-results-approach",
        "native-drill-instructions-preflight", "native-drill-instructions-convert", "native-drill-instructions-approach",
        "native-mindcore-preflight", "native-mindcore-upload", "native-mindcore-install-fixture", "native-mindcore-approach-fixture",
        "native-mindcore-deck-load-fixture",
        "native-mindcore-interact-fixture", "native-mindcore-carry-fixture",
        "native-mindcore-guest-carry-fixture", "native-mindcore-carry-guest-approach",
        "native-prototype-drill-preflight",
        "native-pump-room-preflight", "native-pump-room-approach", "native-pump-room-xp-fixture",
        "native-mosby-station-fixture",
        "native-subway-valve-preflight", "native-subway-valve-approach", "native-subway-valve-activate",
        "native-subway-valve-xp-fixture", "native-subway-valve-position-fixture", "native-subway-valve-input-fixture",
        "native-campaign-npc-approach", "native-campaign-npc-refresh", "native-guest-sickness-fixture",
        "native-inventory-view-fixture",
        "native-rest-inspect", "native-rest", "native-rest-approach", "native-rest-ui-fixture", "native-rest-insomnia-fixture",
        "native-rest-dead-bridge-rep-fixture",
        "native-rest-guest-interaction-fixture", "native-wipe-inventory-fixture", "native-wipe-items", "native-wipe-recover",
        "native-bag-projection-fixture",
        "native-loot-inspect", "native-loot-open", "native-loot-approach", "native-loot-seed",
        "native-loot-claim", "native-loot-crate-diagnostic", "native-loot-open-fixture", "native-loot-crate-fixture", "native-loot-view-fixture",
        "native-loot-replenishment-fixture", "native-loot-vehicle-pry-fixture", "native-guest-vehicle-blowtorch",
        "native-loot-electronic-power-fixture",
        "native-guest-vehicle-crowbar", "native-guest-door-blowtorch", "native-guest-door-crowbar",
        "native-stock-door-interact", "native-stock-door-fixture", "native-stock-door-input-fixture",
        "native-stock-door-stage-fixture",
        "native-stock-door-trace",
        "native-stock-enemy-escrow-fixture", "native-stock-broker-key-fixture", "native-stock-enemy-loot-claim",
        "native-loose-pickup-claim", "native-loose-pickup-approach", "native-loose-pickup-fixture",
        "native-loose-pickup-expire-fixture",
        "native-mushroom-claim", "native-mushroom-approach-fixture", "native-mushroom-harvest-fixture", "native-mushroom-locate-fixture",
        "native-prop-carry-fixture",
        "native-wire-quest-preflight", "native-wire-quest-approach", "native-wire-board-fixture",
        "native-wire-board-stock-fixture", "native-wire-board-stock-f-fixture", "native-crossroads-jammer-preflight", "native-crossroads-jammer-approach",
        "native-crossroads-jammer-aim",
        "native-turret-photo-preflight", "native-turret-photo-capture", "native-turret-photo-capture-fixture", "native-turret-photo-shutter",
        "native-turret-photo-xp-fixture", "native-bounty-board-list", "native-board-contract-catalog", "native-host-camera-input-fixture",
        "native-host-camera-aim-fixture", "native-host-camera-equip-fixture", "refresh-invite",
        "native-host-landmine-prepare-fixture", "native-host-landmine-deploy-fixture", "native-host-landmine-inspect-fixture",
        "native-host-dig-acquire-fixture", "native-host-dig-fixture", "native-host-dig-inspect-fixture",
        "native-host-dig-live-fixture",
        "native-computer-board-preflight", "native-computer-board-approach",
        "native-coup-report-preflight", "native-coup-report-approach",
        "native-depot-documents-preflight", "native-depot-documents-approach",
        "native-brandy-preflight", "native-brandy-approach",
        "native-missing-contact-preflight", "native-missing-contact-approach", "native-missing-contact-handoff",
        "native-package-handoff-journal-fixture",
        "native-stolen-bag-preflight", "native-stolen-bag-approach", "native-stolen-bag-xp-fixture",
        "native-black-box-preflight", "native-black-box-approach", "native-black-box-xp-fixture",
        "native-dead-drop-preflight", "native-dead-drop-approach", "native-dead-drop-xp-fixture",
        "native-metro-dead-drop-preflight", "native-metro-dead-drop-approach", "native-metro-dead-drop-xp-fixture",
        "native-pocket-watch-preflight", "native-pocket-watch-approach",
        "native-savlo-ring-preflight", "native-savlo-ring-approach", "native-savlo-ring-xp-fixture",
        "native-molasses-preflight", "native-molasses-approach",
        "native-savlo-keycard-preflight", "native-savlo-keycard-approach", "native-savlo-keycard-xp-fixture",
        "native-meet-broker-preflight", "native-meet-broker-approach",
        "native-flood-jammer-preflight", "native-flood-jammer-approach", "native-flood-jammer-aim",
        "native-lake-jammer-preflight", "native-lake-jammer-approach", "native-lake-jammer-aim",
        "native-lenny-preflight", "native-lenny-approach", "native-lenny-aim", "native-lenny-revive-approach",
        "native-spratt-preflight", "native-spratt-approach", "native-spratt-xp-fixture", "native-spratt-aim", "native-spratt-revive-approach",
        "native-inventory-list", "native-inventory-split", "native-inventory-take-ammo", "native-inventory-sort",
        "native-inventory-store-equipment", "native-inventory-unequip",
        "native-stash-list", "native-stash-move",
        "native-stash-approach-fixture", "native-stash-setting-fixture",
        "native-guest-stack-drop-probe", "native-guest-stack-drop", "native-guest-stack-reclaim", "native-host-stack-drop",
        "native-host-stack-reclaim",
        "native-host-stack-drop-menu-fixture", "native-host-stack-drop-menu-close-fixture",
        "native-observatory-lantern-fixture", "native-standing-fuel-tank-tip-fixture",
        "native-guest-drop-projection-fixture",
        "native-world-loot-fixture",
        "native-world-loot-approach",
        "native-bandit-spawn", "native-bandit-position", "native-bandit-client-spawner-probe",
        "native-shop-approach", "native-shop-buy", "native-finite-shop-list", "native-finite-shop-buy",
        "native-finite-shop-sale-quote", "native-finite-shop-sell",
        "native-finite-shop-approach", "native-finite-shop-wallet-fixture", "native-interaction-fixture",
        "native-shop-view-fixture",
        "native-hazard-position", "native-hazard-start", "native-hazard-return",
        "native-anomaly-field-fixture", "native-anomaly-trigger-fixture", "native-placed-anomaly-guest-fixture",
        "native-electrode-retire-fixture",
        "native-canister-prepare-fixture", "native-canister-shoot-fixture", "native-canister-guest-guard-fixture",
        "native-canister-ignite-fixture",
        "native-fixed-explosive-prepare-fixture", "native-fixed-explosive-shoot-fixture",
        "native-fixed-explosive-guest-guard-fixture",
        "native-fall-position", "native-fall-return", "native-kill-volume-fall-fixture",
        "native-door-position", "native-door-probe", "native-cistern-keypad",
        "native-transport-probe",
        "native-matrix-inspect", "native-matrix-seed", "native-matrix-equip", "native-matrix-fuel", "native-matrix-probe",
    ])
    action.add_argument("--target", help="character ID, loot ID, destination map path, or quoted GUID|AMOUNT|EXPECTED for an ammo split")
    action.add_argument("--operation-id", help="repeat the same 32-digit lowercase hex ID to retry an interaction")
    action.add_argument("--amount", type=int, help="quantity or lane index for the selected fixture")
    action.add_argument("--kind", choices=sorted(SEED_KINDS | ARMOR_SEED_PROFILES.keys()),
                        help="stock weapon or gear profile for a headless seed fixture")
    action.add_argument("--x", type=float, help="normalized world X movement")
    action.add_argument("--y", type=float, help="normalized world Y movement")
    action.add_argument("--milliseconds", type=int, help="controlled movement or SMG hold duration")
    action.add_argument("--continuous", action="store_true", default=None,
                        help="apply each stock walking input on every available game frame")
    action.add_argument("--keep", action="append", metavar="ITEM=COUNT", help="inventory stack to retain after a wipe")
    action.add_argument("--exit-position", choices=("near", "inside", "far"), help="position relative to the staged exit")
    action.add_argument("--entrance", help="select an authored entrance tag for the local exit position fixture")
    action.add_argument("--exit-actor", help="select the exact stock actor when an entrance has multiple pads")
    action.add_argument("--pickup-source", help="select the exact placed pickup for a headless pickup fixture")
    action.add_argument("--item-guid", help="exact secondary item GUID for a fixture")
    return cli


def _repository_run(opts) -> Path:
    if getattr(opts, "title_profile", None) is not None:
        return select_run(opts.work, "titles", opts.title_profile)
    if opts.campaign is not None:
        return select_run(opts.work, "campaigns", opts.campaign)
    return select_run(opts.work, "clients", opts.profile)


async def _events(directory: Path, limit: int) -> dict:
    try:
        current = await current_status(directory)
    except FileNotFoundError:
        current = {}
    if current.get("event_source") == "memory":
        if current.get("active") and not current["stale"] and current.get("state") in {"hosting", "connected"}:
            result = await submit(directory, "events-recent", tail=limit)
            if result.get("ok") is not True:
                raise RuntimeError("The active controller could not return its recent events")
            return {"schema": 1, "events": result["events"]}
        return {"schema": 1, "events": current.get("recent_events", [])[-limit:]}
    with (directory / "events.jsonl").open(encoding="utf-8") as file:
        rows = deque(file, maxlen=limit)
    return {"schema": 1, "events": [json.loads(line) for line in rows if line.endswith("\n")]}


async def staged_title_menu(opts, *, on_title_ready=None, require_embedded=False, headless=False,
                            pause_fixture=False) -> dict:
    from . import embedded_title_plan, runtime
    from .service import MENU_HOST_GUEST_LIMIT, MENU_HOST_OPTIONS, menu_host_start
    from .title_session import SessionPlan, TitleSession, host_plan, join_plan

    join_offered = bool(opts.invite or getattr(opts, "interactive_join", False))
    if not (opts.campaign or join_offered):
        raise ValueError("Set a campaign for Host or enable Join")
    if opts.character and not opts.invite:
        raise ValueError("Character selection requires an invitation")
    if not runtime.qualified_authored_routes(opts.work):
        raise ValueError("The co-op title requires the current matched native stage")
    embedded = embedded_title_plan.installed(opts.work)
    if require_embedded and not embedded:
        raise ValueError("The packaged co-op title requires its matched embedded runtime")
    plans = {}
    if opts.campaign:
        default_port = opts.port or runtime.spare_udp_port()

        def make_host_plan(name, bind, port):
            campaign = select_run(opts.work, "campaigns", name)
            mode, area = menu_host_start(campaign)
            starting = {"start_preset": mode} if mode == "wanderer" else {}
            expected = area if starting else None
            if embedded:
                return embedded_title_plan.host_plan(opts.work, campaign, bind, port, mode, expected,
                                                     headless=headless, pause_fixture=pause_fixture)
            return host_plan(opts.work, campaign, bind, port, headless=headless, expected_map=expected,
                             max_guests=MENU_HOST_GUEST_LIMIT, **MENU_HOST_OPTIONS, **starting)

        configured = make_host_plan(opts.campaign, opts.bind, default_port)

        def resolve_host(claim):
            if claim.friendly_fire:
                raise ValueError("Choosing friendly fire requires the resident co-op title")
            if claim.stock_creator:
                raise ValueError("Stock character creation requires the resident co-op title")
            if claim.host_settings is None:
                return make_host_plan(opts.campaign, opts.bind, default_port)
            name, bind, port_text = claim.host_settings
            port = int(port_text) if port_text else default_port
            address = bind or opts.bind
            if not 1 <= port <= 65535 or "%" in address:
                raise ValueError("Host requires a valid UDP port and a numeric local address")
            return make_host_plan(name or opts.campaign, str(ip_address(address)), port)

        plans["host"] = replace(configured, resolve_claim=resolve_host)
    join_settings = None
    if join_offered:
        from .title_join_settings import TitleJoinSettings

        def make_join_plan(name, invite, character):
            guest = select_run(opts.work, "clients", name)
            if embedded:
                return embedded_title_plan.guest_plan(opts.work, guest, invite, character, headless=headless,
                                                      pause_fixture=pause_fixture)
            return join_plan(opts.work, guest, invite, character_id=character, headless=headless)

        fixed = make_join_plan(opts.profile, opts.invite.resolve(strict=True), opts.character) if opts.invite else None

        def resolve_join(claim):
            if claim.join_settings is None:
                if fixed is None:
                    raise ValueError("Import an invitation and choose a character before joining")
                return fixed
            import_id, name, character = claim.join_settings
            invite = join_settings.resolve(claim.generation, import_id, character)
            return make_join_plan(name or opts.profile, invite, character)

        async def join_without_selection():
            raise ValueError("A verified invitation and character selection are required")

        offered = fixed or SessionPlan(join_without_selection, lambda: None, lambda _: False)
        plans["join"] = replace(offered, resolve_claim=resolve_join)

    title = select_run(opts.work, "titles", opts.title_profile)
    ready_callback = on_title_ready

    def publish_ready():
        nonlocal ready_callback
        if ready_callback is not None:
            ready_callback()
            ready_callback = None

    with Control(title / "run") as control:
        while True:
            mailbox = title / "run" / "game-bridge" / uuid.uuid4().hex
            profile = title / "game-user"
            process = runtime.launch(opts.work, profile, mailbox, headless=headless, title_fixture=headless,
                                     inspect_title_profile=on_title_ready is not None)
            join_settings = None
            try:
                if join_offered:
                    join_settings = TitleJoinSettings(opts.work, mailbox)
                session = TitleSession(
                    runtime.Bridge(mailbox, process), plans, control=control, join_settings=join_settings,
                    on_title_ready=publish_ready if on_title_ready is not None else None,
                    title_saved_directory=profile / "Saved" if on_title_ready is not None else None,
                )
                await session.run()
            finally:
                try:
                    if process.poll() is None:
                        await runtime.stop_game(process)
                finally:
                    if join_settings is not None:
                        await join_settings.close()
            if not session.return_to_title:
                break
    return {"ok": True, "state": "stopped"}


async def execute(opts):
    operation = opts.operation
    if operation == "stage":
        return prepare_game(opts.work, opts.source, opts.ue4ss, opts.signatures,
                            cpp_runtime=opts.cpp_runtime, cpp_addon=opts.cpp_addon, stock_cache=opts.stock_cache)
    if operation == "menu":
        return await staged_title_menu(opts)
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
        direct_password = getpass("Direct IP host password: ") if opts.direct_password else "" if opts.direct else None
        if opts.direct_password and not direct_password:
            raise ValueError("Enter a password or use --direct for passwordless hosting")
        await host_game(opts.work, select_run(opts.work, "campaigns", opts.campaign),
                        opts.bind, opts.port, opts.headless, opts.fixtures, rules,
                        native_events=opts.native_events, native_guest=opts.native_guest, native_zones=opts.native_zones,
                        native_zone_walls=opts.native_zone_walls, start_zone=opts.start_zone, max_guests=opts.max_guests,
                        expanded_zones=opts.native_expanded_zones, native_shop_view=opts.native_shop_view,
                        guest_regen=opts.native_guest_regen, guest_sickness=opts.native_guest_sickness, guest_mre=opts.native_guest_mre,
                        guest_held_fire=opts.native_guest_held_fire,
                        guest_melee_canary=opts.native_guest_melee_canary,
                        host_dig_live_fixture=opts.native_host_dig_live_fixtures,
                        native_dead_bridge_enemy_loot=opts.native_dead_bridge_enemy_loot,
                        start_preset=opts.native_start_preset, difficulty=opts.native_difficulty,
                        world_seed=opts.native_world_seed, direct_password=direct_password,
                        extraction=None if opts.native_extraction_keep is None else (
                            opts.native_extraction_keep, opts.native_extraction_backpacks, opts.native_extraction_lose_slot))
        return {"ok": True, "state": "stopped"}
    if operation == "join":
        from . import direct_ip
        from .runtime import staged_build
        from .service import PROTOCOL, join_game
        profile = select_run(opts.work, "clients", opts.profile)
        port, password = _direct_input(opts)
        invitation = opts.invite
        if opts.host is not None:
            if not opts.character:
                raise ValueError("Select a character from your Direct IP catalog with --character")
            invitation = await direct_ip.connect(profile, opts.host, port, password, staged_build(opts.work), PROTOCOL)
        await join_game(opts.work, profile, invitation, opts.headless, opts.character, native_zones=opts.native_zones,
                        native_zone_walls=opts.native_zone_walls, guest_reload_input=opts.native_guest_reload_input,
                        guest_inventory_view=opts.native_guest_inventory_view, guest_primary_input=opts.native_guest_primary_input)
        return {"ok": True, "state": "stopped"}
    if operation in {"characters", "create-character"}:
        from .service import character_catalog, direct_catalog
        port, password = _direct_input(opts)
        name, operation_id = getattr(opts, "name", None), getattr(opts, "operation_id", None)
        if opts.host is not None:
            profile = select_run(opts.work, "clients", opts.profile)
            return await direct_catalog(opts.work, profile, opts.host, port, password, name, operation_id)
        return await character_catalog(opts.work, opts.invite, name, operation_id)
    if operation == "invite":
        directory = select_run(opts.work, "campaigns", opts.campaign) / "run"
        return await submit(directory, "invite", name=opts.name, character=opts.character,
                            address=opts.address)

    if operation in {"title-select", "title-cancel"}:
        title = select_run(opts.work, "titles", opts.title_profile) / "run"
        fields = {"role": opts.role} if operation == "title-select" else {}
        return await submit(title, operation, handoff_generation=opts.handoff_generation, **fields)

    directory = _repository_run(opts) / "run"
    if operation == "status":
        return await current_status(directory)
    if operation == "routes":
        current = await current_status(directory)
        game = current.get("game") or {}
        role = current.get("role")
        expected = {"host": "hosting", "client": "connected"}.get(role)
        live = (expected is not None and current.get("active") is True and not current["stale"]
                and current.get("state") == game.get("state") == expected
                and (role == "host" or current.get("gameplay_fresh") is True))
        area = game.get("map") if live else None
        return {"schema": 1, "role": role, "state": current.get("state"), "map": area,
                "zone_epoch": current.get("zone_epoch") if live else None, "live": live,
                "routes": [asdict(route) for route in AUTHORED_ROUTES if route.source == area]}
    if operation == "events":
        return await _events(directory, opts.tail)
    if opts.kind is not None:
        valid_kinds = {"native-guest-weapon-seed": SEED_KINDS, "native-guest-armor-fixture": ARMOR_SEED_PROFILES}
        if opts.kind not in valid_kinds.get(opts.action, ()):
            raise ValueError("--kind must select a stock profile supported by this headless seed fixture")
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
                        amount=opts.amount, x=opts.x, y=opts.y, milliseconds=opts.milliseconds, continuous=opts.continuous,
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
