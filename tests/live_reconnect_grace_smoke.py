"""Exercise a staged Direct IP outage, the active party gate, and optional lease expiry."""

import argparse
import asyncio
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes


REPO = Path(__file__).resolve().parents[1]
OBSERVATORY = "/Game/Classes/Maps/Maps_OverWorld/Observitory_P"
CROSSROADS = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
GRACE_SECONDS = 90
TRAVEL_RADIUS = 1500
ARRIVAL_RADIUS = 250
SETTLE_SECONDS = 2
SETTLE_RADIUS = 25
OUTAGE_FRAME_GAP_SECONDS = 3


def status(run: Path) -> dict:
    try:
        return json.loads((run / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lease(server: dict, character: str) -> dict:
    return next((row for row in server.get("campaign", {}).get("leases", [])
                 if row.get("character_id") == character), {})


def expiry_receipt(run: Path, character: str, slot: str) -> dict:
    try:
        lines = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if (event.get("event") == "native_guest_expiry_checkpointed" and event.get("character_id") == character
                and event.get("slot") == slot):
            return event
    return {}


def native_guest(server: dict, character: str) -> dict:
    game = server.get("game", {})
    participant = server.get("gameplay", {}).get("players", {}).get("guest", {})
    pawn = game.get("pawns", {}).get("guest", {})
    roster = game.get("native_roster", {})
    scope = game.get("native_guest_save_members", {}).get(pawn.get("name"), {})
    if not scope:
        scope = game.get("native_guest_save", {})
    connection = next((row for row in roster.get("connections", [])
                       if row.get("pawn") == pawn.get("name") and row.get("controller") == pawn.get("controller")), {})
    if (game.get("map") != OBSERVATORY or game.get("remote_clients") != 1 or participant.get("character_id") != character
            or scope.get("state") != "bound" or not connection.get("id") or not roster.get("driver")
            or not pawn.get("name") or not pawn.get("controller") or not isinstance(pawn.get("health"), (int, float))):
        return {}
    return {"pawn": pawn["name"], "controller": pawn["controller"], "connection_id": connection["id"],
            "driver": roster["driver"], "location": pawn["location"], "health": pawn["health"],
            "downed": participant.get("downed"), "zone_epoch": server["zone_epoch"]}


def exact_identity(pawn: dict) -> tuple:
    return tuple(pawn.get(key) for key in ("pawn", "controller", "connection_id", "driver", "zone_epoch"))


def manifest_digest(hashes: dict[str, str]) -> str:
    return sha256(json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def validate(work: Path, original_saved: Path, *, outage: float, check_expiry: bool, single_slot: bool) -> None:
    if os.name != "nt":
        raise OSError("The live reconnect test requires the staged Windows game")
    name = "reconnect-grace-" + uuid.uuid4().hex[:8]
    host_run = work / "campaigns" / name / "run"
    guest_run = work / "clients" / name / "run"
    host_run.mkdir(parents=True)
    guest_run.mkdir(parents=True)
    print(host_run, flush=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    primary, secondary = ("--campaign", name), ("--profile", name)
    initial_hashes = saved_hashes(original_saved)
    if not initial_hashes:
        raise ValueError("The original Saved directory must contain files")
    launched = []
    evidence = {"grace_seconds": GRACE_SECONDS, "requested_outage_seconds": outage,
                "mode": "single-slot" if single_slot else "party-members",
                "original_files": len(initial_hashes), "original_manifest_sha256": manifest_digest(initial_hashes)}

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(
            *cli, *arguments, cwd=REPO, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            output, error = await asyncio.wait_for(process.communicate(), 40)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if not output or (process.returncode == 0) != expected:
            raise RuntimeError(f"{arguments}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is not expected:
            raise RuntimeError(f"{arguments}: unexpected response {reply}")
        return reply

    async def action(owner, operation, *arguments, expected=True):
        return await command("action", *owner, operation, *arguments, expected=expected)

    async def start(kind, run, target, *arguments):
        with (run / (kind + ".txt")).open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                *cli, kind, *target, *arguments, cwd=REPO, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        launched.append((process, target))

    async def wait(label, predicate, seconds=40):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            server, client = status(host_run), status(guest_run)
            if predicate(server, client):
                return server, client
            if (any(process.returncode is not None for process, _ in launched)
                    or "failed" in (server.get("state"), client.get("state"))):
                break
            await asyncio.sleep(0.25)
        raise RuntimeError(label)

    def paired(server, client):
        return (server.get("state") == "hosting" and client.get("state") == "connected"
                and client.get("link_connected") is True and client.get("gameplay_fresh") is True
                and client.get("character_id") == character and client.get("host_session") == server.get("generation")
                and client.get("zone_epoch") == server.get("zone_epoch") and native_guest(server, character)
                and client.get("game", {}).get("pawns", {}).get("local", {}).get("name"))

    async def pause_link():
        dropped = await action(secondary, "drop-link")
        started = time.monotonic()
        if dropped.get("link_connected") is not False:
            raise AssertionError(dropped)
        server, _ = await wait("The host did not reserve the disconnected guest's active lease",
            lambda server, client: server.get("link_connected") is False and lease(server, character).get("state") == "reserved"
            and client.get("link_connected") is False and client.get("reconnect_suspended") is True, 12)
        remaining = lease(server, character)["grace_remaining"]
        if not 0 < remaining <= GRACE_SECONDS:
            raise AssertionError(lease(server, character))
        return started, server

    async def settled_pair():
        anchor = None
        anchored_at = 0.0
        last_sequence = -1
        frame_count = 0

        def separated(server, client):
            nonlocal anchor, anchored_at, last_sequence, frame_count
            if not paired(server, client):
                anchor = None
                return False
            game = server["game"]
            if game["sequence"] <= last_sequence:
                return False
            last_sequence = game["sequence"]
            pawn = native_guest(server, character)
            lead = game["pawns"]["local"]["location"]
            if pawn["health"] <= 0 or math.dist(lead[:2], pawn["location"][:2]) <= TRAVEL_RADIUS:
                anchor = None
                return False
            points = (lead, pawn["location"])
            now = time.monotonic()
            if anchor is None or any(math.dist(old, current) > SETTLE_RADIUS for old, current in zip(anchor, points)):
                anchor, anchored_at, frame_count = points, now, 1
                return False
            frame_count += 1
            return frame_count >= 3 and now - anchored_at >= SETTLE_SECONDS

        return await wait("Both pawns did not settle at least 15 metres apart horizontally", separated)

    async def blocked_travel():
        denied = await action(primary, "travel", "--target", CROSSROADS, expected=False)
        if denied.get("error") != "travel_blocked" or denied.get("blockers") != [{"id": character, "reason": "too_far"}]:
            raise AssertionError(denied)
        return denied["blockers"][0]

    async def inventory_item(guid):
        inventory = (await action(primary, "native-inventory-list", "--target", "guest"))["native_inventory"]
        items = [item for item in inventory["items"] if item.get("guid") == guid]
        if len(items) != 1:
            raise AssertionError("The exact staged item is missing from the guest backpack")
        return {key: items[0][key] for key in ("guid", "class_path", "source", "quantity")}

    try:
        await start("host", host_run, primary, "--port", "0", "--headless", "--fixtures",
                    "--native-zones", "--native-events", "--native-guest",
                    "--max-guests", "1" if single_slot else "2", "--start-zone", OBSERVATORY)
        await wait("The staged Observatory host did not become ready",
            lambda server, _: server.get("state") == "hosting"
            and server.get("game", {}).get("native_zone", {}).get("state") == "ready")
        invited = await command("invite", *primary, "--name", "Reconnect grace guest", "--character", "Reserved guest")
        character = invited["character_id"]
        await start("join", guest_run, secondary, "--headless", "--native-zones", "--invite", invited["invite_file"])
        await wait("The guest did not bind its exact native pawn and detached save", paired)
        if check_expiry:
            marker = uuid.uuid4().hex.upper()
            await action(primary, "native-guest-weapon-seed", "--target", "guest", "--item-guid", marker)
            evidence["placed_inventory"] = await inventory_item(marker)

        # Leave the guest at the normal spawn and position only the listen host at the Observatory exit.
        await action(primary, "native-exit-position", "--target", "host", "--exit-position", "near")
        server, client = await settled_pair()
        initial = native_guest(server, character)
        client_pawn = client["game"]["pawns"]["local"]["name"]
        evidence["before_outage"] = initial
        await action(primary, "checkpoint")
        started, first_reserved = await pause_link()
        paused = native_guest(first_reserved, character)
        if (not paused or exact_identity(paused) != exact_identity(initial)
                or math.dist(paused["location"], initial["location"]) > SETTLE_RADIUS):
            raise AssertionError({"settled_before_outage": initial, "first_reserved_frame": paused})
        evidence["first_reserved_frame"] = paused
        evidence["initial_blocker"] = await blocked_travel()
        seq = first_reserved["game"]["sequence"]

        def outage_proof(server, client):
            return {
                "pawn": native_guest(server, character), "lease": lease(server, character),
                "host": {key: server.get(key) for key in ("state", "active", "link_connected", "updated_unix")},
                "peer": {key: client.get(key) for key in ("state", "active", "link_connected", "updated_unix",
                                                         "reconnect_suspended")},
            }

        def holds(proof):
            pawn, hold, server, client = (proof[key] for key in ("pawn", "lease", "host", "peer"))
            return (pawn and exact_identity(pawn) == exact_identity(initial)
                    and math.dist(pawn["location"], paused["location"]) <= 100 and hold.get("state") == "reserved"
                    and server["active"] is True and server["link_connected"] is False
                    and client["link_connected"] is False and client["reconnect_suspended"] is True)

        last_consistent_at, last_frame = time.monotonic(), None
        last_good, last_bad = None, None
        mismatch_count = 0
        while time.monotonic() - started < outage:
            current, peer = status(host_run), status(guest_run)
            now = time.monotonic()
            frame = (current.get("updated_unix"), peer.get("updated_unix"))
            fresh = all(type(stamp) in (float, int) for stamp in frame) and (
                last_frame is None or all(stamp != prior for stamp, prior in zip(frame, last_frame)))
            if fresh:
                last_frame = frame
                proof = outage_proof(current, peer)
                if holds(proof):
                    last_consistent_at, last_good = now, proof
                    mismatch_count = 0
                else:
                    last_bad = proof
                    mismatch_count += 1
            gap = now - last_consistent_at
            if gap >= OUTAGE_FRAME_GAP_SECONDS or (mismatch_count >= 3 and gap >= 1):
                evidence["last_good_outage_frame"] = last_good
                evidence["last_bad_outage_frame"] = last_bad
                evidence["missed_outage_seconds"] = round(gap, 3)
                evidence["successive_mismatches"] = mismatch_count
                raise AssertionError("The exact active guest could not be verified across successive outage frames")
            await asyncio.sleep(0.25)
        evidence["late_blocker"] = await blocked_travel()
        late, _ = await wait("The final outage frame did not contain the in-grace pawn",
            lambda state, peer: state.get("game", {}).get("sequence", 0) > seq and holds(outage_proof(state, peer)), 5)
        retained = native_guest(late, character)
        evidence["outage_seconds"] = round(time.monotonic() - started, 3)
        evidence["in_grace"] = retained
        evidence["in_grace_remaining"] = lease(late, character)["grace_remaining"]

        resumed = await action(secondary, "reconnect")
        if resumed.get("resumed") is not True or resumed.get("link_connected") is not True:
            raise AssertionError(resumed)
        host_after, client_after = await wait("The guest did not reconnect to its original native pawn", paired)
        after = native_guest(host_after, character)
        if (exact_identity(after) != exact_identity(initial) or after["health"] != retained["health"]
                or after["downed"] != retained["downed"] or math.dist(after["location"], retained["location"]) > 100
                or client_after["game"]["pawns"]["local"]["name"] != client_pawn):
            raise AssertionError({"last_disconnected": retained, "after": after})
        evidence["resumed"] = after
        evidence["post_resume_blocker"] = await blocked_travel()

        if check_expiry:
            second_outage, reserved = await pause_link()
            expired, _ = await wait("The reservation and old native pawn did not clear after expiry",
                lambda server, _: server.get("state") == "hosting" and not lease(server, character)
                and server.get("game", {}).get("remote_clients") == 0
                and "guest" not in server.get("gameplay", {}).get("players", {}), 120)
            if time.monotonic() - second_outage < GRACE_SECONDS - 2:
                raise AssertionError("The host retired the reservation earlier than its 90-second grace")
            evidence["expiry_seconds"] = round(time.monotonic() - second_outage, 3)
            clear = await action(primary, "travel", "--target", CROSSROADS, expected=False)
            if clear.get("error") != "authored_exit_required" or clear.get("blockers"):
                raise AssertionError(clear)
            durable = next(row["state"] for row in expired["campaign"]["characters"] if row["character_id"] == character)
            frame = durable.get("native_guest_save", {})
            if frame.get("character_id") != character or frame.get("map") != OBSERVATORY or "health" not in durable:
                raise AssertionError("The expired character did not retain an exact detached native save generation")
            receipt = expiry_receipt(host_run, character, frame["slot"])
            if (type(receipt.get("revision")) is not int
                    or not reserved["campaign"]["revision"] < receipt["revision"] <= expired["campaign"]["revision"]):
                raise AssertionError({"durable_slot": frame["slot"], "expiry_checkpoint": receipt})
            evidence["expiry_checkpoint"] = receipt
            resumed = await action(secondary, "reconnect")
            if resumed.get("resumed") is not False or resumed.get("link_connected") is not True:
                raise AssertionError(resumed)
            current, _ = await wait("The expired character did not return with a replacement native pawn", paired)
            returned = native_guest(current, character)
            leads = current["game"]["pawns"]["local"]["location"]
            if (returned["pawn"] == initial["pawn"] or returned["connection_id"] == initial["connection_id"]
                    or returned["zone_epoch"] != initial["zone_epoch"] or returned["health"] != durable["health"]
                    or returned["downed"] != retained["downed"] or math.dist(leads, returned["location"]) > ARRIVAL_RADIUS):
                raise AssertionError({"old": initial, "replacement": returned, "host_location": leads})
            if await inventory_item(marker) != evidence["placed_inventory"]:
                raise AssertionError("The replacement did not restore the exact stock inventory item")
            evidence["expired_replacement"] = {"pawn": returned, "durable_slot": frame["slot"], "inventory_restored": True}
        evidence["verified"] = True
    except BaseException as error:
        evidence["verified"] = False
        evidence["failure"] = str(error)
        evidence["final_states"] = {"host": status(host_run).get("state"), "guest": status(guest_run).get("state")}
        raise
    finally:
        for process, target in reversed(launched):
            if process.returncode is not None:
                continue
            try:
                await action(target, "stop")
                await asyncio.wait_for(process.wait(), 12)
            except (OSError, RuntimeError, TimeoutError):
                if process.returncode is None:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill.exe", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await asyncio.wait_for(killer.wait(), 12)
                    await asyncio.wait_for(process.wait(), 12)
        unchanged = saved_hashes(original_saved) == initial_hashes
        evidence["original_saves_verified"] = unchanged
        evidence["verified"] = unchanged and evidence.get("verified", False)
        (host_run / "live-reconnect-grace-verification.json").write_text(
            json.dumps(evidence, indent=2) + "\n", encoding="utf-8",
        )
        print(json.dumps(evidence), flush=True)
        if not unchanged:
            raise AssertionError("Original singleplayer files changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="directory containing a prepared stage")
    parser.add_argument("--vanilla-saved", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "SilverFish" / "Saved")
    parser.add_argument("--outage", type=float, default=75, help="seconds to remain suspended within the 90-second grace")
    parser.add_argument("--expiry", action="store_true", help="verify the final expiry checkpoint and replacement stock state")
    parser.add_argument("--single-slot", action="store_true", help="test the legacy one-slot host instead of PartyMembers")
    options = parser.parse_args()
    if not 5 <= options.outage <= 80:
        parser.error("--outage must be between 5 and 80 seconds")
    if not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing original Saved directory")
    asyncio.run(validate(options.work.resolve(), options.vanilla_saved.resolve(), outage=options.outage,
                         check_expiry=options.expiry, single_slot=options.single_slot))
