"""Verify stock sickness is scoped to one detached guest across a host restart."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
from struct import pack, unpack_from
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes
from live_reconnect_grace_smoke import OBSERVATORY, exact_identity, manifest_digest, native_guest, status
from live_wanderer_preset_smoke import verified_slot


REPO = Path(__file__).resolve().parents[1]
DOSE = 120
RESTART_FLOOR = 16
COUNTERS = ("infect_reads", "infect_writes", "slow_reads", "slow_writes")
VALUES = ("guest_sickness", "host_sickness", "guest_before", "guest_after")


def tagged_name(name: str) -> bytes:
    return pack("<i", len(name) + 1) + name.encode("ascii") + b"\0"


FLAGS = tagged_name("Flags")
FLAGS_TAG = (FLAGS + tagged_name("MapProperty") + pack("<i", 2)
             + tagged_name("NameProperty") + pack("<i", 0) + tagged_name("IntProperty") + pack("<i", 0))


def saved_sickness(payload: bytes) -> int | None:
    """Read the exact stock UE 5.4 Flags TMap payload from a verified GVAS frame."""
    if not payload.startswith(b"GVAS"):
        raise AssertionError("Expected a stock GVAS save")
    offset = payload.find(FLAGS_TAG)
    if offset == -1:
        if FLAGS in payload:
            raise AssertionError("The saved stock Flags tag changed shape")
        return None
    if payload.find(FLAGS_TAG, offset + 1) != -1:
        raise AssertionError("Multiple stock Flags tags in the save")
    offset += len(FLAGS_TAG)
    size, tag_flags = unpack_from("<iB", payload, offset)
    offset += 5
    finish = offset + size
    if tag_flags != 0 or size < 8 or finish > len(payload):
        raise AssertionError("The saved stock Flags payload is unqualified")
    removed, count = unpack_from("<ii", payload, offset)
    offset += 8
    if removed != 0 or not 0 <= count <= 16384:
        raise AssertionError("The saved stock Flags map is unqualified")
    values = {}
    for _ in range(count):
        if offset + 4 > finish:
            raise AssertionError("The stock Flags map ended before an entry")
        length, = unpack_from("<i", payload, offset)
        offset += 4
        wide = length < 0
        width = 2 if wide else 1
        size = abs(length) * width
        if size < width or offset + size + 4 > finish:
            raise AssertionError("Invalid stock Flags name size")
        name = payload[offset:offset + size].decode("utf-16-le" if wide else "utf-8")
        offset += size
        if not name.endswith("\0") or "\0" in name[:-1] or name[:-1].casefold() in values:
            raise AssertionError("Invalid or repeated stock Flags name")
        value, = unpack_from("<i", payload, offset)
        offset += 4
        values[name[:-1].casefold()] = value
    if offset != finish:
        raise AssertionError("The stock Flags map size did not match its entries")
    return values.get("sickness")


def sickness_row(server: dict, pawn: str | None) -> dict:
    return server.get("game", {}).get("native_guest_sickness", {}).get(pawn, {})


def qualified_row(row: dict) -> bool:
    return (row.get("state") in {"watching", "redirected_infection", "redirected_sickness_tick"}
            and all(type(row.get(key)) is int and row[key] >= 0 for key in COUNTERS)
            and all(type(row.get(key)) is int for key in VALUES))


def verify_dose(reply: dict, pawn: str, amount: int, before: dict, canonical: int) -> dict:
    receipt = reply["native_guest_sickness_fixture"]
    after = reply["native_guest_sickness"]
    expected = {"state": "stock_infection_verified", "map": OBSERVATORY, "pawn": pawn, "amount": amount}
    if (receipt != expected or not qualified_row(after) or after["state"] != "redirected_infection"
            or after["host_sickness"] != canonical or after["guest_after"] != after["guest_sickness"]
            or after["infect_reads"] != before["infect_reads"] + 1 or after["infect_writes"] != before["infect_writes"] + 1
            or any(after[key] < before[key] for key in ("slow_reads", "slow_writes"))):
        raise AssertionError({"before": before, "receipt": receipt, "after": after})
    delta = after["guest_after"] - after["guest_before"]
    if (amount > 0 and not 1 <= delta <= amount) or (amount < 0 and (after["guest_before"] <= 0 or delta != amount)):
        raise AssertionError({"amount": amount, "observed_delta": delta, "proof": after})
    return {"receipt": receipt, "proof": after}


def checkpoint_sickness(saves: Path, campaign: dict, character: str) -> dict:
    host = verified_slot(saves, campaign["world"]["native_host_save"])
    selected, = (entry for entry in campaign["characters"] if entry["character_id"] == character)
    guest = verified_slot(saves, selected["state"]["native_guest_save"], character=character)
    if host["slot"] == guest["slot"]:
        raise AssertionError("Host and guest shared an immutable save slot")
    return {"host_frame": host, "guest_frame": guest,
            "host_sickness": saved_sickness((saves / f"{host['slot']}.sav").read_bytes()),
            "guest_sickness": saved_sickness((saves / f"{guest['slot']}.sav").read_bytes())}


async def verify(work: Path, original: Path) -> None:
    if os.name != "nt":
        raise OSError("The live sickness test requires the staged Windows game")
    name = "guest-sickness-" + uuid.uuid4().hex[:8]
    host_root = work / "campaigns" / name
    host_run, guest_run = host_root / "run", work / "clients" / name / "run"
    saves = host_root / "game-user" / "Saved" / "SaveGames"
    host_run.mkdir(parents=True)
    guest_run.mkdir(parents=True)
    print(host_run, flush=True)
    primary, secondary = ("--campaign", name), ("--profile", name)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    host_flags = ("--headless", "--fixtures", "--native-zones", "--native-events", "--native-guest", "--native-guest-sickness")
    baseline = saved_hashes(original)
    if not baseline:
        raise ValueError("The original Saved directory must contain files")
    proof = {"original_files": len(baseline), "original_manifest_sha256": manifest_digest(baseline), "positive_doses": []}
    processes = []

    async def start(kind, target, run, log, *arguments):
        with (run / log).open("wb") as output:
            process = await asyncio.create_subprocess_exec(*cli, kind, *target, *arguments, stdout=output,
                stderr=output, cwd=REPO, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, target))
        return process

    async def command(*args, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=REPO, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if not stdout or (process.returncode == 0) != expected:
            raise RuntimeError(f"{args[0]} failed: {(stdout + stderr).decode(errors='replace')}")
        result = json.loads(stdout)
        if result.get("ok") is not expected and not (expected and args[0] == "events" and isinstance(result.get("events"), list)):
            raise RuntimeError(f"{args[0]} returned {result}")
        return result

    async def action(verb, *args, expected=True):
        return await command("action", *primary, verb, *args, expected=expected)

    async def wait(label, predicate, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            server, client = status(host_run), status(guest_run)
            if predicate(server, client):
                return server, client
            if "failed" in (server.get("state"), client.get("state")):
                break
            await asyncio.sleep(0.1)
        raise RuntimeError(label)

    def paired(server, client):
        guest = native_guest(server, character)
        return (server.get("state") == "hosting" and client.get("state") == "connected" and guest
                and client.get("link_connected") is True and client.get("gameplay_fresh") is True
                and client.get("character_id") == character and client.get("host_session") == server.get("generation")
                and client.get("zone_epoch") == server.get("zone_epoch") and client.get("game", {}).get("map") == OBSERVATORY
                and guest.get("downed") is False and qualified_row(sickness_row(server, guest["pawn"])))

    def owner_state(server, guest, expected_identity=None):
        local = server["game"]["pawns"]["local"]
        rows = server["game"].get("native_guest_sickness", {})
        row = rows.get(guest["pawn"], {})
        if (expected_identity is not None and exact_identity(guest) != expected_identity
                or local["name"] == guest["pawn"] or set(rows) != {guest["pawn"]} or not qualified_row(row)
                or row["host_sickness"] != canonical or not math.isclose(local["health"], host_hp, abs_tol=1e-6)
                or not math.isclose(guest["health"], guest_hp, abs_tol=1e-6)):
            raise AssertionError({"host": local, "guest": guest, "sickness_rows": rows})
        return row

    async def dose(guest, before, amount):
        reply = await action("native-guest-sickness-fixture", "--target", "guest", "--amount", str(amount))
        return verify_dose(reply, guest["pawn"], amount, before, canonical)

    try:
        host = await start("host", primary, host_run, "host-first.txt", "--port", "0", "--start-zone", OBSERVATORY, *host_flags)
        first, _ = await wait("The stock Observatory listen host did not start", lambda server, _: server.get("state") == "hosting")
        port, generation = first["endpoint"]["port"], first["generation"]
        rules = first["game"].get("native_rules", {})
        if first["game"].get("map") != OBSERVATORY or rules.get("extraction_mode") != 0:
            raise AssertionError({"map": first["game"].get("map"), "rules": rules})
        invitation = await command("invite", *primary, "--name", "Stock sickness guest", "--character", "Sickness observer")
        character = invitation["character_id"]
        proof["character_id"] = character
        await start("join", secondary, guest_run, "join.txt", "--headless", "--native-zones", "--invite", invitation["invite_file"])
        server, _ = await wait("The exact detached guest sickness scope was not ready", paired)
        guest = native_guest(server, character)
        initial = sickness_row(server, guest["pawn"])
        canonical = initial["host_sickness"]
        host_hp, guest_hp = server["game"]["pawns"]["local"]["health"], guest["health"]
        if not (math.isfinite(host_hp) and host_hp > 0 and math.isfinite(guest_hp) and guest_hp > 0
                and initial["guest_sickness"] == initial["infect_reads"] == initial["infect_writes"] == 0):
            raise AssertionError({"initial": initial, "host_hp": host_hp, "guest_hp": guest_hp})
        proof["initial"] = {"guest": guest, "native": owner_state(server, guest, exact_identity(guest)),
                            "canonical_host_health": host_hp, "stock_extraction_mode": rules["extraction_mode"]}

        proof["zero_refused"] = await action("native-guest-sickness-fixture", "--target", "guest", "--amount", "0", expected=False)
        server, _ = await wait("No fresh sickness frame after refused zero dose", lambda left, right:
            paired(left, right) and left["game"]["sequence"] > server["game"]["sequence"])
        before = owner_state(server, native_guest(server, character), exact_identity(guest))
        if (before["guest_sickness"], before["infect_reads"], before["infect_writes"]) != (0, 0, 0):
            raise AssertionError({"zero_dose_changed_detached_sickness": before})

        last = before
        for _ in range(3):
            added = await dose(guest, last, DOSE)
            proof["positive_doses"].append(added)
            last = added["proof"]
            if last["guest_sickness"] >= RESTART_FLOOR:
                break
        if last["guest_sickness"] < RESTART_FLOOR:
            raise AssertionError({"stock_infection_too_low_for_restart_probe": last})
        await action("checkpoint")
        await action("stop")
        await asyncio.wait_for(host.wait(), 15)
        stopped = status(host_run)
        prior = checkpoint_sickness(saves, stopped["campaign"], character)
        persisted = prior["guest_sickness"]
        if (stopped.get("state") != "stopped" or type(persisted) is not int or not 0 < persisted <= last["guest_after"]
                or (prior["host_sickness"] or 0) != canonical):
            raise AssertionError({"terminal_stock_infection": last, "stopped_checkpoint": prior})
        proof["saved_before_restart"] = prior

        await start("host", primary, host_run, "host-restart.txt", "--port", str(port), *host_flags)
        returned, _ = await wait("The original character did not rebind its detached sickness after same-port restart",
            lambda left, right: left.get("generation") != generation and paired(left, right))
        restored_guest = native_guest(returned, character)
        restored = owner_state(returned, restored_guest)
        events = (await command("events", *primary, "--tail", "50"))["events"]
        restores = [row for row in events if row.get("event") == "native_guest_save_restored"
                    and row.get("generation") == returned["generation"] and row.get("character_id") == character
                    and row.get("slot") == prior["guest_frame"]["slot"] and row.get("pawn") == restored_guest["pawn"]]
        possible_decay = restored["slow_writes"] + (max(0, persisted - DOSE) if restored["slow_writes"] else 0)
        if (returned["endpoint"]["port"] != port or len(restores) != 1 or any(restored[key] for key in COUNTERS[:2])
                or not 0 < restored["guest_sickness"] <= persisted
                or persisted - restored["guest_sickness"] > possible_decay):
            raise AssertionError({"saved_value": persisted, "native_restore": restored, "restores": restores,
                                  "expected_port": port, "actual_port": returned["endpoint"]["port"]})
        proof["same_port_restart"] = {"port": port, "generation": returned["generation"], "guest": restored_guest,
                                      "first_native": restored, "save_restore": restores[0]}

        last = restored
        proof["negative_doses"] = []
        for _ in range(2):
            cleared = await dose(restored_guest, last, -DOSE)
            proof["negative_doses"].append(cleared)
            last = cleared["proof"]
            if last["guest_sickness"] <= 0:
                break
        if last["guest_sickness"] > 0:
            raise AssertionError({"signed_treatment_did_not_clear_sickness": last})
        final, _ = await wait("The treated guest did not publish a settled detached sickness frame", lambda left, right:
            paired(left, right) and (row := sickness_row(left, restored_guest["pawn"])).get("infect_writes") == last["infect_writes"]
            and row["guest_sickness"] <= 0)
        proof["final_native"] = owner_state(final, native_guest(final, character), exact_identity(restored_guest))
        proof["verified"] = True
    except BaseException as error:
        proof["verified"] = False
        proof["failure"] = str(error)
        server = status(host_run)
        game = server.get("game", {})
        proof["failure_frame"] = {"host_phase": server.get("state"), "guest_phase": status(guest_run).get("state"),
                                  "map": game.get("map"), "sickness": game.get("native_guest_sickness"),
                                  "fixture": game.get("native_guest_sickness_fixture")}
        raise
    finally:
        for process, target in reversed(processes):
            if process.returncode is not None:
                continue
            try:
                await command("action", *target, "stop")
                await asyncio.wait_for(process.wait(), 15)
            except (OSError, RuntimeError, TimeoutError):
                if process.returncode is None:
                    killer = await asyncio.create_subprocess_exec("taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
                    await asyncio.wait_for(killer.wait(), 12)
                    await asyncio.wait_for(process.wait(), 12)
        unchanged = saved_hashes(original) == baseline
        proof["original_saves_verified"] = unchanged
        proof["verified"] = unchanged and proof.get("verified", False)
        (host_run / "live-guest-sickness-verification.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(proof), flush=True)
        if not unchanged:
            raise AssertionError("Original singleplayer Saved files changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="directory containing a prepared native stage")
    parser.add_argument("--vanilla-saved", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "SilverFish" / "Saved")
    options = parser.parse_args()
    if not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(verify(options.work.resolve(), options.vanilla_saved.resolve()))
