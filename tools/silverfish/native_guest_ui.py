"""Mirror stock guest inventory and the host's Journal into the isolated resident view."""

import asyncio
import base64
import contextlib
from hashlib import sha256
import math
import re
import time
import uuid
import zlib

from . import party, runtime


ACTION = "native-guest-ui-read"
HEALTH = "native_guest_health"
HEALTH_PROJECT = "native-guest-health-project"
VITALS = "native_guest_vitals"
MAX_BYTES = runtime.GUEST_UI_MAX_BYTES
_DIGEST = re.compile(r"[0-9a-f]{64}")
_READY = re.compile(r"stock co-op inventory and Journal projected (\d+) (\d+) (\d+) (\d+) (\d+)")


def bounded_health(health, maximum):
    return (type(health) in (int, float) and type(maximum) in (int, float)
            and 1 <= maximum <= 1_000_000 and 0 <= health <= maximum)


def _vital_values(frame, maximum):
    values = {}
    for field, limit in (("food", 200), ("regen", maximum), ("stim", 1_000_000), ("rads", 1_000_000)):
        value = frame.get(field)
        if type(value) in (int, float) and 0 <= value <= limit:
            values[field] = value
    for field in ("fatigue", "intox"):
        value = frame.get(field)
        if type(value) is int and 0 <= value <= 100:
            values[field] = value
    sickness = frame.get("sickness")
    if type(sickness) is int and -2**31 <= sickness < 2**31:
        values["sickness"] = sickness
    return values


def _matches_vital(current, expected):
    return expected is None or type(current) in (int, float) and math.isclose(current, expected, abs_tol=.01)


def health_frame(game, epoch, owner, member, player):
    if game.get("state") != "hosting" or not member.get("connected") or player.get("character_id") != owner:
        return None
    pawn = game.get("pawns", {}).get(member["alias"], {})
    if pawn.get("name") != member["pawn"]:
        return None
    health = 0 if player.get("downed") else pawn.get("health")
    maximum = pawn.get("max_health")
    if not bounded_health(health, maximum) or health == 0 and not player.get("downed"):
        return None
    frame = {"map": game["map"], "epoch": epoch, "owner": owner, "health": health, "maximum": maximum}
    frame.update(_vital_values(game.get(VITALS, {}).get(member["pawn"], {}), maximum))
    return frame


def validate(contents: bytes, area: str, character: str) -> str:
    if (not isinstance(contents, bytes) or not 0 < len(contents) <= MAX_BYTES
            or contents.count(b"\n") > 128 or not contents.endswith(b"\n")
            or not contents.startswith(f"{area}\t{character}\n".encode("ascii"))):
        raise ValueError("The co-op view is stale or has invalid bounds")
    return contents.decode("ascii")


async def capture(host, bridge, character: str, known) -> dict:
    if known != "-" and (not isinstance(known, str) or not _DIGEST.fullmatch(known)):
        raise party.RuleError("invalid_coop_view_revision")
    owner = host.native_recipients().get(character)
    game = host.last_game
    if (not owner or not owner["connected"] or host.transitioning or host.stopping
            or host.native_checkpoint_unsafe or game.get("state") != "hosting"):
        raise party.RuleError("native_guest_ui_unavailable")
    path = None
    try:
        if game.get(runtime.GUEST_UI_MEMORY) is True and bridge.local_bridge is not None:
            snapshot, contents = await bridge.guest_ui_command(owner["pawn"], timeout=3)
        else:
            nonce = uuid.uuid4().hex
            path = bridge.directory / f"native-guest-ui-{nonce}.tsv"
            snapshot = await bridge.command(ACTION, owner["pawn"], nonce, timeout=3)
        if (snapshot.get("state"), snapshot.get("map"), snapshot.get("ack", {}).get("message")) != (
                "hosting", game["map"], "captured co-op guest view"):
            raise RuntimeError("The host did not verify the co-op view")
        if path is not None:
            with path.open("rb") as file:
                contents = file.read(MAX_BYTES + 1)
        validate(contents, game["map"], character)
    finally:
        if path is not None:
            with contextlib.suppress(FileNotFoundError, PermissionError):
                path.unlink()
    digest = sha256(contents).hexdigest()
    result = {"map": game["map"], "epoch": host.zone_epoch, "owner": character, "digest": digest}
    if digest != known:
        result["payload"] = base64.b64encode(zlib.compress(contents, level=1)).decode("ascii")
    return {"native_guest_ui": result}


class GuestView:
    def __init__(self):
        self.next_poll = 0.0
        self.request = None
        self.request_for_stock = None
        self.pending_stock_refresh = None
        self.identity = None
        self.digest = "-"
        self.last_error = None
        self.health_receipt = None
        self.health_checked = None
        self.health_error = None

    async def close(self):
        if self.request is not None:
            self.request.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError, ValueError, RuntimeError):
                await self.request
            self.request = None
        self.request_for_stock = None
        self.pending_stock_refresh = None

    @staticmethod
    def inventory_scope(client):
        game = client.last_game
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        identity = (client.host_session, client.zone_epoch, game.get("map"), pawn)
        if (game.get("state") == "connected" and all(identity) and identity[2] == client.expected_zone
                and game.get("save_scope") == uuid.UUID(client.invite["character_id"]).hex):
            return identity
        return None

    def stock_view(self, client):
        if client.last_game.get("native_inventory_view", {}).get("state") == "stock":
            return self.inventory_scope(client)
        return None

    def inventory_needs_refresh(self, client):
        scope = self.inventory_scope(client)
        return scope is not None and scope in (self.pending_stock_refresh, self.request_for_stock)

    def queue_stock_inventory(self, client):
        """Coalesce committed pickups, including those made before the inventory opens."""
        if scope := self.inventory_scope(client):
            self.pending_stock_refresh = scope
            self.next_poll = 0.0

    def stock_refresh_ready(self):
        return ((self.request_for_stock is not None and self.request is not None and self.request.done())
                or (self.pending_stock_refresh is not None and self.request is None))

    async def sync_health(self, client, active):
        game, frame = client.last_game, client.gameplay.get(HEALTH)
        if not active or game.get("state") != "connected":
            self.health_receipt = self.health_checked = self.health_error = None
            return
        if not isinstance(frame, dict) or time.monotonic() - client.gameplay_seen >= 2:
            return
        character = client.invite["character_id"]
        area, epoch, session = game.get("map"), client.zone_epoch, client.host_session
        if (not session or not epoch or area != client.expected_zone
                or (frame.get("owner"), frame.get("map"), frame.get("epoch")) != (character, area, epoch)):
            return
        token = uuid.UUID(character).hex
        pawn = game.get("pawns", {}).get("local", {})
        name = pawn.get("name")
        guard = game.get("native_client_damage", {})
        health, maximum = frame.get("health"), frame.get("maximum")
        if (game.get("save_scope") != token or not name or guard.get("pawn") != name
                or guard.get("dead") is not False or not bounded_health(health, maximum)):
            return
        identity = (session, character, area, epoch, name)
        checked = (identity, client.gameplay_seen)
        vitals = _vital_values(frame, maximum)
        receipt = (identity, health, maximum)
        if self.health_checked == checked:
            return
        self.health_checked = checked
        current, current_maximum = pawn.get("health"), pawn.get("max_health")
        current_vitals = game.get(VITALS, {}).get(name, {})
        if (self.health_receipt == receipt
                and (current is None or _matches_vital(current, health)) and current_maximum == maximum
                and all(_matches_vital(current_vitals.get(field), value) for field, value in vitals.items())):
            return
        if "rads" in vitals:
            fields = ("food", "regen", "stim", "fatigue", "intox", "sickness", "rads")
        elif "sickness" in vitals:
            fields = ("food", "regen", "stim", "fatigue", "intox", "sickness")
        elif "fatigue" in vitals or "intox" in vitals:
            fields = ("food", "regen", "stim", "fatigue", "intox")
        else:
            fields = ("food", "regen", "stim") if "stim" in vitals else ("food", "regen") if vitals else ()
        try:
            updated = await client.bridge.command(HEALTH_PROJECT, area, session, epoch, token, name,
                                                  str(health), str(maximum),
                                                  *(str(vitals.get(field, "-")) for field in fields), timeout=1.5)
            projected = updated.get(VITALS, {}).get(name, {})
            if ((updated.get("state"), updated.get("map"), updated.get("save_scope")) != ("connected", area, token)
                    or updated.get("pawns", {}).get("local", {}).get("name") != name
                    or updated.get("ack", {}).get("message") != "stock co-op guest health projected"
                    or not all(_matches_vital(projected.get(field), value) for field, value in vitals.items())):
                raise RuntimeError("The isolated stock health view did not verify")
        except (OSError, RuntimeError) as error:
            reason = str(error)
            if self.health_error != reason:
                self.health_error = reason
                client.control.event("native_guest_health_unavailable", reason=reason)
            return
        self.health_receipt, self.health_error = receipt, None
        client.last_game = updated

    async def sync(self, client, active):
        game = client.last_game
        if not active or not game.get("save_scope") or not client.zone_epoch:
            await self.close()
            return
        character = client.invite["character_id"]
        token = uuid.UUID(character).hex
        if game.get("save_scope") != token:
            await self.close()
            return
        area, epoch = game["map"], client.zone_epoch
        identity = (client.host_session, epoch, area)
        scope = self.inventory_scope(client)
        pending = self.pending_stock_refresh if self.pending_stock_refresh == scope else None
        if self.identity != identity:
            await self.close()
            self.identity, self.digest, self.next_poll = identity, "-", 0.0
        self.pending_stock_refresh = pending
        if self.request is not None and self.request.done():
            request, self.request = self.request, None
            requested_stock, self.request_for_stock = self.request_for_stock, None
            try:
                reply = request.result()
                # A pickup arrived after this request started. Use one fresh view for all resulting changes.
                if not pending and (requested_stock is None or requested_stock == scope):
                    projected = await self.receive(client, reply["native_guest_ui"], area, epoch, character)
                    if not projected and requested_stock is not None:
                        self.pending_stock_refresh = requested_stock
                    if projected and requested_stock is not None and requested_stock == self.stock_view(client):
                        client.last_game = await client.bridge.command("native-inventory-stock-refresh", epoch, timeout=4)
                    self.last_error = None
            except (KeyError, TypeError, OSError, RuntimeError, ValueError, zlib.error) as error:
                if requested_stock is not None and requested_stock == self.inventory_scope(client):
                    self.pending_stock_refresh = requested_stock
                description = str(error)
                if description != self.last_error:
                    event = ("native_stock_inventory_refresh_unavailable" if requested_stock is not None
                             else "native_guest_ui_unavailable")
                    client.control.event(event, reason=description)
                    self.last_error = description
        now = time.monotonic()
        if self.request is None and now >= self.next_poll:
            self.next_poll = now + 3
            self.request_for_stock = self.pending_stock_refresh
            self.pending_stock_refresh = None
            self.request = asyncio.create_task(client.gameplay_action(ACTION, uuid.uuid4().hex, self.digest))

    async def receive(self, client, result, area, epoch, character, *, layout_sequence=None):
        digest = result["digest"]
        if ((result["map"], result["epoch"], result["owner"]) != (area, epoch, character)
                or (client.last_game.get("map"), client.zone_epoch) != (area, epoch)
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
            raise ValueError("The co-op view belongs to another visit")
        if digest == self.digest:
            return True
        encoded = result["payload"]
        if not isinstance(encoded, str) or len(encoded) > 27_000:
            raise ValueError("Invalid co-op view encoding")
        stream = zlib.decompressobj()
        contents = stream.decompress(base64.b64decode(encoded, validate=True), MAX_BYTES + 1)
        if not stream.eof or stream.unused_data or sha256(contents).hexdigest() != digest:
            raise ValueError("Invalid co-op view content")
        projected = await self.project(client, character, validate(contents, area, character),
                                       layout_sequence=layout_sequence)
        if projected:
            self.digest = digest
        return projected

    async def refresh(self, client, *, layout_sequence=None):
        """Project a newly committed inventory while the requesting screen is still mounted."""
        await self.close()
        if layout_sequence is not None:
            self.digest = "-"
        area, epoch = client.last_game.get("map"), client.zone_epoch
        character = client.invite["character_id"]
        try:
            reply = await client.gameplay_action(ACTION, uuid.uuid4().hex, "-")
            if not await self.receive(client, reply["native_guest_ui"], area, epoch, character,
                                      layout_sequence=layout_sequence):
                raise RuntimeError("The local inventory changed before reconciliation")
        except (KeyError, TypeError, OSError, RuntimeError, ValueError, zlib.error):
            self.queue_stock_inventory(client)
            raise
        self.identity = (client.host_session, epoch, area)
        self.next_poll = time.monotonic() + 3

    async def project(self, client, character, contents, *, layout_sequence=None):
        bridge, game = client.bridge, getattr(client, "last_game", {})
        path = None
        try:
            if game.get(runtime.GUEST_UI_MEMORY) is True and bridge.local_bridge is not None:
                layout = {"layout_sequence": layout_sequence} if layout_sequence is not None else {}
                response, _ = await bridge.guest_ui_command(character, payload=contents.encode("ascii"), **layout)
                if (response.get("state"), response.get("map"), response.get("save_scope")) != (
                        "connected", game.get("map"), uuid.UUID(character).hex):
                    raise RuntimeError("The resident stock view belongs to another guest or visit")
            else:
                nonce = uuid.uuid4().hex
                path = bridge.directory / f"native-guest-ui-{nonce}.tsv"
                runtime.write_text(path, contents, newline="")
                layout = (layout_sequence,) if layout_sequence is not None else ()
                response = await bridge.command("native-guest-ui-project", character, nonce, *layout, timeout=4)
            message = response.get("ack", {}).get("message", "")
            if message == "stock personal inventory update pending":
                return False
            receipt = _READY.fullmatch(message)
            if not receipt:
                raise RuntimeError("The isolated guest inventory and Journal did not verify")
            items, notes, quests, completed, attachments = map(int, receipt.groups())
            client.last_game = response
            client.control.event("native_guest_ui_projected", items=items, notes=notes, quests=quests,
                                 completed=completed, attachments=attachments)
            return True
        finally:
            if path is not None:
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    path.unlink()
