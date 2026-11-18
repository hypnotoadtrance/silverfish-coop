"""Optional Windows integration test. Launches two headless staged games on loopback."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid

options = argparse.ArgumentParser(description=__doc__)
options.add_argument("--work", type=Path, required=True, help="root of a prepared stage workspace")
options.add_argument("--vanilla-saved", type=Path, help="optional original Saved directory to hash before and after")
args = options.parse_args()
work = args.work.resolve()
profile = "validation-" + uuid.uuid4().hex[:8]
campaign = profile
run = work / "campaigns" / campaign / "run"
client_run = work / "clients" / profile / "run"
base = [sys.executable, "-u", "-m", "tools.silverfish", "--work", str(work)]
records = []
processes = []
deadline = 40

def note(label, summary):
    message = {"check": label, **summary}
    records.append(message)
    print(json.dumps(message), flush=True)

def snapshot_hashes(directory):
    hashes = {}
    for file in directory.rglob("*"):
        if file.is_file():
            with file.open("rb") as stream:
                hashes[str(file.relative_to(directory))] = hashlib.file_digest(stream, "sha256").hexdigest()
    return hashes


class InterruptibleRelay(asyncio.DatagramProtocol):
    """Model loss of the network path without changing any machine network settings."""

    def __init__(self, server):
        self.server = server
        self.client = None
        self.offline = False

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, source):
        if self.offline:
            return
        if source == self.server:
            if self.client:
                self.transport.sendto(data, self.client)
        else:
            self.client = source
            self.transport.sendto(data, self.server)

async def command(*args, check=True):
    process = await asyncio.create_subprocess_exec(*base, *args, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.PIPE)
    output, error = await asyncio.wait_for(process.communicate(), timeout=45)
    if check and process.returncode:
        raise RuntimeError(f"{args}: {output.decode(errors='replace')} {error.decode(errors='replace')}")
    return json.loads(output)

async def start(*args):
    path = run / (args[0] + "-" + str(len(processes)) + ".txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        process = await asyncio.create_subprocess_exec(*base, *args, stdout=output, stderr=output)
    processes.append((process, path))
    return process

async def wait(directory, condition):
    end = time.monotonic() + deadline
    last = None
    while time.monotonic() < end:
        try:
            last = json.loads((directory / "status.json").read_text(encoding="utf-8"))
            if condition(last):
                return last
            if last.get("state") == "failed":
                raise RuntimeError(f"session failed: {last}")
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            pass
        await asyncio.sleep(0.2)
    raise TimeoutError(f"Status did not reach the expected state: {last}")

async def stop(process, flag, value):
    if process.returncode is None:
        await command("action", flag, value, "stop", check=False)
        await asyncio.wait_for(process.wait(), timeout=12)
    assert process.returncode == 0

async def smoke():
    original = args.vanilla_saved
    baseline = snapshot_hashes(original) if original else None
    relay = None
    try:
        host = await start("host", "--campaign", campaign, "--port", "0", "--headless")
        host_status = await wait(run, lambda state: state.get("state") == "hosting")
        port = host_status["endpoint"]["port"]
        note("host_listening", {"udp_port": port, "map": host_status["game"]["map"],
                                "campaign_id": host_status["campaign"]["campaign_id"]})
        issued = await command("invite", "--campaign", campaign, "--name", "Validation guest",
                               "--character", "Validation character")
        loop = asyncio.get_running_loop()
        relay, interruption = await loop.create_datagram_endpoint(
            lambda: InterruptibleRelay(("127.0.0.1", port)), local_addr=("127.0.0.1", 0),
        )
        invitation = Path(issued["invite_file"])
        invitation_data = json.loads(invitation.read_text(encoding="utf-8"))
        invitation_data["port"] = relay.get_extra_info("sockname")[1]
        fixture = invitation.with_name("loss-test.invite.json")
        fixture.write_text(json.dumps(invitation_data), encoding="utf-8")
        client = await start("join", "--profile", profile, "--invite", str(fixture), "--headless")
        guest = await wait(client_run, lambda state: state.get("state") == "connected"
                           and len(state.get("game", {}).get("pawns", {})) == 2)
        host_status = await wait(run, lambda state: state.get("game", {}).get("remote_clients") == 1
                                 and len(state["game"]["pawns"]) == 2)
        assert guest["character_id"] == issued["character_id"]
        assert len(guest["game"]["pawns"]) == 2
        original_guest = host_status["game"]["pawns"]["guest"]["name"]
        assert original_guest != host_status["game"]["pawns"]["local"]["name"]
        note("two_players", {"host_and_client_pawns": len(host_status["game"]["pawns"]),
                             "client_can_see": len(guest["game"]["pawns"]),
                             "host_pawn": host_status["game"]["pawns"]["local"],
                             "guest_pawn": host_status["game"]["pawns"]["guest"],
                             "character_id": issued["character_id"]})
        dropped = await command("action", "--profile", profile, "drop-link")
        assert dropped["link_connected"] is False
        reserved = await wait(run, lambda state: not state.get("link_connected")
                              and bool(state.get("campaign", {}).get("leases"))
                              and state["campaign"]["leases"][0]["state"] == "reserved")
        assert reserved["game"]["pawns"]["guest"]["name"] == original_guest
        note("link_dropped", {"active_native_pawn_retained": True,
                              "character_reserved": reserved["campaign"]["leases"][0]["character_id"]})
        await asyncio.sleep(2)
        reconnected = await command("action", "--profile", profile, "reconnect")
        assert reconnected["link_connected"] and reconnected["resumed"]
        after = await wait(run, lambda state: state.get("link_connected") and state.get("game", {}).get("remote_clients") == 1)
        assert after["game"]["pawns"]["guest"]["name"] == original_guest
        note("same_pawn_reconnected", {"resumed": reconnected["resumed"], "pawn": original_guest})
        interruption.offline = True
        try:
            await wait(run, lambda state: not state.get("link_connected")
                       and state["campaign"]["leases"][0]["state"] == "reserved")
        finally:
            interruption.offline = False
        automatic = await wait(run, lambda state: state.get("link_connected") and
                               state.get("game", {}).get("remote_clients") == 1)
        assert automatic["game"]["pawns"]["guest"]["name"] == original_guest
        note("packet_loss_recovers_automatically", {"pawn": original_guest})
        saved = await command("action", "--campaign", campaign, "checkpoint")
        assert saved["revision"] > 0
        snapshot = json.loads((run.parent / "save" / "campaign.json").read_text(encoding="utf-8"))
        saved_character = snapshot["characters"][issued["character_id"]]
        assert len(saved_character["state"]["location"]) == 3
        assert saved_character["state"]["map"] == host_status["game"]["map"]
        note("isolated_checkpoint", {"revision": saved["revision"],
                                     "character_location": saved_character["state"]["location"]})
        await stop(host, "--campaign", campaign)
        host_restart = await start("host", "--campaign", campaign, "--port", str(port), "--headless")
        restored = await wait(run, lambda state: state.get("state") == "hosting" and
                              state.get("generation") != host_status["generation"])
        assert restored["campaign"]["campaign_id"] == host_status["campaign"]["campaign_id"]
        assert restored["campaign"]["characters"][0]["character_id"] == issued["character_id"]
        waiting = await wait(run, lambda state: state.get("link_connected")
                             and state.get("game", {}).get("remote_clients") == 1
                             and len(state.get("game", {}).get("pawns", {})) == 2)
        await wait(client_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("state") == "connected"
                   and state.get("host_session") == restored["generation"])
        restored_location = waiting["game"]["pawns"]["guest"]["location"]
        assert all(abs(new - old) < 2 for new, old in zip(
            restored_location, saved_character["state"]["location"], strict=True,
        ))
        note("restart_and_original_invite", {"same_campaign_id": restored["campaign"]["campaign_id"],
                                             "same_character_id": issued["character_id"],
                                             "restored_location": restored_location})
        await stop(client, "--profile", profile)
        await stop(host_restart, "--campaign", campaign)
        if original:
            assert snapshot_hashes(original) == baseline
            note("singleplayer_hashes_match", {"files": len(baseline)})
    finally:
        if relay:
            relay.close()
        for kind, name in (("--profile", profile), ("--campaign", campaign)):
            try:
                await command("action", kind, name, "stop", check=False)
            except (OSError, RuntimeError, TimeoutError):
                pass
        for process, path in reversed(processes):
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            text = path.read_text(encoding="utf-8", errors="replace")
            if text:
                print(path.name, text.strip(), flush=True)
        (run / "live-verification.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(run, flush=True)

asyncio.run(smoke())
