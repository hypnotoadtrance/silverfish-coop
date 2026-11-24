"""Route a qualified stock Pause exit through the embedded role worker."""

from contextlib import suppress
import time
import uuid

from .embedded_engine import owns_session_bridge


ORDERLY_REASONS = ("guest_left", "host_session_ended")
OFFER_SECONDS = 5


class PauseExit:
    """Dispatch one native press through the serialized role loop."""

    def __init__(self, bridge, control, role: str, *, engine, clock=time.monotonic):
        local = bridge.local_bridge
        if (role not in {"host", "guest"} or local is None or not local.has_pause_exit
                or not owns_session_bridge(bridge.directory, control.run, engine)):
            raise ValueError("The Pause exit must belong to the active embedded co-op role")
        self._local, self._control, self._clock = local, control, clock
        self._role = 1 if role == "host" else 2
        self._scope = self._token = None
        self._expires = 0

    def active_token(self) -> str | None:
        return self._token if self._clock() < self._expires else None

    def close(self):
        if self._token is not None:
            self._local.offer_pause_exit(None, 0)
        self._scope = self._token = None
        self._expires = 0

    async def poll(self, scope: tuple[str, ...] | None, finish):
        if scope is None:
            self.close()
            return

        # Drain a press from the current scene before replacing an expired offer.
        # The native bridge only queues a press while its lease is live.
        request = None
        if scope == self._scope:
            request = self._local.take_pause_exit(self._token.encode("ascii"), self._role)
        if request is None:
            now = self._clock()
            if scope != self._scope or now >= self._expires:
                self.close()
                self._scope, self._token = scope, uuid.uuid4().hex
            if now + 2 >= self._expires:
                if self._local.offer_pause_exit(self._token.encode("ascii"), self._role):
                    self._expires = now + OFFER_SECONDS
            return

        serial, reason = request
        operation = uuid.uuid5(uuid.UUID(hex=self._token), str(serial)).hex
        try:
            receipt = await finish(operation)
        except (OSError, ValueError, RuntimeError) as error:
            status, event = "error", {"error": str(error)}
            self.close()
        else:
            status, event = "ok", {"receipt": receipt}
        with suppress(OSError):
            self._control.event("stock_pause_exit", reason=reason, operation_id=operation, result=status, **event)
