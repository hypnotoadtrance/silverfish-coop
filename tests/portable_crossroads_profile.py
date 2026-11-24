"""Time co-op work on isolated seats before and after stock travel to Crossroads."""

import asyncio
from pathlib import Path
import math
import re

from portable_guest_world_start import wait_resident_status
from tools.silverfish.control import current_status, submit


def instrument_profile(source):
    code = (Path(__file__).with_name("fixtures") / "portable_crossroads_profile.lua").read_text(encoding="utf-8")
    anchor = "local function write_status("
    before, separator, rest = source.partition(anchor)
    if not separator:
        raise ValueError("The status publication anchor changed")
    names = r"(?:native_[a-z_]+|players(?:\.critters)?)\.[\w]+"
    wrapper = "status_publication.profile.call"
    rest, protected = re.subn(r"\bpcall\((" + names + r"|tick)(?=[,)])",
                              lambda m: 'pcall(' + wrapper + ', "' + m[1] + '", ' + m[1], rest)
    rest, direct = re.subn(
        r"(?<!function )\b(" + names + r"|inspect|write_status|read_command|observe_session|current_world)\((?=[^)]*\))",
                           lambda m: wrapper + '("' + m[1] + '", ' + m[1]
                           + ("" if rest[m.end()] == ")" else ", "), rest)
    if protected < 10 or direct < 20:
        raise ValueError("The gameplay timing anchors changed")
    mailbox = 'io.open(command_path, "rb")'
    assert before.count(mailbox) == 1
    before = before.replace(mailbox, f'{wrapper}("command_mailbox", io.open, command_path, "rb")')
    return before + "status_publication.profile = (function()\n" + code + "\nend)()\n\n" + anchor + rest


def read_comparison(raw):
    summary, slow_frame = raw.split("!")
    duration, *sections = summary.split(";")
    totals = {}
    for section in sections:
        name, count, p50, p95, p99, maximum, total = section.split(",")
        totals[name] = dict(count=int(count), p50_ms=float(p50), p95_ms=float(p95), p99_ms=float(p99),
                            max_ms=float(maximum), sum_ms=float(total))
    assert float(duration) >= 3 and totals["frame"]["count"] > 10 and totals["write_status"]["count"] > 3
    elapsed, *calls = slow_frame.split(";")
    slow_calls = dict((name, float(total)) for name, total in (entry.split(",") for entry in calls))
    return {"seconds": float(duration), "timings": totals,
            "slowest_interval": {"elapsed_ms": float(elapsed), "inclusive_calls_ms": slow_calls}}


async def profile_world(host, guest, area, probe):
    guest_run = guest.client / "run"
    seats = {"host": host, "guest": guest}

    async def command(seat, action):
        response = await probe(seat.bridge, "crossroads-profile", action, reply_end="|end")
        return response.removesuffix("|end")

    async def arm():
        assert await asyncio.gather(*(command(seat, "arm") for seat in seats.values())) == ["armed", "armed"]

    async def read(seat):
        async with asyncio.timeout(7):
            while (raw := await command(seat, "read")) == "sampling":
                await asyncio.sleep(.16)
        return read_comparison(raw)

    async def collect():
        profiles = await asyncio.gather(*(read(seat) for seat in seats.values()))
        assert all("command_mailbox" not in profile["timings"] for profile in profiles), profiles
        return dict(zip(seats, profiles, strict=True))

    def position(status):
        return status.get("game", {}).get("pawns", {}).get("local", {}).get("location")

    baseline = await wait_resident_status(guest_run, lambda f: f["game"].get("map") == area and position(f))
    await arm()
    idle = await collect()
    await arm()
    # Use normal guest movement with short pulses near the stock arrival point.
    movement = []
    for x, y in ((1, 0), (0, 1), (-1, 0), (0, -1)):
        reply = await submit(guest_run, "walk", x=x, y=y, milliseconds=550, continuous=True)
        assert reply["ok"] and reply["movement_scheduled"], reply
        await asyncio.sleep(.58)
        point = position(await current_status(guest_run))
        if point:
            movement.append(math.dist(point, position(baseline)))
    moving = await collect()
    assert movement and max(movement) >= 40, movement
    return {"idle": idle, "guest_walking": moving, "guest_max_displacement_cm": max(movement)}
