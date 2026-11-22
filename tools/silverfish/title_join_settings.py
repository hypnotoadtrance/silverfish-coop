"""Keep invitation custody and authenticated character drafts private to one title."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
import secrets
from tempfile import NamedTemporaryFile
from uuid import UUID

from . import direct_ip, link
from .embedded_engine import _assert_no_reparse, _exact_existing
from .embedded_startup import INVITE_LIMIT, _read_file
from .native_player_avatar import character_appearance
from .native_guest_creator import valid_profile
from .title_handoff import TOKEN


REQUEST_FILE = "title-join-request.tsv"
RESPONSE_FILE = "title-join-response.tsv"
MAX_IMPORTS = 4
MAX_CHARACTERS = 64
MAX_OPERATIONS = 128
_REQUEST_LIMIT = INVITE_LIMIT + 512
_RESPONSE_LIMIT = 20 * 1024
_ENVELOPE = re.compile(rb"1\t([0-9a-f]{32})\t([0-9a-f]{32})\t")
_COUNT = re.compile(rb"[1-9][0-9]{0,4}")
Catalog = Callable[[Path, Path, str | None, str | None], Awaitable[dict]]
Roster = tuple[tuple[str, str], ...]


class _DraftError(ValueError):
    """An error safe to send to the form without endpoint or invitation data."""


def _token(value) -> bool:
    return isinstance(value, str) and value != "0" * 32 and TOKEN.fullmatch(value) is not None


def _uuid(value) -> bool:
    try:
        return isinstance(value, str) and len(value) == 36 and str(UUID(value)) == value
    except ValueError:
        return False


@dataclass(frozen=True)
class _Request:
    operation_id: str
    action: str
    payload: bytes = field(repr=False)
    name: str | None = None
    appearance: str | None = None
    profile: str | None = None

    @property
    def fingerprint(self) -> tuple[str, str, str | None, str | None, str | None]:
        return self.action, sha256(self.payload).hexdigest(), self.name, self.appearance, self.profile


def _parse(operation_id: str, fields: bytes) -> _Request:
    header, newline, body = fields.partition(b"\n")
    action, separator, tail = header.partition(b"\t")
    if not newline or not separator:
        raise _DraftError("invalid_request")
    if action == b"import" and _COUNT.fullmatch(tail) and int(tail) == len(body) <= INVITE_LIMIT:
        try:
            invite = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError):
            raise _DraftError("invalid_invite") from None
        if type(invite) is not dict:
            raise _DraftError("invalid_invite")
        return _Request(operation_id, "import", body)
    if action == b"direct" and not body:
        _endpoint(tail)
        return _Request(operation_id, "direct", tail)
    if action == b"create":
        identifier, separator, raw_name = tail.partition(b"\t")
        try:
            text = raw_name.decode("utf-8")
            import_id = identifier.decode("ascii")
            appearance, newline, profile = body.decode("ascii").partition("\n")
        except UnicodeError:
            raise _DraftError("invalid_request") from None
        name = text.strip()
        if (separator and _token(import_id) and len(raw_name) <= 256 and 1 <= len(name) <= 64 and text.isprintable()
                and (not appearance or character_appearance(appearance))
                and (not newline or appearance and valid_profile(profile))):
            return _Request(operation_id, "create", identifier, name, appearance or None, profile or None)
    raise _DraftError("invalid_request")


def _endpoint(payload: bytes) -> tuple[str, int, str, str | None]:
    try:
        address, port, password, *verification = payload.decode("utf-8").split("\t")
        if verification and (len(verification) != 1 or not re.fullmatch(r"[0-9a-f]{64}", verification[0])):
            raise ValueError
        if len(address) > 45 or "%" in address or not re.fullmatch(r"[1-9][0-9]{0,4}", port):
            raise ValueError
        if int(port) > 65535:
            raise ValueError
        direct_ip._password_bytes(password)
        return str(ip_address(address)), int(port), password, verification[0] if verification else None
    except ValueError:
        raise _DraftError("invalid_endpoint") from None


def _character(value) -> tuple[str, str]:
    if type(value) is not dict or value.keys() != {"character_id", "name"} or not _uuid(value["character_id"]):
        raise _DraftError("catalog_invalid")
    name = value["name"]
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not name.isprintable() or name.strip() != name:
        raise _DraftError("catalog_invalid")
    return value["character_id"], name


def _roster(value) -> Roster:
    if type(value) is not list or len(value) > MAX_CHARACTERS:
        raise _DraftError("catalog_invalid")
    rows = tuple(_character(item) for item in value)
    if len({identity for identity, _ in rows}) != len(rows):
        raise _DraftError("catalog_invalid")
    return rows


def _remove(path: Path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


@dataclass
class _Import:
    identity: str
    path: Path = field(repr=False)
    digest: str = field(repr=False)
    characters: Roster = ()

    def verify(self):
        try:
            _assert_no_reparse(self.path.parent.lstat())
            _exact_existing(self.path.parent)
            unchanged = sha256(_read_file(self.path, INVITE_LIMIT)).hexdigest() == self.digest
        except (OSError, ValueError):
            unchanged = False
        if not unchanged:
            raise _DraftError("invite_changed")


@dataclass
class _Direct:
    identity: str
    connection: direct_ip.Connection | None = field(default=None, repr=False)
    characters: Roster = ()
    path = None

    def verify(self):
        if self.connection is None:
            raise _DraftError("unknown_import")


@dataclass
class _Operation:
    fingerprint: tuple[str, str, str | None, str | None, str | None]
    result: bytes | None = None
    import_id: str | None = None


@dataclass
class _Pending:
    task: asyncio.Task
    operation_id: str
    draft: _Import | _Direct
    created_file: bool


class TitleJoinSettings:
    """Poll the one-slot form mailbox without waiting for the catalog network calls.

    Keep this object alive until the supervisor and selected role finish. Resolve a
    claim before disarming its offer. Disarming retains the resolved invite for
    launch. A new offer after owned launch cleanup can discard older invitations.
    """

    def __init__(self, work: Path, bridge: Path, *, catalog: Catalog | None = None, timeout: float = 25,
                 direct_profile: Path | None = None, qualify_connection: Callable[[dict], None] | None = None,
                 saved: Path | None = None, endpoint_fingerprint: Callable[[str, int], str | None] | None = None):
        self._work, self._bridge = _exact_existing(work), _exact_existing(bridge)
        if bridge == work or not bridge.is_relative_to(work):
            raise ValueError("The title Join mailbox must be inside the private co-op workspace")
        for path in (bridge, *(parent for parent in bridge.parents if parent.is_relative_to(work))):
            _assert_no_reparse(path.lstat())
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("Expected a bounded title Join request timeout")
        if catalog is None:
            from .service import character_catalog
            catalog = character_catalog
        if not callable(catalog):
            raise ValueError("Expected a title Join catalog service")
        self._catalog, self._timeout = catalog, timeout
        self._direct_profile, self._qualify_connection = direct_profile, qualify_connection
        self._endpoint_fingerprint = endpoint_fingerprint
        self.request_path, self.response_path = bridge / REQUEST_FILE, bridge / RESPONSE_FILE
        self._storage = bridge / "title-join-imports"
        self._generation = self._resolved = None
        self._imports, self._operations = {}, {}
        self._paths, self._retired = set(), set()
        self._pending = self._outbox = None
        self._closed = False
        self._creator_directory = None
        self._creator_file = bridge / "title-join-creator-scope.txt"
        if saved is not None and direct_profile is not None:
            from . import native_host_save, runtime

            identifier = secrets.token_hex(16)
            scope = native_host_save.SaveScope(saved.parent, identifier)
            directory = native_host_save.directory(scope)
            directory.mkdir(parents=True)
            self._creator_directory = directory
            runtime.write_text(self._creator_file, identifier + "\n", newline="\n")

    def resume_creator(self, generation):
        """Keep the selected authenticated catalog over the stock editor's map travel."""
        if (self._creator_directory is None or self._generation is None or generation == self._generation
                or self._pending is not None):
            return
        self._generation = generation
        self._operations.clear()
        self._outbox = None
        _remove(self.response_path)

    def _forget_task(self, task):
        self._retired.discard(task)
        if not task.cancelled():
            task.exception()

    def _delete(self, path):
        if _remove(path):
            self._paths.discard(path)

    def _switch(self, generation):
        if self._pending is not None:
            pending, self._pending = self._pending, None
            pending.task.cancel()
            self._retired.add(pending.task)
            pending.task.add_done_callback(self._forget_task)
        keep = self._resolved.path if generation is None and self._resolved is not None else None
        for path in tuple(self._paths):
            if path != keep:
                self._delete(path)
        if generation is not None:
            self._resolved = None
        self._generation = generation
        self._operations.clear()
        self._imports.clear()
        self._outbox = None
        _remove(self.response_path)

    def _write_response(self):
        temporary = None
        try:
            with NamedTemporaryFile(dir=self._bridge, prefix=".title-join-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(self._outbox)
            temporary.replace(self.response_path)
        except OSError:
            return False
        finally:
            if temporary is not None:
                _remove(temporary)
        self._outbox = None
        return True

    def _error(self, operation_id, code):
        self._outbox = f"1\t{self._generation}\t{operation_id}\terror\t{code}\t0\n".encode("ascii")

    async def _fetch(self, draft, name, operation_id, appearance=None, profile=None):
        async def request(name, operation_id, refusal, **creation):
            draft.verify()
            try:
                if isinstance(draft, _Direct):
                    from .service import _character_catalog
                    reply = await _character_catalog(draft.connection.session(), name, operation_id, **creation)
                else:
                    reply = await self._catalog(self._work, draft.path, name, operation_id, **creation)
            except ValueError:
                raise _DraftError("invalid_invite") from None
            draft.verify()
            if type(reply) is not dict or type(reply.get("ok")) is not bool:
                raise _DraftError("catalog_invalid")
            if not reply["ok"]:
                raise _DraftError(refusal)
            return reply

        async with asyncio.timeout(self._timeout):
            created = None
            if name is not None:
                creation = {"appearance": appearance} if appearance is not None else {}
                if profile is not None:
                    creation["profile"] = profile
                reply = await request(name, operation_id, "create_refused", **creation)
                created = _character(reply.get("character"))
                if created[1] != name:
                    raise _DraftError("catalog_invalid")
            reply = await request(None, None, "invite_refused")
            characters = _roster(reply.get("characters"))
            if created is not None and created not in characters:
                raise _DraftError("catalog_invalid")
            return characters

    async def _connect(self, draft: _Direct, payload: bytes):
        from .service import PROTOCOL, staged_build, validate_invite

        address, port, password, fingerprint = _endpoint(payload)
        steam_fingerprint = self._endpoint_fingerprint(address, port) if self._endpoint_fingerprint else None
        if steam_fingerprint is not None:
            if fingerprint is not None and fingerprint != steam_fingerprint:
                raise _DraftError("direct_identity_verification_failed")
            fingerprint = steam_fingerprint
        build = staged_build(self._work)
        async with asyncio.timeout(self._timeout):
            try:
                draft.connection = await direct_ip.connect(self._direct_profile, address, port, password, build, PROTOCOL,
                                                           verified_fingerprint=fingerprint, require_trust=True)
            except direct_ip.HostTrustRequired:
                raise
            except direct_ip.Refused as error:
                raise _DraftError(error.reason) from None
            except link.LinkError:
                raise _DraftError("direct_host_unreachable") from None
            try:
                invite = validate_invite(draft.connection.session(), build, catalog=True)
                if self._qualify_connection is not None:
                    self._qualify_connection(invite)
            except ValueError:
                raise _DraftError("incompatible_game_or_protocol") from None
            return await self._fetch(draft, None, None)

    def _queue(self, request):
        operation_id, fingerprint = request.operation_id, request.fingerprint
        if (request.action == "direct" and self._direct_profile is None
                or request.action == "import" and self._direct_profile is not None):
            return self._error(operation_id, "invalid_request")
        operation = self._operations.get(operation_id)
        if operation is not None and operation.fingerprint != fingerprint:
            return self._error(operation_id, "operation_conflict")
        if operation is not None and operation.result is not None:
            try:
                self._imports[operation.import_id].verify()
            except _DraftError as error:
                return self._error(operation_id, str(error))
            self._outbox = operation.result
            return
        if self._pending is not None or self._retired:
            if self._pending is None or operation_id != self._pending.operation_id:
                self._error(operation_id, "request_pending")
            return
        if operation is None:
            if len(self._operations) >= MAX_OPERATIONS:
                return self._error(operation_id, "operation_limit")
            self._operations[operation_id] = _Operation(fingerprint)

        created_file = False
        if request.action == "direct":
            if len(self._imports) >= MAX_IMPORTS:
                return self._error(operation_id, "import_limit")
            draft = _Direct(secrets.token_hex(16))
            task = asyncio.create_task(self._connect(draft, request.payload))
            self._pending = _Pending(task, operation_id, draft, False)
            return
        if request.action == "import":
            digest = fingerprint[1]
            draft = next((item for item in self._imports.values() if item.digest == digest), None)
            if draft is None:
                if len(self._paths) >= MAX_IMPORTS:
                    return self._error(operation_id, "import_limit")
                try:
                    self._storage.mkdir(exist_ok=True)
                    _assert_no_reparse(self._storage.lstat())
                    _exact_existing(self._storage)
                    identity = secrets.token_hex(16)
                    if not _token(identity):
                        raise ValueError("invalid generated token")
                    path = self._storage / (identity + ".invite.json")
                    with path.open("xb") as invite:
                        created_file = True
                        self._paths.add(path)
                        invite.write(request.payload)
                except (OSError, ValueError):
                    if created_file:
                        self._delete(path)
                    return self._error(operation_id, "catalog_unavailable")
                draft = _Import(identity, path, digest)
        else:
            draft = self._imports.get(request.payload.decode("ascii"))
            if draft is None:
                return self._error(operation_id, "unknown_import")
            if len(draft.characters) >= MAX_CHARACTERS:
                return self._error(operation_id, "create_refused")
        task = asyncio.create_task(self._fetch(draft, request.name, operation_id, request.appearance, request.profile))
        self._pending = _Pending(task, operation_id, draft, created_file)

    def _complete(self):
        if self._pending is None or not self._pending.task.done():
            return
        pending, self._pending = self._pending, None
        identity, operation_id = pending.draft.identity, pending.operation_id
        try:
            roster = pending.task.result()
            header = f"1\t{self._generation}\t{operation_id}\tok\t{identity}\t{len(roster)}\n"
            payload = (header + "".join(f"{character}\t{name}\n" for character, name in roster)).encode("utf-8")
            if len(payload) > _RESPONSE_LIMIT:
                raise _DraftError("catalog_invalid")
        except direct_ip.HostTrustRequired as request:
            reason = "changed" if request.changed else "new"
            self._outbox = (
                f"1\t{self._generation}\t{operation_id}\ttrust\t{reason}\t1\n{request.fingerprint}\n"
            ).encode("ascii")
            return
        except (asyncio.CancelledError, OSError, RuntimeError, ValueError) as error:
            self._error(operation_id, str(error) if isinstance(error, _DraftError) else "catalog_unavailable")
            if pending.created_file:
                self._delete(pending.draft.path)
            return
        pending.draft.characters = roster
        self._imports[identity] = pending.draft
        self._operations[operation_id].result = self._outbox = payload
        self._operations[operation_id].import_id = identity

    def poll(self, generation: str | None) -> None:
        """Consume a request or publish its result. Pass None when the offer is disarmed."""
        if self._closed:
            return
        if generation is not None and not _token(generation):
            raise ValueError("Invalid title Join offer generation")
        if generation != self._generation:
            self._switch(generation)
        if generation is None:
            _remove(self.request_path)
            return
        self._complete()
        if self._outbox is not None:
            self._write_response()
            return
        try:
            raw = _read_file(self.request_path, _REQUEST_LIMIT)
        except (FileNotFoundError, PermissionError):
            return
        except (OSError, ValueError):
            _remove(self.request_path)
            return
        envelope = _ENVELOPE.match(raw)
        if envelope is None:
            _remove(self.request_path)
            return
        offered, operation_id = (part.decode("ascii") for part in envelope.groups())
        if offered != generation or not _token(operation_id):
            _remove(self.request_path)
            return
        # A retry can reuse its operation ID. The form waits for the request to
        # disappear, so remove a previous response before dropping that fence.
        if not _remove(self.response_path) or not _remove(self.request_path):
            return
        try:
            request = _parse(operation_id, raw[envelope.end():])
        except _DraftError as error:
            self._error(operation_id, str(error))
        else:
            self._queue(request)
        if self._outbox is not None:
            self._write_response()

    def resolve(self, generation: str, import_id: str, character_uuid: str) -> Path | direct_ip.Connection:
        """Resolve an authenticated exact character to its invitation or connection."""
        if self._closed or generation != self._generation or not _token(generation):
            raise ValueError("title_join_stale_generation")
        draft = self._imports.get(import_id) if _token(import_id) else None
        if draft is None or not _uuid(character_uuid) or not any(row[0] == character_uuid for row in draft.characters):
            raise ValueError("title_join_unverified_character")
        if self._pending is not None:
            raise ValueError("title_join_request_pending")
        try:
            draft.verify()
        except _DraftError:
            raise ValueError("title_join_invite_changed") from None
        self._resolved = draft
        return draft.connection if isinstance(draft, _Direct) else draft.path

    async def close(self) -> None:
        """Disarm the mailbox and discard private invitation files after the role ends."""
        self._closed = True
        self._resolved = None
        self._switch(None)
        try:
            if self._retired:
                await asyncio.gather(*self._retired, return_exceptions=True)
        finally:
            _remove(self.request_path)
            _remove(self._creator_file)
            if self._creator_directory is not None:
                try:
                    self._creator_directory.rmdir()
                except OSError:
                    pass
