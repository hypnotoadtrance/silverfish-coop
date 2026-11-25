"""Cold-start two private replicas with the drag-and-drop co-op release."""

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import combinations
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap import install_stage
from tools.silverfish import (
    direct_ip, embedded_startup, native_finite_shop, native_guest_consumable, native_guest_drop, native_guest_melee,
    native_host_save, native_inventory, native_loose_pickup, native_smg_hold,
    native_anomaly_effects, native_critters, native_destructibles, native_lantern, native_npc_barter, native_loot, native_prop_carry, native_stash, native_stock_switch, native_weapon_fire_view,
    native_stock_enemy_drops, native_world_loot, runtime,
)
from tools.silverfish.authored_loot import GUARANTEED_CRATES
from tools.silverfish.control import NAME, current_status, submit
from tools.silverfish.guest_weapon_profiles import FIREARMS, SHOTGUN_AMMO, THIRTY_06_FMJ, THIRTY_06_INCENDIARY
from tools.silverfish.service import PROTOCOL, menu_host_checkpoint
from tools.silverfish.steam_cloud import FOLDER
from tools.silverfish.travel import CROSSROADS, OBSERVATORY
from tools.silverfish.win_job import GameProcess
from live_installed_title_smoke import boot_states, native_mailbox
from live_loose_pickup_smoke import DEFAULT_ACTOR
from live_ai_smoke import wait
from live_cold_cabinet_pickup_smoke import copy_resident_checkpoint, instrument, verify_resident_pickup, verify_saved_pickup
from live_dynamic_board_smoke import staged_probe as eye_board_overlay
from live_multi_smoke import campaign_save_hashes, saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_stock_creator_smoke import creator_action, installed_probe
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, offered, start_pair
from portable_dock_pursuit import verify_dock_pursuit
from portable_held_mask import verify_stock_held_mask
from portable_eye_board import verify_stock_eye_stacks
from portable_guest_world_start import GuestWorldStartProof, resident_events, wait_resident_status
from portable_acid_pool import acid_pool_overlay, verify_acid_pool
from portable_alternate_ammo import verify_alternate_ammo
from portable_light_ar import verify_fire_mode_reopen, verify_stock_light_ar
from portable_loki_control import verify_guest_loki, verify_guest_loki_reconnected, verify_guest_loki_reopened, verify_stock_loki_control
from portable_lingering_teleport import lingering_overlay, verify_stock_lingering_teleport
from portable_personal_consumable import verify_personal_consumable_reconnect, verify_stock_personal_consumable
from portable_wipe_chooser import verify_stock_wipe_chooser
from portable_simmi_jammer import verify_stock_simmi_jammers
from portable_quarry_photo import verify_quarry_photo
from portable_weather_travel import verify_weather_travel
from portable_lightning import verify_stock_lightning
from portable_storm import StockStormTrial
from portable_stock_door import verify_stock_crossroads_door, verify_stock_dock_gate
from portable_stock_shovel import verify_stock_buried_placements, verify_stock_shovel, verify_stock_shovel_reconnect
from portable_mushrooms import verify_stock_mushrooms
from portable_placed_item import placed_item_overlay, verify_stock_placed_item
from portable_pickup_preview import pickup_preview_overlay, verified_drop_preview
from portable_cash_popup import cash_pickup_overlay, verify_stock_cash_popup
from portable_world_view_receipt import patch_world_view_receipts, verify_world_view_receipt
from tools.silverfish.title_session import STATUS_FILE as TITLE_STATUS_FILE


CABINET_RELEASE_SHA256 = "e35f410f7231f2c510b4ec43c34fb59ff7e9574187e2cd32fe66a61b3e87c14c"
WAREHOUSE_DOCK = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"

# Checks that reacquire their target on an already loaded map can still run
# when an unrelated fixture on the same map has failed.
INDEPENDENT_RESIDENT_MAPS = {
    "stock_food": {OBSERVATORY}, "stock_cooked_meal": {OBSERVATORY}, "stock_melee_edges": {OBSERVATORY},
    "stock_avatar_tail": {OBSERVATORY}, "stock_impact_cues": {OBSERVATORY},
    "stock_lockpicks": {OBSERVATORY}, "stock_observatory_tv": {OBSERVATORY},
    "stock_critters": {CROSSROADS}, "stock_lingering_teleport": {CROSSROADS}, "stock_mushrooms": {CROSSROADS},
    "stock_placed_item": {CROSSROADS}, "stock_cash_popup": {CROSSROADS}, "stock_weather_travel": {OBSERVATORY},
    "stock_warehouse_switch": {OBSERVATORY, WAREHOUSE_DOCK}, "stock_warehouse_radio": {OBSERVATORY, WAREHOUSE_DOCK},
    "stock_warehouse_candle": {WAREHOUSE_DOCK}, "stock_dock_gate": {WAREHOUSE_DOCK},
    "stock_bottle": {OBSERVATORY, WAREHOUSE_DOCK},
    "stock_prop_breaks": {WAREHOUSE_DOCK}, "stock_acid_bottle": {WAREHOUSE_DOCK}, "stock_wood_debris": {WAREHOUSE_DOCK},
}


async def run_resident_checks(checks, host, guest, finish_check, skip_check):
    gameplay = {}
    blocking_failure = None
    for name, check in checks:
        if blocking_failure and name not in INDEPENDENT_RESIDENT_MAPS:
            skip_check(name, blocking_failure)
            continue
        started = time.perf_counter()
        try:
            # Travel replaces the stock guest pawn, so reacquire the current binding.
            frame = await wait_resident_status(host.campaign / "run", lambda f:
                not f.get("awaiting_zone_guest")
                and f.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            if blocking_failure and frame["game"].get("map") not in INDEPENDENT_RESIDENT_MAPS[name]:
                skip_check(name, blocking_failure)
                continue
            evidence = await check(host, guest, frame["game"]["native_guest_save"]["pawn"])
        except Exception as error:
            finish_check(name, started, error)
            blocking_failure = blocking_failure or name
        else:
            finish_check(name, started, evidence=evidence)
            gameplay.update(evidence)
    return gameplay


@dataclass(frozen=True)
class PortableRelease:
    package: Path
    manifest_sha256: str
    title_directory: Path
    selection: dict
    activation: dict[Path, str | None]


def portable_main_overlay(game: Path, patch_script):
    return portable_script_overlay(game, "main.lua", patch_script)


@contextmanager
def portable_script_overlay(game: Path, script_name: str, patch_script):
    """Qualify and restore an isolated Lua overlay on a versioned portable replica."""
    if any(part.casefold() == "steamapps" for part in game.parts):
        raise ValueError("Use only a private portable replica for the test overlay")
    script = (game / runtime.SHIPPING).parent / runtime.MOD_SCRIPTS / script_name
    manifest = game / runtime.PORTABLE_STAGE_MANIFEST
    for file in (script, manifest):
        install_stage._ordinary(file)
    original_script, original_manifest = script.read_bytes(), manifest.read_bytes()
    inventory = json.loads(original_manifest)
    normalized_before = sha256(original_script.replace(b"\r\n", b"\n")).hexdigest()
    if inventory.get("lua_files", {}).get(script_name) != normalized_before:
        raise ValueError(f"The portable replica must start with its exact released {script_name}")

    newline = "\r\n" if b"\r\n" in original_script else "\n"
    original = original_script.decode("utf-8").replace("\r\n", "\n")
    patched = patch_script(original).replace("\n", newline)
    normalized_after = sha256(patched.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    inventory["lua_files"][script_name] = normalized_after
    patched_manifest = json.dumps(inventory, ensure_ascii=True, allow_nan=False) + "\n"
    provenance = {
        "script_source_before": normalized_before,
        "script_source_overlay": normalized_after,
        "manifest_before_sha256": sha256(original_manifest).hexdigest(),
        "manifest_overlay_sha256": sha256(patched_manifest.encode("utf-8")).hexdigest(),
    }
    def restore(path, original, overlay):
        install_stage._ordinary(path)
        current = path.read_bytes()
        if current not in (original, overlay.encode("utf-8")):
            raise RuntimeError(f"The private portable overlay changed unexpectedly at {path}")
        if current != original:
            runtime.write_text(path, original.decode("utf-8"), newline="")

    try:
        # Atomic replacement avoids editing a shared hard link in place.
        runtime.write_text(script, patched, newline="")
        runtime.write_text(manifest, patched_manifest, newline="")
        yield provenance
    finally:
        try:
            restore(script, original_script, patched)
        finally:
            restore(manifest, original_manifest, patched_manifest)
        provenance["original_bytes_restored"] = script.read_bytes() == original_script and manifest.read_bytes() == original_manifest
        assert provenance["original_bytes_restored"], "The private portable overlay failed to restore its release files"


def host_npc_combat_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_npc_override_probe.lua").read_text(encoding="utf-8")
    project_downed = """
function(pawn, next_state)
    local state = inspect()
    assert(phase(state) == "hosting", "downed projection needs the active stock host")
    assert(players.command({"fixture", "player-state-exact", pawn:GetFullName(), next_state}, state, "hosting")
        == "player state projected")
    players.sync(state, "hosting")
end
"""
    return portable_main_overlay(game, lambda original: original + "\n;(function(project_downed)\n" + fixture
                                 + "\nend)(" + project_downed + ")\n")


def tutorial_dock_zone_overlay(game: Path):
    def allow_dock_position(script):
        gate = 'local fixture_mode = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        if script.count(gate) != 1:
            raise ValueError("The private replica lacks the authored exit fixture gate")
        return script.replace(gate, gate + '\n    or (os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
                              + '\n        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")', 1)

    return portable_script_overlay(game, "native_zone.lua", allow_dock_position)


def stock_door_overlay(game: Path):
    def allow_door_fixtures(script):
        for gate in ("SILVERFISH_COOP_NATIVE_FIXTURES", "SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES"):
            expected = f'headless and os.getenv("{gate}") == "1"'
            if script.count(expected) != 1:
                raise ValueError("The private replica lacks its stock door fixture gate")
            script = script.replace(expected, expected
                                    + '\n    or headless and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"', 1)
        return script

    return portable_script_overlay(game, "native_stock_door.lua", allow_door_fixtures)


def stock_wipe_chooser_overlay(game: Path):
    def allow_stock_fixture(script):
        gate = 'local stock_fixture = os.getenv("SILVERFISH_COOP_STOCK_WIPE_TEST") == "1"'
        if script.count(gate) != 1:
            raise ValueError("The private replica lacks the stock wipe chooser fixture gate")
        return script.replace(gate, gate + '\n    or headless and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"')

    return portable_script_overlay(game, "native_wipe_view.lua", allow_stock_fixture)


def jammer_aim_overlay(game: Path):
    def allow_isolated_aim(script):
        gate = 'local enabled = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1" and os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
        if script.count(gate) != 1:
            raise ValueError("The replica lacks the stock jammer aim fixture gate")
        return script.replace(gate, gate + '\n    or (os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
                              + '\n        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")', 1)

    return portable_script_overlay(game, "native_flood_jammer_aim_fixture.lua", allow_isolated_aim)


def shop_lease_overlay(game: Path):
    def allow_isolated_approach(text):
        gate = 'local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        if text.count(gate) != 1:
            raise ValueError("The replica lacks the known stock merchant approach gate")
        return text.replace(gate, gate + '\n    or (os.getenv("SILVERFISH_COOP_HEADLESS") == "1"'
                            + '\n        and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")', 1)

    return portable_script_overlay(game, "native_finite_shop.lua", allow_isolated_approach)


def stock_nearby_overlay(game: Path):
    def omit_available_bolt(script):
        marker = '                if path then\n                    rows[#rows + 1] = status_row("native-loose-pickup",'
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock loose-item nearby report")
        condition = f'if path and (phase ~= "connected" or path ~= "{DEFAULT_ACTOR}" or availability ~= "available") then'
        return script.replace(marker, marker.replace("if path then", condition), 1)

    return portable_script_overlay(game, "native_authored_pickup.lua", omit_available_bolt)


def loose_revisit_overlay(game: Path):
    def add_loose_observation(script):
        marker = '    if action == "native-loose-pickup-check" then'
        observer = '''    if action == "fixture-loose-revisit-visibility" and fixtures and title_fixtures then
        local dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
        assert(state.map == dock and (phase == "hosting" or phase == "connected"), "The test needs Tutorial Dock")
        local leaf = fields[3]
        assert(#fields == 3 and (leaf == "Item_Bandage_C_1" or leaf == "Item_Bandage_C_2" or leaf == "Wep_RustyPistol_C_1"))
        local diagnosis = SilverfishDiagnoseLoosePickup(dock .. ".Tut_Dock_P:PersistentLevel." .. leaf)
        if diagnosis == "actor_absent" then return "absent" end
        if diagnosis == "hidden_or_disabled" then return "hidden" end
        if diagnosis == "stock_item_available" then return "visible" end
        error("Unexpected stock loose pickup state " .. tostring(diagnosis), 0)
    end
'''
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock loose-item command")
        return script.replace(marker, observer + marker, 1)

    return portable_script_overlay(game, "native_authored_pickup.lua", add_loose_observation)


def stock_shovel_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_stock_shovel_probe.lua").read_text(encoding="utf-8")

    def add_shovel_fixture(script):
        marker = "function dig.command(fields, state, phase)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock dig command")
        return script.replace(marker, marker + fixture, 1)

    return portable_script_overlay(game, "native_host_dig_live.lua", add_shovel_fixture)


def stock_board_approach_overlay(game: Path):
    def allow_portable_approach(script):
        gate = 'local fixtures = os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
        if script.count(gate) != 1:
            raise ValueError("The private replica lacks the authored board approach gate")
        return script.replace(gate, 'local fixtures = (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") == "1"'
                              '\n    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")', 1)

    return portable_script_overlay(game, "native_wire_quest.lua", allow_portable_approach)


def guest_weapon_fixture_overlay(game: Path, name: str):
    fixture = (Path(__file__).with_name("fixtures") / name).read_text(encoding="utf-8") + "\n"

    def add_weapon_fixture(script):
        marker = "function guest_weapon.command(fields, state, phase)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the guest weapon command")
        return script.replace(marker, marker + fixture, 1)

    return portable_script_overlay(game, "native_guest_weapon.lua", add_weapon_fixture)


def warehouse_switch_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_stock_switch_probe.lua").read_text(encoding="utf-8")

    def add_switch_fixture(script):
        marker = "function switches.command(fields, state, phase)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock switch command")
        return script.replace(marker, marker + fixture, 1)

    return portable_script_overlay(game, "native_stock_switch.lua", add_switch_fixture)


def warehouse_board_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_stock_destructible_probe.lua").read_text(encoding="utf-8")

    def add_board_fixture(script):
        marker = "function props.command(args, state, phase)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock destructible command")
        return script.replace(marker, marker + fixture, 1)

    return portable_script_overlay(game, "native_destructibles.lua", add_board_fixture)


@contextmanager
def stock_bottle_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_stock_bottle_probe.lua").read_text(encoding="utf-8")

    def add_probe(script):
        marker = "function fields.rows(state, phase, row)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the anomaly state publisher")
        return script.replace(marker, fixture + "\n" + marker + "    poll_stock_prop_fixture(state, phase)\n", 1)

    def add_grip_probe(script):
        marker = "function carry.command(fields, current, seat)\n"
        probe = '''    if fields[2] == "fixture-stock-bottle-grip" and headless and seat == "hosting" then
        local actor = StaticFindObject(fields[3])
        assert(prop_mesh(actor) and actor:HasAuthority() and actor.BeingHeld
            and actor:GetClass():GetFName():ToString() == "Prop_Bottle_Booze01_C")
        for _, entry in pairs(holdings) do
            if same(entry.actor, actor) and held(entry.pawn, actor, entry.grip) then
                local target, rotation = {}, {}
                entry.grip:GetTargetLocationAndRotation(target, rotation)
                local desired = entry.target
                return ("sample=%s receivedZ=%s targetZ=%.1f"):format(tostring(entry.sample),
                    tostring(desired and desired.Z), target.Z)
            end
        end
        return "no authoritative bottle grip"
    end
'''
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock prop carry command")
        return script.replace(marker, marker + probe, 1)

    with ExitStack() as overlays:
        yield {name: overlays.enter_context(portable_script_overlay(game, name, patch)) for name, patch in (
            ("native_anomaly_fields.lua", add_probe), ("native_prop_carry.lua", add_grip_probe),
        )}


def roaming_barter_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_roaming_barter_probe.lua").read_text(encoding="utf-8")

    def add_barter_probe(script):
        marker = "function barter.sync(state, phase)\n"
        call = "    identity.sync(state, phase)\n"
        if script.count(marker) != 1 or script.count(call) != 1:
            raise ValueError("The replica lacks the stock NPC barter registry")
        return script.replace(marker, fixture + "\n" + marker, 1).replace(
            call, call + "    poll_roaming_probe(state, phase)\n", 1)

    return portable_script_overlay(game, "native_npc_barter.lua", add_barter_probe)


def stock_critter_overlay(game: Path):
    fixture = (Path(__file__).with_name("fixtures") / "portable_stock_critter_probe.lua").read_text(encoding="utf-8")

    def add_probe(script):
        marker = "function critters.command(args, state, seat)\n"
        if script.count(marker) != 1:
            raise ValueError("The replica lacks the stock critter command")
        return script.replace(marker, marker + fixture, 1)

    return portable_script_overlay(game, "native_critters.lua", add_probe)


def stash_memory_overlays(probes: ExitStack, game: Path, *, large: bool = False) -> dict:
    site_scan_probe = False

    def replace(text, original, updated):
        if text.count(original) != 1:
            raise ValueError("The replica lacks the exact released stash fixture")
        return text.replace(original, updated, 1)

    def approach(text):
        nonlocal site_scan_probe
        original = '            or os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1" then'
        text = replace(text, original,
            '            or (os.getenv("SILVERFISH_COOP_NATIVE_FIXTURES") ~= "1"\n'
            '                and (fields[2] ~= "native-stash-approach-fixture"\n'
            '                    or os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") ~= "1")) then')
        if not large:
            return text
        seed_sites = "local function seed_sites(cache, now)"
        if seed_sites in text:
            site_scan_probe = True
            text = replace(text, seed_sites, "local portable_site_scan\n" + seed_sites)
            scan = "    statics:GetAllActorsOfClass(cache.world, cache.base, found)"
            text = replace(text, scan, scan + """
    if not portable_site_scan or not same(portable_site_scan.world, cache.world) or portable_site_scan.map ~= cache.map then
        portable_site_scan = {world = cache.world, map = cache.map, count = 0}
    end
    portable_site_scan.count = portable_site_scan.count + 1""")
        seed = (Path(__file__).with_name("fixtures") / "portable_large_stash_authority.lua").read_text(encoding="utf-8")
        command = "function stash.command(fields, state, phase)"
        return replace(text, command, seed + "\n" + command + """
    if fields[2] == "portable-large-stash-seed" or fields[2] == "portable-large-stash-clear"
        or fields[2] == "portable-large-stash-census-reads" or fields[2] == "portable-stash-site-scan-reads" then
        return portable_large_stash(fields, state, phase)
    end""")

    def stock_view(text):
        gate = 'local fixtures = headless and os.getenv("SILVERFISH_COOP_NATIVE_INTERACT_FIXTURES") == "1"'
        text = replace(text, gate,
            'local fixtures = headless and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1"')
        start = """                local consumed, status = SilverfishProbeNativeInteractInput(pawn:GetAddress(), true)
                dispatch = status
                if consumed ~= true or status ~= "stock_started_consumed" then break end"""
        stock = """                local input = StaticFindObject("/Script/EnhancedInput.Default__EnhancedInputLibrary")
                local action = StaticFindObject("/Game/Classes/Player/Player_Inputs/IA_Interact.IA_Interact")
                if not valid(input) or not valid(action) or pawn.Holding_Interact ~= false then break end
                local started = input:MakeInputActionValueOfType(1, 0, 0, 0)
                local completed = input:MakeInputActionValueOfType(0, 0, 0, 0)
                pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_19(started, 0, 0, action)
                pawn:InpActEvt_IA_Interact_K2Node_EnhancedInputActionEvent_21(completed, .05, .05, action)
                dispatch = "stock Interact Started and Completed"
"""
        text = replace(text, start, stock.removesuffix("\n"))
        text = replace(text, '    current.state = "rendered"',
                       '    current.state, current.portable_memory_rendered = "rendered", resident'
                       + ('\n    current.portable_memory_bytes = resident and #raw or 0' if large else ''))
        dispatch = '    local verb = fields[2]'
        text = replace(text, dispatch, dispatch + """
    if verb == "portable-stash-memory-proof" and fixtures and #fields == 4 then
        local binding = report(state, phase)
        if not binding or not current or current.state ~= "rendered" or not current.portable_memory_rendered
            or tostring(current.sequence) ~= fields[3] or current.id ~= fields[4]
            or not same(binding.pawn, current.binding.pawn) or not visible(binding.controller, current.screen, true) then
            error("portable stash memory readback is unavailable", 0)
        end
        local cards = current.screen.StashPanel.Cards
        local count = cards:GetArrayNum()
        local first = count > 0 and entry(cards, 1)
        local item = valid(first) and first.Item
        return string.format("portable stock stash memory %s %d %s %d", fields[3], count,
            item and identifier(item[item_guid]) or "-", item and item[item_ammo] or 0)
    end""")
        if not large:
            return text
        text = replace(text, '    if verb == "portable-stash-memory-proof" and fixtures and #fields == 4 then',
            '    if verb == "portable-stash-memory-proof" and fixtures and (#fields == 4 or #fields == 5 and fields[5] == "large") then')
        return replace(text, "        local first = count > 0 and entry(cards, 1)", """
        if fields[5] == "large" then
            assert(view.memory_body_limit() == 2500000 and current.portable_memory_bytes > 32768 and count == 384,
                "portable large stash render did not cross the native ticket boundary")
            local guids = {}
            for index = 1, count do
                local card = entry(cards, index)
                local item = valid(card) and card.Item
                local class = item and item[item_class]
                local guid = item and identifier(item[item_guid])
                assert(guid and valid(class)
                    and class:GetFullName() == "BlueprintGeneratedClass /Game/Classes/Items/Item_Bandage.Item_Bandage_C"
                    and item[item_ammo] == 1, "portable large stash stock card is invalid")
                guids[index] = guid
            end
            return ("portable large stash memory %s %d %d %s"):format(
                fields[3], count, current.portable_memory_bytes, table.concat(guids, ","))
        end
        local first = count > 0 and entry(cards, 1)""".lstrip("\n"))

    overlays = {name: probes.enter_context(portable_script_overlay(game, name, patch_script))
                for name, patch_script in (("native_stash.lua", approach), ("native_stash_view.lua", stock_view))}
    overlays["native_stash.lua"]["site_scan_probe"] = site_scan_probe
    return overlays


def compact_crate_status_overlay(game: Path):
    """Exercise installed memory publication without disabling any portable isolation flags."""
    def patch_script(text):
        original_gate = ("    local compact_crates = resident_title and status_publication.installed\n"
                         "        and not status_publication.mirror and status_publication.memory_live")
        original_mirror = "if status_publication.memory_live and not status_publication.mirror and not file_command then return end"
        if text.count(original_gate) != 1 or text.count(original_mirror) != 1:
            raise ValueError("The replica lacks the exact installed crate diagnostic gate")
        # Keep the real HEADLESS environment and the full title/exit mirror.
        override = ("    local mirror_crate_status = status_publication.mirror\n"
                    '        and current_phase ~= "hosting" and current_phase ~= "connected"')
        text = text.replace(original_gate, override + "\n" + original_gate.replace("status_publication.mirror", "mirror_crate_status"))
        return text.replace(original_mirror, original_mirror.replace("status_publication.mirror", "mirror_crate_status"))

    return portable_main_overlay(game, patch_script)


def nonmirrored_startup_overlay(game: Path):
    def patch_script(text):
        gate = "if status_publication.memory_live and not status_publication.mirror and not file_command then return end"
        if text.count(gate) != 1:
            raise ValueError("The replica lacks the exact installed status publication guard")
        guard = ("if status_publication.memory_live and not file_command\n"
                 '        and (not status_publication.mirror or current_phase == "hosting" or current_phase == "connected")'
                 " then return end")
        return text.replace(gate, guard)

    return portable_main_overlay(game, patch_script)


async def verify_nonmirrored_startup(host, guest, proof):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    roles = {host_run: (host, "host", "hosting"), guest_run: (guest, "join", "connected")}

    async def prove_ready(run, seat, action, phase):
        file = seat.bridge.snapshot()
        assert file, "No original filesystem title receipt"

        def memory_only(frame):
            live = frame.get("game", {})
            return (frame.get("local_bridge") == frame.get("local_status_source") == "memory"
                    and frame.get("state") == phase and live.get("state") == phase
                    and live.get("sequence", 0) > file["sequence"])

        live = await wait_resident_status(run, memory_only)
        assert (file["state"], file["map"]) != (phase, live["game"]["map"]), (
            "The filesystem receipt is already eligible for gameplay readiness", file["state"], file["map"])
        status = seat.bridge.directory / TITLE_STATUS_FILE
        title = {}
        async with asyncio.timeout(8):
            while title.get("state") != "ready" or title.get("attempt", {}).get("action") != action:
                try:
                    title = runtime.read_json(status)
                except (FileNotFoundError, PermissionError):
                    pass
                if title.get("state") in {"ready", "failed", "cleanup_unverified"}:
                    break
                await asyncio.sleep(.1)
        assert (title["state"], title.get("progress"), title.get("attempt", {}).get("action")) == ("ready", phase, action), title
        assert seat.bridge.snapshot() == file, "A file command refreshed the title frame before readiness"
        return {"title_attempt": title["attempt"], "title_progress": title["progress"],
                "file_sequence": file["sequence"], "file_phase": file["state"],
                "memory_sequence": live["game"]["sequence"], "memory_generation": live["generation"],
                "game_save_scope": live["game"]["save_scope"]}

    async def wait_with_initial_readiness(run, predicate):
        frame = await wait_resident_status(run, predicate)
        seat, action, phase = roles[run]
        if frame.get("state") == phase and action not in proof:
            proof[action] = await prove_ready(run, seat, action, phase)
        return frame

    async def stock_character(guest_seat, join, name):
        assert proof.get("host")
        await create_stock_bag_character(guest_seat, join, name)
        creator_host = await prove_ready(host_run, host, "host", "hosting")
        assert creator_host["title_attempt"] == proof["host"]["title_attempt"]
        assert creator_host["memory_generation"] == proof["host"]["memory_generation"]
        assert creator_host["file_sequence"] == proof["host"]["file_sequence"]
        proof["host_stayed_ready_during_guest_stock_creation"] = True

    async def pause_from_memory(run):
        return await wait_resident_status(run, lambda frame: frame.get("game", {}).get("stock_pause_exit", {}).get("state")
                                          == "armed")

    return await start_pair(host, guest, "Unmirrored title scout", wait_status=wait_with_initial_readiness,
                            create_character=stock_character, wait_for_host_pause=pause_from_memory)


def launch_portable(game: Path, profile: Path) -> GameProcess:
    image = game / runtime.SHIPPING
    install_stage._ordinary(image)
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("SILVERFISH_COOP_")}
    arguments = subprocess.list2cmdline([str(image)])
    arguments += (f' -SilverfishCoopIsolated -userdir="{profile}" -unattended -NoSplash -windowed'
                  " -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480"
                  " -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,"
                  "[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock")
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=image, cwd=image.parent, env=env, startupinfo=info)


def fingerprint(path: Path) -> str | None:
    if not os.path.lexists(path):
        return None
    install_stage._ordinary(path)
    return runtime._file_sha256(path)


def qualify_release(game: Path, legacy_work: Path | None) -> PortableRelease:
    for name in runtime.STOCK_PACKAGES:
        package = game / runtime.PAKS / name
        if not package.is_file() or not install_stage._ordinary(package).st_size:
            raise ValueError(f"Stock content is missing from the portable test replica: {package}")
    mods = game / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    embedded = mods / install_stage.MOD
    marker = embedded / "resident-portable.json"
    descriptor = embedded / "resident-install.json"
    stage = game / runtime.PORTABLE_STAGE_MANIFEST
    activation = {
        mods / "mods.txt": fingerprint(mods / "mods.txt"),
        descriptor: fingerprint(descriptor),
        marker: fingerprint(marker),
        stage: fingerprint(stage),
    }
    if activation[marker] is None or activation[stage] is None:
        raise ValueError("Apply the release ZIP over the stock replica before running this test")
    for module in ("SilverfishCoop", runtime.CPP_MOD_NAME, install_stage.MOD):
        enabled = mods / module / "enabled.txt"
        install_stage._ordinary(enabled)
        activation[enabled] = fingerprint(enabled)
    metadata = runtime.read_json(marker, limit=8192)
    if metadata["schema"] != 1 or runtime._file_sha256(game / runtime.SHIPPING) != metadata["shipping_sha256"]:
        raise ValueError("The portable release does not match the shipping executable")

    addon = embedded / "dlls" / "main.dll"
    install_stage._ordinary(addon)
    image = addon.read_bytes()
    pinned = []
    for candidate in embedded.glob("runtime-*"):
        if re.fullmatch(r"runtime-[0-9a-f]{16}", candidate.name) and candidate.is_dir():
            digest = fingerprint(candidate / "runtime-manifest.json")
            if digest is not None and digest.encode("ascii") in image:
                manifest, _, _ = install_stage._manifest(candidate, addon)
                if candidate.name == "runtime-" + manifest[:16]:
                    pinned.append((candidate, manifest))
    if len(pinned) != 1:
        raise ValueError("The DLL must pin exactly one qualified versioned embedded runtime")
    package, digest = pinned[0]

    boot = {"schema": 1, "campaign": "default", "bind": "127.0.0.1", "port": 7777}
    seat_name = "local"
    if legacy_work is None:
        if activation[descriptor] is not None:
            raise ValueError("A clean portable replica must have no resident-install.json")
    else:
        if activation[descriptor] is None:
            raise ValueError("The upgrade replica needs its original isolated resident-install.json")
        legacy = runtime.read_json(descriptor, limit=8192)
        if (legacy.get("schema"), legacy.get("isolated"), legacy.get("shipping_sha256")) != (
                1, True, metadata["shipping_sha256"]
        ) or Path(legacy["work"]) != legacy_work or not NAME.fullmatch(legacy["seat"]):
            raise ValueError("The original upgrade descriptor must point at the supplied private isolated workspace")
        seat_name = legacy["seat"]
        boot.update({key: legacy[key] for key in ("campaign", "bind", "port")})
    return PortableRelease(package, digest, Path("titles") / seat_name, boot, activation)


def cabinet_provenance(archive: Path, games: tuple[Path, Path], releases: tuple[PortableRelease, PortableRelease],
                       candidate_sha256: str | None) -> dict:
    expected = CABINET_RELEASE_SHA256 if candidate_sha256 is None else candidate_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or runtime._file_sha256(archive) != expected:
        raise ValueError("The cabinet archive differs from its expected SHA-256 digest")
    if candidate_sha256 == CABINET_RELEASE_SHA256:
        raise ValueError("The published cabinet baseline cannot stand in for the joint presentation candidate")
    with ZipFile(archive) as bundle:
        for game, release in zip(games, releases, strict=True):
            # qualify_release has already verified all installed files pinned by this manifest.
            manifest = release.package / install_stage.MANIFEST
            embedded = release.package.parent
            gameplay = game / runtime.SHIPPING.parent / runtime.CPP_ADDON_DLL
            for path in (manifest, embedded / "resident-portable.json", embedded / "dlls/main.dll", gameplay):
                data = bundle.read(path.relative_to(game).as_posix())
                if sha256(data).hexdigest() != fingerprint(path):
                    raise ValueError(f"The portable cabinet replica differs from the pinned archive: {path}")
            revision = json.loads(manifest.read_bytes()).get("app_revision", "")
            if not re.fullmatch(r"[0-9a-f]{40}", revision) or (not candidate_sha256 and not revision.startswith("2abe7b2")):
                raise ValueError("The cabinet release does not identify its packed source revision")
        if candidate_sha256:
            projection = runtime.SHIPPING.parent / runtime.MOD_SCRIPTS / "native_guest_drop_projection.lua"
            if b"native-world-loot-present-host-claim" not in bundle.read(projection.as_posix()):
                raise ValueError("The pinned candidate does not include the joint pickup command")
    return {"release_source": revision[:7], "release_revision": revision, "release_archive_sha256": expected,
            "native_sha256": fingerprint(gameplay), "runtime_manifest_sha256": release.manifest_sha256,
            "cabinet_presentation": "combined" if candidate_sha256 else "separate"}


async def discover_mailbox(root: Path, process: GameProcess) -> Path:
    async with asyncio.timeout(30):
        while process.poll() is None and not root.is_dir():
            await asyncio.sleep(.1)
        if process.poll() is not None:
            raise RuntimeError(f"The shipping executable exited before creating its portable mailbox ({process.returncode})")
        return await native_mailbox(root, process)


async def portable_probe_command(bridge, prefix, request, *, reply_end=None):
    mailbox = bridge.directory
    ticket = uuid.uuid4().hex
    runtime.write_text(mailbox / f"{prefix}-command.tsv", f"{ticket}\t{request}")
    reply = mailbox / f"{prefix}-reply.tsv"
    async with asyncio.timeout(8):
        while bridge.process.poll() is None:
            try:
                fields = reply.read_text(encoding="utf-8").split("\t", 2)
            except (FileNotFoundError, PermissionError):
                fields = ()
            if len(fields) == 3 and fields[0] == ticket:
                assert fields[1] == "ok", fields[2]
                if reply_end is None or fields[2].endswith(reply_end):
                    return fields[2]
            await asyncio.sleep(.05)
    raise RuntimeError(f"The {prefix} process exited before replying")


def weapon_cue_files(host, guest):
    return [str(path) for seat in (host, guest) for name in ("native-weapon-shots", "native-weapon-shots.tmp")
            if os.path.lexists(path := seat.bridge.directory / name)]


async def verify_resident_footsteps(host: Seat, guest: Seat) -> list[dict]:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-footstep", action)

    for seat in (host, guest):
        assert await command(seat, "arm") == "armed"
        async with asyncio.timeout(5):
            while await command(seat, "ready") != "true":
                await asyncio.sleep(.05)

    receipts = []
    for direction, sender, observer in (("host_to_guest", host, guest), ("guest_to_host", guest, host)):
        for seat in (sender, observer):
            assert await command(seat, "clear") == "clear"
        original = await command(sender, "step")
        assert original.startswith("SoundCue /Game/Assets/Sounds/CUEs/") and "|" not in original, original
        async with asyncio.timeout(5):
            while True:
                local, remote = (await command(observer, "read")).split(";")
                if remote:
                    break
                await asyncio.sleep(.05)
        await asyncio.sleep(.2)
        assert await command(sender, "read") == f"{original};", "footstep echoed to sender"
        local, remote = (await command(observer, "read")).split(";")
        assert not local and "|" not in remote, (local, remote)
        cue, gain, pitch, offset, distance = remote.split(",")
        assert ",".join((cue, gain, pitch)) == original, (original, remote)
        assert float(offset) <= 2 and 0 < float(distance) <= 3000, remote
        receipts.append({"direction": direction, "cue": cue, "gain": float(gain), "pitch": float(pitch),
                         "offset_from_emitter_feet_cm": float(offset), "listener_distance_cm": float(distance),
                         "observer_replays": 1, "sender_echoes": 0})
    return receipts


async def verify_resident_pistol(host: Seat, guest: Seat, *, guest_combat: bool = False,
                                 rusty: bool = False, memory_queue: bool = False, pursuit: bool = False) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-dry-fire", action)

    async def wait_for(seat, action, expected):
        async with asyncio.timeout(8):
            while await command(seat, action) != expected:
                await asyncio.sleep(.05)

    async def read_single_click(seat, source):
        async with asyncio.timeout(6):
            while True:
                projectiles, clicks = (await command(seat, "read")).split(";")
                assert projectiles == "0", (source, projectiles)
                if clicks:
                    break
                await asyncio.sleep(.05)
        await asyncio.sleep(.12)
        assert await command(seat, "read") == f"0;{clicks}", "trigger click repeated or spawned a projectile"
        kind, *position = clicks.split(",")
        assert kind == source and len(position) == 3, clicks
        return [float(axis) for axis in position]

    async def input_result(event, sequence):
        async with asyncio.timeout(6):
            while True:
                for entry in await resident_events(guest_run, guest_frame):
                    if entry.get("sequence") == sequence and entry["event"] in (event, event + "_rejected"):
                        assert entry["event"] == event, entry
                        return entry["native_guest_weapon"]
                await asyncio.sleep(.08)

    for seat in (host, guest):
        assert await command(seat, "arm-rusty" if rusty else "arm") == "armed"
    host_guid = await command(host, "seed-local")
    guest_guid = await command(host, "seed-peer")
    assert all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in (host_guid, guest_guid)) and host_guid != guest_guid
    await wait_for(guest, "projected", guest_guid)
    for seat, guid, rounds in ((host, host_guid, 0), (guest, guest_guid, 1)):
        assert await command(seat, "equip") == "selected"
        await wait_for(seat, "ready", f"{guid},{rounds},{rounds}")
    await wait_for(host, "peer-ready", f"{guest_guid},1,1")

    guest_run = guest.client / "run"
    kind = "rusty-pistol" if rusty else "pistol"
    guest_frame = await wait_resident_status(guest_run, lambda frame: (scope := frame.get("game", {}).get("native_guest_primary_scope", {}))
                                            and (scope.get("guid"), scope.get("kind")) == (guest_guid, kind), timeout=12)
    tracked = {"native_weapon_shot_played", "native_weapon_dry_fire_played"}
    before = [entry for entry in await resident_events(guest_run, guest_frame) if entry["event"] in tracked]
    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    assert await command(host, "host-press") == "pressed"
    host_click, guest_heard = await asyncio.gather(read_single_click(host, "stock-weapon"), read_single_click(guest, "observer"))
    assert math.dist(host_click, guest_heard) <= .03, (host_click, guest_heard)

    assert await command(guest, "face-away") == "turned"
    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    if rusty:
        assert await command(host, "retire-peer-projectile") == "retirement-armed"
    loaded_press = await submit(guest_run, "native-guest-primary-input-fixture")
    assert loaded_press["ok"], loaded_press
    capture = loaded_press["native_guest_primary_fixture"]
    shot = await input_result("native_guest_primary_input", capture["sequence"])
    assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (guest_guid, 1, 0, 1), shot
    if rusty:
        retirement = await command(host, "retirement-read")
        assert retirement == "retired", retirement
    expected_publication = None
    if memory_queue:
        expected_publication = {key: capture[key] for key in ("map", "pawn", "guid", "sequence")}
        expected_publication["lane"] = "memory"
        await wait_resident_status(guest_run, lambda frame:
            frame.get("game", {}).get("native_guest_primary_publication", {}).get("tap") == expected_publication,
            timeout=5)
    await wait_for(host, "peer-ready", f"{guest_guid},0,0")
    await wait_for(guest, "ready", f"{guest_guid},0,0")
    host_shot = tuple(map(int, (await command(host, "shot-read")).split(",")))
    assert host_shot == (1, 1, 1, 0), host_shot
    async with asyncio.timeout(6):
        while (attached_flash := int((await command(guest, "first-person-read")).split(";")[0])) != 1:
            assert attached_flash == 0, "The guest played a duplicate first-person flash"
            await asyncio.sleep(.05)
    after_round = [entry for entry in await resident_events(guest_run, guest_frame) if entry["event"] in tracked]

    for seat in (host, guest):
        assert await command(seat, "clear") == "clear"
    press = await submit(guest_run, "native-guest-primary-input-fixture")
    assert press["ok"] and press["native_guest_primary_fixture"]["sequence"] > 0, press
    host_heard, guest_click = await asyncio.gather(read_single_click(host, "observer"), read_single_click(guest, "observer"))
    assert math.dist(host_heard, guest_click) <= .03, (host_heard, guest_click)
    assert await command(host, "ready") == f"{host_guid},0,0" and await command(guest, "ready") == f"{guest_guid},0,0"
    assert await command(host, "peer-ready") == f"{guest_guid},0,0"

    since = await resident_events(guest_run, guest_frame)
    host_dry_events = [entry for entry in after_round if entry["event"] == "native_weapon_dry_fire_played" and entry not in before]
    guest_dry_events = [entry for entry in since if entry["event"] in tracked and entry not in after_round]
    assert len(host_dry_events) == 1 and [entry["event"] for entry in guest_dry_events] == ["native_weapon_dry_fire_played"], since
    events = host_dry_events + guest_dry_events
    assert len({entry["serial"] for entry in events}) == 2, events
    guest_input, = [entry for entry in since if entry["event"] == "native_guest_primary_input_rejected"
                    and entry.get("sequence") == press["native_guest_primary_fixture"]["sequence"]]
    assert guest_input["reason"] == "guest_weapon_preloaded_magazine_required", guest_input

    reserve_rounds = 16 if pursuit else 9
    reserve = await command(host, "seed-pursuit-reserve" if pursuit else "seed-reserve")
    assert re.fullmatch(rf"[0-9A-F]{{32}},{reserve_rounds}", reserve), reserve
    await wait_for(guest, "reserve", reserve)
    reloaded = await submit(guest_run, "native-guest-reload-input-fixture")
    assert reloaded["ok"] and reloaded["native_guest_reload_fixture"]["stock_wrappers"] == 2, reloaded
    reload = await input_result("native_guest_reload_input", reloaded["native_guest_reload_fixture"]["sequence"])
    async with asyncio.timeout(6):
        while True:
            _, reload_clip, position = (await command(guest, "first-person-read")).split(";")
            if reload_clip != "-" and float(position) > .03:
                break
            await asyncio.sleep(.05)
    owner_reloads = [row for row in await resident_events(guest_run, guest_frame)
                     if row["event"] == "native_guest_reload_view_played"]
    assert [row["clip"] for row in owner_reloads] == [reload_clip], owner_reloads
    loaded = 8 if rusty else 9
    reserve_left = f"{reserve.split(',')[0]},{reserve_rounds - loaded}" if rusty else ""
    assert (reload["guid"], reload["before"], reload["after"], reload["reserve_before"], reload["reserve_after"]) == (
        guest_guid, 0, loaded, reserve_rounds, reserve_rounds - loaded), reload
    await wait_for(host, "peer-ready", f"{guest_guid},{loaded},{loaded}")
    await wait_for(guest, "ready", f"{guest_guid},{loaded},{loaded}")
    await wait_for(guest, "reserve", reserve_left)
    assert await command(host, "peer-reserve") == reserve_left and await command(host, "reserve") == ""
    assert await command(host, "ready") == f"{host_guid},0,0"

    async def fire_at_drone():
        loaded_press = await submit(guest_run, "native-guest-primary-input-fixture")
        assert loaded_press["ok"], loaded_press
        result = await input_result("native_guest_primary_input", loaded_press["native_guest_primary_fixture"]["sequence"])
        assert (result["guid"], result["before"], result["after"], result["projectiles"]) == (
            guest_guid, loaded, loaded - 1, 1), result
        await wait_for(host, "peer-ready", f"{guest_guid},{loaded - 1},{loaded - 1}")
        await wait_for(guest, "ready", f"{guest_guid},{loaded - 1},{loaded - 1}")
        return result

    combat = await verify_resident_npc_override(host, guest, fire_guest=fire_at_drone) if guest_combat else {}
    if memory_queue:
        for filename in ("native-guest-primary-events", "native-guest-held-fire-events"):
            assert not (guest.bridge.directory / filename).exists(), filename
    return {**combat, "weapon_kind": kind, "host_stock_click": host_click, "guest_heard_host": guest_heard, "guest_primary_input": press,
            "guest_primary_publication": expected_publication,
            "host_heard_guest": host_heard, "guest_click": guest_click, "guest_cue_receipts": events,
            "guest_live_round": shot, "guest_projectile_retired_during_birth": rusty,
            "host_guest_shot_dispatch": {"projectile_calls": host_shot[0],
                "shot_sounds": host_shot[1], "muzzle_flashes": host_shot[2]},
            "guest_empty_magazine_receipt": guest_input,
            "pistols_still_empty_after_dry_fire": True, "projectiles_spawned": 0, "observers_played_shot_effects": 0,
            "guest_reload_input": reloaded, "guest_reload": reload, "reserve_before": reserve, "reserve_after": reserve_left,
            "guest_muzzle_attachments": attached_flash,
            "guest_first_person_reload": {"stock_clip": reload_clip, "playhead_seconds": float(position)},
            "both_games_projected_reloaded_magazine": True, "host_magazine_and_reserve_unchanged": True}


async def verify_resident_npc_shots(host: Seat, guest_run: Path, guest_frame: dict) -> list:
    def shots(events):
        return {row["serial"]: row for row in events
                if row["event"] == "native_weapon_shot_played" and row["generation"] == guest_frame["generation"]}

    evidence = []
    for kind in ("state", "bandit"):
        previous = shots(await resident_events(guest_run, guest_frame))
        raw = await portable_probe_command(host.bridge, "portable-npc-override", f"fire-npc\t{kind}")
        observed_faction, path, *position = raw.split(";")
        faction = int(observed_faction)
        assert 0 <= faction < 15 and (faction == 4) == (kind == "state"), raw
        assert native_weapon_fire_view.stock_weapon_class(path), raw
        try:
            async with asyncio.timeout(6):
                while True:
                    recent = shots(await resident_events(guest_run, guest_frame))
                    matching = [row for serial, row in recent.items() if serial not in previous and row["weapon"] == path]
                    if matching:
                        break
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"No observer cue for stock {kind} faction shot from {raw}") from error
        cue, = matching
        assert cue["stock_sound"] and not cue["recoil"], cue
        evidence.append({"faction": faction, "weapon": path, "host_muzzle": list(map(float, position)), "guest": cue})
    return evidence


async def verify_resident_npc_kill(host: Seat, guest: Seat) -> dict:
    async def sample(seat):
        return await portable_probe_command(seat.bridge, "portable-npc-override", "npc-state")

    async with asyncio.timeout(8):
        while (guest_alive := await sample(guest)) == "absent":
            await asyncio.sleep(.05)
    host_alive = await sample(host)
    assert all(row.startswith("true;false;0;") for row in (host_alive, guest_alive)), (host_alive, guest_alive)
    target = await portable_probe_command(host.bridge, "portable-npc-override", "npc-kill")
    result = None
    try:
        async with asyncio.timeout(8):
            while True:
                result = await asyncio.gather(*(sample(seat) for seat in (host, guest)))
                if all(row.startswith("false;true;1;") for row in result):
                    break
                await asyncio.sleep(.05)
    except TimeoutError as error:
        raise AssertionError(f"Stock rifle hit did not resolve on both seats for {target}: {result}") from error
    assert result[0] == f"false;true;1;state;{target}", result
    assert (result[0].split(";")[-1], result[1].split(";")[-1]) == (
        host_alive.split(";")[-1], guest_alive.split(";")[-1]), result
    return {"before": [host_alive, guest_alive], "after": result, "host_recorded_npc_shooter": True}

async def verify_resident_turret_combat(host: Seat, guest: Seat, guest_frame: dict) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    assert await command(guest, "turret-state") == "absent"
    replication = await command(host, "turret-spawn")
    assert replication == "true", replication
    async with asyncio.timeout(8):
        while (guest_before := await command(guest, "turret-state")) == "absent":
            await asyncio.sleep(.05)
    before = [await command(host, "turret-state"), guest_before]
    assert before == ["true,false,false,false"] * 2, before
    guest_fire = await command(guest, "turret-fire")
    assert guest_fire == "0,1,true", guest_fire
    host_fire = await command(host, "turret-retarget")
    assert host_fire == "1,1,true", host_fire
    async with asyncio.timeout(6):
        while True:
            events = await resident_events(guest.client / "run", guest_frame)
            shots = [row for row in events if row["event"] == "native_weapon_shot_played"
                     and row["generation"] == guest_frame["generation"]
                     and row["weapon"].endswith("/Turret_MechGun_01_BP.Turret_MechGun_01_BP_C")]
            if shots:
                break
            await asyncio.sleep(.05)
    shot, = shots
    assert shot["stock_sound"] and not shot["recoil"], shot
    assert await asyncio.gather(*(command(seat, "turret-arm-effects") for seat in (host, guest))) == ["armed"] * 2
    await command(host, "turret-kill")
    after = None
    try:
        async with asyncio.timeout(8):
            while True:
                after = await asyncio.gather(*(command(seat, "turret-state") for seat in (host, guest)))
                if after == ["false,true,true,true"] * 2:
                    break
                await asyncio.sleep(.05)
    except TimeoutError as error:
        raise AssertionError(f"Expected both turrets to be dead and visibly burnt: {after}") from error
    await asyncio.sleep(1.2)
    death_effects = await asyncio.gather(*(command(seat, "turret-read-effects") for seat in (host, guest)))
    # Stock fire damage comes from a host-only gameplay actor. Guest smoke and blast are cosmetic.
    assert [row.split(";")[0] for row in death_effects] == ["1,1,1,1", "1,1,1,0"], death_effects
    assert all(abs(float(row.split(";")[1])) > 25 for row in death_effects), death_effects
    assert death_effects[1].split(";")[-1] == "1,1,1", death_effects
    assert await command(host, "turret-extinguish") == "extinguished"
    finished = None
    try:
        async with asyncio.timeout(5):
            while True:
                finished = (await command(guest, "turret-read-effects")).split(";")
                if finished[1] == "22.50" and finished[-1] == "1,1,0":
                    break
                await asyncio.sleep(.1)
    except TimeoutError as error:
        raise AssertionError(f"The host extinguished its fire but the guest fire view stayed active: {finished}") from error
    assert finished[0] == "1,1,1,0", finished
    return {"stock_replication": replication == "true", "before": before, "after": after,
            "replica_fire_denied": True, "host_skipped_downed_target": True, "guest_stock_fire_cue": shot,
            "death_blast_sound_smoke_and_fire_fields": death_effects, "guest_fire_extinguished": finished}

async def verify_stock_npc_rivalry(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def ask(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    async def sample(seat):
        return [part.split(",") for part in (await ask(seat, "rivalry-state")).split(";")]

    guest_run = guest.client / "run"
    frame = await wait_resident_status(guest_run, lambda status: status.get("state") == "connected")
    assert await ask(guest, "rivalry-watch") == "watching"
    began = time.time()
    baseline = await ask(host, "rivalry-start")
    snapshots, target_seen, weapons = None, False, set()
    try:
        async with asyncio.timeout(8):
            while True:
                ready = await sample(guest)
                if len(ready) == 2 and all(row[:3] == ["true", "false", "0"] for row in ready):
                    break
                await asyncio.sleep(.15)
        assert await ask(host, "rivalry-initialize") == "initialized"
        try:
            async with asyncio.timeout(24):
                while True:
                    source, observer = await asyncio.gather(sample(host), sample(guest))
                    snapshots = source, observer
                    if len(source) != 2 or len(observer) != 2 or any(len(row) != 10 for row in source + observer):
                        await asyncio.sleep(.15)
                        continue
                    target_seen |= any(row[4] == "true" for row in source)
                    weapons.update(row[7] for row in source if row[7] != "-")
                    killed = [i for i, row in enumerate(source) if row[:3] == ["false", "true", "1"]
                              and int(row[5]) > 0 and row[6] == "true"]
                    if target_seen and killed and all(observer[i][:3] == ["false", "true", "1"] for i in killed):
                        break
                    await asyncio.sleep(.15)
        except TimeoutError as error:
            raise AssertionError(f"Stock NPC rivalry did not resolve: {baseline}, targets={target_seen}, "
                                 f"last={snapshots}") from error
        shots = [row for row in await resident_events(guest_run, frame) if row["time_unix"] >= began
                 and row["event"] == "native_weapon_shot_played" and row["weapon"] in weapons and row["stock_sound"]]
        assert shots, (weapons, snapshots)
        return {"stock_npc_rivalry": {"stock_max_health_and_factions": baseline, "stock_target_seen": target_seen,
                "killed": [["State Conscript", "Bandit"][i] for i in killed], "host_and_guest": snapshots,
                "remote_fire_cues": len(shots)}}
    finally:
        assert await ask(host, "rivalry-cleanup") == "cleared"


async def equip_stock_guest_melee(host: Seat, guest: Seat, seed: str, kind: str) -> dict:
    guid = await portable_probe_command(host.bridge, "portable-npc-override", seed)
    assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
    async with asyncio.timeout(8):
        while await portable_probe_command(guest.bridge, "portable-npc-override", f"select-pipe\t{guid}") != "selected":
            await asyncio.sleep(.05)
    return await wait_resident_status(guest.client / "run", lambda current: (
        (scope := current.get("game", {}).get("native_guest_primary_scope", {})).get("guid") == guid
        and scope.get("kind") == kind), timeout=12)


async def verify_resident_guest_melee(host: Seat, guest: Seat, *, heavy: bool = False,
                                      verify_observer: bool = False) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    guest_run = guest.client / "run"
    frame = await equip_stock_guest_melee(
        host, guest, "seed-pipe\theavy" if heavy else "seed-pipe", "melee" if heavy else "old-pipe")
    host_run = host.campaign / "run"
    grip_error = None
    if verify_observer:
        host_frame = await current_status(host_run)
        actor = host_frame["game"]["native_guest_save"]["pawn"]

        def observer(state):
            return state.get("game", {}).get("native_avatar_melee", {}).get(actor, {})

        grip = await command(guest, "melee-grip")
        async with asyncio.timeout(5):
            while (report := await command(host, f"melee-grip\t{grip}")) == "waiting":
                await asyncio.sleep(.1)
        grip_error = float(report)
        assert 0 <= grip_error <= 5, (grip, grip_error)
    misses = []
    # Retry a verified miss from a different stock contact lane.
    for attempt in range(1, 4 if verify_observer else 2):
        if verify_observer:
            prior_serial = observer(await current_status(host_run)).get("serial", 0)
        center, name = (await command(host, f"stage-melee\t{attempt}")).split(";", 1)
        async with asyncio.timeout(6):
            while not (await command(guest, f"melee-state\t{center}")).startswith("true;false;"):
                await asyncio.sleep(.05)
        try:
            assert await command(guest, f"face-melee\t{center}") == "ready"
            await asyncio.sleep(.25)
            assert await command(host, f"align-melee\t{center}") == "true"
            host_view, guest_view = await asyncio.gather(*(command(seat, "melee-view") for seat in (host, guest)))
            before = await command(host, f"melee-state\t{center}")
            assert before == f"true;false;7.000;{name}", before
            tap = await submit(guest_run, "native-guest-primary-input-fixture")
            assert tap["ok"], tap
            sequence = tap["native_guest_primary_fixture"]["sequence"]
            async with asyncio.timeout(8):
                while True:
                    events = await resident_events(guest_run, frame)
                    event = next((row for row in events if row.get("sequence") == sequence
                                  and row["event"] in {"native_guest_melee_input", "native_guest_melee_input_rejected"}), None)
                    if event:
                        break
                    await asyncio.sleep(.05)
        finally:
            cleared = await asyncio.gather(*(command(seat, "clear-melee-aim") for seat in (host, guest)),
                                           return_exceptions=True)
        assert cleared == ["cleared", "cleared"], cleared
        assert event["event"] == "native_guest_melee_input", event
        proof = event[native_guest_melee.SWING_KEY]
        contact = await command(host, "melee-contact")
        after = await command(host, f"melee-state\t{center}")
        geometry = {"host_before": host_view, "guest_before": guest_view,
                    "host_contact": await command(host, "melee-contact-view")}
        result = {"stock_input": tap, "host_damage": proof, "contact_geometry": geometry}
        if verify_observer:
            assert proof["pawn"] == actor
            try:
                async with asyncio.timeout(5):
                    while True:
                        host_frame = await current_status(host_run)
                        pose = observer(host_frame)
                        if pose.get("serial", 0) > prior_serial and not pose["active"] and pose["weapon_hand_motion_cm"] > 10:
                            result["host_observer_swing"] = pose
                            break
                        await asyncio.sleep(.15)
            except TimeoutError as error:
                avatar = host_frame["game"].get("native_player_avatars", {}).get(actor)
                raise AssertionError(f"Guest swing did not animate for host: {observer(host_frame)}, {avatar}, {proof}") from error
        if not (native_guest_melee.verified_miss(proof) and after == before and contact == "sphere_empty"):
            break
        misses.append({"stock_input": tap, "host_receipt": proof, "contact": contact,
                       "fixture_target_position": center, "contact_geometry": geometry})
    assert proof["target"] == name and proof["enemy_health_after"] <= 0 and native_guest_melee.verified_damage(proof), (
        proof, contact, before, after, result.get("host_observer_swing"), misses, geometry)
    if misses:
        result["fixture_misses"] = misses
    if grip_error is not None:
        result["observer_grip_error_cm"] = grip_error
    async with asyncio.timeout(8):
        while True:
            states = await asyncio.gather(*(command(seat, f"melee-state\t{center}") for seat in (host, guest)))
            if all(state.startswith("false;true;") for state in states):
                break
            await asyncio.sleep(.05)
    result["both_peers_dead_and_ragdolled"] = states
    return result


async def stock_melee_edge(guest_run, guid, edge):
    reply = await submit(guest_run, native_guest_melee.INPUT_FIXTURE, target=edge)
    assert reply["ok"], reply
    fixture = reply[native_guest_melee.INPUT_FIXTURE_KEY]
    assert fixture["guid"] == guid and fixture["edge"] == ("release" if edge == "tap" else edge), fixture
    return dict(fixture, requested_edge=edge)


async def stock_melee_edge_receipts(guest_run, guest_frame, *fixtures):
    sequences = {fixture["sequence"] for fixture in fixtures}
    async with asyncio.timeout(6):
        while True:
            rows = {event.get("sequence"): event for event in await resident_events(guest_run, guest_frame)
                    if event["event"] in ("native_guest_melee_edge", "native_guest_melee_edge_rejected")
                    and event.get("sequence") in sequences}
            if sequences <= rows.keys():
                break
            await asyncio.sleep(.05)
    for fixture in fixtures:
        event = rows[fixture["sequence"]]
        assert event["event"] == "native_guest_melee_edge" and event["edge"] == fixture["requested_edge"], event
    return [rows[fixture["sequence"]][native_guest_melee.INPUT_KEY] for fixture in fixtures]


async def stock_melee_terminal(host_run, ack):
    key = native_guest_melee.INPUT_KEY
    frame = await wait_resident_status(host_run, lambda value: (
        (proof := value.get("game", {}).get(key, {})).get("serial") == ack["serial"]
        and proof.get("status") != "pending"), timeout=8)
    proof = frame["game"][key]
    assert proof["status"] == "settled" and proof["guid"] == ack["guid"], proof
    return proof


async def stock_melee_charge(host_run, guest_run, guest_frame, guid, *, before_release=None, hold=.95):
    press, = await stock_melee_edge_receipts(guest_run, guest_frame, await stock_melee_edge(guest_run, guid, "press"))
    assert (press["presses"], press["releases"]) == (1, 0), press
    await asyncio.sleep(hold)
    if before_release:
        await before_release()
    release, = await stock_melee_edge_receipts(guest_run, guest_frame, await stock_melee_edge(guest_run, guid, "release"))
    assert release["serial"] == press["serial"], (press, release)
    charged = await stock_melee_terminal(host_run, release)
    assert (charged["presses"], charged["releases"], charged["contacts"], charged["charged"]) == (1, 1, 1, 1), charged
    return charged


async def verify_stock_melee_edges(host: Seat, guest: Seat, *, observe=None) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    guest_frame = await current_status(guest_run)
    scope = guest_frame["game"]["native_guest_primary_scope"]
    assert scope["kind"] == "old-pipe", scope
    guid = scope["guid"]
    tap, = await stock_melee_edge_receipts(guest_run, guest_frame, await stock_melee_edge(guest_run, guid, "tap"))
    tap = await stock_melee_terminal(host_run, tap)
    assert (tap["presses"], tap["releases"], tap["contacts"], tap["charged"], tap["combo"]) == (1, 1, 1, 0, 0), tap
    if observe:
        assert int(await observe("melee-local-read")) == 1, "The first swing did not reach the guest's stock animator"
        repeat, = await stock_melee_edge_receipts(guest_run, guest_frame, await stock_melee_edge(guest_run, guid, "tap"))
        repeat = await stock_melee_terminal(host_run, repeat)
        assert repeat["serial"] != tap["serial"] and repeat["contacts"] == 1, repeat
        assert int(await observe("melee-local-read")) == 2, "The repeat swing did not reach the same stock animator"
    charged = await stock_melee_charge(host_run, guest_run, guest_frame, guid)

    attempts = []
    for spacing in (.24, .12, .36, .48, .60):
        first = await stock_melee_edge(guest_run, guid, "tap")
        await asyncio.sleep(spacing)
        second = await stock_melee_edge(guest_run, guid, "tap")
        one, two = await stock_melee_edge_receipts(guest_run, guest_frame, first, second)
        combo = await stock_melee_terminal(host_run, two)
        if (one["serial"] == two["serial"] and
                (combo["presses"], combo["releases"], combo["contacts"], combo["combo"]) == (2, 2, 2, 1)):
            return {"tap": tap, "charged": charged, "combo": combo, "spacing_seconds": spacing}
        attempts.append((spacing, one, two, combo))
    raise AssertionError(f"The original stock combo did not buffer a second attack: {attempts}")


async def verify_stock_melee_family(host: Seat, guest: Seat, _pawn: str) -> dict:
    before = campaign_save_hashes(host.campaign)
    heavy_pipe = await verify_resident_guest_melee(host, guest, heavy=True, verify_observer=True)
    assert await portable_probe_command(host.bridge, "portable-npc-override", "clear-melee-target") == "cleared"
    old_pipe = await verify_resident_guest_melee(host, guest, verify_observer=True)
    assert heavy_pipe["host_damage"]["stock_damage"] > old_pipe["host_damage"]["stock_damage"]
    assert await portable_probe_command(host.bridge, "portable-npc-override", "clear-melee-target") == "cleared"
    edges = await verify_stock_melee_edges(host, guest)
    assert campaign_save_hashes(host.campaign) == before, "A regular melee attack wrote campaign saves"
    return {"stock_melee_family": {"heavy_pipe": heavy_pipe, "old_pipe": old_pipe, "edges": edges, "campaign_unchanged": True}}


async def verify_stock_melee_edges_only(host: Seat, guest: Seat, pawn: str) -> dict:
    before = campaign_save_hashes(host.campaign)
    frame = await equip_stock_guest_melee(host, guest, "seed-melee-edges", "old-pipe")
    guid = frame["game"]["native_guest_primary_scope"]["guid"]
    async def observe(action):
        return await portable_probe_command(guest.bridge, "portable-npc-override", action)

    assert await observe("melee-local-arm") == "watching"
    edges = await verify_stock_melee_edges(host, guest, observe=observe)
    for edge in ("tap", "charged", "combo"):
        assert (edges[edge]["pawn"], edges[edge]["guid"]) == (pawn, guid), edges[edge]
    first_person_contacts = int(await observe("melee-local-read"))
    assert first_person_contacts >= 5, (first_person_contacts, edges)
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    async def prop_state():
        return list(map(float, (await command(host, "melee-prop-state")).split(";")))

    center, target = (await command(host, "stage-melee-prop")).split(";", 1)
    health, speed, *position = await prop_state()
    assert health == 250 and speed < 3, (health, speed, position)
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    try:
        assert await command(guest, f"face-melee\t{center}") == "ready"
        assert await command(host, f"align-melee\t{center}") == "true"
        started = time.time()
        ack, = await stock_melee_edge_receipts(guest_run, frame, await stock_melee_edge(guest_run, guid, "tap"))
        impact = await stock_melee_terminal(host_run, ack)
    finally:
        await asyncio.gather(*(command(seat, "clear-melee-aim") for seat in (host, guest)))
    after_health, after_speed, *after_position = await prop_state()
    assert impact["contacts"] == impact["hits"] == 1 and after_health < health, (impact, target, after_health)
    travel = math.dist(position, after_position)
    assert after_speed > 3 or travel > 3, (impact, after_health, after_speed, travel)
    async with asyncio.timeout(5):
        while True:
            sounds = [event for event in await resident_events(guest_run, frame)
                      if event["time_unix"] >= started and event["event"] == "native_melee_impact_played"]
            if sounds:
                break
            await asyncio.sleep(.08)
    prop = {"target": target, "health_before": health, "health_after": after_health, "velocity_cm_s": after_speed,
            "movement_cm": travel, "guest_sounds": sounds}
    assert campaign_save_hashes(host.campaign) == before, "A regular melee attack wrote campaign saves"
    return {"stock_melee_edges": {**edges, "first_person_contacts": first_person_contacts, "prop_impact": prop,
                                  "campaign_unchanged": True}}


async def verify_tutorial_dock_retaliation(host: Seat, guest: Seat, frame: dict) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    guest_run = guest.client / "run"

    scope = frame["game"]["native_guest_primary_scope"]
    rejected = None
    assert await command(guest, "dock-block-weapon") == "true"
    try:
        # Include a service renewal while the stock action gate is closed.
        await asyncio.sleep(1.1)
        locked = await wait_resident_status(guest_run, lambda snapshot:
            snapshot.get("game", {}).get("native_guest_primary_scope") == scope, timeout=2)
        rejected = await submit(guest_run, "native-guest-primary-input-fixture")
        assert rejected.get("ok") is False and "temporarily blocked" in rejected["error"], rejected
        assert locked["game"]["native_guest_primary_scope"] == scope
    finally:
        assert await command(guest, "dock-unblock-weapon") == "false"

    center, seat_point = (await command(host, "dock-natural-stage")).split(";")
    try:
        baseline = await command(host, "dock-natural-sample")
        assert baseline.split(";")[4] == "-", baseline
        expected_sound = await command(host, "dock-natural-impact")
        origin = tuple(map(float, center.split(",")))
        seat_origin = tuple(map(float, seat_point.split(",")))
        async with asyncio.timeout(6):
            while True:
                guest_point = await command(guest, "dock-natural-position")
                local_point = await command(guest, "dock-natural-seat")
                if (guest_point != "absent" and math.dist(origin, tuple(map(float, guest_point.split(",")))) < 350
                        and math.dist(seat_origin, tuple(map(float, local_point.split(",")))) < 100):
                    break
                await asyncio.sleep(.05)
        assert await command(guest, f"dock-aim\t{guest_point}") == "ready"
        await asyncio.sleep(.15)
        target = await command(host, "dock-natural-position")
        assert await command(host, f"dock-aim\t{target}") == "ready"
        async def shoot_guest(remaining):
            started = time.time()
            tap = await submit(guest_run, "native-guest-primary-input-fixture")
            assert tap["ok"], tap
            sequence = tap["native_guest_primary_fixture"]["sequence"]
            async with asyncio.timeout(8):
                while True:
                    shot = next((row for row in await resident_events(guest_run, frame)
                                 if row.get("sequence") == sequence and row["time_unix"] >= started
                                 and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")), None)
                    if shot:
                        break
                    await asyncio.sleep(.05)
            assert shot["event"] == "native_guest_primary_input", shot
            proof = shot["native_guest_weapon"]
            assert (proof["before"], proof["after"], proof["projectiles"]) == (remaining, remaining - 1, 1), proof
            return proof

        began = time.time()
        info = await shoot_guest(7)
        current = baseline
        try:
            async with asyncio.timeout(15):
                while True:
                    current = await command(host, "dock-natural-sample")
                    alive, hit, initial, health, target, guest_hits, host_hits, guest_before, guest_after = current.split(";")
                    if (alive, hit, target, host_hits) == ("true", "true", "guest", "0") and int(guest_hits) > 0:
                        assert float(health) < float(initial) and float(guest_after) < float(guest_before), current
                        break
                    await asyncio.sleep(.08)
        except (TimeoutError, RuntimeError) as error:
            raise AssertionError(f"Stock NPC did not retaliate against its guest attacker: {baseline} to {current}") from error
        local_position = tuple(map(float, (await command(guest, "dock-natural-seat")).split(",")))
        async with asyncio.timeout(4):
            while True:
                impacts = [row for row in await resident_events(guest_run, frame)
                           if row["event"] == "native_npc_impact_played" and row["time_unix"] >= began
                           and row["sound"] == expected_sound and math.dist(row["position"], local_position) < 350]
                if impacts:
                    break
                await asyncio.sleep(.05)
        retaliation = current
        fight = [{"shot": info, "stock_result": current}]
        for remaining in range(info["after"], 0, -1):
            if current.startswith("false;"):
                break
            # Give the shooter another clear lane while the stock worker keeps moving and attacking.
            destination = await command(host, "dock-natural-retreat")
            retreat = tuple(map(float, destination.split(",")))
            assert await command(guest, f"dock-natural-arrive\t{destination}") == "ready"
            local_point = None
            try:
                async with asyncio.timeout(6):
                    retry_at = time.monotonic() + .4
                    retried = False
                    while True:
                        local_point = await command(guest, "dock-natural-seat")
                        if math.dist(retreat, tuple(map(float, local_point.split(",")))) < 80:
                            break
                        if not retried and time.monotonic() >= retry_at:
                            assert await command(guest, f"dock-natural-arrive\t{destination}") == "ready"
                            retried = True
                        await asyncio.sleep(.05)
            except TimeoutError as error:
                raise AssertionError(f"The guest did not reach the warehouse retreat {retreat} from {local_point}. "
                                     f"Last stock fight sample was {current}") from error
            for seat in (guest, host):
                target = await command(seat, "dock-natural-position")
                assert target != "absent" and await command(seat, f"dock-aim\t{target}") == "ready"
            result = await shoot_guest(remaining)
            before_health = float(current.split(";")[3])
            deadline = time.monotonic() + 1.2
            while True:
                current = await command(host, "dock-natural-sample")
                health = float(current.split(";")[3])
                if health < before_health or current.startswith("false;") or time.monotonic() >= deadline:
                    break
                await asyncio.sleep(.05)
            fight.append({"shot": result, "stock_result": current})

        assert current.startswith("false;true;") and any(
            float(entry["stock_result"].split(";")[3]) < float(retaliation.split(";")[3]) for entry in fight[1:]
        ), ("The stock-health warehouse worker survived the guest's magazine", fight)
        async with asyncio.timeout(10):
            while True:
                bodies = await asyncio.gather(*(command(seat, "dock-natural-state") for seat in (host, guest)))
                if all(body.startswith("false;true;1;") for body in bodies):
                    break
                await asyncio.sleep(.05)
        return {"temporary_weapon_block": {"scope": scope, "rejected": rejected},
                "baseline": baseline, "shot": info, "stock_target_and_hit": retaliation, "guest_impact_sound": impacts[0],
                "stock_health_fight": fight, "both_peers_dead_and_ragdolled": bodies}
    finally:
        assert await command(host, "dock-natural-stop") == "stopped"


async def verify_tutorial_bandage_revisit(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
    observatory = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
    bandage = f"{dock}.Tut_Dock_P:PersistentLevel.Item_Bandage_C_1"
    second_bandage = f"{dock}.Tut_Dock_P:PersistentLevel.Item_Bandage_C_2"
    pistol = f"{dock}.Tut_Dock_P:PersistentLevel.Wep_RustyPistol_C_1"

    def bound(frame, area):
        game = frame.get("game", {})
        return (game.get("map") == area and not frame.get("awaiting_zone_guest")
                and game.get("native_guest_save", {}).get("state") == "bound"
                and game.get("native_zone", {}).get("state") == "ready")

    async def travel(source, target, exit_actor, guest_pawn=None):
        prior = await wait_resident_status(host_run, lambda frame: bound(frame, source))
        participant = guest_pawn or prior["game"]["native_guest_save"]["pawn"]
        for seat, placement in ((participant, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", seat, placement, "Obs_Dock", exit_actor)
        arrival = await wait_resident_status(host_run, lambda frame: bound(frame, target)
                                             and frame.get("zone_epoch") != prior["zone_epoch"], timeout=60)
        await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
                                   and frame.get("game", {}).get("map") == target
                                   and frame.get("zone_epoch") == arrival["zone_epoch"], timeout=35)
        return arrival

    async def visibility(seat, actor):
        leaf = actor.rsplit(".", 1)[-1]
        report = await seat.bridge.command("fixture-loose-revisit-visibility", leaf, update_status=False)
        return report["ack"]["message"]

    first = await wait_resident_status(host_run, lambda frame: bound(frame, observatory) or bound(frame, dock))
    if first["game"]["map"] == observatory:
        first = await travel(observatory, dock, "Level_Exit_BP_C_213", pawn)
    for actor in (bandage, second_bandage, pistol):
        assert await visibility(host, actor) == await visibility(guest, actor) == "visible", actor
    await host.bridge.command(native_loose_pickup.APPROACH, bandage, first["game"]["pawns"]["local"]["name"])
    claimed = await submit(host_run, native_loose_pickup.CLAIM, target=f"loose:{bandage}")
    assert claimed["ok"] and claimed[native_loose_pickup.PROOF]["mode"] == 0, claimed
    await wait_resident_status(guest_run, lambda frame: bandage in frame.get("gameplay", {}).get(native_loose_pickup.STATE, []))
    await host.bridge.command(native_loose_pickup.APPROACH, pistol, first["game"]["native_guest_save"]["pawn"])
    await guest.bridge.wait_for(lambda game: game.get(native_loose_pickup.KEY, {}).get(pistol, {}).get("state") == "available")
    await guest.bridge.command(native_loose_pickup.FIXTURE, pistol)
    pistol_state = await wait_resident_status(guest_run, lambda frame:
        pistol in frame.get("gameplay", {}).get(native_loose_pickup.STATE, []))
    pistol_events = [row for row in await resident_events(guest_run, pistol_state)
                     if row.get(native_loose_pickup.PROOF, {}).get("actor") == pistol]
    [pistol_claim] = [row[native_loose_pickup.PROOF] for row in pistol_events]
    assert pistol_claim["mode"] == 0 and pistol_claim["owner"] == pistol_state["character_id"], pistol_claim
    await travel(dock, observatory, "Level_Exit_BP_C_1")
    revisit = await travel(observatory, dock, "Level_Exit_BP_C_213")
    await host.bridge.wait_for(lambda game: game.get(native_loose_pickup.CHECK_KEY, {}).get(bandage) is False)
    def settled(frame):
        hidden = frame.get("gameplay", {}).get(native_loose_pickup.STATE, [])
        return frame.get("zone_epoch") == revisit["zone_epoch"] and bandage in hidden and pistol not in hidden

    await wait_resident_status(guest_run, settled, timeout=10)
    host_visibility = await visibility(host, bandage)
    assert host_visibility in ("absent", "hidden"), host_visibility
    async with asyncio.timeout(8):
        while (guest_visibility := await visibility(guest, bandage)) == "visible":
            await asyncio.sleep(.05)
    assert guest_visibility in ("absent", "hidden"), guest_visibility
    pistol_visibility = await asyncio.gather(visibility(host, pistol), visibility(guest, pistol))
    assert pistol_visibility == ["visible", "visible"], dict(actor=pistol, visibility=pistol_visibility)
    host_suppressed = await visibility(host, second_bandage)
    assert host_suppressed in ("absent", "hidden"), host_suppressed
    respawn_hidden = (host.bridge.snapshot() or {}).get(native_loose_pickup.RESPAWN_HIDDEN, [])
    assert second_bandage in respawn_hidden, dict(unclaimed=second_bandage, host_respawn_hidden=respawn_hidden)
    await wait_resident_status(guest_run, lambda frame: settled(frame) and
        second_bandage in frame["gameplay"][native_loose_pickup.STATE], timeout=8)
    async with asyncio.timeout(8):
        while (guest_suppressed := await visibility(guest, second_bandage)) == "visible":
            await asyncio.sleep(.05)
    assert guest_suppressed in ("absent", "hidden"), guest_suppressed
    return {"stock_bandage_revisit": {"actor": bandage, "claim": claimed[native_loose_pickup.PROOF],
            "first_visit": first["zone_epoch"], "revisit": revisit["zone_epoch"],
            "host_visibility": host_visibility, "guest_visibility": guest_visibility,
            "guest_owned_respawn": {"actor": pistol, "claim": pistol_claim, "visibility": pistol_visibility},
            "host_owned_unclaimed_source": {"actor": second_bandage, "visibility": [host_suppressed, guest_suppressed]}}}


WAREHOUSE_DEVICES = {
    device: f"{kind} {WAREHOUSE_DOCK}.Tut_Dock_P:PersistentLevel.{kind}_{number}"
    for device, kind, number in (("switch", "CircutBox_BP_C", 1), ("radio", "Prop_Radio01_C", 3))
}


def warehouse_device_states(frame):
    switches = frame.get("game", {}).get(native_stock_switch.KEY, {})
    return {device: switches[name] for device, name in WAREHOUSE_DEVICES.items() if name in switches}


async def prepare_warehouse_late_join(host):
    host_run = host.campaign / "run"
    await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    for placement in ("near", "inside"):
        await host.bridge.command("native-exit-position", "local", placement, "Obs_Dock", "Level_Exit_BP_C_213")
    arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == WAREHOUSE_DOCK
                                        and warehouse_device_states(frame).keys() == WAREHOUSE_DEVICES.keys(), timeout=60)
    assert not arrival.get("awaiting_zone_guest")
    assert not any(lease["state"] == "active" for lease in arrival["campaign"]["leases"]), arrival["campaign"]["leases"]
    initial = warehouse_device_states(arrival)
    checkpoint = campaign_save_hashes(host.campaign)
    for device in WAREHOUSE_DEVICES:
        await host.bridge.command(f"fixture-warehouse-{device}", "host")
    toggled = warehouse_device_states(await wait_resident_status(host_run, lambda frame:
        (current := warehouse_device_states(frame)).keys() == initial.keys()
        and all(current[device]["powered"] != initial[device]["powered"] for device in initial)
        and current["switch"]["angles"] != initial["switch"]["angles"]
        and current["switch"]["lights"] != initial["switch"]["lights"]
        and current["switch"]["panel"] != initial["switch"]["panel"]))
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"zone_epoch": arrival["zone_epoch"], "initial": initial, "toggled": toggled}


async def verify_warehouse_late_join(host, guest, baseline):
    assert baseline
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    expected = baseline["toggled"]
    host_frame, guest_frame = await asyncio.gather(*(wait_resident_status(run, lambda frame:
        frame.get("game", {}).get("map") == WAREHOUSE_DOCK and frame.get("zone_epoch") == baseline["zone_epoch"]
        and warehouse_device_states(frame) == expected) for run in (host_run, guest_run)))
    return {"stock_warehouse_late_join": {**baseline, "host": warehouse_device_states(host_frame),
                                          "guest_first_visit": warehouse_device_states(guest_frame)}}


async def verify_observatory_tv(host: Seat, guest: Seat, pawn: str) -> dict:
    fixture = "fixture-observatory-tv"
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    actor = (await host.bridge.command(fixture, "discover"))["ack"]["message"]

    def powered(frame):
        return frame.get("game", {}).get(native_stock_switch.KEY, {}).get(actor, {}).get("powered")

    async def views(expected, *, require_screen=True):
        for seat, run in ((host, host_run), (guest, guest_run)):
            await wait_resident_status(run, lambda frame: powered(frame) is expected)
        end = time.monotonic() + 5
        readings = []
        while time.monotonic() < end:
            rows = await asyncio.gather(*(seat.bridge.command(fixture, "screen", "-", actor, update_status=False)
                                          for seat in (host, guest)))
            readings = [row["ack"]["message"] for row in rows]
            if all(reading.startswith(f"{int(expected)}|")
                   and (not require_screen or ("M_Screen_Off" not in reading) == expected)
                   for reading in readings):
                return readings
            await asyncio.sleep(.08)
        raise AssertionError(f"Expected the television power and screen to agree on both seats: {expected}, {readings}")

    initial = powered(await wait_resident_status(host_run, lambda frame: type(powered(frame)) is bool))
    before = await views(initial, require_screen=False)
    await host.bridge.command(fixture, "host", "-", actor)
    host_toggle = await views(not initial, require_screen=initial)
    moved = await host.bridge.command(fixture, "stage", pawn, actor)
    point = list(map(float, moved["ack"]["message"].split(",")))
    frame = await wait_resident_status(guest_run, lambda row:
        math.dist(row.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0] * 3), point) < 70)
    started = time.time()
    await guest.bridge.command(fixture, "guest", "-", actor)
    guest_toggle = await views(initial, require_screen=not initial)
    receipt, = [row for row in await resident_events(guest_run, frame) if row["time_unix"] >= started
                and row.get("native_stock_switch_interaction", {}).get("actor") == actor]
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_observatory_tv": {"actor": actor, "initial_screens": before, "host_toggle_screens": host_toggle,
                                     "guest_toggle_screens": guest_toggle, "guest_receipt_ms": receipt["elapsed_ms"]}}


async def verify_warehouse_candle(host: Seat, guest: Seat, _pawn: str) -> dict:
    fixture = "fixture-warehouse-candle"
    kind = "Prop_Candle_01_C"
    actor = f"{kind} {WAREHOUSE_DOCK}.Tut_Dock_P:PersistentLevel.{kind}_1"
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    async def invoke(seat, mode, *fields):
        if mode == "state":
            return (await seat.bridge.command(fixture, mode, timeout=.8, update_status=False))["ack"]["message"]
        return (await seat.bridge.command(fixture, mode, *fields))["ack"]["message"]

    async def view(lit):
        for run in (host_run, guest_run):
            await wait_resident_status(run, lambda frame:
                frame.get("game", {}).get(native_stock_switch.KEY, {}).get(actor, {}).get("powered") is lit)
        readings = {}
        try:
            async with asyncio.timeout(8):
                while True:
                    for name, seat in (("host", host), ("guest", guest)):
                        try:
                            readings[name] = (await invoke(seat, "state")).split("|", 4)
                        except TimeoutError:
                            continue  # The legacy bridge status can be superseded by a runtime command.
                    if len(readings) == 2 and all(
                        row[:2] == [str(int(lit))] * 2 and (lit or row[2] == "0")
                        and ("UnLit" in row[-1]) is not lit
                        for row in readings.values()
                    ) and readings["guest"][3] == "0":
                        return readings
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"Expected stock candle lit={lit}, got {readings}") from error

    before = await view(True)
    frame = await wait_resident_status(guest_run, lambda row: row.get("game", {}).get("map") == WAREHOUSE_DOCK)
    assert await invoke(guest, "local-extinguish") == "1"
    started = time.time()
    assert await invoke(guest, "guest-near") == "guest pressed"
    after_guest = await view(False)
    receipt, = [event for event in await resident_events(guest_run, frame) if event["time_unix"] >= started
                and event.get("native_stock_switch_interaction", {}).get("actor") == actor]
    assert await invoke(host, "ignite") == "host lit the candle"
    relit = await view(True)
    assert await invoke(host, "host") == "host pressed"
    after_host = await view(False)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_warehouse_candle": {"actor": actor, "before": before, "after_guest": after_guest,
            "relit": relit, "after_host": after_host, "guest_receipt_ms": receipt["elapsed_ms"],
            "guest_local_extinguish_suppressed": True, "rendered_niagara_checked": False,
            "campaign_checkpoint_unchanged": True}}


async def verify_warehouse_switch(host: Seat, guest: Seat, pawn: str, *, radio: bool = False,
                                  check_track_selection: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    dock = WAREHOUSE_DOCK
    device = "radio" if radio else "switch"
    name = WAREHOUSE_DEVICES[device]
    fixture = f"fixture-warehouse-{device}"

    def switch(frame):
        return frame.get("game", {}).get(native_stock_switch.KEY, {}).get(name, {})

    async def playback(powered):
        expected = "playing" if powered else "silent"
        observed = {}

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            for name, seat in (("host", host), ("guest", guest)):
                if observed.get(name, "").partition("|")[0] == expected:
                    continue
                try:
                    reply = await seat.bridge.command(fixture, "playback", timeout=.8, update_status=False)
                except TimeoutError:
                    continue
                observed[name] = reply["ack"]["message"]
            if all(observed.get(seat, "").partition("|")[0] == expected for seat in ("host", "guest")):
                break
            await asyncio.sleep(.12)
        return observed

    current = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if current["game"].get("map") != dock:
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obs_Dock", "Level_Exit_BP_C_213")
    arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == dock
                                         and switch(frame) and not frame.get("awaiting_zone_guest"), timeout=60)
    await wait_resident_status(guest_run, lambda frame: frame.get("zone_epoch") == arrival["zone_epoch"]
                               and switch(frame) == switch(arrival), timeout=30)
    initial = switch(arrival)
    if not radio:
        assert initial["lights"], initial
    checkpoint = campaign_save_hashes(host.campaign)
    await host.bridge.command(fixture, "host")
    host_flip = switch(await wait_resident_status(host_run, lambda frame: switch(frame).get("powered") is not None
                                                  and switch(frame)["powered"] != initial["powered"]
                                                  and (radio or switch(frame)["angles"] != initial["angles"])))
    if not radio:
        assert host_flip["lights"] != initial["lights"] and host_flip["panel"] != initial["panel"], host_flip
    await wait_resident_status(guest_run, lambda frame: switch(frame) == host_flip)
    host_media = await playback(host_flip["powered"]) if radio else None

    guest_pawn = arrival["game"]["native_guest_save"]["pawn"]
    positioned = await host.bridge.command(fixture, "stage", guest_pawn)
    point = list(map(float, positioned["ack"]["message"].split(",")))
    guest_frame = await wait_resident_status(guest_run, lambda frame:
                                            math.dist(frame.get("game", {}).get("pawns", {}).get("local", {}).get(
                                                "location", [0, 0, 0]), point) < 80)
    pressed_at = time.time()
    pressed = await guest.bridge.command(fixture, "guest")
    assert pressed["ack"]["message"] == "guest pressed", pressed.get("ack")
    async with asyncio.timeout(8):
        while True:
            events = [row for row in await resident_events(guest_run, guest_frame) if row["time_unix"] >= pressed_at]
            assert not [row for row in events if row["event"] == "interaction_rejected"], events
            if any(row.get("native_stock_switch_interaction", {}).get("actor") == name for row in events):
                break
            await asyncio.sleep(.08)
    await wait_resident_status(host_run, lambda frame: switch(frame) == initial)
    await wait_resident_status(guest_run, lambda frame: switch(frame) == initial)
    guest_media = await playback(initial["powered"]) if radio else None
    assert campaign_save_hashes(host.campaign) == checkpoint, "A switch caused an early checkpoint"
    result = {"actor": name, "initial": initial, "host_flip": host_flip,
              "guest_returned_to_initial": True, "checkpoint_unchanged": True}
    if radio:
        result["media_both_seats"] = [host_media, guest_media]
        active, inactive = (host_media, guest_media) if host_flip["powered"] else (guest_media, host_media)
        result["stock_track_selected"] = all(active.get(seat, "").partition("|")[2].lower().endswith(".wav")
                                             for seat in ("host", "guest"))
        result["media_playing_both_seats"] = all(active.get(seat, "").partition("|")[0] == "playing"
                                                 and inactive.get(seat, "").partition("|")[0] == "silent"
                                                 for seat in ("host", "guest"))
        if check_track_selection:
            assert result["stock_track_selected"], result
    return {f"stock_warehouse_{device}": result}


async def verify_warehouse_board(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
    name = "Dest_WoodPlank_BP_C_1"

    def intact(frame):
        roster = frame.get("game", {}).get(native_destructibles.KEY)
        return isinstance(roster, list) and name in roster

    current = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if current["game"].get("map") != dock:
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obs_Dock", "Level_Exit_BP_C_213")
    arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == dock
                                         and intact(frame) and not frame.get("awaiting_zone_guest")
                                         and frame["game"].get("native_guest_save", {}).get("state") == "bound", timeout=60)
    pawn = arrival["game"]["native_guest_save"]["pawn"]
    epoch = arrival["zone_epoch"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_destructible_view") ==
                               {"map": dock, "epoch": epoch}, timeout=30)

    async def operate(seat, action, *args):
        try:
            reply = await seat.bridge.command("fixture-warehouse-board", action, *args)
        except TimeoutError as error:
            path = seat.bridge.directory / "status.tsv"
            diagnostics = path.read_text(encoding="utf-8").splitlines()[:5] if path.is_file() else []
            raise RuntimeError(f"Warehouse board fixture {action} timed out. Last status header {diagnostics}") from error
        return reply["ack"]["message"]

    before_host, before_guest = await operate(host, "inspect"), await operate(guest, "inspect")
    assert before_host == before_guest and before_host.startswith("false,true,"), (before_host, before_guest)
    # A guest-only damage callback must not remove a stock obstacle while the host still sees it.
    await operate(guest, "damage")
    assert await operate(guest, "inspect") == before_guest
    checkpoint = campaign_save_hashes(host.campaign)
    guid = await operate(host, "seed", pawn)
    assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
    async with asyncio.timeout(8):
        while await operate(guest, "select", guid) != "selected":
            await asyncio.sleep(.08)
    guest_frame = await wait_resident_status(guest_run, lambda frame:
        frame.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid, timeout=12)
    assert guest_frame["game"]["native_guest_primary_scope"]["kind"] == "melee"

    async def approach(action):
        async with asyncio.timeout(8):
            while (placement := await operate(host, action, pawn)) == "waiting for stock pipe":
                await asyncio.sleep(.08)
        position = list(map(float, placement.split(",")))
        await wait_resident_status(guest_run, lambda frame: math.dist(
            frame.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), position) < 35)

    await approach("approach")
    # This plank's stock threshold admits the Heavy Pipe's charged attack, but a
    # plain tap must leave the actor intact and publish a readable terminal receipt.
    for _ in range(3):
        assert await operate(guest, "aim") == "ready"
        assert await operate(host, "aim", pawn) == "ready"
        tap = await submit(guest_run, "native-guest-primary-input-fixture")
        assert tap["ok"], tap
        sequence = tap["native_guest_primary_fixture"]["sequence"]
        async with asyncio.timeout(8):
            while True:
                events = await resident_events(guest_run, guest_frame)
                event = next((row for row in events if row.get("sequence") == sequence
                              and row["event"] in {"native_guest_melee_input", "native_guest_melee_input_rejected"}), None)
                if event:
                    break
                await asyncio.sleep(.08)
        assert event["event"] == "native_guest_melee_input", event
        ordinary_tap = event[native_guest_melee.SWING_KEY]
        contact = await operate(host, "contact")
        assert native_guest_melee.verified_miss(ordinary_tap) and await operate(host, "inspect") == before_host, (
            ordinary_tap, contact)
        if contact == "obstacle_damage_blocked":
            break
    else:
        raise AssertionError(f"The stock Heavy Pipe tap did not authenticate the obstacle no-op: {contact}")

    await approach("approach-charged")

    async def aim_charged():
        assert await operate(guest, "aim-charged") == "ready"
        assert await operate(host, "aim-charged", pawn) == "ready"

    receipts, misses = [], []
    previous = before_host
    for _ in range(5):
        await aim_charged()
        proof = await stock_melee_charge(host_run, guest_run, guest_frame, guid, before_release=aim_charged, hold=1.1)
        after = await operate(host, "inspect")
        assert proof["pawn"] == pawn and proof["map"] == dock, proof
        if proof["hits"] == 0 and after == previous:
            misses.append(proof)
            continue
        assert proof["hits"] == 1, (proof, previous, after)
        receipts.append({"input": proof, "before": previous, "after": after})
        if after == "removed":
            break
        assert after.startswith("false,true,") and float(after.rsplit(",", 1)[1]) < float(previous.rsplit(",", 1)[1]), (
            previous, after, proof)
        previous = after
    assert after == "removed" and len(receipts) >= 2, (previous, receipts, misses)
    await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == dock
                               and native_destructibles.KEY in frame["game"] and not intact(frame), timeout=6)
    deadline = asyncio.get_running_loop().time() + 8
    while True:
        after_guest = await operate(guest, "inspect")
        if after_guest.startswith("true,false,"):
            break
        assert asyncio.get_running_loop().time() < deadline, after_guest
        await asyncio.sleep(.08)
    assert campaign_save_hashes(host.campaign) == checkpoint, "A broken obstacle caused an early checkpoint"
    return {"stock_warehouse_board": {"actor": name, "before": before_host, "guest_after": after_guest,
                                       "host_removed": True, "guest_damage_guarded": True,
                                       "stock_tap_blocked": ordinary_tap, "guest_stock_swings": receipts,
                                       "fixture_misses": misses,
                                       "checkpoint_unchanged": True}}


async def warehouse_prop_probe(seat, mode, *args, timeout=8):
    end = "\nportable-stock-prop-complete"
    async with asyncio.timeout(timeout):
        reply = await portable_probe_command(seat.bridge, "portable-stock-prop", "\t".join((mode, *args)), reply_end=end)
    return reply.removesuffix(end)


async def wait_stock_prop_break(host, guest, frame, path, started, point=None, *, guest_path=None, kind="bottle"):
    guest_run = guest.client / "run"
    event = native_anomaly_effects.CUE_EVENTS[kind]
    deadline = asyncio.get_running_loop().time() + 8
    while True:
        host_after, guest_after = await asyncio.gather(warehouse_prop_probe(host, "inspect", path),
                                                      warehouse_prop_probe(guest, "inspect", guest_path or path))
        cues = [row for row in await resident_events(guest_run, frame)
                if row["time_unix"] >= started and row["event"] == event and row["cue"] == kind]
        if host_after == guest_after == "absent" and cues:
            assert len(cues) == 1, cues
            if point:
                assert math.dist(cues[0]["location"], point) < 10, (cues, point)
            return cues[0]
        if asyncio.get_running_loop().time() >= deadline:
            host_frame = await wait_resident_status(host.campaign / "run", lambda row: row.get("game", {}).get("map") == WAREHOUSE_DOCK)
            contacts = await warehouse_prop_probe(host, "contact-read", path) if point is None else None
            raise AssertionError((kind, path, host_after, guest_after, cues,
                                  host_frame["game"].get(native_anomaly_effects.KEY, []), contacts))
        await asyncio.sleep(.12)


async def verify_stock_bottle(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
    current = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if current["game"].get("map") != dock:
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obs_Dock", "Level_Exit_BP_C_213")
    arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == dock
                                         and not frame.get("awaiting_zone_guest"), timeout=60)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == dock
                                             and frame.get("zone_epoch") == arrival["zone_epoch"], timeout=30)

    operate = warehouse_prop_probe
    path = await operate(host, "find")
    async with asyncio.timeout(6):
        while (guest_before := await operate(guest, "inspect", path)) == "absent":
            await asyncio.sleep(.08)
    assert guest_before == "1,false", guest_before
    assert await operate(host, "inspect", path) == "3,false"
    await operate(guest, "local-break", path)
    assert await operate(guest, "inspect", path) == guest_before
    checkpoint = campaign_save_hashes(host.campaign)
    started = time.time()
    point = list(map(float, (await operate(host, "impact", path)).split(",")))
    impact = await wait_stock_prop_break(host, guest, guest_frame, path, started, point)

    thrown = await operate(host, "find")
    assert thrown != path
    guest_pawn = arrival["game"]["native_guest_save"]["pawn"]
    position = list(map(float, (await operate(host, "approach", thrown, guest_pawn)).split(",")))
    await wait_resident_status(guest_run, lambda frame: math.dist(
        frame.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), position) < 45)
    stock_class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Bottle_Booze01.Prop_Bottle_Booze01_C"
    actor_name = f"Prop_Bottle_Booze01_C {thrown}"
    grabbed = await submit(guest_run, native_prop_carry.FIXTURE, target=f"pickup@{stock_class}|{actor_name}")
    assert grabbed["ok"], grabbed
    receipt = await wait_resident_status(host_run, lambda frame: (edge := frame.get("game", {}).get(native_prop_carry.PROOF, {}))
                                         and edge["edge"] == "grab" and edge["actor"] == actor_name)
    lease = receipt["game"][native_prop_carry.PROOF]["lease"]
    await wait_resident_status(guest_run, lambda row: (gate := row.get("game", {}).get(native_prop_carry.FILTER, {}))
                               and gate["lease"] == lease and gate["active"], timeout=6)
    height_before = float(await operate(host, "height", thrown))
    assert await operate(guest, "lift", thrown) == "guest lifted the bottle"
    height = height_before
    try:
        async with asyncio.timeout(5):
            while (height := float(await operate(host, "height", thrown))) < height_before + 45:
                await asyncio.sleep(.05)
    except TimeoutError as error:
        authority = await host.bridge.command("fixture-stock-bottle-grip", thrown)
        raise AssertionError({"height_cm": [height_before, height], "authority": authority["ack"]["message"],
                              "guest_grip": await operate(guest, "guest-grip", thrown)}) from error
    assert await operate(host, "contact-watch", thrown) == "armed"
    started = time.time()
    assert await operate(guest, "throw-down", thrown) == "guest threw at the floor"
    release = await wait_resident_status(host_run, lambda frame: (edge := frame.get("game", {}).get(native_prop_carry.PROOF, {}))
                                         and edge["edge"] == "throw" and edge["actor"] == actor_name)
    throw_cue = await wait_stock_prop_break(host, guest, guest_frame, thrown, started)
    contacts = await operate(host, "contact-read", thrown)
    assert impact["serial"] != throw_cue["serial"]
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_bottle": {"host_impact": {"actor": path, "guest_role": guest_before, "break_cue": impact},
                             "guest_throw": {"actor": thrown, "break_cue": throw_cue, "stock_contacts": contacts,
                                             "host_release": release["game"][native_prop_carry.PROOF],
                                             "lift_cm": height - height_before},
                             "checkpoint_unchanged": True}}


async def stage_warehouse_prop(host, guest, stock_class, placement=""):
    path, position = (await warehouse_prop_probe(host, "spawn-prop", stock_class, placement)).split(";")
    async with asyncio.timeout(8):
        while (replica := await warehouse_prop_probe(guest, "locate-prop", stock_class, position)) == "waiting":
            await asyncio.sleep(.08)
    return path, replica, position


async def verify_stock_prop_breaks(host: Seat, guest: Seat, _pawn: str) -> dict:
    checkpoint = campaign_save_hashes(host.campaign)
    frame = await wait_resident_status(guest.client / "run", lambda row: row.get("game", {}).get("map") == WAREHOUSE_DOCK)

    async def check(kind, stock_class):
        path, replica, _ = await stage_warehouse_prop(host, guest, stock_class)
        await warehouse_prop_probe(guest, "local-break", replica)
        assert await warehouse_prop_probe(guest, "inspect", replica) == "1,false", (kind, replica)
        started = time.time()
        point = list(map(float, (await warehouse_prop_probe(host, "impact", path)).split(",")))
        return {"class": stock_class, "cue": await wait_stock_prop_break(
            host, guest, frame, path, started, point, guest_path=replica, kind=kind,
        )}

    results, failures = {}, {}
    for kind, stock_class in (("bottle", "Prop_Bottle_Oil"), ("ceramic", "Prop_CoffeeMug"),
                              ("platestand", "Prop_PlateStand"), ("glass", "Prop_ShotGlass"),
                              ("paper", "Prop_OpenBook"), ("cardboard", "Prop_CardboardBox_01")):
        try:
            results[kind] = await check(kind, stock_class)
        except (AssertionError, RuntimeError, TimeoutError) as error:
            failures[kind] = f"{type(error).__name__}: {error}"
    assert not failures, (list(results), failures)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_prop_breaks": results}


async def verify_stock_acid_bottle(host: Seat, guest: Seat, _pawn: str) -> dict:
    checkpoint = campaign_save_hashes(host.campaign)
    frame = await wait_resident_status(guest.client / "run", lambda row: row.get("game", {}).get("map") == WAREHOUSE_DOCK)
    path, replica, position = await stage_warehouse_prop(host, guest, "Prop_AcidBottle")
    await warehouse_prop_probe(guest, "local-break", replica)
    assert await warehouse_prop_probe(guest, "inspect", replica) == "1,false"
    assert await warehouse_prop_probe(guest, "pool-near", position) == "absent"

    started = time.time()
    impact = await warehouse_prop_probe(host, "floor-impact", path)
    location = list(map(float, impact.split(",")))
    cue = await wait_stock_prop_break(host, guest, frame, path, started, location, guest_path=replica)

    async def read_pool(seat, phase):
        async with asyncio.timeout(14):
            while True:
                try:
                    value = await warehouse_prop_probe(seat, "pool-near", impact, timeout=1.2)
                except TimeoutError:
                    continue
                if value == "absent" and phase == value:
                    return None
                if value.startswith(phase + ";"):
                    found, position, size, burn, spheres, dot, collision, hidden = value.split(";")
                    assert hidden == "false", value
                    return {"phase": found, "position": list(map(float, position.split(","))), "size": float(size),
                            "burn_time": float(burn), "spheres": int(spheres),
                            "damage_active": dot == "true", "collision": collision == "true"}
                await asyncio.sleep(.09)

    authority, projected = await asyncio.gather(*(read_pool(seat, "active") for seat in (host, guest)))
    assert math.dist(authority["position"], location) < .02, authority
    assert math.dist(projected["position"], authority["position"]) < .02, (authority, projected)
    assert authority["size"] == projected["size"] == .6 and authority["spheres"] == projected["spheres"] > 0
    assert 6 <= authority["burn_time"] <= 8 and projected["burn_time"] == 0, (authority, projected)
    assert authority["damage_active"] and authority["collision"] and not projected["damage_active"] and not projected["collision"]

    expired = await asyncio.gather(*(read_pool(seat, "extinguished") for seat in (host, guest)))
    assert all(not reading["damage_active"] for reading in expired), expired
    assert expired[0]["spheres"] == expired[1]["spheres"] < authority["spheres"], expired
    assert await warehouse_prop_probe(host, "pool-remove", impact) == "removed"
    await asyncio.gather(*(read_pool(seat, "absent") for seat in (host, guest)))
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_acid_bottle": {"cue": cue, "host_pool": authority, "guest_pool": projected, "extinguished": expired,
                                  "guest_local_break_veto": True, "removed_on_both_seats": True,
                                  "checkpoint_unchanged": True}}


async def verify_stock_wood_debris(host: Seat, guest: Seat, pawn: str) -> dict:
    checkpoint = campaign_save_hashes(host.campaign)
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    frame = await wait_resident_status(guest_run, lambda row: row.get("game", {}).get("map") == WAREHOUSE_DOCK)

    async def pieces(seat, position):
        raw = await warehouse_prop_probe(seat, "wood-pieces", position)
        result = {}
        for row in raw.split(";") if raw else ():
            name, kind, mesh, role, random_mesh, *center = row.split(",")
            result[name] = {"kind": kind, "mesh": mesh, "role": int(role), "random_mesh": random_mesh == "1",
                            "center": list(map(float, center))}
        return result

    def meshes(parts):
        return sorted((row["kind"], row["mesh"]) for row in parts.values())

    async def guest_carry(source, selected, authority, guest_replica):
        scope = source.partition(":PersistentLevel.")[0] + ":PersistentLevel."
        host_path, guest_path = scope + selected, scope + guest_replica
        actor_name = f"{authority['kind']} {host_path}"
        remote_name = f"{authority['kind']} {guest_path}"
        stem = authority["kind"].removesuffix("_C")
        asset = f"/Game/Assets/StaticMeshes/Mesh_Props/{stem}.{authority['kind']}"

        async def body(seat, path):
            location, held = (await warehouse_prop_probe(seat, "prop-motion", path)).split(";")
            return list(map(float, location.split(","))), held == "true"

        async def motion():
            return await asyncio.gather(body(host, host_path), body(guest, guest_path))

        async def proof(edge):
            result = await wait_resident_status(host_run, lambda row: (
                noted := row.get("game", {}).get(native_prop_carry.PROOF, {}))
                and (noted["edge"], noted["pawn"], noted["actor"]) == (edge, pawn, actor_name), timeout=6)
            return result["game"][native_prop_carry.PROOF]

        async def stage():
            placement = await warehouse_prop_probe(host, "approach", host_path, pawn)
            position = list(map(float, placement.split(",")))
            await wait_resident_status(guest_run, lambda row: math.dist(
                row.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), position) < 45)
            async with asyncio.timeout(4):
                while True:
                    starting = await motion()
                    if math.dist(starting[0][0], starting[1][0]) < 30:
                        return starting
                    await asyncio.sleep(.06)

        request = f"grab@{asset}|{remote_name}"
        await stage()
        assert await warehouse_prop_probe(host, "wood-deny-grip", host_path) == "reserved"
        try:
            started = time.time()
            predicted = await submit(guest_run, native_prop_carry.FIXTURE, target=request)
            assert predicted["ok"], predicted
            async with asyncio.timeout(6):
                while True:
                    refusals = [row for row in await resident_events(guest_run, frame)
                                if row["time_unix"] >= started and row["event"] == "native_prop_carry_refused"
                                and row["edge"] == "grab"]
                    held = await warehouse_prop_probe(guest, "guest-grip", guest_path)
                    if refusals and held.endswith("held=false"):
                        break
                    await asyncio.sleep(.07)
            assert (await body(host, host_path))[1], "the host reservation was cleared by a guest grab"
        finally:
            assert await warehouse_prop_probe(host, "wood-release-grip", host_path) == "released"

        starting = await stage()
        grabbed = await submit(guest_run, native_prop_carry.FIXTURE, target=request)
        assert grabbed["ok"], grabbed
        grabbed_proof = await proof("grab")
        lease = grabbed_proof["lease"]

        async def filter_status(active, *, require_suppressed=False):
            confirmed = await wait_resident_status(guest_run, lambda row: (
                gate := row.get("game", {}).get(native_prop_carry.FILTER, {}))
                and gate["lease"] == lease and gate["active"] == active
                and (not require_suppressed or gate["suppressed"] > 0), timeout=6)
            return confirmed["game"][native_prop_carry.FILTER]

        armed = await filter_status(True)
        assert await warehouse_prop_probe(guest, "lift", guest_path) == "guest lifted the bottle"
        lift_started = asyncio.get_running_loop().time()
        samples, settled = [], 0
        try:
            async with asyncio.timeout(5):
                while True:
                    lifted = await motion()
                    gap = math.dist(lifted[0][0], lifted[1][0])
                    samples.append([round(asyncio.get_running_loop().time() - lift_started, 3), round(gap, 2)])
                    both_up = all(held and center[2] > base[0][2] + 40
                                  for (center, held), base in zip(lifted, starting, strict=True))
                    settled = settled + 1 if both_up and gap < 40 else 0
                    if settled >= 4:
                        break
                    await asyncio.sleep(.06)
        except TimeoutError as error:
            raise AssertionError({"staged": starting, "last_lift": lifted, "lift_gaps": samples}) from error
        protected = await filter_status(True, require_suppressed=True)
        assert await warehouse_prop_probe(guest, "throw-up", guest_path) == "guest threw upward"
        thrown_proof = await proof("throw")
        assert thrown_proof["lease"] == lease and thrown_proof.get("release_pose_applied"), thrown_proof
        cleared = await filter_status(False)
        final = None
        try:
            async with asyncio.timeout(7):
                while True:
                    final = await motion()
                    if (not any(held for _, held in final) and math.dist(final[0][0], final[1][0]) < 40
                            and math.dist(final[0][0][:2], lifted[0][0][:2]) > 60):
                        break
                    await asyncio.sleep(.06)
        except TimeoutError as error:
            raise AssertionError({"lifted": lifted, "last_throw": final}) from error
        return {"contested_grab": refusals[0], "guest_released_refused_grip": True,
                "grab": grabbed_proof, "held": lifted, "throw": thrown_proof, "released": final,
                "lift_gap_seconds_cm": samples, "filter": [armed, protected, cleared]}

    async def check(stock_class, kind, count):
        path, replica, position = await stage_warehouse_prop(host, guest, stock_class, "floor-physics")
        before = await asyncio.gather(*(pieces(seat, position) for seat in (host, guest)))
        await warehouse_prop_probe(guest, "local-break", replica)
        after = await warehouse_prop_probe(guest, "inspect", replica)
        assert after == "1,false", {"guest_only_break_retired_source": after}
        local_parts = (await pieces(guest, position)).keys() - before[1].keys()
        assert not local_parts, {"guest_only_break_spawned_parts": list(local_parts)}
        started = time.time()
        hit = list(map(float, (await warehouse_prop_probe(host, "impact", path)).split(",")))
        impact = await wait_stock_prop_break(host, guest, frame, path, started, hit, guest_path=replica, kind=kind)

        async def new_pair():
            pair = await asyncio.gather(*(pieces(seat, position) for seat in (host, guest)))
            return [{name: row for name, row in current.items() if name not in existing}
                    for current, existing in zip(pair, before, strict=True)]

        authority, remote = {}, {}
        try:
            async with asyncio.timeout(7):
                while True:
                    authority, remote = await new_pair()
                    if len(authority) == len(remote) == count and meshes(authority) == meshes(remote):
                        break
                    await asyncio.sleep(.08)
        except TimeoutError as error:
            raise AssertionError({"host_pieces": authority, "guest_pieces": remote, "expected": count}) from error
        assert (all(row["role"] == 3 for row in authority.values())
                and all(row["role"] == 1 for row in remote.values())), (authority, remote)
        assert all(not row["random_mesh"] for row in remote.values() if row["kind"] == "Prop_ScrapWood_Post_C"), remote

        selected, original = next(iter(authority.items()))
        assert await warehouse_prop_probe(host, "wood-nudge", selected) == "moved"
        displaced = None
        actual, view = authority, remote
        try:
            async with asyncio.timeout(6):
                while True:
                    actual, view = await new_pair()
                    target = actual.get(selected, {}).get("center")
                    if target and math.dist(target[:2], original["center"][:2]) > 80:
                        displaced = target
                    matches = sorted((math.dist(row["center"], displaced), name) for name, row in view.items()
                                     if displaced and row["mesh"] == original["mesh"] and row["kind"] == original["kind"])
                    if matches and matches[0][0] < 30 and (len(matches) == 1 or matches[1][0] - matches[0][0] > 10):
                        break
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError({"relocation": displaced, "original": original, "last_readback": [actual, view]}) from error

        cues = [row for row in await resident_events(guest_run, frame)
                if row["time_unix"] >= started and row["event"] == "native_prop_break_played"
                and row["cue"] == "woodsplinters"]
        assert len(cues) == 1 and math.dist(cues[0]["location"], hit) < 150, cues
        interaction = await guest_carry(path, selected, original, matches[0][1]) if stock_class == "Prop_Chair_Wood" else None
        assert await warehouse_prop_probe(host, "wood-remove", selected) == "removed"
        try:
            async with asyncio.timeout(8):
                while any(await new_pair()):
                    await asyncio.sleep(.09)
        except TimeoutError as error:
            raise AssertionError("destroyed stock debris remained visible") from error
        return {"host_pieces": authority, "guest_pieces": remote, "nudge": displaced, "guest_interaction": interaction,
                "impact": impact, "splinters": cues[0], "removed_on_both": True}

    results, failures = {}, {}
    for stock_class, kind, count in (("Prop_Chair_Wood", "woodchips", 5), ("Prop_WoodCrate_Med_01", "wood", 5),
                                     ("Prop_WoodPallet", "woodchips", 6)):
        try:
            results[stock_class] = await check(stock_class, kind, count)
        except (AssertionError, RuntimeError, TimeoutError) as error:
            failures[stock_class] = f"{type(error).__name__}: {error}"
    assert not failures, (list(results), failures)
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_wood_debris": results}


async def verify_stock_throwables(host: Seat, guest: Seat, pawn: str, case: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory")
    host_frame = await wait_resident_status(host_run, lambda frame: frame.get("local_bridge") == "memory")

    async def probe(seat, action, kind, arg=""):
        return await portable_probe_command(seat.bridge, "portable-stock-throwables", "\t".join((action, kind, arg)))

    async def observe(seat, kind, role, *, retired=False, must_render=False):
        deadline = asyncio.get_running_loop().time() + 8
        while asyncio.get_running_loop().time() < deadline:
            if kind == "bolt" and must_render and int(await probe(seat, "visual", kind)) > 0:
                return {"role": role, "short_flight_visual": True}
            status = await probe(seat, "shot", kind)
            if status != "absent":
                name, actual_role, active, peak, fuse, visible, game_seconds, display = status.split(",")
                assert int(actual_role) == role, status
                in_view = float(peak) > 8 and (not must_render or int(visible) > 0)
                if (active == "false" if retired else in_view or kind == "bolt" and not must_render):
                    return {"actor": name, "role": role, "active": active == "true", "travel_cm": float(peak),
                            "fuse_seconds": float(fuse), "visible_samples": int(visible),
                            "game_seconds": float(game_seconds), "display": display}
            await asyncio.sleep(.04)
        raise AssertionError((kind, role, "retired" if retired else "in flight", status))

    verified = {}
    for owner, kind in ((host, "pipebomb"), (host, "bolt"), (guest, "bolt"), (guest, "pipebomb")):
        if case not in ("all", kind) and (case != "ground" or kind != "bolt"):
            continue
        side = "host" if owner is host else "guest"
        await asyncio.gather(*(probe(seat, "reset", kind) for seat in (host, guest)))
        guid = await probe(host, "seed", kind, side)
        assert re.fullmatch(r"[A-F0-9]{32}", guid), guid
        if owner is guest:
            stored = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=guid)
            assert stored["ok"], stored
            slot = str(stored[native_inventory.STORE_KEY]["slot"])
            async with asyncio.timeout(8):
                while await probe(guest, "equip", kind, slot) != "ready":
                    await asyncio.sleep(.05)
            await wait_resident_status(guest_run, lambda frame: (
                frame.get("game", {}).get("native_guest_equipment_selection", {}).get("guid") == guid))
        if kind == "bolt":
            target = list(map(float, (await probe(host, "stage", kind, side)).split(",")))
            position_run = guest_run if owner is guest else host_run
            await wait_resident_status(position_run, lambda frame: math.dist(frame.get("game", {}).get("pawns", {})
                                       .get("local", {}).get("location", [0, 0, 0]), target) < 20, timeout=8)
        assert await probe(owner, "press", kind) == "press"
        if owner is guest and kind == "pipebomb":
            async with asyncio.timeout(3):
                while (cooked := float(await probe(owner, "cook", kind))) < .18:
                    await asyncio.sleep(.05)
        else:
            await asyncio.sleep(.2)
        started = time.time()
        assert await probe(owner, "release", kind) == "release"
        shots = await asyncio.gather(observe(host, kind, 3, must_render=owner is guest),
                                     observe(guest, kind, 1, must_render=owner is host))
        record = {"projectiles": shots}
        if kind == "bolt":
            async with asyncio.timeout(5):
                while (ground := await probe(host, "landed", kind)) == "absent":
                    await asyncio.sleep(.05)
            x, y, z, name = ground.split(",", 3)
            deadline = asyncio.get_running_loop().time() + 3
            derived = "absent"
            while derived == "absent" and asyncio.get_running_loop().time() < deadline:
                derived = await probe(guest, "nearby", kind, ",".join((x, y, z)))
                await asyncio.sleep(.08)
            record["ground_probe"] = {"location": list(map(float, (x, y, z))), "host": name, "guest": derived}
            if case == "ground":
                async with asyncio.timeout(10):
                    while True:
                        banked = [row for row in await resident_events(host_run, host_frame)
                                  if row["event"] == "native_stock_enemy_loot_banked" and row["actor"] == name]
                        if banked:
                            bank, = banked
                            break
                        await asyncio.sleep(.08)
                assert bank["ammo"] == 1 and bank["revision"] is None, bank
                identity = native_world_loot.stock_enemy_identity(bank["guid"], bank.get("visual_id"))
                point = bank["transform"]["position"]
                close = await probe(host, "nearlanding", kind, ",".join(f"{axis:.3f}" for axis in point))
                assert close == "near", close
                await wait_resident_status(host_run, lambda frame: identity in frame.get("game", {}).get(
                    native_world_loot.KEY, {}), timeout=8)
                picker, picker_run = (guest, guest_run) if owner is host else (host, host_run)
                picker_name = pawn if picker is guest else host_frame["game"]["pawns"]["local"]["name"]
                async def count_bolts():
                    report = await submit(picker_run, "native-inventory-list")
                    return sum(item["quantity"] for item in report["native_inventory"]["items"]
                               if item["class_path"] == bank["class_path"])
                before = await count_bolts()
                if picker is host:
                    await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
                approached = await host.bridge.command(native_world_loot.APPROACH, identity, picker_name, timeout=5)
                assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
                await wait_resident_status(guest_run, lambda frame: identity in frame.get("game", {}).get(
                    native_world_loot.KEY, {}), timeout=8)
                await wait_resident_status(picker_run, lambda frame: math.dist(frame.get("game", {}).get("pawns", {})
                                           .get("local", {}).get("location", [0, 0, 0]), point) < 225, timeout=8)
                picked = await picker.bridge.command(native_world_loot.FIXTURE, identity, timeout=5)
                assert picked["ack"]["message"] == "stock world loot interaction intercepted", picked["ack"]
                for run in (host_run, guest_run):
                    await wait_resident_status(run, lambda frame: identity not in frame.get("game", {}).get(
                        native_world_loot.KEY, {}), timeout=8)
                assert await count_bolts() == before + 1
                record["ground_pickup"] = {"id": identity, "class": bank["class_path"], "quantity": 1}
        if owner is guest:
            confirmation = await wait_resident_status(host_run, lambda frame: (
                frame.get("game", {}).get("native_guest_flare_throw", {}).get("guid") == guid))
            receipt = confirmation["game"]["native_guest_flare_throw"]
            assert (receipt["pawn"], receipt["before"], receipt["after"]) == (pawn, 1, 0), receipt
            record["consumed"] = receipt
            if kind == "pipebomb":
                assert 0 < receipt["fuse_ms"] < 5000 and abs(shots[0]["fuse_seconds"] - receipt["fuse_ms"] / 1000) < .002
                record["stock_cook_before_release"] = cooked
                async with asyncio.timeout(5):
                    while (charges := await probe(guest, "charges", kind)) != "0,-1":
                        await asyncio.sleep(.05)
                record["guest_charges_after_throw"] = charges
        if kind == "pipebomb":
            record["retired"] = await asyncio.gather(observe(host, kind, 3, retired=True),
                                                      observe(guest, kind, 1, retired=True))
            record["stock_blast"] = await probe(host, "blasts", kind)
            assert record["stock_blast"].startswith("1:"), record
            async with asyncio.timeout(8):
                while True:
                    cues = [row for row in await resident_events(guest_run, guest_frame)
                            if row["event"] == "native_pipe_bomb_effect_played" and row["time_unix"] >= started]
                    if cues:
                        record["detonation_cue"], = cues
                        break
                    await asyncio.sleep(.05)
        verified[f"{side}_{kind}"] = record
    assert campaign_save_hashes(host.campaign) == checkpoint
    assert not weapon_cue_files(host, guest)
    return {"stock_throwables": verified}


async def verify_roaming_barter(host: Seat, guest: Seat, _pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def operate(seat, mode, *args):
        return await portable_probe_command(seat.bridge, "portable-roaming-barter", "\t".join((mode, *args)))

    def scope(frame):
        return frame.get("game", {}).get(native_npc_barter.VIEW, {})

    async def inspect(seat, point):
        raw = await operate(seat, "inspect", point)
        if "," in raw:
            keys = ("local_role", "npc_id", "stock_save", "stock_trader_id", "mod_identity", "stock_items")
            return dict(zip(keys, raw.split(","), strict=True))
        return {}

    async def screen(seat, point):
        raw = await operate(seat, "screen", point)
        if raw == "closed":
            return {}
        widget, wallet, enabled, opacity, cards = raw.split(",", 4)
        return {"widget": widget, "wallet": int(wallet), "visible": enabled == "true" and float(opacity) == 1,
                "cards": set(cards.split("|")) if cards else set()}

    point = await operate(host, "spawn")
    await asyncio.sleep(1.2)
    before_registration = await inspect(host, point)
    assert before_registration.get("mod_identity") == "-", before_registration
    assert await operate(host, "register", point) == "stock merchant data restored"
    host_before, guest_before = {}, {}
    deadline = asyncio.get_running_loop().time() + 8
    while asyncio.get_running_loop().time() < deadline:
        host_before, guest_before = await asyncio.gather(inspect(host, point), inspect(guest, point))
        if host_before.get("mod_identity", "-") != "-" and host_before.get("mod_identity") == guest_before.get("mod_identity"):
            break
        await asyncio.sleep(.12)
    assert host_before.get("local_role") == "3" and guest_before.get("local_role") == "1", (host_before, guest_before)
    identity = host_before["mod_identity"]
    assert identity != "-" and identity == guest_before["mod_identity"], (host_before, guest_before)
    assert host_before["stock_trader_id"] != guest_before["stock_trader_id"], (host_before, guest_before)
    host_item, item, contested, unbought, target = (await operate(host, "prepare", point)).split(",", 4)
    seeded = {host_item, item, contested, unbought}
    assert len(seeded) == 4 and all(re.fullmatch(r"[0-9A-F]{32}", key) for key in seeded)
    location = list(map(float, target.split(",")))
    await wait_resident_status(guest_run, lambda frame: math.dist(
        frame.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), location) < 55)
    host_stock = await inspect(host, point)
    assert seeded.issubset(host_stock["stock_items"].split("|"))
    assert seeded.isdisjoint(guest_before["stock_items"].split("|"))
    checkpoint = campaign_save_hashes(host.campaign)
    opacity, enabled = (await operate(guest, "open", point)).split(",")
    assert float(opacity) == 0 and enabled == "false", (opacity, enabled)
    ready = await wait_resident_status(guest_run, lambda frame: scope(frame).get("state") == "ready"
                                       and scope(frame).get("trader") == identity)
    projected = await inspect(guest, point)
    assert projected["stock_items"] == host_stock["stock_items"], (projected, host_stock)
    guest_screen = await screen(guest, point)
    assert guest_screen["visible"] and {host_item, item}.issubset(guest_screen["cards"]), guest_screen

    await operate(host, "open", point)
    host_screen = await screen(host, point)
    assert host_screen["visible"] and host_screen["wallet"] == guest_screen["wallet"], host_screen
    host_payment = int(await operate(host, "buy", point, host_item))
    async with asyncio.timeout(12):
        while True:
            updated = await screen(guest, point)
            if updated.get("visible") and host_item not in updated["cards"]:
                break
            await asyncio.sleep(.1)
    assert updated == {**guest_screen, "wallet": guest_screen["wallet"] - host_payment,
                       "cards": guest_screen["cards"] - {host_item}}, (guest_screen, updated)

    if not await screen(host, point):
        await operate(host, "open", point)
    host_browsing = await screen(host, point)
    assert host_browsing["visible"] and host_browsing["wallet"] == updated["wallet"], host_browsing
    started = time.time()
    cash = int(await operate(guest, "buy", point, item))
    new_view = await wait_resident_status(guest_run, lambda frame: scope(frame).get("state") == "ready"
                                         and scope(frame).get("sequence", 0) > scope(ready)["sequence"])
    host_frame = await wait_resident_status(host_run, lambda frame: frame.get("state") == "hosting")
    commits = [entry for entry in await resident_events(host_run, host_frame)
               if entry["event"] == "native_npc_barter_committed" and entry["time_unix"] >= started]
    trade, = commits
    assert trade["trader"] == identity and trade["cash_before"] - cash == trade["cash"], trade
    received, = trade["transfers"]
    assert received["guid"] == item and received["direction"] == "receive", received
    host_after, guest_after = await asyncio.gather(inspect(host, point), inspect(guest, point))
    assert host_after["stock_items"] == guest_after["stock_items"] and item not in host_after["stock_items"].split("|")
    host_updated = await screen(host, point)
    assert host_updated == {**host_browsing, "wallet": updated["wallet"] - cash,
                            "cards": host_browsing["cards"] - {item}}, (host_browsing, host_updated)
    assert int(await operate(guest, "stage", point, contested)) > 0
    last_host_payment = int(await operate(host, "buy", point, contested))
    async with asyncio.timeout(8):
        while contested in (await inspect(host, point))["stock_items"].split("|"):
            await asyncio.sleep(.08)
    disputed_at = time.time()
    assert await operate(guest, "confirm", point) == "confirmed"
    recovered = await wait_resident_status(guest_run, lambda frame: scope(frame).get("state") == "ready"
                                           and scope(frame).get("sequence", 0) > scope(new_view)["sequence"])
    recoveries = [entry for entry in await resident_events(guest_run, recovered)
                  if entry["time_unix"] >= disputed_at and entry["event"] == "native_npc_barter_refused"]
    refusal, = recoveries
    assert refusal["reason"] == "barter_refused_stock_selection" and refusal["catalogue_restored"] is True, refusal
    screen_after_refusal = await screen(guest, point)
    assert screen_after_refusal["visible"] and screen_after_refusal["cards"] == host_updated["cards"] - {contested}
    assert screen_after_refusal["wallet"] == host_updated["wallet"] - last_host_payment, screen_after_refusal
    final_host = await wait_resident_status(host_run, lambda frame: frame.get("state") == "hosting")
    assert not [entry for entry in await resident_events(host_run, final_host)
                if entry["event"] == "native_npc_barter_committed" and entry["time_unix"] >= disputed_at]
    if await screen(host, point):
        assert await operate(host, "close", point) == "0"
    assert await operate(guest, "dieconfirm", point, unbought) == "declined"
    assert await operate(guest, "deadopen", point) == "refused"
    await wait_resident_status(guest_run, lambda frame: not scope(frame))
    assert unbought in (await inspect(host, point))["stock_items"].split("|")
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_roaming_barter": {"stock_ids": [host_before["stock_trader_id"], guest_before["stock_trader_id"]],
                                     "late_merchant_registration": True,
                                     "shared_identity": identity, "committed_trade": trade, "stock_view": scope(new_view),
                                     "host_purchase_refreshed_same_guest_widget": True,
                                     "guest_purchase_refreshed_same_host_widget": True,
                                     "wallet": [guest_screen["wallet"], updated["wallet"], host_updated["wallet"]],
                                     "contested_offer_refused": refusal["reason"],
                                     "death_during_confirmation_kept_inventory_and_cash": True,
                                     "dead_trader_open_refused": True,
                                     "recovered_guest_wallet": screen_after_refusal["wallet"],
                                     "native_checkpoint_unchanged": True}}


async def verify_stock_critters(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    crossroads = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"

    async def probe(seat, mode, *args):
        report = await seat.bridge.command("fixture-stock-critter", mode, *args, update_status=False)
        return report["ack"]["message"]

    current = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if current["game"].get("map") != crossroads:
        for seat, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", seat, position, "Obsv_Etr", "Level_Exit_BP_C_1")
    arrival = await wait_resident_status(host_run, lambda f: f.get("game", {}).get("map") == crossroads
                                         and not f.get("awaiting_zone_guest"), timeout=60)
    connected = await wait_resident_status(guest_run, lambda f: f.get("game", {}).get("map") == crossroads
                                            and f.get("zone_epoch") == arrival["zone_epoch"], timeout=35)
    number, actor = (await probe(host, "find")).split(";", 1)
    point = tuple(map(float, (await probe(host, "stage", number)).split(",")))
    await wait_resident_status(guest_run, lambda f: f["game"].get("pawns", {}).get("local", {}).get("location")
                               and math.dist(f["game"]["pawns"]["local"]["location"], point) < 65, timeout=12)
    async with asyncio.timeout(10):
        while not (guest_idle := await probe(guest, "view", number)).startswith("1,false,idle,"):
            await asyncio.sleep(.05)

    assert await probe(host, "hop", number) == "stock character hop dispatched"
    async with asyncio.timeout(5):
        while not (guest_hop := await probe(guest, "view", number)).startswith("1,true,hop,"):
            await asyncio.sleep(.03)
    assert float(guest_hop.rsplit(",", 1)[1]) > float(guest_idle.rsplit(",", 1)[1])
    assert await probe(host, "settle", number) == "stock placement restored"
    async with asyncio.timeout(6):
        while not (await probe(guest, "view", number)).startswith("1,false,idle,"):
            await asyncio.sleep(.05)

    checkpoint = campaign_save_hashes(host.campaign)
    host_pack = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    pressed = time.time()
    assert await probe(guest, "press", number) == "stock Interact dispatched"
    async with asyncio.timeout(8):
        while True:
            events = [row for row in await resident_events(host_run, arrival)
                      if row["time_unix"] >= pressed and row["event"] == "native_stock_critter_claimed"]
            if events:
                break
            await asyncio.sleep(.06)
    claim, = events
    assert claim["actor"] == actor and claim["number"] == number, claim
    for seat in (host, guest):
        async with asyncio.timeout(7):
            while await probe(seat, "view", number) != "absent":
                await asyncio.sleep(.05)
    items = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
    assert any(item["guid"] == claim.get("inventory_guid", claim["guid"])
               and item["class_path"].endswith(".Item_LiveFrog_C") for item in items), items
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_pack
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_critters": {"actor": actor, "idle": guest_idle, "hop": guest_hop, "claim": claim,
                               "removed_on_both_peers": True, "checkpoint_unchanged": True}}


async def travel_pair_to_dock(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    dock = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
    initial = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_zone", {}).get("state") == "ready")
    if initial["game"].get("map") == dock:
        arrival = initial
    else:
        for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
            await host.bridge.command("native-exit-position", participant, placement, "Obs_Dock", "Level_Exit_BP_C_213")
        arrival = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == dock
                                             and frame.get("zone_epoch") != initial["zone_epoch"]
                                             and not frame.get("awaiting_zone_guest"), timeout=60)
    def at_dock(frame):
        return (frame.get("state") == "connected" and frame.get("gameplay_fresh")
                and frame.get("game", {}).get("map") == dock and frame.get("zone_epoch") == arrival["zone_epoch"])

    return await wait_resident_status(guest_run, at_dock, timeout=30)


async def verify_tutorial_dock_melee(host: Seat, guest: Seat, pawn: str) -> dict:
    guest_run = guest.client / "run"

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    inventory = await equip_stock_guest_melee(host, guest, "seed-pipe\theavy", "melee")
    guid = inventory["game"]["native_guest_primary_scope"]["guid"]
    arrival = await travel_pair_to_dock(host, guest, pawn)
    async with asyncio.timeout(8):
        while await command(guest, f"dock-melee-equip\t{guid}") != "selected":
            await asyncio.sleep(.05)
    frame = await wait_resident_status(guest_run, lambda snapshot: (
        snapshot.get("zone_epoch") == arrival["zone_epoch"]
        and (scope := snapshot.get("game", {}).get("native_guest_primary_scope", {})).get("guid") == guid
        and scope["kind"] == "melee"), timeout=12)
    host_frame = await current_status(host.campaign / "run")
    actor = host_frame["game"]["native_guest_save"]["pawn"]
    saves = campaign_save_hashes(host.campaign)
    starting_host, starting_guest = await asyncio.gather(*(command(seat, "dock-natural-state") for seat in (host, guest)))
    assert all(value.startswith("true;false;0;") for value in (starting_host, starting_guest)), (starting_host, starting_guest)
    assert starting_host.split(";")[4] == starting_guest.split(";")[4], (starting_host, starting_guest)
    worker = starting_host.split(";")[4]

    try:
        _, guest_point = (await command(host, "dock-melee-stage")).split(";")
        misses = []
        for attempt in range(3):
            assert await command(guest, f"dock-natural-arrive\t{guest_point}") == "ready"
            destination = tuple(map(float, guest_point.split(",")))
            async with asyncio.timeout(6):
                while math.dist(destination, tuple(map(float, (await command(guest, "dock-natural-seat")).split(",")))) > 30:
                    await asyncio.sleep(.05)
            baseline = (await command(host, "dock-natural-sample")).split(";")
            assert baseline[:4] == ["true", "false", "120.000", "120.000"], baseline

            host_frame, guest_frame = await asyncio.gather(*(command(seat, "dock-melee-aim") for seat in (host, guest)))
            host_eye, host_direction, target_before = host_frame.split(";")
            guest_eye, guest_direction, _ = guest_frame.split(";")
            tap = await submit(guest_run, "native-guest-primary-input-fixture")
            if tap.get("error", "").endswith("(native_guest_primary_fixture_scope_unavailable)") and attempt < 2:
                misses.append({"refusal": tap["error"], "target_before": target_before})
                guest_point = await command(host, "dock-melee-approach")
                continue
            assert tap["ok"], (tap, misses)
            sequence = tap["native_guest_primary_fixture"]["sequence"]
            async with asyncio.timeout(8):
                while True:
                    event = next((row for row in await resident_events(guest_run, frame) if row.get("sequence") == sequence
                                  and row["event"] in {"native_guest_melee_input", "native_guest_melee_input_rejected"}), None)
                    if event:
                        break
                    await asyncio.sleep(.05)
            assert event["event"] == "native_guest_melee_input", event
            proof = event[native_guest_melee.SWING_KEY]
            after = (await command(host, "dock-natural-sample")).split(";")
            geometry = {"host_before": f"{host_eye},{host_direction}", "guest_before": f"{guest_eye},{guest_direction}",
                        "host_contact": await command(host, "dock-melee-contact-view"),
                        "contact": await command(host, "dock-melee-contact"),
                        "target_before": target_before, "target_after": await command(host, "dock-natural-position")}
            assert (proof["map"], proof["pawn"], proof["guid"]) == (arrival["game"]["map"], actor, guid), (proof, geometry)
            if not (native_guest_melee.verified_miss(proof) and after[:4] == baseline[:4]):
                break
            misses.append({"proof": proof, "contact_geometry": geometry, "after": after})
            if attempt < 2:
                guest_point = await command(host, "dock-melee-approach")

        assert proof["target"] == worker and native_guest_melee.verified_damage(proof), (proof, after, misses, geometry)
        assert math.isclose(proof["enemy_health_before"], float(baseline[3]), abs_tol=.001), (proof, baseline)
        assert after[1] == "true" and math.isclose(proof["enemy_health_after"], float(after[3]), abs_tol=.001), (proof, after)
        assert campaign_save_hashes(host.campaign) == saves, "A warehouse melee hit wrote campaign saves"
        return {"tutorial_dock_melee": {"stock_input": tap, "authority_damage": proof, "baseline": baseline, "after": after,
                                        "contact_geometry": geometry, "prior_misses": misses}}
    finally:
        for seat in (host, guest):
            assert await command(seat, "clear-melee-aim") == "cleared"
        assert await command(host, "dock-natural-stop") == "stopped"


async def verify_solo_combat(seat: Seat, slot: str) -> dict:
    bridge, probe = seat.bridge, "portable-npc-override"
    assert await portable_probe_command(bridge, probe, f"solo-load\t{slot}") == "loading stock save"
    await bridge.wait_for(lambda frame: frame["state"] == "ready"
                          and frame.get("pawns", {}).get("local"), timeout=30)
    async with asyncio.timeout(12):
        while (result := await portable_probe_command(bridge, probe, f"solo-combat\t{slot}")) == "loading":
            await asyncio.sleep(.1)
    assert result.startswith("stock inventory and lethal drone damage "), result
    assert bridge.process.poll() is None
    return {"save_slot": slot, "stock_result": result, "pid": bridge.process.pid}


async def verify_dock_enemy_drop(host: Seat, guest: Seat, arrival: dict) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    scope = native_stock_enemy_drops
    checkpoint = campaign_save_hashes(host.campaign)
    owner = arrival["character_id"]

    authority = await wait_resident_status(host_run, lambda frame: frame["state"] == "hosting")
    async def banked_drop():
        return next((row for row in await resident_events(host_run, authority)
                     if row["event"] == "native_stock_enemy_loot_banked"
                     and row.get("source", "").endswith(".NPC_Survivor_Worker_C_2")
                     and row.get("killer") == owner and row.get("settled")), None)

    async with asyncio.timeout(12):
        while (item := await banked_drop()) is None:
            await asyncio.sleep(.1)
    guid = item["guid"]
    identity = native_world_loot.stock_enemy_identity(guid, item.get("visual_id"))
    pawn = authority["game"]["native_guest_save"]["pawn"]
    await wait_resident_status(host_run, lambda frame: identity in frame["game"].get(native_world_loot.KEY, {}))
    approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
    await wait_resident_status(guest_run, lambda frame: identity in frame["game"].get(native_world_loot.KEY, {})
                               and math.dist(frame["game"]["pawns"]["local"]["location"], item["transform"]["position"]) < 225)
    picked = await guest.bridge.command(native_world_loot.FIXTURE, identity, timeout=5)
    assert picked["ack"]["message"] == "stock world loot interaction intercepted", picked["ack"]
    for run in (host_run, guest_run):
        await wait_resident_status(run, lambda frame: guid not in frame.get("gameplay", {}).get(scope.STATE, {})
                                   and identity not in frame["game"].get(native_world_loot.KEY, {}))
    receipt, = [row for row in await resident_events(host_run, authority)
                if row["event"] == "native_stock_enemy_loot_claimed" and row.get("guid") == guid]
    assert receipt["claimed_by"] == owner and receipt["revision"] is None, receipt
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
    assert any(row["guid"] == guid and row["class_path"] == item["class_path"] for row in inventory), inventory
    refused = await submit(host_run, scope.CLAIM, target=guid)
    assert refused.get("error") == "stock_enemy_loot_already_claimed", refused
    assert campaign_save_hashes(host.campaign) == checkpoint, "Stock enemy drop pickup wrote the campaign before a save"
    return {"item": item["class_path"], "stock_source": item["source"], "inventory_guid": guid,
            "pickup_preview": await verified_drop_preview(guest, identity),
            "duplicate": refused["error"], "checkpoint_unchanged": True}


async def tap_dock_primary(host: Seat, guest: Seat, command) -> dict:
    blockers = "unavailable"
    try:
        async with asyncio.timeout(4):
            while True:
                tap = await submit(guest.client / "run", "native-guest-primary-input-fixture")
                if tap["ok"]:
                    return tap
                # This refusal occurs before either stock input wrapper runs.
                assert "native_guest_primary_fixture_scope_unavailable" in tap.get("error", ""), tap
                blockers = await asyncio.gather(*(command(seat, "dock-garage-blockers") for seat in (host, guest)))
                await asyncio.sleep(.06)
    except TimeoutError as error:
        raise AssertionError({"stock_primary_input_gates": blockers}) from error


async def verify_tutorial_dock_combat(host: Seat, guest: Seat, pawn: str, *, retaliation: bool = False,
                                     pursuit: bool = False, held_mask: bool = False, grounded_gait: bool = False,
                                     shot_view: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    for seat in (host, guest):
        for probe in ("portable-dry-fire", "portable-footstep"):
            assert await portable_probe_command(seat.bridge, probe, "disarm") == "disarmed"
    arrival = await travel_pair_to_dock(host, guest, pawn)
    dock = arrival["game"]["map"]

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    async with asyncio.timeout(8):
        while await command(guest, "dock-equip") != "selected":
            await asyncio.sleep(.05)
    frame = await wait_resident_status(guest_run, lambda frame: (
        frame.get("zone_epoch") == arrival["zone_epoch"] and frame.get("state") == "connected"
        and frame.get("gameplay_fresh") and frame.get("game", {}).get("map") == dock
        and frame["game"].get("native_guest_primary_scope", {}).get("kind") == "rusty-pistol"), timeout=12)
    presentation = {}
    if shot_view:
        presentation.update(await verify_dock_npc_population(host, guest, command))
        presentation.update(await verify_dock_garage_shots(host, guest, frame, command))
        presentation.update(await verify_dock_garage_burst(host, guest, frame, command))
        return presentation
    if grounded_gait:
        from portable_gait import verify_stock_gait

        presentation.update(await verify_stock_gait(host, guest, portable_probe_command))
    if held_mask:
        return {**presentation, **await verify_stock_held_mask(host, guest, portable_probe_command)}
    if grounded_gait and not pursuit and not retaliation:
        return presentation
    if pursuit:
        try:
            presentation.update(await verify_stock_held_mask(host, guest, portable_probe_command))
        except (RuntimeError, AssertionError, TimeoutError) as error:
            presentation["stock_held_mask_error"] = str(error) or type(error).__name__
            print(f"stock held mask failed: {presentation['stock_held_mask_error']}", flush=True)
        combat = await verify_dock_pursuit(host, guest, frame, command)
        assert "stock_held_mask_error" not in presentation, (presentation, combat)
        stock_pickup = await verify_dock_enemy_drop(host, guest, frame)
        return {**presentation, "tutorial_dock_pursuit": combat, "stock_enemy_pickup": stock_pickup}
    if retaliation:
        return {**presentation, "tutorial_dock_retaliation": await verify_tutorial_dock_retaliation(host, guest, frame)}
    seen = await asyncio.gather(*(command(seat, "dock-worker") for seat in (host, guest)))
    assert all(row.startswith("true;false;0;") for row in seen), seen
    target = await command(host, "dock-stage")
    point = tuple(map(float, target.split(",")))
    async with asyncio.timeout(8):
        while True:
            view = await command(guest, "dock-worker")
            if view != "absent" and math.dist(point, tuple(map(float, view.split(";")[3].split(",")))) <= 5:
                break
            await asyncio.sleep(.05)
    assert await command(guest, f"dock-aim\t{target}") == "ready"
    await asyncio.sleep(.2)
    assert await command(host, f"dock-aim\t{target}") == "ready"
    started = time.time()
    tap = await tap_dock_primary(host, guest, command)
    sequence = tap["native_guest_primary_fixture"]["sequence"]
    async with asyncio.timeout(8):
        while True:
            matches = [row for row in await resident_events(guest_run, frame) if row.get("sequence") == sequence
                       and row["time_unix"] >= started
                       and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")]
            if matches:
                break
            await asyncio.sleep(.05)
    event, = matches
    assert event["event"] == "native_guest_primary_input", event
    shot = event["native_guest_weapon"]
    assert (shot["map"], shot["before"], shot["after"], shot["projectiles"]) == (dock, 7, 6, 1), event
    async with asyncio.timeout(10):
        while True:
            result = await asyncio.gather(*(command(seat, "dock-worker") for seat in (host, guest)))
            if all(row.startswith("false;true;1;") for row in result):
                break
            await asyncio.sleep(.05)
    assert await command(guest, "dock-strike") == "0", "the simulated worker executed its own strike"
    started = time.time()
    host_hp, before, after, attacker = (await command(host, "dock-strike")).split(";")
    async with asyncio.timeout(8):
        while True:
            injuries = [row for row in await resident_events(guest_run, frame)
                        if row["event"] == "native_guest_hurt_played" and row["time_unix"] >= started]
            if injuries:
                break
            await asyncio.sleep(.05)
    hurt, = injuries
    assert hurt["damage"] > 0 and math.isclose(float(before) - float(after), hurt["damage"], abs_tol=1e-5), hurt
    for seat in (host, guest):
        assert await command(seat, "dock-arm-swing") == "ready"
    try:
        swing_started = time.time()
        clip = await command(host, "dock-play-swing")
        source = await command(host, "dock-read-swing")
        assert source == f"1;1.100;true;{clip}", source
        async with asyncio.timeout(5):
            while (observer := await command(guest, "dock-read-swing")).startswith("0;"):
                await asyncio.sleep(.05)
        assert observer == source, (source, observer)
        async with asyncio.timeout(5):
            while True:
                receipts = [row for row in await resident_events(guest_run, frame)
                            if row["event"] == "native_npc_melee_received" and row["time_unix"] >= swing_started
                            and row["clip"] == clip.split(" ", 1)[1]]
                if receipts:
                    break
                await asyncio.sleep(.05)
        received, = receipts
    finally:
        for seat in (host, guest):
            assert await command(seat, "dock-disarm-swing") == "disarmed"
    return {"tutorial_dock_combat": {"before": seen, "native_shot": shot, "both_peers_dead_and_ragdolled": result,
                                     "stock_melee": {"attacker": attacker, "host_health": float(host_hp),
                                                     "guest_before": float(before), "guest_after": float(after),
                                                     "guest_hurt_cue": hurt, "replica_strike_denied": True,
                                                     "retargeted_when_guest_downed": True,
                                                     "downed_aggro_and_queued_strike_denied": True},
                                     "stock_swing": {"host": source, "guest": observer, "received": received}}}

async def verify_dock_npc_population(host: Seat, guest: Seat, command) -> dict:
    async def wait_for_population(seat, points, expected):
        observed = "unavailable"
        try:
            async with asyncio.timeout(7):
                while (observed := await command(seat, f"dock-population-at\t{points}")) != expected:
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(f"Expected {expected} at {points}, saw {observed}") from error

    async def guest_weapons_clear():
        observed = "unavailable"
        try:
            async with asyncio.timeout(6):
                while (observed := await command(guest, "dock-population-weapons")) != "empty":
                    await asyncio.sleep(.08)
        except TimeoutError as error:
            details = await command(guest, "dock-population-weapon-details")
            raise AssertionError(f"Stock weapons outlived their NPCs on the guest: {observed}. {details}") from error

    try:
        local_points = await command(guest, "dock-population-spawn")
        await wait_for_population(guest, local_points, "empty")
        await wait_for_population(host, local_points, "empty")
        await guest_weapons_clear()
        authoritative_points = await command(host, "dock-population-spawn")
        await wait_for_population(host, authoritative_points, "bandit=3,state=3")
        await wait_for_population(guest, authoritative_points, "bandit=1,state=1")
        paths = await asyncio.gather(*(command(seat, f"dock-population-paths\t{authoritative_points}")
                                       for seat in (host, guest)))
        staged = int(await command(guest, f"dock-population-arm-view\t{authoritative_points}"))
        assert await command(guest, "dock-population-weapons") == "bandit,state"
        assert await command(host, "dock-population-weapons") == "bandit,state"
        assert await command(host, "dock-population-cleanup") == "cleared"
        await wait_for_population(guest, authoritative_points, "empty")
        await guest_weapons_clear()
        return {"tutorial_dock_npc_population": {"client_only_retired": 2, "host_authorities": 2,
                "guest_replicas": 2, "host_removal_seen": True, "client_only_weapons_retired": 2,
                "replica_weapons_retired": 2, "stock_equip_calls_on_npc_replicas": staged,
                "actor_names": dict(zip(("host", "guest"), paths))}}
    finally:
        assert await asyncio.gather(command(host, "dock-population-cleanup"), command(guest, "dock-population-cleanup")) == [
            "cleared", "cleared",
        ]


async def verify_dock_garage_shots(host: Seat, guest: Seat, frame: dict, command, *,
                                   pellets: int = 1, family: str | None = None) -> dict:
    try:
        assert await asyncio.gather(*(command(seat, "dock-garage-open") for seat in (host, guest))) == ["raised", "raised"]
        evidence = await compare_dock_garage_shots(host, guest, frame, command, pellets=pellets, family=family)
    finally:
        restored = await asyncio.gather(*(command(seat, "dock-garage-restore") for seat in (host, guest)),
                                         return_exceptions=True)
    assert restored == ["restored", "restored"], restored
    return evidence


async def compare_dock_garage_shots(host: Seat, guest: Seat, frame: dict, command, *,
                                    pellets: int, family: str | None) -> dict:
    guest_run = guest.client / "run"
    stage = f"dock-garage-stage-dynamic\t{family}" if family else "dock-garage-stage"
    low, worker, high, obstruction, health = (await command(host, stage)).split(";")
    contact_name = ":PersistentLevel.NPC_Survivor_Worker_C_2"
    actor_names = None
    if family:
        async with asyncio.timeout(8):
            while True:
                names = await asyncio.gather(*(command(seat, f"dock-garage-dynamic-name\t{worker};{family}")
                                               for seat in (host, guest)))
                references = [name.split(";") for name in names]
                if all(len(entry) == 2 and entry[1].isdecimal() for entry in references):
                    break
                await asyncio.sleep(.08)
        (host_name, reference), (guest_name, guest_reference) = references
        assert host_name != guest_name and reference == guest_reference, references
        contact_name = f"npc:{reference}"
        actor_names = {"host": host_name, "guest": guest_name, "shared": contact_name}
        health = await command(host, "dock-garage-health")
    health = float(health)
    above = f"dock-garage-aim\t{high};{obstruction}"
    below = f"dock-garage-aim\t{low};{worker}" + (f";{family}" if family else "")

    async def shoot(expected_health, *, should_hit, contact_required=False):
        started = time.time()
        tap = await tap_dock_primary(host, guest, command)
        sequence = tap["native_guest_primary_fixture"]["sequence"]
        async with asyncio.timeout(8):
            while not (events := [row for row in await resident_events(guest_run, frame)
                                  if row.get("sequence") == sequence and row["time_unix"] >= started
                                  and row["event"] in ("native_guest_primary_input", "native_guest_primary_input_rejected")]):
                await asyncio.sleep(.05)
        event, = events
        assert event["event"] == "native_guest_primary_input", event
        shot = event["native_guest_weapon"]
        assert shot["after"] == shot["before"] - 1 and shot["projectiles"] == pellets, event
        if contact_required:
            contact = event.get("native_guest_trigger_view", {}).get("contact", {})
            assert contact.get("actor", "").endswith(contact_name), event
        try:
            async with asyncio.timeout(6):
                while True:
                    observed = float(await command(host, "dock-garage-health"))
                    if should_hit is not True or observed < expected_health:
                        break
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            witness = await command(host, "dock-garage-shot-witness")
            raise AssertionError(dict(host_projectile=witness, shot=event, worker_health=expected_health)) from error
        if should_hit is False:
            await asyncio.sleep(.35)
            observed = float(await command(host, "dock-garage-health"))
            assert observed == expected_health, dict(roof_contact_damaged_worker=observed, event=event)
        return event, observed

    seen = "unavailable"
    try:
        async with asyncio.timeout(7):
            while (seen := await command(guest, below)) != "worker":
                await asyncio.sleep(.05)
    except TimeoutError as error:
        raise AssertionError(dict(guest_low_trace=seen, origin=low, target=worker)) from error
    assert await command(host, above) == "blocked"
    assert await command(guest, above) == "blocked"
    blocked, after_blocked = (None, health) if family in ("drone", "turret") else await shoot(health, should_hit=False)
    assert await command(host, above) == "blocked"
    assert await command(guest, below) == "worker"
    evidence = {}
    before_mismatch = after_blocked
    if pellets == 1:
        clear, before_mismatch = await shoot(after_blocked, should_hit=True)
        for event, expected in ((blocked, high), (clear, low)):
            geometry = event["native_guest_trigger_view"]
            assert math.dist(tuple(map(float, geometry["eye"].split(","))), tuple(map(float, expected.split(",")))) < 10, event
        evidence["tutorial_dock_shot_view"] = {"host_camera_line": "blocked", "clear_guest_line": "worker",
            "health": [health, after_blocked, before_mismatch], "blocked": blocked, "clear": clear}
    assert await command(host, "dock-garage-lower-host") == "lowered"
    assert await command(host, below) == "blocked"
    assert await command(guest, below) == "worker"
    second_barrier = await command(host, f"dock-garage-second-barrier\t{low};{worker}") if pellets == 1 else None
    guest_collision = await command(guest, f"dock-garage-projectile-trace\t{low};{worker}")
    host_collision = await command(host, f"dock-garage-projectile-trace\t{low};{worker}")
    expected_guest_collision = actor_names["guest"] if actor_names else "NPC_Survivor_Worker_C_2"
    assert guest_collision == expected_guest_collision and host_collision == "StaticMeshActor_347", (
        guest_collision, host_collision)
    if family == "turret":
        async def turret(action):
            return await asyncio.gather(*(command(seat, f"dock-garage-turret-{action}\t{actor_names[name]}")
                                          for seat, name in ((host, "host"), (guest, "guest"))))

        assert await turret("view") == ["true,false,false,false"] * 2
        assert await turret("arm") == ["armed"] * 2
    receipt, after_disagreement = await shoot(before_mismatch, should_hit=True, contact_required=True)
    key = {None: "tutorial_dock_world_collision_mismatch", "bandit": "tutorial_dock_buckshot_mismatch",
           "drone": "tutorial_dock_drone_mismatch", "turret": "tutorial_dock_turret_mismatch"}[family]
    evidence[key] = {
        "same_low_aim": True, "host_world_line": "blocked", "guest_world_line": "worker",
        "health": [before_mismatch, after_disagreement], "blocked_shot": blocked, "accepted_shot": receipt,
    }
    if actor_names:
        evidence[key]["actor_names"] = actor_names
        if family == "turret":
            shots = [receipt]
            while after_disagreement > 0 and shots[-1]["native_guest_weapon"]["after"] > 0:
                target = await command(guest, f"dock-garage-turret-aimpoint\t{actor_names['guest']}")
                approach = f"dock-garage-aim\t{low};{target};turret"
                lanes = [await command(host, approach), await command(guest, approach)]
                assert lanes == ["blocked", "worker"], {"lanes": lanes, "target": target, "health": after_disagreement}
                followup, after_disagreement = await shoot(after_disagreement, should_hit=True, contact_required=True)
                shots.append(followup)
                evidence[key]["health"].append(after_disagreement)
            assert after_disagreement <= 0, evidence[key]["health"]
            evidence[key]["shots"] = shots
            async with asyncio.timeout(5):
                while (views := await turret("view")) != ["false,true,true,true"] * 2:
                    await asyncio.sleep(.05)
            evidence[key]["turret_views"] = dict(zip(("host", "guest"), views))
            async with asyncio.timeout(5):
                while True:
                    effects = await turret("effects")
                    if [row.split(";")[0] for row in effects] == ["1,1,1,1", "1,1,1,0"]:
                        break
                    await asyncio.sleep(.05)
            evidence[key]["turret_effects"] = dict(zip(("host", "guest"), effects))
        if after_disagreement <= 0:
            async with asyncio.timeout(5):
                while True:
                    deaths = await asyncio.gather(*(command(seat, f"dock-garage-dynamic-death\t{actor_names[name]}")
                                                    for seat, name in ((host, "host"), (guest, "guest"))))
                    if all(state in ("dead", "removed") for state in deaths):
                        break
                    await asyncio.sleep(.05)
            evidence[key]["death"] = dict(zip(("host", "guest"), deaths))
    if second_barrier:
        witness = await command(host, "dock-garage-shot-witness")
        exclusions = witness.rsplit(";", 1)[-1].split(",")
        assert second_barrier in exclusions and host_collision in exclusions, (second_barrier, witness)
        evidence[key]["host_only_barriers"] = [host_collision, second_barrier]
    if pellets == 1:
        eye, target, barrier, distant_health = (await command(host, "dock-garage-long-stage")).split(";")
        distant_health = float(distant_health)
        aim = f"dock-garage-aim\t{eye};{target}"
        assert await asyncio.gather(*(command(seat, f"dock-garage-long-block\t{barrier}") for seat in (host, guest))) == [
            "blocked", "blocked",
        ]
        assert await command(host, aim) == await command(guest, aim) == "blocked"
        blocked, after_blocked = await shoot(distant_health, should_hit=False)
        assert not blocked["native_guest_trigger_view"].get("contact"), blocked
        assert await command(guest, f"dock-garage-long-clear\t{barrier}") == "clear"
        observed = "unavailable"
        try:
            async with asyncio.timeout(8):
                while (observed := await command(guest, aim)) != "worker":
                    await asyncio.sleep(.05)
        except TimeoutError as error:
            raise AssertionError(dict(distant_guest_trace=observed, firing_position=eye, worker_position=target)) from error
        assert await command(host, aim) == "blocked"
        assert await command(host, f"dock-garage-projectile-trace\t{eye};{target}") == host_collision
        accepted, after_hit = await shoot(after_blocked, should_hit=True, contact_required=True)
        geometry = accepted["native_guest_trigger_view"]
        distance = math.dist(tuple(map(float, geometry["muzzle"].split(","))),
                             tuple(map(float, geometry["contact"]["point"].split(","))))
        assert distance > 5000 and after_hit > 0, (distance, after_hit, accepted)
        witness = await command(host, "dock-garage-shot-witness")
        assert host_collision in witness.rsplit(";", 1)[-1].split(","), witness
        evidence["tutorial_dock_distant_contact"] = {
            "meters": round(distance / 100, 2), "health": [distant_health, after_blocked, after_hit],
            "blocked_shot": blocked, "accepted_shot": accepted, "host_only_barrier": host_collision,
        }

        board = "Dest_WoodPlank_BP_C_1"
        stage = f"dock-garage-plank\t{eye};{target}"
        host_camera, host_muzzle, _, board_health = (await command(host, f"{stage};in")).split(";")
        guest_camera, guest_muzzle, _, _ = (await command(guest, f"{stage};out")).split(";")
        assert (host_camera, host_muzzle) == ("blocked", host_collision), (host_camera, host_muzzle)
        assert (guest_camera, guest_muzzle) == ("worker", "NPC_Survivor_Worker_C_2"), (guest_camera, guest_muzzle)
        refused, protected_health = await shoot(after_hit, should_hit=False, contact_required=True)
        exclusions = (await command(host, "dock-garage-shot-witness")).rsplit(";", 1)[-1].split(",")
        assert host_collision not in exclusions and board not in exclusions, (exclusions, refused)
        assert float(await command(host, "dock-garage-plank-health")) == float(board_health)
        assert await command(host, "dock-garage-plank-impact") == "0;-"

        guest_camera, guest_muzzle, _, _ = (await command(guest, f"{stage};in")).split(";")
        assert (guest_camera, guest_muzzle) == ("worker", board), (guest_camera, guest_muzzle)
        assert (await command(host, f"{stage};in")).split(";")[:2] == ["blocked", host_collision]
        accepted, _ = await shoot(protected_health, should_hit=None)
        contact = accepted["native_guest_trigger_view"].get("contact", {})
        assert contact.get("actor", "").endswith(f":PersistentLevel.{board}"), accepted
        exclusions = (await command(host, "dock-garage-shot-witness")).rsplit(";", 1)[-1].split(",")
        assert host_collision in exclusions and board not in exclusions, (exclusions, accepted)
        async with asyncio.timeout(3):
            while (impact := await command(host, "dock-garage-plank-impact")) == "0;-":
                await asyncio.sleep(.05)
        hits, kinetic, piercing, breacher, threshold, ballistic, armor = impact.split(";")
        board_after = float(await command(host, "dock-garage-plank-health"))
        stock_damage = (float(kinetic) if ballistic == "true" else 0) + (float(piercing) if armor == "true" else 0)
        remaining = float(board_health) - stock_damage if breacher == "true" or stock_damage >= float(threshold) else float(board_health)
        assert hits == "1" and math.isclose(board_after, remaining, abs_tol=1e-4), (impact, board_health, board_after)
        evidence["tutorial_dock_damageable_contact"] = {
            "camera_target": "NPC_Survivor_Worker_C_2", "muzzle_target": board,
            "blocked_shot": refused, "accepted_shot": accepted,
            "board_health_before": float(board_health), "board_health_after": board_after,
            "stock_impact": {"hits": int(hits), "damage": stock_damage, "threshold": float(threshold),
                             "breacher": breacher == "true"},
        }
    return evidence


async def verify_dock_garage_buckshot(host: Seat, guest: Seat, pawn: str) -> dict:
    arrival = await travel_pair_to_dock(host, guest, pawn)
    guest_run = guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)

    async def weapon(seat, action):
        return await portable_probe_command(seat.bridge, "portable-dry-fire", action)

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-npc-override", action)

    seats = host, guest
    try:
        assert await asyncio.gather(*(weapon(seat, "arm-buckshot") for seat in seats)) == ["armed", "armed"]
        guid = await weapon(host, "seed-dock-buckshot")
        async with asyncio.timeout(8):
            while await weapon(guest, "projected") != guid:
                await asyncio.sleep(.05)
        assert await weapon(guest, "equip") == "selected"
        frame = await wait_resident_status(guest_run, lambda row:
            row.get("zone_epoch") == arrival["zone_epoch"] and
            row.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        result = {}
        failures = {}
        for family in ("bandit", "drone", "turret"):
            try:
                result.update(await verify_dock_garage_shots(host, guest, frame, command, pellets=9, family=family))
            except Exception as error:
                failures[family] = traceback.format_exception_only(type(error), error)[-1].strip() or type(error).__name__
        assert not failures, (failures, list(result))
        turret_shots = result["tutorial_dock_turret_mismatch"]["shots"]
        ammo = turret_shots[-1]["native_guest_weapon"]["after"]
        assert ammo == 7 - (3 + len(turret_shots)), turret_shots
        async with asyncio.timeout(6):
            while await weapon(host, "peer-ready") != f"{guid},{ammo},{ammo}" or await weapon(guest, "ready") != f"{guid},{ammo},{ammo}":
                await asyncio.sleep(.05)
        assert campaign_save_hashes(host.campaign) == checkpoint
        return result
    finally:
        assert await asyncio.gather(*(weapon(seat, "disarm") for seat in seats)) == ["disarmed", "disarmed"]


async def verify_dock_garage_burst(host: Seat, guest: Seat, dock_frame: dict, command) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    seats = (host, guest)

    async def weapon(seat, action):
        return await portable_probe_command(seat.bridge, "portable-dry-fire", action)

    async def guest_route(expected, approach):
        async with asyncio.timeout(7):
            while await command(guest, approach) != expected:
                await asyncio.sleep(.05)

    try:
        assert await asyncio.gather(*(weapon(seat, "arm-light-ar") for seat in seats)) == ["armed", "armed"]
        guid = await weapon(host, "seed-dock-light-ar")
        async with asyncio.timeout(8):
            while await weapon(guest, "projected") != guid:
                await asyncio.sleep(.05)
        assert await weapon(guest, "equip") == "selected"
        guest_frame = await wait_resident_status(guest_run, lambda row:
            row.get("zone_epoch") == dock_frame["zone_epoch"] and
            row.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        assert await weapon(guest, "light-ar-auto") == "light-ar-auto"
        host_frame = await wait_resident_status(host_run, lambda row: any(
            item.get("guid") == guid and item.get("fire_mode") == "auto"
            for item in row.get("game", {}).get("native_guest_equipped_weapons", {}).values()))
        before = campaign_save_hashes(host.campaign)
        assert await asyncio.gather(*(command(seat, "dock-garage-open") for seat in seats)) == ["raised", "raised"]
        low, worker, high, obstruction, health = (await command(host, "dock-garage-stage")).split(";")
        above, below = f"dock-garage-aim\t{high};{obstruction}", f"dock-garage-aim\t{low};{worker}"
        assert await command(host, above) == "blocked"
        assert await command(host, "dock-garage-lower-host") == "lowered"
        assert await command(host, f"dock-garage-projectile-trace\t{low};{worker}") == "StaticMeshActor_347"
        await guest_route("blocked", above)

        start = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=550)
        assert start["ok"], start
        stroke = start["native_guest_held_fire_fixture"]["stroke"]
        async with asyncio.timeout(3):
            while True:
                events = await resident_events(guest_run, guest_frame)
                if any(event.get("stroke") == stroke and event.get("edge") == "press"
                       and event["event"] == "native_guest_held_fire_input" for event in events):
                    break
                await asyncio.sleep(.015)
        blocked_health = float(await command(host, "dock-garage-health"))
        assert math.isclose(blocked_health, float(health), rel_tol=0, abs_tol=1e-6), (blocked_health, health)
        assert await command(guest, below) == "worker"
        async with asyncio.timeout(6):
            while not (terminal := next((event for event in await resident_events(host_run, host_frame)
                            if event["event"] == "native_guest_smg_hold_accepted" and event.get("guid") == guid), None)):
                await asyncio.sleep(.05)
        after_health = float(await command(host, "dock-garage-health"))
        witness = await command(host, "dock-garage-shot-witness")
        assert after_health < blocked_health, (blocked_health, after_health, terminal, witness)
        assert terminal["status"] == "released" and 1 < terminal["rounds"] == terminal["projectiles"], terminal
        assert terminal["before"] - terminal["after"] == terminal["rounds"], terminal
        after_ammo = f"{guid},{terminal['after']},{terminal['after']}"
        async with asyncio.timeout(5):
            while await weapon(host, "peer-ready") != after_ammo or await weapon(guest, "ready") != after_ammo:
                await asyncio.sleep(.05)
        assert campaign_save_hashes(host.campaign) == before
        return {"tutorial_dock_held_shot_view": {"health": [float(health), blocked_health, after_health],
                "host_world_line": "blocked", "clear_guest_line": "worker", "burst": terminal}}
    finally:
        restored = await asyncio.gather(*(command(seat, "dock-garage-restore") for seat in seats), return_exceptions=True)
        disarmed = await asyncio.gather(*(weapon(seat, "disarm") for seat in seats), return_exceptions=True)
        assert restored == ["restored", "restored"] and disarmed == ["disarmed", "disarmed"], (restored, disarmed)


async def capture_resident_weapon_cues(host: Seat, guest: Seat, pawn: str, *, guest_combat: bool,
                                       memory_queue: bool,
                                      rusty: bool, npc_rivalry: bool, pursuit: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_frame, guest_frame = await asyncio.gather(
        wait_resident_status(host_run, lambda frame: frame.get("local_bridge") == "memory"),
        wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory"
                             and any(avatar["state"] in {"stock_body", "default_body"} and avatar.get("player_id") is not None
                                     for avatar in frame.get("game", {}).get("native_player_avatars", {}).values())),
    )
    assert host_frame["game"]["native_guest_save"]["pawn"] == pawn
    item = native_guest_consumable.BANDAGE_CLASS
    event_names = ("native_avatar_use_sound_played", "native_avatar_use_visual_played")
    campaign = host.campaign / "save"
    cloud = host.profile / "Saved/SaveGames" / FOLDER

    def save_state():
        native = (path for seat in (host, guest) for path in (seat.profile / "Saved/SaveGames").rglob("*.sav")
                  if not path.is_relative_to(seat.profile / "Saved/SaveGames" / FOLDER))
        return {path: (fingerprint(path), path.stat().st_mtime_ns) for path in (*campaign.glob("campaign*.json"), *native)}

    def use_events(events):
        return [row for row in events if row["event"] in event_names and row["generation"] == guest_frame["generation"]
                and row["item_class"] == item]

    assert not weapon_cue_files(host, guest), "A weapon cue mailbox already exists"
    assert not use_events(await resident_events(guest_run, guest_frame)), "The observer already has a Bandage cue"
    before = save_state()
    assert campaign / "campaign.json" in before
    backup_before = {path: fingerprint(path) for path in cloud.glob("SFCoopBackup-*.sav")}
    checkpoint = json.loads((campaign / "campaign.json").read_bytes())
    result = await portable_probe_command(host.bridge, "avatar-probe", "consume\n")
    player_id, class_name = result.strip().split("\t")
    player_id = int(player_id)
    assert class_name == f"BlueprintGeneratedClass {item}", result

    def heard_use(frame):
        game = frame.get("game", {})
        for remote, avatar in game.get("native_player_avatars", {}).items():
            if avatar.get("player_id") == player_id:
                use = game.get("native_avatar_use", {}).get(remote, {})
                if use.get("item") == item and use.get("active") is False and use["hand_motion_cm"] > 20:
                    return use
        return None

    observed = await wait_resident_status(guest_run, heard_use, timeout=12)
    prop = heard_use(observed)
    assert prop["mesh"].endswith(".SM_Prop_Bandage_02"), prop
    cues = use_events(await resident_events(guest_run, observed))
    assert {entry["event"] for entry in cues} == set(event_names) and len(cues) == 2, cues
    assert {entry["serial"] for entry in cues} == {prop["serial"]}, (cues, prop)
    visual, = [entry for entry in cues if entry["event"] == event_names[1]]
    assert visual["player_id"] == player_id, visual
    guest_melee = await verify_resident_guest_melee(host, guest) if guest_combat and not rusty else {}
    footsteps = await verify_resident_footsteps(host, guest)
    pistol = await verify_resident_pistol(host, guest, guest_combat=guest_combat, rusty=rusty, memory_queue=memory_queue,
                                         pursuit=pursuit)
    npc_shots = await verify_resident_npc_shots(host, guest_run, guest_frame) if guest_combat else []
    npc_kill = await verify_resident_npc_kill(host, guest) if guest_combat and not npc_rivalry else {}
    turret_death = await verify_resident_turret_combat(host, guest, guest_frame) if guest_combat else {}
    assert not weapon_cue_files(host, guest), "Stock player cues wrote a legacy weapon cue mailbox"
    after = save_state()
    changed = [path for path in before.keys() | after.keys() if before.get(path) != after.get(path)]
    assert not changed, f"Campaign saves changed during stock player actions: {changed}"
    # An asynchronous Auto-Cloud copy may catch up with the preceding enrollment checkpoint.
    backup_revisions = []
    for path in cloud.glob("SFCoopBackup-*.sav"):
        if fingerprint(path) != backup_before.get(path):
            with ZipFile(path) as archive:
                backed_up = json.loads(archive.read("campaign.json"))
            assert backed_up == checkpoint, "The cloud mirror backed up a new checkpoint during stock player actions"
            backup_revisions.append(backed_up["revision"])
    return {"resident_weapon_cues": {"stock_player_id": player_id, "source": class_name, "observer_cues": cues,
                                     "prop": prop, "local_bridge_both_peers": "memory",
                                     "no_cue_mailbox": True, "saves_unchanged_before_checkpoint": True,
                                     "previous_checkpoint_cloud_copies": backup_revisions,
                                     "stock_footsteps": footsteps, "stock_pistol": pistol, "npc_shots": npc_shots, "npc_kill": npc_kill,
                                     "turret_death": turret_death, "guest_melee": guest_melee}}


async def verify_resident_weapon_cues(host: Seat, guest: Seat, pawn: str, *, guest_combat: bool = False,
                                      rusty: bool = False, npc_rivalry: bool = False, memory_queue: bool = False,
                                      pursuit: bool = False) -> dict:
    probes = [("portable-dry-fire", "disarm"), ("portable-footstep", "disarm")]
    if guest_combat:
        probes.append(("portable-npc-override", "disarm-weapon-observers"))
    try:
        evidence = await capture_resident_weapon_cues(host, guest, pawn, guest_combat=guest_combat,
                                                      rusty=rusty, npc_rivalry=npc_rivalry, memory_queue=memory_queue,
                                                      pursuit=pursuit)
    finally:
        # The installed probes stay registered when the same process loads another map.
        replies = await asyncio.gather(*(portable_probe_command(seat.bridge, probe, action)
                                         for seat in (host, guest) for probe, action in probes))
        assert all(reply == "disarmed" for reply in replies), replies
    evidence["resident_weapon_cues"]["observer_scopes_disarmed"] = True
    return evidence


async def verify_stock_lockpicks(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    host_inventory = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    proofs = []
    for kind, units in (("lockpick", 2), ("heavy-lockpick", 3)):
        seeded = await host.bridge.command("fixture-stock-take-ammo-seed", pawn, kind, str(units))
        guid = seeded["ack"]["message"]
        frame = await wait_resident_status(guest_run, lambda f: f.get("state") == "connected")
        async with asyncio.timeout(10):
            while True:
                observed = await guest.bridge.command("fixture-stock-take-ammo-backpack", guid, update_status=False)
                if observed["ack"]["message"] == str(units):
                    break
                await asyncio.sleep(.1)
        # The stock fixture opens Tab and uses the item's stock Equip menu.
        equipped = await submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=guid)
        assert equipped["ok"], equipped
        slot = equipped[native_inventory.STORE_KEY]["slot"]
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
        item, = [row for row in inventory if row["guid"] == guid]
        assert item["source"] == "equipment" and item["index"] == slot and item["quantity"] == units, item
        state = (guest.bridge.snapshot() or {}).get("native_inventory_view", {}).get("state")
        if state == "stock":
            assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
            await wait_resident_status(guest_run, lambda f:
                f.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
        selected = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, "equip")
        assert selected["ack"]["message"] == f"{units},{units}", selected
        async with asyncio.timeout(8):
            while True:
                held = next((row["native_guest_weapon"] for row in await resident_events(guest_run, frame)
                             if row["event"] == "native_guest_equipment_selected"
                             and row["native_guest_weapon"]["guid"] == guid), None)
                if held:
                    break
                await asyncio.sleep(.08)
        assert held["actor_ammo"] == held["stored_ammo"] == units, held
        proofs.append({"class_path": item["class_path"], "slot": slot, "units": units})
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_inventory
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_lockpicks": proofs}


async def verify_stock_avatar_binoculars(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def command(seat, action):
        return (await portable_probe_command(seat.bridge, "avatar-probe", action + "binoculars\n")).strip()

    async def view(seat, phase):
        async with asyncio.timeout(8):
            while True:
                reply = await command(seat, "observe")
                if reply.split("\t")[0] == phase:
                    return reply.split("\t")
                await asyncio.sleep(.12)

    checkpoint = campaign_save_hashes(host.campaign)
    assert await command(host, "seed") == "seeded"
    outcomes = {}
    for label, owner, observer in (("host", host, guest), ("guest", guest, host)):
        async with asyncio.timeout(10):
            while True:
                try:
                    assert await command(owner, "select") == "selected"
                    break
                except AssertionError as error:
                    if str(error).strip() != "avatar_probe_rig_pending":
                        raise
                    await asyncio.sleep(.25)
        lowered = await view(observer, "lowered")
        assert await command(owner, "raise") == "input"
        raised = await view(observer, "raised")
        assert await command(owner, "lower") == "input"
        returned = await view(observer, "lowered")
        assert lowered[1] == raised[1] == returned[1], (lowered, raised, returned)
        assert lowered[2] == returned[2] == "Weapon_Socket01"
        assert 30 <= float(raised[3]) <= 40 and raised[2] == "None", raised
        before, up, after = (tuple(map(int, report[4:6])) for report in (lowered, raised, returned))
        assert up == (before[0] + 1, before[1]) and after == (up[0], up[1] + 1), (before, up, after)
        outcomes[label] = {"retained_mesh": raised[1], "raised_offset_cm": float(raised[3]), "scope_cues": 2}
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_avatar_binoculars": outcomes}


async def verify_stock_avatar_tail(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def command(seat, action):
        return (await portable_probe_command(seat.bridge, "avatar-probe", action + "\n")).strip()

    async def observe(seat, predicate):
        async with asyncio.timeout(12):
            while True:
                try:
                    row = list(map(float, (await command(seat, "rig")).split("\t")))
                except AssertionError as error:
                    if not str(error).rstrip().endswith("avatar_probe_rig_pending"):
                        raise
                else:
                    if predicate(row):
                        assert row[0] < .01 and row[1] < 3 and row[2] < .15, row
                        assert row[12] < (5 if row[11] else .15), row
                        return {"yaw": row[9], "airborne": bool(row[10]), "long_tail": bool(row[11]),
                                "seam_distance_cm": row[12], "body_offset_cm": row[1]}
                await asyncio.sleep(.025)

    await command(host, "style")
    initial, docked = await asyncio.gather(observe(guest, lambda row: row[11] == 1 and row[10] == 0),
                                          observe(host, lambda row: row[11] == 0 and row[10] == 0))
    await command(host, "turn")
    turned = await observe(guest, lambda row: abs((row[9] - initial["yaw"] + 180) % 360 - 180) > 60)
    await command(host, "jump")
    airborne = await observe(guest, lambda row: row[10] == 1)
    landed = await observe(guest, lambda row: row[10] == 0)
    return {"stock_avatar_tail": {"armored": initial, "docked": docked, "turned": turned,
                                  "airborne": airborne, "landed": landed}}


async def verify_stock_take_ammo(host: Seat, guest: Seat, pawn: str, *, shotguns: bool = False,
                                 impact_cues: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_inventory = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    checkpoint = campaign_save_hashes(host.campaign)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected")
    proofs = []
    previous_equipped = None

    async def start_input(name):
        requested = await submit(guest_run, f"native-guest-{name}-input-fixture")
        assert requested["ok"], requested
        return requested[f"native_guest_{name}_fixture"]["sequence"]

    async def wait_input(name, sequence, count):
        event = f"native_guest_{name}_input"
        async with asyncio.timeout(10):
            while True:
                rows = [row for row in await resident_events(guest_run, guest_frame)
                        if row.get("sequence") == sequence and row["event"] in (event, f"{event}_rejected")]
                assert all(row["event"] == event for row in rows), rows
                if len(rows) == count:
                    return [row["native_guest_weapon"] for row in rows]
                assert len(rows) < count, rows
                await asyncio.sleep(.025)

    async def stock_input(name, count=1):
        return await wait_input(name, await start_input(name), count)

    async def impact_probe(seat, action):
        return await portable_probe_command(seat.bridge, "portable-impact", action)

    async def verify_decals(started, pellet_count):
        async with asyncio.timeout(8):
            while True:
                host_rows, guest_rows = await asyncio.gather(impact_probe(host, "decals"), impact_probe(guest, "decals"))
                host_marks = host_rows.split("|") if host_rows else []
                guest_marks = guest_rows.split("|") if guest_rows else []
                played = [event for event in await resident_events(guest_run, guest_frame)
                          if event["time_unix"] >= started and event["event"] == "native_bullet_decal_played"]
                if len(guest_marks) >= pellet_count and len(played) >= pellet_count:
                    assert len(host_marks) == len(guest_marks) == len(played) == pellet_count
                    assert len(set(host_marks)) == pellet_count and sorted(host_marks) == sorted(guest_marks), (
                        host_marks, guest_marks)
                    return {"host": host_marks, "guest": guest_marks, "pellets": pellet_count}
                await asyncio.sleep(.08)

    profiles = {profile.kind: profile.magazine for profile in FIREARMS}
    cases = (("revolver-9mm", "backpack", None), ("break-action-50", "stored_equipment", None),
             ("obrez", "backpack", None), ("obrez", "equipped", THIRTY_06_INCENDIARY),
             ("revolver-9mm", "equipped", None), ("heavy-pistol-silenced", "equipped", None),
             ("rifle-50-semi", "equipped", None), ("type33-scoped", "equipped", None))
    if shotguns:
        kinds = ("pump-shotgun", "sawn-off-shotgun", "sg-71-b", "bolt-action-shotgun-suppressed",
                 "bolt-action-shotgun", "cult-shotgun")
        cases = (("full-auto-shotgun-02", "equipped", None), *(
            (kind, "equipped", alternate) for kind, alternate in zip(kinds, (None, *SHOTGUN_AMMO[1:]), strict=True)))
    if impact_cues:
        cases = (("bolt-action-shotgun-suppressed", "equipped", None),)
    for kind, source, alternate in cases:
        seed_started = time.time()
        tube = profiles[kind] and profiles[kind].single_reload
        automatic = kind == "full-auto-shotgun-02"
        starting_rounds = (profiles[kind].capacity if automatic else 4 if kind == "pump-shotgun" else 3 if tube else 1)
        seeded = await host.bridge.command("fixture-stock-take-ammo-seed", pawn, kind, str(starting_rounds))
        guid = seeded["ack"]["message"]
        assert re.fullmatch(r"[0-9A-F]{32}", guid), seeded["ack"]
        before = (await submit(guest_run, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert before["status"] == "backpack" and before["stored_ammo"] > 0, before
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert any(row["guid"] == guid and row["source"] == "backpack" for row in inventory["items"])
        async with asyncio.timeout(8):
            while not any(entry["event"] == "native_guest_ui_projected" and entry["items"] == len(inventory["items"])
                          and entry["time_unix"] >= seed_started for entry in await resident_events(guest_run, guest_frame)):
                await asyncio.sleep(.1)
        if source != "backpack":
            moved = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=guid)
            assert moved["ok"], moved
            slot = moved[native_inventory.STORE_KEY]["slot"]

            async def read_loadout(action="read"):
                # Resident gameplay can replace the portable file bridge acknowledgement.
                # Selecting the exact already-held GUID is idempotent in the probe.
                try:
                    read = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, action,
                                                      timeout=1.5)
                except TimeoutError:
                    return "waiting"
                return read["ack"]["message"]

            async with asyncio.timeout(8):
                while await read_loadout() == "waiting":
                    await asyncio.sleep(.15)
            if source == "equipped":
                async with asyncio.timeout(8):
                    while await read_loadout("equip") != f"{before['stored_ammo']},{before['stored_ammo']}":
                        await asyncio.sleep(.15)
                async with asyncio.timeout(8):
                    while not any(entry["event"] == "native_guest_equipment_selected"
                                  and entry.get("native_guest_weapon", {}).get("guid") == guid
                                  for entry in await resident_events(guest_run, guest_frame)):
                        await asyncio.sleep(.1)
                if alternate:
                    changed = await host.bridge.command("fixture-stock-take-ammo-select-round", pawn, guid, alternate.item_path)
                    assert changed["ack"]["message"] == "stock round loaded"
            else:
                assert await read_loadout() == f"{before['stored_ammo']},stored"
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
        pressed = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=f"{guid}|take-ammo")
        assert pressed["ok"] and pressed["stock_action"]["guid"] == guid, pressed
        async with asyncio.timeout(12):
            while True:
                matches = [entry[native_inventory.TAKE_AMMO_KEY] for entry in await resident_events(guest_run, guest_frame)
                           if entry.get(native_inventory.TAKE_AMMO_KEY, {}).get("source_guid") == guid]
                if matches:
                    break
                await asyncio.sleep(.1)
        unloaded, = matches
        assert unloaded["remaining"] == 0 and unloaded["split"] == before["stored_ammo"], unloaded
        expected = alternate or (THIRTY_06_FMJ if kind == "obrez" else profiles[kind].ammo)
        assert unloaded["class_path"] == expected.item_path, unloaded
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        ammo, = [row for row in inventory["items"] if row["guid"] == unloaded["new_guid"]]
        assert (ammo["source"], ammo["class_path"], ammo["quantity"]) == ("backpack", unloaded["class_path"], unloaded["split"]), ammo
        after = (await submit(guest_run, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert (after["status"], after["stored_ammo"]) == (source, 0), after
        if source == "equipped":
            assert after["actor_ammo"] == 0 and await read_loadout() == "0,0", after
        elif source == "stored_equipment":
            assert await read_loadout() == "0,stored"
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
        proof = {"source": source, "kind": kind, **unloaded}
        if kind == "cult-shotgun" and previous_equipped:
            switched_slot, switched_guid = previous_equipped
            switched = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(switched_slot), switched_guid,
                                                   "switch-during-reload")
            assert switched["ack"]["message"] == "stock_cancelled", switched["ack"]
            async with asyncio.timeout(8):
                while await read_loadout() != "0,stored":
                    await asyncio.sleep(.1)
                while await read_loadout("equip") != "0,0":
                    await asyncio.sleep(.1)
            await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
            proof["switch_cancelled_reload"] = True
        if source == "equipped":
            previous_equipped = slot, guid
            expected_rounds = starting_rounds
            resumed_at = 0
            if kind == "pump-shotgun":
                first_seq = await start_input("reload")
                first, = await wait_input("reload", first_seq, 1)
                assert (first["guid"], first["before"], first["after"]) == (guid, 0, 1), first
                pressed = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, "pump-press")
                assert pressed["ack"]["message"] == "stock_held", pressed["ack"]
                await wait_resident_status(guest_run, lambda frame:
                    frame.get("game", {}).get("native_guest_primary_scope", {}).get("pump_trigger_held") is True)
                await asyncio.sleep(1.6)
                inserts = [event["native_guest_weapon"] for event in await resident_events(guest_run, guest_frame)
                           if event["event"] == "native_guest_reload_input" and event.get("sequence") == first_seq]
                assert 1 <= len(inserts) <= 2, inserts
                fired = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), guid, "pump-release")
                shot, = await wait_input("primary", int(fired["ack"]["message"]), 1)
                expected_rounds -= 1
                resumed_at = len(inserts) - 1
                assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                    guid, len(inserts), resumed_at, expected.projectile_count), shot
                async with asyncio.timeout(8):
                    while True:
                        try:
                            cycle = await host.bridge.command("fixture-stock-take-ammo-cycle", pawn, guid, timeout=1.5)
                        except TimeoutError:
                            continue
                        if cycle["ack"]["message"] == "ready":
                            break
                        await asyncio.sleep(.1)
                proof["trigger_interrupted_reload"] = {"inserts": inserts, "shot": shot}
            reload_count = expected_rounds - resumed_at if tube else 1
            reloads = await stock_input("reload", reload_count)
            expected_inserts = ([(guid, count, count + 1) for count in range(resumed_at, expected_rounds)]
                                if tube else [(guid, 0, expected_rounds)])
            assert [(row["guid"], row["before"], row["after"]) for row in reloads] == expected_inserts, reloads
            if tube:
                assert [(row["reserve_before"], row["reserve_after"]) for row in reloads] == [
                    (count, count - 1) for count in range(reload_count, 0, -1)], reloads
                proof["shell_reloads"] = reloads
            async with asyncio.timeout(10):
                while await read_loadout() != f"{expected_rounds},{expected_rounds}":
                    await asyncio.sleep(.15)
                while await read_loadout("ammo") != expected.item_path:
                    await asyncio.sleep(.15)
            proof["reload"] = reloads[-1]
            if automatic:
                started = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=410)
                assert started["ok"], started
                stroke = started["native_guest_held_fire_fixture"]["stroke"]
                await wait_resident_status(guest_run, lambda state:
                    (release := state.get("game", {}).get("native_guest_held_fire_fixture", {})).get("stroke") == stroke
                    and release.get("edge") == "release", timeout=6)
                host_frame = await wait_resident_status(host_run, lambda frame: frame.get("state") == "hosting")
                async with asyncio.timeout(8):
                    while True:
                        shots = [event for event in await resident_events(host_run, host_frame)
                                 if event["event"] == "native_guest_smg_hold_accepted" and event.get("guid") == guid]
                        if shots:
                            shot, = shots
                            break
                        await asyncio.sleep(.05)
                remaining = shot["after"]
                assert shot["before"] == expected_rounds and shot["status"] == "released"
                assert 1 < shot["rounds"] == expected_rounds - remaining < expected_rounds
                assert shot["projectiles"] == shot["rounds"] * expected.projectile_count, shot
            else:
                if impact_cues:
                    assert await asyncio.gather(*(impact_probe(seat, "arm") for seat in (host, guest))) == ["armed", "armed"]
                    assert await read_loadout("aim-at-floor") == "aimed"
                    impact_started = time.time()
                shot, = await stock_input("primary")
                remaining = expected_rounds - 1
                assert (shot["guid"], shot["before"], shot["after"], shot["projectiles"]) == (
                    guid, expected_rounds, remaining, expected.projectile_count,
                ), shot
                if impact_cues:
                    proof["impact_decals"] = await verify_decals(impact_started, expected.projectile_count)
            async with asyncio.timeout(8):
                while await read_loadout() != f"{remaining},{remaining}":
                    await asyncio.sleep(.15)
            proof["shot"] = shot
        proofs.append(proof)

    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_inventory
    assert campaign_save_hashes(host.campaign) == checkpoint
    return {"stock_take_ammo": proofs}


async def verify_resident_guest_food(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    expected = native_guest_consumable.MEALS[native_guest_consumable.MRE]
    grouped = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    checkpoint_hashes = campaign_save_hashes(host.campaign)

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-guest-food", action)

    async def read_host():
        return tuple(map(float, (await command(host, "host-read")).split(",")))

    async def read_guest(minimum):
        async with asyncio.timeout(8):
            while True:
                food, regen, widget = (await command(guest, "guest-read")).split(",", 2)
                if float(food) > minimum:
                    # NullRHI may leave the stock label on the preceding HUD sample.
                    food, regen, widget = (await command(guest, "guest-tick")).split(",", 2)
                    match = re.search(r"\d+(?:[.,]\d+)?", widget)
                    assert match and abs((amount := float(match[0].replace(",", "."))) - float(food)) <= .2, (food, regen, widget)
                    return {"food": float(food), "regen": float(regen), "widget": widget, "displayed_food": amount}
                await asyncio.sleep(.08)

    seed_started = time.time()
    guid = await command(host, "seed")
    assert re.fullmatch(r"[0-9A-F]{32}", guid) and guid != "0" * 32, guid
    host_before = await read_host()
    assert 37 < host_before[2] <= 38 and host_before[3] == 230, host_before
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory")
    inv_before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    meal, = [row for row in inv_before["items"] if row["guid"] == guid and row["source"] == "backpack"]
    assert meal["class_path"] == expected.class_path and meal["quantity"] == 1, meal
    async with asyncio.timeout(8):
        while not any(entry["event"] == "native_guest_ui_projected" and entry["items"] == len(inv_before["items"])
                      and entry["time_unix"] >= seed_started
                      for entry in await resident_events(guest_run, guest_frame)):
            await asyncio.sleep(.08)
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    before = await read_guest(35)
    assert before["food"] <= 38 and before["regen"] == 230, before

    press = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=guid)
    assert press["ok"] and press["stock_action"]["guid"] == guid, press
    key = native_guest_consumable.RESULT[native_guest_consumable.MRE]
    async with asyncio.timeout(12):
        while True:
            rows = await resident_events(guest_run, guest_frame)
            refused = [entry for entry in rows if entry["event"] == "native_stock_inventory_rejected"]
            assert not refused, refused
            results = [entry[key] for entry in rows if entry["event"] == "native_stock_inventory_action"
                       and entry.get(key, {}).get("guid") == guid]
            if results:
                used, = results
                break
            await asyncio.sleep(.12)
    assert (used["pawn"], used["quantity_before"], used["quantity_after"]) == (pawn, 1, 0), used
    assert used["food_after"] > used["food_before"] + 20 and used["regen_after"] > used["regen_before"], used
    after = await read_guest(used["food_after"] - 1)
    host_after = await read_host()
    assert abs(after["food"] - host_after[2]) <= 1 and after["regen"] == host_after[3] == used["regen_after"], (after, host_after, used)
    assert 0 <= host_before[0] - host_after[0] < 2 and host_before[1] == host_after[1], (host_before, host_after)
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    assert guid not in {row["guid"] for row in inventory["items"]}, inventory
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == grouped
    assert campaign_save_hashes(host.campaign) == checkpoint_hashes, "A stock meal caused an early campaign checkpoint"
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    cases = ("leftovers", "honey-egg", "dirty-bandage", "gauze", "medical-salve", "large-medkit", "stim-pills",
             "antibiotics", "brown-mushroom")
    seeded_at = time.time()
    seeded = (await command(host, "seed-use-cases")).split(",")
    assert len(seeded) == len(set(seeded)) == len(cases) and all(re.fullmatch(r"[0-9A-F]{32}", value) for value in seeded)
    stocked = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
    async with asyncio.timeout(8):
        while not any(row["event"] == "native_guest_ui_projected" and row["items"] == len(stocked)
                      and row["time_unix"] >= seeded_at for row in await resident_events(guest_run, guest_frame)):
            await asyncio.sleep(.08)
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    canonical, _ = (await command(host, "host-medical-vitals")).split(";")
    verified = {}
    observed_food = {}

    def observed_use(frame, class_path):
        use = frame.get("game", {}).get("native_avatar_use", {}).get(pawn, {})
        return use if use.get("item") == class_path and use["active"] is False else None

    for label, item in zip(cases, seeded, strict=True):
        verb = f"native-guest-{label}"
        profile = native_guest_consumable.STOCK_CONSUMABLES[verb]
        key = native_guest_consumable.RESULT[verb]
        initial = 2 if label == "gauze" else 1
        when = time.time()
        pressed = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=item)
        assert pressed["ok"] and pressed["stock_action"]["guid"] == item, pressed
        async with asyncio.timeout(10):
            while True:
                events = [row for row in await resident_events(guest_run, guest_frame) if row["time_unix"] >= when]
                refused = [row for row in events if row["event"] == "native_stock_inventory_rejected"]
                assert not refused, (label, refused)
                results = [row[key] for row in events if row["event"] == "native_stock_inventory_action"
                           and row.get(key, {}).get("guid") == item]
                if results:
                    receipt, = results
                    break
                await asyncio.sleep(.10)
        assert (receipt["class_path"], receipt["quantity_before"], receipt["quantity_after"]) == (
            profile.class_path, initial, initial - 1), receipt
        if profile.food:
            assert receipt["food_after"] > receipt["food_before"], receipt
            await read_guest(receipt["food_after"] - .5)
        else:
            if profile.instant:
                assert receipt["health_after"] > receipt["health_before"], receipt
            if profile.stim:
                assert receipt["stim_after"] >= profile.stim, receipt
            if label == "antibiotics":
                assert 1 <= receipt["antibiotic_resistance_after"] - receipt["antibiotic_resistance_before"] <= 3, receipt
            async with asyncio.timeout(8):
                while True:
                    hp, regen, stim, _, _ = map(float, (await command(guest, "guest-medical-vitals")).split(","))
                    if (hp >= receipt["health_after"] - .2 and abs(regen - receipt["regen_after"]) < .2
                            and stim >= receipt["stim_after"] - .35):
                        break
                    await asyncio.sleep(.1)
        if label in ("leftovers", "brown-mushroom"):
            def finished(frame):
                return observed_use(frame, profile.class_path)

            use = finished(await wait_resident_status(host_run, finished, timeout=8))
            assert 23 <= use.get("food_forward_of_head_cm", 0) <= 38 and use["hand_motion_cm"] > 20, use
            observed_food[label] = use
        verified[label] = receipt
    remaining = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
    assert [(row["guid"], row["quantity"]) for row in remaining if row["guid"] in seeded] == [(seeded[3], 1)], remaining

    assert "Mushroom" in observed_food["brown-mushroom"]["mesh"], observed_food
    assert observed_food["leftovers"]["food_forward_of_head_cm"] > 24, observed_food
    host_vitals, _ = (await command(host, "host-medical-vitals")).split(";")
    assert host_vitals == canonical, (canonical, host_vitals)
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == grouped
    assert campaign_save_hashes(host.campaign) == checkpoint_hashes, "Stock medicine caused an early campaign checkpoint"
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"resident_guest_food": {"receipt": used, "before": before, "after": after,
                                    "host_food_before": host_before[0], "host_food_after": host_after[0],
                                    "host_regen_unchanged": True, "campaign_checkpoint_unchanged": True,
                                    "stock_widget_tick_invoked_in_muted_fixture": True, "additional_stock_uses": verified,
                                    "host_observed_food_use": observed_food}}


async def verify_stock_cooked_meal(host: Seat, guest: Seat, _pawn: str) -> dict:
    guest_run = guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    frame = await wait_resident_status(guest_run, lambda row: row.get("local_bridge") == "memory")

    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-guest-food", action)

    def stats(value):
        return tuple(map(float, value.split(",")))

    assert await command(host, "seed-cooking-state") == "seeded"
    async with asyncio.timeout(8):
        while not 54 <= stats(await command(guest, "guest-cooking-state"))[0] <= 55:
            await asyncio.sleep(.1)
    stock_before, detached_before, host_pot = (await command(host, "host-cooking-state")).split(";")
    started = time.time()
    local_before, local_after, remainder = (await command(guest, "cook")).split(";")
    food_before, _, _ = stats(local_before)
    food_after, _, _ = stats(local_after)
    assert float(remainder) == 0 and 19.9 <= food_after - food_before <= 20.1, (local_before, local_after, remainder)
    async with asyncio.timeout(8):
        while True:
            events = [row for row in await resident_events(guest_run, frame)
                      if row["time_unix"] >= started and row["event"].startswith("native_guest_cooked_meal")]
            if events:
                receipt, = events
                assert receipt["event"] == "native_guest_cooked_meal", receipt
                break
            await asyncio.sleep(.1)
    empty_before, empty_after, empty_remainder = (await command(guest, "cook-empty")).split(";")
    assert empty_before == empty_after and float(empty_remainder) == 0
    stock_after, detached_after, host_pot_after = (await command(host, "host-cooking-state")).split(";")
    assert host_pot == host_pot_after and stats(stock_before)[1:] == stats(stock_after)[1:]
    assert abs(stats(stock_before)[0] - stats(stock_after)[0]) < 1
    guest_food, guest_fatigue, guest_intox = stats(detached_after)
    assert 19 <= guest_food - stats(detached_before)[0] <= 20.1, detached_after
    assert (guest_fatigue, guest_intox) == (40, 0), detached_after
    assert campaign_save_hashes(host.campaign) == checkpoint, "Eat All wrote an early campaign checkpoint"
    return {"stock_cooked_meal": {"local_before": stats(local_before), "local_after": stats(local_after),
                                 "host_guest_before": stats(detached_before), "host_guest_after": stats(detached_after),
                                 "host_pot_unchanged": True, "empty_pot_had_no_effect": True, "receipt": receipt}}


async def verify_resident_board_offers(host: Seat, guest: Seat, pawn: str) -> dict:
    from tools.silverfish import native_anomaly_effects, native_board_contract, native_inventory
    from tools.silverfish.authored_routes import AUTHORED_ROUTES
    from tools.silverfish.native_flood_jammer import OBSERVATORY_BOUNTY_BOARD
    from tools.silverfish.native_quest import AUTHORED_BOARD_ACTIONS
    from tools.silverfish.native_wire_board import catalog_specialists
    from tools.silverfish.travel import CITY, CROSSROADS, DOCKS, FOREST, OBSERVATORY, RAIL_YARD

    dry_dock = "/Game/Classes/Maps/Maps_Dungeons/DryDock_P"
    objectives = (
        ("Sab_Fuel_01", "Kill_ForestTank", 6, 1600, (OBSERVATORY, CROSSROADS, FOREST), ("Prop_FuelTank_C_1",), "fixedtank"),
        ("Sab_Factory", "PB_Factory_Kill", 4, 1200, (OBSERVATORY, CROSSROADS, RAIL_YARD, CITY, DOCKS, dry_dock),
         tuple(f"Prop_Explosive_PowerBox_C_{index}" for index in (5, 11, 9, 3)), "powerbox"),
    )

    async def command(seat, action, quest=None):
        request = f"{action}\t{quest}" if quest is not None else action
        return await portable_probe_command(seat.bridge, "portable-board-offer", request)

    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def board_menu():
        opened = await submit(guest_run, "native-wire-board-stock-f-fixture", target=OBSERVATORY_BOUNTY_BOARD)
        assert opened["ok"], opened
        return await wait_resident_status(guest_run, lambda frame: (
            (board := frame.get("game", {}).get("native_wire_board_stock", {}))
            and board.get("state") == "visible" and board.get("board") == OBSERVATORY_BOUNTY_BOARD))

    async def select(quest, verb):
        result = await submit(guest_run, "native-wire-board-stock-fixture", target=f"{quest}|{verb}")
        assert result["ok"], result
        await wait_resident_status(guest_run, lambda frame: not frame.get("game", {}).get("native_wire_board_stock"))

    async def board_catalog():
        result = await submit(guest_run, native_board_contract.CATALOG, target=OBSERVATORY_BOUNTY_BOARD)
        assert result["ok"], result
        return result[native_board_contract.CATALOG_KEY]

    async def at_point(target):
        await wait_resident_status(guest_run, lambda frame: (
            (point := frame.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
            and math.dist(point, target) <= 25))

    async def approach_board():
        await at_point(tuple(map(float, (await command(host, "approach")).split(","))))

    async def cross(source, destination):
        nonlocal pawn
        route, = (row for row in AUTHORED_ROUTES if (row.source, row.destination) == (source, destination))
        await wait_resident_status(host_run, lambda frame: frame["game"]["map"] == source
                                   and frame["game"].get("native_zone", {}).get("state") == "ready")
        if route.kind == "door":
            for participant in (pawn, "local"):
                await host.bridge.command("native-door-position", participant, "near", route.entrance)
            opened = await host.bridge.command("native-door-probe", "local", route.entrance)
            assert opened["native_door"]["decision"] in {"host_opening", "travel_requested"}, opened["native_door"]
        else:
            assert route.kind == "overlap", route
            for participant, placement in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                await host.bridge.command("native-exit-position", participant, placement, route.entrance, route.actor)
        arrived = await wait_resident_status(host_run, lambda frame: frame["game"]["map"] == destination
            and not frame.get("awaiting_zone_guest") and frame["game"].get("native_guest_save", {}).get("state") == "bound",
            timeout=55)
        pawn = arrived["game"]["native_guest_save"]["pawn"]
        await wait_resident_status(guest_run, lambda frame: frame.get("zone_epoch") == arrived["zone_epoch"]
                                   and frame.get("game", {}).get("map") == destination, timeout=35)

    async def shoot_objective(key, count, areas, actors, cue):
        async def weapon_setup(seat, verb):
            return await portable_probe_command(seat.bridge, "simmi-jobs", verb)

        guid = await weapon_setup(host, "seedpistol")
        stored = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=guid)
        assert stored["ok"] and stored[native_inventory.STORE_KEY]["guid"] == guid, stored
        slot = stored[native_inventory.STORE_KEY]["slot"]
        async with asyncio.timeout(9):
            while await weapon_setup(guest, f"selectpistol\t{slot}") != "equipped":
                await asyncio.sleep(.15)
        for source, destination in zip(areas, areas[1:]):
            await cross(source, destination)
        area = areas[-1]
        prefix = f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel."
        for actor in actors:
            target = prefix + actor
            try:
                aimed = await host.bridge.command("native-fixed-explosive-aim-fixture", target, pawn, guid, timeout=6)
                break
            except RuntimeError as exc:
                if str(exc) != "stock_flood_jammer_aim_no_stock_sight_line":
                    raise
        else:
            for source, destination in zip(reversed(areas[1:]), reversed(areas[:-1])):
                await cross(source, destination)
            raise AssertionError(f"No reachable position outside the blast for {actors} in {area}")
        save_hashes = campaign_save_hashes(host.campaign)

        def prop(frame):
            return frame.get("game", {}).get(native_anomaly_effects.FIXED_EXPLOSIVES, {}).get(target, {})

        def wreck(frame):
            entry = prop(frame)
            return entry.get("destroyed") and entry.get("damaged_mesh") and not entry.get("burning")

        async def flag():
            response = await host.bridge.command("native-fixed-explosive-flag-fixture", target)
            return response["ack"]["message"]

        assert await flag() == f"{key}|{count}"
        await wait_resident_status(host_run, lambda frame: prop(frame) and not prop(frame)["destroyed"])
        connected = await wait_resident_status(guest_run, lambda frame:
            prop(frame) and not prop(frame)["destroyed"]
            and frame.get("game", {}).get("native_guest_primary_scope", {}).get("guid") == guid)
        local_pawn = connected["game"]["native_guest_primary_scope"]["pawn"]
        guard = "native_canister_blocked" if cue == "fixedtank" else "native_fixed_explosive_blocked"
        blocked = connected["game"].get(guard, 0)
        await guest.bridge.command("native-fixed-explosive-guest-guard-fixture", target)
        guarded = await wait_resident_status(guest_run, lambda frame:
            frame.get("game", {}).get(guard, 0) >= blocked + 2)
        assert not prop(guarded)["destroyed"] and not prop(guarded).get("burning")
        assert await flag() == f"{key}|{count}"
        magazine = 7
        fired = []
        for shot_number in range(magazine):
            if shot_number:
                aimed = await host.bridge.command("native-fixed-explosive-aim-fixture", target, pawn, guid, timeout=6)
            await at_point(aimed["pawns"]["guest"]["location"])
            async with asyncio.timeout(7):
                while True:
                    try:
                        tap = await guest.bridge.command("native-guest-primary-input-fixture", local_pawn, target)
                        break
                    except RuntimeError as exc:
                        if str(exc) != "Stock primary input is temporarily blocked by the equipped player's state":
                            raise
                        await asyncio.sleep(.15)
            sequence = tap["native_guest_primary_fixture"]["sequence"]
            async with asyncio.timeout(6):
                while True:
                    shot = [row for row in await resident_events(guest_run, connected)
                            if row.get("sequence") == sequence and row["event"].startswith("native_guest_primary_input")]
                    if shot:
                        break
                    await asyncio.sleep(.1)
            result, = shot
            assert result["event"] == "native_guest_primary_input", result
            assert result.get("native_guest_trigger_view", {}).get("contact", {}).get("actor") == target, result
            debit = result["native_guest_weapon"]
            assert (debit["guid"], debit["before"], debit["after"], debit["projectiles"]) == (guid, magazine, magazine - 1, 1)
            magazine -= 1
            fired.append(debit["after"])
            try:
                async with asyncio.timeout(2.7):
                    destroyed = await wait_resident_status(host_run, wreck)
                break
            except TimeoutError:
                continue
        else:
            raise AssertionError(f"The guest did not destroy {target}")

        witness = await wait_resident_status(guest_run, wreck)
        assert prop(witness)["mesh"] == prop(destroyed)["mesh"]
        assert destroyed["game"]["pawns"]["guest"]["health"] > 0
        cues = [r for r in await resident_events(guest_run, witness)
                if r["event"] == "native_fixed_explosive_effect_played" and r.get("cue") == cue and r["map"] == area]
        assert len(cues) == 1, cues
        assert await flag() == f"{key}|{count + 1}"
        assert campaign_save_hashes(host.campaign) == save_hashes, "Destroying the stock objective wrote a save"
        for source, destination in zip(reversed(areas[1:]), reversed(areas[:-1])):
            await cross(source, destination)
        await approach_board()
        return {"actor": target, "guest_magazine": fired, "stock_counter": [count, count + 1],
                "host_mesh": prop(destroyed)["mesh"], "guest_mesh": prop(witness)["mesh"],
                "denied_guest_local_callbacks": True, "no_save_on_destruction": True, "guest_blast_relay": cues[0]}

    checkpoint = campaign_save_hashes(host.campaign)
    await approach_board()
    catalog, extended = (await command(host, "catalog")).split(";")
    offered = set(filter(None, catalog.split(",")))
    advanced = set(filter(None, extended.split(","))) - AUTHORED_BOARD_ACTIONS
    other_boards = offered & (AUTHORED_BOARD_ACTIONS - catalog_specialists(OBSERVATORY, OBSERVATORY_BOUNTY_BOARD))
    previously_filtered = advanced | other_boards
    assert previously_filtered <= offered and previously_filtered, (offered, previously_filtered)
    quest = min(previously_filtered)

    guest_frame = await board_menu()
    visible = set(filter(None, (await command(guest, "menu")).split(",")))
    assert visible == offered, (visible, offered)
    await select(quest, "start")

    async with asyncio.timeout(8):
        while True:
            observed = await asyncio.gather(*(command(seat, "journal", quest) for seat in (host, guest)))
            if observed[0] == observed[1] and observed[0] != "-":
                break
            await asyncio.sleep(.1)
    entries = await resident_events(guest_run, guest_frame)
    assert any(entry.get("quest") == quest and entry.get("state") == "accepted" for entry in entries), entries

    assert await command(host, "qualify-tracked") == "10"
    assert campaign_save_hashes(host.campaign) == checkpoint, "Accepting a stock board offer caused an early save"

    async def settle(quest_id, cash):
        before = campaign_save_hashes(host.campaign)
        ready = await board_catalog()
        assert quest_id in ready["turnins"], ready
        current = await wait_resident_status(host_run,
            lambda frame: type(frame.get("game", {}).get("native_wallet")) is int)
        wallet = current["game"]["native_wallet"]
        await board_menu()
        await select(quest_id, "turnin")
        settled = await wait_resident_status(host_run, lambda frame:
            frame.get("gameplay", {}).get("native_quests", {}).get(quest_id, {}).get("step") == -10)
        receipt = settled["gameplay"]["native_quests"][quest_id]
        assert (receipt["cash"], receipt["xp"], receipt["wallet"]) == (cash, 1, wallet + cash), receipt
        member = guest_frame["character_id"]
        assert set(receipt["recipients"]) == {"host", member} and receipt["actor"] == member, receipt
        await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("native_wallet") == wallet + cash)
        async with asyncio.timeout(8):
            while True:
                journals = await asyncio.gather(*(command(seat, "journal", quest_id) for seat in (host, guest)))
                if journals == ["-10", "-10"]:
                    break
                await asyncio.sleep(.1)
        assert quest_id not in (await board_catalog())["turnins"]
        assert campaign_save_hashes(host.campaign) == before, "Redeeming a stock board quest caused an early save"
        return receipt

    followup_quest = "Asn_Panzer_01"
    photo_quest = "Photo_BrnzQuarry"
    photo_selection = native_board_contract.menu_target(guest_frame["zone_epoch"], OBSERVATORY_BOUNTY_BOARD, photo_quest)
    assert await command(host, "add-photo-contract") == "1"
    assert photo_quest not in (await board_catalog())["turnins"]
    incomplete = await submit(guest_run, "native-quest-turnin", target=photo_selection)
    assert not incomplete["ok"] and incomplete["error"] == "stock_board_contract_objective_not_complete", incomplete
    assert await command(host, "complete-photo-objective") == "2"
    photo_receipt = await settle(photo_quest, 100)
    assert "objective_guid" not in photo_receipt
    assert await command(host, "photo-consumption-field") == "true"
    replay = await submit(guest_run, "native-quest-turnin", target=photo_selection)
    assert replay["ok"] and replay["effect"] == "native_quest_already_settled" and replay["wallet"] == photo_receipt["wallet"]
    wallet_after = await wait_resident_status(host_run, lambda frame: type(frame.get("game", {}).get("native_wallet")) is int)
    assert wallet_after["game"]["native_wallet"] == photo_receipt["wallet"]
    assert campaign_save_hashes(host.campaign) == checkpoint
    objective_step = int(await command(host, "stage-turnin-followup", followup_quest))
    assert objective_step > 0
    inspected = (await host.bridge.command(native_board_contract.COMMAND, "turnin", "inspect",
                                           pawn, OBSERVATORY_BOUNTY_BOARD, followup_quest))[native_board_contract.KEY]
    assert inspected["state"] == "qualified" and inspected["step_before"] == objective_step, inspected
    stale = {**inspected, "step_before": objective_step - 1}
    try:
        await host.bridge.command(native_board_contract.COMMAND, "turnin", "apply",
                                  pawn, OBSERVATORY_BOUNTY_BOARD, followup_quest,
                                  *native_board_contract.apply_arguments(stale))
    except RuntimeError as exc:
        assert str(exc) == "stock_board_contract_inspection_changed", exc
    else:
        raise AssertionError("The stock board accepted a stale quest inspection")
    assert await command(host, "journal", followup_quest) == str(objective_step)
    assert await command(host, "turnin-followups") == "0"
    assert campaign_save_hashes(host.campaign) == checkpoint
    followup_receipt = await settle(followup_quest, 3500)
    selection = native_board_contract.menu_target(guest_frame["zone_epoch"], OBSERVATORY_BOUNTY_BOARD, followup_quest)
    replay = await submit(guest_run, "native-quest-turnin", target=selection)
    assert replay["ok"] and replay["effect"] == "native_quest_already_settled", replay
    assert await command(host, "turnin-followups") == "1"
    assert campaign_save_hashes(host.campaign) == checkpoint

    tracked_quests = []
    for tracked, key, progress, cash, areas, actors, cue in objectives:
        checkpoint = campaign_save_hashes(host.campaign)
        offers = await board_catalog()
        assert tracked in offers["offers"] and tracked not in offers["turnins"], offers
        await board_menu()
        await select(tracked, "start")
        early = await board_catalog()
        assert tracked not in early["offers"] and tracked not in early["turnins"], early
        assert await command(host, "track-progress", tracked) == str(progress)
        assert await command(host, "journal", tracked) == "1"
        assert tracked not in (await board_catalog())["turnins"]
        assert campaign_save_hashes(host.campaign) == checkpoint
        objective = await shoot_objective(key, progress, areas, actors, cue)
        receipt = await settle(tracked, cash)
        tracked_quests.append({"quest_id": tracked, "world_flag_progress": progress, "receipt": receipt, **objective})
    return {"resident_board_offers": {"stock_offers": sorted(offered), "guest_offers": sorted(visible),
                                      "previously_filtered_offers": sorted(previously_filtered),
                                      "accepted": quest, "journal_step": int(observed[0]),
                                      "tracked_turnins": tracked_quests, "followup_turnin": followup_receipt,
                                      "stale_turnin_refused_before_stock_callback": True,
                                      "empty_deposit_turnin": photo_receipt, "photo_objective_staged": True,
                                      "campaign_checkpoint_unchanged": True}}


async def verify_resident_npc_override(host: Seat, guest: Seat, _pawn=None, *, fire_guest=None) -> dict:
    async def command(seat, action, actor=None):
        request = f"{action}\t{actor}" if actor is not None else action
        return await portable_probe_command(seat.bridge, "portable-npc-override", request)

    checkpoint = campaign_save_hashes(host.campaign)
    assert await command(guest, "census") == "empty", "A drone already existed on the guest before the test spawn"
    drone = await command(host, "spawn-guest-target" if fire_guest else "spawn")
    async with asyncio.timeout(8):
        while (observed := await command(guest, "observe", drone)) == "absent":
            await asyncio.sleep(.1)
    before = float(await command(host, "observe", drone))
    assert float(observed) > 0 and before > 0, (before, observed)
    if not fire_guest:
        assert float(observed) == before, (before, observed)
    assert await command(guest, "guest-damage", drone) == "damage denied"
    guest_drone = await command(guest, "census")
    assert guest_drone.startswith("NPC_Drone_C /Game/"), guest_drone
    assert float(await command(host, "observe", drone)) == before
    if fire_guest:
        aim = await command(guest, "guest-aim", drone)
        views = await asyncio.gather(*(command(seat, "view", drone) for seat in (host, guest)))
        authority = {"guest_aim": aim, "views_before_shot": views, "guest_shot": await fire_guest()}
    else:
        total = float(await command(host, "host-damage", drone))
        assert total >= before
        authority = {"authority_damage": total}
    result = None
    try:
        async with asyncio.timeout(8):
            while True:
                result = await asyncio.gather(*(command(seat, "death", drone) for seat in (host, guest)))
                if result == ["false,true,1"] * 2:
                    break
                await asyncio.sleep(.1)
    except TimeoutError as error:
        health = await asyncio.gather(*(command(seat, "observe", drone) for seat in (host, guest)))
        raise AssertionError(f"The host and guest drone death states were {result}, health {health}, {authority}") from error
    assert campaign_save_hashes(host.campaign) == checkpoint, "Drone damage caused an early checkpoint"
    return {"resident_npc_override": {"host_stock_drone": drone, "guest_stock_drone": guest_drone,
                                      "hit_points_before": before,
                                      "guest_local_damage_denied": True, **authority,
                                      "ragdoll_on_both": True, "campaign_checkpoint_unchanged": True}}


async def verify_stock_host_corpse_carry(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def carry(seat, action, *, read=False):
        return await portable_probe_command(seat.bridge, "portable-npc-override",
                                            f"corpse-{action}", reply_end="|end" if read else None)

    checkpoint = campaign_save_hashes(host.campaign)
    assert await carry(host, "spawn") == "spawned"
    async with asyncio.timeout(8):
        while True:
            references = await asyncio.gather(*(carry(seat, "combat-reference") for seat in (host, guest)))
            if references[0] == references[1] and re.fullmatch(r"[0-9A-F]{32}", references[0]):
                break
            await asyncio.sleep(.1)
    assert await carry(host, "kill") == "killed"
    async with asyncio.timeout(8):
        while await asyncio.gather(carry(host, "ready"), carry(guest, "ready")) != ["ready", "ready"]:
            await asyncio.sleep(.1)
    await asyncio.sleep(.4)
    assert await carry(host, "grab") == "grabbed"
    await asyncio.sleep(.35)
    assert await asyncio.gather(carry(host, "arm"), carry(guest, "arm")) == ["armed", "armed"]
    assert await carry(host, "sweep") == "sweeping"
    await asyncio.sleep(1.55)
    assert await asyncio.gather(carry(host, "mark"), carry(guest, "mark")) == ["marked", "marked"]
    assert await carry(host, "drop") == "dropped"
    await asyncio.sleep(3.4)
    views = {}
    for name, receipt in zip(("host", "guest"), await asyncio.gather(
            *(carry(seat, "read", read=True) for seat in (host, guest)))):
        header, *samples, _ = receipt.split("|")
        number, marked = map(int, header.split())
        views[name] = {"number": number, "held_samples": marked,
                       "samples": [tuple(map(float, row.split())) for row in samples]}
    host_view, guest_view = views["host"], views["guest"]
    assert host_view["number"] == guest_view["number"] > 0
    host_held = host_view["samples"][:host_view["held_samples"]]
    guest_held = guest_view["samples"][:guest_view["held_samples"]]
    assert len(host_held) >= 12 and all(row[7:11] == (1, 3, 1, 1) for row in host_held), host_held[::10]
    assert len(guest_held) >= 12 and all(row[7:11] == (1, 3, 0, 1) for row in guest_held), guest_held[::10]
    guest_spin = max(row[14] for row in guest_held)
    assert guest_spin < 180, (guest_spin, guest_held[::10])
    assert guest_view["samples"][-1][7:11] == (0, 3, 0, 1), guest_view["samples"][-1]
    origin, end = host_held[0][1:4], host_held[-1][1:4]
    distance = math.dist(origin, end)
    guest_distance = math.dist(guest_held[0][1:4], guest_held[-1][1:4])
    assert distance >= 170 and guest_distance >= distance * .65, (distance, guest_distance)
    direction = [(b - a) / distance for a, b in zip(origin, end)]
    travel = [sum((x - a) * axis for x, a, axis in zip(row[1:4], origin, direction)) for row in guest_held]
    reverse = max(0, *(a - b for a, b in zip(travel, travel[1:])))
    assert reverse < 18, reverse
    end_gap = math.dist(host_held[-1][1:4], guest_held[-1][1:4])
    assert end_gap < 75, end_gap
    settled_gap = math.dist(*(view["samples"][-1][1:4] for view in views.values()))
    assert settled_gap < 40, (settled_gap, [view["samples"][-1] for view in views.values()])
    assert await carry(guest, "ready") == "ready", "the guest corpse did not resume ragdoll physics after the drop"
    assert await carry(guest, "focus") == "focused", "stock guest LookTrace could not select the released body"
    assert campaign_save_hashes(host.campaign) == checkpoint, "Carrying a corpse caused an early checkpoint"
    return {"stock_host_corpse_carry": {"host_displacement_cm": distance, "guest_displacement_cm": guest_distance,
                                      "guest_peak_anchor_spin_deg_per_sec": guest_spin,
                                      "guest_max_backstep_cm": reverse, "end_gap_cm": end_gap,
                                      "settled_gap_cm": settled_gap, "guest_stock_focus_restored": True,
                                      "campaign_checkpoint_unchanged": True, "live_combat_reference": references[0], **views}}


async def verify_stock_flashlights(host: Seat, guest: Seat, _pawn: str) -> dict:
    async def torch(seat, mode, action):
        return (await portable_probe_command(seat.bridge, "avatar-probe", f"{mode}x{action}\n")).strip().split("\t")

    async def view(observer, source, profile, power, after=None):
        run = observer.campaign / "run" if observer is host else observer.client / "run"
        def applies(state):
            game = state.get("game", {})
            projected = (game.get("native_guest_flashlight_view", {}).get(str(source), {})
                         if observer is host else game.get("native_flashlight_view", {}))
            if (projected.get("profile") == profile and projected.get("player") == source
                    and projected.get("power") is power and projected.get("visible") is power
                    and (after is None or math.dist(projected["aim"], after) > 4)):
                return projected
        return applies(await wait_resident_status(run, applies, timeout=8))

    async def inspect(observer, mode, source, *, offhand=False, powered=True):
        values = await torch(observer, mode, f"observe{source}")
        assert sum(map(int, values[3:5])) == int(powered), values
        if not powered:
            assert values[-1] == "-", values
            return {"spotlight": None}
        _, hidden, affects, intensity, radius, inner, outer, units, inverse, falloff, cookie, fade, socket, *angles = values[7:]
        assert hidden == "false" and affects == "true", values
        assert tuple(map(float, (intensity, radius, inner, outer))) == (17, 5000, 15, 40), values
        assert cookie.endswith("/M_LightCookie.M_LightCookie"), values
        if offhand:
            assert values[6] == "1" and socket == "LeftHand", values
        return {"spotlight": values[7], "light_units": units, "inverse_squared": inverse,
                "falloff": float(falloff), "cookie": cookie, "fade_distance": float(fade),
                "direction": list(map(float, angles))}

    async def switch_offhand(seat, mode):
        assert (await torch(seat, mode, "coffhandstart"))[0] == "ongoing_start"
        await asyncio.sleep(.42)
        assert (await torch(seat, mode, "coffhandhold"))[0] == "ongoing_hold"
        assert (await torch(seat, mode, "coffhandrelease"))[0] == "completed_release"

    def angular_error(owner_angles, observer_angles):
        a, b = [math.radians(float(axis)) for axis in owner_angles[:2]], [
            math.radians(float(axis)) for axis in observer_angles[:2]]
        dot = math.sin(a[0]) * math.sin(b[0]) + math.cos(a[0]) * math.cos(b[0]) * math.cos(a[1] - b[1])
        return math.degrees(math.acos(max(-1, min(1, dot))))

    async def turn_held(owner, observer, mode, player, previous_source, previous_beam):
        await torch(owner, mode, "turnlight")
        await asyncio.sleep(.45)
        async with asyncio.timeout(4):
            while True:
                source = (await torch(owner, mode, "readlight"))[9:12]
                beam = await inspect(observer, mode, player)
                if (angular_error(previous_source, source) > 15 and
                        angular_error(previous_beam["direction"], beam["direction"]) > 15):
                    return {"owner": list(map(float, source)), "observer": beam,
                            "error_degrees": angular_error(source, beam["direction"])}
                await asyncio.sleep(.12)

    checkpoint = campaign_save_hashes(host.campaign)
    stock, offhand = "regularhost", "offhandhost"
    seeded = await torch(host, offhand, "seedoffhand")
    host_id = int(seeded[0])
    assert seeded[6] == "false", seeded
    assert await torch(host, offhand, "selectregular") == ["selected"]
    await view(guest, host_id, None, False)
    host_stock_light = await torch(host, stock, "lighton")
    assert host_stock_light[4:6] == ["true", "true"]
    host_held = await view(guest, host_id, None, True)
    assert host_held["heard"] == host_held["switch"]
    regular_beam = await inspect(guest, stock, host_id)
    host_turn = await turn_held(host, guest, stock, host_id, host_stock_light[9:12], regular_beam)
    assert (await torch(host, stock, "lightoff"))[4:6] == ["false", "false"]
    await view(guest, host_id, None, False)
    await inspect(guest, stock, host_id, powered=False)
    assert await torch(host, offhand, "selectprimary") == ["selected"]
    await wait_resident_status(guest.client / "run",
                               lambda frame: not frame.get("game", {}).get("native_flashlight_view"), timeout=8)
    await switch_offhand(host, offhand)
    host_offhand = await view(guest, host_id, "offhand", True)
    offhand_beam = await inspect(guest, offhand, host_id, offhand=True)
    await torch(host, offhand, "turnoffhand")
    turned = await view(guest, host_id, "offhand", True, after=host_offhand["aim"])
    await asyncio.sleep(.32)
    aimed_beam = await inspect(guest, offhand, host_id, offhand=True)
    assert math.dist(offhand_beam["direction"], aimed_beam["direction"]) > 4
    await switch_offhand(host, offhand)
    await wait_resident_status(guest.client / "run",
                               lambda frame: not frame.get("game", {}).get("native_flashlight_view"))
    await inspect(guest, offhand, host_id, powered=False)
    guest_mode, guest_stock = "offhandguest", "regularguest"
    guard = await torch(host, guest_mode, "guestoffhandboundary")
    _, gun, light = await torch(host, guest_mode, "guestoffhandseed")
    async with asyncio.timeout(8):
        while True:
            try:
                guest_setup = await torch(guest, guest_mode, f"selectoffhandgun{gun}{light}")
                break
            except AssertionError as error:
                if str(error).strip() != "avatar_probe_rig_pending":
                    raise
                await asyncio.sleep(.2)
    guest_id = int(guest_setup[0])
    assert guest_id != host_id and guest_setup[6] == "false"
    assert await torch(guest, guest_mode, "selectregular") == ["selected"]
    await view(host, guest_id, None, False)
    guest_stock_light = await torch(guest, guest_stock, "lighton")
    assert guest_stock_light[4:6] == ["true", "true"]
    guest_held = await view(host, guest_id, None, True)
    assert guest_held["heard"] == guest_held["switch"]
    host_beam = await inspect(host, guest_stock, guest_id)
    guest_turn = await turn_held(guest, host, guest_stock, guest_id, guest_stock_light[9:12], host_beam)
    assert (await torch(guest, guest_stock, "lightoff"))[4:6] == ["false", "false"]
    await view(host, guest_id, None, False)
    await inspect(host, guest_stock, guest_id, powered=False)
    assert await torch(guest, guest_mode, "selectprimary") == ["selected"]
    await switch_offhand(guest, guest_mode)
    guest_offhand = await view(host, guest_id, "offhand", True)
    host_offhand_beam = await inspect(host, guest_mode, guest_id, offhand=True)
    await switch_offhand(guest, guest_mode)
    await wait_resident_status(host.campaign / "run",
                               lambda frame: not frame.get("game", {}).get("native_guest_flashlight_view"), timeout=8)
    await inspect(host, guest_mode, guest_id, powered=False)
    assert await torch(host, guest_mode, "guestoffhandboundary") == guard
    assert await torch(host, "visor", "seedhost") == [str(host_id)]
    assert await torch(host, "visor", "seedguest") == [str(guest_id)]
    helmet = "/Game/Classes/Armor_Items/Helm_TacLight.Helm_TacLight_C"
    await wait_resident_status(guest.client / "run", lambda frame: any(
        row.get("head") == helmet for row in frame.get("gameplay", {}).get("native_avatar_outfits", {}).get("outfits", {}).values()))

    async def worn_views(target, owner_id, powered):
        run = target.campaign / "run" if target is host else target.client / "run"
        await wait_resident_status(run, lambda frame: (str(owner_id) in frame.get("game", {}).get(
            "native_avatar_visor_view", [])) is powered)

    async def worn(owner, observer, owner_id, selection, hand):
        async with asyncio.timeout(6):
            while True:
                try:
                    before = await torch(owner, "visor", "read")
                    break
                except AssertionError as error:
                    if "visor stock helmet not projected" not in str(error):
                        raise
                    await asyncio.sleep(.1)
        assert before[:2] == ["false", "false"], before
        assert await torch(owner, selection, "selectregular") == ["selected"]
        await torch(owner, hand, "lighton")
        await view(observer, owner_id, None, True)
        source = await torch(owner, "visor", "on")
        assert source[:2] == ["true", "true"], source
        await worn_views(observer, owner_id, True)
        beam = await torch(observer, "visor", f"observe{owner_id}")
        assert beam[0] == "2" and beam[1:5] == source[3:7] and float(beam[-1]) < 25, (source, beam)
        await torch(owner, "visor", "turn")
        async with asyncio.timeout(4):
            while True:
                turned = await torch(owner, "visor", "read")
                observed = await torch(observer, "visor", f"observe{owner_id}")
                error = angular_error(turned[7:10], observed[5:8])
                if error < 8 and angular_error(beam[5:8], observed[5:8]) > 15:
                    break
                await asyncio.sleep(.12)
        assert (await torch(owner, "visor", "off"))[:2] == ["false", "false"]
        await worn_views(observer, owner_id, False)
        await view(observer, owner_id, None, True)
        assert await torch(observer, "visor", f"observe{owner_id}") == ["1"]
        await torch(owner, hand, "lightoff")
        await view(observer, owner_id, None, False)
        return {"source": source, "observer": beam, "turned_error_degrees": error, "held_light_stayed_on": True}

    host_visor = await worn(host, guest, host_id, offhand, stock)
    guest_visor = await worn(guest, host, guest_id, guest_mode, guest_stock)
    assert campaign_save_hashes(host.campaign) == checkpoint, "Flashlight controls caused a premature save"
    angle = {"host_initial": angular_error(host_stock_light[9:12], regular_beam["direction"]),
             "guest_initial": angular_error(guest_stock_light[9:12], host_beam["direction"]),
             "host_turned": host_turn["error_degrees"], "guest_turned": guest_turn["error_degrees"]}
    assert all(value < 8 for value in angle.values()), (angle, host_turn, guest_turn)
    return {"stock_flashlights": {"host_held": host_held, "guest_held_beam": regular_beam,
                                  "host_offhand": host_offhand, "guest_offhand_beam": offhand_beam,
                                  "turned_offhand": turned, "guest_aimed_beam": aimed_beam,
                                  "guest_offhand": guest_offhand, "host_offhand_beam": host_offhand_beam,
                                  "guest_held": guest_held,
                                  "host_held_beam": host_beam, "held_aim_error_degrees": angle,
                                  "host_held_turn": host_turn, "guest_held_turn": guest_turn,
                                  "host_visor": host_visor, "guest_visor": guest_visor,
                                  "cleared_on_holster": True, "campaign_checkpoint_unchanged": True}}


async def verify_resident_voices(host, guest, command):
    async def move(action):
        assert await asyncio.gather(*(command(seat, action) for seat in (host, guest))) == ["moved", "moved"]

    def at_distance(frame, minimum, maximum):
        pawns = frame.get("pawns", {})
        local, remote = (pawns.get(seat, {}).get("location") for seat in ("local", "guest"))
        return local and remote and minimum <= math.dist(local, remote) <= maximum

    receipts = {}
    assert await asyncio.gather(*(command(seat, "voice-character") for seat in (host, guest))) == ["voice selected"] * 2
    await move("voice-near")
    try:
        audible = await host.bridge.wait_for(lambda frame: at_distance(frame, 2800, 3300)
                                            and len(frame.get("native_voice_speakers", {})) == 2)
        await guest.bridge.wait_for(lambda frame: at_distance(frame, 2800, 3300))
        for sender, listener, role, kind in ((host, guest, "local", "hurt"), (guest, host, "guest", "cough")):
            profile = audible["native_voice_speakers"][audible["pawns"][role]["name"]]
            assert len(profile) == 3, profile
            if role == "guest":
                assert profile[0] == "Fem_02" and abs(profile[1] - .94) < .001, profile
            assert await asyncio.gather(*(command(seat, "voice-clear") for seat in (host, guest))) == ["cleared", "cleared"]
            await sender.bridge.command("native-voice-fixture", kind)
            async with asyncio.timeout(6):
                while not (heard := await command(listener, "voice-read")):
                    await asyncio.sleep(.05)
            await asyncio.sleep(.35)
            assert await command(listener, "voice-read") == heard and not await command(sender, "voice-read")
            cue, gain, pitch, x, y, z = heard.split(",")
            point = [float(x), float(y), float(z)]
            asset = f"PV_{profile[0].replace('_', '')}_{kind.title()}_Cue"
            assert cue == f"SoundCue /Game/Assets/Sounds/Voclas/Player_Vocals/{asset}.{asset}" and float(gain) == 2, heard
            assert abs(float(pitch) - profile[1]) < .001 and math.dist(point, profile[2]) < 35, (profile, heard)
            receipts[kind] = {"cue": cue, "gain": float(gain), "pitch": float(pitch), "origin": point,
                              "listener_distance_cm": math.dist(audible["pawns"]["local"]["location"],
                                                                audible["pawns"]["guest"]["location"]),
                              "observer_replays": 1, "sender_echoes": 0}
        await move("voice-far")
        await asyncio.gather(*(seat.bridge.wait_for(lambda frame: at_distance(frame, 5700, 6300))
                               for seat in (host, guest)))
        assert await asyncio.gather(*(command(seat, "voice-clear") for seat in (host, guest))) == ["cleared", "cleared"]
        far = await guest.bridge.command("native-voice-fixture", "gasp")
        assert far["ack"]["message"] == "stock voice call-out invoked"
        await asyncio.sleep(1.5)
        assert not await command(host, "voice-read") and not await command(guest, "voice-read")
        receipts["out_of_range"] = True
    finally:
        await move("voice-restore")
    return receipts


async def verify_resident_campfire(host: Seat, guest: Seat, pawn: str) -> dict:
    async def command(seat, action):
        return await portable_probe_command(seat.bridge, "portable-campfire", action)

    async def sample(seat):
        own, remote, overlay, audio, muted, fire, peer_fire, voices, offset, inside, peer_inside, own_regen, remote_regen = (
            await command(seat, "read")).split(",")
        return {"local_health": float(own), "remote_health": float(remote), "overlays": int(overlay), "burn_hits": int(audio),
                "muted_remote_burn_hits": int(muted), "local_fire_pulses": int(fire), "remote_fire_pulses": int(peer_fire),
                "hurt_voices": int(voices), "remote_hit_offset": float(offset),
                "local_overlap": inside == "true", "remote_overlap": peer_inside == "true",
                "local_regen": float(own_regen), "remote_regen": float(remote_regen)}

    async def wait_for(seat, predicate, timeout=7):
        result = None
        try:
            async with asyncio.timeout(timeout):
                while True:
                    result = await sample(seat)
                    if predicate(result):
                        return result
                    await asyncio.sleep(.1)
        except TimeoutError as error:
            raise AssertionError(f"Campfire observation timed out after {result}") from error

    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = campaign_save_hashes(host.campaign)
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("local_bridge") == "memory")
    assert all(result == "armed" for result in await asyncio.gather(*(command(seat, "arm") for seat in (host, guest))))
    voices = await verify_resident_voices(host, guest, command)
    assert all(result == "cleared" for result in await asyncio.gather(*(command(seat, "clear") for seat in (host, guest))))
    baseline = await sample(host)
    assert not baseline["local_overlap"] and not baseline["remote_overlap"], baseline
    sickness, coefficient, floor, maximum = map(float, (await command(host, "regen-policy")).split(","))
    assert 0 <= sickness <= 100 and 0 < coefficient < 1 and 0 <= floor < 1 and maximum > 0
    assert baseline["remote_regen"] == baseline["remote_health"] == maximum, baseline

    assert all(result == "moved" for result in await asyncio.gather(command(host, "enter-peer"), command(guest, "enter")))
    try:
        remote_hit = await wait_for(host, lambda hit: hit["remote_fire_pulses"] > 0 and hit["remote_health"] < baseline["remote_health"])
    finally:
        await asyncio.gather(command(guest, "leave"), command(host, "leave-peer"))
    host_observed = await sample(host)
    assert remote_hit["remote_hit_offset"] > 1, remote_hit
    guest_heard = await wait_for(guest, lambda hit: hit["overlays"] > 0 and hit["burn_hits"] > 0)
    assert remote_hit["local_health"] == host_observed["local_health"] == baseline["local_health"], (baseline, host_observed)
    assert host_observed["overlays"] == host_observed["burn_hits"] == 0 and host_observed["muted_remote_burn_hits"] > 0, host_observed
    assert guest_heard["burn_hits"] > 0 and guest_heard["local_health"] < baseline["remote_health"], guest_heard
    received = [entry for entry in await resident_events(guest_run, guest_frame)
                if entry["event"] == "native_guest_hurt_played" and entry["sound_mask"] & 4]
    assert received, guest_heard
    damage = baseline["remote_health"] - remote_hit["remote_health"]
    expected_ceiling = max(baseline["remote_regen"] - damage * (coefficient + (1 - coefficient) * sickness / 100),
                           maximum * floor)
    assert remote_hit["remote_health"] < expected_ceiling < maximum, (remote_hit, expected_ceiling)
    assert abs(remote_hit["remote_regen"] - expected_ceiling) < .01, (remote_hit, expected_ceiling)
    assert host_observed["local_regen"] == baseline["local_regen"], (baseline, host_observed)
    def healing_pulse(frame):
        proof = frame["game"].get("native_guest_regen", {}).get(pawn, {})
        return proof.get("state") == "applied" and proof["health_after"] > proof["health_before"]

    guest_recovered, healing_frame = await asyncio.gather(
        wait_for(guest, lambda hit: abs(hit["local_health"] - expected_ceiling) <= .2
                 and abs(hit["local_regen"] - expected_ceiling) < .01, timeout=25),
        wait_resident_status(host_run, healing_pulse, timeout=25),
    )
    healing_game = healing_frame["game"]
    healing = healing_game["native_guest_regen"][pawn]
    published = healing_game["pawns"]["guest"]
    assert published["name"] == pawn and abs(published["health"] - healing["health_after"]) < 1e-7, (published, healing)
    remote_recovered = await sample(host)
    assert abs(remote_recovered["remote_health"] - expected_ceiling) <= .2, remote_recovered
    assert abs(remote_recovered["remote_regen"] - expected_ceiling) < .01, remote_recovered

    await asyncio.sleep(1)
    assert all(result == "cleared" for result in await asyncio.gather(*(command(seat, "clear") for seat in (host, guest))))
    baseline = await sample(host)
    assert not baseline["local_overlap"] and not baseline["remote_overlap"], baseline
    assert await command(host, "enter") == "moved"
    try:
        local_hit = await wait_for(host, lambda hit: hit["local_fire_pulses"] > 0 and hit["local_health"] < baseline["local_health"])
    finally:
        await command(host, "leave")
    assert local_hit["overlays"] > 0 and local_hit["burn_hits"] > 0, local_hit
    assert local_hit["remote_health"] >= baseline["remote_health"] and local_hit["remote_regen"] == baseline["remote_regen"], (
        baseline, local_hit)
    guest_observed = await sample(guest)
    assert guest_observed["overlays"] == guest_observed["burn_hits"] == 0, guest_observed

    assert all(result == "cleared" for result in await asyncio.gather(*(command(seat, "clear") for seat in (host, guest))))
    burst_host_before = await sample(host)
    burst_started = time.time()
    burst_health = list(map(float, (await command(host, "burst-peer")).split(",")))
    burst_guest = await wait_for(guest, lambda hit: hit["burn_hits"] > 0 and hit["hurt_voices"] > 0)
    burst_host = await sample(host)
    burst_audio = await asyncio.gather(command(host, "burst-audio"), command(guest, "burst-audio"))
    assert burst_audio == ["0,1", "1,0"], burst_audio
    assert burst_guest["burn_hits"] == burst_guest["hurt_voices"] == 1, burst_guest
    assert burst_host["overlays"] == burst_host["burn_hits"] == burst_host["hurt_voices"] == 0, burst_host
    assert burst_host["muted_remote_burn_hits"] == 1 and burst_host["local_health"] >= burst_host_before["local_health"], burst_host
    burst_cues = [entry for entry in await resident_events(guest_run, guest_frame)
                  if entry["event"] == "native_guest_hurt_played" and entry["time_unix"] >= burst_started]
    assert len(burst_cues) == 1 and burst_cues[0]["sound_mask"] == 6 and burst_cues[0]["damage"] == 8, burst_cues
    assert burst_cues[0]["hit_direction"] == [-15, 0, 0], burst_cues
    assert campaign_save_hashes(host.campaign) == checkpoint, "Campfire damage triggered an early save"
    return {"resident_stock_voices": voices, "resident_campfire": {"guest_authority_damage": remote_hit, "host_observer": host_observed,
                                  "guest_feedback": guest_heard, "guest_feedback_events": received,
                                  "stock_guest_regeneration_ceiling": expected_ceiling,
                                  "guest_recovered": guest_recovered, "authoritative_guest_recovered": remote_recovered,
                                  "healing_pulse_status_health": published["health"], "healing_pulse": healing,
                                  "host_feedback": local_hit, "guest_observer": guest_observed,
                                  "same_tick_hits": {"health": burst_health, "guest": burst_guest,
                                                     "host": burst_host, "blunt_audio": burst_audio, "cue": burst_cues[0]},
                                  "campaign_checkpoint_unchanged": True}}


def unequip_rows(raw):
    result = []
    for entry in raw.split("|"):
        guid, equipped, backpack, rounds, tag = entry.split(",", 4)
        assert re.fullmatch(r"[0-9A-F]{32}", guid) and (equipped, backpack) in {("1", "0"), ("0", "1")}
        result.append({"guid": guid, "source": "equipment" if equipped == "1" else "backpack",
                       "rounds": int(rounds), "data_tag": tag})
    assert len(result) == len({row["guid"] for row in result}) == 3
    return result


def unequip_receipt(raw):
    before, inactive, after, selected, held, retired, live = raw.split(";")
    assert retired in {"true", "false"} and held in {"pistol", "fallback", "other"}
    return {"before": unequip_rows(before), "after_inactive": unequip_rows(inactive), "after_selected": unequip_rows(after),
            "selected_slot": int(selected), "held": held, "original_pistol_retired": retired == "true",
            "live_rounds": int(live)}


def unequip_seat(raw):
    rows, selected, held, retired, ammo = raw.split(";")
    assert retired in {"true", "false"} and held in {"pistol", "fallback", "other"}
    return {"rows": unequip_rows(rows), "slot": int(selected), "held": held, "retired": retired == "true",
            "ammo": int(ammo) if ammo != "-" else None}


async def verify_stock_unequip(first, second, evidence):
    probe = "portable-unequip"

    def saves():
        return (campaign_save_hashes(first.campaign), saved_hashes(first.profile / "Saved/SaveGames"),
                saved_hashes(second.profile / "Saved/SaveGames"))

    async def await_current_mirror(host):
        campaign = menu_host_checkpoint(host.campaign, profile=host.profile, require_scoped=True)
        assert campaign is not None
        campaign_id = campaign["campaign_id"]
        backup = host.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{uuid.UUID(campaign_id).hex}.sav"
        async with asyncio.timeout(25):
            while True:
                try:
                    with ZipFile(backup) as archive:
                        mirrored = json.loads(archive.read("campaign.json"))
                    if (mirrored["campaign_id"], mirrored["revision"]) == (campaign_id, campaign["revision"]):
                        return
                except (FileNotFoundError, PermissionError):
                    pass
                await asyncio.sleep(.15)

    async def solo_baseline(host, _guest, _character):
        await await_current_mirror(host)
        before = saves()
        result = unequip_receipt(await portable_probe_command(host.bridge, probe, "baseline"))
        evidence["solo_stock_baseline"] = result
        pistol, fallback, control = result["before"]
        assert (pistol["rounds"], result["live_rounds"]) == (5, 3), result
        moved_control = dict(control, source="backpack")
        assert result["after_inactive"] == [pistol, fallback, moved_control], result
        packed, returned_fallback, returned_control = result["after_selected"]
        assert packed == dict(pistol, source="backpack", rounds=packed["rounds"]) and packed["rounds"] in (3, 5)
        assert (returned_fallback, returned_control) == (fallback, moved_control)
        assert (result["selected_slot"], result["held"]) == (4, "fallback"), result
        result["saved_live_rounds"] = packed["rounds"] == result["live_rounds"]
        assert saves() == before, "The solo stock Unequip wrote a save before a checkpoint"

    async def guest_stock_actions(host, guest, pawn):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        for run in (host_run, guest_run):
            await wait_resident_status(run, lambda frame: frame.get("local_bridge") == "memory")

        async def witness(event, field, guid, since=0):
            async with asyncio.timeout(15):
                while True:
                    frame = await wait_resident_status(guest_run, lambda row: row.get("state") == "connected")
                    entries = await resident_events(guest_run, frame)
                    matches = [row for row in entries if row.get("event") == event and row.get(field, {}).get("guid") == guid
                               and row["time_unix"] >= since]
                    assert len(matches) <= 1, matches
                    if matches:
                        return matches[0]
                    rejected = [row for row in entries if row.get("event") == "native_stock_inventory_rejected"]
                    if event == "native_stock_inventory_action" and rejected:
                        raise RuntimeError(rejected[-1]["reason"])
                    await asyncio.sleep(.15)

        async def leaves_stock_open():
            await wait_resident_status(guest_run, lambda frame:
                frame.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")

        async def close_stock():
            assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
            await wait_resident_status(guest_run, lambda row:
                row.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")

        await await_current_mirror(host)
        before = saves()
        ids = (await portable_probe_command(host.bridge, probe, f"remote-seed\t{pawn}")).split(",")
        assert len(ids) == len(set(ids)) == 3 and all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in ids)
        authority = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))["rows"]
        assert [row["guid"] for row in authority] == ids
        # TAB can reuse the authenticated join view before the next ordinary inventory poll.
        # Wait for the newly seeded exact slots before the screen builds its quick-slot cards.
        projection = {"expected": ids}
        evidence["guest_initial_projection"] = projection
        async with asyncio.timeout(15):
            while True:
                seen = (await portable_probe_command(guest.bridge, probe, "guest-projection")).split(",")
                projection["observed"] = seen
                if seen == ids:
                    break
                await asyncio.sleep(.25)
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await leaves_stock_open()
        guest_before = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-ready\t" + ",".join(ids)))
        assert guest_before["rows"] == authority
        selection = await witness("native_guest_equipment_selected", "native_guest_weapon", ids[0])
        host_before = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-ready\t{pawn}"))
        assert host_before == guest_before and host_before["held"] == "pistol" and host_before["ammo"] == 5
        evidence["guest_before"] = {"guest": guest_before, "host": host_before, "selection": selection}

        canceled = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-cancel-inactive"))
        assert canceled == host_before
        current = await wait_resident_status(guest_run, lambda row: row.get("state") == "connected")
        entries = await resident_events(guest_run, current)
        assert all(row.get("event") != "native_stock_inventory_action" for row in entries), entries
        await portable_probe_command(guest.bridge, probe, "guest-press-inactive")
        inactive = await witness("native_stock_inventory_action", "native_inventory_unequip", ids[2])
        assert (inactive["native_inventory_unequip"]["slot"], inactive["stock_inventory_proof"]) == (
            5, "verified stock unequip from slot 5"), inactive
        rows = [*authority[:2], dict(authority[2], source="backpack")]
        guest_inactive = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read"))
        host_inactive = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))
        assert guest_inactive == host_inactive == dict(host_before, rows=rows), (guest_inactive, host_inactive)
        evidence["guest_inactive"] = {"guest": guest_inactive, "host": host_inactive, "receipt": inactive,
                                      "canceled_drag_kept_slot": True}
        await leaves_stock_open()

        changed = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-ammo\t{pawn}"))
        assert changed == dict(host_inactive, ammo=3), changed
        stock_press = await portable_probe_command(guest.bridge, probe, "guest-press-selected")
        before_press, held_actor = stock_press.rsplit(";", 1)
        locally_pressed = unequip_seat(before_press)
        assert locally_pressed == dict(guest_inactive, ammo=2), locally_pressed
        evidence["guest_selected_press"] = {"state": locally_pressed, "stock_actor": dict(zip(
            ("authority", "instigated_by_pawn", "held_by_player", "being_destroyed"), held_actor.split(","), strict=True))}
        try:
            selected = await witness("native_stock_inventory_action", "native_inventory_unequip", ids[0])
        except (RuntimeError, TimeoutError):
            evidence["guest_selected_after_timeout"] = {
                "guest": unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read")),
                "host": unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}")),
            }
            raise
        assert (selected["native_inventory_unequip"]["slot"], selected["native_inventory_unequip"]["ammo"],
                selected["stock_inventory_proof"]) == (1, 3, "verified stock unequip from slot 1"), selected
        guest_after = unequip_seat(await portable_probe_command(guest.bridge, probe, "guest-read"))
        host_after = unequip_seat(await portable_probe_command(host.bridge, probe, f"remote-read\t{pawn}"))
        expected_rows = [dict(rows[0], source="backpack", rounds=3), *rows[1:]]
        for state in (guest_after, host_after):
            assert state["rows"] == expected_rows and (state["slot"], state["held"], state["retired"]) == (
                4, "fallback", True), state
        evidence["guest_selected"] = {"guest": guest_after, "host": host_after, "receipt": selected,
                                      "host_live_rounds": changed["ammo"], "guest_live_rounds": locally_pressed["ammo"]}
        await close_stock()

        worn = (("native_guest_helmet", 8, "guest-equip-helmet", "guest-remove-helmet"),
                ("native_guest_armor", 9, "guest-equip-armor", "guest-drag-armor"))
        seeded_worn = (await portable_probe_command(host.bridge, probe, f"remote-worn-seed\t{pawn}")).split(",")
        assert len(seeded_worn) == len(set(seeded_worn)) == 4
        assert all(re.fullmatch(r"[0-9A-F]{32}", guid) for guid in seeded_worn)
        worn_guids, replacements = seeded_worn[:2], seeded_worn[2:]
        async with asyncio.timeout(15):
            while not set(seeded_worn).issubset(
                    (await portable_probe_command(guest.bridge, probe, "guest-worn-backpack")).split(",")):
                await asyncio.sleep(.25)
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await leaves_stock_open()
        current_worn = ["-", "-"]
        evidence["guest_worn_equip"] = []
        for index, ((key, slot, equip, _), guid) in enumerate(zip(worn, worn_guids, strict=True)):
            assert (await portable_probe_command(guest.bridge, probe, f"{equip}\t{guid}")).split(",") == current_worn
            receipt = await witness("native_stock_inventory_action", key, guid)
            assert receipt["stock_inventory_proof"] == f"verified stock equipment slot {slot}", receipt
            proof = receipt[key]
            if slot == 8:
                assert proof["class_path"].endswith("/Helm_FaceMask.Helm_FaceMask_C"), proof
            else:
                assert proof["class_path"].endswith("/Armor_LabCoat.Armor_LabCoat_C") and proof["minimum"] == 22, proof
            current_worn[index] = guid
            assert (await portable_probe_command(guest.bridge, probe, "guest-worn-state")).split(",") == current_worn
            authoritative = await submit(guest_run, "native-inventory-list")
            rows = [row for row in authoritative["native_inventory"]["items"] if row["guid"] == guid]
            source = "helmet" if slot == 8 else "armor"
            assert len(rows) == 1 and rows[0]["source"] == source, authoritative
            evidence["guest_worn_equip"].append({"receipt": receipt, "worn_row": rows[0]})
        evidence["guest_worn_swap"] = []
        for index, ((key, slot, equip, _), guid) in enumerate(zip(worn, replacements, strict=True)):
            displaced = current_worn[index]
            assert (await portable_probe_command(guest.bridge, probe, f"{equip}\t{guid}")).split(",") == current_worn
            receipt = await witness("native_stock_inventory_action", key, guid)
            assert receipt["stock_inventory_proof"] == f"verified stock equipment slot {slot}", receipt
            assert receipt[key]["replaced_guid"] == displaced, receipt
            current_worn[index] = guid
            assert (await portable_probe_command(guest.bridge, probe, "guest-worn-state")).split(",") == current_worn
            inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
            sources = {row["guid"]: row["source"] for row in inventory}
            assert (sources.get(displaced), sources.get(guid)) == ("backpack", "helmet" if slot == 8 else "armor")
            evidence["guest_worn_swap"].append(receipt)
        evidence["guest_worn"] = []
        for index, ((_, slot, _, action), guid) in enumerate(zip(worn, replacements, strict=True)):
            assert (await portable_probe_command(guest.bridge, probe, f"{action}\t{guid}")).split(",") == current_worn
            receipt = await witness("native_stock_inventory_action", "native_inventory_unequip", guid)
            assert (receipt["native_inventory_unequip"]["slot"], receipt["stock_inventory_proof"]) == (
                slot, f"verified stock unequip from slot {slot}"), receipt
            current_worn[index] = "-"
            assert (await portable_probe_command(guest.bridge, probe, "guest-worn-state")).split(",") == current_worn
            authoritative = await submit(guest_run, "native-inventory-list")
            assert authoritative["ok"], authoritative
            rows = [row for row in authoritative["native_inventory"]["items"] if row["guid"] == guid]
            assert len(rows) == 1 and rows[0]["source"] == "backpack", authoritative
            evidence["guest_worn"].append({"receipt": receipt, "backpack_row": rows[0]})

        assert saves() == before, "The guest stock Unequip wrote a save before a checkpoint"
        evidence["guest_worn_world_drop"] = []
        for (key, slot, equip, _), guid in zip(worn, worn_guids, strict=True):
            started = time.time()
            await portable_probe_command(guest.bridge, probe, f"{equip}\t{guid}")
            await witness("native_stock_inventory_action", key, guid, since=started)
            action = "guest-drop-helmet" if slot == 8 else "guest-drop-armor"
            await portable_probe_command(guest.bridge, probe, f"{action}\t{guid}")
            accepted = await witness("native_stock_inventory_action", native_guest_drop.RESULT, guid)
            assert accepted["stock_inventory_proof"] == f"verified stock world drop from slot {slot}", accepted
            receipt = accepted[native_guest_drop.RESULT]
            identity = native_world_loot.player_drop_identity(receipt["drop_id"])
            for run in (host_run, guest_run):
                await wait_resident_status(run, lambda frame: identity in frame.get("game", {}).get(native_world_loot.KEY, {}))
            assert (await portable_probe_command(guest.bridge, probe, "guest-worn-state")) == "-,-"
            guest_items = (await submit(guest_run, "native-inventory-list"))["native_inventory"]["items"]
            assert all(row["guid"] != guid for row in guest_items), guest_items
            host_state = await host.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
            local_pawn = host_state["pawns"]["local"]["name"]
            approached = await host.bridge.command(native_world_loot.APPROACH, identity, local_pawn, timeout=5)
            assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
            picked = await host.bridge.command(native_world_loot.FIXTURE, identity, timeout=5)
            assert picked["ack"]["message"] == "stock world loot interaction intercepted", picked["ack"]
            for run in (host_run, guest_run):
                await wait_resident_status(run, lambda frame: identity not in frame.get("game", {}).get(native_world_loot.KEY, {})
                    and receipt["drop_id"] not in frame.get("gameplay", {}).get("native_guest_stack_drops", {}))
            host_items = (await submit(host_run, "native-inventory-list"))["native_inventory"]["items"]
            recovered, = [row for row in host_items if row["guid"] == guid]
            assert (recovered["source"], recovered["class_path"], recovered["quantity"]) == (
                "backpack", receipt["class_path"], 1), recovered
            evidence["guest_worn_world_drop"].append({"receipt": receipt, "host_pickup": recovered})
        assert saves() == before, "The guest worn drop or host pickup wrote a save before a checkpoint"
        await close_stock()
        mask = worn_guids[0]
        await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("native_host_stack_drop_scope"))
        assert await portable_probe_command(host.bridge, probe, f"host-wear-mask\t{mask}") == mask
        host_frame = await wait_resident_status(host_run, lambda frame: frame.get("state") == "hosting")
        await portable_probe_command(host.bridge, probe, f"host-drop-mask\t{mask}")
        async with asyncio.timeout(10):
            while True:
                drops = [row for row in await resident_events(host_run, host_frame)
                         if row.get("event") == "native_host_stock_drop" and row.get("entry") == "equipped_context_menu"]
                if drops:
                    assert len(drops) == 1 and not drops[0].get("error"), drops
                    dropped = drops[0][native_guest_drop.RESULT]
                    assert dropped["guid"] == mask, dropped
                    break
                await asyncio.sleep(.08)
        marker = native_world_loot.player_drop_identity(dropped["drop_id"])
        for run in (host_run, guest_run):
            await wait_resident_status(run, lambda frame: marker in frame.get("game", {}).get(native_world_loot.KEY, {}))
        evidence["host_face_mask_drop"] = dropped
        assert saves() == before, "The worn Face Mask drop wrote a save before a checkpoint"
        assert await portable_probe_command(host.bridge, probe, "host-close-inventory") == "closed"
        approached = await host.bridge.command(native_world_loot.APPROACH, marker, pawn, timeout=5)
        assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
        await wait_resident_status(guest_run, lambda frame: math.dist(
            frame.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]),
            dropped["location"]) < 225, timeout=8)
        frame = await guest.bridge.wait_for(lambda state: marker in state.get(native_world_loot.KEY, {}))
        actor = frame[native_world_loot.KEY][marker]["actor"].split(" ", 1)[1]
        assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
        await leaves_stock_open()
        picked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{actor}@drag")
        assert picked["ok"] and picked["proof"] == "stock Nearby Items card requested", picked
        for run in (host_run, guest_run):
            await wait_resident_status(run, lambda frame: marker not in frame.get("game", {}).get(native_world_loot.KEY, {}))
        authoritative = await submit(guest_run, "native-inventory-list")
        restored, = [row for row in authoritative["native_inventory"]["items"] if row["guid"] == mask]
        assert (restored["source"], restored["class_path"], restored["quantity"]) == ("backpack", dropped["class_path"], 1)
        displayed = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{actor}@{mask}")
        assert displayed["ok"] and displayed["proof"] == "verified stock Nearby Items transfer", displayed
        evidence["guest_reclaimed_face_mask"] = {"item": restored, "stock_drag": displayed["proof"]}
        started = time.time()
        assert await portable_probe_command(guest.bridge, probe, f"guest-equip-helmet	{mask}") == "-,-"
        equip = await witness("native_stock_inventory_action", "native_guest_helmet", mask, since=started)
        assert equip["stock_inventory_proof"] == "verified stock equipment slot 8", equip
        assert await portable_probe_command(guest.bridge, probe, "guest-worn-state") == f"{mask},-"
        evidence["guest_equipped_reclaimed_face_mask"] = equip
        assert saves() == before, "The Face Mask pickup and equip wrote a save before a checkpoint"
        await close_stock()
        action = "native-guest-save-collection-fixture"
        response = await host.bridge.command(action, "start")
        assert response["ack"]["message"] == "requested save collection"
        async with asyncio.timeout(20):
            while True:
                response = await host.bridge.command(action, "status")
                retained_result = response["ack"]["message"]
                if retained_result != "save collection pending":
                    break
                await asyncio.sleep(.15)
        assert retained_result == "detached save retained through collection and released", retained_result
        evidence["host_detached_save_collection"] = retained_result
        assert saves() == before, "Retaining a detached save through collection wrote a campaign file"
        return {"selected_and_inactive_slots_checked": True, "guest_duplicate_clicks_rejected": True,
                "guest_worn_slots_checked": True, "guest_equipped_host_drop": True,
                "saves_unchanged_before_checkpoint": True}

    return await start_pair(first, second, "Unequip probe scout", before_join=solo_baseline, on_join=guest_stock_actions)


async def verify_stock_prop_grip(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory"
             and state.get("game", {}).get(native_prop_carry.FILTER_READY) is True),
    )
    approach = [float(value) for value in (await portable_probe_command(
        host.bridge, "portable-lantern", f"approach\t{pawn}")).split()]
    await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
               and math.dist(point, approach) < 100)
    await asyncio.sleep(1.25)
    lantern_class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
    grabbed = await submit(guest_run, native_prop_carry.FIXTURE, target=f"pickup@{lantern_class}|{native_lantern.ACTOR}")
    assert grabbed["ok"], grabbed

    async def edge(name, lease=None):
        observed = await wait(host_run, lambda state: (row := state.get("game", {}).get(native_prop_carry.PROOF, {}))
                              and row["edge"] == name and row["actor"] == native_lantern.ACTOR and row["pawn"] == pawn
                              and (lease is None or row["lease"] == lease))
        return observed["game"][native_prop_carry.PROOF]

    async def sample():
        held, *position = (await portable_probe_command(host.bridge, "portable-lantern", f"sample\t{pawn}")).split()
        return held == "1", [float(value) for value in position]

    grab = await edge("grab")
    confirmations = [row for row in await resident_events(host_run, host_status)
                     if row["event"] == "native_prop_carry_action" and row.get("lease") == grab["lease"]
                     and row.get("edge") == "grab"]
    confirmation, = confirmations
    assert confirmation["filter_confirmed"], confirmation

    def active_counters(state):
        counters = state.get("game", {}).get(native_prop_carry.FILTER, {})
        return counters if counters.get("lease") == grab["lease"] else {}

    holding_state = await wait(guest_run, lambda state: (row := active_counters(state))
                               and row["active"] and row["suppressed"] > 0)
    held_filter = active_counters(holding_state)
    held, held_position = await sample()
    assert held, held_position
    before = (await wait(host_run, lambda state: state.get("transport", {}).get("prop_pose_datagrams_received", 0) > 0))
    prior_count = before["transport"]["prop_pose_datagrams_received"]
    local_pawn = holding_state["game"]["pawns"]["local"]["name"]
    assert await portable_probe_command(guest.bridge, "portable-lantern", f"sweep\t{local_pawn}") == "sweeping"
    await asyncio.sleep(1.35)
    moved, swept_position = await sample()
    after = await wait(host_run, lambda state: state.get("updated_unix", 0) > before["updated_unix"] + 1.2)
    streamed = after["transport"]["prop_pose_datagrams_received"] - prior_count
    assert moved and math.dist(held_position, swept_position) > 25 and streamed > 0, (swept_position, streamed)
    dropped = await submit(guest_run, native_prop_carry.FIXTURE, target="drop")
    assert dropped["ok"], dropped
    drop = await edge("drop", grab["lease"])
    assert drop["sequence"] > grab["sequence"] and drop.get("release_pose_applied") is True, drop
    resumed_state = await wait(guest_run, lambda state: (row := active_counters(state))
                               and not row["active"] and row["passed"] > held_filter["passed"])
    async with asyncio.timeout(8):
        while True:
            held, released_position = await sample()
            if not held and math.dist(held_position, released_position) > 25:
                break
            await asyncio.sleep(.1)
    assert not (host.bridge.directory / "native-prop-carry-poses").exists()
    return {
        "local_bridge": {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]},
        "authored_prop": native_lantern.ACTOR, "grab": grab, "drop": drop,
        "grab_receipt_confirmed_filter": True,
        "filter_while_held": held_filter, "filter_after_drop": active_counters(resumed_state),
        "held_position": held_position, "released_position": released_position,
        "stock_sweep": {"target_seconds": 1.2, "received": streamed,
                        "host_displacement_cm": math.dist(held_position, swept_position),
                        "swept_position": swept_position},
    }


def lantern_angle_distance(before, after):
    return max(abs((first - second + 180) % 360 - 180) for first, second in zip(before, after, strict=True))


def lantern_observer_trace(receipt, role):
    header, *lines, terminator = receipt.split("|")
    assert terminator == "end", "Incomplete lantern observation receipt"
    seen_role, sweep_end, frequency = header.split()
    end, configured_hz = int(sweep_end), float(frequency)
    samples = [tuple(map(float, line.split())) for line in lines]
    assert int(seen_role) == role and math.isfinite(configured_hz)
    assert 2 <= end < len(samples) <= 240
    assert all(len(row) == 9 and all(map(math.isfinite, row)) for row in samples)
    assert all(after[0] > before[0] for before, after in zip(samples, samples[1:]))
    hidden_at = next((row[0] for row in samples if row[7] != 1), None)
    assert hidden_at is None, ("The exact stock lantern became hidden during the trace", role, hidden_at)
    sweep = samples[:end]
    assert all(row[8] == (1 if role == 3 else 0) for row in sweep) and samples[-1][8] == 0
    if role == 1:
        assert all(row[8] == 0 for row in samples), "The guest must observe without grabbing the lantern"
    steps = [math.dist(before[1:4], after[1:4]) for before, after in zip(sweep, sweep[1:])]
    max_rotation_step = max(lantern_angle_distance(before[4:7], after[4:7]) for before, after in zip(sweep, sweep[1:]))
    changes = [after[0] for after, step in zip(sweep[1:], steps, strict=True) if step >= .25]
    intervals_ms = [1000 * (after - before) for before, after in zip(changes, changes[1:])]
    displacement = max(math.dist(sweep[0][1:4], row[1:4]) for row in sweep)
    assert displacement > 25, ("The stock lantern failed to move during the host's sweep", role, displacement)
    end_pose = samples[-1]
    tail = [row for row in samples[end:] if end_pose[0] - row[0] <= .25]
    settled = (len(tail) >= 3 and end_pose[0] - tail[0][0] >= .15 and all(
        math.dist(row[1:4], end_pose[1:4]) <= 2 and lantern_angle_distance(row[4:7], end_pose[4:7]) <= 5
        for row in tail))
    return {"role": role, "configured_net_update_hz": configured_hz, "sweep_end_sample": end,
            "sweep_displacement_cm": displacement, "position_changes_over_quarter_cm": len(changes),
            "longest_change_interval_ms": max(intervals_ms, default=None), "largest_sample_step_cm": max(steps),
            "longest_sample_interval_ms": 1000 * max(after[0] - before[0] for before, after in zip(sweep, sweep[1:])),
            "largest_sample_rotation_step_degrees": max_rotation_step, "settled": settled, "samples": samples}


async def verify_stock_host_prop_observer(host: Seat, guest: Seat, pawn: str, evidence: dict) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(*(
        wait(run, lambda state: state.get("local_bridge") == state.get("local_status_source") == "memory"
             and state.get("game", {}).get("pawns", {}).get("local", {}).get("name"))
        for run in (host_run, guest_run)
    ))
    host_pawn, guest_pawn = (state["game"]["pawns"]["local"]["name"] for state in (host_status, guest_status))
    assert host_pawn != pawn and host_status["game"]["pawns"]["guest"]["name"] == pawn
    evidence["local_bridge"] = {seat: state["local_bridge"]
                                for seat, state in (("host", host_status), ("guest", guest_status))}

    async def call(seat, action, *, reply_end=None):
        owner = host_pawn if seat is host else guest_pawn
        return await portable_probe_command(seat.bridge, "portable-lantern", f"{action}\t{owner}", reply_end=reply_end)

    evidence["authored_prop"] = native_lantern.ACTOR
    lantern_class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_Lantern_01.Prop_Lantern_01_C"
    checkpoint = campaign_save_hashes(host.campaign)

    async def verify_flame(lit):
        await asyncio.gather(*(wait_resident_status(run, lambda frame:
            frame.get("game", {}).get(native_lantern.KEY, {}).get("lit") is lit) for run in (host_run, guest_run)))

    await portable_probe_command(host.bridge, "portable-lantern", f"approach\t{pawn}")
    assert await call(host, "hostignite") == "lit"
    await verify_flame(True)
    switched = await submit(guest_run, native_prop_carry.FIXTURE, target=f"lamp-use@{lantern_class}|{native_lantern.ACTOR}")
    evidence["stock_ignition"] = {"both_seats_lit": True, "guest_interaction": switched}
    assert switched["ok"], switched
    await verify_flame(False)
    evidence["stock_ignition"]["both_seats_extinguished"] = True

    assert await call(host, "hostgrab") == "grabbed"
    await asyncio.sleep(.35)
    assert await asyncio.gather(call(host, "tracearm"), call(guest, "tracearm")) == ["armed", "armed"]
    assert await call(host, "hostsweep") == "sweeping"
    await asyncio.sleep(1.35)
    async with asyncio.timeout(3):
        while True:
            marked = await call(host, "tracemark")
            if marked == "marked":
                break
            assert marked == "sweeping", marked
            await asyncio.sleep(.05)
    assert await call(guest, "tracemark") == "marked"
    assert await call(host, "hostdrop") == "dropped"
    await asyncio.sleep(2)
    authority, observer = await asyncio.gather(
        call(host, "traceread", reply_end="|end"), call(guest, "traceread", reply_end="|end"))
    evidence["raw_sample_receipts"] = {"host": authority, "guest": observer}
    data = {name: lantern_observer_trace(receipt, role)
            for name, receipt, role in (("host", authority, 3), ("guest", observer, 1))}
    del evidence["raw_sample_receipts"]
    gap = math.dist(*(row["samples"][-1][1:4] for row in data.values()))
    evidence.update(stock_host_grab_and_drop=True,
                    sample_columns=["elapsed_s", "x", "y", "z", "pitch", "yaw", "roll", "visible", "local_grip"],
                    end_position_gap_cm=gap,
                    settled_position_gap_cm=gap if all(row["settled"] for row in data.values()) else None,
                    seats=data)
    position = tuple(map(float, (await call(host, "impactlane")).split()))
    await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
               and math.dist(point, position) < 45)

    def impact_frame(raw):
        before, after, target, settled, prop_response, target_response, final_response, impulses, mass, calls, attributed = raw.split(";")
        return {"hp": [float(hp) if hp != "-" else None for hp in (before, after)],
                "target": list(map(float, target.split())), "settled": list(map(float, settled.split())),
                "responses": list(map(int, (prop_response, target_response, final_response))),
                "impulses": list(map(float, impulses.split())), "mass_kg": float(mass),
                "damage_calls": int(calls), "attributed_calls": int(attributed)}

    async def guest_grab(class_path, actor, launch, position_action):
        async with asyncio.timeout(3):
            while math.dist(list(map(float, (await call(guest, position_action)).split()))[:2], launch[:2]) > 45:
                await asyncio.sleep(.05)
        grabbed = await submit(guest_run, native_prop_carry.FIXTURE, target=f"pickup@{class_path}|{actor}")
        assert grabbed["ok"], grabbed
        await wait(host_run, lambda frame:
                   (row := frame.get("game", {}).get(native_prop_carry.PROOF, {}))
                   and (row["edge"], row["pawn"], row["actor"]) == ("grab", pawn, actor))

    async def guest_throw(actor):
        thrown = await submit(guest_run, native_prop_carry.FIXTURE, target="throw")
        assert thrown["ok"], thrown
        await wait(host_run, lambda frame:
                   (row := frame.get("game", {}).get(native_prop_carry.PROOF, {}))
                   and (row["edge"], row["pawn"], row["actor"]) == ("throw", pawn, actor))

    async def impact_pair(class_path, actor, prefix="", *, require_protection=False):
        impacts = {}
        for thrower_name, thrower, recipient in (("host", host, "guest"), ("guest", guest, "host")):
            launch = list(map(float, (await call(host, f"{prefix}impactplace{thrower_name}")).split()))
            if thrower is host:
                assert await call(host, f"{prefix}impactgrab") == "grabbed"
            else:
                await guest_grab(class_path, actor, launch, f"{prefix}impactposition")
            assert await call(thrower, f"{prefix}impactaim") == "aimed"
            await asyncio.sleep(.35)
            assert await asyncio.gather(*(call(seat, f"{prefix}impactarm{recipient}") for seat in (host, guest))) == ["armed", "armed"]
            if thrower is host:
                evidence[f"{prefix}stock_host_throw_release"] = [list(map(float, value.split()))
                    for value in (await call(host, f"{prefix}impactthrow")).split(";")]
            else:
                await guest_throw(actor)
            await asyncio.sleep(1.25)
            impacts[recipient] = {name: impact_frame(raw) for name, raw in zip(
                ("host", "guest"), await asyncio.gather(
                    call(host, f"{prefix}impactread"), call(guest, f"{prefix}impactread")), strict=True)}
            frames = impacts[recipient]
            if require_protection:
                assert frames["host"]["attributed_calls"] == frames["host"]["damage_calls"] == 1, frames
                assert max(frames["host"]["impulses"], default=0) > 5000, frames
                assert frames["host"]["hp"][0] == frames["host"]["hp"][1] == frames["guest"]["hp"][1], frames
            assert math.dist(frames["host"]["settled"], frames["guest"]["settled"]) < 20, frames
        return impacts

    impacts = evidence["stock_impacts"] = await impact_pair(lantern_class, native_lantern.ACTOR)
    evidence["stock_contact_recipients"] = [name for name, frames in impacts.items()
                                            if max(frames["host"]["impulses"], default=0) > 0]
    toolbox_class = "/Game/Assets/StaticMeshes/Mesh_Props/Prop_ToolBox01.Prop_ToolBox01_C"
    toolbox = f"Prop_ToolBox01_C {OBSERVATORY}.Observitory_P:PersistentLevel.Prop_ToolBox01_C_7"
    evidence["stock_toolbox_impacts"] = await impact_pair(toolbox_class, toolbox, "heavy", require_protection=True)
    position = tuple(map(float, (await call(host, "heavyimpactlaneguestnpc")).split()))
    await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
               and math.dist(point, position) < 45)
    launch = list(map(float, (await call(host, "heavyimpactplaceguestnpc")).split()))
    await guest_grab(toolbox_class, toolbox, launch, "heavyimpactposition")
    assert await call(guest, "heavyimpactaimnpc") == "aimed"
    await asyncio.sleep(.35)
    assert await call(host, "heavyimpactarmguestnpc") == "armed"
    await guest_throw(toolbox)
    await asyncio.sleep(1.25)
    target = evidence["guest_toolbox_npc_impact"] = impact_frame(await call(host, "heavyimpactreadnpc"))
    assert target["attributed_calls"] == target["damage_calls"] == 1, target
    assert target["hp"][1] < target["hp"][0], target
    assert campaign_save_hashes(host.campaign) == checkpoint, "A stock prop impact wrote a campaign checkpoint"
    return {"stock_host_prop_observer": True,
            "stock_prop_contact_both_seats": len(evidence["stock_contact_recipients"]) == 2,
            "heavy_prop_contact_and_friendly_fire_protection_both_directions": True,
            "heavy_guest_prop_damaged_stock_npc": True}


async def verify_guest_shop_lease(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_status, guest_status = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory" and state.get("gameplay_fresh") is True),
    )
    area, actor = native_finite_shop.OBSERVATORY, native_finite_shop.ACTOR
    local_pawn = guest_status["game"]["pawns"]["local"]["name"]
    assert host_status["game"]["map"] == guest_status["game"]["map"] == area and local_pawn

    paths = tuple(guest.bridge.directory / name for name in (
        "native-shop-view-heartbeat", "native-broker-input-heartbeat",
        "native-dialogue-heartbeat", "native-mosby-station-heartbeat",
    ))

    def require_memory_only():
        files = [path.name for path in paths if os.path.lexists(path)]
        assert not files, files

    save_roots = ((host.campaign / "save", "campaign*.json"),
                  (host.profile / "Saved" / "SaveGames", "*.sav"),
                  (guest.profile / "Saved" / "SaveGames", "*.sav"))

    def saves():
        return tuple({path.name: (runtime._file_sha256(path), path.stat().st_mtime_ns) for path in root.glob(pattern)}
                     for root, pattern in save_roots)

    baseline = saves()
    assert all(baseline)
    require_memory_only()
    approached = await host.bridge.command("native-finite-shop-approach", pawn, timeout=5)
    assert approached["ack"]["message"] == "exact stock merchant approach verified", approached["ack"]
    position, = [row["location"] for row in approached["pawns"].values() if row.get("name") == pawn]

    def arrived(state):
        local = state.get("game", {}).get("pawns", {}).get("local", {})
        point = local.get("location")
        return local.get("name") == local_pawn and point and math.dist(point, position) < 50

    await wait(guest_run, arrived)
    opened = await submit(guest_run, "native-shop-view-fixture", target="open")
    assert opened["ok"], opened
    pending = opened["native_shop_view"]
    assert (pending["state"], pending["map"], pending["actor"], pending["pawn"]) == (
        "pending", area, actor, local_pawn), pending

    def stock_ready(state):
        panel = state.get("game", {}).get("native_shop_view", {})
        return (state.get("state") == "connected" and state.get("local_bridge") == "memory"
                and panel.get("state") == "ready" and panel.get("stock") is True and panel.get("visible", 0) > 0
                and all(panel.get(key) == pending[key] for key in ("sequence", "map", "actor", "pawn")))

    ready = await wait(guest_run, stock_ready)
    started = time.monotonic()
    while time.monotonic() - started < 3.2:
        require_memory_only()
        await asyncio.sleep(.1)
    require_memory_only()
    checked_seconds = round(time.monotonic() - started, 3)
    # A single integer-second timestamp expires in at most three wall-clock seconds.
    current = await wait(guest_run, lambda state: stock_ready(state)
                         and state["game"]["sequence"] > ready["game"]["sequence"], timeout=5)
    closed = await submit(guest_run, "native-shop-view-fixture", target="back")
    assert closed["ok"], closed
    await wait(guest_run, lambda state: state.get("game", {}).get("native_shop_view", {}).get("state") in (None, "closed"))
    require_memory_only()
    assert saves() == baseline, "Opening the stock shop changed a campaign or stock save"
    return {
        "local_bridge": {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]},
        "host_authority_pawn": pawn, "guest_local_pawn": local_pawn, "stock_menu": current["game"]["native_shop_view"],
        "native_game_sequence_before": ready["game"]["sequence"],
        "native_game_sequence_after": current["game"]["sequence"],
        "absent_heartbeat_files": [path.name for path in paths],
        "heartbeat_check_seconds": checked_seconds,
        "saves_unchanged_before_checkpoint": True,
    }


async def verify_guest_stash_memory(host: Seat, guest: Seat, pawn: str, *, large: bool = False,
                                    site_scan_probe: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    area = native_finite_shop.OBSERVATORY
    home, joined = await asyncio.gather(
        wait_resident_status(host_run, lambda row: row.get("local_bridge") == "memory"
                             and row.get("game", {}).get(native_stash.SITES)),
        wait_resident_status(guest_run, lambda row: row.get("local_bridge") == "memory"
                             and row.get("gameplay_fresh") and row.get("game", {}).get(native_stash.MEMORY_VIEW)),
    )
    assert home["game"]["map"] == joined["game"]["map"] == area
    if large:
        assert joined["game"].get(native_stash.LARGE_MEMORY_VIEW) is True, "The installed bridge lacks the large stash capability"
        assert home["local_status_source"] == joined["local_status_source"] == "memory"
    local_pawn = joined["game"]["pawns"]["local"]["name"]
    site, authored = min(home["game"][native_stash.SITES].items(), key=lambda pair: pair[1]["actor"])
    assert authored["map"] == area and home["game"][native_stash.LIVE_CENSUS] and home["game"][native_stash.LIVE_MOVE]
    approached = await host.bridge.command("native-stash-approach-fixture", pawn, site)
    assert approached["ack"]["message"] == "stock stash fixture is within interaction range", approached["ack"]
    position, = [row["location"] for row in approached["pawns"].values() if row.get("name") == pawn]

    def arrived(frame):
        local = frame.get("game", {}).get("pawns", {}).get("local", {})
        point = local.get("location")
        return local.get("name") == local_pawn and point and math.dist(point, position) < 50

    await wait_resident_status(guest_run, arrived)

    # Let the installed worker mirror the already committed guest creation
    # before measuring writes during the interactive stash window.
    checkpoint = menu_host_checkpoint(host.campaign, profile=host.profile, require_scoped=True)
    assert checkpoint is not None, "The installed host has no committed campaign to baseline"
    campaign_hex = uuid.UUID(checkpoint["campaign_id"]).hex
    backup = host.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{campaign_hex}.sav"
    async with asyncio.timeout(25):
        while True:
            try:
                with ZipFile(backup) as archive:
                    mirrored = json.loads(archive.read("campaign.json"))
                if (mirrored["campaign_id"], mirrored["revision"]) == (checkpoint["campaign_id"], checkpoint["revision"]):
                    break
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.15)

    def saves():
        roots = ((host.campaign / "save", "campaign*.json"),
                 (host.profile / "Saved/SaveGames", "*.sav"), (guest.profile / "Saved/SaveGames", "*.sav"))
        return tuple({str(path.relative_to(root)): (fingerprint(path), path.stat().st_mtime_ns)
                      for path in root.rglob(pattern) if path.is_file()}
                     for root, pattern in roots)

    def require_no_page_file():
        for pattern in ("native-stash-screen-*.tsv", ".native-stash-screen-*.tsv.*.tmp"):
            assert not list(guest.bridge.directory.glob(pattern)), pattern

    async def wait_for_stash_render(pending):
        def rendered(frame):
            require_no_page_file()
            return (frame.get("local_bridge") == "memory" and
                    frame.get("game", {}).get(native_stash.VIEW) == {**pending, "state": "rendered"})

        return (await wait_resident_status(guest_run, rendered))["game"][native_stash.VIEW]

    async def readback(pending, count, item="-"):
        observed = await wait_for_stash_render(pending)
        ack = await guest.bridge.command("portable-stash-memory-proof", str(pending["sequence"]), site)
        message = ack["ack"]["message"]
        proof = re.fullmatch(rf"portable stock stash memory {pending['sequence']} {count} {item} (-?[0-9]+)", message)
        assert proof, ack
        return observed, message, int(proof[1])

    async def close():
        assert (await submit(guest_run, "native-stash-view-click-fixture", target="close"))["ok"]
        await wait_resident_status(guest_run, lambda row: native_stash.VIEW not in row.get("game", {}))
        require_no_page_file()

    async def verify_site_scans():
        def same_box(frame):
            game = frame.get("game", {})
            return (game.get("state") == "hosting" and game.get("map") == area
                    and frame.get("zone_epoch") == home["zone_epoch"]
                    and game.get(native_stash.SITES, {}).get(site) == authored)

        first_frame = await wait_resident_status(host_run, same_box)
        first_sequence = first_frame["game"]["sequence"]
        started = time.monotonic()
        later_frame = await wait_resident_status(
            host_run, lambda frame: time.monotonic() - started >= 2.3 and same_box(frame)
            and frame["game"]["sequence"] >= first_sequence + 3, timeout=8)
        observed_seconds = round(time.monotonic() - started, 3)
        readback = await host.bridge.command("portable-stash-site-scan-reads", pawn, site, joined["character_id"])
        assert readback["ack"]["message"] == "portable stash site scans 1", readback["ack"]
        return {"world_scans": 1, "idle_observation_seconds": observed_seconds,
                "status_sequence_before": first_sequence, "status_sequence_after": later_frame["game"]["sequence"]}

    def require_unchanged_saves(before):
        changed = [(group, path) for group, (start, end) in enumerate(zip(before, saves(), strict=True))
                   for path in sorted(start.keys() | end.keys()) if start.get(path) != end.get(path)]
        assert not changed, ("The memory-backed stash view or transfer changed a campaign save", changed)

    before = saves()
    assert all(before)
    require_no_page_file()
    initial = await submit(guest_run, native_stash.LIST, target=site)
    assert initial["ok"] and initial[native_stash.RESULT]["owner"] == joined["character_id"]
    assert initial[native_stash.RESULT]["items"] == []

    if large:
        seed = await host.bridge.command("portable-large-stash-seed", pawn, site, joined["character_id"], timeout=12)
        expected = re.fullmatch(r"portable large stash seeded ([0-9A-F,]+)", seed["ack"]["message"])
        assert expected, seed["ack"]
        guids = expected[1].split(",")
        assert len(guids) == len(set(guids)) == 384 and all(native_stash.valid_guid(guid) for guid in guids)
        require_unchanged_saves(before)
    else:
        listed = await submit(guest_run, "native-inventory-list")
        assert listed["ok"], listed
        item, = [row for row in listed["native_inventory"]["items"]
                 if row["source"] == "backpack" and row["class_path"] == native_guest_consumable.STARTER_BAGS["hyperrandom"]]

    opened_at = time.perf_counter()
    opened = await submit(guest_run, "native-stash-view-fixture", target=site)
    assert opened["ok"], opened
    pending = opened[native_stash.VIEW]
    assert (pending["state"], pending["pawn"], pending["site"], pending["mode"], pending["item"]) == (
        "pending", local_pawn, site, "stash", None)
    if large:
        rendered = await wait_for_stash_render(pending)
        open_seconds = round(time.perf_counter() - opened_at, 3)
        receipt = await guest.bridge.command("portable-stash-memory-proof", str(pending["sequence"]), site, "large")
        matched = re.fullmatch(rf"portable large stash memory {pending['sequence']} 384 (\d+) ([0-9A-F,]+)",
                               receipt["ack"]["message"])
        assert matched, receipt["ack"]
        payload_bytes = int(matched[1])
        visible_guids = matched[2].split(",")
        assert visible_guids == guids and 32 * 1024 < payload_bytes <= native_stash.LARGE_MEMORY_VIEW_MAX_BYTES
        census_snapshot = home["game"].get(native_stash.CENSUS_SNAPSHOT) is True
        census_reads = None
        if census_snapshot:
            metrics = await host.bridge.command("portable-large-stash-census-reads", pawn, site, joined["character_id"])
            attempted = re.fullmatch(r"portable live stash census reads (\d+)", metrics["ack"]["message"])
            assert attempted and int(attempted[1]) == 1, metrics["ack"]
            census_reads = int(attempted[1])
        require_unchanged_saves(before)
        await close()
        cleared = await host.bridge.command("portable-large-stash-clear", pawn, site, joined["character_id"])
        assert cleared["ack"]["message"] == "portable large stash cleared without save", cleared["ack"]
        site_discovery = await verify_site_scans() if site_scan_probe else None
        require_unchanged_saves(before)
        return {"stash_actor": authored["actor"], "site": site, "stock_view": rendered, "large_native_ticket": True,
                "memory_payload_bytes": payload_bytes, "stock_card_count": len(guids),
                "stash_open_seconds": open_seconds,
                "site_discovery": site_discovery,
                "census_snapshot": census_snapshot, "live_census_reads": census_reads,
                "first_guid": guids[0], "last_guid": guids[-1], "ordered_card_guids": guids,
                "local_bridge": {"host": home["local_bridge"], "guest": joined["local_bridge"]},
                "stash_page_files_seen": False, "saves_unchanged_before_checkpoint": True,
                "fixture_inventory_cleared": True}

    empty, empty_proof, empty_ammo = await readback(pending, 0)
    assert empty_ammo == 0
    moved = await submit(guest_run, "native-stash-view-click-fixture", target=f"drag-backpack:{item['guid']}")
    assert moved["ok"], moved
    pending = moved[native_stash.VIEW]
    assert (pending["state"], pending["pawn"], pending["site"], pending["mode"], pending["item"]) == (
        "pending", local_pawn, site, "backpack", item["guid"]) and pending["sequence"] > empty["sequence"]
    filled, filled_proof, ammo = await readback(pending, 1, item["guid"])
    page = await submit(guest_run, native_stash.LIST, target=site)
    assert page["ok"] and page[native_stash.RESULT]["owner"] == joined["character_id"]
    stored, = page[native_stash.RESULT]["items"]
    assert all(stored[key] == item[key] for key in ("guid", "class_path", "quantity")), stored
    assert stored["ammo"] == ammo, stored
    backpack = await submit(guest_run, "native-inventory-list")
    assert backpack["ok"] and all(row["guid"] != item["guid"] for row in backpack["native_inventory"]["items"])
    await close()
    require_unchanged_saves(before)
    return {"stash_actor": authored["actor"], "site": site, "stashed_guid": item["guid"],
            "stock_empty": empty, "stock_filled": filled, "memory_readbacks": [empty_proof, filled_proof],
            "local_bridge": {"host": home["local_bridge"], "guest": joined["local_bridge"]},
            "stash_page_files_seen": False, "saves_unchanged_before_checkpoint": True}


async def verify_stock_crate_ring(host: Seat, guest: Seat, pawn: str, *, memory_positions: bool = False, cold: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_state, guest_state = await asyncio.gather(
        wait(host_run, lambda state: state.get("local_bridge") == "memory"),
        wait(guest_run, lambda state: state.get("local_bridge") == "memory"),
    )
    area = host_state["game"]["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1")]
    approached = await host.bridge.command("native-loot-approach", pawn, crate, timeout=5)
    position, = [row["location"] for row in approached["pawns"].values() if row["name"] == pawn]
    if memory_positions:
        await wait(guest_run, lambda state: (point := state.get("game", {}).get("pawns", {}).get("local", {}).get("location"))
                   and math.dist(point, position) < 50)
    else:
        await guest.bridge.wait_for(lambda state: math.dist(state["pawns"]["local"]["location"], position) < 50)

    def save_fingerprints():
        return campaign_save_hashes(host.campaign), saved_hashes(host.profile / "Saved/SaveGames")

    before = save_fingerprints()
    assert all(before)
    request_at = time.monotonic()
    guest_actor = await portable_probe_command(guest.bridge, "portable-crate", "coldpress" if cold else "press")
    pressed_at = time.monotonic()
    # The guest's stock actor can have a different Unreal instance suffix.
    prefix = crate.rsplit("_", 1)[0] + "_"
    assert guest_actor.startswith(prefix) and guest_actor[len(prefix):].isdigit(), guest_actor
    opened = await wait(guest_run, lambda state: crate in state.get("gameplay", {}).get(native_loot.STATE_KEY, {}))
    visible_at = time.monotonic()
    roll = opened["gameplay"][native_loot.STATE_KEY][crate]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_loot_crates", {}).get(crate, {}).get("state")
               == "exhausted")
    lid_at = time.monotonic()
    async with asyncio.timeout(2):
        while (motion := await portable_probe_command(guest.bridge, "portable-crate", "motion")) == "pending":
            await asyncio.sleep(.05)
    motion_ms, collision_safe = motion.split(",")
    assert 0 < float(motion_ms) < 200 and collision_safe == "true", motion
    await asyncio.sleep(.65)  # Let the compatible status copies arrive after the ring receipt.

    async def events(run, state, name):
        return [row for row in await resident_events(run, state) if row["event"] == name and row.get("crate") == crate]

    received, = await events(guest_run, guest_state, "native_stock_crate_interaction")
    applied, = await events(host_run, host_state, "native_loot_roll_accepted")
    assert received["input_source"] == "memory_input" and received["operation_id"] == applied["id"] == roll["id"]
    assert save_fingerprints() == before
    return {
        "local_bridge": {"host": host_state["local_bridge"], "guest": guest_state["local_bridge"]},
        "stock_crate": crate, "guest_actor": guest_actor,
        "input_source": received["input_source"], "sequence": received["sequence"],
        "roll_id": roll["id"], "roll_count": 1, "guest_scope_cleared_before_press": cold,
        "saves_unchanged_after_open": True,
        "probe_request_to_press_reply_ms": round(1000 * (pressed_at - request_at), 2),
        "probe_request_to_visible_roll_ms": round(1000 * (visible_at - request_at), 2),
        "probe_request_to_guest_lid_ms": round(1000 * (lid_at - request_at), 2),
        "guest_press_to_preview_motion_ms": float(motion_ms), "guest_preview_collision_preserved": True,
    }


async def verify_compact_crate_status(host: Seat, guest: Seat, pawn: str) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    before = await wait(host_run, lambda state: state.get("local_status_source") == "memory")
    area = before["game"]["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.endswith(":PersistentLevel.LootCrate_Small_BP_C_1")]
    details_key = native_loot.CRATE_DIAGNOSTICS
    diagnostic_key = "native_loot_crate_diagnostic"

    def ordinary(frame, phase, state, *, scope=None, after=0):
        game = frame.get("game", {})
        return (frame.get("state") == phase and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and game.get("map") == area
                and game.get("sequence", 0) > after and
                game.get("native_loot_crates", {}).get(crate) == {"map": area, "crate": crate, "state": state}
                and (scope is None or game.get("pawns", {}).get("local", {}).get("name") == scope)
                and details_key not in game and diagnostic_key not in game)

    async def seat_check(seat, run, phase):
        initial = await wait(run, lambda frame: ordinary(frame, phase, "ready"))
        local_pawn = initial["game"]["pawns"]["local"]["name"]
        tsv = seat.bridge.directory / "status.tsv"
        # Explicit file commands still refresh their real fallback status TSV.
        file_frame = await seat.bridge.command(native_loot.CRATE_DIAGNOSTIC, area, local_pawn, crate, timeout=5)
        raw = tsv.read_bytes()
        raw_rows = [line for line in raw.splitlines() if line.startswith(b"native-loot-crate-diagnostic\t")]
        file_row = file_frame[details_key][crate]
        assert len(raw_rows) == len(file_frame[details_key]) > 0
        assert file_frame["pawns"]["local"]["name"] == local_pawn
        assert file_frame["native_loot_crates"][crate]["state"] == "ready"
        assert (file_row["map"], file_row["crate"], file_row["phase"], file_row["scope"], file_row["looted"]) == (
            area, crate, phase, "active", False), file_row

        prior = await wait(run, lambda frame: ordinary(frame, phase, "ready", scope=local_pawn, after=file_frame["sequence"]))
        result = await submit(run, native_loot.CRATE_DIAGNOSTIC, target=crate)
        assert result["ok"] and result[diagnostic_key] == file_row, result
        invalid_area = await submit(run, native_loot.CRATE_DIAGNOSTIC, target=native_loot.CRATE)
        assert not invalid_area["ok"] and "exact authored crate" in invalid_area["error"], invalid_area
        later = await wait(run, lambda frame: ordinary(frame, phase, "ready", scope=local_pawn,
                                                      after=prior["game"]["sequence"]))
        assert tsv.read_bytes() == raw, "A resident memory query unexpectedly refreshed its fallback TSV"
        assert (prior["generation"], prior["zone_epoch"]) == (later["generation"], later["zone_epoch"])
        return {"pawn": local_pawn, "phase": phase, "sequence_before": prior["game"]["sequence"],
                "sequence_after": later["game"]["sequence"], "ready_before_and_after": True,
                "generation": later["generation"], "zone_epoch": later["zone_epoch"],
                "host_session": later.get("host_session"),
                "fallback_tsv": {"sha256": sha256(raw).hexdigest(), "bytes": len(raw), "diagnostic_rows": len(raw_rows),
                                 "sequence": file_frame["sequence"], "unchanged_across_memory_query": True},
                "memory_detail_fields_absent_before_and_after": True, "result": result[diagnostic_key],
                "different_map_target_refused": True}

    inspections = await asyncio.gather(seat_check(host, host_run, "hosting"), seat_check(guest, guest_run, "connected"))
    assert inspections[0]["generation"] == inspections[1]["host_session"]
    assert inspections[0]["zone_epoch"] == inspections[1]["zone_epoch"]
    stock_open = await verify_stock_crate_ring(host, guest, pawn, memory_positions=True)
    assert stock_open["stock_crate"] == crate and stock_open["roll_count"] == 1
    exhausted = await asyncio.gather(
        wait(host_run, lambda frame: ordinary(frame, "hosting", "exhausted", scope=inspections[0]["pawn"])),
        wait(guest_run, lambda frame: ordinary(frame, "connected", "exhausted", scope=inspections[1]["pawn"])),
    )
    for inspection, observed in zip(inspections, exhausted, strict=True):
        inspection["exhausted_sequence"] = observed["game"]["sequence"]
    return {"stock_crate": crate, "host": inspections[0], "guest": inspections[1], "stock_open": stock_open}


async def verify_gameplay(host: Seat, guest: Seat, pawn: str, *, stock_drop: bool = False,
                          stock_split: bool = False, stock_equip: bool = False, stock_nearby: bool = False,
                          projected_nearby: bool = False) -> dict:
    def qualified(game):
        return (game.get("native_enemy_loot_cqc_version") == 3
                and game.get("native_guest_regen", {}).get(pawn, {}).get("state") in {"applied", "anchored", "paused_game_clock"}
                and "guest_sickness" in game.get("native_guest_sickness", {}).get(pawn, {}))

    game = await host.bridge.wait_for(qualified)
    await guest.bridge.wait_for(lambda frame: frame.get("native_inventory_view_binding") == "ready")
    guest_run = guest.client / "run"
    async with asyncio.timeout(12):
        while True:
            try:
                events = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                if any(json.loads(line).get("event") == "native_guest_ui_projected" for line in events):
                    break
            except (FileNotFoundError, PermissionError, json.JSONDecodeError):
                pass
            await asyncio.sleep(.15)
    preview = await submit(guest_run, "native-inventory-view-fixture", target="stock")
    assert preview["ok"] and re.fullmatch(r"verified stock guest inventory with \d+ backpack cards",
                                          preview.get("stock_inventory_proof", "")), preview
    stock_drop_proof, stock_sort_proof, stock_use_proofs = None, None, {}
    if stock_drop:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        sorted_pack = await submit(guest_run, native_guest_drop.STOCK_SORT_FIXTURE)
        assert sorted_pack["ok"], sorted_pack
        stock_sort_proof = sorted_pack[native_inventory.SORT_KEY]
        assert re.fullmatch(r"verified open stock backpack sort with \d+ cards",
                            sorted_pack.get("stock_inventory_proof", "")), sorted_pack
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"], contents
        original_rows, sorted_rows = (part["native_inventory"]["items"] for part in (before, contents))
        assert stock_sort_proof["count"] == sum(row["source"] == "backpack" for row in sorted_rows)
        original_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in original_rows}
        sorted_payload = {row["guid"]: {key: value for key, value in row.items() if key != "index"} for row in sorted_rows}
        assert sorted_payload == original_payload
        assert (original_rows != sorted_rows) == (stock_sort_proof["state"] == "sorted")
        host_after = await submit(host_run, "native-inventory-list")
        assert host_after["ok"] and host_before["native_inventory"] == host_after["native_inventory"], host_after
        selected = next((row for row in sorted_rows if row["class_path"] not in native_guest_consumable.COMMANDS
                         and native_guest_drop.droppable(row, generic=True)), None)
        assert selected is not None, contents
        dropped = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=selected["guid"])
        assert dropped["ok"], dropped
        assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                            dropped.get("stock_inventory_proof", "")), dropped
        stock_drop_proof = dropped[native_guest_drop.RESULT]
        assert (stock_drop_proof["guid"], stock_drop_proof["class_path"], stock_drop_proof["quantity"]) == (
            selected["guid"], selected["class_path"], selected["quantity"],
        ), dropped
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        for seat in (host, guest):
            await seat.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
        contents = await submit(guest_run, "native-inventory-list")
        assert contents["ok"] and selected["guid"] not in {
            row["guid"] for row in contents["native_inventory"]["items"]
        }, contents
        for class_path in (native_guest_consumable.BANDAGE_CLASS, native_guest_consumable.MRE_CLASS):
            selected = next((row for row in contents["native_inventory"]["items"]
                             if row["class_path"] == class_path and row["source"] == "backpack"), None)
            assert selected is not None, contents
            used = await submit(guest_run, native_guest_drop.STOCK_USE_FIXTURE, target=selected["guid"])
            assert used["ok"], used
            assert re.fullmatch(r"verified open stock backpack refresh with \d+ cards",
                                used.get("stock_inventory_proof", "")), used
            key = native_guest_consumable.RESULT[native_guest_consumable.COMMANDS[class_path]]
            proof = stock_use_proofs[key] = used[key]
            assert (proof["guid"], proof["state"], proof["quantity_before"], proof["quantity_after"]) == (
                selected["guid"], "used", selected["quantity"], selected["quantity"] - 1,
            ), used
            contents = await submit(guest_run, "native-inventory-list")
            assert contents["ok"], contents
            remaining = [row["quantity"] for row in contents["native_inventory"]["items"]
                         if row["guid"] == selected["guid"]]
            assert remaining == ([proof["quantity_after"]] if proof["quantity_after"] else []), contents
    split_proof, bandage_split = None, None
    if stock_split:
        host_run = host.campaign / "run"
        checkpoint = campaign_save_hashes(host.campaign)
        seeded = (await host.bridge.command("fixture-stock-split-bandage"))["ack"]["message"]
        before = await submit(guest_run, "native-inventory-list")
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        items = before["native_inventory"]["items"]
        ammo = next(row for row in items if row["class_path"].endswith("/Ammo_ShotgunShells_BP.Ammo_ShotgunShells_BP_C")
                    and native_inventory.splittable(row))
        bandage = next(row for row in items if row["guid"] == seeded)
        assert native_inventory.splittable(bandage) and bandage["quantity"] == 9

        async def stock_split_item(stack, take):
            inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
            guid, quantity = stack["guid"], stack["quantity"]
            stale = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity + 1}")
            assert not stale["ok"] and "stack changed" in stale["error"], stale
            assert (await submit(guest_run, "native-inventory-list"))["native_inventory"] == inventory
            split = await submit(guest_run, native_guest_drop.STOCK_SPLIT_FIXTURE, target=f"{guid}|{take}|{quantity}")
            assert split["ok"] and re.fullmatch(r"verified two stock stacks in an open backpack with \d+ cards",
                                               split.get("stock_inventory_proof", "")), split
            proof = split[native_inventory.SPLIT_KEY]
            after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
            by_guid = {row["guid"]: row for row in after["items"]}
            new_guid = proof["new_guid"]
            assert (by_guid[guid]["quantity"], by_guid[new_guid]["quantity"]) == (quantity - take, take), after
            assert by_guid[guid]["class_path"] == by_guid[new_guid]["class_path"] == stack["class_path"], after
            assert len(after["items"]) == len(inventory["items"]) + 1, after
            return proof

        split_proof = await stock_split_item(ammo, min(2, ammo["quantity"] - 1))
        bandage_split = await stock_split_item(bandage, 4)
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
        assert campaign_save_hashes(host.campaign) == checkpoint
    equip_proof = None
    if stock_equip:
        before = await submit(guest_run, "native-inventory-list")
        host_run = host.campaign / "run"
        host_before = await submit(host_run, "native-inventory-list")
        assert before["ok"] and host_before["ok"], (before, host_before)
        inventory = before["native_inventory"]
        shotgun = "/Game/Classes/Weapons/Wep_BoltAction_Shotgun.Wep_BoltAction_Shotgun_C"
        item = next((row for row in inventory["items"] if row["class_path"] == shotgun and row["source"] == "backpack"), None)
        assert item is not None, inventory
        equipped = await submit(guest_run, native_guest_drop.STOCK_EQUIP_FIXTURE, target=item["guid"])
        assert equipped["ok"], equipped
        equip_proof = equipped[native_inventory.STORE_KEY]
        assert equipped["stock_inventory_proof"] == f"verified stock equipment slot {equip_proof['slot']}"
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        row, = (row for row in after["items"] if row["guid"] == item["guid"])
        assert row == dict(item, source="equipment", index=equip_proof["slot"], droppable=False), (row, item)
        assert {row["guid"] for row in after["items"]} == {row["guid"] for row in inventory["items"]}
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    if stock_nearby:
        host_run = host.campaign / "run"
        target = f"loose:{DEFAULT_ACTOR}"
        host_pawn = game["pawns"]["local"]["name"]
        for participant in (host_pawn, pawn):
            approached = await host.bridge.command(native_loose_pickup.APPROACH, DEFAULT_ACTOR, participant, timeout=5)
            assert approached.get("ack", {}).get("message") == "shared level pickup approach verified", approached["ack"]
        def bolt_in_reach(state):
            game = state.get("game", {})
            item = game.get(native_loose_pickup.KEY, {}).get(DEFAULT_ACTOR, {})
            origin = game.get("pawns", {}).get("local", {}).get("location")
            return item.get("state") == "available" and origin and math.dist(item["location"], origin) <= 250
        near_host = await wait(host_run, bolt_in_reach)
        location = near_host["game"][native_loose_pickup.KEY][DEFAULT_ACTOR]["location"]
        near_guest = await guest.bridge.wait_for(
            lambda frame: frame.get("state") == "connected"
            and frame.get(native_loose_pickup.VIEW_SCOPE)
            and math.dist(frame["pawns"]["local"]["location"], location) <= 250)
        # The stock panel sees the actor even when the bridge's capped nearby list omits it.
        assert DEFAULT_ACTOR not in near_guest.get(native_loose_pickup.KEY, {}), near_guest
        host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    dropped_actor = None
    if projected_nearby:
        if stock_split:
            picked = await submit(guest_run, native_guest_drop.STOCK_DROP_FIXTURE, target=split_proof["source_guid"])
            assert picked["ok"], picked
            stock_drop_proof = picked[native_guest_drop.RESULT]
            assert stock_drop_proof["quantity"] == split_proof["remaining"] > 1, picked
        identity = native_world_loot.player_drop_identity(stock_drop_proof["drop_id"])
        await host.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {}))
        approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
        assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
        frame = await guest.bridge.wait_for(lambda state: identity in state.get(native_world_loot.KEY, {})
            and math.dist(state["pawns"]["local"]["location"], stock_drop_proof["location"]) < 225)
        dropped_actor = frame[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    nearby_proof = None
    if stock_nearby:
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=target)
        assert clicked["ok"] and clicked["proof"] == "stock Nearby Items card requested", clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: state.get("game", {}).get(native_loose_pickup.KEY, {})
                       .get(DEFAULT_ACTOR, {}).get("state") == "claimed")
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        nearby_proof, = [event[native_loose_pickup.PROOF] for event in events
                         if event.get(native_loose_pickup.PROOF, {}).get("actor") == DEFAULT_ACTOR]
        inventory_guid = nearby_proof.get("inventory_guid", nearby_proof["guid"])
        quantity = nearby_proof.get("stack_merge", {}).get("after", 1)
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{target}@{inventory_guid}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert inventory["owner"] == nearby_proof["owner"], nearby_proof
        matching = [row for row in inventory["items"] if row["guid"] == inventory_guid]
        assert [(row["class_path"], row["quantity"]) for row in matching] == [(nearby_proof["class_path"], quantity)], matching
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
        duplicate = await submit(host_run, native_loose_pickup.CLAIM, target=target)
        assert duplicate.get("error") == "loose_pickup_already_claimed", duplicate
    drop_reclaim_proof = None
    if projected_nearby:
        before_reclaim = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=dropped_actor)
        count = stock_drop_proof["quantity"]
        expected = "stock Nearby Items card requested" + (f" x{count}" if count > 1 else "")
        assert clicked["ok"] and clicked["proof"] == expected, clicked
        for run in (host_run, guest_run):
            await wait(run, lambda state: identity not in state.get("game", {}).get(native_world_loot.KEY, {})
                and stock_drop_proof["drop_id"] not in state.get("gameplay", {}).get("native_guest_stack_drops", {}))
        events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        drop_reclaim_proof, = [row for event in events if (row := event.get(native_guest_drop.RESULT))
                               and row.get("state") == "reclaimed" and row.get("drop_id") == stock_drop_proof["drop_id"]]
        assert (drop_reclaim_proof["guid"], drop_reclaim_proof["quantity"]) == (stock_drop_proof["guid"], count)
        inventory_guid = drop_reclaim_proof.get("inventory_guid", stock_drop_proof["guid"])
        merge = drop_reclaim_proof.get("stack_merge", {})
        if stock_split:
            existing, = [row for row in before_reclaim["items"] if row["guid"] == inventory_guid]
            assert existing["class_path"] == stock_drop_proof["class_path"], existing
            assert merge == {"amount": count, "before": existing["quantity"], "after": existing["quantity"] + count}, drop_reclaim_proof
        display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE,
                               target=f"{dropped_actor}@{inventory_guid}")
        assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
        guest_inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        assert drop_reclaim_proof["claimed_by"] == guest_inventory["owner"]
        returned = [row["quantity"] for row in guest_inventory["items"] if row["guid"] == inventory_guid]
        assert returned == [merge.get("after", count)], guest_inventory
        if merge:
            assert all(row["guid"] != stock_drop_proof["guid"] for row in guest_inventory["items"]), guest_inventory
        assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before["native_inventory"]
    normal_tab_proof = None
    if stock_drop:
        before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        selected = next((row for row in before["items"] if row["source"] == "backpack"
                         and row["class_path"] == native_guest_consumable.BANDAGE_CLASS), None)
        assert selected is not None, before
        clicked = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=selected["guid"])
        assert clicked["ok"] and clicked["stock_action"]["guid"] == selected["guid"], clicked
        await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock"
                   and "native_inventory_stock_action" not in state.get("game", {}))
        after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
        remaining = [row["quantity"] for row in after["items"] if row["guid"] == selected["guid"]]
        assert remaining == ([selected["quantity"] - 1] if selected["quantity"] > 1 else []), after
        normal_tab_proof = {"guid": selected["guid"], "remaining": selected["quantity"] - 1}
    requested = await submit(guest_run, "native-inventory-view-fixture")
    assert requested["ok"], requested
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"placed_enemy_loot_bridge": game["native_enemy_loot_cqc_version"],
            "guest_regeneration_and_sickness_bound": True, "guest_shop_heartbeat": True,
            "guest_inventory_action_verified": True, "guest_inventory_projection_verified": True,
            **({"guest_stock_nearby_pickup": nearby_proof} if nearby_proof else {}),
            **({"guest_stock_nearby_drop_reclaim": drop_reclaim_proof} if drop_reclaim_proof else {}),
            **({"normal_tab_use": normal_tab_proof} if normal_tab_proof else {}),
            **({"guest_stock_menu_split": split_proof, "guest_stock_bandage_split": bandage_split} if split_proof else {}),
            **({"guest_stock_menu_equip": equip_proof} if equip_proof else {}),
            **({"guest_stock_menu_drop": stock_drop_proof, "guest_stock_menu_use": stock_use_proofs,
                "guest_stock_menu_sort": stock_sort_proof}
               if stock_drop_proof else {})}


async def verify_container_pickup(host: Seat, guest: Seat, pawn: str, *, memory_ui: bool = False) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    game = await host.bridge.wait_for(lambda frame: frame.get("state") == "hosting")
    area = game["map"]
    crate, = [name for name in GUARANTEED_CRATES if native_loot.CRATES[name] == area
              and name.startswith("LootCrate_Small_BP_C ")]
    host_pawn = game["pawns"]["local"]["name"]
    await host.bridge.command("native-loot-approach", host_pawn, crate, timeout=5)
    opened = await submit(host_run, "native-loot-open", target=crate)
    assert opened["ok"], opened
    roll = opened["native_loot_roll"]
    item = next((row for row in roll["items"] if row["class_path"] not in native_loot.CASH_RANGES), None)
    assert item is not None and "ammo" in item, roll
    identity = uuid.uuid5(uuid.NAMESPACE_OID, f"{roll['id']}:{item['guid']}").hex
    await host.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {}))
    approached = await host.bridge.command(native_world_loot.APPROACH, identity, pawn, timeout=5)
    assert approached["ack"]["message"] == "stock loot height and line of sight verified", approached["ack"]
    seen = await guest.bridge.wait_for(lambda frame: identity in frame.get(native_world_loot.KEY, {})
        and math.dist(frame["pawns"]["local"]["location"], item["transform"]["position"]) < 225)
    actor = seen[native_world_loot.KEY][identity]["actor"].split(" ", 1)[1]
    host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]

    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    def save_state():
        return (campaign_save_hashes(host.campaign), saved_hashes(host.profile / "Saved/SaveGames"),
                saved_hashes(guest.profile / "Saved/SaveGames"))

    def projections():
        rows = (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        return [row for line in rows if (row := json.loads(line))["event"] == "native_guest_ui_projected"]

    if memory_ui:
        for run in (host_run, guest_run):
            await wait(run, lambda state: state.get("local_bridge") == "memory"
                       and state.get("game", {}).get(runtime.GUEST_UI_MEMORY) is True)
        before = save_state()
        projections_before = len(projections())
        assert before[0] and projections_before

    clicked = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=actor)
    assert clicked["ok"] and clicked["proof"].startswith("stock Nearby Items card requested"), clicked

    def settled(state):
        rows = state.get("gameplay", {}).get(native_loot.STATE_KEY, {}).get(crate, {}).get("items", ())
        return identity not in state.get("game", {}).get(native_world_loot.KEY, {}) and any(
            row["guid"] == item["guid"] and row.get("claimed_by") for row in rows)

    await wait(host_run, settled)
    await wait(guest_run, settled)
    events = (json.loads(line) for line in (host_run / "events.jsonl").read_text(encoding="utf-8").splitlines())
    claim, = [event for event in events if event["event"] == "native_loot_claim_accepted" and event.get("guid") == item["guid"]]
    inventory_guid = claim.get("inventory_guid", item["guid"])
    display = await submit(guest_run, native_loose_pickup.STOCK_CARD_FIXTURE, target=f"{actor}@{inventory_guid}")
    assert display["ok"] and display["proof"] == "verified stock Nearby Items transfer", display
    inventory = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    received, = [row for row in inventory["items"] if row["guid"] == inventory_guid]
    assert received["class_path"] == item["class_path"], received
    if received["stackable"]:
        assert received["quantity"] == claim.get("stack_merge", {}).get("after", item["ammo"]), received
        if item["ammo"] > 1:
            assert clicked["proof"] == f"stock Nearby Items card requested x{item['ammo']}", clicked
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
    duplicate = await submit(host_run, "native-loot-claim", target=f"{item['guid']}@{crate}")
    assert duplicate.get("error") == "native_loot_already_claimed", duplicate
    memory_proof = {}
    if memory_ui:
        refreshed = projections()[projections_before:]
        assert refreshed and refreshed[-1]["items"] >= 1, refreshed
        assert save_state() == before
        memory_proof = {"resident_ui_projection": refreshed[-1], "saves_unchanged_before_checkpoint": True}
    checkpoint = await submit(host_run, "checkpoint")
    assert checkpoint["ok"], checkpoint
    saved = json.loads((host.campaign / "save" / "campaign.json").read_text(encoding="utf-8"))
    native_host_save.verify_checkpoint(host.profile, saved)
    durable = saved["world"]["party"][native_loot.STATE_KEY][crate]
    committed, = [row for row in durable["items"] if row["guid"] == item["guid"]]
    assert committed == {**item, "claimed_by": inventory["owner"]}, committed
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    return {"crate": crate, "roll": roll["id"], "item": committed, "rendered": clicked["proof"], "received": received,
            "duplicate": duplicate["error"], **memory_proof}


async def verify_stock_use(host: Seat, guest: Seat, pawn: str, *, starter: bool) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    campaign_file = host.campaign / "save" / "campaign.json"

    def save_hashes():
        paths = [campaign_file, *host.profile.rglob("*.sav")]
        return {path: sha256(path.read_bytes()).hexdigest() for path in paths}

    before_saves = save_hashes()
    before = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    host_before = (await submit(host_run, "native-inventory-list"))["native_inventory"]
    classes = ({native_guest_consumable.STARTER_BAGS["hyperrandom"]} if starter
               else {native_guest_consumable.BANDAGE_CLASS})
    candidates = [row for row in before["items"] if row["source"] == "backpack" and row["class_path"] in classes]
    assert candidates and (not starter or len(candidates) == 1), before
    item = min(candidates, key=lambda row: row["guid"])
    previous_use = None
    if not starter:
        observer = await wait(host_run, lambda state: pawn in state.get("game", {}).get("native_player_avatars", {}))
        previous_use = observer["game"].get("native_avatar_use", {}).get(pawn, {}).get("serial")
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock")
    gesture = "drag" if starter else "context"
    target = item["guid"] + ("|drag" if starter else "")
    selected = await submit(guest_run, "native-inventory-stock-use-click-fixture", target=target)
    assert selected["ok"] and selected["stock_action"]["guid"] == item["guid"], selected
    action = native_guest_consumable.COMMANDS[item["class_path"]]
    key = native_guest_consumable.RESULT[action]
    async with asyncio.timeout(15):
        while True:
            events = [json.loads(line) for line in (guest_run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            refused = next((entry for entry in events if entry["event"] == "native_stock_inventory_rejected"), None)
            if refused:
                raise RuntimeError(refused["reason"])
            if succeeded := [entry for entry in events if entry["event"] == "native_stock_inventory_action"
                             and entry.get(key, {}).get("guid") == item["guid"]]:
                event, = succeeded
                break
            await asyncio.sleep(.1)
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "stock"
               and "native_inventory_stock_action" not in state.get("game", {}))
    receipt = event[key]
    assert (receipt["pawn"], receipt["quantity_before"], receipt["quantity_after"]) == (
        pawn, item["quantity"], item["quantity"] - 1), receipt
    after = (await submit(guest_run, "native-inventory-list"))["native_inventory"]
    contents = {row["guid"]: row for row in after["items"]}
    added = set(contents) - {row["guid"] for row in before["items"]}
    if starter:
        assert added and added == set(receipt["awarded_guids"]), (after, receipt)
        assert all(contents[guid]["source"] == "backpack" for guid in added), after
    else:
        assert not added, after
    assert contents.get(item["guid"], {}).get("quantity", 0) == receipt["quantity_after"], after
    remaining = {row["guid"]: {key: value for key, value in row.items() if key != "index"}
                 for row in after["items"] if row["guid"] not in added and row["guid"] != item["guid"]}
    assert remaining == {row["guid"]: {key: value for key, value in row.items() if key != "index"}
                         for row in before["items"] if row["guid"] != item["guid"]}, after
    assert (await submit(host_run, "native-inventory-list"))["native_inventory"] == host_before
    replay = await submit(guest_run, action, target=item["guid"], operation_id=event["operation_id"])
    assert replay["ok"] and replay[key] == receipt, replay
    assert (await submit(guest_run, "native-inventory-list"))["native_inventory"] == after
    assert save_hashes() == before_saves, "Using a stock item wrote a campaign save"
    saved = await submit(host_run, "checkpoint")
    assert saved["ok"], saved
    native_host_save.verify_checkpoint(host.profile, json.loads(campaign_file.read_text(encoding="utf-8")))
    assert (await submit(guest_run, "native-inventory-view-fixture"))["ok"]
    await wait(guest_run, lambda state: state.get("game", {}).get("native_inventory_view", {}).get("state") == "closed")
    evidence = {"gesture": gesture, "receipt": receipt}
    if not starter:
        def observed_use(state):
            use = state.get("game", {}).get("native_avatar_use", {}).get(pawn, {})
            return (use.get("serial") != previous_use and use.get("item") == item["class_path"]
                    and not use["active"] and use["hand_motion_cm"] > 20)
        host_state = await wait(host_run, observed_use, timeout=8)
        presentation = host_state["game"]["native_avatar_use"][pawn]
        assert presentation["mesh"].endswith(".SM_Prop_Bandage_02"), presentation
        evidence["host_observed"] = presentation
    return evidence


async def create_stock_drop_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    await creator_action(guest, "guestkit")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def create_stock_bag_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    await creator_action(guest, "guesthyperkit")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def create_stock_split_character(guest, join, name):
    await join("create-editor", name)
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    assert await creator_action(guest, "preset") == "page=4"
    assert (await creator_action(guest, "veteran")).startswith("class=Veteran;")
    assert await creator_action(guest, "next") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)


async def verify_cloud_restore(first, second, session, game, processes):
    campaign_id = uuid.UUID(session["campaign_id"]).hex
    source = first.profile / "Saved/SaveGames" / FOLDER / f"SFCoopBackup-{campaign_id}.sav"
    assert source.is_file(), "The installed title did not back up the committed host campaign"
    name = f"campaign-{campaign_id}"
    directory = second.campaign.parent / name
    destination = second.profile / "Saved/SaveGames" / FOLDER / source.name
    destination.parent.mkdir(exist_ok=True)
    assert not directory.exists() and not destination.exists()
    solo = stock_save_hashes(second.profile)
    solo.pop("Config.sav", None)
    preferences = await stock_profile(second.bridge)
    form = "native-title-host-form-fixture"

    async def simulate_steam_download():
        await runtime.stop_game(second.bridge.process)
        # Auto-Cloud downloads the file before launching the game on the other PC.
        shutil.copy2(source, destination)
        root = second.bridge.directory.parent
        previous = set(root.iterdir())
        process = launch_portable(game, second.profile)
        processes.append((game, process))
        async with asyncio.timeout(30):
            while process.poll() is None:
                fresh = [path for path in root.iterdir() if path not in previous and path.is_dir()]
                if fresh:
                    mailbox, = fresh
                    second.bridge = runtime.Bridge(mailbox, process)
                    await offered(second)
                    return
                await asyncio.sleep(.1)
        raise RuntimeError("The second replica exited before its next Steam-style launch")

    async def pick_restored():
        await second.bridge.command(form, "open")
        async with asyncio.timeout(12):
            while not (second.bridge.directory / "title-host-campaigns.tsv").is_file():
                await asyncio.sleep(.1)
        await second.bridge.command(form, "browse")
        for _ in range(3):
            observed = (await second.bridge.command(form, "saved-state"))["ack"]["message"]
            if observed.endswith(":" + name):
                return
            await second.bridge.command(form, "next")
        raise AssertionError("The downloaded backup is missing from Host Load Game")

    def checkpoint():
        return menu_host_checkpoint(directory, profile=second.profile, require_scoped=True)

    await simulate_steam_download()
    await pick_restored()
    restored = checkpoint()
    assert restored["campaign_id"] == session["campaign_id"] and restored["revision"] == session["revision"]
    character = restored["characters"][session["character_id"]]
    assert character["state"]["native_guest_save"]
    await second.bridge.command(form, "choose-saved")
    await second.bridge.command(form, "port", str(second.port))
    await second.bridge.command(form, "submit")
    hosted = await wait(directory / "run", lambda frame: frame.get("state") == "hosting")
    assert hosted["campaign"]["campaign_id"] == restored["campaign_id"]
    assert hosted["game"]["save_scope"] == campaign_id
    identity = direct_ip.read_resident_identity(first.campaign.parent.parent, first.campaign)
    work = second.campaign.parent.parent
    with runtime.installed_content(work, game):
        build = runtime.staged_build(work)
    pin = sha256(identity["certificate"].encode("utf-8")).hexdigest()
    guest_connection = await direct_ip.connect(second.client, "127.0.0.1", second.port, "", build, PROTOCOL,
                                               verified_fingerprint=pin)
    assert guest_connection.details["guest_id"] == character["guest_id"]
    await second.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
    await stock_pause(second.bridge, "open")
    await stock_pause(second.bridge, "save_and_quit")
    await wait(directory / "run", lambda frame: frame.get("state") == "stopped")
    await offered(second)
    newer = checkpoint()
    assert newer["revision"] > restored["revision"]
    assert destination.read_bytes() != source.read_bytes(), "The resumed host's backup was not refreshed"

    await simulate_steam_download()
    await pick_restored()
    assert checkpoint() == newer, "The old cloud backup replaced a newer local campaign"
    await second.bridge.command(form, "cancel")
    await second.bridge.command(form, "cancel")
    modes = await second.bridge.command("native-title-button-fixture", "modes")
    await second.bridge.wait_for(lambda frame: frame["sequence"] > modes["sequence"])
    observed_solo = stock_save_hashes(second.profile)
    observed_solo.pop("Config.sav", None)
    assert observed_solo == solo and await stock_profile(second.bridge) == preferences
    return {"restored_revision": restored["revision"], "retained_revision": newer["revision"],
            "guest_character_restored": session["character_id"], "host_identity_restored": True,
            "singleplayer_unchanged": True,
            "steam_download_simulated": True}


async def verify(work: Path, first_game: Path, second_game: Path, original: Path,
                 second_legacy_work: Path | None = None, *, stock_drop: bool = False, stock_split: bool = False,
                 stock_equip: bool = False, stock_nearby: bool = False, projected_nearby: bool = False,
                 container_nearby: bool = False, steam_cloud: bool = False, memory_bridge: bool = False,
                 stock_use: bool = False, stock_food: bool = False, stock_campfire: bool = False, stock_cooked_meal: bool = False,
                 stock_personal_consumable: bool = False,
                 stock_wipe_chooser: bool = False,
                 stock_npc_override: bool = False, stock_board_offers: bool = False, stock_quarry_photo: bool = False,
                 stock_host_corpse_carry: bool = False, stock_flashlights: bool = False, stock_simmi_jammers: bool = False,
                 stock_weather_travel: bool = False, stock_lightning: bool = False, stock_storm_ambience: bool = False,
                 crossroads_profile: bool = False, stock_crossroads_door: bool = False, stock_dock_gate: bool = False,
                 stock_melee_family: bool = False, stock_melee_edges: bool = False,
                 stock_warehouse_switch: bool = False, stock_warehouse_board: bool = False, stock_bottle: bool = False,
                 stock_roaming_barter: bool = False, stock_critters: bool = False, stock_placed_item: bool = False,
                 stock_mushrooms: bool = False, stock_eye_stacks: bool = False, stock_lingering_teleport: bool = False, stock_acid_pool: bool = False,
                 stock_npc_rivalry: bool = False, stock_throwables: str | None = None,
                 memory_status: bool = False, stock_prop_grip: bool = False,
                 stock_unequip: bool = False, stock_host_prop_observer: bool = False, resident_weapon_cues: bool = False,
                 resident_held_queue: bool = False, resident_stale_quick_slot: bool = False,
                 rusty_pistol: bool = False, tutorial_dock_combat: bool = False, tutorial_dock_retaliation: bool = False,
                 tutorial_dock_melee: bool = False, tutorial_dock_pursuit: bool = False,
                 tutorial_dock_held_mask: bool = False, tutorial_dock_gait: bool = False,
                 tutorial_dock_shot_view: bool = False,
                 stock_bandage_revisit: bool = False, stock_shovel: bool = False, stock_shovel_loot: bool = False,
                 stock_shovel_quest: bool = False, stock_shovel_host_quest: bool = False,
                 stock_buried_placements: bool = False,
                 stock_take_ammo: bool = False, stock_shotguns: bool = False, stock_alternate_ammo: bool = False,
                 stock_impact_cues: bool = False,
                 stock_avatar_tail: bool = False, stock_avatar_binoculars: bool = False,
                 stock_interaction_feedback: bool = False,
                 stock_loki_control: bool = False, stock_loki_guest: bool = False,
                 stock_light_ar: bool = False, stock_fire_mode_reopen: bool = False,
                 stock_crate_ring: bool = False, guest_world_start: bool = False,
                 guest_world_start_travel: bool = False, guest_ui_memory: bool = False, nonmirrored_startup: bool = False,
                 compact_crate_status: bool = False, compact_world_view: bool = False,
                 guest_shop_lease: bool = False, guest_stash_memory: bool = False, guest_stash_large_memory: bool = False,
                 stock_pause_exit: bool = False,
                 zone_lease_preflight: bool = False,
                 resident_cabinet_archive: Path | None = None,
                 resident_cabinet_checkpoint: Path | None = None,
                 resident_cabinet_candidate_sha256: str | None = None) -> None:
    guest_world_start = guest_world_start or guest_world_start_travel
    stock_weather_travel = stock_weather_travel or crossroads_profile
    guest_stash_memory = guest_stash_memory or guest_stash_large_memory
    container_nearby = container_nearby or guest_ui_memory
    roots = (work, first_game, second_game, original)
    if second_legacy_work is not None:
        roots += (second_legacy_work,)
    if (os.name != "nt" or any("steamapps" in {part.casefold() for part in path.parts} for path in roots if path != original)
            or any(a.is_relative_to(b) or b.is_relative_to(a) for a, b in combinations(roots, 2))):
        raise ValueError("Use separate private replica and work directories outside the original saves")
    before = saved_hashes(original)
    if ((resident_cabinet_archive is None) != (resident_cabinet_checkpoint is None)
            or resident_cabinet_candidate_sha256 and not resident_cabinet_archive):
        raise ValueError("The cabinet profile needs the exact release archive and cold checkpoint")
    combined_presentation = resident_cabinet_candidate_sha256 is not None
    source_before = campaign_save_hashes(resident_cabinet_checkpoint) if resident_cabinet_checkpoint else None
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if not before or len(stock_slots) < 2 or not any(path.name == "Config.sav" for path in stock_slots):
        raise ValueError("Supply the original Saved directory with a stock configuration and at least one campaign slot")
    qualified = (qualify_release(first_game, None), qualify_release(second_game, second_legacy_work))
    if qualified[0].manifest_sha256 != qualified[1].manifest_sha256:
        raise ValueError("Both replicas must use the same portable release")
    if qualified[0].activation[first_game / runtime.SHIPPING.parent / "ue4ss/Mods/mods.txt"] is not None:
        raise ValueError("The first replica must have no mods.txt")
    if os.path.lexists(first_game / runtime.STAGE_MANIFEST):
        raise ValueError("The clean replica must start from the contents of the SilverFish folder alone")

    case = work / ("portable-title-" + uuid.uuid4().hex[:8])
    case.mkdir()
    print(case, flush=True)
    workspaces = case / "first", second_legacy_work or case / "second"
    games = first_game, second_game
    proof = {"verified": False, "native_bridges": [], "sessions": []}
    processes, seats, baselines = [], [], []
    probes = ExitStack()
    seeded = None

    # Install the requested probes once. Campaigns with incompatible map, quest,
    # or loadout requirements start fresh from the same two stock title processes.
    resident_campaigns = {}
    warehouse_prejoin = {}

    async def prepare_warehouse(host, _guest, _character):
        warehouse_prejoin.update(await prepare_warehouse_late_join(host))

    async def check_dock_gate(host, guest, pawn):
        await travel_pair_to_dock(host, guest, pawn)
        return await verify_stock_dock_gate(host, guest)

    storm_trial = StockStormTrial(portable_probe_command) if stock_storm_ambience else None
    weapon_campaign = "tutorial_dock_combat" if tutorial_dock_combat else "observatory"
    shot_view_campaign = "tutorial_dock_shot_view" if tutorial_dock_pursuit else weapon_campaign
    dock_world_checks = stock_bandage_revisit or stock_warehouse_switch or stock_warehouse_board or stock_bottle or stock_dock_gate
    prop_campaign = "tutorial_dock" if dock_world_checks else "observatory"
    stock_radio_media = stock_warehouse_switch and all(
        next((game / "SilverFish/Content/Music/Radio").glob("*.wav"), None) for game in games)
    if resident_held_queue:
        from portable_held_queue import verify_stock_held_queue
    else:
        verify_stock_held_queue = None
    dock_combat_check = ("stock_held_mask" if tutorial_dock_held_mask else
                         "tutorial_dock_pursuit" if tutorial_dock_pursuit else
                         "tutorial_dock_retaliation" if tutorial_dock_retaliation else
                         "stock_grounded_gait" if tutorial_dock_gait else "tutorial_dock_combat")
    for enabled, campaign, name, check in (
        (stock_campfire, "observatory", "stock_campfire", verify_resident_campfire),
        (stock_npc_override and not resident_weapon_cues, "observatory", "stock_npc_override", verify_resident_npc_override),
        (stock_board_offers, "observatory", "stock_board_offers", verify_resident_board_offers),
        (stock_quarry_photo, "quarry_photo", "stock_quarry_photo",
         lambda host, guest, pawn: verify_quarry_photo(host, guest, pawn, portable_probe_command)),
        (stock_host_corpse_carry, "observatory", "stock_host_corpse_carry", verify_stock_host_corpse_carry),
        (stock_host_prop_observer, prop_campaign, "stock_host_prop_observer",
         lambda host, guest, pawn: verify_stock_host_prop_observer(host, guest, pawn, proof.setdefault("host_prop_observer", {}))),
        (stock_flashlights, "observatory", "stock_flashlights", verify_stock_flashlights),
        (stock_melee_edges, "observatory", "stock_melee_edges", verify_stock_melee_edges_only),
        (stock_avatar_tail, "observatory", "stock_avatar_tail", verify_stock_avatar_tail),
        (stock_avatar_binoculars, "observatory", "stock_avatar_binoculars", verify_stock_avatar_binoculars),
        (stock_interaction_feedback, "observatory", "stock_lockpicks", verify_stock_lockpicks),
        (stock_interaction_feedback, "observatory", "stock_observatory_tv", verify_observatory_tv),
        (stock_melee_family, "observatory", "stock_melee_family", verify_stock_melee_family),
        (stock_throwables, "observatory", "stock_throwables",
         lambda host, guest, pawn: verify_stock_throwables(host, guest, pawn, stock_throwables)),
        (stock_npc_rivalry, weapon_campaign, "stock_npc_rivalry", verify_stock_npc_rivalry),
        (resident_weapon_cues, weapon_campaign, "resident_weapon_cues",
         lambda host, guest, pawn: verify_resident_weapon_cues(host, guest, pawn, guest_combat=stock_npc_override,
                                                               rusty=rusty_pistol, npc_rivalry=stock_npc_rivalry,
                                                               memory_queue=resident_held_queue, pursuit=tutorial_dock_pursuit)),
        (stock_light_ar, "observatory", "stock_light_ar",
         lambda host, guest, pawn: verify_stock_light_ar(host, guest, pawn, portable_probe_command,
                                                        re_equip=resident_weapon_cues and rusty_pistol
                                                        and weapon_campaign == "observatory")),
        (resident_held_queue, weapon_campaign, "stock_held_queue",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command,
                                                           stale_quick_slot=resident_stale_quick_slot)),
        (resident_held_queue, weapon_campaign, "stock_bizon_hold",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command, kind="bizon-01")),
        (resident_held_queue, weapon_campaign, "stock_burst_hold",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command, kind="smg-03")),
        (resident_held_queue, weapon_campaign, "stock_famas_hold",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command, kind="famas")),
        (resident_held_queue, weapon_campaign, "stock_lmg_hold",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command, kind="lmg-01")),
        (resident_held_queue, weapon_campaign, "stock_auto_shotgun_hold",
         lambda host, guest, pawn: verify_stock_held_queue(host, guest, pawn, portable_probe_command, kind="full-auto-shotgun")),
        (stock_food, "observatory", "stock_food", verify_resident_guest_food),
        (stock_cooked_meal, "observatory", "stock_cooked_meal", verify_stock_cooked_meal),
        (stock_personal_consumable, "personal_consumable", "stock_personal_consumable",
         lambda host, guest, pawn: verify_stock_personal_consumable(host, guest, pawn, portable_probe_command)),
        (stock_wipe_chooser, "wipe_chooser_guest_first", "stock_wipe_chooser_guest_first",
         lambda host, guest, pawn: verify_stock_wipe_chooser(host, guest, pawn, portable_probe_command, first="guest")),
        (stock_wipe_chooser, "wipe_chooser_host_first", "stock_wipe_chooser_host_first",
         lambda host, guest, pawn: verify_stock_wipe_chooser(host, guest, pawn, portable_probe_command, first="host")),
        (stock_take_ammo, "observatory", "stock_take_ammo", verify_stock_take_ammo),
        (stock_shotguns, "observatory", "stock_shotguns",
         lambda host, guest, pawn: verify_stock_take_ammo(host, guest, pawn, shotguns=True)),
        (stock_impact_cues, "observatory", "stock_impact_cues",
         lambda host, guest, pawn: verify_stock_take_ammo(host, guest, pawn, impact_cues=True)),
        (stock_loki_control, "observatory", "stock_loki_control", verify_stock_loki_control),
        (stock_loki_guest, "observatory", "stock_loki_guest", verify_guest_loki),
        (stock_alternate_ammo, "observatory", "stock_alternate_ammo",
         lambda host, guest, pawn: verify_alternate_ammo(host, guest, pawn, portable_probe_command)),
        (stock_roaming_barter, "observatory", "stock_roaming_barter", verify_roaming_barter),
        (stock_crate_ring and crossroads_profile, "crossroads", "stock_crate_ring",
         lambda host, guest, pawn: verify_stock_crate_ring(host, guest, pawn, memory_positions=True, cold=True)),
        (stock_weather_travel, "crossroads", "stock_weather_travel",
         lambda host, guest, pawn: verify_weather_travel(host, guest, pawn, portable_probe_command,
                                                        profile=crossroads_profile)),
        (stock_lightning, "crossroads", "stock_lightning",
         lambda host, guest, pawn: verify_stock_lightning(host, guest, pawn, portable_probe_command)),
        (storm_trial, "storm", "stock_storm_ambience",
         storm_trial.joined if storm_trial else None),
        (stock_crossroads_door, "crossroads", "stock_crossroads_door", verify_stock_crossroads_door),
        (stock_critters, "crossroads", "stock_critters", verify_stock_critters),
        (stock_shovel, "crossroads", "stock_shovel", lambda host, guest, pawn: verify_stock_shovel(
            host, guest, pawn, loot=stock_shovel_loot, quest=stock_shovel_quest, host_quest=stock_shovel_host_quest)),
        (stock_lingering_teleport, "crossroads" if crossroads_profile else "crossroads_anomaly", "stock_lingering_teleport",
         lambda host, guest, pawn: verify_stock_lingering_teleport(host, guest, pawn, portable_probe_command)),
        (stock_placed_item, "crossroads", "stock_placed_item", verify_stock_placed_item),
        (stock_placed_item, "crossroads", "stock_cash_popup",
         lambda host, guest, pawn: verify_stock_cash_popup(host, guest, pawn, portable_probe_command, campaign_save_hashes)),
        (stock_mushrooms, "crossroads", "stock_mushrooms", verify_stock_mushrooms),
        (stock_buried_placements, "buried_placements" if stock_shovel_quest else "crossroads",
         "stock_buried_placements", verify_stock_buried_placements),
        (stock_simmi_jammers, "simmi", "stock_simmi_jammers",
         lambda host, guest, pawn: verify_stock_simmi_jammers(host, guest, pawn, portable_probe_command)),
        (stock_acid_pool, "lab_east_wing_acid", "stock_acid_pool",
         lambda host, guest, _pawn: verify_acid_pool(host, guest, portable_probe_command)),
        (stock_dock_gate, "tutorial_dock", "stock_dock_gate", check_dock_gate),
        (stock_warehouse_switch, "tutorial_dock", "stock_warehouse_switch", verify_warehouse_switch),
        (stock_warehouse_switch, "tutorial_dock", "stock_warehouse_radio",
         lambda host, guest, pawn: verify_warehouse_switch(host, guest, pawn, radio=True, check_track_selection=stock_radio_media)),
        (stock_warehouse_switch, "tutorial_dock", "stock_warehouse_candle", verify_warehouse_candle),
        (stock_warehouse_switch, "tutorial_dock_late_join", "stock_warehouse_late_join",
         lambda host, guest, _pawn: verify_warehouse_late_join(host, guest, warehouse_prejoin)),
        (stock_bandage_revisit, "tutorial_dock", "stock_bandage_revisit", verify_tutorial_bandage_revisit),
        (stock_warehouse_board, "tutorial_dock", "stock_warehouse_board", verify_warehouse_board),
        (stock_bottle, "tutorial_dock", "stock_bottle", verify_stock_bottle),
        (stock_bottle, "tutorial_dock", "stock_prop_breaks", verify_stock_prop_breaks),
        (stock_bottle, "tutorial_dock", "stock_acid_bottle", verify_stock_acid_bottle),
        (stock_bottle, "tutorial_dock", "stock_wood_debris", verify_stock_wood_debris),
        (tutorial_dock_combat and (not tutorial_dock_shot_view or tutorial_dock_pursuit),
         "tutorial_dock_combat", dock_combat_check,
         lambda host, guest, pawn: verify_tutorial_dock_combat(host, guest, pawn, retaliation=tutorial_dock_retaliation,
                                                               pursuit=tutorial_dock_pursuit,
                                                               held_mask=tutorial_dock_held_mask,
                                                               grounded_gait=tutorial_dock_gait)),
        (tutorial_dock_shot_view and tutorial_dock_pursuit, shot_view_campaign, "shot_view_weapon_setup",
         lambda host, guest, pawn: verify_resident_weapon_cues(host, guest, pawn, rusty=True, npc_rivalry=False)),
        (tutorial_dock_shot_view, shot_view_campaign, "tutorial_dock_shot_view",
         lambda host, guest, pawn: verify_tutorial_dock_combat(host, guest, pawn, shot_view=True)),
        (tutorial_dock_shot_view, "tutorial_dock_buckshot", "tutorial_dock_buckshot", verify_dock_garage_buckshot),
        (tutorial_dock_melee, "tutorial_dock_melee", "tutorial_dock_melee", verify_tutorial_dock_melee),
        (stock_eye_stacks, "shopkeep", "stock_eye_stacks", verify_stock_eye_stacks),
    ):
        if enabled:
            resident_campaigns.setdefault(campaign, []).append((name, check))
    single_scenario = (bool(resident_campaigns) or stock_prop_grip
                       or stock_crate_ring or guest_world_start
                       or guest_ui_memory or compact_crate_status or compact_world_view or guest_shop_lease
                       or guest_stash_memory or nonmirrored_startup or stock_unequip or stock_fire_mode_reopen
                       or resident_cabinet_archive is not None)
    try:
        if crossroads_profile:
            from portable_crossroads_profile import (instrument_inline_crate_scan, instrument_input_mappings,
                                                     instrument_loose_discovery, instrument_npc_class_discovery, instrument_profile)

            crate_probe = ((Path(__file__).with_name("fixtures") / "portable_crate_input_probe.lua").read_text(encoding="utf-8")
                           if stock_crate_ring else None)

            def crossroads_overlay(source):
                profiled = instrument_profile(source)
                # Native globals are registered on the co-op Lua state.
                return (profiled + "\nstatus_publication.cold_crate_probe = function()\n" + crate_probe
                        + "\nend\nstatus_publication.cold_crate_probe()\n") if crate_probe else profiled

            proof["crossroads_profile_overlays"] = [
                probes.enter_context(portable_main_overlay(game, crossroads_overlay)) for game in games
            ]
            proof["crossroads_input_overlays"] = [
                probes.enter_context(portable_script_overlay(game, "players.lua", instrument_input_mappings))
                for game in games
            ]
            proof["crossroads_npc_overlays"] = [
                probes.enter_context(portable_script_overlay(game, "native_npc_barter_identity.lua", instrument_npc_class_discovery))
                for game in games
            ]
            proof["crossroads_loot_overlays"] = [
                probes.enter_context(portable_script_overlay(game, name, instrument))
                for game in games
                for name, instrument in (
                    ("native_loot_crate_input.lua", instrument_inline_crate_scan),
                    ("native_authored_pickup.lua", instrument_loose_discovery),
                )
            ]
        if stock_weather_travel:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableWeatherTravelProbe",
                                                     fixture="portable_weather_travel_probe.lua"))
        if stock_lightning:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableLightningProbe",
                                                     fixture="portable_lightning_probe.lua"))
        if storm_trial:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableStormProbe",
                                                     fixture="portable_storm_probe.lua"))
        if stock_simmi_jammers or stock_board_offers:
            proof["jammer_aim_overlays"] = [probes.enter_context(jammer_aim_overlay(game)) for game in games]
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableSimmiJobsProbe",
                                                     fixture="simmi_jobs_probe.lua"))
        if stock_quarry_photo:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableQuarryPhotoProbe",
                                                     fixture="portable_quarry_photo_probe.lua"))
        if stock_flashlights:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableFlashlightProbe",
                                                     fixture="flashlight_source_probe.lua"))
        if resident_cabinet_archive:
            proof.update(cabinet_provenance(resident_cabinet_archive, games, qualified, resident_cabinet_candidate_sha256),
                         test_only_timing_probes=True)
            for game in games:
                probes.enter_context(instrument(game, release_archive=resident_cabinet_archive,
                                                combined=combined_presentation))
        if guest_world_start:
            probes.enter_context(installed_probe(second_game, mod="SilverfishPortableGuestWorldStartProbe",
                                                 fixture="portable_guest_world_start_probe.lua"))
        if guest_world_start_travel or stock_acid_pool:
            probes.enter_context(installed_probe(first_game, mod="SilverfishPortableRuinExitProbe",
                                                 fixture="portable_ruin_exit_probe.lua"))
        if stock_throwables:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableStockThrowablesProbe",
                                                     fixture="portable_stock_throwables_probe.lua"))
        if stock_wipe_chooser:
            proof["stock_wipe_chooser_overlays"] = [probes.enter_context(stock_wipe_chooser_overlay(game)) for game in games]
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableWipeChooserProbe",
                                                     fixture="portable_wipe_chooser_probe.lua"))
        if stock_food or stock_cooked_meal or stock_personal_consumable:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableGuestFoodProbe",
                                                     fixture="portable_guest_food_probe.lua"))
        if stock_nearby:
            proof["stock_nearby_overlays"] = [probes.enter_context(stock_nearby_overlay(game)) for game in games]
        if stock_bandage_revisit:
            proof["loose_revisit_overlays"] = [probes.enter_context(loose_revisit_overlay(game)) for game in games]
        if stock_shovel or stock_buried_placements:
            proof["stock_shovel_overlays"] = [probes.enter_context(stock_shovel_overlay(game)) for game in games]
        if stock_shovel_quest:
            for game in games:
                probes.enter_context(stock_board_approach_overlay(game))
        if stock_split:
            proof["stock_split_overlay"] = [probes.enter_context(
                guest_weapon_fixture_overlay(game, "portable_stock_split_probe.lua")) for game in games]
        if (stock_take_ammo or stock_shotguns or stock_impact_cues or stock_loki_guest or resident_held_queue
                or stock_interaction_feedback or stock_wipe_chooser):
            proof["stock_take_ammo_overlays"] = [
                probes.enter_context(guest_weapon_fixture_overlay(game, "portable_take_ammo_probe.lua")) for game in games
            ]
        if stock_impact_cues:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableImpactProbe", fixture="portable_impact_probe.lua"))
        if stock_loki_control or stock_loki_guest:
            proof["stock_loki_control_overlays"] = [
                probes.enter_context(guest_weapon_fixture_overlay(game, "portable_loki_control_probe.lua")) for game in games
            ]
        if stock_crossroads_door or stock_dock_gate:
            proof["stock_door_overlays"] = [
                probes.enter_context(stock_door_overlay(game)) for game in games
            ]
        if stock_warehouse_switch or stock_interaction_feedback:
            proof["warehouse_switch_overlays"] = [probes.enter_context(warehouse_switch_overlay(game)) for game in games]
        if stock_warehouse_board:
            proof["warehouse_board_overlays"] = [probes.enter_context(warehouse_board_overlay(game)) for game in games]
        if stock_bottle:
            proof["stock_bottle_overlays"] = [probes.enter_context(stock_bottle_overlay(game)) for game in games]
        if stock_roaming_barter:
            proof["roaming_barter_overlays"] = [probes.enter_context(roaming_barter_overlay(game)) for game in games]
        if stock_critters:
            proof["stock_critter_overlays"] = [probes.enter_context(stock_critter_overlay(game)) for game in games]
        projected_drops = stock_shovel_host_quest or tutorial_dock_pursuit
        if stock_placed_item or projected_drops:
            for game in games:
                probes.enter_context(pickup_preview_overlay(game, portable_script_overlay,
                                                           projected_drops=projected_drops))
        if stock_placed_item:
            proof["stock_placed_item_overlays"] = [
                probes.enter_context(placed_item_overlay(game, portable_script_overlay)) for game in games
            ]
            for game in games:
                probes.enter_context(cash_pickup_overlay(game, portable_script_overlay))
                probes.enter_context(installed_probe(game, mod="SilverfishPortableCashPopupProbe",
                                                     fixture="portable_cash_popup_probe.lua"))
        if stock_mushrooms:
            probes.enter_context(installed_probe(first_game, mod="SilverfishPortableMushroomRegrowthProbe",
                                                 fixture="mushroom_regrowth_probe.lua"))
        if stock_lingering_teleport:
            proof["lingering_teleport_overlays"] = [
                probes.enter_context(lingering_overlay(game, portable_script_overlay)) for game in games
            ]
        if stock_acid_pool:
            proof["acid_pool_overlays"] = [
                probes.enter_context(acid_pool_overlay(game, portable_script_overlay)) for game in games
            ]
        if stock_eye_stacks:
            for game in games:
                probes.enter_context(eye_board_overlay(game))
        if stock_board_offers:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableBoardOfferProbe",
                                                     fixture="portable_board_offer_probe.lua"))
        if (stock_npc_override and resident_weapon_cues) or stock_melee_family or tutorial_dock_melee:
            proof["npc_combat_overlays"] = [probes.enter_context(host_npc_combat_overlay(game)) for game in games]
        elif (stock_host_corpse_carry or stock_npc_rivalry or stock_npc_override or stock_melee_edges
              or tutorial_dock_combat):
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableNpcOverrideProbe",
                                                     fixture="portable_npc_override_probe.lua"))
        if (tutorial_dock_combat or tutorial_dock_melee or stock_bandage_revisit or stock_shovel or stock_buried_placements
                or stock_simmi_jammers or stock_weather_travel or stock_lightning
                or stock_warehouse_switch or stock_warehouse_board or stock_bottle or stock_critters or stock_placed_item
                or stock_crossroads_door or stock_dock_gate
                or stock_board_offers or stock_quarry_photo or stock_eye_stacks or stock_lingering_teleport or stock_acid_pool or stock_mushrooms):
            overlay_key = ("weather_zone_overlays" if stock_weather_travel or stock_lightning else
                           "jammer_zone_overlays" if stock_simmi_jammers else
                           "acid_pool_zone_overlays" if stock_acid_pool else "dock_zone_overlays")
            proof[overlay_key] = [probes.enter_context(tutorial_dock_zone_overlay(game)) for game in games]
        if stock_campfire:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableCampfireProbe",
                                                     fixture="portable_campfire_probe.lua"))
        if tutorial_dock_gait:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableGaitProbe",
                                                     fixture="portable_gait_probe.lua"))
        if tutorial_dock_pursuit or tutorial_dock_held_mask:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableHeldMaskProbe",
                                                     fixture="portable_held_mask_probe.lua"))
        if resident_held_queue:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableHeldQueueProbe",
                                                     fixture="portable_held_queue_probe.lua"))
        if resident_stale_quick_slot:
            fixture = (Path(__file__).with_name("fixtures") / "portable_stale_quick_slot_reply.lua").read_text(encoding="utf-8")

            def add_stale_quick_slot_witness(script):
                marker = "function guest_ui.command(fields, state, phase, bridge)\n"
                if script.count(marker) != 1:
                    raise ValueError("The replica lacks the stock guest inventory projection entry point")
                return script.replace(marker, marker + fixture, 1)

            proof["stale_quick_slot_overlays"] = [
                probes.enter_context(portable_script_overlay(game, "native_guest_ui.lua", add_stale_quick_slot_witness))
                for game in games
            ]
        if resident_weapon_cues or stock_avatar_tail or stock_avatar_binoculars:
            for game in games if stock_avatar_tail or stock_avatar_binoculars else (first_game,):
                probes.enter_context(installed_probe(game, mod="SilverfishPortableWeaponCueProbe",
                                                     fixture="avatar_presentation_probe.lua"))
        if resident_weapon_cues:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableFootstepProbe",
                                                     fixture="portable_footstep_probe.lua"))
        if resident_weapon_cues or stock_light_ar or stock_fire_mode_reopen or stock_alternate_ammo:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableDryFireProbe",
                                                     fixture="portable_dry_fire_probe.lua"))
        if stock_unequip:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableUnequipProbe",
                                                     fixture="portable_unequip_probe.lua"))
        if stock_prop_grip or stock_host_prop_observer:
            for game in games:
                probes.enter_context(installed_probe(game, mod="SilverfishPortableLanternProbe",
                                                     fixture="portable_lantern_probe.lua"))
        if compact_crate_status:
            proof["crate_status_overlay"] = {str(game): probes.enter_context(compact_crate_status_overlay(game)) for game in games}
        if nonmirrored_startup:
            proof["nonmirrored_startup_overlay"] = {
                str(game): probes.enter_context(nonmirrored_startup_overlay(game)) for game in games
            }
        if guest_shop_lease:
            overlays = [probes.enter_context(shop_lease_overlay(game)) for game in games]
            proof["shop_lease_overlay"] = dict(zip(map(str, games), overlays, strict=True))
            for key in ("script_source_overlay", "manifest_overlay_sha256"):
                assert overlays[0][key] == overlays[1][key], "The enrolled seats need identical overlay source"
        if guest_stash_memory:
            overlays = [stash_memory_overlays(probes, game, large=guest_stash_large_memory) for game in games]
            proof["stash_memory_overlay"] = dict(zip(map(str, games), overlays, strict=True))
            for name in overlays[0]:
                for key in ("script_source_overlay", "manifest_overlay_sha256"):
                    assert overlays[0][name][key] == overlays[1][name][key], "Stash overlays differ between seats"
            site_scan_probe = overlays[0]["native_stash.lua"]["site_scan_probe"]
            assert site_scan_probe == overlays[1]["native_stash.lua"]["site_scan_probe"]
        if compact_world_view:
            proof["world_view_overlay"] = {
                str(game): probes.enter_context(portable_main_overlay(game, patch_world_view_receipts)) for game in games
            }
        if stock_crate_ring and not crossroads_profile or compact_crate_status:
            probes.enter_context(installed_probe(second_game, mod="SilverfishPortableCrateProbe",
                                                 fixture="portable_crate_input_probe.lua"))
        if (stock_use or stock_drop or stock_split or stock_equip or stock_nearby or container_nearby
                or guest_stash_memory or nonmirrored_startup):
            for game in games:
                probes.enter_context(installed_probe(game))
        if not second_legacy_work:
            build_ids = []
            for workspace, game in zip(workspaces, games, strict=True):
                with runtime.installed_content(workspace, game):
                    build_ids.append(runtime.staged_build(workspace))
            assert build_ids[0] == build_ids[1], "The two portable fixtures produced different network build identities"
        boot_started = time.perf_counter()
        for game, workspace, release, port in zip(games, workspaces, qualified, choose_ports(), strict=True):
            title = workspace / release.title_directory
            profile, mailbox_root = title / "game-user", title / "run/game-bridge"
            if mailbox_root.is_dir() and any(mailbox_root.iterdir()):
                raise ValueError("Use an unused isolated title configuration for the upgrade test")
            if os.path.lexists(mailbox_root):
                install_stage._ordinary(mailbox_root, directory=True)
            stock = profile / "Saved/SaveGames"
            stock.mkdir(parents=True, exist_ok=True)
            install_stage._ordinary(stock, directory=True)
            for source in stock_slots:
                destination = stock / source.name
                if not os.path.lexists(destination):
                    shutil.copy2(source, destination)
                install_stage._ordinary(destination)
            hashes = stock_save_hashes(profile)
            del hashes["Config.sav"]

            campaign = workspace / "campaigns" / ("title-host-settings-" + uuid.uuid4().hex[:8])
            if resident_cabinet_checkpoint and game == first_game:
                seeded = copy_resident_checkpoint(resident_cabinet_checkpoint, campaign, profile)
            process = launch_portable(game, profile)
            processes.append((game, process))
            mailbox = await discover_mailbox(mailbox_root, process)
            assert runtime.read_json(mailbox / embedded_startup.UNIFIED_FILE) == release.selection
            seat = Seat(profile, campaign, workspace / "clients" / title.name,
                        port, runtime.Bridge(mailbox, process))
            seats.append(seat)
            await offered(seat)
            proof["native_bridges"].append({
                "seat": title.name, "pid": process.pid, "bridge": mailbox.name,
                "runtime": release.package.name, "runtime_manifest_sha256": release.manifest_sha256,
                "workspace": str(workspace), "legacy_descriptor_selected": workspace == second_legacy_work,
            })
            baselines.append((hashes, await stock_profile(seat.bridge)))

        first, second = seats
        proof["boot_seconds"] = round(time.perf_counter() - boot_started, 3)

        async def check_gameplay(host, guest, pawn):
            evidence = await verify_gameplay(host, guest, pawn, stock_drop=stock_drop, stock_split=stock_split,
                                             stock_equip=stock_equip, stock_nearby=stock_nearby, projected_nearby=projected_nearby)
            if stock_use:
                evidence["guest_stock_use"] = await verify_stock_use(host, guest, pawn, starter=guest is second)
            if memory_bridge:
                host_status = await wait(host.campaign / "run", lambda state: state.get("local_bridge") == "memory")
                guest_status = await wait(guest.client / "run", lambda state: state.get("local_bridge") == "memory")
                assert host_status["transport"]["schema_version"] == guest_status["transport"]["schema_version"] == 1
                evidence["local_bridge"] = {"host": host_status["local_bridge"], "guest": guest_status["local_bridge"]}
            if memory_status:
                for run in (host.campaign / "run", guest.client / "run"):
                    await wait(run, lambda state: state.get("local_status_source") == "memory")
                evidence["memory_status_both_peers"] = True
            if container_nearby:
                evidence["guest_stock_nearby_container_pickup"] = await verify_container_pickup(
                    host, guest, pawn, memory_ui=guest_ui_memory)
            return evidence
        if nonmirrored_startup:
            startup = proof["nonmirrored_startup"] = {}
            proof["sessions"].append(await verify_nonmirrored_startup(first, second, startup))
        elif guest_world_start:
            startup = GuestWorldStartProof(second)
            character_name = "Observatory startup scout"

            async def check_reconnect(guest, started):
                await startup.reconnected(guest, started)
                if guest_world_start_travel:
                    await startup.travel(first, guest, portable_probe_command,
                                         blocker_name=character_name if zone_lease_preflight else None)

            exit_options = {"host_exit_actions": ("prompt", "confirm"), "after_exit": startup.verify_travel_exit} \
                if guest_world_start_travel else {}
            proof["sessions"].append(await start_pair(first, second, character_name,
                                                      before_join=startup.arm, on_join=startup.joined,
                                                      after_reconnect=check_reconnect,
                                                      wait_status=wait_resident_status, **exit_options))
        elif stock_fire_mode_reopen:
            proof["sessions"].extend(await verify_fire_mode_reopen(
                first, second, portable_probe_command, proof.setdefault("stock_fire_mode_reopen", {})))
        elif stock_unequip and not resident_campaigns:
            proof["sessions"].append(await verify_stock_unequip(first, second, proof.setdefault("stock_unequip", {})))
        elif stock_prop_grip:
            proof["sessions"].append(await start_pair(first, second, "Lantern scout", on_join=verify_stock_prop_grip))
        elif resident_campaigns:
            results = proof["resident_check_results"] = {}
            proof["resident_campaigns"] = {campaign: [name for name, _ in checks]
                                           for campaign, checks in resident_campaigns.items()}
            used_ports = {first.port, second.port}
            if stock_unequip:
                proof["sessions"].append(await verify_stock_unequip(first, second, proof.setdefault("stock_unequip", {})))

            async def run_resident_campaign(campaign, checks, host):
                campaign_started = time.perf_counter()
                gameplay = {}

                def finish_check(name, started, error=None, *, evidence=None):
                    row = {"passed": error is None, "status": "passed" if error is None else "failed",
                           "seconds": round(time.perf_counter() - started, 3)}
                    if evidence:
                        row["evidence"] = evidence
                    if error is not None:
                        row.update(error=str(error) or type(error).__name__, traceback=traceback.format_exc())
                        message = row["error"].splitlines()[0]
                        if len(message) > 1200:
                            message = message[:1200] + "… Full details are in portable-title-verification.json."
                        print(f"{name} failed: {message}", flush=True)
                    results[name] = row

                def skip_check(name, blocked_by):
                    results[name] = {"passed": False, "status": "skipped", "blocked_by": blocked_by}
                    print(f"{name} skipped after {blocked_by} failed", flush=True)

                async def run_checks(host, guest, _pawn):
                    gameplay["join_seconds"] = round(time.perf_counter() - campaign_started, 3)
                    proof.setdefault("join_seconds", gameplay["join_seconds"])
                    gameplay.update(await run_resident_checks(checks, host, guest, finish_check, skip_check))
                    return gameplay

                async def check_shovel_reconnect(guest, _started):
                    evidence = gameplay.get("stock_missing_contact_guest_dig")
                    if evidence is None:
                        return
                    started = time.perf_counter()
                    try:
                        await verify_stock_shovel_reconnect(host, guest, evidence)
                    except Exception as error:
                        finish_check("stock_missing_contact_reconnect_and_turn_in", started, error)
                    else:
                        finish_check("stock_missing_contact_reconnect_and_turn_in", started)

                async def check_personal_reconnect(guest, _started):
                    evidence = gameplay.get("stock_personal_consumable")
                    if evidence is None:
                        return
                    started = time.perf_counter()
                    try:
                        await verify_personal_consumable_reconnect(host, guest, evidence, portable_probe_command)
                    except Exception as error:
                        finish_check("stock_personal_consumable_reconnect", started, error)
                    else:
                        finish_check("stock_personal_consumable_reconnect", started)

                async def check_storm_reconnect(guest, reconnect_started):
                    if "stock_storm_ambience" not in gameplay:
                        return
                    started = time.perf_counter()
                    try:
                        await storm_trial.reconnected(host, guest, reconnect_started)
                    except Exception as error:
                        finish_check("stock_storm_ambience_reconnect", started, error)
                    else:
                        finish_check("stock_storm_ambience_reconnect", started)

                async def check_loki_reconnect(guest, _started):
                    if (saved := gameplay.get("loki_saved_load")) is None:
                        return
                    started = time.perf_counter()
                    try:
                        await verify_guest_loki_reconnected(host, guest, saved)
                    except Exception as error:
                        finish_check("stock_loki_reconnect", started, error)
                    else:
                        finish_check("stock_loki_reconnect", started)

                exit_actions = ("prompt", "confirm") if campaign in {
                    "tutorial_dock", "tutorial_dock_combat", "tutorial_dock_shot_view", "tutorial_dock_buckshot",
                    "tutorial_dock_melee", "tutorial_dock_late_join",
                    "crossroads", "crossroads_anomaly", "quarry_photo", "buried_placements",
                    "lab_east_wing_acid", "shopkeep"
                } or stock_host_prop_observer and campaign == prop_campaign else ("save_and_quit",)
                after_reconnect = None
                if campaign == "crossroads" and stock_shovel_quest and not stock_shovel_host_quest:
                    after_reconnect = check_shovel_reconnect
                if campaign == "personal_consumable":
                    after_reconnect = check_personal_reconnect
                if campaign == "storm":
                    after_reconnect = check_storm_reconnect
                if campaign == "observatory" and stock_loki_guest:
                    after_reconnect = check_loki_reconnect

                async def unexpected_process_exit():
                    while True:
                        for role, seat in (("host", host), ("guest", second)):
                            code = seat.bridge.process.poll()
                            if code is not None:
                                raise RuntimeError(f"The staged {role} exited during {campaign} with code {code}")
                        await asyncio.sleep(.2)

                before_join = {"storm": storm_trial.prepare} if storm_trial else {}
                before_join["tutorial_dock_late_join"] = prepare_warehouse
                session = asyncio.create_task(start_pair(
                    host, second, "Gameplay scout", on_join=run_checks,
                    before_join=before_join.get(campaign),
                    initial_map=WAREHOUSE_DOCK if campaign == "tutorial_dock_late_join" else OBSERVATORY,
                    wait_status=wait_resident_status, host_exit_actions=exit_actions,
                    after_reconnect=after_reconnect))
                watcher = asyncio.create_task(unexpected_process_exit())
                try:
                    done, _ = await asyncio.wait((session, watcher), return_when=asyncio.FIRST_COMPLETED)
                    return await (watcher if watcher in done else session)
                finally:
                    for task in (session, watcher):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(session, watcher, return_exceptions=True)

            host = first
            for campaign, checks in resident_campaigns.items():
                if proof["sessions"]:
                    # A fresh loopback port also preserves the stock first-contact trust exercise.
                    port = next(port for port in choose_ports() if port not in used_ports)
                    used_ports.add(port)
                    host = replace(first, port=port, campaign=first.campaign.with_name(
                        "title-host-settings-" + uuid.uuid4().hex[:8]))
                session = await run_resident_campaign(campaign, checks, host)
                proof["sessions"].append(dict(session, resident_campaign=campaign))
                if saved := session.get("loki_saved_load"):
                    async def reopen_loki(host, guest, pawn):
                        return await verify_guest_loki_reopened(host, guest, pawn, saved)

                    reopened = await start_pair(host, second, "Gameplay scout", load_existing=True,
                                                resume_character=session["character_id"], on_join=reopen_loki,
                                                wait_status=wait_resident_status)
                    assert reopened["campaign_id"] == session["campaign_id"]
                    assert reopened["revision"] > session["revision"]
                    proof["sessions"].append(dict(reopened, resident_campaign="loki_reopened"))
            assert len({row["campaign_id"] for row in proof["sessions"]}) == len(resident_campaigns) + int(stock_unequip)
            assert all(row["host_pid"] == first.bridge.process.pid and row["guest_pid"] == second.bridge.process.pid
                       for row in proof["sessions"])
            if resident_weapon_cues:
                assert not weapon_cue_files(first, second), "A legacy weapon cue mailbox appeared before Save and Quit"
        elif compact_world_view:
            proof["sessions"].append(await start_pair(first, second, "Weather receipt scout", on_join=verify_world_view_receipt,
                                                      wait_status=wait_resident_status))
        elif compact_crate_status:
            async def refresh_host_pause(_guest, _started):
                # The generic title helper reads Pause readiness over its file bridge.
                armed = await wait(first.campaign / "run",
                                   lambda frame: frame.get("game", {}).get("stock_pause_exit", {}).get("state") == "armed")
                game = armed["game"]
                crate = proof["crate_status_trial"]["stock_crate"]
                await first.bridge.command(native_loot.CRATE_DIAGNOSTIC, game["map"], game["pawns"]["local"]["name"], crate)

            async def check_crates(host, guest, pawn):
                result = await verify_compact_crate_status(host, guest, pawn)
                proof["crate_status_trial"] = result
                return {"compact_crate_status": True}

            proof["sessions"].append(await start_pair(first, second, "Crate diagnostics scout", on_join=check_crates,
                                                      after_reconnect=refresh_host_pause))
        elif stock_crate_ring:
            proof["sessions"].append(await start_pair(first, second, "Crate scout", on_join=verify_stock_crate_ring))
        elif guest_shop_lease:
            proof["sessions"].append(await start_pair(first, second, "Merchant scout", on_join=verify_guest_shop_lease))
        elif guest_stash_memory:
            async def check_stash(host, guest, pawn):
                return await verify_guest_stash_memory(host, guest, pawn, large=guest_stash_large_memory,
                                                       site_scan_probe=site_scan_probe)

            proof["sessions"].append(await start_pair(first, second, "Stash scout", on_join=check_stash,
                                                      create_character=create_stock_bag_character,
                                                      wait_status=wait_resident_status))
        elif guest_ui_memory:
            proof["sessions"].append(await start_pair(first, second, "Inventory scout", on_join=check_gameplay))
        elif resident_cabinet_checkpoint:
            assert seeded is not None
            cabinet_evidence = {}
            proof["cabinet_pickup"] = cabinet_evidence

            async def check_cabinet(host, guest, pawn):
                cabinet_evidence.update(await verify_resident_pickup(host, guest, pawn, seeded, combined=combined_presentation))
                return cabinet_evidence

            async def check_cabinet_saved(host, stopped, saved, character):
                cabinet_evidence["stock_save_and_quit"] = await verify_saved_pickup(host, stopped, saved, seeded)

            proof["sessions"].append(await start_pair(first, second, "Cabinet scout", on_join=check_cabinet,
                                                      load_existing=True, wait_status=wait_resident_status,
                                                      after_exit=check_cabinet_saved))
        else:
            create = create_stock_split_character if stock_split or stock_equip else create_stock_drop_character if stock_drop else None
            proof["sessions"].append(await start_pair(first, second, "Second portable scout", on_join=check_gameplay,
                                                      create_character=create_stock_bag_character if stock_use else create,
                                                      guest_stock_exit=stock_pause_exit))
            proof["sessions"].append(await start_pair(second, first, "First portable scout", on_join=check_gameplay,
                                                      create_character=create_stock_drop_character if stock_use else create))
            assert proof["sessions"][0]["host_pid"] == proof["sessions"][1]["guest_pid"]
            assert proof["sessions"][0]["guest_pid"] == proof["sessions"][1]["host_pid"]
            assert len({leg["campaign_id"] for leg in proof["sessions"]}) == 2
        if steam_cloud:
            proof["steam_cloud"] = await verify_cloud_restore(first, second, proof["sessions"][0], second_game, processes)
        for seat, (hashes, preferences) in zip(seats, baselines, strict=True):
            for key in ("singleplayer", "modes", "multiplayer", "modes", "singleplayer"):
                button = await seat.bridge.command("native-title-button-fixture", key)
                await seat.bridge.wait_for(lambda frame: frame["sequence"] > button["sequence"])
            cached = await seat.bridge.command("native-title-stock-profile-fixture", "cache")
            assert cached["ack"]["message"] == preferences
            observed = stock_save_hashes(seat.profile)
            del observed["Config.sav"]
            assert observed == hashes and await stock_profile(seat.bridge) == preferences
            assert seat.bridge.process.poll() is None
        if stock_npc_override or tutorial_dock_pursuit:
            slot = max((source for source in stock_slots if source.stem != "Config"),
                       key=lambda source: source.stat().st_mtime).stem
            proof["singleplayer_after_coop"] = await verify_solo_combat(first, slot)
        failures = [name for name, result in proof.get("resident_check_results", {}).items()
                    if result["status"] == "failed"]
        if failures:
            raise AssertionError(f"Resident checks failed: {', '.join(failures)}")
        proof.update(verified=True, solo_slots_and_settings_preserved=True,
                     both_titles_reused=len(proof["sessions"]) > 1,
                     launched_without_coop_environment=True)
    except Exception as error:
        if nonmirrored_startup:
            proof["nonmirrored_startup_failure"] = str(error) or type(error).__name__
        if stock_host_prop_observer:
            proof["stock_host_prop_observer_failure"] = str(error) or type(error).__name__
        if resident_campaigns and not any(not row["passed"] for row in proof.get("resident_check_results", {}).values()):
            proof["resident_session_failure"] = str(error) or type(error).__name__
        if compact_crate_status:
            proof["crate_status_failure"] = str(error)
        if compact_world_view:
            proof["world_view_receipt_failure"] = str(error) or type(error).__name__
        if guest_shop_lease:
            proof["shop_lease_failure"] = str(error) or type(error).__name__
        if guest_stash_memory:
            proof["stash_memory_failure"] = str(error) or type(error).__name__
        if resident_cabinet_checkpoint:
            proof["cabinet_pickup_failure"] = str(error) or type(error).__name__
        if guest_world_start_travel:
            proof["stock_travel_failure"] = str(error)
        raise
    finally:
        proof["game_executable_launches"] = len(processes)
        proof["native_boot_states"] = {str(game): boot_states(game) for game, _ in processes}
        for _, process in reversed(processes):
            if process.poll() is None:
                await runtime.stop_game(process)
        if single_scenario:
            proof["processes_closed"] = len(processes) == 2 and all(process.poll() is not None for _, process in processes)
            proof["verified"] &= proof["processes_closed"]
        probes.close()
        proof["original_saves_verified"] = saved_hashes(original) == before
        if resident_cabinet_checkpoint:
            proof["resident_source_preserved"] = campaign_save_hashes(resident_cabinet_checkpoint) == source_before
            proof["verified"] &= proof["resident_source_preserved"]
        proof["portable_files_and_existing_activation_preserved"] = all(
            fingerprint(path) == digest for release in qualified for path, digest in release.activation.items()
        )
        proof["verified"] &= proof["original_saves_verified"] and proof["portable_files_and_existing_activation_preserved"]
        runtime.write_json(case / "portable-title-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"
        assert proof.get("resident_source_preserved", True), "The source cabinet checkpoint changed"
        assert proof["portable_files_and_existing_activation_preserved"], "The portable marker or existing activation changed"
        if guest_world_start or guest_shop_lease or guest_stash_memory or nonmirrored_startup or resident_weapon_cues:
            assert all(process.poll() is not None for _, process in processes), "A portable test process is still running"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="Existing private parent for new evidence and profile directories")
    parser.add_argument("--first-game", type=Path, required=True, help="Clean replica with the release ZIP applied and no mods.txt")
    parser.add_argument("--second-game", type=Path, required=True, help="Separate replica with the same release ZIP applied")
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--stock-pause-exit", action="store_true",
                        help="exercise guest stock Quit to Menu and host Save and Quit in the same resident pair")
    parser.add_argument("--stock-drop", action="store_true", help="test stock guest backpack Sort, Drop, and Use actions")
    parser.add_argument("--stock-use", action="store_true", help="open a starter bag by stock drag and use a Bandage by stock click")
    parser.add_argument("--stock-food", action="store_true", help="verify the guest's Food stat after a meal, combinable with campfire and weapon cues")
    parser.add_argument("--stock-cooked-meal", action="store_true", help="verify the guest's stock cooking pot Eat All action")
    parser.add_argument("--stock-personal-consumable", action="store_true",
                        help="use an unlisted stock consumable through the guest inventory and recover it across reconnect")
    parser.add_argument("--stock-campfire", action="store_true", help="verify stock campfire damage and feedback, combinable with food and weapon cues")
    parser.add_argument("--stock-board-offers", action="store_true",
                        help="compare stock board offers and complete a Forest fuel tank bounty through guest gunfire")
    parser.add_argument("--stock-simmi-jammers", action="store_true",
                        help="Complete both stock Simmi jammer bounties through the installed title")
    parser.add_argument("--stock-eye-stacks", action="store_true",
                        help="redeem ShopKeep's Eye contract from one of two stock guest inventory stacks")
    parser.add_argument("--stock-lingering-teleport", action="store_true",
                        help="verify authority and presentation of the moving Crossroads teleport anomaly")
    parser.add_argument("--stock-acid-pool", action="store_true",
                        help="verify stock authored pool damage and lifecycle in Lab East Wing")
    parser.add_argument("--stock-flashlights", action="store_true",
                        help="Observe stock handheld and offhand beams on the connected seat")
    parser.add_argument("--stock-host-corpse-carry", action="store_true",
                        help="Measure stock host ragdoll carry on the guest and verify physics returns on release")
    parser.add_argument("--stock-npc-override", action="store_true",
                        help="verify stock drone damage authority in co-op and stock combat after returning to Singleplayer")
    parser.add_argument("--stock-split", action="store_true", help="test the stock guest stack-split popup with a Veteran loadout")
    parser.add_argument("--stock-equip", action="store_true", help="test the stock guest Equip action with a Veteran loadout")
    parser.add_argument("--stock-unequip", action="store_true",
                        help="verify stock solo and host-authorized guest quick-slot Unequip")
    parser.add_argument("--stock-nearby", action="store_true", help="pick up a loose actor from the guest's stock Nearby Items panel")
    parser.add_argument("--projected-nearby", action="store_true", help="reclaim a player drop through stock Nearby Items")
    parser.add_argument("--container-nearby", action="store_true", help="pick up live stock container loot through Nearby Items")
    parser.add_argument("--steam-cloud", action="store_true", help="test a copied cloud backup through Host Load Game")
    parser.add_argument("--memory-bridge", action="store_true", help="verify both embedded workers use the native bridge")
    parser.add_argument("--memory-status", action="store_true", help="verify both workers consume live frames through memory")
    parser.add_argument("--resident-weapon-cues", action="store_true",
                        help="verify Bandage, footsteps, firing and reload, combinable with campfire and food")
    parser.add_argument("--resident-held-queue", action="store_true",
                        help="verify a stock SMG hold through the resident in-memory input queue")
    parser.add_argument("--resident-stale-quick-slot", action="store_true",
                        help="replay a prior stock inventory view after switching to the SMG")
    parser.add_argument("--rusty-pistol", action="store_true",
                        help="exercise the stock Rusty Pistol during the resident weapon checks")
    parser.add_argument("--stock-alternate-ammo", action="store_true",
                        help="verify guest stock .45 hollow point reload, fire, and Take Ammo")
    parser.add_argument("--stock-light-ar", action="store_true",
                        help="verify one guest stock LightAR tap and R reload with rifle FMJ")
    parser.add_argument("--stock-fire-mode-reopen", action="store_true",
                        help="save a guest's stock LightAR selector choice and reopen the same campaign")
    parser.add_argument("--tutorial-dock-combat", action="store_true",
                        help="follow the stock exit to Tutorial Dock and dispatch a shot at a placed worker")
    parser.add_argument("--tutorial-dock-retaliation", action="store_true",
                        help="observe the stock AI's response to a nonlethal guest shot at a placed worker")
    parser.add_argument("--tutorial-dock-gait", action="store_true",
                        help="verify the stock grounded gait on the Dock observer")
    parser.add_argument("--tutorial-dock-held-mask", action="store_true",
                        help="check observer-only masking of a late held mesh")
    parser.add_argument("--tutorial-dock-pursuit", action="store_true",
                        help="fight the live warehouse worker with local guest aiming and movement through death")
    parser.add_argument("--tutorial-dock-shot-view", action="store_true",
                        help="compare blocked and clear stock guest shots at the same warehouse door")
    parser.add_argument("--tutorial-dock-melee", action="store_true",
                        help="verify the stock guest melee input damages a live, full-health warehouse worker")
    parser.add_argument("--stock-bandage-revisit", action="store_true",
                        help="collect the Tutorial Dock bandage and pistol and compare both views on revisit")
    parser.add_argument("--stock-warehouse-switch", action="store_true",
                        help="verify host and guest interactions on the Tutorial Dock warehouse circuit box")
    parser.add_argument("--stock-warehouse-board", action="store_true",
                        help="verify both peers see the Tutorial Dock warehouse board break")
    parser.add_argument("--stock-quarry-photo", action="store_true",
                        help="verify a guest stock camera shutter advances the authored Bronze Quarry photo objective")
    parser.add_argument("--stock-bottle", action="store_true",
                        help="verify stock bottle throws, prop break cues, acid pools, and wooden debris on both peers")
    parser.add_argument("--stock-throwables", choices=("all", "pipebomb", "bolt", "ground"), nargs="?", const="all",
                        help="verify host and guest bolt flight, a stock host pipe bomb detonation, or both")
    parser.add_argument("--stock-roaming-barter", action="store_true",
                        help="compare the stock identity of a replicated dynamically spawned NPC trader")
    parser.add_argument("--stock-critters", action="store_true",
                        help="verify a Crossroads frog hops and can be picked up by the guest")
    parser.add_argument("--stock-buried-placements", action="store_true",
                        help="verify authored buried rewards in Ruin Fields and Beach in the same pair")
    parser.add_argument("--stock-shovel", action="store_true",
                        help="verify stock shovel digs from both co-op seats")
    parser.add_argument("--stock-shovel-loot", action="store_true",
                        help="also verify stock unearthed loot and props during the shovel check")
    parser.add_argument("--stock-shovel-quest", action="store_true",
                        help="verify stock guest excavation and pickup of the buried Missing Contact package")
    parser.add_argument("--stock-shovel-host-quest", action="store_true",
                        help="excavate the Missing Contact package as host and pick it up as guest")
    parser.add_argument("--stock-take-ammo", action="store_true",
                        help="unload guest firearms through the normal stock context menu")
    parser.add_argument("--stock-shotguns", action="store_true",
                        help="verify guest shotgun Take Ammo, reload, and fire for each stock 12-gauge round")
    parser.add_argument("--stock-impact-cues", action="store_true",
                        help="verify the guest's suppressed shotgun receives the host's individual stock impact decals")
    parser.add_argument("--stock-avatar-binoculars", action="store_true",
                        help="verify both peers see and hear the stock binoculars raise and lower")
    parser.add_argument("--stock-avatar-tail", action="store_true",
                        help="verify the armored stock tail seam through turning and jumping")
    parser.add_argument("--stock-interaction-feedback", action="store_true",
                        help="verify the guest's stock lockpick equipment and Observatory television")
    parser.add_argument("--stock-loki-control", action="store_true",
                        help="verify stock Loki mode, reload, and fire on the three host receiver mounts")
    parser.add_argument("--stock-loki-guest", action="store_true",
                        help="verify guest Loki mode, reload, and independent primary and underbarrel fire")
    parser.add_argument("--stock-npc-rivalry", action="store_true",
                        help="observe stock perception, attacks, and death between two NPC factions")
    parser.add_argument("--guest-ui-memory", action="store_true",
                        help="verify the resident guest UI refresh on one stock Nearby Items container pickup")
    parser.add_argument("--stock-prop-grip", action="store_true", help="verify default guest carry filtering in one portable lantern session")
    parser.add_argument("--stock-host-prop-observer", action="store_true",
                        help="sample a carried lantern and verify stock light and heavy prop impacts")
    parser.add_argument("--stock-crate-ring", action="store_true", help="verify one guest stock F crate open through the in-process ring")
    parser.add_argument("--compact-crate-status", action="store_true",
                        help="test normal crate status routing with one private muted portable pair and a temporary Lua overlay")
    parser.add_argument("--guest-shop-lease", action="store_true",
                        help="verify the Observatory stock shop remains open while guest interaction leases stay in memory")
    stash_cases = parser.add_mutually_exclusive_group()
    stash_cases.add_argument("--guest-stash-memory", action="store_true",
                            help="verify empty and filled stock Observatory stash renders through the resident bridge")
    stash_cases.add_argument("--guest-stash-large-memory", action="store_true",
                            help="verify a stock Observatory stash larger than one ordinary resident command ticket")
    parser.add_argument("--compact-world-view", action="store_true",
                        help="verify one compact resident stock weather receipt in a private muted portable pair")
    parser.add_argument("--stock-weather-travel", action="store_true",
                        help="verify stock weather, rain, and sirens after opening Crossroads in one muted portable pair")
    parser.add_argument("--stock-dock-gate", action="store_true",
                        help="verify stock guest opening and closing animation on the Intro bars gate")
    parser.add_argument("--stock-crossroads-door", action="store_true",
                        help="verify stock guest door toggles, replay, and panel collision in Crossroads")
    parser.add_argument("--crossroads-profile", action="store_true",
                        help="measure co-op frame and subsystem times around the stock weather travel check")
    parser.add_argument("--stock-lightning", action="store_true",
                        help="verify one stock lightning strike and thunder across a private Crossroads pair")
    parser.add_argument("--stock-storm-ambience", action="store_true",
                        help="verify active stock storms across fresh join and reconnect in one private pair")
    parser.add_argument("--stock-mushrooms", action="store_true",
                        help="verify both players harvest identified stock Crossroads mushrooms in one campaign")
    parser.add_argument("--stock-placed-item", action="store_true",
                        help="verify one placed Crossroads item's stock carry, drop, and shared pickup")
    parser.add_argument("--stock-melee-family", action="store_true",
                        help="verify guest Heavy Pipe and Old Pipe kills and stock melee input")
    parser.add_argument("--stock-melee-edges", action="store_true",
                        help="verify guest Old Pipe tap, charge, and combo without spawning an NPC target")
    parser.add_argument("--stock-wipe-chooser", action="store_true",
                        help="verify stock Pause and Journal yield to the stock party wipe chooser and both players confirm")
    parser.add_argument("--guest-world-start", action="store_true",
                        help="verify first stock guest map startup and short reconnect in one portable session")
    parser.add_argument("--nonmirrored-startup", action="store_true",
                        help="verify installed host/guest title readiness without live game status file mirroring")
    parser.add_argument("--guest-world-start-travel", action="store_true",
                        help="also verify stock guest startup through Crossroads and Ruin Fields")
    parser.add_argument("--zone-lease-preflight", action="store_true",
                        help="check the named Observatory wall blocker and file-free lease before the stock travel")
    parser.add_argument("--resident-cabinet-archive", type=Path,
                        help="profile one stock C_3 pickup using the published baseline or pinned candidate ZIP")
    parser.add_argument("--resident-cabinet-candidate-sha256",
                        help="expected archive SHA-256 for the joint pickup candidate, omitted for the published baseline")
    parser.add_argument("--resident-cabinet-checkpoint", type=Path,
                        help="closed standalone Observatory C_3 checkpoint whose stock escrow will be copied")
    parser.add_argument("--second-legacy-work", type=Path,
                        help="Unused isolated workspace from the second replica's retained legacy descriptor")
    args = parser.parse_args()
    if args.nonmirrored_startup and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip, args.stock_host_prop_observer,
            args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory,
            args.compact_crate_status, args.compact_world_view, args.guest_shop_lease, args.resident_cabinet_archive,
            args.second_legacy_work)):
        parser.error("Use --nonmirrored-startup with two fresh private replicas and no other gameplay fixtures")
    if args.zone_lease_preflight and not args.guest_world_start_travel:
        parser.error("Use --zone-lease-preflight with --guest-world-start-travel")
    if (args.guest_stash_memory or args.guest_stash_large_memory) and any((
                                       args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                       args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                       args.stock_host_prop_observer, args.nonmirrored_startup,
                                       args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                       args.guest_ui_memory, args.guest_shop_lease, args.compact_crate_status,
                                       args.compact_world_view, args.steam_cloud, args.resident_cabinet_archive,
                                       args.second_legacy_work)):
        parser.error("Use the guest stash checks with two fresh private replicas and no other gameplay fixtures")
    if args.guest_shop_lease and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                     args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                     args.guest_ui_memory, args.compact_crate_status, args.compact_world_view,
                                     args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --guest-shop-lease with two fresh private replicas and no other gameplay fixtures")
    if args.compact_world_view and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                        args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                        args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                        args.guest_ui_memory, args.compact_crate_status, args.steam_cloud,
                                        args.second_legacy_work)):
        parser.error("Use --compact-world-view with two fresh private replicas and no other gameplay fixtures")
    if args.compact_crate_status and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                          args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                          args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                          args.guest_ui_memory, args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --compact-crate-status with two fresh private replicas and no other gameplay fixtures")
    if args.guest_ui_memory and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.stock_prop_grip,
                                     args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel,
                                     args.steam_cloud, args.second_legacy_work)):
        parser.error("Use --guest-ui-memory without other gameplay fixtures")
    if args.projected_nearby and (args.stock_drop == args.stock_split or args.stock_nearby):
        parser.error("Use --projected-nearby with either --stock-drop or --stock-split and without --stock-nearby")
    if args.stock_use and (args.stock_drop or args.stock_split or args.stock_equip):
        parser.error("Use --stock-use without other stock backpack character fixtures")
    if args.stock_prop_grip and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                    args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                    args.stock_crate_ring)):
        parser.error("Use --stock-prop-grip without other gameplay fixtures")
    if args.stock_host_prop_observer and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip, args.stock_crate_ring,
            args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory, args.compact_crate_status,
            args.compact_world_view, args.guest_shop_lease, args.resident_cabinet_archive, args.second_legacy_work)):
        parser.error("Use --stock-host-prop-observer with two fresh private replicas and no other gameplay fixtures")
    resident_check_options = {"stock_unequip", "stock_food", "stock_personal_consumable", "stock_wipe_chooser",
                              "stock_campfire", "stock_npc_override",
                              "stock_board_offers", "stock_quarry_photo", "resident_weapon_cues", "resident_held_queue", "resident_stale_quick_slot",
                              "rusty_pistol", "tutorial_dock_combat", "tutorial_dock_retaliation", "tutorial_dock_pursuit",
                              "tutorial_dock_held_mask", "tutorial_dock_gait",
                              "tutorial_dock_shot_view",
                              "tutorial_dock_melee", "stock_npc_rivalry",
                              "stock_bandage_revisit", "stock_buried_placements", "stock_shovel", "stock_shovel_loot",
                              "stock_shovel_quest", "stock_shovel_host_quest", "stock_take_ammo",
                              "stock_shotguns", "stock_impact_cues", "stock_avatar_tail", "stock_avatar_binoculars",
                              "stock_interaction_feedback",
                              "stock_loki_control", "stock_loki_guest",
                              "stock_host_corpse_carry", "stock_host_prop_observer", "stock_flashlights", "stock_simmi_jammers",
                              "stock_weather_travel", "stock_lightning", "stock_crossroads_door", "stock_dock_gate",
                              "stock_storm_ambience",
                              "stock_melee_family", "stock_melee_edges", "stock_warehouse_switch", "stock_warehouse_board", "stock_bottle",
                              "stock_roaming_barter", "stock_critters", "stock_placed_item", "stock_mushrooms", "stock_throwables", "stock_eye_stacks",
                              "stock_lingering_teleport", "stock_light_ar", "stock_acid_pool", "stock_alternate_ammo"}
    if args.resident_held_queue and not args.resident_weapon_cues:
        parser.error("Use --resident-held-queue with --resident-weapon-cues")
    if args.stock_fire_mode_reopen and (args.second_legacy_work or args.resident_cabinet_archive
            or any(value is True for key, value in vars(args).items() if key != "stock_fire_mode_reopen")):
        parser.error("Use --stock-fire-mode-reopen with a fresh private pair and no other gameplay fixtures")
    if args.resident_stale_quick_slot and not args.resident_held_queue:
        parser.error("Use --resident-stale-quick-slot with --resident-held-queue")
    if args.rusty_pistol and not args.resident_weapon_cues:
        parser.error("Use --rusty-pistol with --resident-weapon-cues")
    if args.tutorial_dock_combat and not (args.rusty_pistol and (
            args.tutorial_dock_pursuit or args.stock_npc_override or args.tutorial_dock_gait)):
        parser.error("Use --tutorial-dock-combat with --rusty-pistol and a Dock pursuit, NPC, or gait fixture")
    if args.tutorial_dock_retaliation and not args.tutorial_dock_combat:
        parser.error("Use --tutorial-dock-retaliation with --tutorial-dock-combat")
    if args.tutorial_dock_gait and not args.tutorial_dock_combat:
        parser.error("Use --tutorial-dock-gait with --tutorial-dock-combat")
    if args.tutorial_dock_held_mask and (not args.tutorial_dock_combat or args.tutorial_dock_pursuit
                                         or args.tutorial_dock_retaliation):
        parser.error("Use --tutorial-dock-held-mask with --tutorial-dock-combat and without another Dock fight")
    if args.tutorial_dock_pursuit and (not args.tutorial_dock_combat or args.tutorial_dock_retaliation):
        parser.error("Use --tutorial-dock-pursuit with --tutorial-dock-combat and without --tutorial-dock-retaliation")
    if args.tutorial_dock_shot_view and (not args.tutorial_dock_combat or not args.stock_npc_override or any(
            (args.tutorial_dock_retaliation, args.tutorial_dock_gait, args.tutorial_dock_held_mask))):
        parser.error("Use --tutorial-dock-shot-view with --tutorial-dock-combat and --stock-npc-override")
    if args.stock_shovel_loot and not args.stock_shovel:
        parser.error("Use --stock-shovel-loot with --stock-shovel")
    if args.stock_shovel_quest and (not args.stock_shovel or args.stock_shovel_loot):
        parser.error("Use --stock-shovel-quest with --stock-shovel alone")
    if args.stock_shovel_host_quest and not args.stock_shovel_quest:
        parser.error("Use --stock-shovel-host-quest with --stock-shovel-quest")
    resident_check_options.add("stock_cooked_meal")
    resident_check_options.add("crossroads_profile")
    if args.crossroads_profile:
        resident_check_options.add("stock_crate_ring")
    if any((args.stock_food, args.stock_cooked_meal, args.stock_personal_consumable, args.stock_wipe_chooser,
            args.stock_campfire, args.stock_npc_override, args.stock_board_offers, args.stock_quarry_photo, args.tutorial_dock_melee,
            args.stock_npc_rivalry, args.stock_bandage_revisit, args.stock_shovel, args.stock_buried_placements,
            args.stock_take_ammo, args.stock_shotguns,
            args.stock_impact_cues, args.stock_avatar_tail, args.stock_avatar_binoculars, args.stock_interaction_feedback,
            args.stock_loki_control, args.stock_loki_guest,
            args.stock_host_corpse_carry, args.stock_flashlights, args.stock_simmi_jammers, args.stock_weather_travel,
            args.crossroads_profile, args.stock_lightning, args.stock_crossroads_door, args.stock_dock_gate,
            args.stock_storm_ambience,
            args.stock_melee_family, args.stock_melee_edges, args.stock_warehouse_switch, args.stock_warehouse_board, args.stock_bottle,
            args.stock_roaming_barter, args.stock_critters, args.stock_placed_item, args.stock_throwables, args.stock_eye_stacks,
            args.stock_lingering_teleport, args.stock_acid_pool, args.stock_mushrooms)) and (
            args.second_legacy_work or args.resident_cabinet_archive or any(
                value is True for key, value in vars(args).items() if key not in resident_check_options)):
        parser.error("Run the resident gameplay checks without other gameplay fixtures")
    if args.resident_weapon_cues and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.steam_cloud, args.stock_prop_grip,
            args.stock_crate_ring, args.guest_world_start, args.guest_world_start_travel, args.guest_ui_memory,
            args.compact_crate_status, args.compact_world_view, args.guest_shop_lease, args.guest_stash_memory,
            args.guest_stash_large_memory, args.nonmirrored_startup, args.resident_cabinet_archive, args.second_legacy_work)):
        parser.error("Use --resident-weapon-cues with two fresh private replicas and no other gameplay fixtures")
    if args.stock_crate_ring and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                     args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                     args.second_legacy_work)):
        parser.error("Use --stock-crate-ring with two fresh private replicas and no other gameplay fixtures")
    if (args.guest_world_start or args.guest_world_start_travel) and any((
            args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby, args.projected_nearby,
            args.container_nearby, args.stock_use, args.stock_prop_grip, args.stock_crate_ring, args.steam_cloud)):
        parser.error("Use the guest world-start checks without other gameplay fixtures")
    if ((args.resident_cabinet_archive is None) != (args.resident_cabinet_checkpoint is None)
            or args.resident_cabinet_candidate_sha256 and not args.resident_cabinet_archive):
        parser.error("Use --resident-cabinet-archive and --resident-cabinet-checkpoint together")
    if args.resident_cabinet_archive and any((args.stock_drop, args.stock_split, args.stock_equip, args.stock_nearby,
                                              args.projected_nearby, args.container_nearby, args.stock_use, args.steam_cloud,
                                              args.stock_prop_grip, args.stock_crate_ring, args.guest_world_start,
                                              args.guest_world_start_travel, args.guest_ui_memory,
                                              args.compact_crate_status, args.compact_world_view, args.guest_shop_lease,
                                              args.second_legacy_work)):
        parser.error("Use --resident-cabinet-archive with two fresh private replicas and no other gameplay fixtures")
    cabinet_archive = args.resident_cabinet_archive.resolve(strict=True) if args.resident_cabinet_archive else None
    cabinet_checkpoint = args.resident_cabinet_checkpoint.resolve(strict=True) if args.resident_cabinet_checkpoint else None
    legacy = args.second_legacy_work.resolve(strict=True) if args.second_legacy_work else None
    asyncio.run(verify(args.work.resolve(strict=True), args.first_game.resolve(strict=True),
                       args.second_game.resolve(strict=True), args.vanilla_saved.resolve(strict=True), legacy,
                       stock_drop=args.stock_drop, stock_split=args.stock_split, stock_equip=args.stock_equip,
                       stock_unequip=args.stock_unequip,
                       stock_nearby=args.stock_nearby, projected_nearby=args.projected_nearby,
                       container_nearby=args.container_nearby, steam_cloud=args.steam_cloud, memory_bridge=args.memory_bridge,
                       stock_use=args.stock_use, stock_food=args.stock_food, stock_campfire=args.stock_campfire,
                       stock_pause_exit=args.stock_pause_exit,
                       stock_cooked_meal=args.stock_cooked_meal,
                       stock_personal_consumable=args.stock_personal_consumable,
                       stock_wipe_chooser=args.stock_wipe_chooser,
                       stock_npc_override=args.stock_npc_override, stock_board_offers=args.stock_board_offers,
                       stock_quarry_photo=args.stock_quarry_photo,
                       stock_npc_rivalry=args.stock_npc_rivalry,
                       stock_host_corpse_carry=args.stock_host_corpse_carry,
                       stock_flashlights=args.stock_flashlights,
                       stock_simmi_jammers=args.stock_simmi_jammers,
                       stock_weather_travel=args.stock_weather_travel,
                       crossroads_profile=args.crossroads_profile, stock_crossroads_door=args.stock_crossroads_door,
                       stock_dock_gate=args.stock_dock_gate,
                       stock_lightning=args.stock_lightning,
                       stock_storm_ambience=args.stock_storm_ambience,
                       stock_melee_family=args.stock_melee_family,
                       stock_melee_edges=args.stock_melee_edges,
                       stock_warehouse_switch=args.stock_warehouse_switch,
                       stock_warehouse_board=args.stock_warehouse_board, stock_bottle=args.stock_bottle,
                       stock_roaming_barter=args.stock_roaming_barter, stock_critters=args.stock_critters,
                       stock_placed_item=args.stock_placed_item, stock_mushrooms=args.stock_mushrooms,
                       stock_eye_stacks=args.stock_eye_stacks, stock_lingering_teleport=args.stock_lingering_teleport,
                       stock_acid_pool=args.stock_acid_pool,
                       stock_throwables=args.stock_throwables,
                       memory_status=args.memory_status, stock_prop_grip=args.stock_prop_grip,
                       stock_host_prop_observer=args.stock_host_prop_observer, resident_weapon_cues=args.resident_weapon_cues,
                       resident_held_queue=args.resident_held_queue, resident_stale_quick_slot=args.resident_stale_quick_slot,
                       rusty_pistol=args.rusty_pistol, tutorial_dock_combat=args.tutorial_dock_combat,
                       tutorial_dock_melee=args.tutorial_dock_melee, tutorial_dock_pursuit=args.tutorial_dock_pursuit,
                       tutorial_dock_held_mask=args.tutorial_dock_held_mask, tutorial_dock_gait=args.tutorial_dock_gait,
                       tutorial_dock_shot_view=args.tutorial_dock_shot_view,
                       tutorial_dock_retaliation=args.tutorial_dock_retaliation, stock_bandage_revisit=args.stock_bandage_revisit,
                       stock_shovel=args.stock_shovel, stock_buried_placements=args.stock_buried_placements,
                       stock_shovel_loot=args.stock_shovel_loot,
                       stock_shovel_quest=args.stock_shovel_quest, stock_shovel_host_quest=args.stock_shovel_host_quest,
                       stock_take_ammo=args.stock_take_ammo, stock_shotguns=args.stock_shotguns,
                       stock_impact_cues=args.stock_impact_cues,
                       stock_avatar_tail=args.stock_avatar_tail, stock_avatar_binoculars=args.stock_avatar_binoculars,
                       stock_interaction_feedback=args.stock_interaction_feedback,
                       stock_loki_control=args.stock_loki_control, stock_loki_guest=args.stock_loki_guest,
                       stock_alternate_ammo=args.stock_alternate_ammo,
                       stock_light_ar=args.stock_light_ar,
                       stock_fire_mode_reopen=args.stock_fire_mode_reopen,
                       stock_crate_ring=args.stock_crate_ring, guest_world_start=args.guest_world_start,
                       guest_world_start_travel=args.guest_world_start_travel, guest_ui_memory=args.guest_ui_memory,
                       nonmirrored_startup=args.nonmirrored_startup,
                       compact_crate_status=args.compact_crate_status, compact_world_view=args.compact_world_view,
                       guest_shop_lease=args.guest_shop_lease, guest_stash_memory=args.guest_stash_memory,
                       guest_stash_large_memory=args.guest_stash_large_memory,
                       zone_lease_preflight=args.zone_lease_preflight,
                       resident_cabinet_archive=cabinet_archive, resident_cabinet_checkpoint=cabinet_checkpoint,
                       resident_cabinet_candidate_sha256=args.resident_cabinet_candidate_sha256))
