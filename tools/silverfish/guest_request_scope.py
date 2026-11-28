"""Track whether a queued guest action has crossed a side-effect boundary."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class GuestRequestScope:
    first_effect: str | None = None


active: ContextVar[GuestRequestScope | None] = ContextVar("active_guest_request", default=None)


def mark_effect(effect: str) -> None:
    scope = active.get()
    if scope is not None and scope.first_effect is None:
        scope.first_effect = effect
