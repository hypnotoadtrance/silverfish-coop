"""Mirror a nearby party drop as a read-only local stock actor."""

import math
import re
import time
import uuid

from . import native_guest_drop_record as record, party


KEY = "native_guest_drop_projection"
ROW = "native-guest-drop-projection"
SYNC = ROW + "-sync"
CLEAR = ROW + "-clear"
FIXTURE = ROW + "-fixture"
_TARGET = re.compile(r"stack-drop:([0-9a-f]{32}):([0-9a-f]{32})\Z")


def target_parts(target: str | None) -> tuple[str, str] | None:
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    return match.groups() if match else None


def parse(fields: list[str], state: str, area: str) -> dict:
    if len(fields) != 7 or state not in {"connected", "hosting"} or not record.supported_map(area):
        raise ValueError("Unexpected stock drop projection report")
    _, reported_area, token, drop_id, pawn, actor, sequence = fields
    if (reported_area != area or not record.valid_id(token) or not record.valid_id(drop_id)
            or not record.actor_matches(area, actor) or not record.pawn_matches(area, pawn)
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", sequence) or int(sequence) >= 2**53):
        raise ValueError("Invalid stock drop projection report")
    return dict(map=area, epoch=token, drop_id=drop_id, pawn=pawn, actor=actor, sequence=int(sequence))


def _nearby_projection(game, rows, session: str, zone: str, viewer: str, admission: str):
    area = game.get("map")
    local_pawn = game.get("pawns", {}).get("local", {})
    pawn, origin = local_pawn.get("name"), local_pawn.get("location")
    if (not session or not record.valid_id(zone) or not record.pawn_matches(area, pawn)
            or not record.valid_position(origin) or type(rows) is not dict or not 1 <= len(rows) <= record.MAX_ACTIVE):
        return None
    if any(not record.valid_id(drop_id) or type(row) is not dict
           or (row.get("drop_id"), row.get("map"), row.get("class_path")) != (drop_id, area, record.ITEM)
           or not isinstance(row.get("owner"), str) or not row["owner"]
           or not record.valid_position(row.get("location")) for drop_id, row in rows.items()):
        return None
    drop_id = min(rows, key=lambda key: (math.dist(origin, rows[key]["location"]), key))
    epoch = uuid.uuid5(uuid.NAMESPACE_OID, f"{session}:{zone}:{viewer}:{admission}:{area}").hex
    return epoch, drop_id, pawn, area, rows[drop_id]["location"]


def _desired(client, active: bool, admission: str):
    game = client.last_game
    if (not active or client.stopping or client.suspended or client.needs_travel or client.server_transitioning
            or not client.transport.game_access or game.get("state") != "connected"
            or client.expected_zone != game.get("map")):
        return None
    return _nearby_projection(game, client.gameplay.get(record.STATE), client.host_session, client.zone_epoch,
                              client.invite["character_id"], admission)


def _host_desired(host, admission: str):
    from .native_guest_drop import visible_drops

    game = host.last_game
    if (not host.native_guest or not host.native_zones or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or host.party.get("wipe")
            or (game.get("state"), game.get(record.CAPABILITY)) != ("hosting", 1)):
        return None
    rows = visible_drops(host.party, "host", game.get("map"))
    return _nearby_projection(game, rows, host.control.generation, host.zone_epoch, "host", admission)


def _fresh(client) -> bool:
    return time.monotonic() - client.gameplay_seen < 2


class Projection:
    def __init__(self):
        self.current = None
        self.claimed = None
        self.next_sync = 0.0
        self.last_error = None
        self.admission = uuid.uuid4().hex

    def rebind(self):
        self.admission = uuid.uuid4().hex
        self.next_sync = 0.0

    @staticmethod
    def _matches(snapshot, desired):
        proof = snapshot.get(KEY, {})
        epoch, drop_id, pawn, area, _ = desired
        return (proof.get("epoch"), proof.get("drop_id"), proof.get("pawn"), proof.get("map")) == (epoch, drop_id, pawn, area)

    async def _clear(self, bridge, game):
        if self.current is not None or game.get(KEY):
            game = await bridge.command(CLEAR, timeout=2)
            if game.get(KEY):
                raise RuntimeError("The stock drop projection did not retire")
        self.current = None
        self.next_sync = 0.0
        return game

    async def _sync(self, bridge, game, desired, fresh):
        if desired is None or self.claimed == desired[:2]:
            return await self._clear(bridge, game)
        # A stalled read can age the snapshot. Let native hide
        # the actor and renew its same weak identity when the next authenticated frame arrives.
        now = time.monotonic()
        if not fresh or (self.current == desired[:4] and now < self.next_sync):
            return game
        epoch, drop_id, _, area, xyz = desired
        self.next_sync = now + .6
        snapshot = await bridge.command(SYNC, area, epoch, drop_id, *(str(axis) for axis in xyz), timeout=2)
        if not self._matches(snapshot, desired):
            raise RuntimeError("The exact local stock drop projection was unverified")
        self.current = desired[:4]
        self.last_error = None
        return snapshot

    async def sync(self, client, active: bool):
        if active and client.gameplay.get(record.STATE) == {}:
            self.claimed = None
        client.last_game = await self._sync(client.bridge, client.last_game, _desired(client, active, self.admission),
                                            _fresh(client))

    async def sync_host(self, host, bridge):
        game = await self._sync(bridge, host.last_game, _host_desired(host, self.admission),
                                time.monotonic() - host.last_seen < 2)
        if game is not host.last_game:
            host.last_game, host.last_seen = game, time.monotonic()

    def _qualify_claim(self, game, desired, fresh, target):
        if (not fresh or desired is None or self.current != desired[:4] or desired[:2] != target
                or self.claimed == target or not self._matches(game, desired)):
            raise party.RuleError("native_guest_drop_projection_intent_stale")

    def _accept_claim(self, result, target, claimant):
        from .native_guest_drop import RESULT

        receipt = result.get(RESULT, {})
        if (receipt.get("state"), receipt.get("drop_id"), receipt.get("claimed_by", receipt.get("owner"))) != (
                "reclaimed", target[1], claimant):
            raise RuntimeError("The exact claimant did not receive a stock drop pickup receipt")
        self.claimed = target
        return result

    async def reclaim(self, client, operation: str, target: tuple[str, str]):
        from .native_guest_drop import RECLAIM

        self._qualify_claim(client.last_game, _desired(client, True, self.admission), _fresh(client), target)
        result = await client.gameplay_action(RECLAIM, operation, target[1])
        return self._accept_claim(result, target, client.invite["character_id"])

    async def reclaim_host(self, host, bridge, operation: str, target: tuple[str, str]):
        from .native_guest_drop import RECLAIM, perform

        self._qualify_claim(host.last_game, _host_desired(host, self.admission), time.monotonic() - host.last_seen < 2,
                            target)
        result = await perform(host, bridge, "host", RECLAIM, target[1], operation)
        return self._accept_claim(result, target, "host")

    async def fixture(self, client, drop_id: str | None):
        client.last_game = await self._fixture(client.bridge, client.last_game, _desired(client, True, self.admission),
                                               _fresh(client), drop_id)
        return client.last_game[KEY]

    async def fixture_host(self, host, bridge, drop_id):
        host.last_game = await self._fixture(bridge, host.last_game, _host_desired(host, self.admission),
                                             time.monotonic() - host.last_seen < 2, drop_id)
        host.last_seen = time.monotonic()
        return host.last_game[KEY]

    async def _fixture(self, bridge, game, desired, fresh, drop_id):
        if (not fresh or not record.valid_id(drop_id) or desired is None
                or self.current != desired[:4] or desired[1] != drop_id
                or not self._matches(game, desired)):
            raise party.RuleError("stock_drop_projection_fixture_scope_unavailable")
        before = game[KEY]["sequence"]
        snapshot = await bridge.command(FIXTURE, drop_id, timeout=4)
        proof = snapshot.get(KEY, {})
        if not self._matches(snapshot, desired) or proof.get("sequence") != before + 1:
            raise RuntimeError("The exact stock drop did not intercept the headless stock press")
        return snapshot
