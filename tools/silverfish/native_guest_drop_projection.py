"""Mirror one authenticated owner's durable stock drop as a read-only local actor."""

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
_ITEM_NAME = "Ammo_Pistol_BP_C"
_PREFIX = f"{record.OBSERVATORY}.Observitory_P:PersistentLevel."
_ACTOR = re.compile(re.escape(f"{_ITEM_NAME} {_PREFIX}{_ITEM_NAME}_") + r"[0-9]{1,10}\Z")
_PAWN = re.compile(re.escape(f"Player_FP_C {_PREFIX}Player_FP_C_") + r"[0-9]{1,10}\Z")


def target_parts(target: str | None) -> tuple[str, str] | None:
    match = _TARGET.fullmatch(target) if isinstance(target, str) else None
    return match.groups() if match else None


def parse(fields: list[str], state: str, area: str) -> dict:
    if len(fields) != 7 or (state, area) != ("connected", record.OBSERVATORY):
        raise ValueError("Unexpected stock drop projection report")
    _, reported_area, token, drop_id, pawn, actor, sequence = fields
    if (reported_area != area or not record.valid_id(token) or not record.valid_id(drop_id)
            or not _ACTOR.fullmatch(actor) or not _PAWN.fullmatch(pawn)
            or not re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", sequence) or int(sequence) >= 2**53):
        raise ValueError("Invalid stock drop projection report")
    return dict(map=area, epoch=token, drop_id=drop_id, pawn=pawn, actor=actor, sequence=int(sequence))


def _desired(client, active: bool, admission: str):
    game = client.last_game
    character = client.invite["character_id"]
    if (not active or client.stopping or client.suspended or client.needs_travel or client.server_transitioning
            or not client.transport.game_access or not client.host_session
            or not record.valid_id(client.zone_epoch)
            or (game.get("state"), game.get("map"), client.expected_zone) !=
                ("connected", record.OBSERVATORY, record.OBSERVATORY)):
        return None
    rows = client.gameplay.get(record.STATE, {})
    if not isinstance(rows, dict) or len(rows) != 1:
        return None
    drop_id, row = next(iter(rows.items()))
    if (not record.valid_id(drop_id) or not isinstance(row, dict)
            or (row.get("drop_id"), row.get("owner"), row.get("map"), row.get("class_path")) !=
                (drop_id, character, record.OBSERVATORY, record.ITEM)
            or not record.valid_position(row.get("location"))):
        return None
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if not isinstance(pawn, str) or not _PAWN.fullmatch(pawn):
        return None
    epoch = uuid.uuid5(uuid.NAMESPACE_OID, f"{client.host_session}:{client.zone_epoch}:{character}:{admission}").hex
    return epoch, drop_id, pawn, row["location"]


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
        epoch, drop_id, pawn, _ = desired
        return (proof.get("epoch"), proof.get("drop_id"), proof.get("pawn")) == (epoch, drop_id, pawn)

    async def clear(self, client):
        if self.current is not None or client.last_game.get(KEY):
            client.last_game = await client.bridge.command(CLEAR, timeout=2)
            if client.last_game.get(KEY):
                raise RuntimeError("The stock drop projection did not retire")
        self.current = None
        self.next_sync = 0.0

    async def sync(self, client, active: bool):
        desired = _desired(client, active, self.admission)
        if desired is None:
            if active and client.gameplay.get(record.STATE) == {}:
                self.claimed = None
            await self.clear(client)
            return
        epoch, drop_id, _, xyz = desired
        if self.claimed == (epoch, drop_id):
            await self.clear(client)
            return
        # A stalled read can age the snapshot without changing its owner or scene. Let native hide
        # the actor and renew its same weak identity when the next authenticated frame arrives.
        now = time.monotonic()
        if not _fresh(client) or now < self.next_sync:
            return
        self.next_sync = now + .6
        snapshot = await client.bridge.command(SYNC, epoch, drop_id, *(str(axis) for axis in xyz), timeout=2)
        if not self._matches(snapshot, desired):
            raise RuntimeError("The exact local stock drop projection was unverified")
        client.last_game, self.current = snapshot, desired[:3]
        self.last_error = None

    async def reclaim(self, client, operation: str, target: tuple[str, str]):
        from .native_guest_drop import RECLAIM, RESULT

        desired = _desired(client, True, self.admission)
        if (not _fresh(client) or desired is None or self.current != desired[:3] or desired[:2] != target
                or self.claimed == target
                or not self._matches(client.last_game, desired)):
            raise party.RuleError("native_guest_drop_projection_intent_stale")
        result = await client.gameplay_action(RECLAIM, operation, target[1])
        receipt = result.get(RESULT, {})
        if (receipt.get("state"), receipt.get("drop_id"), receipt.get("owner")) != (
                "reclaimed", target[1], client.invite["character_id"]):
            raise RuntimeError("The exact owner did not receive a stock drop reclaim receipt")
        self.claimed = target
        return result

    async def fixture(self, client, drop_id: str | None):
        desired = _desired(client, True, self.admission)
        if (not _fresh(client) or not record.valid_id(drop_id) or desired is None
                or self.current != desired[:3] or desired[1] != drop_id
                or not self._matches(client.last_game, desired)):
            raise party.RuleError("stock_drop_projection_fixture_scope_unavailable")
        before = client.last_game[KEY]["sequence"]
        snapshot = await client.bridge.command(FIXTURE, drop_id, timeout=4)
        proof = snapshot.get(KEY, {})
        if not self._matches(snapshot, desired) or proof.get("sequence") != before + 1:
            raise RuntimeError("The exact stock drop did not intercept the headless stock press")
        client.last_game = snapshot
        return proof
