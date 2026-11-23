"""Commit a stock flare or bolt thrown from a guest's equipped stack."""

import re
import time
import uuid

from . import native_gold, native_inventory, party


ACTION = "native-guest-flare-throw"
INPUT = "native-guest-flare-input"
PROOF = "native_guest_flare_throw"
INTENT = "native_guest_flare_intent"
CLASSES = {
    f"/Game/Classes/Equipment/{item}.{item}_C": projectile
    for item, projectile in (
        ("Eqp_Flare", "Flare_Projectile_BP_C"),
        ("Eqp_Flare_Blue", "Blue_Flare_Projectile_BP_C"),
        ("Eqp_Flare_Yellow", "Yellow_Flare_Projectile_BP_C"),
        ("Eqp_Bolt", "Bolt_Projectile_BP_C"),
    )
}


def parse(fields, phase, area):
    if len(fields) < 2 or not area or fields[1] != area:
        raise ValueError("The flare action belongs to a different map")
    if fields[0] == INPUT and phase == "connected" and len(fields) == 7:
        _, _, pawn, serial, guid, toss, item_class = fields
        sequence = int(serial)
        if not 0 < sequence < 2**53 or not native_gold.valid_guid(guid) or toss not in ("0", "1") or item_class not in CLASSES:
            raise ValueError("Invalid stock flare input")
        return dict(map=area, pawn=pawn, sequence=sequence, guid=guid, toss=toss, class_path=item_class)
    if fields[0] == ACTION and phase == "hosting" and len(fields) == 9:
        _, _, pawn, guid, toss, raw_before, raw_after, item_class, projectile = fields
        before, after = int(raw_before), int(raw_after)
        projectile_class = CLASSES.get(item_class)
        if (not native_gold.valid_guid(guid) or toss not in ("0", "1") or not 1 <= before < 2**31 or after != before - 1
                or not projectile_class or not re.fullmatch(
                    re.escape(f"{area}.{area.rsplit('/', 1)[-1]}:PersistentLevel.{projectile_class}_") + r"[0-9]+",
                    projectile)):
            raise ValueError("Invalid stock flare throw receipt")
        return dict(map=area, pawn=pawn, guid=guid, toss=toss, before=before, after=after,
                    class_path=item_class, projectile=projectile)
    raise ValueError("Invalid stock flare scope")


async def perform(host, bridge, character, selection, operation):
    fields = selection.split("@") if isinstance(selection, str) and len(selection) <= 70 else []
    if (len(fields) != 3 or not native_gold.valid_guid(fields[1]) or fields[2] not in ("0", "1")
            or not isinstance(operation, str) or not party.REQUEST_ID.fullmatch(operation)):
        raise party.RuleError("native_guest_flare_exact_slot_required")
    epoch, guid, toss = fields
    if (not host.native_guest or not host.native_profile or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party.get("wipe") or epoch != host.zone_epoch
            or host.last_game.get("state") != "hosting" or time.monotonic() - host.last_seen > 3
            or host.party["players"].get(character, {}).get("downed", True)
            or character in host.party["pending_spawns"]):
        raise party.RuleError("native_guest_flare_unavailable")
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn") if owner.get("connected") else None
    if not pawn:
        raise party.RuleError("native_guest_flare_refused_owner")
    fingerprint = [ACTION, selection, None]
    for receipt in host.party["receipts"].get(character, ()):
        if receipt["id"] == operation:
            if receipt["request"] != fingerprint:
                raise party.RuleError("operation_id_conflict")
            return receipt["result"]
    area = host.last_game.get("map")
    deferred = await native_inventory.retain_guest_save(host, bridge, character, pawn)
    host.native_checkpoint_unsafe = True
    try:
        try:
            snapshot = await bridge.command(ACTION, pawn, guid, toss, timeout=4)
        except RuntimeError as error:
            if str(error).startswith("native_guest_flare_refused_"):
                host.native_checkpoint_unsafe = False
                raise party.RuleError(str(error)) from error
            raise
        proof = snapshot.get(PROOF, {})
        if (snapshot.get("state"), snapshot.get("map"), proof.get("map"), proof.get("pawn"), proof.get("guid"),
                proof.get("toss")) != ("hosting", area, area, pawn, guid, toss):
            raise RuntimeError("The stock throw did not verify the exact guest slot")
        result = {PROOF: dict(proof, owner=character)}
        candidate = party.load_party(host.party)
        receipts = candidate["receipts"].setdefault(character, [])
        receipts.append({"id": operation, "request": fingerprint, "result": result})
        del receipts[:-party.RECENT_ACTIONS]
        host.last_game, host.last_seen = snapshot, time.monotonic()
        revision = None
        if not deferred:
            revision = await host.checkpoint_native(bridge, candidate, require_guest_frames=frozenset({character}),
                                                    typed_inventory=True)
        host.party = candidate
        host.native_checkpoint_unsafe = False
    finally:
        if host.native_checkpoint_unsafe:
            host.stopping = True
    event = "native_guest_flare_accepted" if deferred else "native_guest_flare_committed"
    host.control.event(event, operation_id=operation, revision=revision, **result[PROOF])
    return result


async def service_input(client, active):
    proof = client.last_game.get(INTENT, {})
    sequence = proof.get("sequence")
    if (not active or client.stopping or not client.host_session or not client.zone_epoch
            or type(sequence) is not int or not client.last_flare_intent < sequence < 2**53
            or proof.get("map") != client.last_game.get("map")
            or proof.get("pawn") != client.last_game.get("pawns", {}).get("local", {}).get("name")):
        return
    client.last_flare_intent = sequence
    operation = uuid.uuid5(client.bridge.input_namespace, f"flare:{client.zone_epoch}:{sequence}").hex
    try:
        result = await client.gameplay_action(ACTION, operation, f"{client.zone_epoch}@{proof['guid']}@{proof['toss']}")
        client.control.event("native_guest_flare_input", operation_id=operation, **result)
    except (OSError, ValueError, RuntimeError) as error:
        client.control.event("native_guest_flare_input_rejected", operation_id=operation, reason=str(error))
        return
    receipt = result.get(PROOF, {})
    if receipt.get("guid") == proof["guid"] and receipt.get("class_path") == proof["class_path"]:
        try:
            client.last_game = await client.bridge.command("native-guest-flare-complete", str(sequence), proof["guid"],
                str(receipt["before"]), str(receipt["after"]), timeout=2)
        except (OSError, ValueError, RuntimeError) as error:
            client.control.event("native_guest_flare_local_charge_unverified", operation_id=operation, reason=str(error))
