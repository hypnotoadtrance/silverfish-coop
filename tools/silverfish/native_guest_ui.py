"""Mirror stock guest inventory and the host's Journal into the isolated resident view."""

import asyncio
import base64
import contextlib
from hashlib import sha256
import re
import time
import uuid
import zlib

from . import party, runtime


ACTION = "native-guest-ui-read"
MAX_BYTES = 20_000
_DIGEST = re.compile(r"[0-9a-f]{64}")
_READY = re.compile(r"stock co-op inventory and Journal projected (\d+) (\d+) (\d+)")


def validate(contents: bytes, area: str, character: str) -> str:
    if (not 0 < len(contents) <= MAX_BYTES or contents.count(b"\n") > 128 or not contents.endswith(b"\n")
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
    nonce = uuid.uuid4().hex
    path = bridge.directory / f"native-guest-ui-{nonce}.tsv"
    try:
        snapshot = await bridge.command(ACTION, owner["pawn"], nonce, timeout=3)
        if (snapshot.get("state"), snapshot.get("map"), snapshot.get("ack", {}).get("message")) != (
                "hosting", game["map"], "captured co-op guest view"):
            raise RuntimeError("The host did not verify the co-op view")
        with path.open("rb") as file:
            contents = file.read(MAX_BYTES + 1)
        validate(contents, game["map"], character)
    finally:
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

    async def close(self):
        if self.request is not None:
            self.request.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError, ValueError, RuntimeError):
                await self.request
            self.request = None

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
                result = request.result()["native_guest_ui"]
                digest = result["digest"]
                if ((result["map"], result["epoch"], result["owner"]) != (area, epoch, character)
                        or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
                    raise ValueError("The co-op view belongs to another visit")
                if digest != self.digest:
                    encoded = result["payload"]
                    if not isinstance(encoded, str) or len(encoded) > 27_000:
                        raise ValueError("Invalid co-op view encoding")
                    stream = zlib.decompressobj()
                    contents = stream.decompress(base64.b64decode(encoded, validate=True), MAX_BYTES + 1)
                    if not stream.eof or stream.unused_data or sha256(contents).hexdigest() != digest:
                        raise ValueError("Invalid co-op view content")
                    await self.project(client, character, validate(contents, area, character))
                    self.digest = digest
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

    async def project(self, client, character, contents):
        nonce = uuid.uuid4().hex
        path = client.bridge.directory / f"native-guest-ui-{nonce}.tsv"
        try:
            runtime.write_text(path, contents, newline="")
            response = await client.bridge.command("native-guest-ui-project", character, nonce, timeout=4)
            receipt = _READY.fullmatch(response.get("ack", {}).get("message", ""))
            if not receipt:
                raise RuntimeError("The isolated guest inventory and Journal did not verify")
            items, notes, quests = map(int, receipt.groups())
            client.last_game = response
            client.control.event("native_guest_ui_projected", items=items, notes=notes, quests=quests)
        finally:
            with contextlib.suppress(FileNotFoundError, PermissionError):
                path.unlink()
