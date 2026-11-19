"""Recover one empty Unreal join after an authenticated normal zone change."""

from typing import TYPE_CHECKING

from .runtime import MENU

if TYPE_CHECKING:
    from .service import Client


class EmptyZoneRetry:
    def __init__(self) -> None:
        self._target: tuple[str, str, str] | None = None
        self._used = False

    def note_transition(self, session: str, epoch: str, zone: str) -> None:
        target = (session, epoch, zone)
        if target != self._target:
            self._target, self._used = target, False

    def clear(self) -> None:
        self._used = True

    def _current(self, client: "Client", target: tuple[str, str, str]) -> bool:
        return (self._target == target and not self._used
                and (client.host_session, client.zone_epoch, client.expected_zone) == target
                and client.needs_travel and not client.stopping and not client.suspended
                and not client.server_transitioning and client.native_return_reservation is None
                and client.transport.game_access and client.bridge.process.poll() is None)

    @staticmethod
    def _empty_title(client: "Client", snapshot: dict | None, phase: str, mode: str) -> bool:
        return bool(snapshot and snapshot.get("map") == MENU and snapshot.get("state") == phase
                    and snapshot.get("mode") == mode and snapshot.get("server_connection") is False
                    and snapshot.get("remote_clients") == 0
                    and (client.transport.game_packets_sent, client.transport.game_packets_received) == (0, 0))

    async def after_timeout(self, client: "Client", epoch: str | None) -> bool:
        target = self._target
        if target is None or target[1] != epoch or not self._current(client, target):
            return False
        snapshot = client.bridge.snapshot()
        if not self._empty_title(client, snapshot, "joining", "client"):
            return False
        ack = snapshot.get("ack", {})
        if (ack.get("result"), ack.get("message")) != ("accepted", "client travel requested"):
            return False

        seen = client.gameplay_seen
        await client.refresh_gameplay()
        snapshot = client.bridge.snapshot()
        if (client.gameplay_seen <= seen or not self._current(client, target)
                or not self._empty_title(client, snapshot, "joining", "client")):
            return False
        await client.leave_for_rejoin(snapshot)
        if not self._current(client, target) or not self._empty_title(client, client.last_game, "ready", "idle"):
            return False

        self.clear()
        client.control.event("native_zone_empty_join_retry", zone_epoch=epoch, map=target[2],
                             local_datagrams=client.transport.game_local_datagrams, sent=0, received=0)
        await client.open_game_link(renew_endpoint=True)
        return True
