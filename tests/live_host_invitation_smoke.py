"""Verify an in-game Journal invitation through a real headless Direct IP join."""

import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from live_title_host_settings_smoke import until
from tools.silverfish import runtime, service
from tools.silverfish.control import status, submit
from tools.silverfish.travel import OBSERVATORY


FIXTURE = "native-host-invite-fixture"
REPORT = "live-host-invitation-verification.json"
TOKEN = re.compile(r"[0-9a-f]{32}")


async def verify(work: Path, original: Path) -> None:
    if not runtime.staged_script(work, "native_host_invite.lua", FIXTURE):
        raise ValueError("Use the matched native stage with the in-game invitation form")
    before = saved_hashes(original)
    if not before:
        raise ValueError("Supply the original single-player Saved directory")
    name = "journal-invitation-" + uuid.uuid4().hex[:8]
    case = work / "title-probes" / name
    case.mkdir(parents=True)
    print(case, flush=True)
    campaign, guest = work / "campaigns" / name, work / "clients" / name
    host_run, guest_run = campaign / "run", guest / "run"
    port = runtime.spare_udp_port()
    participant, starter, address = "Journal guest", "Journal scout", "127.0.0.1"
    proof = {"verified": False, "build": runtime.staged_build(work)}
    launch = service.launch
    jobs, host_bridges = [], []

    def track(workspace, profile, mailbox, *args, **kwargs):
        process = launch(workspace, profile, mailbox, *args, **kwargs)
        if profile == campaign / "game-user":
            host_bridges.append(runtime.Bridge(mailbox, process))
        return process

    with patch.object(service, "launch", side_effect=track):
        try:
            host_job = asyncio.create_task(service.host_game(work, campaign, address, port, True, fixtures=True,
                                                            start_preset="wanderer", **service.MENU_HOST_OPTIONS))
            jobs.append(host_job)
            await until(lambda: status(host_run), lambda current: current.get("state") == "hosting"
                        and current.get("game", {}).get("map") == OBSERVATORY)
            bridge, = host_bridges
            request = bridge.directory / "host-invite-request.tsv"
            response = bridge.directory / "host-invite-response.tsv"
            offer = bridge.directory / "host-invite-offer.tsv"
            assert not offer.exists() and not request.exists()

            async def act(step, *args):
                row = await bridge.command(FIXTURE, step, *args, timeout=10)
                expected = {"open": "journal_open", "close": "journal_closed"}.get(step, step + "_accepted")
                assert row["ack"]["message"] == "native_host_invite_" + expected, row["ack"]

            async with asyncio.timeout(25):
                while True:
                    try:
                        await act("open")
                        break
                    except RuntimeError as error:
                        if str(error) != "native_host_invite_controller_loading":
                            raise
                        await asyncio.sleep(.15)
            await until(lambda: status(host_run), lambda current:
                        current.get("game", {}).get("native_host_invite_journal") == "journal")
            await until(lambda: offer.read_bytes(), bool)
            await act("begin")
            await act("participant", participant)
            await act("starter", starter)
            await act("address", address)
            assert not tuple((host_run / "invites").glob("*.invite.json"))
            await act("submit")

            reply = await until(lambda: response.read_text(encoding="utf-8").split("\t"),
                                lambda row: len(row) == 6)
            schema, generation, epoch, op_id, outcome, code = (field.strip() for field in reply)
            assert schema == "1" and outcome == "ok" and TOKEN.fullmatch(op_id), (outcome, code)
            assert (generation, epoch) == (status(host_run)["generation"], status(host_run)["zone_epoch"])
            await until(lambda: status(host_run), lambda current:
                        current.get("game", {}).get("native_host_invite_journal") == "exported")
            published = bridge.directory / f"host-invite-{op_id}.invite.json"
            archived, = (host_run / "invites").glob("*.invite.json")
            assert published.read_bytes() == archived.read_bytes()
            exported = runtime.read_json(published)
            assert (exported["host"], exported["port"]) == (address, port)
            character = exported["character_id"]
            catalog = await service.character_catalog(work, published, None, None)
            assert [(row["character_id"], row["name"]) for row in catalog["characters"]] == [(character, starter)]
            host_events = host_run / "events.jsonl"

            # The same real form operation may be redelivered while the Journal stays open.
            runtime.write_text(request, "\t".join(("1", generation, epoch, op_id, participant, starter, address)) + "\n")
            await until(request.exists, lambda pending: pending is False)
            assert tuple((host_run / "invites").glob("*.invite.json")) == (archived,)
            assert sha256(published.read_bytes()).digest() == sha256(archived.read_bytes()).digest()
            events = [json.loads(line) for line in host_events.read_text(encoding="utf-8").splitlines() if line]
            assert sum(row["event"] == "invite_created" for row in events) == 1

            guest_job = asyncio.create_task(service.join_game(work, guest, archived, True))
            jobs.append(guest_job)
            await until(lambda: status(guest_run), lambda current: current.get("state") == "connected"
                        and current.get("character_id") == character and current.get("gameplay_fresh") is True)
            paired = await until(lambda: status(host_run), lambda current:
                                 current.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            assert paired["game"].get("native_host_invite_journal") == "exported"
            await act("close")
            await until(published.exists, lambda present: present is False)
            assert archived.is_file()
            for file in (host_run / "status.json", host_events, bridge.directory / "status.tsv"):
                report = file.read_text(encoding="utf-8")
                assert exported["credential"] not in report and "BEGIN CERTIFICATE" not in report
            checkpoint = await submit(host_run, "checkpoint")
            assert checkpoint["ok"] is True and checkpoint["revision"] > 0
            for run, job in ((guest_run, guest_job), (host_run, host_job)):
                assert (await submit(run, "stop"))["ok"]
                await asyncio.wait_for(asyncio.shield(job), 25)
            proof.update(verified=True, public_port=port, character_id=character, duplicate_issuance_refused=True,
                         remained_active_through_guest_admission=True, private_export_cleared=True,
                         revision=checkpoint["revision"])
        except (OSError, ValueError, RuntimeError, AssertionError, TimeoutError) as error:
            proof["failure"] = str(error)
            raise
        finally:
            for job in reversed(jobs):
                if not job.done():
                    job.cancel()
                await asyncio.gather(job, return_exceptions=True)
            proof["original_saves_verified"] = saved_hashes(original) == before
            proof["verified"] = proof["verified"] and proof["original_saves_verified"]
            runtime.write_json(case / REPORT, proof)
            assert proof["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--vanilla-saved", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
