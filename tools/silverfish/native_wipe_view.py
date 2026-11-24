"""Project an authenticated owner's frozen stock wipe selection into the local game."""

import contextlib
import time
import uuid

from . import native_inventory, native_inventory_wipe, party
from .native_gold import valid_guid


class WipeView:
    def __init__(self, bridge, control, owner: str):
        self.bridge, self.control, self.owner = bridge, control, owner
        self.namespace = uuid.uuid4()
        self.offset = 0
        self.scope = None
        self.confirmed = None
        self.next_lease = 0.0
        self.next_page = None
        self.retry_at = 0.0
        self.last_error = None
        self.sequence = 0

    def read_events(self) -> list[list[str]]:
        try:
            with (self.bridge.directory / "native-wipe-view-events").open("rb") as stream:
                stream.seek(self.offset)
                batch = stream.read(4096)
        except OSError:
            return []
        end = batch.rfind(b"\n") + 1
        self.offset += end if end else len(batch) if len(batch) == 4096 else 0
        return [line.decode("utf-8", errors="replace").split("\t") for line in batch[:end].splitlines()]

    def binding(self, game: dict, draft: dict | None) -> tuple[str, ...] | None:
        if not draft or not draft.get("native_inventory_ready") or self.owner not in draft["roster"]:
            return None
        limit, remaining, identifier = draft["keep_slots"], draft.get("remaining_seconds"), draft["id"]
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        area = game.get("map")
        if (type(limit) is not int or not 1 <= limit <= 10
                or not isinstance(identifier, str) or not party.REQUEST_ID.fullmatch(identifier) or not area or not pawn):
            return None
        owner = identifier, area, pawn, self.owner
        if draft.get("deadline", 0) is None and remaining is None:
            return (*owner, "0")
        if type(remaining) not in (int, float) or not 0 < remaining <= 30:
            return None
        now = time.time()
        deadline = self.scope[4] if self.scope and self.scope[:4] == owner else str(int(now + remaining))
        return (*owner, deadline) if int(deadline) > now else None

    async def render(self, scope: tuple[str, ...], limit: int, offset: int, fetch) -> dict:
        page = (await fetch(str(offset)))["native_wipe_items"]
        total, forced, items = (page[key] for key in ("count", "forced_count", "items"))
        if (page["wipe_id"] != scope[0] or page["owner"] != self.owner or page["offset"] != offset
                or type(total) is not int or not 0 <= total <= native_inventory.MAX_ITEMS or type(forced) is not int
                or not 0 <= forced <= min(total, 1) or offset % native_inventory.PAGE_SIZE
                or not 0 <= offset <= total or type(items) is not list or len(items) != min(native_inventory.PAGE_SIZE, total - offset)):
            raise RuntimeError("The frozen stock inventory page did not match the current wipe")
        rows = tuple("|".join(str(value) for value in (
            row["guid"], row["source"], row["index"], row["quantity"], row["class_path"],
            native_inventory_wipe.selection_policy(row), int(row["stackable"]),
        )) for row in items)
        confirmed = "-"
        if self.confirmed is not None:
            if (type(self.confirmed) is not dict or len(self.confirmed) > limit - forced
                    or any(not valid_guid(guid) or type(count) is not int or count != 1
                           for guid, count in self.confirmed.items())):
                raise RuntimeError("The host reported an invalid confirmed wipe selection")
            confirmed = ",".join(sorted(self.confirmed)) or "0"
        snapshot = await self.bridge.command("native-wipe-view-render", *scope, str(limit), str(forced), str(offset),
                                             str(total), confirmed, *rows, timeout=4)
        observed = snapshot.get("native_wipe_view", {})
        if (tuple(observed.get(key) for key in ("id", "map", "pawn", "owner")) != scope[:4]
                or observed.get("offset") != offset or observed.get("total") != total):
            raise RuntimeError("The native wipe overlay did not render the current frozen inventory page")
        self.next_lease = time.monotonic() + .8
        self.next_page = offset + len(items) if (observed.get("screen"), observed.get("state")) == ("stock", "paging") else None
        return snapshot

    async def fixture(self, action: str, key: str) -> dict:
        if not self.scope or not isinstance(key, str) or (
                key not in ("up", "down", "toggle", "submit", "back", "stock-submit", "stock-waiting",
                            "stock-reopen-pause", "stock-reopen-journal")
                and not (key.startswith("stock-keep-") and valid_guid(key[11:]))):
            raise ValueError("Select an input for the active staged wipe overlay")
        return await self.bridge.command(action, *self.scope[:4], key, timeout=4)

    async def sync(self, game: dict, draft: dict | None, fetch, submit) -> dict:
        events = self.read_events()
        scope = self.binding(game, draft)
        confirmed = draft["choices"].get(self.owner) if scope else None
        if self.scope and (scope != self.scope or confirmed is not None and confirmed != self.confirmed):
            with contextlib.suppress(OSError, RuntimeError, ValueError):
                game = await self.bridge.command("native-wipe-view-clear", *self.scope[:4], timeout=2)
            self.scope = None
            self.next_page = None
        if not scope:
            self.confirmed = None
            self.retry_at = 0.0
            self.last_error = None
            return game
        try:
            if self.scope is None:
                if time.monotonic() < self.retry_at:
                    return game
                self.confirmed = confirmed
                game = await self.render(scope, draft["keep_slots"], 0, fetch)
                self.scope = scope
                self.last_error = None
            elif self.next_page is not None:
                game = await self.render(scope, draft["keep_slots"], self.next_page, fetch)
            for row in events:
                if (len(row) < 7 or tuple(row[2:6]) != scope[:4] or not row[1].isascii() or not row[1].isdigit()
                        or len(row[1]) > 16 or not self.sequence < (sequence := int(row[1])) < 2**53):
                    continue
                self.sequence = sequence
                if row[0] == "native-wipe-view-page" and len(row) == 7 and row[6].isascii() and row[6].isdigit() and len(row[6]) <= 4:
                    game = await self.render(scope, draft["keep_slots"], int(row[6]), fetch)
                elif (self.confirmed is None and row[0] == "native-wipe-view-submit"
                      and row[6] == str(len(row[7:])) and len(row[7:]) <= draft["keep_slots"]
                      and len(set(row[7:])) == len(row[7:]) and all(valid_guid(guid) for guid in row[7:])):
                    operation = uuid.uuid5(self.namespace, f"{scope[0]}:{sequence}").hex
                    outcome = "refused"
                    retained = dict.fromkeys(row[7:], 1)
                    try:
                        await submit(operation, retained)
                        outcome = "accepted"
                        self.control.event("native_wipe_view_choice", wipe_id=scope[0], character_id=self.owner,
                                           operation_id=operation, selected=len(row[7:]))
                    except (OSError, ValueError, RuntimeError) as error:
                        self.control.event("native_wipe_view_choice_rejected", wipe_id=scope[0], operation_id=operation, reason=str(error))
                    with contextlib.suppress(OSError, RuntimeError, ValueError):
                        game = await self.bridge.command("native-wipe-view-result", *scope[:4], outcome, timeout=2)
                        if outcome == "accepted":
                            self.confirmed = retained
                    break
            if time.monotonic() >= self.next_lease:
                game = await self.bridge.command("native-wipe-view-lease", *scope, timeout=2)
                self.next_lease = time.monotonic() + .8
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
            reason = str(error)
            if self.last_error != reason:
                self.control.event("native_wipe_view_unavailable", wipe_id=scope[0], reason=reason)
                self.last_error = reason
            self.scope = None
            self.next_page = None
            self.retry_at = time.monotonic() + 1
        return game
