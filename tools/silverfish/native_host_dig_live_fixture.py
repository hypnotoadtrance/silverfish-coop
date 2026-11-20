"""Dispatch headless stock input to the ordinary Missing Contact observer."""

import time

from . import native_host_dig as stock, native_host_dig_live as custody, party
from .native_gold import valid_guid
from .travel import CROSSROADS


ACTION = "native-host-dig-live-fixture"
_PRESS = "native-host-dig-live-press-fixture"
_COLLECT = "native-host-dig-live-collect-fixture"


async def perform(host, bridge, target: str | None, operation_id: str) -> dict:
    if (not host.native_host_dig_live_fixture or not host.native_host_dig_live
            or not host.fixtures or not host.native_guest or not host.native_zones or not host.native_profile
            or host.stopping or host.transitioning or host.party["wipe"] or host.native_checkpoint_unsafe
            or time.monotonic() - host.last_seen >= 3):
        raise party.RuleError("stock_live_dig_fixture_unavailable")
    if (target not in ("acquire", "press", "collect") or not isinstance(operation_id, str)
            or not party.REQUEST_ID.fullmatch(operation_id)):
        raise party.RuleError("stock_live_dig_fixture_arguments")
    if target == "acquire":
        return await stock.perform(host, bridge, stock.ACQUIRE, None, operation_id)

    baseline = custody._report(host)
    pawn = baseline.get("pawn")
    peers = host.native_recipients()
    if (not pawn or not stock._PAWN.fullmatch(pawn) or len(peers) != 1
            or not all(seat.get("connected") for seat in peers.values())
            or any(host.party["players"].get(actor, {}).get("downed") for actor in ("host", *peers))):
        raise party.RuleError("stock_live_dig_fixture_exact_party_required")

    previous = host.party.get(custody.CUSTODY)
    if target == "press":
        tool = host.last_game.get(stock.KEY, {})
        if (previous is not None or baseline.get("state") != "ready" or tool.get("state") != "equipped"
                or (tool.get("map"), tool.get("pawn")) != (CROSSROADS, pawn) or not valid_guid(tool.get("guid"))):
            raise party.RuleError("stock_live_dig_fixture_spade_preflight")
        args = (_PRESS, pawn, tool["guid"])
        settled = {"unclaimed", "escrowed", "failed"}
        expected = "escrowed"
    else:
        if (not previous or previous.get("state") != "escrowed" or baseline.get("state") != "escrowed"
                or not custody._matches(previous, baseline, "escrow_guid") or not baseline.get("actor")):
            raise party.RuleError("stock_live_dig_fixture_custody_unavailable")
        args = (_COLLECT, pawn, str(baseline["serial"]), baseline["actor"])
        settled = {"collected", "owned", "failed"}
        expected = "owned"

    def observed(snapshot):
        report = snapshot.get(custody.KEY, {})
        return (snapshot.get("state"), snapshot.get("map"), report.get("pawn")) == (
            "hosting", CROSSROADS, pawn) and report.get("state") in settled

    try:
        snapshot = await bridge.command(*args, timeout=6)
    except RuntimeError as error:
        if str(error).startswith(("stock_host_dig_live_fixture_", "stock_live_dig_fixture_")):
            raise party.RuleError(str(error)) from error
        raise
    if not observed(snapshot):
        snapshot = await bridge.wait_for(observed, timeout=8)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    if await custody.settle(host, bridge):
        raise RuntimeError("The ordinary stock dig fixture did not reach a durable phase")
    report = custody._report(host, pawn)
    if report.get("state") != expected or host.party.get(custody.CUSTODY, {}).get("state") != expected:
        raise RuntimeError("The ordinary stock dig fixture checkpoint did not verify")
    return {custody.KEY: report, custody.CUSTODY: host.party[custody.CUSTODY]}
