"""Scope exact stock weapon operations to a connected guest's native save."""

import re
import time
import uuid

from . import guest_weapon_profiles, native_guest_melee, native_inventory, party

GUEST_ACTIONS = ("native-guest-weapon-inspect", "native-guest-weapon-equip", "native-guest-weapon-fire",
                 "native-guest-weapon-reload")
GUID = re.compile(r"[0-9A-F]{32}\Z")
SELECT_EQUIPMENT = "native-guest-weapon-select"
READBACK_STATES = {"backpack", "equipped", "stored_equipment"}
CAMERA_READBACK_STATES = {"camera_backpack", "camera_stored_equipment", "camera_equipped"}
CAMERA_PATH = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
BLOWTORCH_PATH = "/Game/Classes/Equipment/Eqp_BlowTorch.Eqp_BlowTorch_C"
BLOWTORCH_STATES = {"blowtorch_backpack", "blowtorch_stored_equipment", "blowtorch_equipped"}
CROWBAR_PATH = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Crowbar.MeleeWep_Crowbar_C"
CROWBAR_STATES = {"crowbar_backpack", "crowbar_stored_equipment", "crowbar_equipped"}
OLD_PIPE = "/Game/Classes/Weapons/MeleeWeapons/MeleeWep_OldPipe.MeleeWep_OldPipe_C"
OLD_PIPE_STATES = {"old_pipe_backpack", "old_pipe_stored_equipment", "old_pipe_equipped"}
FIRE_CLASSES = {firearm.kind: firearm.path for firearm in guest_weapon_profiles.FIREARMS}
CLASS_PATHS = {*FIRE_CLASSES.values(), CAMERA_PATH, OLD_PIPE, BLOWTORCH_PATH, CROWBAR_PATH}
PRIMARY_KINDS = frozenset((*FIRE_CLASSES, "camera", "blowtorch", "crowbar", native_guest_melee.KIND))
SEED_KINDS = frozenset(firearm.kind for firearm in guest_weapon_profiles.FIREARMS if firearm.headless_seed) | {
    "blowtorch", "crowbar", "smg-standard-full", "stock-flare", "stock-flare-blue", "stock-flare-yellow",
}
PROJECTILES_PER_ROUND = {
    firearm.kind: firearm.magazine.ammo.projectile_count
    for firearm in guest_weapon_profiles.FIREARMS
    if firearm.magazine
}


async def select_equipment(host, bridge, character: str, target: str | None) -> dict:
    area = host.last_game.get("map")
    epoch, _, guid = target.partition("@") if isinstance(target, str) else ("", "", "")
    if not GUID.fullmatch(guid) or epoch != host.zone_epoch:
        raise party.RuleError("native_guest_selection_expired")
    if (not host.native_guest or not host.native_zones or host.native_profile is None or host.stopping
            or host.transitioning or host.native_checkpoint_unsafe or host.last_game.get("state") != "hosting"
            or time.monotonic() - host.last_seen > 3 or host.party["wipe"]
            or character in host.party["pending_spawns"]
            or host.party["players"].get(character, {}).get("downed", True)):
        raise party.RuleError("native_guest_selection_unavailable")
    scope = host.native_recipients().get(character, {})
    if not scope.get("connected") or not (pawn := scope.get("pawn")):
        raise party.RuleError("native_guest_selection_unavailable")

    await native_inventory.retain_guest_save(host, bridge, character, pawn)
    host.native_checkpoint_unsafe = True
    try:
        snapshot = await bridge.command(SELECT_EQUIPMENT, pawn, guid, timeout=3)
        proof = snapshot.get("native_guest_weapon", {})
        if ((snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"),
             proof.get("action"), proof.get("guid")) != ("hosting", area, area, pawn, "select", guid)):
            raise RuntimeError("Guest equipment selection did not verify the enrolled player's stock slot")
        if proof.get("ok") is not True:
            refusal = proof.get("status", "")
            if refusal.startswith("native_guest_selection_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(refusal)
            raise RuntimeError(refusal or "mutation_unverified_guest_equipment_selection")
        if (proof.get("status") != "equipped" or type(loaded := proof.get("stored_ammo")) is not int
                or loaded < -1 or loaded != proof.get("actor_ammo")):
            raise RuntimeError("Guest equipment selection did not preserve the stock item")
        host.last_game, host.last_seen = snapshot, time.monotonic()
        host.native_checkpoint_unsafe = False
        return {"native_guest_weapon": proof}
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True


async def service_equipment_selection(client, active: bool) -> None:
    from . import native_gold

    proof = client.last_game.get("native_guest_equipment_selection", {})
    sequence, area, pawn, guid = (proof.get(key) for key in ("sequence", "map", "pawn", "guid"))
    if (not active or client.stopping or not client.host_session or not client.zone_epoch
            or type(sequence) is not int or not client.last_equipment_selection < sequence < 2**53
            or area != client.last_game.get("map", "") or (area, pawn) !=
               (client.expected_zone, client.last_game.get("pawns", {}).get("local", {}).get("name"))
            or not native_gold.valid_guid(guid)):
        return
    client.last_equipment_selection = sequence
    operation = uuid.uuid5(client.bridge.input_namespace, f"stock-equipment:{client.zone_epoch}:{sequence}").hex
    try:
        result = await client.gameplay_action(SELECT_EQUIPMENT, operation, f"{client.zone_epoch}@{guid}")
    except (OSError, RuntimeError, ValueError) as error:
        client.control.event("native_guest_equipment_selection_rejected", sequence=sequence, reason=str(error))
    else:
        client.control.event("native_guest_equipment_selected", sequence=sequence, **result)


async def perform(host, bridge, character: str, operation_id: str, action: str, guid: str, *, seed_kind=None,
                  bandit_fixture: str | None = None) -> dict:
    fixture = bandit_fixture is not None or action in ("native-guest-weapon-seed", "native-guest-weapon-seed-ammo")
    if (not host.native_guest or (fixture and not host.fixtures) or host.native_profile is None
            or host.transitioning or host.stopping or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen > 3 or host.last_game.get("state") != "hosting"):
        raise party.RuleError("native_guest_weapon_unavailable")
    implicit = action == "native-guest-weapon-reload" and guid == "equipped"
    if not implicit and (not isinstance(guid, str) or not GUID.fullmatch(guid)):
        raise party.RuleError("invalid_weapon_guid")
    if not isinstance(operation_id, str) or not party.REQUEST_ID.fullmatch(operation_id):
        raise party.RuleError("invalid_operation_id")
    if seed_kind is not None and (action != "native-guest-weapon-seed" or not isinstance(seed_kind, str) or seed_kind not in SEED_KINDS):
        raise party.RuleError("invalid_guest_weapon_fixture_kind")

    scope = host.native_recipients().get(character)
    if not scope or not scope["connected"]:
        raise party.RuleError("guest_not_active")
    operation = action.removeprefix("native-guest-weapon-")
    extra = (seed_kind,) if seed_kind is not None else ()
    fingerprint = [action, guid, bandit_fixture or (extra[0] if extra else None)]
    mutates = operation != "inspect"
    if mutates:
        for receipt in host.party["receipts"].get(character, []):
            if receipt["id"] == operation_id:
                if receipt["request"] != fingerprint:
                    raise party.RuleError("operation_id_conflict")
                return receipt["result"]
        if host.party["players"].get(character, {}).get("downed") or host.party["wipe"] is not None:
            raise party.RuleError("downed")
        if character in host.party["pending_spawns"]:
            raise party.RuleError("respawn_in_progress")

    source, pawn = host.last_game["map"], scope["pawn"]
    deferred = mutates and (not fixture or operation == "fire") and await native_inventory.retain_guest_save(
        host, bridge, character, pawn,
    )
    if mutates:
        host.native_checkpoint_unsafe = True
    try:
        if bandit_fixture is None:
            try:
                snapshot = await bridge.command(action, pawn, guid, *extra, timeout=5)
            except RuntimeError as error:
                if str(error) != "guest_weapon_refused_detached_save":
                    raise
                if mutates:
                    host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
        else:
            try:
                snapshot = await bridge.command("native-bandit-shot-fixture", bandit_fixture, pawn, guid, timeout=5)
            except RuntimeError as error:
                reason = str(error)
                if reason.startswith(("native_bandit_shot_", "guest_weapon_", "Stock Bandit aim could not be verified")) or reason in {
                    "downed", "The exact enrolled guest or living Bandit is unavailable",
                    "The verified standing guest, stock equipped weapon or living Bandit is unavailable",
                    "The stock Bandit aim geometry is unavailable", "The stock Bandit aim component was unavailable",
                    "No exact stock Bandit line of fire from the qualified staging points",
                }:
                    host.native_checkpoint_unsafe = False
                    raise party.RuleError(reason) from error
                raise
        proof = snapshot.get("native_guest_weapon", {})
        observed_guid = proof.get("guid")
        qualified_guid = (observed_guid == guid if not implicit else
                          isinstance(observed_guid, str) and (
                              GUID.fullmatch(observed_guid) is not None
                              or (observed_guid == guid and proof.get("ok") is False)))
        if (snapshot.get("state") != "hosting" or snapshot.get("map") != source
                or (proof.get("map"), proof.get("action"), proof.get("pawn")) != (source, operation, pawn)
                or not qualified_guid):
            raise RuntimeError("Native guest weapon response did not match the authenticated pawn")
        if proof.get("ok") is not True:
            reason = proof.get("status") or "native_guest_weapon_denied"
            if mutates and reason.startswith("mutation_unverified_"):
                raise RuntimeError(reason)
            if mutates:
                host.native_checkpoint_unsafe = False
            raise party.RuleError(reason)

        actor, stored = proof.get("actor_ammo"), proof.get("stored_ammo")
        camera = proof.get("status") in CAMERA_READBACK_STATES
        old_pipe = proof.get("status") in OLD_PIPE_STATES
        blowtorch = proof.get("status") in BLOWTORCH_STATES
        crowbar = proof.get("status") in CROWBAR_STATES
        if camera:
            status = proof["status"]
            if ((operation != "inspect" and (operation != "equip" or status != "camera_equipped"))
                    or type(stored) is not int or stored not in (-1, 0)
                    or type(actor) is not int or actor != (stored if status == "camera_equipped" else -1)):
                raise RuntimeError("Native guest stock camera equipment did not verify")
        elif old_pipe:
            status = proof["status"]
            if ((operation != "inspect" and (operation != "equip" or status != "old_pipe_equipped"))
                    or actor != -1 or stored != -1 or source not in native_guest_melee.BY_MAP):
                raise RuntimeError("Native guest stock Old Pipe equipment did not verify")
        elif blowtorch:
            status = proof["status"]
            if (operation not in {"inspect", "equip", "seed"} or operation == "equip" and status != "blowtorch_equipped"
                    or operation == "seed" and seed_kind != "blowtorch" or type(stored) is not int or not 0 <= stored <= 3
                    or type(actor) is not int or actor != (stored if status == "blowtorch_equipped" else -1)):
                raise RuntimeError("Native guest stock blowtorch and fuel did not verify")
        elif crowbar:
            if (operation not in {"inspect", "equip", "seed"}
                    or operation == "equip" and proof["status"] != "crowbar_equipped"
                    or operation == "seed" and seed_kind != "crowbar" or actor != -1 or stored != -1):
                raise RuntimeError("Native guest stock crowbar did not verify")
        elif operation == "seed-ammo":
            if proof.get("status") not in ("seeded", "present"):
                raise RuntimeError("The stock guest reserve fixture did not verify the exact ammo GUID")
        elif (proof.get("status") not in READBACK_STATES or type(stored) is not int or stored < 0
              or type(actor) is not int or actor != (stored if proof["status"] == "equipped" else -1)):
            raise RuntimeError("Native guest stock weapon ammunition did not verify")
        if operation == "fire":
            equipped = snapshot.get("native_guest_equipped_weapons", {}).get(pawn, {})
            shot_kind = equipped.get("kind") if equipped.get("guid") == guid else None
            expected_projectiles = PROJECTILES_PER_ROUND.get(shot_kind, 1)
            projectile_count = proof.get("projectiles")
            if (proof["status"] != "equipped" or type(proof.get("before")) is not int
                    or proof.get("after") != stored or proof["before"] - stored != 1
                    or type(projectile_count) is not int or projectile_count != expected_projectiles):
                raise RuntimeError("The stock shot did not verify its guest-owned projectile count and one round debit")
        if operation == "reload":
            before, reserve_before, reserve_after = (proof.get(key) for key in ("before", "reserve_before", "reserve_after"))
            if (proof["status"] != "equipped" or proof.get("after") != stored
                    or any(type(amount) is not int for amount in (before, reserve_before, reserve_after))
                    or not 0 <= before < stored or not 0 <= reserve_after < reserve_before
                    or stored - before != reserve_before - reserve_after):
                raise RuntimeError("Stock reserve ammunition did not move exactly into the guest magazine")

        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {"native_guest_weapon": dict(proof, kind="camera", class_path=CAMERA_PATH) if camera else proof}
        if old_pipe:
            result["native_guest_weapon"] = dict(proof, kind=native_guest_melee.KIND, class_path=OLD_PIPE)
        if blowtorch:
            result["native_guest_weapon"] = dict(proof, kind="blowtorch", class_path=BLOWTORCH_PATH)
        if crowbar:
            result["native_guest_weapon"] = dict(proof, kind="crowbar", class_path=CROWBAR_PATH)
        if bandit_fixture is not None:
            aim = snapshot.get("native_bandit_aim", {})
            if (aim.get("map"), aim.get("bandit"), aim.get("pawn")) != (source, bandit_fixture, pawn):
                raise RuntimeError("The stock Bandit shot did not verify the exact aim receipt")
            result["native_bandit_aim"] = aim
        if mutates:
            candidate = party.load_party(host.party)
            receipts = candidate["receipts"].setdefault(character, [])
            receipts.append({"id": operation_id, "request": fingerprint, "result": result})
            del receipts[:-party.RECENT_ACTIONS]
            if not deferred:
                await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}))
            host.party = candidate
            host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
