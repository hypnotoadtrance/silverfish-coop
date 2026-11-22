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
    header = f"1\t{generation.decode()}\t{token.decode()}\t{result}\t{len(rows)}\n"
    with suppress(OSError):
        runtime.write_text(bridge / RESPONSE, header + "".join(f"{row.host_name}\n" for row in rows), newline="\n")


async def serve(bridge, transport, open_lobby, pump):
    request_path = bridge / REQUEST
    response_path = bridge / RESPONSE
    lookup = None
    for path in (request_path, response_path):
        with suppress(OSError):
            path.unlink(missing_ok=True)
    try:
        while not pump.done():
            request = _take(request_path)
            if request is not None and _qualified(bridge, request[1]):
                generation, token, action = request.groups()
                if action == b"search":
                    lookup = None
                    try:
                        rows = await transport.public_lobbies()
                    except (OSError, ValueError, RuntimeError):
                        _publish(bridge, generation, token, "unavailable")
                    else:
                        lookup = generation, token, time.monotonic() + 60, rows
                        _publish(bridge, generation, token, "ready", rows)
                else:
                    index = int(action) - 1
                    current, lookup = lookup, None
                    if (current and current[:2] == (generation, token) and time.monotonic() < current[2]
                            and index < len(current[3]) and await open_lobby(current[3][index].lobby)):
                        _publish(bridge, generation, token, "joined")
                    else:
                        _publish(bridge, generation, token, "unavailable")
            await asyncio.sleep(.15)
    finally:
        for path in (request_path, response_path):
            with suppress(OSError):
                path.unlink(missing_ok=True)
