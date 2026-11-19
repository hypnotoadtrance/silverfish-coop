"""Host-owned co-op campaign saves and in-memory character leases."""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


SCHEMA_VERSION = 1
DEFAULT_RECONNECT_GRACE = 90.0
_CREDENTIAL_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_DIGEST_RE = re.compile(r"[a-f0-9]{64}")


class CampaignError(RuntimeError):
    """The requested campaign operation could not complete."""


class StoreBusyError(CampaignError):
    """Another host process already owns this campaign directory."""


class CampaignFormatError(CampaignError):
    """A campaign save or candidate value is invalid."""


class CampaignVersionError(CampaignError):
    """The saved campaign uses a different schema version."""


class AuthenticationError(CampaignError):
    """The campaign rejected the supplied guest credential."""


class CharacterError(CampaignError):
    """The selected character is unavailable to this guest."""


class LeaseError(CampaignError):
    """The character is already in use or its lease has expired."""


@dataclass
class _Lease:
    lease_id: str
    guest_id: str
    character_id: str
    grace_seconds: float
    deadline: float | None = None


def _is_uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _name(value: object) -> str:
    if type(value) is not str:
        raise CampaignFormatError("A display name must be text.")
    value = value.strip()
    if not 1 <= len(value) <= 64 or not value.isprintable():
        raise CampaignFormatError("A display name must contain 1 to 64 printable characters.")
    return value


def _validate_json_value(value):
    value_type = type(value)
    if value_type is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise CampaignFormatError("JSON state keys must be strings.")
            _validate_json_value(child)
    elif value_type is list:
        for child in value:
            _validate_json_value(child)
    elif value_type is float:
        if not math.isfinite(value):
            raise CampaignFormatError("JSON state does not support non-finite numbers.")
    elif value_type not in (str, int, bool, type(None)):
        raise CampaignFormatError("JSON state contains a value of an unsupported type.")


def _validate_state(value):
    if type(value) is not dict:
        raise CampaignFormatError("Campaign and character state must be JSON objects.")
    try:
        _validate_json_value(value)
    except RecursionError as exc:
        raise CampaignFormatError("JSON state is cyclic or nested too deeply.") from exc


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise CampaignFormatError("The saved JSON contains a duplicate object member.")
        result[name] = value
    return result


def _valid_grace(seconds) -> float:
    if type(seconds) in (int, float):
        try:
            seconds = float(seconds)
        except OverflowError:
            seconds = math.inf
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    raise CampaignFormatError("Reconnect grace must be a finite, nonnegative number of seconds.")


class CampaignStore:
    """Store one host campaign in a dedicated directory.

    Hold this object for the lifetime of the host process. The directory lock
    prevents a second host from writing the same campaign. Use a context
    manager or call close() to release the lock. Guest IDs, character IDs, and
    state survive a host restart. Connection leases live only in memory.
    """

    def __init__(self, directory, *, clock=time.monotonic, verify_world=None, verify_checkpoint=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "campaign.json"
        self.previous_path = self.directory / "campaign.previous.json"
        self._clock = clock
        self._verify_world = verify_world
        self._verify_checkpoint = verify_checkpoint
        self._mutex = threading.RLock()
        self._lease_by_id: dict[str, _Lease] = {}
        self._lease_by_guest: dict[str, _Lease] = {}
        self._lock_file = self._lock_directory()
        self.recovered = False
        try:
            self._data = self._load_or_create()
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        self._require_open()
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._mutex:
            if self._lock_file is not None:
                self._lock_file.close()
                self._lock_file = None

    def _require_open(self):
        if self._lock_file is None:
            raise CampaignError("The campaign store is closed.")

    def _lock_directory(self):
        handle = (self.directory / ".campaign.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise StoreBusyError("This campaign is already open by another host.") from exc
            raise
        return handle

    def _load_or_create(self):
        invalid_primary = None
        try:
            return self._read_verified(self.path)
        except FileNotFoundError:
            pass
        except CampaignFormatError as exc:
            invalid_primary = exc

        try:
            data = self._read_verified(self.previous_path)
        except FileNotFoundError:
            if invalid_primary is not None:
                raise invalid_primary
            data = {
                "schema_version": SCHEMA_VERSION,
                "campaign_id": str(uuid.uuid4()),
                "revision": 0,
                "world": {},
                "guests": {},
                "characters": {},
            }
        else:
            self.recovered = True

        self._write(self.path, data)
        return data

    def _read_verified(self, path):
        data = self._read(path)
        if self._verify_world is not None:
            self._verify_world(data["world"])
        if self._verify_checkpoint is not None:
            self._verify_checkpoint(data)
        return data

    @staticmethod
    def _read(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        except (ValueError, RecursionError) as exc:
            raise CampaignFormatError(f"Invalid campaign JSON in {path.name}.") from exc
        CampaignStore._validate(data)
        return data

    @staticmethod
    def _validate(data):
        if type(data) is not dict or type(data.get("schema_version")) is not int:
            raise CampaignFormatError("The campaign schema version is missing or invalid.")
        if data["schema_version"] != SCHEMA_VERSION:
            raise CampaignVersionError(f"Unsupported campaign schema {data['schema_version']}.")
        if set(data) != {"schema_version", "campaign_id", "revision", "world", "guests", "characters"}:
            raise CampaignFormatError("The campaign save fields do not match the schema.")
        if not _is_uuid(data["campaign_id"]) or type(data["revision"]) is not int or data["revision"] < 0:
            raise CampaignFormatError("The campaign ID or revision is invalid.")
        _validate_state(data["world"])
        guests = data["guests"]
        characters = data["characters"]
        if type(guests) is not dict or type(characters) is not dict:
            raise CampaignFormatError("Campaign guests and characters must be maps.")
        for guest_id, guest in guests.items():
            if not _is_uuid(guest_id) or type(guest) is not dict or set(guest) != {"name", "credential_sha256"}:
                raise CampaignFormatError("The campaign contains an invalid guest record.")
            if _name(guest["name"]) != guest["name"]:
                raise CampaignFormatError("A guest display name is not in canonical form.")
            digest = guest["credential_sha256"]
            if type(digest) is not str or not _DIGEST_RE.fullmatch(digest):
                raise CampaignFormatError("A guest credential digest is invalid.")
        for character_id, character in characters.items():
            if (
                not _is_uuid(character_id)
                or type(character) is not dict
                or set(character) != {"guest_id", "name", "state"}
            ):
                raise CampaignFormatError("The campaign contains an invalid character record.")
            if not _is_uuid(character["guest_id"]) or character["guest_id"] not in guests:
                raise CampaignFormatError("A character does not have a valid owner.")
            if _name(character["name"]) != character["name"]:
                raise CampaignFormatError("A character display name is not in canonical form.")
            _validate_state(character["state"])

    @staticmethod
    def _write(path, data):
        try:
            body = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
        except (ValueError, RecursionError) as exc:
            raise CampaignFormatError("Campaign state cannot be serialized as JSON.") from exc

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False,
            ) as temp:
                temp_path = Path(temp.name)
                temp.write(body)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _commit(self, candidate):
        self._require_open()
        candidate["revision"] = self._data["revision"] + 1
        self._validate(candidate)
        self._write(self.previous_path, self._data)
        self._write(self.path, candidate)
        self._data = candidate
        return candidate["revision"]

    @staticmethod
    def _add_guest(candidate, name):
        guest_id = str(uuid.uuid4())
        credential = secrets.token_urlsafe(32)
        candidate["guests"][guest_id] = {
            "name": name,
            "credential_sha256": hashlib.sha256(credential.encode("ascii")).hexdigest(),
        }
        return {"guest_id": guest_id, "credential": credential}

    @staticmethod
    def _add_character(candidate, guest_id, name, state, character_id=None):
        character_id = character_id or str(uuid.uuid4())
        candidate["characters"][character_id] = {
            "guest_id": guest_id,
            "name": name,
            "state": copy.deepcopy(state),
        }
        return character_id

    def issue_guest(self, name):
        """Issue a campaign guest credential. The cleartext is returned once."""
        with self._mutex:
            self._require_open()
            name = _name(name)
            candidate = copy.deepcopy(self._data)
            identity = self._add_guest(candidate, name)
            self._commit(candidate)
            return identity

    def issue_invitation(self, guest_name, character_name):
        """Create a guest and a starter character in the same save revision."""
        with self._mutex:
            self._require_open()
            guest_name = _name(guest_name)
            character_name = _name(character_name)
            candidate = copy.deepcopy(self._data)
            identity = self._add_guest(candidate, guest_name)
            character_id = self._add_character(candidate, identity["guest_id"], character_name, {})
            self._commit(candidate)
            return {**identity, "character_id": character_id}

    def _authenticate(self, guest_id, credential):
        self._require_open()
        if not _is_uuid(guest_id) or type(credential) is not str or not _CREDENTIAL_RE.fullmatch(credential):
            raise AuthenticationError("Campaign guest authentication failed.")
        guest = self._data["guests"].get(guest_id)
        expected = guest["credential_sha256"] if guest is not None else "0" * 64
        supplied = hashlib.sha256(credential.encode("ascii")).hexdigest()
        if not secrets.compare_digest(supplied, expected) or guest is None:
            raise AuthenticationError("Campaign guest authentication failed.")

    def _public_character(self, character_id):
        return {"character_id": character_id, **copy.deepcopy(self._data["characters"][character_id])}

    def linked_character_catalog(self, guest_id):
        """Only for an application session already authenticated by this store."""
        with self._mutex:
            self._require_open()
            if guest_id not in self._data["guests"]:
                raise AuthenticationError("Unknown campaign guest.")
            return [
                {"character_id": character_id, "name": character["name"]}
                for character_id, character in self._data["characters"].items()
                if character["guest_id"] == guest_id
            ]

    def create_linked_character(self, guest_id, name, operation_id):
        """Create once for the owner of an authenticated application session."""
        if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise CharacterError("Invalid character creation operation ID.")
        with self._mutex:
            self._require_open()
            if guest_id not in self._data["guests"]:
                raise AuthenticationError("Unknown campaign guest.")
            name = _name(name)
            character_id = str(uuid.uuid5(uuid.UUID(guest_id), operation_id))
            existing = self._data["characters"].get(character_id)
            if existing is not None:
                if existing["guest_id"] != guest_id or existing["name"] != name:
                    raise CharacterError("Character creation operation ID is already in use.")
            else:
                candidate = copy.deepcopy(self._data)
                self._add_character(candidate, guest_id, name, {}, character_id)
                self._commit(candidate)
            return {"character_id": character_id, "name": name}

    def list_characters(self, guest_id, credential):
        """Return only the authenticated guest's persistent characters."""
        with self._mutex:
            self._authenticate(guest_id, credential)
            characters = self._data["characters"]
            return [
                self._public_character(character_id)
                for character_id, character in characters.items()
                if character["guest_id"] == guest_id
            ]

    def create_character(self, guest_id, credential, name, state=None):
        """Persist a new character for an authenticated guest."""
        with self._mutex:
            self._authenticate(guest_id, credential)
            name = _name(name)
            state = {} if state is None else state
            _validate_state(state)
            candidate = copy.deepcopy(self._data)
            character_id = self._add_character(candidate, guest_id, name, state)
            self._commit(candidate)
            return self._public_character(character_id)

    def _drop_lease(self, lease):
        del self._lease_by_id[lease.lease_id]
        del self._lease_by_guest[lease.guest_id]

    def _expire_leases(self, now):
        for lease in tuple(self._lease_by_id.values()):
            if lease.deadline is not None and lease.deadline <= now:
                self._drop_lease(lease)

    def _get_lease(self, lease_id):
        lease = self._lease_by_id.get(lease_id) if type(lease_id) is str else None
        if lease is None:
            raise LeaseError("The character lease is unavailable or expired.")
        return lease

    def activate(self, guest_id, credential, character_id, *, grace_seconds=DEFAULT_RECONNECT_GRACE):
        """Activate a saved character or reclaim its reserved connection lease."""
        with self._mutex:
            self._authenticate(guest_id, credential)
            grace_seconds = _valid_grace(grace_seconds)
            character = self._data["characters"].get(character_id) if _is_uuid(character_id) else None
            if character is None or character["guest_id"] != guest_id:
                raise CharacterError("The selected character is unavailable to this guest.")

            self._expire_leases(self._clock())
            lease = self._lease_by_guest.get(guest_id)
            resumed = lease is not None
            if resumed:
                if lease.deadline is None or lease.character_id != character_id:
                    raise LeaseError("This guest already has an active or reserved character.")
                lease.deadline = None
            else:
                lease = _Lease(secrets.token_urlsafe(24), guest_id, character_id, grace_seconds)
                self._lease_by_id[lease.lease_id] = lease
                self._lease_by_guest[guest_id] = lease

            return {
                "lease_id": lease.lease_id,
                "character": self._public_character(character_id),
                "resumed": resumed,
            }

    def disconnect(self, lease_id, grace_seconds=None):
        """Reserve a disconnected character. Repeated notices keep the same deadline."""
        with self._mutex:
            self._require_open()
            now = self._clock()
            self._expire_leases(now)
            lease = self._get_lease(lease_id)
            duration = lease.grace_seconds if grace_seconds is None else _valid_grace(grace_seconds)
            if lease.deadline is None:
                lease.deadline = now + duration
            self._expire_leases(now)

    def release(self, lease_id):
        """End a character lease after an orderly departure."""
        with self._mutex:
            self._require_open()
            self._expire_leases(self._clock())
            self._drop_lease(self._get_lease(lease_id))

    def _checkpoint(self, world, character_states):
        self._require_open()
        _validate_state(world)
        if type(character_states) is not dict:
            raise CampaignFormatError("Character snapshots must be keyed by lease ID.")
        self._expire_leases(self._clock())
        candidate = copy.deepcopy(self._data)
        candidate["world"] = copy.deepcopy(world)
        for lease_id, state in character_states.items():
            lease = self._get_lease(lease_id)
            _validate_state(state)
            candidate["characters"][lease.character_id]["state"] = copy.deepcopy(state)
        return self._commit(candidate)

    def checkpoint(self, world, character_states):
        """Save world state and leased character snapshots in a single revision."""
        with self._mutex:
            return self._checkpoint(world, character_states)

    def save_character(self, lease_id, state):
        """Save a connected or grace-reserved character and return the revision."""
        with self._mutex:
            if type(lease_id) is not str:
                raise LeaseError("The character lease is unavailable or expired.")
            return self._checkpoint(self._data["world"], {lease_id: state})

    def save_world(self, state):
        """Save world state and return the revision."""
        return self.checkpoint(state, {})

    def status(self):
        """Return campaign and roster data for trusted local inspection."""
        with self._mutex:
            self._require_open()
            now = self._clock()
            self._expire_leases(now)
            return {
                "schema_version": SCHEMA_VERSION,
                "campaign_id": self._data["campaign_id"],
                "revision": self._data["revision"],
                "recovered": self.recovered,
                "world": copy.deepcopy(self._data["world"]),
                "guest_count": len(self._data["guests"]),
                "characters": [
                    self._public_character(character_id) for character_id in self._data["characters"]
                ],
                "leases": [
                    {
                        "guest_id": lease.guest_id,
                        "character_id": lease.character_id,
                        "state": "active" if lease.deadline is None else "reserved",
                        "grace_remaining": None if lease.deadline is None else max(0.0, lease.deadline - now),
                    }
                    for lease in self._lease_by_id.values()
                ],
            }
