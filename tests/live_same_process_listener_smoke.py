"""Verify authenticated Direct IP joins and two listen sessions in the same game processes."""

import argparse
import asyncio
from contextlib import AsyncExitStack, ExitStack
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_multi_smoke import saved_hashes
from tools.silverfish import link, runtime, udp_ownership
from tools.silverfish.win_job import GameProcess


MOD = "TitleListenProof"
PUBLIC_PORT = 7777


def install_fixture(game: Path, addon: Path) -> None:
    mods = game.parent / "ue4ss/Mods"
    mods.mkdir(exist_ok=True)
    if mods.resolve(strict=True) != mods or any(path.name not in {MOD, "mods.txt"} for path in mods.iterdir()):
        raise ValueError("Use the dedicated stock replica for the listen fixture")
    scripts = Path(__file__).parent / "fixtures"
    contents = {
        mods / MOD / "Scripts/main.lua": (scripts / "same_process_listener.lua").read_bytes(),
        mods / MOD / "dlls/main.dll": addon.read_bytes(),
        mods / "mods.txt": f"{MOD} : 1\n".encode("ascii"),
    }
    for path, source in contents.items():
        if path.resolve() != path or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("The fixture paths must be ordinary files")
        if path.name == "mods.txt" and path.exists() and path.read_bytes().replace(b"\r\n", b"\n") != source:
            raise ValueError("The stock replica has another mods list")
    for path, source in contents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)


def choose_ports() -> tuple[int, int]:
    with ExitStack() as stack:
        listeners = [stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) for _ in range(2)]
        for listener in listeners:
            listener.bind(("127.0.0.1", 0))
        return tuple(listener.getsockname()[1] for listener in listeners)


async def observe(case: Path, process: GameProcess, expected: str) -> list[str]:
    async with asyncio.timeout(20):
        while process.poll() is None:
            try:
                rows = (case / "progress.tsv").read_text(encoding="ascii").splitlines()
            except (FileNotFoundError, PermissionError):
                rows = []
            if any(row.startswith("arm_refused") for row in rows):
                raise RuntimeError("The native listener refused the stock title's request")
            if any(row == expected or row.startswith(expected + "\t") for row in rows):
                return rows
            await asyncio.sleep(.05)
    raise RuntimeError(f"The stock game exited before reporting {expected!r} ({process.returncode})")


def launch(game: Path, case: Path, *, ports: tuple[int, int], guest: bool) -> GameProcess:
    profile = case / "game-user"
    profile.mkdir(parents=True)
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("SILVERFISH_COOP_", "UE4SS_", "SF_LISTEN_"))}
    env.update(SILVERFISH_COOP_HEADLESS="1", SF_LISTEN_NATIVE_PROBE="1", SF_LISTEN_PROOF_DIRECTORY=str(case))
    env.update(SF_LISTEN_PORT_1=str(ports[0]), SF_LISTEN_PORT_2=str(ports[1]), SF_LISTEN_GUEST=str(int(guest)))
    arguments = subprocess.list2cmdline([str(game)])
    arguments += (f' -userdir="{profile}" -unattended -NoSplash -windowed'
                  ' -nullrhi -RenderOffScreen -nosound -ResX=640 -ResY=480'
                  ' -ini:Input:[/Script/Engine.InputSettings]:DefaultViewportMouseCaptureMode=NoCapture,'
                  '[/Script/Engine.InputSettings]:DefaultViewportMouseLockMode=DoNotLock')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return GameProcess(arguments, executable=game, cwd=game.parent, env=env, startupinfo=startup)


def command(case: Path, action: str) -> None:
    runtime.write_text(case / "command.txt", action + "\n", newline="\n")


async def verify_session(case: Path, guest_case: Path, host_process: GameProcess, guest_process: GameProcess,
                         port: int, other_port: int, round_number: int, identity: link.ServerIdentity) -> dict:
    disconnected = asyncio.Queue()
    request = {"guest_id": "test-guest", "credential": secrets.token_urlsafe(32),
               "character_id": "test-character", "protocol_version": 1, "game_build": "same-process-listener"}

    def admit(candidate):
        if candidate == request:
            return {"accepted": True, "character_id": request["character_id"]}
        return {"accepted": False, "reason": "invalid_fixture_credential"}

    async with AsyncExitStack() as resources:
        host = await link.start_host("127.0.0.1", PUBLIC_PORT, "127.0.0.1", port, identity, admit,
                                     disconnected.put_nowait)
        resources.push_async_callback(host.close)
        command(case, f"host{round_number}")
        await observe(case, host_process, f"host\t{round_number}\t1\t1\t1")
        udp_ownership.verify_loopback_socket(host_process.pid, port)
        assert not udp_ownership.owns_udp_port(host_process.pid, PUBLIC_PORT)
        assert not udp_ownership.owns_udp_port(host_process.pid, other_port)

        client = await link.start_client()
        resources.push_async_callback(client.close)
        request["runtime_id"] = client.runtime_id
        denied = await client.connect(*host.address, identity.certificate_pem, dict(request, credential="wrong"))
        assert denied == {"accepted": False, "reason": "invalid_fixture_credential"}
        assert not client.game_access and host.guest_endpoint(request["guest_id"]) is None
        accepted = await client.connect(*host.address, identity.certificate_pem, request)
        assert accepted.get("character_id") == request["character_id"] and client.game_access
        guest_route, local_route = host.guest_endpoint(request["guest_id"]), client.address
        assert guest_route is not None
        command(guest_case, f"join{round_number} {local_route[1]}")
        joined = await observe(guest_case, guest_process, f"joined\t{round_number}")
        live_pawn = joined[-1].split("\t")[2:]
        assert len(live_pawn) == 2 and all(int(value, 16) > 0 for value in live_pawn)
        paired = await observe(case, host_process, f"peer\t{round_number}")
        server_connection = paired[-1].split("\t")[2]
        assert int(server_connection, 16) > 0
        sent, received = client.game_packets_sent, client.game_packets_received
        assert sent > 0 and received > 0
        async with asyncio.timeout(15):
            await client.disconnect()
            assert await disconnected.get() == request["guest_id"]
            accepted = await client.connect(*host.address, identity.certificate_pem, request)
            assert accepted.get("character_id") == request["character_id"] and client.game_access
            assert host.guest_endpoint(request["guest_id"]) == guest_route and client.address == local_route
            while client.game_packets_sent <= sent or client.game_packets_received <= received:
                await asyncio.sleep(.05)
        command(guest_case, f"sample{round_number}")
        command(case, f"sample{round_number}")
        resumed = await observe(guest_case, guest_process, f"sampled\t{round_number}")
        assert resumed[-1].split("\t")[2:] == live_pawn, "The guest's stock pawn or connection changed"
        observed = await observe(case, host_process, f"sampled_peer\t{round_number}")
        assert observed[-1].split("\t")[2] == server_connection, "The host's stock guest connection changed"
        command(guest_case, f"leave{round_number}")
        await observe(guest_case, guest_process, f"title\t{round_number + 1}")
        command(case, f"leave{round_number}")
        events = await observe(case, host_process, f"title\t{round_number + 1}")
        assert not udp_ownership.owns_udp_port(host_process.pid, port), "The retired listener is still bound"
        return {"port": port, "loopback_only": True, "closed_at_title": True, "stock_bind_restored": True,
                "wrong_credential_denied": True, "real_unreal_packets": {"sent": sent, "received": received},
                "reconnected_same_pawn": True, "reconnected_same_host_connection": True, "host_events": events}


async def verify(work: Path, addon: Path, original: Path) -> None:
    if (os.name != "nt" or "steamapps" in {part.casefold() for part in work.parts}
            or work.is_relative_to(original) or original.is_relative_to(work)):
        raise ValueError("Use a dedicated Windows replica and a disjoint original save tree")
    game = work / "stage" / runtime.SHIPPING
    if game.resolve(strict=True) != game or not game.is_file():
        raise ValueError("The stock replica executable must be an ordinary path")
    originals = saved_hashes(original)
    if not originals:
        raise ValueError("Supply the original single-player Saved directory for hash comparison")
    install_fixture(game, addon)
    case = work / "title-probes" / f"same-listen-{uuid.uuid4().hex[:12]}"
    guest_case = case / f"same-listen-{uuid.uuid4().hex[:12]}"
    print(case, flush=True)
    evidence = {"verified": False, "public_test_port": PUBLIC_PORT, "sessions": []}
    processes = []
    try:
        ports = choose_ports()
        host = launch(game, case, ports=ports, guest=False)
        processes.append(host)
        guest = launch(game, guest_case, ports=ports, guest=True)
        processes.append(guest)
        evidence.update(host_pid=host.pid, guest_pid=guest.pid)
        await asyncio.gather(observe(case, host, "title\t1"), observe(guest_case, guest, "title\t1"))
        identity = link.make_server_identity()
        for index, port in enumerate(ports, 1):
            session = await verify_session(case, guest_case, host, guest, port, ports[2 - index], index, identity)
            evidence["sessions"].append(session)
        evidence["verified"] = True
    except (OSError, RuntimeError, ValueError, TimeoutError, AssertionError) as error:
        evidence["failure"] = repr(error)
        raise
    finally:
        for process in reversed(processes):
            await runtime.stop_game(process)
        evidence["original_saves_verified"] = saved_hashes(original) == originals
        evidence["verified"] = evidence["verified"] and evidence["original_saves_verified"]
        runtime.write_json(case / "live-same-process-listener-verification.json", evidence)
        assert evidence["original_saves_verified"], "The original single-player save hashes changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--addon", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.addon.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
