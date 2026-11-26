"""Mirror complete, committed host campaigns through Silverfish's Steam Auto-Cloud rule."""

import asyncio
from hashlib import sha256
from itertools import islice
import json
import logging
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import zlib

from . import direct_ip, link, native_host_save
from .campaign import CampaignError, CampaignFormatError, CampaignStore


FOLDER = "SilverfishCoopCloud"
_ARCHIVE = re.compile(r"SFCoopBackup-([0-9a-f]{32})\.sav\Z")
_CHECKPOINT = "campaign.json"
_DELETION = "deleted-campaign"
_CAMPAIGN_KEY = re.compile(r"[0-9a-f]{32}\Z")
_IDENTITY_LIMIT = 4096
_BACKUP_INTERVAL = 20
_MAX_CAMPAIGNS = 256
_ERRORS = (OSError, CampaignError, BadZipFile, ValueError, OverflowError, EOFError, NotImplementedError, zlib.error)
_LOG = logging.getLogger(__name__)


def _exact(path, file_type):
    info = path.lstat()
    if (not file_type(info.st_mode) or path.resolve(strict=True) != path.absolute()
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise CampaignFormatError("A co-op cloud backup path is redirected or has an unexpected file type.")
    return info


def _campaign_hex(checkpoint):
    return checkpoint["campaign_id"].replace("-", "")


def deletion_marker(campaigns, campaign_id):
    if not _CAMPAIGN_KEY.fullmatch(campaign_id):
        raise CampaignFormatError("Invalid deleted campaign identity.")
    return campaigns.parent / "deleted-campaigns" / campaign_id


def deleted_campaign(campaigns, campaign_id):
    marker = deletion_marker(campaigns, campaign_id)
    try:
        _exact(marker.parent, stat.S_ISDIR)
        return _exact(marker, stat.S_ISREG).st_size == 0
    except FileNotFoundError:
        return False


def record_deletion(campaigns, campaign_id):
    marker = deletion_marker(campaigns, campaign_id)
    _exact(marker.parent.parent, stat.S_ISDIR)
    marker.parent.mkdir(exist_ok=True)
    _exact(marker.parent, stat.S_ISDIR)
    if not deleted_campaign(campaigns, campaign_id):
        with marker.open("xb"):
            pass


def _contains_deletion(archive, campaign_id):
    members = archive.infolist()
    return (len(members) == 1 and members[0].filename == _DELETION
            and members[0].file_size == 32 and archive.read(members[0]) == campaign_id.encode("ascii"))


def _slots(profile, checkpoint):
    from .service import _require_resident_host_checkpoint, verify_travel_checkpoint

    _require_resident_host_checkpoint(profile, checkpoint)
    verify_travel_checkpoint(checkpoint["world"])
    slots = {}
    for record in native_host_save.references(checkpoint):
        slot = record.get("slot")
        digest = record.get("sha256")
        if (not isinstance(slot, str) or not native_host_save.SLOT.fullmatch(slot)
                or not isinstance(digest, str) or not native_host_save.SHA256.fullmatch(digest)):
            raise CampaignFormatError("Invalid stock save generation in the co-op backup.")
        frame = (record["size"], digest)
        if slots.setdefault(f"{slot}.sav", frame) != frame:
            raise CampaignFormatError("Conflicting stock save references in the co-op backup.")
    return slots


def _verified(data, frame):
    return (len(data) == frame[0] and data.startswith(b"GVAS") and sha256(data).hexdigest() == frame[1])


def _check_identity(identity):
    if (not isinstance(identity, dict) or set(identity) != {"certificate", "private_key"}
            or any(type(value) is not str for value in identity.values())):
        raise CampaignFormatError("Invalid co-op host identity in the cloud backup.")
    try:
        link.load_server_identity(identity["certificate"], identity["private_key"])
    except link.LinkError as error:
        raise CampaignFormatError("Invalid co-op host identity in the cloud backup.") from error


def _read_archive(archive, path, profile):
    from .service import _MENU_CHECKPOINT_BYTES, _MENU_NATIVE_SAVE_BYTES

    limit = _MENU_CHECKPOINT_BYTES + _MENU_NATIVE_SAVE_BYTES + _IDENTITY_LIMIT
    members = archive.infolist()
    if (len(members) > 4096 or sum(member.file_size for member in members) > limit
            or any(member.flag_bits & 1 for member in members)):
        raise CampaignFormatError("Co-op backup exceeds the supported size.")
    names = {member.filename: member.file_size for member in members}
    if len(names) != len(members) or not 0 < names.get(_CHECKPOINT, 0) <= _MENU_CHECKPOINT_BYTES:
        raise CampaignFormatError("Invalid co-op backup contents.")
    checkpoint = CampaignStore.decode(archive.read(_CHECKPOINT))
    if path.name != f"SFCoopBackup-{_campaign_hex(checkpoint)}.sav" or checkpoint["revision"] == 0:
        raise CampaignFormatError("The co-op backup filename does not match its campaign.")
    slots = _slots(profile, checkpoint)
    identity_name = direct_ip.RESTORED_IDENTITY
    identity_size = names.get(identity_name, 0)
    expected = {_CHECKPOINT: names[_CHECKPOINT], identity_name: identity_size,
                **{name: size for name, (size, _) in slots.items()}}
    if names != expected or not 0 < identity_size <= _IDENTITY_LIMIT:
        raise CampaignFormatError("The co-op backup is missing a referenced save or contains extra files.")
    identity = json.loads(archive.read(identity_name))
    _check_identity(identity)
    return checkpoint, slots, identity


def _open_backup(path):
    from .service import _MENU_CHECKPOINT_BYTES, _MENU_NATIVE_SAVE_BYTES

    # Allow the ZIP index and headers in addition to the checkpoint and save data.
    if _exact(path, stat.S_ISREG).st_size > _MENU_CHECKPOINT_BYTES + _MENU_NATIVE_SAVE_BYTES + 1024**2:
        raise CampaignFormatError("Co-op backup exceeds the supported size.")
    return ZipFile(path)


def _install_slot(path, data):
    temp = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


class CloudBackups:
    def __init__(self, campaigns: Path, profile: Path):
        self.campaigns = campaigns
        self.profile = profile
        self.root = profile / "Saved" / "SaveGames"
        self.directory = self.root / FOLDER

    def publish_deletion(self, campaign_id):
        """Replace the full cloud archive with its campaign ID alone."""
        if not deleted_campaign(self.campaigns, campaign_id):
            raise CampaignFormatError("The local co-op deletion is missing.")
        for directory in (self.profile, self.root.parent, self.root):
            _exact(directory, stat.S_ISDIR)
        self.directory.mkdir(exist_ok=True)
        _exact(self.directory, stat.S_ISDIR)
        path = self.directory / f"SFCoopBackup-{campaign_id}.sav"
        if os.path.lexists(path):
            _exact(path, stat.S_ISREG)
        temp = None
        try:
            with NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                with ZipFile(stream, "w") as archive:
                    archive.writestr(_DELETION, campaign_id)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def publish(self, campaign: Path) -> bool:
        """Publish a snapshot only after its checkpoint and every generation verify."""
        from .service import menu_host_checkpoint

        if campaign.parent != self.campaigns:
            raise CampaignFormatError("The backup source is outside the host campaign directory.")
        for directory in (self.campaigns, campaign, campaign / "save"):
            _exact(directory, stat.S_ISDIR)
        checkpoint = menu_host_checkpoint(campaign, profile=self.profile, require_scoped=True)
        if checkpoint is None:
            return False
        campaign_id = _campaign_hex(checkpoint)
        if deleted_campaign(self.campaigns, campaign_id):
            return False
        storage = native_host_save.checkpoint_profile(self.profile, checkpoint)
        for directory in (self.profile, self.root.parent, self.root):
            _exact(directory, stat.S_ISDIR)
        self.directory.mkdir(exist_ok=True)
        _exact(self.directory, stat.S_ISDIR)
        path = self.directory / f"SFCoopBackup-{campaign_id}.sav"
        try:
            with _open_backup(path) as present:
                if _contains_deletion(present, campaign_id):
                    record_deletion(self.campaigns, campaign_id)
                    return False
                remote, _, _ = _read_archive(present, path, self.profile)
            if remote["revision"] >= checkpoint["revision"]:
                return False
        except FileNotFoundError:
            pass
        slots = _slots(self.profile, checkpoint)
        identity = direct_ip.read_resident_identity(self.campaigns.parent, campaign)
        _check_identity(identity)
        temp = None
        try:
            with NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                temp = Path(stream.name)
                with ZipFile(stream, "w", compression=ZIP_DEFLATED, compresslevel=3) as archive:
                    archive.writestr(_CHECKPOINT, json.dumps(checkpoint, allow_nan=False))
                    archive.writestr(direct_ip.RESTORED_IDENTITY, json.dumps(identity, allow_nan=False))
                    for name, frame in slots.items():
                        source = native_host_save.slot_path(storage, name.removesuffix(".sav"))
                        if _exact(source, stat.S_ISREG).st_size != frame[0]:
                            raise CampaignFormatError("A stock save generation changed during cloud backup.")
                        data = source.read_bytes()
                        if not _verified(data, frame):
                            raise CampaignFormatError("A stock save generation changed during cloud backup.")
                        archive.writestr(name, data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        return True

    def _local_campaigns(self):
        from .service import _MENU_CHECKPOINT_BYTES

        by_id = {}
        with os.scandir(self.campaigns) as entries:
            local = list(islice(entries, _MAX_CAMPAIGNS + 1))
        if len(local) > _MAX_CAMPAIGNS:
            raise CampaignFormatError("Too many local campaigns to compare cloud backup revisions safely.")
        for entry in local:
            directory = Path(entry.path)
            try:
                _exact(directory, stat.S_ISDIR)
                _exact(directory / "save", stat.S_ISDIR)
            except _ERRORS:
                continue
            for name in (_CHECKPOINT, "campaign.previous.json"):
                try:
                    checkpoint = CampaignStore._read(directory / "save" / name, max_read_bytes=_MENU_CHECKPOINT_BYTES)
                except _ERRORS:
                    continue
                by_id.setdefault(checkpoint["campaign_id"], set()).add(directory)
        return by_id

    def restore(self) -> None:
        """Import complete, newer campaigns before the Host load menu opens."""
        try:
            for directory in (self.profile, self.root.parent, self.root, self.directory):
                _exact(directory, stat.S_ISDIR)
        except FileNotFoundError:
            return
        except _ERRORS as error:
            _LOG.warning("Could not inspect co-op backups: %s", error)
            return
        try:
            self.campaigns.mkdir(exist_ok=True)
            _exact(self.campaigns, stat.S_ISDIR)
            local = self._local_campaigns()
            with os.scandir(self.directory) as entries:
                backups = [Path(entry.path) for entry in islice(entries, _MAX_CAMPAIGNS) if _ARCHIVE.fullmatch(entry.name)]
        except _ERRORS as error:
            _LOG.warning("Could not compare local campaigns to co-op backups: %s", error)
            return
        for path in backups:
            try:
                campaign_id = _ARCHIVE.fullmatch(path.name)[1]
                with _open_backup(path) as archive:
                    removed = _contains_deletion(archive, campaign_id)
                    if removed:
                        record_deletion(self.campaigns, campaign_id)
                        continue
                    if deleted_campaign(self.campaigns, campaign_id):
                        continue
                    checkpoint, slots, identity = _read_archive(archive, path, self.profile)
                    matches = local.get(checkpoint["campaign_id"], ())
                    if len(matches) > 1:
                        continue
                    directory = next(iter(matches), self.campaigns / f"campaign-{_campaign_hex(checkpoint)}")
                    self._restore(directory, checkpoint, archive, slots, identity)
            except _ERRORS as error:
                _LOG.warning("Could not restore co-op backup %s: %s", path.name, error)

    def _restore(self, directory, checkpoint, archive, slots, identity):
        from .service import _MENU_CHECKPOINT_BYTES

        save = directory / "save"
        for branch in (directory, save):
            branch.mkdir(exist_ok=True)
            _exact(branch, stat.S_ISDIR)
        lock = save / ".campaign.lock"
        if os.path.lexists(lock):
            _exact(lock, stat.S_ISREG)
        storage = native_host_save.checkpoint_profile(self.profile, checkpoint)

        def install():
            identity_path = save / direct_ip.RESTORED_IDENTITY
            existing = direct_ip.read_resident_identity(self.campaigns.parent, directory)
            if (existing is not None and existing != identity
                    and any((save / name).exists() for name in (_CHECKPOINT, "campaign.previous.json"))):
                raise CampaignFormatError("The newer cloud backup uses a different local campaign identity.")
            native_dir = native_host_save.directory(storage)
            for branch in (native_dir.parent, native_dir):
                branch.mkdir(exist_ok=True)
                _exact(branch, stat.S_ISDIR)
            for name, frame in slots.items():
                data = archive.read(name)
                if not _verified(data, frame):
                    raise CampaignFormatError("A cloud backup contains a corrupt stock save generation.")
                path = native_host_save.slot_path(storage, name.removesuffix(".sav"))
                try:
                    _install_slot(path, data)
                except FileExistsError:
                    if _exact(path, stat.S_ISREG).st_size != frame[0] or not _verified(path.read_bytes(), frame):
                        raise CampaignFormatError("The cloud backup conflicts with a local stock save generation.") from None
            native_host_save.verify_checkpoint(self.profile, checkpoint)
            if not identity_path.exists():
                _install_slot(identity_path, json.dumps(identity, allow_nan=False).encode("ascii"))
            elif existing != identity:
                raise CampaignFormatError("The cloud backup conflicts with a saved host identity.")

        return CampaignStore.restore_backup(save, checkpoint, install, max_read_bytes=_MENU_CHECKPOINT_BYTES)

    async def mirror_host(self, campaign, session):
        """Keep a recent durable backup and flush the final checkpoint on a clean stop."""
        finished = asyncio.Event()

        def publish():
            try:
                self.publish(campaign)
            except FileNotFoundError:
                pass
            except _ERRORS as error:
                _LOG.warning("Could not back up hosted co-op campaign: %s", error)

        async def mirror():
            while not finished.is_set():
                await asyncio.to_thread(publish)
                try:
                    await asyncio.wait_for(finished.wait(), _BACKUP_INTERVAL)
                except TimeoutError:
                    continue
            await asyncio.to_thread(publish)

        task = asyncio.create_task(mirror())
        try:
            await session
        finally:
            finished.set()
            await task
