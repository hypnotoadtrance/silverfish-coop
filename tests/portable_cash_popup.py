"""Verify a committed pickup reaches both seats' stock cash widget."""

import asyncio
from contextlib import contextmanager
import math
import uuid

from portable_guest_world_start import resident_events, wait_resident_status
from tools.silverfish import native_authored_pickup as pickup, native_wallet
from tools.silverfish.travel import CROSSROADS


@contextmanager
def cash_pickup_overlay(game, overlay):
    gate = 'if phase ~= "hosting" or not fixtures or not host_fixtures then\n            error("The stock loose pickup positioning fixture is unavailable", 0)'

    def enable(script):
        assert script.count(gate) == 1
        return script.replace(gate, gate.replace("not host_fixtures", "not (host_fixtures or title_fixtures)"))

    def allow_title_approach(script):
        guard = "not approach_scope(state, phase, pawn, actor, quest_source and profile.map or sources[profile.source])"
        assert script.count(guard) == 1
        return script.replace(guard, guard[:-1] + ', os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1")')

    def wallet_fixture(script):
        marker = "function wallet.rows(state, phase, status_row)\n"
        assert script.count(marker) == 1
        fixture = r"""local fixture_bridge = os.getenv("SILVERFISH_COOP_HEADLESS") == "1"
    and os.getenv("SILVERFISH_COOP_TITLE_FIXTURES") == "1" and os.getenv("SILVERFISH_COOP_BRIDGE")
local wallet_request = fixture_bridge and fixture_bridge .. "/portable-shared-wallet-command.tsv"
local wallet_reply = fixture_bridge and fixture_bridge .. "/portable-shared-wallet-reply.tsv"
local fixture_ticket, fixture_reply, next_fixture_check = nil, nil, 0

local function edit_fixture_wallet(state, phase, kind, amount)
    local delta = assert(integer(amount, -100, 100))
    local key = FName("Player_Cash")
    if phase == "connected" then
        local save = assert(save_scope.bind_guest(state, phase))
        if not guest or guest.save ~= save:GetAddress() then return "wallet not bound" end
        local before = read_projected(save, key)
        assert(write_projected(save, key, before + delta))
        assert(wallet.project_guest(save, before))
        assert(read_projected(save, key) == before + delta)
        return ("%d %d"):format(before, before + delta)
    end
    local instance = game_instance(assert(host_pawn(state, phase)))
    local before = read_cash(instance, key)
    if kind == "stock" then
        instance:Give_PlayerCash(math.abs(delta), delta < 0)
        assert(read_cash(instance, key) == before + delta)
    else
        write_cash(instance, key, before + delta)
    end
    return ("%d %d"):format(before, before + delta)
end

local function poll_fixture_wallet(state, phase)
    if not wallet_request or state.map ~= "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
        or (phase ~= "hosting" and phase ~= "connected") then return end
    local now = os.clock()
    if now < next_fixture_check then return end
    next_fixture_check = now + .05
    if not fixture_reply then
        local input = io.open(wallet_request, "rb")
        if not input then return end
        local request = input:read(128)
        input:close()
        local ticket, kind, amount = (request or ""):match("^(%x+)\t([a-z]+)\t(%-?%d+)$")
        if not token(ticket) or fixture_ticket == ticket or (kind ~= "flag" and kind ~= "stock") then return end
        fixture_ticket = ticket
        local ok, result = pcall(edit_fixture_wallet, state, phase, kind, amount)
        fixture_reply = ticket .. (ok and "\tok\t" or "\terror\t") .. tostring(result):gsub("[\t\r\n]", " ") .. "|end"
    end
    local output = io.open(wallet_reply, "wb")
    if not output then return end
    local written = output:write(fixture_reply)
    local closed = output:close()
    -- Retain the reply on a write failure. The ticket has already consumed the edit.
    if written and closed then fixture_reply = nil end
end

"""
        return script.replace(marker, fixture + marker + "    poll_fixture_wallet(state, phase)\n", 1)

    with overlay(game, "native_authored_pickup.lua", enable), overlay(
        game, "native_authored_pickup_input_fixture.lua", allow_title_approach
    ), overlay(game, "wallet.lua", wallet_fixture):
        yield


async def verify_reported_wallet(host, guest, _pawn, command, submit, save_hashes):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    checkpoint = save_hashes(host.campaign)
    start = await wait_resident_status(guest_run, lambda frame:
        native_wallet.ACK in frame.get("gameplay", {}) and type(frame["gameplay"]["shared_wallet"]) is int
        and frame["game"].get("native_wallet") == frame["gameplay"]["shared_wallet"])
    initial = start["gameplay"]["shared_wallet"]

    async def edit(seat, kind, amount):
        reply = await command(seat.bridge, "portable-shared-wallet", f"{kind}\t{amount}", reply_end="|end")
        return reply.removesuffix("|end")

    async with asyncio.timeout(8):
        while await edit(guest, "flag", 0) == "wallet not bound":
            await asyncio.sleep(.08)

    # A host credit remains part of the canonical balance when the guest submits its delta.
    host_credit, guest_grant = await asyncio.gather(edit(host, "stock", 37), edit(guest, "flag", 50))
    before, after = map(int, host_credit.split())
    assert after - before == 37
    before, after = map(int, guest_grant.split())
    assert after - before == 50

    async def converged(balance, sequence):
        return await wait_resident_status(guest_run, lambda frame:
            frame.get("game", {}).get("native_wallet") == balance
            and frame.get("gameplay", {}).get("shared_wallet") == balance
            and bool(ack := frame["gameplay"].get(native_wallet.ACK))
            and ack[1] == sequence)

    granted = await converged(initial + 87, 1)
    stream, sequence, total = granted["gameplay"][native_wallet.ACK]
    assert total == 50
    assert await edit(guest, "flag", -13) == f"{initial + 87} {initial + 74}"
    await converged(initial + 74, 2)
    replay = "@".join((granted["host_session"], stream, str(sequence), str(total)))
    repeated = await submit(guest_run, native_wallet.REPORT, target=replay, operation_id=uuid.uuid4().hex)
    assert repeated["ok"] and repeated[native_wallet.ACK] == [stream, 2, 37], repeated
    await converged(initial + 74, 2)
    settled = await wait_resident_status(host_run, lambda frame:
                                         frame.get("game", {}).get("native_wallet") == initial + 74)
    events = [row for row in await resident_events(host_run, settled) if row["event"] == "native_guest_wallet_settled"]
    assert [row["reported_total"] for row in events] == [50, 37], events
    assert save_hashes(host.campaign) == checkpoint
    return {"guest_reported_wallet": {"initial": initial, "host_grant": 37, "guest_grant": 50, "guest_deduction": -13,
                                      "final": initial + 74, "replay_ack": repeated[native_wallet.ACK],
                                      "campaign_checkpoint_unchanged": True}}


async def verify_stock_cash_popup(host, guest, pawn, command, save_hashes):
    source = "crossroads-small-stack-1"
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    start = await wait_resident_status(host_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
        and frame["game"].get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == "available")
    amount = start["game"][pickup.SOURCES_KEY][source]["wallet_value"]
    balance = start["game"]["native_wallet"]
    assert pickup.valid_cash_amount(source, amount)
    checkpoint = save_hashes(host.campaign)

    async def notice(seat, request):
        return (await command(seat.bridge, "portable-cash-popup", request)).strip()

    assert await asyncio.gather(*(notice(seat, "arm") for seat in (host, guest))) == ["armed", "armed"]
    staged = await host.bridge.command(pickup.APPROACH, pawn, source, timeout=6)
    position, = [row["location"] for row in staged["pawns"].values() if row.get("name") == pawn]
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("game", {}).get("map") == CROSSROADS
        and frame["game"].get(pickup.SOURCES_KEY, {}).get(source, {}).get("state") == "available"
        and math.dist(frame["game"]["pawns"].get("local", {}).get("location", [0] * 3)[:2], position[:2]) < 70)
    pressed = await guest.bridge.command(pickup.STOCK_FIXTURE, source, timeout=6)
    assert pressed["ack"]["message"] == "exact stock loose pickup input consumed", pressed["ack"]
    settled = await wait_resident_status(host_run, lambda frame: frame.get("gameplay", {})
        .get(pickup.STATE_KEY, {}).get(source, {}).get("wallet_credit", {}).get("after") == balance + amount)
    await wait_resident_status(guest_run, lambda frame: frame.get("gameplay", {}).get("shared_wallet") == balance + amount
        and frame.get("game", {}).get("native_wallet") == balance + amount)

    expected = f"RECEIVED ${amount}!"
    readings = {}
    try:
        async with asyncio.timeout(8):
            while True:
                readings = dict(zip(("host", "guest"), await asyncio.gather(*(notice(seat, "read") for seat in (host, guest)))))
                if set(readings.values()) == {expected}:
                    break
                await asyncio.sleep(.08)
    except TimeoutError as error:
        raise AssertionError(f"Expected {expected} once on each seat, saw {readings}") from error
    await asyncio.sleep(.8)
    assert await asyncio.gather(*(notice(seat, "read") for seat in (host, guest))) == [expected, expected]
    notices = [event for event in await resident_events(guest_run, guest_frame)
               if event.get("event") == "native_cash_popup_queued"]
    committed = [event for event in await resident_events(host_run, start)
                 if event.get("event") == "native_authored_pickup_committed" and event.get("source") == source]
    event, = committed
    receipt, = [notice for notice in notices if notice.get("operation_id") == event["operation_id"]]
    assert receipt.get("amount") == amount
    assert save_hashes(host.campaign) == checkpoint
    return {"stock_cash_popup": {"source": source, "amount": amount, "before": balance,
            "after": balance + amount, "views": readings, "campaign_checkpoint_unchanged": True,
            "credit": settled["gameplay"][pickup.STATE_KEY][source]["wallet_credit"]}}
