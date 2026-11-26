"""Drive a short stock lean capture in both seats of a private embedded Observatory session."""

import asyncio
from contextlib import contextmanager
import math
from pathlib import Path
import shutil
from statistics import median
import uuid

from live_ai_smoke import wait
from tools.silverfish import runtime


FIELDS = ("seconds", "phase", "requested", "owner_roll", "remote_stock_roll", "displayed_roll",
          "head_roll", "neck_error_cm", "injection_present", "input_trigger", "input_elapsed_seconds",
          "controller_loading", "open_menus", "move_input_ignored", "look_input_ignored", "same_player_input")


@contextmanager
def installed_probe(game: Path):
    game = game.resolve(strict=True)
    mods = game / runtime.SHIPPING.parent / "ue4ss/Mods"
    if "steamapps" in {part.casefold() for part in game.parts} or mods.resolve(strict=True) != mods:
        raise ValueError("Use the private replica's ordinary mod directory")
    suite = mods / "SilverfishAvatarLeanProbe"
    suite.mkdir()
    scripts, enabled = suite / "Scripts", suite / "enabled.txt"
    script = scripts / "main.lua"
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / "avatar_lean_probe.lua", script)
        enabled.touch(exist_ok=False)
        yield
    finally:
        if suite.resolve(strict=True) != suite or scripts.resolve() != scripts:
            raise RuntimeError("The isolated lean fixture path changed during the test")
        enabled.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        suite.rmdir()


async def capture(seat, side, local, remote, *, drive, extended_hold=False):
    ticket = uuid.uuid4().hex
    root = seat.bridge.directory
    command, reply = (root / f"avatar-lean-probe-{kind}.tsv" for kind in ("command", "reply"))
    operation = f"solver{side}" if drive == "solver" else f"hold{side}" if extended_hold else side
    runtime.write_text(command, f"{ticket}\t{operation}\n", newline="")
    async with asyncio.timeout(9):
        while True:
            process = seat.bridge.process
            if process.poll() is not None:
                raise RuntimeError(f"The lean fixture process exited ({process.returncode})")
            try:
                text = reply.read_text(encoding="utf-8")
                lines = text.splitlines() if text.endswith("\n") else []
            except (FileNotFoundError, PermissionError):
                lines = []
            header = lines[0].split("\t") if lines else []
            if header[:2] == [ticket, "error"]:
                raise AssertionError(" ".join(header[2:]))
            if header[:2] == [ticket, "ok"] and len(header) == 19:
                assert header[2:5] == [side, local, remote] and header[10] == drive, header
                count, event_count = int(header[6]), int(header[16])
                if len(lines) != 1 + count + event_count:
                    await asyncio.sleep(.09)
                    continue
                rows = []
                for line in lines[1:1 + count]:
                    values = line.split("\t")
                    row = dict(zip(FIELDS, (value if i == 1 else float(value)
                                           for i, value in enumerate(values)), strict=True))
                    assert row["phase"] in ("baseline", "held", "released"), row
                    assert all(math.isfinite(row[key]) for key in FIELDS if key != "phase"), row
                    rows.append(row)
                gaps = [b["seconds"] - a["seconds"] for a, b in zip(rows, rows[1:])]
                assert all(gap > 0 for gap in gaps), rows
                events = []
                for line in lines[1 + count:]:
                    event, *values = line.split("\t")
                    assert event in {"starts", "cancels", "completes", "cancel_leaning"}, line
                    seconds, requested, leaning = map(float, values)
                    events.append({"event": event, "seconds": seconds, "requested": requested, "leaning": leaning == 1})
                dispatch = {}
                for window, raw in zip(("held", "total"), header[17:19], strict=True):
                    if raw == "-":
                        continue
                    counts = [int(value) for value in raw.split(",")]
                    assert len(counts) == 12 and all(count >= 0 for count in counts), counts
                    dispatch[window] = {
                        phase: dict(zip(("started", "canceled", "completed"), counts[index * 3:index * 3 + 3], strict=True))
                        for index, phase in enumerate(("process_event_entry", "process_event_return", "blueprint_entry",
                                                       "prevented"))
                    }
                injections, started, completed = map(int, header[7:10])
                return {"caller": local, "observer_for": remote, "side": side, "drive": drive, "samples": rows,
                        "events": events, "native_dispatch": dispatch,
                        "injections": injections, "input_method": header[15],
                        "stock_started": started, "stock_completed": completed,
                        "stock_canceled": int(header[11]),
                        "started_requested_degrees": float(header[12]) if header[12] != "-" else None,
                        "started_limit_degrees": float(header[13]) if header[13] != "-" else None,
                        "lean_behavior": int(header[14]) if header[14] != "-" else None,
                        "median_interval_seconds": median(gaps), "final_requested_degrees": float(header[5])}
            await asyncio.sleep(.09)


def input_diagnostic(result):
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, Exception):
        return {"verified": False, "status": "fixture_unavailable", "reason": str(result)}
    held = [row for row in result["samples"] if row["phase"] == "held"]
    sign = -1 if result["side"] == "left" else 1
    minimum_injections = 1 if result["input_method"] == "continuous" else 8
    dispatch = result["native_dispatch"]
    if dispatch and dispatch.keys() != {"held", "total"}:
        return {"verified": False, "status": "incomplete_stock_dispatch_trace", "trace": result}
    if dispatch:
        started = dispatch["held"]["blueprint_entry"]["started"]
        completed = dispatch["total"]["blueprint_entry"]["completed"]
    else:
        started, completed = result["stock_started"], result["stock_completed"]
    if result["injections"] < minimum_injections:
        status = "insufficient_input_samples"
    elif not started:
        status = "stock_started_unobserved"
    elif not completed:
        status = "stock_completed_unobserved"
    elif len(held) < 8 or sign * median(row["requested"] for row in held[-4:]) <= 6:
        status = "stock_request_unobserved"
    elif sign * median(row["owner_roll"] for row in held[-4:]) <= 6:
        status = "stock_roll_unobserved"
    elif abs(result["final_requested_degrees"]) >= .01 or abs(result["samples"][-1]["owner_roll"]) >= 1:
        status = "stock_release_unobserved"
    else:
        status = "verified"
    return {"verified": status == "verified", "status": status, "trace": result}


async def verify_lean(host, guest, pawn, *, projected=True, drive="combined"):
    if drive not in {"combined", "input", "solver"}:
        raise ValueError("Use combined, input, or solver lean verification")

    def ready(state):
        game = state.get("game", {})
        local = game.get("pawns", {}).get("local", {}).get("name")
        rows = game.get("native_player_avatars", {})
        if local and len(rows) == 1:
            remote, avatar = next(iter(rows.items()))
            if avatar["state"] == "default_body" and not avatar.get("diagnostic"):
                return local, remote

    host_status, guest_status = await asyncio.gather(*(
        wait(run, lambda state: state.get("local_bridge") == "memory" and ready(state))
        for run in (host.campaign / "run", guest.client / "run")
    ))
    host_pawns, guest_pawns = ready(host_status), ready(guest_status)
    assert host_pawns[1] == pawn, host_pawns
    replication_rates = host_status["game"].get("native_replication_rates", {})
    assert all(rate["cap_hz"] == min(rate["stock_hz"], 20) for rate in replication_rates.values()), replication_rates
    input_result = {"exercised": False}
    input_verified = False
    if drive == "combined":
        input_result["exercised"] = True
        for attempt, (host_side, guest_side) in enumerate((("left", "right"), ("right", "left"))):
            host_input, guest_input = await asyncio.gather(
                capture(host, host_side, *host_pawns, drive="input", extended_hold=attempt == 0),
                capture(guest, guest_side, *guest_pawns, drive="input", extended_hold=attempt == 0),
                return_exceptions=True,
            )
            input_result.setdefault(host_side, {})["host"] = input_diagnostic(host_input)
            input_result.setdefault(guest_side, {})["guest"] = input_diagnostic(guest_input)
        input_verified = all(input_result[side][seat]["verified"] for side in ("left", "right") for seat in ("host", "guest"))
    wave_drive = "input" if drive == "input" or input_verified else "solver"
    waves = []
    for host_side, guest_side in (("left", "right"), ("right", "left")):
        host_trial, guest_trial = await asyncio.gather(
            capture(host, host_side, *host_pawns, drive=wave_drive), capture(guest, guest_side, *guest_pawns, drive=wave_drive),
        )
        for owner, display in ((host_trial, guest_trial), (guest_trial, host_trial)):
            owner_held = [row for row in owner["samples"] if row["phase"] == "held"]
            display_held = [row for row in display["samples"] if row["phase"] == "held"]
            assert min(len(owner_held), len(display_held)) >= 8, (owner, display)
            owner_steady = median(row["owner_roll"] for row in owner_held[-4:])
            displayed_steady = median(row["displayed_roll"] for row in display_held[-4:])
            sign = -1 if owner["side"] == "left" else 1
            assert sign * owner_steady > 6, owner
            if wave_drive == "solver":
                assert owner["injections"] == owner["stock_started"] == owner["stock_completed"] == 0, owner
                assert abs(owner["final_requested_degrees"]) < .01, owner
            else:
                assert input_diagnostic(owner)["verified"], owner
            assert all(abs(row["displayed_roll"]) < 1 for row in display["samples"] if row["phase"] == "baseline"), display
            assert abs(owner["samples"][-1]["owner_roll"]) < 1 and abs(display["samples"][-1]["displayed_roll"]) < 1, (owner, display)
            assert max(abs(row["remote_stock_roll"]) for row in display["samples"]) < .5, display
            assert max(row["neck_error_cm"] for row in display_held[-4:]) < .35, display
            if projected:
                assert abs(displayed_steady - owner_steady) < 2.5, (owner, display)
                assert any(1 < sign * row["displayed_roll"] < sign * owner_steady - 1 for row in display_held), display
            else:
                assert max(abs(row["displayed_roll"]) for row in display["samples"]) < 1, display
            owner["steady_owner_degrees"] = owner_steady
            owner["steady_observed_degrees"] = displayed_steady
        waves.append({"host": host_trial, "guest": guest_trial})
    if drive == "input":
        input_result = {"exercised": True, "strict_waves_passed": True}
    return {"projected": projected, "game_thread_sample_interval_ms": 50, "stock_replication_rates": replication_rates,
            "stock_input": input_result, "wave_drive": wave_drive,
            "waves": waves}
