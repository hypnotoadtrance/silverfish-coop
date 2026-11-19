"""Scope exact stock weapon operations to a connected guest's native save."""

import re
import time

from . import party

GUEST_ACTIONS = ("native-guest-weapon-inspect", "native-guest-weapon-equip", "native-guest-weapon-fire",
                 "native-guest-weapon-reload")
GUID = re.compile(r"[0-9A-F]{32}\Z")
READBACK_STATES = {"backpack", "equipped", "stored_equipment"}
CAMERA_READBACK_STATES = {"camera_backpack", "camera_stored_equipment", "camera_equipped"}
CAMERA_PATH = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
FIRE_CLASSES = {
    "smg": "/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C",
    "smg-standard": "/Game/Classes/Weapons/Wep_SMG_01.Wep_SMG_01_C",
    "light-pistol": "/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C",
    "light-pistol-suppressed": "/Game/Classes/Weapons/Wep_LightPistol_Suppressed.Wep_LightPistol_Suppressed_C",
    "nagant-scoped": "/Game/Classes/Weapons/Wep_Nagant_Scoped.Wep_Nagant_Scoped_C",
}
CLASS_PATHS = {*FIRE_CLASSES.values(), CAMERA_PATH}
PRIMARY_KINDS = frozenset((*FIRE_CLASSES, "camera"))
SEED_KINDS = frozenset({"light-pistol", "light-pistol-suppressed", "smg-standard"})


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
    if mutates:
        host.native_checkpoint_unsafe = True
    try:
        if bandit_fixture is None:
            snapshot = await bridge.command(action, pawn, guid, *extra, timeout=5)
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
        if camera:
            status = proof["status"]
            if ((operation != "inspect" and (operation != "equip" or status != "camera_equipped"))
                    or type(stored) is not int or stored not in (-1, 0)
                    or type(actor) is not int or actor != (stored if status == "camera_equipped" else -1)):
                raise RuntimeError("Native guest stock camera equipment did not verify")
        elif operation == "seed-ammo":
            if proof.get("status") not in ("seeded", "present"):
                raise RuntimeError("The stock guest reserve fixture did not verify the exact ammo GUID")
        elif (proof.get("status") not in READBACK_STATES or type(stored) is not int or stored < 0
              or type(actor) is not int or actor != (stored if proof["status"] == "equipped" else -1)):
            raise RuntimeError("Native guest stock weapon ammunition did not verify")
        if operation == "fire" and (proof["status"] != "equipped" or type(proof.get("before")) is not int
                                    or proof.get("after") != stored or proof["before"] - stored != 1
                                    or proof.get("projectiles") != 1):
            raise RuntimeError("The stock shot did not produce one verified guest-owned projectile and ammo debit")
        if operation == "reload":
            before, reserve_before, reserve_after = (proof.get(key) for key in ("before", "reserve_before", "reserve_after"))
            if (proof["status"] != "equipped" or proof.get("after") != stored
                    or any(type(amount) is not int for amount in (before, reserve_before, reserve_after))
                    or not 0 <= before < stored or not 0 <= reserve_after < reserve_before
                    or stored - before != reserve_before - reserve_after):
                raise RuntimeError("Stock reserve ammunition did not move exactly into the guest magazine")

        host.last_game, host.last_seen = snapshot, time.monotonic()
        result = {"native_guest_weapon": dict(proof, kind="camera", class_path=CAMERA_PATH) if camera else proof}
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
            await host.checkpoint_native(bridge, candidate)
            host.party = candidate
            host.native_checkpoint_unsafe = False
        return result
    except (OSError, RuntimeError, ValueError):
        if host.native_checkpoint_unsafe:
            host.stopping = True
        raise
