"""Route the stock host inventory's drop action through the durable drop ledger."""

import contextlib
import re
import time
import uuid

from . import native_guest_drop, native_guest_drop_record as record, party
from .native_gold import valid_guid


CAPABILITY = "native_host_stack_drop_menu_capability"
SCOPE_ROW = "native-host-stack-drop-scope"
INTENT_ROW = "native-host-stack-drop-intent"
SCOPE = "native_host_stack_drop_scope"
INTENT = "native_host_stack_drop_intent"
BIND = "native-host-stack-drop-bind"
FINISH = "native-host-stack-drop-finish"
FIXTURE = "native-host-stack-drop-menu-fixture"
CLOSE_FIXTURE = "native-host-stack-drop-menu-close-fixture"
_ENTRIES = {"ctrl_click", "context_menu"}


def parse(fields: list[str], phase: str, area: str) -> dict:
    tag = fields[0]
    if (phase != "hosting" or tag not in (SCOPE_ROW, INTENT_ROW) or len(fields) != (4 if tag == SCOPE_ROW else 8)
            or fields[1] != area or not record.pawn_matches(area, fields[2]) or not record.valid_id(fields[3])):
        raise ValueError("Invalid stock host drop scope")
    row = dict(map=area, pawn=fields[2], epoch=fields[3])
    if tag == INTENT_ROW:
        number, guid, count, entry = fields[4:]
        if (not re.fullmatch(r"[1-9][0-9]{0,15}", number) or int(number) >= 2**53 or not valid_guid(guid)
                or not re.fullmatch(r"[1-9][0-9]{0,2}", count) or int(count) > record.MAX_QUANTITY
                or entry not in _ENTRIES):
            raise ValueError("Invalid stock host drop intent")
        row.update(sequence=int(number), guid=guid, count=int(count), entry=entry)
    return row


def _scope(host):
    game = host.last_game
    area, pawn, epoch = game.get("map"), game.get("pawns", {}).get("local", {}).get("name"), host.zone_epoch
    if (host.native_guest and host.native_zones and not host.stopping and not host.transitioning
            and not host.native_checkpoint_unsafe and time.monotonic() - host.last_seen < 2
            and game.get("state") == "hosting" and game.get(CAPABILITY) == 1
            and record.pawn_matches(area, pawn) and record.valid_id(epoch)):
        return dict(map=area, pawn=pawn, epoch=epoch)


class HostDropInput:
    def __init__(self):
        self.processed = None
        self.retry_at = 0.0

    async def poll(self, host, bridge) -> None:
        scope = _scope(host)
        if scope is None:
            return
        if host.last_game.get(SCOPE) != scope:
            now = time.monotonic()
            if now >= self.retry_at:
                self.retry_at = now + 1.0
                with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                    snapshot = await bridge.command(BIND, scope["epoch"], scope["pawn"], scope["map"], timeout=2)
                    if snapshot.get(SCOPE) == scope:
                        host.last_game, host.last_seen = snapshot, time.monotonic()
            return
        intent = host.last_game.get(INTENT)
        if not intent or any(intent[key] != value for key, value in scope.items()):
            return
        stroke = scope["epoch"], intent["sequence"]
        if self.processed == stroke:
            return
        self.processed = stroke
        operation = uuid.uuid5(uuid.NAMESPACE_OID,
                               f"{host.control.generation}:{stroke[0]}:host-stack-drop:{stroke[1]}").hex
        result, error = {}, None
        try:
            async with host.smg_holds.lock if host.smg_holds is not None else contextlib.nullcontext():
                result = await native_guest_drop.perform(host, bridge, "host", native_guest_drop.DROP,
                                                         f"{intent['guid']}|{intent['count']}", operation)
        except (OSError, RuntimeError, ValueError) as failure:
            error = str(failure)
        refresh = None
        if not host.native_checkpoint_unsafe and _scope(host) == scope:
            with contextlib.suppress(OSError, RuntimeError, TimeoutError):
                snapshot = await bridge.command(FINISH, stroke[0], str(stroke[1]), timeout=2)
                host.last_game, host.last_seen = snapshot, time.monotonic()
                refresh = snapshot.get("ack", {}).get("message")
        host.control.event("native_host_stock_drop", operation_id=operation, entry=intent["entry"],
                           refresh=refresh, error=error, **result)


async def fixture(host, bridge, action: str, target: str) -> dict:
    if not host.fixtures or _scope(host) is None:
        raise party.RuleError("host_stock_drop_fixture_unavailable")
    fields = target.split("|") if isinstance(target, str) else []
    if (not fields or not valid_guid(fields[0]) or (action == CLOSE_FIXTURE and len(fields) != 1)
            or (action == FIXTURE and (len(fields) != 3 or not re.fullmatch(r"[1-9][0-9]{0,2}", fields[1])
                                       or int(fields[1]) > record.MAX_QUANTITY or fields[2] not in _ENTRIES))):
        raise party.RuleError("host_stock_drop_fixture_arguments_unavailable")
    snapshot = await bridge.command(action, *fields, timeout=5)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    return {"native_host_stack_drop_fixture": True}
