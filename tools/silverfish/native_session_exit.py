"""Route a qualified stock Pause exit to the current co-op worker."""

from contextlib import suppress
import os
from pathlib import Path
import re
import time
import uuid

from .embedded_engine import EmbeddedEngine, _assert_no_reparse, _exact_existing, owns_session_bridge
from .embedded_startup import _read_file
from .runtime import write_text
from .title_handoff import TOKEN


SESSION = "native-coop-pause-session.tsv"
REQUEST = "native-coop-pause-request.tsv"
RESPONSE = "native-coop-pause-response.tsv"
ORDERLY_REASONS = ("guest_left", "host_session_ended")
OFFER_SECONDS = 5
_RECORD = re.compile(rb"1\t([0-9a-f]{32})\t([1-9][0-9]{0,14})\t(host|guest)\t(quit_to_menu|save_and_quit)\n")


class PauseExit:
    """Dispatch one native decision through the serialized role loop."""

    def __init__(self, bridge: Path, control, role: str, *, engine: EmbeddedEngine | None = None, clock=time.time):
        if (role not in {"host", "guest"} or not owns_session_bridge(bridge, control.run, engine)
                or not TOKEN.fullmatch(bridge.name)):
            raise ValueError("The Pause exit mailbox must belong to the active co-op role")
        self._bridge = _exact_existing(bridge)
        self._identity = bridge.lstat()
        _assert_no_reparse(self._identity)
        self._control, self._role, self._clock = control, role, clock
        self._session, self._request, self._response = (bridge / name for name in (SESSION, REQUEST, RESPONSE))
        self._scope = self._token = None
        self._expires = self._serial = 0

    def _same_bridge(self):
        try:
            actual = self._bridge.lstat()
            _assert_no_reparse(actual)
            return os.path.samestat(_exact_existing(self._bridge).stat(), self._identity)
        except (OSError, ValueError):
            return False

    def _disarm(self):
        if self._token is not None:
            for path in (self._session, self._request):
                with suppress(OSError):
                    path.unlink(missing_ok=True)
        self._scope = self._token = None
        self._expires = self._serial = 0

    def close(self):
        if self._same_bridge():
            self._disarm()

    def _take(self):
        try:
            raw = _read_file(self._request, 128)
            self._request.unlink()
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            # An invalid record must not prevent a later stock press.
            with suppress(OSError):
                self._request.unlink(missing_ok=True)
            return None
        match = _RECORD.fullmatch(raw)
        if match is None:
            return None
        token, number, role, reason = (field.decode("ascii") for field in match.groups())
        serial = int(number)
        if token != self._token or role != self._role or serial <= self._serial:
            return None
        self._serial = serial
        return serial, reason

    async def poll(self, scope: tuple[str, ...] | None, finish):
        if not self._same_bridge():
            return
        if scope is None:
            self._disarm()
            return
        now = int(self._clock())
        if self._scope != scope or self._expires <= now:
            self._disarm()
            self._scope, self._token = scope, uuid.uuid4().hex
        if now + 2 >= self._expires:
            expires = now + OFFER_SECONDS
            try:
                write_text(self._session, f"1\t{self._token}\t{self._role}\t{expires}\n", newline="\n")
            except OSError:
                return
            self._expires = expires
        request = self._take()
        if request is None:
            return
        serial, reason = request
        operation = uuid.uuid5(uuid.UUID(hex=self._token), str(serial)).hex
        try:
            receipt = await finish(operation)
        except (OSError, ValueError, RuntimeError) as error:
            status, code = "error", "exit_refused"
            event = {"error": str(error)}
        else:
            status, code = "ok", "stopping"
            event = {"receipt": receipt}
        if self._same_bridge():
            with suppress(OSError):
                write_text(self._response, f"1\t{self._token}\t{serial}\t{status}\t{code}\n", newline="\n")
        with suppress(OSError):
            self._control.event("stock_pause_exit", reason=reason, operation_id=operation, result=status, **event)
        if status == "error" and self._same_bridge():
            self._disarm()
