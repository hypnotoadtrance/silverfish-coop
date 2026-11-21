"""Publish the resident title's verified saved host campaigns."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
import os
from pathlib import Path
import re
import stat

from . import native_host_save, runtime
from .control import NAME
from .title_handoff import _unlink


FILE = "title-host-campaigns.tsv"
MAX_CAMPAIGNS = 64
_SCAN_LIMIT = 256
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])", re.IGNORECASE)
_MAP_NAMES = {
    "Anex_01_P": "Annex",
    "Anom_Maze_01": "Anomaly Maze",
    "CrossRoads_P": "Crossroads",
    "Observitory_P": "Observatory",
    "Rev_NorthBase_P": "North Base",
    "RuinFeilds_P": "Ruin Fields",
    "TutTnl_01_P": "Tutorial Tunnel",
    "Tut_Dock_P": "Tutorial Dock",
    "PlaytestMap_01": "Playtest Map 1",
    "PlaytestMap_02": "Playtest Map 2",
}


@dataclass(frozen=True)
class CampaignEntry:
    name: str
    map_label: str
    saved_at: str
    modified_ns: int


def _map_label(area: str) -> str:
    name = area.rsplit("/", 1)[-1]
    if name in _MAP_NAMES:
        return _MAP_NAMES[name]
    name = re.sub(r"_[0-9]+$", "", name.removesuffix("_P").removeprefix("Shop_"))
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.replace("_", " "))
    return name if len(name) <= 64 and re.fullmatch(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+)*", name) else "Campaign area"


def _directory(path: Path) -> None:
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise ValueError("The campaign directory is redirected")


def _qualified_campaign(campaign: Path, saved: Path, name: str) -> tuple[Path, dict]:
    from .service import menu_host_checkpoint

    if not NAME.fullmatch(name) or _DEVICE.fullmatch(name):
        raise ValueError("Invalid saved campaign name")
    if saved.parent == campaign / "game-user" and name != campaign.name:
        raise ValueError("The resident title has a different campaign profile")
    selected = campaign.parent / name
    for path in (campaign.parent, selected, selected / "save"):
        _directory(path)
    checkpoint = menu_host_checkpoint(selected, profile=saved.parent, require_scoped=True)
    if checkpoint is None:
        raise ValueError("The selected campaign checkpoint is missing")
    return selected, checkpoint


def saved_campaign(campaign: Path, saved: Path, name: str) -> tuple[Path, str]:
    """Verify a named campaign against this title's active native save profile."""
    selected, checkpoint = _qualified_campaign(campaign, saved, name)
    return selected, checkpoint["world"]["map"]


def _entry(campaign: Path, saved: Path, name: str) -> CampaignEntry:
    _, checkpoint = _qualified_campaign(campaign, saved, name)
    storage = native_host_save.checkpoint_profile(saved.parent, checkpoint)
    slot = checkpoint["world"]["native_host_save"]["slot"]
    modified_ns = native_host_save.slot_path(storage, slot).stat().st_mtime_ns
    local = datetime.fromtimestamp(modified_ns // 1_000_000_000)
    saved_at = f"{local:%Y-%m-%d %H:%M:%S}"
    return CampaignEntry(name, _map_label(checkpoint["world"]["map"]), saved_at, modified_ns)


def campaign_entries(campaign: Path, saved: Path) -> tuple[CampaignEntry, ...]:
    """Read a bounded set of campaigns and rank the qualified saves newest first."""
    try:
        _directory(campaign.parent)
        if saved.parent == campaign / "game-user":
            names = [campaign.name]
        else:
            with os.scandir(campaign.parent) as entries:
                names = [entry.name for entry in islice(entries, _SCAN_LIMIT)]
    except (OSError, ValueError, RuntimeError):
        return ()
    valid = []
    for name in names:
        try:
            valid.append(_entry(campaign, saved, name))
        except (OSError, ValueError, RuntimeError, OverflowError):
            continue
    valid.sort(key=lambda entry: (-entry.modified_ns, entry.name.casefold(), entry.name))
    return tuple(valid[:MAX_CAMPAIGNS])


class TitleHostCampaigns:
    """Publish read-only scan results for the exact active Host offer."""

    def __init__(self, bridge: Path, scan: Callable[[], tuple[CampaignEntry, ...]]):
        self.path = bridge / FILE
        self._scan = scan
        self._generation = None
        self._entries = None
        self._job = None
        self._published = False
        _unlink(self.path)

    def poll(self, generation: str | None) -> None:
        if generation != self._generation:
            self._generation = generation
            self._entries = None
            self._published = False
            _unlink(self.path)
        if self._job is not None and self._job[1].done():
            scanned, task = self._job
            self._job = None
            entries = task.result()
            if scanned == generation:
                self._entries = entries
        if generation is None:
            return
        if self._entries is None:
            if self._job is None:
                self._job = generation, asyncio.create_task(asyncio.to_thread(self._scan))
            return
        if not self._published:
            body = f"2\t{generation}\t{len(self._entries)}\n"
            body += "".join(f"{entry.name}\t{entry.map_label}\t{entry.saved_at}\n" for entry in self._entries)
            try:
                runtime.write_text(self.path, body, newline="\n")
            except OSError:
                return
            self._published = True

    def contains(self, generation: str, name: str) -> bool:
        return (self._published and generation == self._generation
                and any(entry.name == name for entry in self._entries))

    def close(self) -> None:
        if self._job is not None:
            self._job[1].cancel()
            self._job = None
        self.poll(None)
