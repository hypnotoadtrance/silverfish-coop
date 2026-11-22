"""Exercise stock passive health regeneration in a staged two-player Direct IP session."""

import argparse
import asyncio
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from live_multi_smoke import saved_hashes
from live_observatory_jug_smoke import installed_probe, stock_health
from live_reconnect_grace_smoke import OBSERVATORY, exact_identity, lease, manifest_digest, native_guest, status


REPO = Path(__file__).resolve().parents[1]
REVIVE_HEALTH = 25.0
EXPECTED_CEILING = 250.0
DOWNED_SECONDS = 2.5
MIN_DOWNED_FRAMES = 4
MIN_HEALING_PULSES = 3
MIN_HEALING = 2.0
ACCURACY = 1e-6
BAR_SAMPLE_WINDOW = 1.5
BAR_HEALTH_TOLERANCE = .25


def regen_proof(server: dict, pawn: str) -> dict:
    return server.get("game", {}).get("native_guest_regen", {}).get(pawn, {})


def finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def same_number(first: float, second: float) -> bool:
    return finite(first) and finite(second) and math.isclose(first, second, rel_tol=0, abs_tol=ACCURACY)


async def validate(work: Path, original_saved: Path, *, check_reconnect: bool, check_stock_health: bool = False) -> None:
    if os.name != "nt":
        raise OSError("The live regeneration test requires the staged Windows game")
    name = "guest-regen-" + uuid.uuid4().hex[:8]
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
    evidence = {"original_files": len(initial_hashes), "original_manifest_sha256": manifest_digest(initial_hashes)}

    async def command(*arguments):
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
        if process.returncode != 0 or not output:
            raise RuntimeError(f"{arguments} returned {output.decode(errors='replace')} {error.decode(errors='replace')}")
        reply = json.loads(output)
        if reply.get("ok") is not True:
            raise RuntimeError(f"{arguments} returned {reply}")
        return reply

    async def action(owner, operation, *arguments):
        return await command("action", *owner, operation, *arguments)

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
            await asyncio.sleep(0.15)
        raise RuntimeError(label)

    def paired(server, client):
        return (server.get("state") == "hosting" and client.get("state") == "connected"
                and server.get("link_connected") is True and client.get("link_connected") is True
                and client.get("gameplay_fresh") is True and client.get("character_id") == character
                and client.get("host_session") == server.get("generation")
                and client.get("zone_epoch") == server.get("zone_epoch") and native_guest(server, character)
                and client.get("game", {}).get("pawns", {}).get("local", {}).get("name"))

    def verify_ownership(server, guest):
        game = server["game"]
        current_host = game.get("pawns", {}).get("local", {})
        rows = game.get("native_guest_regen", {})
        if (exact_identity(guest) != exact_identity(initial) or current_host.get("name") != host_pawn
                or not same_number(current_host.get("health"), host_health)
                or set(rows) - {initial["pawn"]} or game.get("native_dead_flags", {}).get("guest") is not False):
            raise AssertionError({"guest": guest, "canonical_host": current_host, "regeneration": rows,
                                  "dead_flags": game.get("native_dead_flags")})

    def standing(server, guest):
        return (guest.get("downed") is False
                and server["game"].get("player_effects", {}).get("guest", {}).get("state") == "standing")

    def verified_pulse(server, guest, row):
        verify_ownership(server, guest)
        numbers = ("total_seconds", "pulse_seconds", "health_before", "health_after", "ceiling", "delay_before",
                   "delay_after", "food_before", "food_after", "stim_before", "stim_after")
        if not (row.get("state") == "applied" and row.get("map") == OBSERVATORY and row.get("pawn") == guest["pawn"]
                and type(row.get("pulses")) is int and row["pulses"] > 0
                and all(finite(row.get(key)) for key in numbers)
                and 0 < row["pulse_seconds"] <= 0.5 and row["total_seconds"] + ACCURACY >= row["pulse_seconds"]
                and same_number(row["ceiling"], EXPECTED_CEILING)
                and 0 < row["health_before"] <= row["health_after"] <= row["ceiling"]
                and same_number(row["health_after"], guest["health"])
                and row["delay_after"] <= row["delay_before"]
                and 0 <= row["food_after"] <= row["food_before"] <= 200
                and 0 <= row["stim_after"] <= row["stim_before"]):
            raise AssertionError({"guest": guest, "regeneration": row})

    async def read_stock_bars(label, minimum_health):
        def menu(state):
            return state.get("game", {}).get("native_inventory_view", {}).get("state")

        await action(secondary, "native-inventory-view-fixture")
        await wait(f"{label}: the stock backpack did not open", lambda _, guest: menu(guest) == "stock", 8)
        samples, last_sequence, display = [], None, None
        end = time.monotonic() + 8
        try:
            while time.monotonic() < end:
                server, client = status(host_run), status(guest_run)
                if paired(server, client):
                    guest = native_guest(server, character)
                    verify_ownership(server, guest)
                    sequence = server["game"]["sequence"]
                    now = time.monotonic()
                    if sequence != last_sequence and standing(server, guest) and finite(guest["health"]):
                        last_sequence = sequence
                        samples.append((now, guest["health"]))
                    # The guest consumes a roughly 0.5 s snapshot while native healing
                    # continues. Compare to a short envelope of actual host readbacks.
                    samples = [(at, hp) for at, hp in samples if now - at <= BAR_SAMPLE_WINDOW]
                    display = await stock_health(guest_run)
                    if (display["pawn"] != client_pawn or display["slot"] != "SFCoopGuest_" + uuid.UUID(character).hex
                            or not same_number(display["maximum"], EXPECTED_CEILING)):
                        raise AssertionError({"wrong_stock_health_view": display, "checkpoint": label})
                    if samples:
                        low, high = min(hp for _, hp in samples), max(hp for _, hp in samples)
                        if max(low, minimum_health) - BAR_HEALTH_TOLERANCE <= display["health"] <= high + BAR_HEALTH_TOLERANCE:
                            return {"stock": display, "authority_health_range": [low, high],
                                    "maximum_sample_age_seconds": BAR_SAMPLE_WINDOW,
                                    "health_tolerance": BAR_HEALTH_TOLERANCE}
                await asyncio.sleep(.12)
            raise AssertionError({"stock_health_outside_host_samples": display, "samples": samples, "checkpoint": label})
        finally:
            if menu(status(guest_run)) == "stock":
                await action(secondary, "native-inventory-view-fixture")
                await wait(f"{label}: the stock backpack did not close", lambda _, guest: menu(guest) == "closed", 8)

    try:
        await start("host", host_run, primary, "--port", "0", "--headless", "--fixtures", "--native-zones",
                    "--native-events", "--native-guest", "--native-guest-regen", "--start-zone", OBSERVATORY)
        await wait("The staged Observatory host did not become ready",
            lambda server, _: server.get("state") == "hosting"
            and server.get("game", {}).get("native_zone", {}).get("state") == "ready")
        invitation = await command("invite", *primary, "--name", "Regeneration guest", "--character", "Regeneration guest")
        character = invitation["character_id"]
        evidence["character_id"] = character
        health_flags = ("--native-guest-inventory-view",) if check_stock_health else ()
        await start("join", guest_run, secondary, "--headless", "--native-zones", "--invite",
                    invitation["invite_file"], *health_flags)

        def ready(server, client):
            guest = native_guest(server, character)
            hook = server.get("game", {}).get("native_death_hook", {})
            fall = server.get("game", {}).get("native_fall", {})
            row = regen_proof(server, guest.get("pawn"))
            return (paired(server, client) and hook.get("state") == "ready" and hook.get("scope") in ("guest", "party")
                    and hook.get("sequence") == 0 and fall.get("state") == "ready" and fall.get("actor_tick") is False
                    and fall.get("pawn") == guest["pawn"] and standing(server, guest) and row.get("state") == "applied")

        server, client = await wait("The exact detached guest never reported stock regeneration with its actor tick disabled",
                                    ready)
        initial = native_guest(server, character)
        host_pawn = server["game"]["pawns"]["local"]["name"]
        host_health = server["game"]["pawns"]["local"]["health"]
        client_pawn = client["game"]["pawns"]["local"]["name"]
        verified_pulse(server, initial, regen_proof(server, initial["pawn"]))
        if not same_number(initial["health"], EXPECTED_CEILING):
            raise AssertionError({"initial_guest": initial, "expected_full_health": EXPECTED_CEILING})
        evidence["initial_guest"] = initial
        evidence["initial_native_regeneration"] = regen_proof(server, initial["pawn"])
        evidence["canonical_host"] = {"pawn": host_pawn, "health": host_health}
        if check_stock_health:
            evidence["stock_health_full"] = await read_stock_bars("initial full guest", EXPECTED_CEILING)

        damage = await action(primary, "native-guest-damage", "--target", "guest")
        hit = damage["native_damage_canary"]
        if (hit.get("slot") != "guest" or not same_number(hit.get("guest_before"), initial["health"])
                or not finite(hit.get("guest_after")) or hit["guest_after"] > 0
                or not same_number(hit.get("host_before"), host_health) or not same_number(hit.get("host_after"), host_health)
                or hit.get("binding_restored") is not False):
            raise AssertionError(hit)
        evidence["stock_lethal_hit"] = hit

        downed_frames = []
        downed_since = 0.0
        last_sequence = -1

        def held_downed(server, client):
            nonlocal downed_since, last_sequence
            guest = native_guest(server, character)
            if not paired(server, client) or server["game"]["sequence"] <= last_sequence:
                return False
            game = server["game"]
            projection = game["player_effects"].get("guest", {})
            if (guest.get("downed") is not True or projection.get("state") != "downed"
                    or projection.get("crouched") is not True or game["native_death_hook"]["sequence"] != hit["sequence"]):
                if downed_frames:
                    raise AssertionError({"guest_left_downed_without_revive": guest, "projection": projection})
                return False
            verify_ownership(server, guest)
            row = regen_proof(server, guest["pawn"])
            if row.get("state") != "paused_projection" or row.get("pawn") != guest["pawn"] or row.get("map") != OBSERVATORY:
                if downed_frames or row.get("state") == "applied":
                    raise AssertionError({"regeneration_while_downed": row, "guest": guest})
                return False
            last_sequence = game["sequence"]
            now = time.monotonic()
            if not downed_frames:
                downed_since = now
            elif not same_number(guest["health"], downed_frames[0]["health"]):
                raise AssertionError({"downed_health_changed": guest, "initial_downed": downed_frames[0]})
            if "pulses" in row:
                raise AssertionError({"downed_regeneration_reported_a_pulse": row})
            downed_frames.append({"sequence": last_sequence, "health": guest["health"], "regeneration": row})
            return len(downed_frames) >= MIN_DOWNED_FRAMES and now - downed_since >= DOWNED_SECONDS

        await wait("Stock lethal damage did not yield a stable, paused downed guest with independent host health", held_downed)
        evidence["downed"] = {"seconds": round(time.monotonic() - downed_since, 3), "frames": downed_frames}

        revive = asyncio.create_task(action(primary, "interact", "--target", character))

        def revived_in_game(server, client):
            if revive.done():
                revive.result()
            guest = native_guest(server, character)
            if not paired(server, client) or not standing(server, guest):
                return False
            verify_ownership(server, guest)
            receipt = server["game"].get("native_guest_revivals", {}).get(guest["pawn"], {})
            return receipt == {"map": OBSERVATORY, "pawn": guest["pawn"], "owner": character,
                               "health": REVIVE_HEALTH, "maximum": EXPECTED_CEILING}

        try:
            revived_state, _ = await wait("The exact standing native pawn did not report the 25 HP revival", revived_in_game, 12)
        finally:
            revival = await revive
        # Older coordinators return party health in legacy 100-point units.
        # The authoritative native 25-HP receipt was already required above.
        if (revival.get("effect") != "revive" or revival.get("target") != character
                or not any(same_number(revival.get("health"), amount) for amount in (10.0, REVIVE_HEALTH))):
            raise AssertionError(revival)
        evidence["revive"] = {key: revival[key] for key in ("effect", "target", "health")}
        evidence["native_revive"] = {"guest": native_guest(revived_state, character),
                                      "applied": revived_state["game"]["native_guest_revivals"][initial["pawn"]],
                                      "regeneration": regen_proof(revived_state, initial["pawn"])}

        healing = []

        def grew_passively(server, client):
            guest = native_guest(server, character)
            if not paired(server, client) or not standing(server, guest):
                return False
            row = regen_proof(server, guest["pawn"])
            verify_ownership(server, guest)
            if row.get("state") != "applied":
                return False
            verified_pulse(server, guest, row)
            if healing and row["pulses"] <= healing[-1]["pulses"]:
                return False
            if healing and (row["health_before"] < healing[-1]["health_after"] - ACCURACY
                            or row["total_seconds"] <= healing[-1]["total_seconds"]):
                raise AssertionError({"previous_heal": healing[-1], "current_heal": row})
            if not healing and not REVIVE_HEALTH - ACCURACY <= row["health_before"] <= REVIVE_HEALTH + 3:
                raise AssertionError({"first_post_revive_health": row, "revive_health": REVIVE_HEALTH})
            if row["health_after"] > row["health_before"] + ACCURACY:
                healing.append(row)
            return len(healing) >= MIN_HEALING_PULSES and guest["health"] >= REVIVE_HEALTH + MIN_HEALING

        server, client = await wait("The revived guest's stock health did not rise on independent passive pulses", grew_passively)
        healed = native_guest(server, character)
        if client["game"]["pawns"]["local"]["name"] != client_pawn:
            raise AssertionError("The owning client pawn changed during revival")
        evidence["natural_healing"] = {"guest": healed, "pulses": healing, "health_gained": healed["health"] - REVIVE_HEALTH}
        if check_stock_health:
            evidence["stock_health_healed"] = await read_stock_bars("revived and healing guest", healed["health"])

        if check_reconnect:
            dropped = await action(secondary, "drop-link")
            if dropped.get("link_connected") is not False:
                raise AssertionError(dropped)
            reserved, _ = await wait("The host did not keep the same active character's lease during the outage",
                lambda server, client: native_guest(server, character) and lease(server, character).get("state") == "reserved"
                and client.get("reconnect_suspended") is True and server.get("link_connected") is False, 12)
            retained = native_guest(reserved, character)
            verify_ownership(reserved, retained)
            if not standing(reserved, retained) or retained["health"] < healed["health"] - ACCURACY:
                raise AssertionError({"healed": healed, "during_outage": retained})
            await asyncio.sleep(3)
            resumed = await action(secondary, "reconnect")
            if resumed.get("resumed") is not True or resumed.get("link_connected") is not True:
                raise AssertionError(resumed)
            server, client = await wait("The revived guest did not reclaim its original native pawn", paired)
            returned = native_guest(server, character)
            verify_ownership(server, returned)
            if (not standing(server, returned) or returned["health"] < retained["health"] - ACCURACY
                    or client["game"]["pawns"]["local"]["name"] != client_pawn):
                raise AssertionError({"retained": retained, "returned": returned})
            evidence["reconnect"] = {"held": retained, "returned": returned,
                                     "reserved_lease": lease(reserved, character), "resumed": True}
            if check_stock_health:
                evidence["stock_health_reconnected"] = await read_stock_bars("resumed guest", retained["health"])
        evidence["verified"] = True
    except BaseException as error:
        evidence["verified"] = False
        evidence["failure"] = str(error)
        last_host = status(host_run)
        last_game = last_host.get("game", {})
        evidence["last_frame"] = {"host_state": last_host.get("state"), "guest_state": status(guest_run).get("state"),
                                  "pawns": last_game.get("pawns"), "native_fall": last_game.get("native_fall"),
                                  "native_death_hook": last_game.get("native_death_hook"),
                                  "guest_save": last_game.get("native_guest_save"),
                                  "guest_regeneration": last_game.get("native_guest_regen")}
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
                        "taskkill.exe", "/PID", str(process.pid), "/T", "/F", stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await asyncio.wait_for(killer.wait(), 12)
                    await asyncio.wait_for(process.wait(), 12)
        unchanged = saved_hashes(original_saved) == initial_hashes
        evidence["original_saves_verified"] = unchanged
        evidence["verified"] = unchanged and evidence.get("verified", False)
        (host_run / "live-guest-regen-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if not unchanged:
            raise AssertionError("Original singleplayer files changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path, help="directory containing a prepared stage")
    parser.add_argument("--vanilla-saved", type=Path,
                        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "SilverFish" / "Saved")
    parser.add_argument("--reconnect", action="store_true", help="verify the revived native guest across a brief link outage")
    parser.add_argument("--stock-health", action="store_true", help="verify both stock health bars against the native guest")
    options = parser.parse_args()
    if not options.vanilla_saved.is_dir():
        parser.error("--vanilla-saved must be an existing original Saved directory")
    work = options.work.resolve()
    with installed_probe(work) if options.stock_health else nullcontext():
        asyncio.run(validate(work, options.vanilla_saved.resolve(), check_reconnect=options.reconnect,
                             check_stock_health=options.stock_health))
