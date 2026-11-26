"""Relay an exact stock gear grant from its owning guest to the host."""

import asyncio
import contextlib
from hashlib import sha256
import re
import time
import uuid

from . import native_inventory, party
from .native_gold import valid_guid


READ = "native-inventory-grant-read"
ACK = "native-inventory-grant-ack"
APPLY = "native-inventory-grant-apply"
REPORT = "native-inventory-grant-report"
READY = "native_inventory_grant_ready"
RESULT = "native_inventory_grant"
MAX_ITEM_BYTES = 8192
_CLASS = re.compile(r"/Game/[A-Za-z0-9_./-]{1,500}_C")
_TERMINAL = {
    "native_inventory_grant_refused_class",
    "native_inventory_grant_refused_guid",
    "native_inventory_grant_refused_serialization",
    "native_inventory_grant_refused_encoding",
}


def parse_ready(fields):
    _, sequence = fields
    if not re.fullmatch(r"[0-9]{1,16}", sequence) or not 0 < int(sequence) < 2**53:
        raise ValueError("Invalid native inventory grant sequence")
    return int(sequence)


def parse_item(value):
    guid, item_class, text = value.split("@", 2)
    if (not valid_guid(guid) or not _CLASS.fullmatch(item_class) or ".." in item_class
            or not text.startswith("(") or not text.endswith(")") or any(ord(c) < 32 for c in text)
            or len(text.encode("utf-8")) > MAX_ITEM_BYTES):
        raise ValueError("Invalid typed inventory grant")
    return guid, item_class, text


async def accept(host, bridge, character, target, operation):
    try:
        epoch, value = target.split("@", 1)
        guid, item_class, text = parse_item(value)
        if not party.REQUEST_ID.fullmatch(operation):
            raise ValueError("Invalid inventory grant operation")
    except (AttributeError, TypeError, ValueError) as error:
        raise party.RuleError("invalid_native_inventory_grant") from error
    game = host.last_game
    owner = host.native_recipients().get(character, {})
    pawn = owner.get("pawn")
    if (bridge.local_bridge is None or epoch != host.zone_epoch or not pawn or not owner.get("connected")
            or not host.native_guest or not host.native_profile or game.get("state") != "hosting"
            or host.stopping or host.transitioning or host.native_checkpoint_unsafe or host.party.get("wipe")):
        raise party.RuleError("native_inventory_grant_scope_changed")
    identity = character, guid
    fingerprint = sha256(value.encode("utf-8")).digest()
    if prior := host.guest_inventory_grants.get(identity):
        if prior[0] != fingerprint:
            raise party.RuleError("native_inventory_grant_refused_guid")
        return {RESULT: prior[1]}
    if not await native_inventory.retain_guest_save(host, bridge, character, pawn):
        raise party.RuleError("native_inventory_grant_scope_changed")

    frame = await bridge.command(APPLY, pawn, guid, item_class, text, timeout=3)
    state = frame.get("ack", {}).get("message")
    if (frame.get("state"), frame.get("map")) != ("hosting", game.get("map")) or state not in ("added", "present"):
        raise RuntimeError("The detached stock inventory grant did not verify")
    host.last_game, host.last_seen = frame, time.monotonic()
    proof = {"owner": character, "guid": guid, "class_path": item_class, "status": state}
    host.guest_inventory_grants[identity] = fingerprint, proof
    host.control.event("native_guest_inventory_grant_settled", operation_id=operation, **proof)
    return {RESULT: proof}


class GuestGrants:
    def __init__(self):
        self.request = None
        self.retry_after = 0.0

    async def close(self):
        if self.request is not None:
            self.request.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.request
            self.request = None

    async def send(self, client, scope, sequence):
        try:
            read = await client.bridge.command(READ, str(sequence), timeout=2, update_status=False)
            value = read["ack"]["message"]
            guid, item_class, _ = parse_item(value)
            if client.stock_interaction_scope() != scope:
                return
            session, epoch, _, _ = scope
            operation = uuid.uuid5(client.bridge.input_namespace, f"{session}:grant:{sequence}").hex
            accepted = False
            try:
                result = await client.gameplay_action(REPORT, operation, f"{epoch}@{value}")
            except ValueError as error:
                if str(error) not in _TERMINAL:
                    raise
                client.control.event("native_guest_inventory_grant_rejected", sequence=sequence, reason=str(error))
            else:
                proof = result.get(RESULT, {})
                if (proof.get("owner"), proof.get("guid"), proof.get("class_path")) != (
                        client.invite["character_id"], guid, item_class):
                    raise RuntimeError("The native stock grant reply did not match")
                client.control.event("native_guest_inventory_grant", operation_id=operation, **proof)
                accepted = True
            if client.stock_interaction_scope() == scope:
                await client.guest_ui.close(preserve_stock=True)
                await client.bridge.command(ACK, str(sequence), "1" if accepted else "0", timeout=2)
                client.guest_ui.queue_stock_inventory(client)
        except (OSError, ValueError, RuntimeError) as error:
            self.retry_after = time.monotonic() + .5
            client.control.event("native_guest_inventory_grant_deferred", sequence=sequence, reason=str(error))

    async def sync(self, client):
        if self.request is not None:
            if not self.request.done():
                return
            await self.request
            self.request = None
        if client.bridge.local_bridge is None or time.monotonic() < self.retry_after:
            return
        scope = client.stock_interaction_scope()
        sequence = client.last_game.get(READY)
        if scope and type(sequence) is int:
            self.request = asyncio.create_task(self.send(client, scope, sequence))
