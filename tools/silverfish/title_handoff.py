"""One-shot stock title selection over an isolated staged bridge."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
import os
from pathlib import Path
import re
import secrets
import stat
from tempfile import NamedTemporaryFile
import time

from . import native_title_entry
from .control import NAME


DESCRIPTOR_FILE = "title-handoff-session.tsv"
REQUEST_FILE = "title-handoff-request.tsv"
ACTIONS = ("host", "join")
LEASE_SECONDS = 10
TOKEN = re.compile(r"[0-9a-f]{32}")
_REQUEST = re.compile(rb"1\t([0-9a-f]{32})\t([0-9a-f]{32})\t(host|join)\r?\n")
_HOST_FIELDS = (rb"\t([0-9a-f]{32})\t([0-9a-f]{32})\thost\t([A-Za-z0-9_-]{0,64})"
                rb"\t([A-Za-z0-9.:%_-]{0,64})\t([0-9]{0,5})")
_HOST_REQUEST = re.compile(rb"2" + _HOST_FIELDS + rb"\r?\n")
_DIRECT_HOST_REQUEST = re.compile(rb"3" + _HOST_FIELDS + rb"\t([^\x00-\x1f\x7f]{0,512})\r?\n")
_CREATOR_HOST_REQUEST = re.compile(rb"4" + _HOST_FIELDS + rb"\t([^\x00-\x1f\x7f]{0,512})\tcreator\r?\n")
_RESUME_HOST_REQUEST = re.compile(rb"5" + _HOST_FIELDS + rb"\t([^\x00-\x1f\x7f]{0,512})\tresume\r?\n")
_STEAM_HOST_REQUEST = re.compile(
    rb"6\t([0-9a-f]{32})\t([0-9a-f]{32})\thost\t([A-Za-z0-9_-]{0,64})"
    rb"\t(invite|friends|public)\t([^\x00-\x1f\x7f]{0,512})\t(creator|resume)\r?\n"
)
_CHARACTER_UUID = rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JOIN_REQUEST = re.compile(rb"2\t([0-9a-f]{32})\t([0-9a-f]{32})\tjoin\t([0-9a-f]{32})"
                           rb"\t([A-Za-z0-9_-]{0,64})\t(" + _CHARACTER_UUID + rb")\r?\n")
_MAX_REQUEST_BYTES = 768


@dataclass(frozen=True)
class HostDefaults:
    campaign: str
    bind: str
    port: int

    def __post_init__(self):
        if (type(self.campaign) is not str or not NAME.fullmatch(self.campaign) or type(self.bind) is not str
                or "%" in self.bind or type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ValueError("Invalid default Host settings")
        object.__setattr__(self, "bind", str(ip_address(self.bind)))


@dataclass(frozen=True)
class Claim:
    action: str
    request_id: str
    generation: str
    binding: object = field(repr=False)
    host_settings: tuple[str, str, str] | None = field(default=None, repr=False)
    join_settings: tuple[str, str, str] | None = field(default=None, repr=False)
    direct_password: str | None = field(default=None, repr=False)
    stock_creator: bool = False
    stock_resume: bool = False
    steam_access: str | None = None


def _stock_title(snapshot):
    if (not isinstance(snapshot, dict)
            or (snapshot.get("schema"), snapshot.get("mode"), snapshot.get("state"), snapshot.get("map"))
            != (1, "idle", "ready", native_title_entry.MAP)
            or snapshot.get("remote_clients") != 0 or snapshot.get("server_connection") is not False):
        return None
    sequence = snapshot.get("sequence")
    row = snapshot.get(native_title_entry.KEY)
    if type(sequence) is not int or sequence <= 0 or not isinstance(row, dict):
        return None
    if ((row.get("map"), row.get("state"), row.get("reason")) != (native_title_entry.MAP, "ready", None)
            or any(row.get(field) is not True for field in ("top", "mounted", "visible"))):
        return None
    count = row.get("menu_stack")
    if type(count) is not int or not 1 <= count <= 32:
        return None
    names = tuple(row.get(field) for field in ("controller", "widget", "viewport"))
    for name, label in zip(names, ("PlayerControler_FP_C", "MainMenu_Widget_C", "GameViewportClient"), strict=True):
        if not isinstance(name, str) or not name.isprintable() or len(name) > 512:
            return None
        actual, space, path = name.partition(" ")
        if actual != label or not space or not path:
            return None
    return sequence, names


def _isolated_stock_title(snapshot, saved_directory: Path):
    title = _stock_title(snapshot)
    if title is None:
        return None
    receipt = snapshot.get(native_title_entry.STARTUP_KEY)
    if (not isinstance(receipt, dict) or set(receipt) != {"map", "sequence", "saved_directory"}
            or receipt["map"] != native_title_entry.MAP or type(receipt["sequence"]) is not int
            or receipt["sequence"] != title[0]):
        return None
    value = receipt["saved_directory"]
    if not isinstance(value, str) or not value or not value.isprintable():
        raise ValueError("The engine did not report an exact effective save directory")
    candidate = Path(value)
    if (not candidate.is_absolute() or candidate.resolve(strict=True) != saved_directory
            or not saved_directory.is_dir() or saved_directory.resolve(strict=True) != saved_directory):
        raise ValueError("The effective Unreal save directory does not match the isolated profile")
    return title


def _unlink(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        return False
    return True


class TitleHandoff:
    """Use one instance per private bridge. Call refresh and poll on the owning supervisor.

    Snapshot values come from that bridge's stock-title status. Bindings are
    supervisor-owned choices, held privately for the lifetime of each offer.
    A claimed handoff stays spent even after closing it.
    """

    def __init__(self, directory: Path, *, clock=time.time):
        directory = directory.resolve(strict=True)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        self.descriptor_path = directory / DESCRIPTOR_FILE
        self.request_path = directory / REQUEST_FILE
        self._clock = clock
        self._claim = None
        self.last_refusal = None
        self.close()

    @property
    def generation(self) -> str | None:
        return self._generation

    def close(self) -> None:
        """Disarm the offer immediately, even if the game is reading its descriptor."""
        self._generation = None
        self._expires = 0
        self._bindings = {}
        self._host_defaults = None
        self._title = None
        self._publish_sequence = 0
        self._pending = None
        _unlink(self.descriptor_path)

    def refresh(self, snapshot: dict | None, bindings: Mapping[str, object], *,
                host_defaults: HostDefaults | None = None) -> bool:
        """Publish or renew usable actions on the current exact stock title screen."""
        choices = dict(bindings)
        if (any(type(action) is not str or action not in ACTIONS or binding is None
                for action, binding in choices.items()) or
                (host_defaults is not None and type(host_defaults) is not HostDefaults)):
            self.close()
            raise ValueError("Unsupported title handoff binding")
        if self._claim is not None or not choices:
            self.close()
            return False
        now = int(self._clock())
        if self._generation and not 0 <= self._expires - now <= LEASE_SECONDS:
            self.close()
        if snapshot is None:
            return bool(self._generation)
        frame = _stock_title(snapshot)
        if frame is None:
            self.close()
            return False
        sequence, title = frame
        defaults = host_defaults if "host" in choices else None
        if self._generation and (title != self._title or choices != self._bindings or defaults != self._host_defaults):
            self.close()
        generation = self._generation
        if generation and (sequence <= self._publish_sequence or self._expires - now > LEASE_SECONDS // 2):
            return True

        generation = generation or secrets.token_hex(16)
        advertised = ",".join(action for action in ACTIONS if action in choices)
        expires = now + LEASE_SECONDS
        if defaults is None:
            row = f"1\t{generation}\t{advertised}\t{expires}\n".encode("ascii")
        else:
            row = (f"2\t{generation}\t{advertised}\t{expires}"
                   f"\t{defaults.campaign}\t{defaults.bind}\t{defaults.port}\n").encode("ascii")
        with NamedTemporaryFile(dir=self.descriptor_path.parent, prefix=".title-handoff-", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(row)
                stream.close()
                temporary.replace(self.descriptor_path)
            except PermissionError:
                return bool(self._generation)
            finally:
                _unlink(temporary)
        self._generation = generation
        self._bindings = choices
        self._host_defaults = defaults
        self._title = title
        self._expires = expires
        self._publish_sequence = sequence
        return True

    def _request_error(self, generation: str, request_id: str, action: str) -> str | None:
        if self._claim is not None:
            return "duplicate_request" if request_id == self._claim.request_id else "handoff_consumed"
        if generation != self._generation:
            return "stale_generation"
        if not 0 <= self._expires - int(self._clock()) <= LEASE_SECONDS:
            self.close()
            return "expired_generation"
        if action not in self._bindings:
            return "unadvertised_action"
        return None

    def queue_local(self, snapshot: dict | None, generation: str, request_id: str, action: str) -> str | None:
        """Publish a structured choice to the same one-use mailbox as the in-game panel."""
        if (not isinstance(generation, str) or not TOKEN.fullmatch(generation) or generation == "0" * 32
                or not isinstance(request_id, str) or not TOKEN.fullmatch(request_id) or request_id == "0" * 32
                or action not in ACTIONS):
            return "malformed_request"
        if error := self._request_error(generation, request_id, action):
            return error
        frame = _stock_title(snapshot)
        if frame is None or frame[1] != self._title:
            return "stock_title_unready"
        with NamedTemporaryFile(dir=self.request_path.parent, prefix=".title-choice-", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(f"1\t{generation}\t{request_id}\t{action}\n".encode("ascii"))
                stream.close()
                if os.name == "nt":
                    temporary.rename(self.request_path)
                else:
                    os.link(temporary, self.request_path)
            except FileExistsError:
                return "selection_pending"
            finally:
                _unlink(temporary)
        return None

    def poll(self, snapshot: dict | None) -> Claim | None:
        """Claim a complete request once, after a later frame confirms the same title."""
        self.last_refusal = None
        try:
            if not stat.S_ISREG(self.request_path.lstat().st_mode):
                self.last_refusal = "request_file_unqualified"
                return None
            with self.request_path.open("rb") as stream:
                data = stream.read(_MAX_REQUEST_BYTES + 1)
        except (FileNotFoundError, PermissionError):
            return None

        host_settings = join_settings = direct_password = steam_access = None
        stock_creator = stock_resume = False
        choice = _REQUEST.fullmatch(data)
        if choice:
            generation, request_id, action = (field.decode("ascii") for field in choice.groups())
        elif choice := _HOST_REQUEST.fullmatch(data):
            generation, request_id, campaign, bind, port = (field.decode("ascii") for field in choice.groups())
            action, host_settings = "host", (campaign, bind, port)
        elif choice := (_DIRECT_HOST_REQUEST.fullmatch(data) or _CREATOR_HOST_REQUEST.fullmatch(data)
                        or _RESUME_HOST_REQUEST.fullmatch(data)):
            stock_creator = choice.re is _CREATOR_HOST_REQUEST
            stock_resume = choice.re is _RESUME_HOST_REQUEST
            *fields, password = choice.groups()
            generation, request_id, campaign, bind, port = (field.decode("ascii") for field in fields)
            try:
                direct_password = password.decode("utf-8")
            except UnicodeDecodeError:
                return self._refuse("malformed_request")
            if len(direct_password) > 128 or (stock_resume and not NAME.fullmatch(campaign)):
                return self._refuse("malformed_request")
            action, host_settings = "host", (campaign, bind, port)
        elif choice := _STEAM_HOST_REQUEST.fullmatch(data):
            generation, request_id, campaign, steam_access, password, action_mode = choice.groups()
            generation, request_id, campaign, steam_access = (
                field.decode("ascii") for field in (generation, request_id, campaign, steam_access)
            )
            stock_creator, stock_resume = action_mode == b"creator", action_mode == b"resume"
            try:
                direct_password = password.decode("utf-8")
            except UnicodeDecodeError:
                return self._refuse("malformed_request")
            if len(direct_password) > 128 or (stock_resume and not NAME.fullmatch(campaign)):
                return self._refuse("malformed_request")
            action, host_settings = "host", (campaign, "", "")
        elif choice := _JOIN_REQUEST.fullmatch(data):
            generation, request_id, invite, profile, character = (field.decode("ascii") for field in choice.groups())
            action, join_settings = "join", (invite, profile, character)
        else:
            return self._refuse("malformed_request")
        if error := self._request_error(generation, request_id, action):
            return self._refuse(error)
        if snapshot is None:
            return None
        frame = _stock_title(snapshot)
        if frame is None or frame[1] != self._title:
            self.close()
            return self._refuse("stock_title_unready")
        sequence, _ = frame
        if self._pending is None or self._pending[0] != data:
            self._pending = data, sequence
            return None
        if sequence <= max(self._pending[1], self._publish_sequence):
            return None

        if not _unlink(self.request_path):
            return None
        claim = Claim(action, request_id, generation, self._bindings[action], host_settings, join_settings,
                      direct_password, stock_creator, stock_resume, steam_access)
        self._claim = claim
        self.last_refusal = None
        self.close()
        return claim

    def _refuse(self, code: str) -> None:
        self.last_refusal = code
        self._pending = None
        _unlink(self.request_path)
