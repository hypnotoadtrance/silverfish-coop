"""Relay stock cash edits as cumulative changes from each admitted character."""

import asyncio
import contextlib
import re
import time
import uuid

from . import party


REPORT = "wallet-guest-report"
SYNC = "wallet-guest-sync"
CHANGE = "wallet_guest_change"
ACK = "wallet_guest_ack"
DEBT = "shared_debt"
DEBT_STATUS = "native_debt_balance"
MAX_TOTAL = 2**53 - 1
MIN_CASH, MAX_CASH = -(2**31), 2**31 - 1
_NUMBER = re.compile(r"-?[0-9]{1,16}")


def parse_sequence(sequence, total, *, minimum=1):
    if not (_NUMBER.fullmatch(sequence) and _NUMBER.fullmatch(total)):
        raise ValueError("Invalid guest wallet counters")
    sequence, total = int(sequence), int(total)
    if not (minimum <= sequence <= MAX_TOTAL and -MAX_TOTAL <= total <= MAX_TOTAL):
        raise ValueError("Invalid guest wallet counters")
    return sequence, total


def parse_change(fields):
    _, session, stream, sequence, total = fields
    if not (party.REQUEST_ID.fullmatch(session) and party.REQUEST_ID.fullmatch(stream)):
        raise ValueError("Invalid guest wallet scope")
    return session, stream, *parse_sequence(sequence, total)


async def settle(host, bridge, character, target, operation):
    try:
        session, stream, sequence, total = parse_change((REPORT, *target.split("@")))
        owner = uuid.UUID(character).hex
        if not party.REQUEST_ID.fullmatch(operation):
            raise ValueError("Invalid guest wallet operation")
    except (AttributeError, TypeError, ValueError) as error:
        raise party.RuleError("invalid_guest_wallet_report") from error
    if (session != host.control.generation or bridge.local_bridge is None or not host.native_guest
            or not host.native_profile or host.stopping or host.transitioning or host.native_checkpoint_unsafe
            or host.last_game.get("state") != "hosting"):
        raise party.RuleError("guest_wallet_unavailable")

    snapshot = await bridge.command(REPORT, session, owner, stream, str(sequence), str(total), timeout=3)
    message = snapshot.get("ack", {}).get("message")
    counters = message.split(" ") if isinstance(message, str) else ()
    if len(counters) != 2:
        raise RuntimeError("The stock wallet receipt did not include its counters")
    accepted, applied = parse_sequence(*counters)
    balance = snapshot.get("native_wallet")
    if ((snapshot.get("state"), snapshot.get("map")) != ("hosting", host.last_game.get("map"))
            or type(balance) is not int or not MIN_CASH <= balance <= MAX_CASH
            or accepted < sequence or accepted == sequence and applied != total):
        raise RuntimeError("The stock wallet did not verify its guest report")
    host.last_game, host.last_seen = snapshot, time.monotonic()
    host.party["economy"].update(wallet=balance, native_wallet_active=True)
    receipt = [stream, accepted, applied]
    if host.guest_wallet_receipts.get(character) != receipt:
        host.guest_wallet_receipts[character] = receipt
        host.control.event("native_guest_wallet_settled", character=character, sequence=accepted,
                           reported_total=applied, balance=balance)
    return {ACK: receipt}


class GuestWallet:
    def __init__(self):
        self.stream = uuid.uuid4().hex
        self.projection = None
        self.report = None
        self.last_report = None
        self.retry_after = 0.0

    async def close(self):
        if self.report is not None:
            self.report.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.report
            self.report = None

    async def _send(self, client, change):
        session, _, sequence, _ = change
        target = "@".join(map(str, change))
        operation = uuid.uuid5(uuid.UUID(self.stream), f"{session}:{sequence}").hex
        try:
            await client.gameplay_action(REPORT, operation, target)
        except (OSError, RuntimeError, ValueError) as error:
            client.control.event("native_guest_wallet_report_deferred", reason=str(error))

    async def sync(self, client):
        if self.report is not None and self.report.done():
            await self.report
            self.report = None
        scope = client.stock_interaction_scope()
        balance = client.gameplay.get("shared_wallet")
        if (not scope or client.bridge.local_bridge is None or ACK not in client.gameplay
                or type(balance) is not int or not MIN_CASH <= balance <= MAX_CASH
                or time.monotonic() - client.gameplay_seen >= 3):
            return
        session = scope[0]
        ack = client.gameplay[ACK]
        sequence, total = 0, 0
        if isinstance(ack, list) and len(ack) == 3 and ack[0] == self.stream:
            _, sequence, total = ack
            if (type(sequence) is not int or not 0 < sequence <= MAX_TOTAL
                    or type(total) is not int or abs(total) > MAX_TOTAL):
                return
        if self.projection and self.projection[0][0] == session and sequence < self.projection[2]:
            return
        debt = client.gameplay.get(DEBT)
        if type(debt) is not int or not MIN_CASH <= debt <= MAX_CASH:
            debt = None
        projection = scope, balance, sequence, total, debt
        if projection != self.projection:
            amounts = (balance, sequence, total) if debt is None else (balance, sequence, total, debt)
            try:
                await client.bridge.command(SYNC, session, self.stream, *map(str, amounts),
                                            timeout=2, update_status=False)
            except (OSError, RuntimeError, ValueError) as error:
                client.control.event("native_guest_wallet_projection_deferred", reason=str(error))
                return
            self.projection = projection
        if client.stock_interaction_scope() != scope:
            return

        game = client.bridge.snapshot() or client.last_game
        if (change := game.get(CHANGE)) and change[:2] == (session, self.stream) and change[2] > sequence:
            if self.report is None and (change != self.last_report or time.monotonic() >= self.retry_after):
                self.last_report = change
                self.retry_after = time.monotonic() + 1.0
                self.report = asyncio.create_task(self._send(client, change))
