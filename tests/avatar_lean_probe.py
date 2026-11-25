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


FIELDS = ("seconds", "phase", "requested", "owner_roll", "remote_stock_roll", "displayed_roll", "head_roll", "neck_error_cm")


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


async def capture(seat, side, local, remote, *, drive):
    ticket = uuid.uuid4().hex
    root = seat.bridge.directory
    command, reply = (root / f"avatar-lean-probe-{kind}.tsv" for kind in ("command", "reply"))
    operation = f"solver{side}" if drive == "solver" else side
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
            if header[:2] == [ticket, "ok"] and len(header) == 15 and len(lines) == 1 + int(header[6]):
                assert header[2:5] == [side, local, remote] and header[10] == drive, header
                rows = []
                for line in lines[1:]:
                    values = line.split("\t")
                    row = dict(zip(FIELDS, (value if i == 1 else float(value)
                                           for i, value in enumerate(values)), strict=True))
                    assert row["phase"] in ("baseline", "held", "released"), row
                    assert all(math.isfinite(row[key]) for key in FIELDS if key != "phase"), row
                    rows.append(row)
                gaps = [b["seconds"] - a["seconds"] for a, b in zip(rows, rows[1:])]
                assert all(gap > 0 for gap in gaps), rows
                injections, started, completed = map(int, header[7:10])
                return {"caller": local, "observer_for": remote, "side": side, "drive": drive, "samples": rows,
                        "injections": injections, "stock_started": started, "stock_completed": completed,
                        "stock_triggered": int(header[11]),
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
    if result["injections"] < 8:
        status = "insufficient_input_samples"
    elif not (result["stock_started"] >= 1 and result["stock_completed"] >= 1):
        status = "stock_events_unobserved"
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
    assert all(rate["cap_hz"] == 24 for rate in replication_rates.values()), replication_rates
    input_result = {"exercised": False}
    if drive == "combined":
        host_input, guest_input = await asyncio.gather(
            capture(host, "left", *host_pawns, drive="input"), capture(guest, "left", *guest_pawns, drive="input"),
            return_exceptions=True,
        )
        input_result = {"exercised": True, "side": "left", "host": input_diagnostic(host_input), "guest": input_diagnostic(guest_input)}
    input_verified = (drive == "combined" and input_result["host"]["verified"] and input_result["guest"]["verified"])
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
