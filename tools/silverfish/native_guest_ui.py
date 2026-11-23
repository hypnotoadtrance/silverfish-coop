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
MAX_BYTES = runtime.GUEST_UI_MAX_BYTES
_DIGEST = re.compile(r"[0-9a-f]{64}")
_READY = re.compile(r"stock co-op inventory and Journal projected (\d+) (\d+) (\d+) (\d+) (\d+)")


def bounded_health(health, maximum):
    return (type(health) in (int, float) and type(maximum) in (int, float)
            and 1 <= maximum <= 1_000_000 and 0 <= health <= maximum)


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
    return {"map": game["map"], "epoch": epoch, "owner": owner, "health": health, "maximum": maximum}


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
        receipt = (identity, health, maximum)
        if self.health_checked == checked:
            return
        self.health_checked = checked
        current, current_maximum = pawn.get("health"), pawn.get("max_health")
        if (self.health_receipt == receipt and (current is None or math.isclose(current, health, abs_tol=.01))
                and current_maximum == maximum):
            return
        try:
            updated = await client.bridge.command(HEALTH_PROJECT, area, session, epoch, token, name,
                                                  str(health), str(maximum), timeout=1.5)
            if ((updated.get("state"), updated.get("map"), updated.get("save_scope")) != ("connected", area, token)
                    or updated.get("pawns", {}).get("local", {}).get("name") != name
                    or updated.get("ack", {}).get("message") != "stock co-op guest health projected"):
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
        if self.identity != identity:
            await self.close()
            self.identity, self.digest, self.next_poll = identity, "-", 0.0
        if self.request is not None and self.request.done():
            request, self.request = self.request, None
            try:
                await self.receive(client, request.result()["native_guest_ui"], area, epoch, character)
                self.last_error = None
            except (KeyError, TypeError, OSError, RuntimeError, ValueError, zlib.error) as error:
                description = str(error)
                if description != self.last_error:
                    client.control.event("native_guest_ui_unavailable", reason=description)
                    self.last_error = description
        now = time.monotonic()
        if self.request is None and now >= self.next_poll:
            self.next_poll = now + 3
            self.request = asyncio.create_task(client.gameplay_action(ACTION, uuid.uuid4().hex, self.digest))

    async def receive(self, client, result, area, epoch, character):
        digest = result["digest"]
        if ((result["map"], result["epoch"], result["owner"]) != (area, epoch, character)
                or (client.last_game.get("map"), client.zone_epoch) != (area, epoch)
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
            raise ValueError("The co-op view belongs to another visit")
        if digest == self.digest:
            return
        encoded = result["payload"]
        if not isinstance(encoded, str) or len(encoded) > 27_000:
            raise ValueError("Invalid co-op view encoding")
        stream = zlib.decompressobj()
        contents = stream.decompress(base64.b64decode(encoded, validate=True), MAX_BYTES + 1)
        if not stream.eof or stream.unused_data or sha256(contents).hexdigest() != digest:
            raise ValueError("Invalid co-op view content")
        await self.project(client, character, validate(contents, area, character))
        self.digest = digest

    async def refresh(self, client):
        """Project a newly committed inventory while the requesting screen is still mounted."""
        await self.close()
        area, epoch = client.last_game.get("map"), client.zone_epoch
        character = client.invite["character_id"]
        reply = await client.gameplay_action(ACTION, uuid.uuid4().hex, "-")
        await self.receive(client, reply["native_guest_ui"], area, epoch, character)
        self.identity = (client.host_session, epoch, area)
        self.next_poll = time.monotonic() + 3

    async def refresh_stock_inventory(self, client):
        if client.last_game.get("native_inventory_view", {}).get("state") != "stock":
            return
        try:
            await self.refresh(client)
            client.last_game = await client.bridge.command("native-inventory-stock-refresh", client.zone_epoch, timeout=4)
        except (KeyError, TypeError, OSError, RuntimeError, ValueError, zlib.error) as error:
            client.control.event("native_stock_inventory_refresh_unavailable", reason=str(error))

    async def project(self, client, character, contents):
        bridge, game = client.bridge, getattr(client, "last_game", {})
        path = None
        try:
            if game.get(runtime.GUEST_UI_MEMORY) is True and bridge.local_bridge is not None:
                response, _ = await bridge.guest_ui_command(character, payload=contents.encode("ascii"))
                if (response.get("state"), response.get("map"), response.get("save_scope")) != (
                        "connected", game.get("map"), uuid.UUID(character).hex):
                    raise RuntimeError("The resident stock view belongs to another guest or visit")
            else:
                nonce = uuid.uuid4().hex
                path = bridge.directory / f"native-guest-ui-{nonce}.tsv"
                runtime.write_text(path, contents, newline="")
                response = await bridge.command("native-guest-ui-project", character, nonce, timeout=4)
            receipt = _READY.fullmatch(response.get("ack", {}).get("message", ""))
            if not receipt:
                raise RuntimeError("The isolated guest inventory and Journal did not verify")
            items, notes, quests, completed, attachments = map(int, receipt.groups())
            client.last_game = response
            client.control.event("native_guest_ui_projected", items=items, notes=notes, quests=quests,
                                 completed=completed, attachments=attachments)
        finally:
            if path is not None:
                with contextlib.suppress(FileNotFoundError, PermissionError):
                    path.unlink()
