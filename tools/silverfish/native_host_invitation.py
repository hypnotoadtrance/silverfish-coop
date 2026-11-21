"""Publish one private invitation export from the active host's game form."""

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time
from uuid import UUID

from .campaign import _CREDENTIAL_RE
from .embedded_engine import EmbeddedEngine, _assert_no_reparse, _exact_existing, owns_session_bridge
from .embedded_startup import INVITE_LIMIT, _read_file
from .title_handoff import TOKEN


OFFER_FILE = "host-invite-offer.tsv"
REQUEST_FILE = "host-invite-request.tsv"
RESPONSE_FILE = "host-invite-response.tsv"
OFFER_SECONDS = 4
MAX_OPERATIONS = 256
_REQUEST_LIMIT = 1024
_ENVELOPE = re.compile(rb"1\t([0-9a-f]{32})\t([0-9a-f]{32})\t([0-9a-f]{32})\t")
_DNS_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_ADDRESS_TEXT = re.compile(r"[A-Za-z0-9.:%_-]{1,253}")
_BUILD = re.compile(r"[a-f0-9]{64}")
_ADDRESS_ERROR = "Supply a reachable host address for the invitation"


class _Refusal(ValueError):
    """An error code safe to send to the form."""


def _token(value) -> bool:
    return isinstance(value, str) and value != "0" * 32 and TOKEN.fullmatch(value) is not None


def _uuid(value) -> bool:
    try:
        return isinstance(value, str) and len(value) == 36 and str(UUID(value)) == value
    except ValueError:
        return False


def _address(value):
    if not isinstance(value, str) or not _ADDRESS_TEXT.fullmatch(value):
        raise _Refusal("invalid_address")
    try:
        address = ip_address(value)
    except ValueError:
        if not all(_DNS_LABEL.fullmatch(label) for label in value.removesuffix(".").split(".")):
            raise _Refusal("invalid_address") from None
    else:
        if address.is_unspecified or address.is_multicast:
            raise _Refusal("invalid_address")
    return value


def _fields(raw):
    if not raw.endswith(b"\n"):
        raise _Refusal("invalid_request")
    fields = raw[:-1].split(b"\t")
    if len(fields) != 3 or any(len(value) > bound for value, bound in zip(fields, (256, 256, 253), strict=True)):
        raise _Refusal("invalid_request")
    try:
        name, character, address = (value.decode("utf-8") for value in fields)
    except UnicodeError:
        raise _Refusal("invalid_request") from None
    if any(not value.isprintable() or not 1 <= len(value.strip()) <= 64 for value in (name, character)):
        raise _Refusal("invalid_request")
    return name.strip(), character.strip(), _address(address) if address else None


def _remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _atomic_write(path, payload, *, exclusive=False):
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".host-invite-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            if exclusive:
                stream.flush()
                os.fsync(stream.fileno())
        if not exclusive:
            temporary.replace(path)
        elif os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)
    finally:
        if temporary is not None:
            _remove(temporary)


def _ordinary_directory(path):
    _assert_no_reparse(path.lstat())
    return _exact_existing(path)


@dataclass
class _Operation:
    scope: tuple[str, str]
    fingerprint: tuple[str, str, str | None] = field(repr=False)
    error: str = "invite_unavailable"
    file: Path | None = field(default=None, repr=False)
    identities: tuple[str, str] | None = None
    digest: str | None = field(default=None, repr=False)


class HostInvitationMailbox:
    """Keep form receipts separate from the host's authoritative invitation files.

    Poll this from the serialized host loop. The caller qualifies the current
    local host and supplies its exact control generation and zone epoch.
    """

    def __init__(self, bridge: Path, campaign_run: Path, issue: Callable[[dict], dict], *,
                 engine: EmbeddedEngine | None = None, clock=time.time):
        self._bridge, self._run = _ordinary_directory(bridge), _ordinary_directory(campaign_run)
        if not owns_session_bridge(bridge, campaign_run, engine) or not _token(bridge.name):
            raise ValueError("The invitation mailbox must belong to the current campaign bridge")
        _ordinary_directory(bridge.parent)
        if not callable(issue):
            raise ValueError("Expected a host invitation issue callback")
        self._issue, self._clock = issue, clock
        self._bridge_status = bridge.stat()
        self.offer_path, self.request_path, self.response_path = (bridge / path for path in (OFFER_FILE, REQUEST_FILE, RESPONSE_FILE))
        self._invites = campaign_run / "invites"
        self._scope = None
        self._expires = 0
        self._operations = {}
        self._drafts, self._retired = {}, {}
        self._outbox = None
        self._closed = False

    def _same_bridge(self):
        try:
            return os.path.samestat(_ordinary_directory(self._bridge).stat(), self._bridge_status)
        except (OSError, ValueError):
            return False

    def _prune(self):
        for path, original in tuple(self._retired.items()):
            try:
                current = path.lstat()
            except FileNotFoundError:
                self._retired.pop(path)
            except OSError:
                continue
            else:
                if not os.path.samestat(current, original) or _remove(path):
                    self._retired.pop(path)

    def _disarm(self):
        self._scope, self._expires, self._outbox = None, 0, None
        self._retired.update(self._drafts)
        self._drafts.clear()
        self._prune()
        _remove(self.offer_path)
        _remove(self.response_path)
        _remove(self.request_path)

    def _offer(self, scope, now):
        expires = now + OFFER_SECONDS
        generation, epoch = scope
        try:
            _atomic_write(self.offer_path, f"1\t{generation}\t{epoch}\t{expires}\n".encode("ascii"))
        except OSError:
            return False
        self._scope, self._expires = scope, expires
        return True

    def _reply(self, operation, code, *, exported=False):
        generation, epoch = self._scope
        status = "ok" if exported else "error"
        self._outbox = f"1\t{generation}\t{epoch}\t{operation}\t{status}\t{code}\n".encode("ascii")

    def _flush(self):
        if self._outbox is not None:
            try:
                _atomic_write(self.response_path, self._outbox)
            except OSError:
                return False
            self._outbox = None
        return True

    def _prepare(self, record):
        name, character, address = record.fingerprint
        try:
            if os.path.lexists(self._invites):
                _ordinary_directory(self._invites)
        except (OSError, ValueError):
            record.error = "export_unverified"
            return
        try:
            result = self._issue({"name": name, "character": character, "address": address})
        except (OSError, RuntimeError, ValueError) as error:
            if isinstance(error, ValueError) and str(error) == _ADDRESS_ERROR:
                record.error = "invalid_address"
            return
        record.error = "export_unverified"
        if type(result) is not dict or result.keys() != {"invite_file", "guest_id", "character_id"}:
            return
        guest, starter = result["guest_id"], result["character_id"]
        if not _uuid(guest) or not _uuid(starter) or not isinstance(result["invite_file"], str):
            return
        expected = self._invites / (guest + ".invite.json")
        if Path(result["invite_file"]) != expected:
            return
        record.file, record.identities = expected, (guest, starter)

    def _source_bytes(self, record):
        try:
            _ordinary_directory(self._invites)
            data = _read_file(record.file, INVITE_LIMIT)
            digest = sha256(data).hexdigest()
            if record.digest is not None:
                if digest != record.digest:
                    raise _Refusal("export_changed")
                return data
            invite = json.loads(data.decode("utf-8"))
            address = record.fingerprint[2]
            if (type(invite) is not dict or type(invite.get("schema")) is not int or invite["schema"] != 1
                    or (invite.get("guest_id"), invite.get("character_id")) != record.identities
                    or type(invite.get("port")) is not int or not 1 <= invite["port"] <= 65535
                    or not isinstance(invite.get("credential"), str) or not _CREDENTIAL_RE.fullmatch(invite["credential"])
                    or not isinstance(invite.get("certificate"), str) or not invite["certificate"]
                    or not isinstance(invite.get("game_build"), str) or not _BUILD.fullmatch(invite["game_build"])
                    or type(invite.get("protocol_version")) is not int or invite["protocol_version"] < 1
                    or not _address(invite.get("host")) or address is not None and invite["host"] != address):
                raise _Refusal("export_unverified")
        except (OSError, ValueError, RecursionError):
            raise _Refusal("export_changed" if record.digest is not None else "export_unverified") from None
        record.digest = digest
        return data

    def _export(self, operation, record):
        try:
            data = self._source_bytes(record)
        except _Refusal as error:
            return self._reply(operation, str(error))
        path = self._bridge / f"host-invite-{operation}.invite.json"
        try:
            if path in self._drafts:
                try:
                    if not os.path.samestat(path.lstat(), self._drafts[path]) or _read_file(path, INVITE_LIMIT) != data:
                        return self._reply(operation, "export_changed")
                except FileNotFoundError:
                    self._drafts.pop(path)
            if path not in self._drafts:
                _atomic_write(path, data, exclusive=True)
                self._drafts[path] = path.lstat()
            if _read_file(path, INVITE_LIMIT) != data:
                return self._reply(operation, "export_unverified")
        except (OSError, ValueError):
            return self._reply(operation, "export_unverified")
        self._reply(operation, "exported", exported=True)

    def _consume(self):
        try:
            request = _read_file(self.request_path, _REQUEST_LIMIT)
        except (FileNotFoundError, PermissionError):
            return
        except (OSError, ValueError):
            _remove(self.request_path)
            return
        envelope = _ENVELOPE.match(request)
        if envelope is None:
            _remove(self.request_path)
            return
        generation, epoch, operation = (part.decode("ascii") for part in envelope.groups())
        if (generation, epoch) != self._scope or not _token(operation):
            _remove(self.request_path)
            return
        # A replay reuses the operation ID. The form waits until this request
        # disappears, so remove the previous response before dropping the fence.
        if not _remove(self.response_path) or not _remove(self.request_path):
            return
        try:
            fields = _fields(request[envelope.end():])
        except _Refusal as error:
            return self._reply(operation, str(error))
        record = self._operations.get(operation)
        if record is None:
            if len(self._operations) >= MAX_OPERATIONS:
                return self._reply(operation, "operation_limit")
            record = self._operations[operation] = _Operation(self._scope, fields)
            self._prepare(record)
        elif (record.scope, record.fingerprint) != (self._scope, fields):
            return self._reply(operation, "operation_conflict")
        if record.file is None:
            return self._reply(operation, record.error)
        self._export(operation, record)

    def poll(self, generation: str | None, epoch: str | None, active: bool) -> None:
        """Refresh the current host offer and handle at most one form request."""
        if self._closed or not self._same_bridge():
            return
        if active is not True:
            self._disarm()
            return
        if not _token(generation) or not _token(epoch):
            self._disarm()
            raise ValueError("Invalid host invitation offer identity")
        scope, now = (generation, epoch), int(self._clock())
        if scope != self._scope:
            self._disarm()
            self._offer(scope, now)
            return
        self._prune()
        if not 0 <= self._expires - now <= OFFER_SECONDS:
            if not _remove(self.request_path):
                return
            _remove(self.response_path)
            self._outbox = None
            self._offer(scope, now)
            return
        if self._expires - now <= OFFER_SECONDS // 2:
            self._offer(scope, now)
        if not self._flush():
            return
        self._consume()
        self._flush()

    def close(self):
        """Unpublish the offer and only the ephemeral copies this mailbox wrote."""
        self._closed = True
        if self._same_bridge():
            self._disarm()
        self._operations.clear()
