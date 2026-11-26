"""Publish the resident title's verified saved host campaigns."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from itertools import islice
import logging
import os
from pathlib import Path
import re
import stat
import uuid

from . import native_host_save, runtime
from .campaign import CampaignError, CampaignFormatError, CampaignStore, DELETED, StoreBusyError
from .control import NAME
from .steam_cloud import deleted_campaign, record_deletion
from .title_handoff import TOKEN, _unlink


FILE = "title-host-campaigns.tsv"
REQUEST = "title-save-request.tsv"
RESPONSE = "title-save-response.tsv"
MAX_CHARACTERS = 256
_OPERATIONS = {"characters", "delete-save", "delete-character"}
MAX_CAMPAIGNS = 64
_SCAN_LIMIT = 256
_LOG = logging.getLogger(__name__)
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
    campaign_id: str = ""
    revision: int = 0
    deleted: bool = False


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
    if deleted_campaign(campaign.parent, uuid.UUID(checkpoint["campaign_id"]).hex):
        raise ValueError("The selected co-op campaign was deleted")
    return selected, checkpoint


def saved_campaign(campaign: Path, saved: Path, name: str) -> tuple[Path, str]:
    """Verify a named campaign against this title's active native save profile."""
    selected, checkpoint = _qualified_campaign(campaign, saved, name)
    if checkpoint["world"].get(DELETED) is True:
        raise ValueError("The selected co-op campaign was deleted")
    return selected, checkpoint["world"]["map"]


def _entry(campaign: Path, saved: Path, name: str) -> CampaignEntry:
    _, checkpoint = _qualified_campaign(campaign, saved, name)
    storage = native_host_save.checkpoint_profile(saved.parent, checkpoint)
    slot = checkpoint["world"]["native_host_save"]["slot"]
    modified_ns = native_host_save.slot_path(storage, slot).stat().st_mtime_ns
    local = datetime.fromtimestamp(modified_ns // 1_000_000_000)
    saved_at = f"{local:%Y-%m-%d %H:%M:%S}"
    return CampaignEntry(name, _map_label(checkpoint["world"]["map"]), saved_at, modified_ns,
                         uuid.UUID(checkpoint["campaign_id"]).hex, checkpoint["revision"],
                         checkpoint["world"].get(DELETED) is True)


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


def manage_campaign(campaign: Path, saved: Path, entry: CampaignEntry, action: str, character: str | None,
                    cloud_backups=None) -> tuple[tuple[str, str, str, str], ...]:
    from .service import _MENU_CHECKPOINT_BYTES, _require_resident_host_checkpoint, verify_travel_checkpoint

    selected, checkpoint = _qualified_campaign(campaign, saved, entry.name)
    campaign_id = str(uuid.UUID(entry.campaign_id))
    if (checkpoint["campaign_id"], checkpoint["revision"]) != (campaign_id, entry.revision):
        raise CampaignError("This co-op save has changed.")

    if action == "characters":
        rows = []
        for key, value in checkpoint["characters"].items():
            if value["state"].get(DELETED) is not True:
                guest = checkpoint["guests"][value["guest_id"]]
                rows.append((key, value["name"], guest["name"], guest.get("steam_account", "-")))
        return tuple(sorted(rows, key=lambda row: (row[1].casefold(), row[0]))[:MAX_CHARACTERS])
    if action not in {"delete-save", "delete-character"}:
        raise ValueError("Unknown save management operation")
    storage = native_host_save.checkpoint_profile(saved.parent, checkpoint)
    lock = selected / "save" / ".campaign.lock"
    if os.path.lexists(lock):
        info = lock.lstat()
        if (not stat.S_ISREG(info.st_mode) or lock.resolve(strict=True) != lock
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise CampaignFormatError("The campaign lock is redirected.")

    def verify(data):
        _require_resident_host_checkpoint(saved.parent, data)
        native_host_save.verify_checkpoint(saved.parent, data)

    with CampaignStore(selected / "save", require_existing=True, verify_world=verify_travel_checkpoint,
                       verify_checkpoint=verify, max_read_bytes=_MENU_CHECKPOINT_BYTES) as store:
        if action == "delete-character":
            remaining = store.remove_character(campaign_id, entry.revision, character)
            native_host_save.prune(storage, {record["slot"] for record in native_host_save.references(remaining)})
        else:
            store.require_management_revision(campaign_id, entry.revision)
            _erase_campaign(selected, store, storage)
    if cloud_backups is not None:
        try:
            if action == "delete-save":
                cloud_backups.publish_deletion(entry.campaign_id)
            else:
                cloud_backups.publish(selected)
        except (OSError, CampaignError, ValueError, RuntimeError) as error:
            _LOG.warning("Could not mirror the updated co-op save: %s", error)
    return ()


def _erase_campaign(selected, store, scope):
    from .direct_ip import RESTORED_IDENTITY

    if not isinstance(scope, native_host_save.SaveScope):
        raise CampaignFormatError("Only scoped co-op saves can be deleted.")
    native_dir = native_host_save.directory(scope)
    _directory(native_dir)
    slots = [path for path in native_dir.iterdir()
             if re.fullmatch(r"SFCoop(?:Live)?_[0-9a-f]{32}\.sav", path.name)]
    paths = [store.path, store.previous_path, selected / "save" / RESTORED_IDENTITY, *slots]
    present = []
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISREG(info.st_mode) or path.resolve(strict=True) != path
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise CampaignFormatError("The selected save file is redirected.")
        present.append(path)
    record_deletion(selected.parent, scope.id)
    for path in present:
        path.unlink()


class TitleHostCampaigns:
    """Publish and manage saved campaigns for the exact active Host offer."""

    def __init__(self, bridge: Path, scan: Callable[[], tuple[CampaignEntry, ...]], manage=None, profile_avatars=None):
        self.path = bridge / FILE
        self.request = bridge / REQUEST
        self.response = bridge / RESPONSE
        self._scan = scan
        self._manage = manage
        self._profile_avatars = profile_avatars
        if manage:
            _unlink(self.request)
            _unlink(self.response)
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
            version = 3 if self._manage else 2
            body = f"{version}\t{generation}\t{len(self._entries)}\n"
            for entry in self._entries:
                fields = (entry.name, entry.map_label, entry.saved_at)
                if self._manage:
                    fields += (entry.campaign_id, str(entry.revision), "deleted" if entry.deleted else "active")
                body += "\t".join(fields) + "\n"
            try:
                runtime.write_text(self.path, body, newline="\n")
            except OSError:
                return
            self._published = True

    def contains(self, generation: str, name: str) -> bool:
        return (self._published and generation == self._generation
                and any(entry.name == name and not entry.deleted for entry in self._entries))

    async def service(self, generation: str | None) -> None:
        if self._manage is None or not generation:
            return
        try:
            info = self.request.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 384
                    or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                return
            with self.request.open("rb") as stream:
                payload = stream.read(385)
        except OSError:
            return
        if not _unlink(self.request) or len(payload) > 384:
            return
        try:
            version, offered, request_id, action, name, key, revision, character = payload.decode("ascii").rstrip("\n").split("\t")
        except (UnicodeError, ValueError):
            return
        if version != "1" or offered != generation or not TOKEN.fullmatch(request_id) or not request_id.strip("0"):
            return
        entry = next((entry for entry in self._entries or () if
                      (entry.name, entry.campaign_id, str(entry.revision)) == (name, key, revision)), None)
        status, data = "error", "stale"
        try:
            needs_character = action == "delete-character"
            valid_character = (character == "-" if not needs_character else str(uuid.UUID(character)) == character)
            if entry and self._published and action in _OPERATIONS and valid_character:
                rows = await asyncio.to_thread(self._manage, entry, action, None if character == "-" else character)
                status, data = "ok", str(len(rows))
                data += "".join(f"\n{key}\t{name}\t{creator}\t{account}" for key, name, creator, account in rows)
                if self._profile_avatars is not None and rows:
                    images = await self._profile_avatars({row[3] for row in rows})
                    data += "".join(f"\n{account}\t{image}" for account, image in images.items())
        except StoreBusyError:
            data = "busy"
        except (OSError, CampaignError, ValueError, RuntimeError):
            data = "unavailable"
        if action != "characters":
            self._published = False
            self._entries = None
            _unlink(self.path)
        try:
            runtime.write_text(self.response, f"2\t{generation}\t{request_id}\t{status}\t{data}\n", newline="\n")
        except OSError:
            return

    def close(self) -> None:
        if self._job is not None:
            self._job[1].cancel()
            self._job = None
        self.poll(None)
        if self._manage:
            _unlink(self.request)
            _unlink(self.response)
