"""Publish the resident title's verified saved host campaigns."""

import asyncio
from collections.abc import Callable
from itertools import islice
import os
from pathlib import Path
import re
import stat

from . import runtime
from .control import NAME
from .title_handoff import _unlink


FILE = "title-host-campaigns.tsv"
MAX_CAMPAIGNS = 64
_SCAN_LIMIT = 256
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", re.IGNORECASE)


def _directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("The campaign directory is redirected")


def saved_campaign(campaign: Path, saved: Path, name: str) -> tuple[Path, str]:
    """Verify a named campaign against this title's active native save profile."""
    from .service import menu_host_start

    if not NAME.fullmatch(name) or _DEVICE.fullmatch(name):
        raise ValueError("Invalid saved campaign name")
    if saved.parent == campaign / "game-user" and name != campaign.name:
        raise ValueError("The resident title has a different campaign profile")
    selected = campaign.parent / name
    for path in (campaign.parent, selected, selected / "save"):
        _directory(path)
    mode, area = menu_host_start(selected, profile=saved.parent, require_scoped=True)
    if mode != "resume":
        raise ValueError("The selected campaign checkpoint is missing")
    return selected, area


def campaign_names(campaign: Path, saved: Path) -> tuple[str, ...]:
    """Inspect a bounded set of campaign directories without opening stores for writing."""
    try:
        _directory(campaign.parent)
        if saved.parent == campaign / "game-user":
            names = [campaign.name]
        else:
            with os.scandir(campaign.parent) as entries:
                names = sorted((entry.name for entry in islice(entries, _SCAN_LIMIT)), key=str.casefold)
    except (OSError, ValueError, RuntimeError):
        return ()
    valid = []
    for name in names:
        try:
            saved_campaign(campaign, saved, name)
        except (OSError, ValueError, RuntimeError):
            continue
        valid.append(name)
        if len(valid) == MAX_CAMPAIGNS:
            break
    return tuple(valid)


class TitleHostCampaigns:
    """Publish read-only scan results for the exact active Host offer."""

    def __init__(self, bridge: Path, scan: Callable[[], tuple[str, ...]]):
        self.path = bridge / FILE
        self._scan = scan
        self._generation = None
        self._names = None
        self._job = None
        self._published = False
        _unlink(self.path)

    def poll(self, generation: str | None) -> None:
        if generation != self._generation:
            self._generation = generation
            self._names = None
            self._published = False
            _unlink(self.path)
        if self._job is not None and self._job[1].done():
            scanned, task = self._job
            self._job = None
            names = task.result()
            if scanned == generation:
                self._names = names
        if generation is None:
            return
        if self._names is None:
            if self._job is None:
                self._job = generation, asyncio.create_task(asyncio.to_thread(self._scan))
            return
        if not self._published:
            body = f"1\t{generation}\t{len(self._names)}\n"
            body += "".join(name + "\n" for name in self._names)
            try:
                runtime.write_text(self.path, body, newline="\n")
            except OSError:
                return
            self._published = True

    def contains(self, generation: str, name: str) -> bool:
        return bool(self._published and generation == self._generation and name in self._names)

    def close(self) -> None:
        if self._job is not None:
            self._job[1].cancel()
            self._job = None
        self.poll(None)
