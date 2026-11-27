"""Publish bounded Steam lobby search results to the stock title controls."""

import asyncio
from contextlib import suppress
from pathlib import Path
import re
import time

from . import runtime
from .title_handoff import DESCRIPTOR_FILE

REQUEST = "steam-coop-browser-request.tsv"
RESPONSE = "steam-coop-browser-response.tsv"
_REQUEST = re.compile(rb"1\t([0-9a-f]{32})\t([0-9a-f]{32})\t(search|[1-9][0-9]?)\n")
_TITLE = re.compile(rb"[12]\t([0-9a-f]{32})\t(?:join|host,join)\t([0-9]{1,10})(?:\t[^\r\n]+)?\r?\n")


def _qualified(bridge: Path, generation: bytes) -> bool:
    with suppress(OSError):
        with (bridge / DESCRIPTOR_FILE).open("rb") as source:
            title = _TITLE.fullmatch(source.read(257))
        return bool(title and title[1] == generation and time.time() <= int(title[2]) + 1)
    return False


def _take(path: Path):
    with suppress(OSError):
        with path.open("rb") as source:
            request = _REQUEST.fullmatch(source.read(97))
        path.unlink()
        return request


def _publish(bridge, generation, token, result, rows=()):
    header = f"2\t{generation.decode()}\t{token.decode()}\t{result}\t{len(rows)}\n"
    listing = "".join(f"{row.host_name}\t{row.version or '-'}\t{int(row.compatible)}\n" for row in rows)
    with suppress(OSError):
        runtime.write_text(bridge / RESPONSE, header + listing, newline="\n")


async def _cancel_search(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def serve(bridge, transport, open_lobby, pump):
    request_path = bridge / REQUEST
    response_path = bridge / RESPONSE
    lookup = None
    search = None
    for path in (request_path, response_path):
        with suppress(OSError):
            path.unlink(missing_ok=True)
    try:
        while not pump.done():
            request = _take(request_path)
            if request is not None and _qualified(bridge, request[1]):
                generation, token, action = request.groups()
                if action == b"search":
                    if search is not None:
                        await _cancel_search(search[2])
                    lookup = None
                    search = generation, token, asyncio.create_task(transport.public_lobbies())
                elif search is None and (lookup is None or lookup[:2] == (generation, token)):
                    index = int(action) - 1
                    result = "unavailable"
                    if (lookup is not None and time.monotonic() < lookup[2] and index < len(lookup[3])):
                        selected = lookup[3][index]
                        if not selected.compatible:
                            result = "incompatible"
                        else:
                            lookup = None
                            if await open_lobby(selected.lobby):
                                result = "joined"
                    _publish(bridge, generation, token, result)
            if search is not None and search[2].done():
                generation, token, task = search
                search = None
                try:
                    rows = task.result()
                except (OSError, ValueError, RuntimeError):
                    rows = None
                if _qualified(bridge, generation):
                    if rows is None:
                        _publish(bridge, generation, token, "unavailable")
                    else:
                        lookup = generation, token, time.monotonic() + 60, rows
                        _publish(bridge, generation, token, "ready", rows)
            await asyncio.sleep(.15)
    finally:
        if search is not None:
            await _cancel_search(search[2])
        for path in (request_path, response_path):
            with suppress(OSError):
                path.unlink(missing_ok=True)
