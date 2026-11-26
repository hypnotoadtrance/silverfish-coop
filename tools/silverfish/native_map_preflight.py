"""Check that a proposed campaign map is installed before leaving the source."""
from __future__ import annotations

import asyncio
import re
import uuid


CHALLENGE = "map_package_challenge"
VOTE = "map_package_vote"
_PACKAGE = re.compile(r"/(?:[A-Za-z0-9_]+/)+[A-Za-z0-9_]+")
_TITLE = "/Game/Classes/Maps/MainMenu_P"


def valid_campaign_package(package: object) -> bool:
    return (isinstance(package, str) and len(package) <= 180 and package != _TITLE
            and _PACKAGE.fullmatch(package) is not None and len(package) + 1 + len(package.rsplit("/", 1)[-1]) <= 180)


async def register(bridge, package: str) -> bool:
    """Resolve the canonical World asset locally and enable its stock route on this seat."""
    if not valid_campaign_package(package):
        return False
    try:
        frame = await bridge.command("native-map-package", package, "register", timeout=3)
    except (OSError, RuntimeError, ValueError):
        return False
    return frame.get("ack", {}).get("message") == "map-package:world"


def challenge(value: object) -> tuple[str, str] | None:
    if isinstance(value, list) and len(value) == 2:
        nonce, package = value
        if isinstance(nonce, str) and re.fullmatch(r"[0-9a-f]{32}", nonce) and valid_campaign_package(package):
            return nonce, package
    return None


class Preflight:
    def __init__(self, package: str, epoch: str, characters: set[str]):
        self.package = package
        self.epoch = epoch
        self.pending = set(characters)
        self.missing: set[str] = set()
        self.nonce = uuid.uuid4().hex
        self.done = asyncio.Event()

    def offer(self, character: str) -> list[str] | None:
        if character in self.pending:
            return [self.nonce, self.package]
        return None

    def receive(self, character: str, epoch: object, value: object) -> None:
        if (epoch != self.epoch or character not in self.pending or not isinstance(value, list)
                or len(value) != 2 or value[0] != self.nonce or type(value[1]) is not bool):
            return
        self.pending.remove(character)
        if not value[1]:
            self.missing.add(character)
        if self.missing or not self.pending:
            self.done.set()

    async def wait(self) -> bool:
        if self.pending and not self.done.is_set():
            try:
                await asyncio.wait_for(self.done.wait(), timeout=4)
            except TimeoutError:
                return False
        return not self.pending and not self.missing
