"""Verify both remote avatar projections and capture an offscreen guest PNG."""

import argparse
import asyncio
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import CROSSROADS, saved_hashes


REPO = Path(__file__).resolve().parents[1]
PROBE = "SilverfishAvatarProbe"


@contextmanager
def installed_probe(work):
    mod = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods" / PROBE
    mod.mkdir()
    scripts = mod / "Scripts"
    script, activation = scripts / "main.lua", mod / "enabled.txt"
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / "avatar_presentation_probe.lua", script)
        activation.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        activation.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        mod.rmdir()


async def avatar_probe(run, operation):
    ticket = uuid.uuid4().hex
    mailbox, = (run / "game-bridge").iterdir()
    pending = mailbox / "avatar-probe-command.pending"
    response = mailbox / "avatar-probe-reply.tsv"
    pending.write_text(f"{ticket}\t{operation}\n", encoding="ascii", newline="")
    pending.replace(mailbox / "avatar-probe-command.tsv")
    async with asyncio.timeout(15):
        while True:
            try:
                fields = response.read_text(encoding="utf-8").rstrip().split("\t", 1)
                if fields[0] == ticket and len(fields) == 2:
                    assert fields[1] == "ok", fields[1]
                    return
            except (FileNotFoundError, PermissionError):
                pass
            await asyncio.sleep(.1)


async def capture(work: Path, vanilla_saved: Path, lifecycle: bool, equipment: bool = False) -> None:
    profile = "avatar-" + uuid.uuid4().hex[:8]
    host_dir = work / "campaigns" / profile
    guest_dir = work / "clients" / profile
    host_run, guest_run = host_dir / "run", guest_dir / "run"
    host_run.mkdir(parents=True)
    env = dict(os.environ, SILVERFISH_COOP_RENDER_CAPTURE="guest")
    if equipment:
        env["SILVERFISH_COOP_RENDER_CAPTURE_EQUIPMENT"] = "1"
    base = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    before = saved_hashes(vanilla_saved)
    processes = []
    host = ("--campaign", profile)
    guest = ("--profile", profile)

    async def command(*args):
        process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, error = await asyncio.wait_for(process.communicate(), 25)
        if process.returncode:
            raise RuntimeError(f"{args} failed with {output!r} {error!r}")
        return json.loads(output)

    async def start(log, target, *args):
        with (host_run / log).open("wb") as stream:
            process = await asyncio.create_subprocess_exec(*base, *args, cwd=REPO, env=env,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, target))
        return process

    def projections(state):
        game = state.get("game", {})
        remote = game.get("pawns", {}).get("guest", {}).get("name")
        items = game.get("native_player_avatars", {})
        return items if remote and set(items) == {remote} else None

    print(host_run, flush=True)
    try:
        await start("host.txt", host, "host", *host, "--port", "0", "--headless", "--fixtures",
            "--native-events", "--native-guest", "--native-zones", "--start-zone", CROSSROADS)
        await wait(host_run, lambda state: state.get("state") == "hosting")
        invitation = await command("invite", *host, "--name", "Loopback avatar guest", "--character", "Avatar guest")
        join = ("join", *guest, "--invite", invitation["invite_file"], "--headless", "--native-zones")
        guest_process = await start("guest.txt", guest, *join)
        evidence = {}
        for role, run in (("host", host_run), ("guest", guest_run)):
            state = await wait(run, projections)
            evidence[role] = projections(state)
            assert all(row["state"] == "default_body" for row in evidence[role].values()), evidence
        if lifecycle:
            identity = invitation["character_id"]
        if equipment:
            await wait(host_run, lambda state: state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")
            await avatar_probe(host_run, "equip")
            guid = uuid.uuid4().hex.upper()
            await command("action", *host, "native-guest-weapon-seed", "--target", "guest", "--kind", "1911", "--item-guid", guid)
            await command("action", *guest, "native-guest-weapon-equip", "--target", guid)

            def equipped_projection(state, *, on_host):
                rows = projections(state)
                if not rows:
                    return
                remote, = rows.values()
                if remote.get("diagnostic") or abs(remote.get("head_roll", 90)) > .1:
                    return
                appearance = remote.get("appearance", {})
                expected_face = "SM_Head_UV_Fangs_01" if on_host else "SM_Head_UV_02_EarRing"
                expected_plumage = "SM_Plumage_012" if on_host else "SM_Plumage_007"
                expected_skin = [.8, .25, .33, 1] if on_host else [.2, .7, .35, 1]
                expected_weapon = {"M1911_Frame", "M1911_Mag", "M1911_Slide"} if on_host else {
                    "9mmAuto_Frame", "9mmAuto_Mag", "9mmAuto_Slide"}
                if (appearance.get("face", "").endswith("." + expected_face)
                        and appearance.get("plumage", "").endswith("." + expected_plumage)
                        and appearance.get("skin_color") == expected_skin
                        and remote.get("body", "").endswith(".Armor_HunterJacket_C")
                        and expected_weapon <= {path.rsplit(".", 1)[-1] for path in remote.get("weapon_meshes", ())}):
                    return remote

            evidence["held"] = {
                role: equipped_projection(await wait(run, lambda state: equipped_projection(state, on_host=(role == "host")), timeout=18),
                                          on_host=(role == "host"))
                for role, run in (("host", host_run), ("guest", guest_run))
            }
            await command("action", *guest, "drop-link")
            await wait(host_run, lambda state: any(lease["character_id"] == invitation["character_id"]
                       and lease["state"] == "reserved" for lease in state.get("campaign", {}).get("leases", ())))
            assert (await command("action", *guest, "reconnect"))["resumed"]
            for run, on_host in ((host_run, True), (guest_run, False)):
                await wait(run, lambda state: equipped_projection(state, on_host=on_host))
            evidence["brief_reconnect"] = True
        for _ in range(80):
            images = list((guest_dir / "game-user").rglob("*.png"))
            if images:
                screenshot = images[0]
                assert screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), screenshot
                break
            await asyncio.sleep(.25)
        else:
            raise RuntimeError("The isolated guest did not write a screenshot")
        evidence["screenshot"] = str(screenshot)
        if equipment:
            await avatar_probe(host_run, "holster")
            def unarmed_host(state):
                rows = projections(state)
                if rows:
                    remote, = rows.values()
                    return remote.get("weapon_meshes") == [] and remote.get("appearance", {}).get("face", "").endswith(
                        ".SM_Head_UV_02_EarRing")
            await wait(guest_run, unarmed_host)
            evidence["host_holster_cleared"] = True
        if lifecycle:
            await wait(host_run, lambda state: any(lease["character_id"] == identity and lease["state"] == "active"
                       for lease in state.get("campaign", {}).get("leases", ())))
            departure = await command("action", *guest, "leave-session")
            assert departure["retirement_confirmed"], departure
            assert await asyncio.wait_for(guest_process.wait(), 18) == 0
            await wait(host_run, lambda state: state.get("game", {}).get("remote_clients") == 0
                       and not state["game"].get("native_player_avatars"))
            env.pop("SILVERFISH_COOP_RENDER_CAPTURE")
            await start("guest-returned.txt", guest, *join)
            def restored(state, role):
                rows = projections(state)
                if not rows or any(row.get("diagnostic") or row.get("head_roll") != 0 for row in rows.values()):
                    return
                if equipment and any(row.get("appearance") != evidence["held"][role]["appearance"] for row in rows.values()):
                    return
                return rows
            host_returned = await wait(host_run, lambda state: restored(state, "host")
                                       and set(restored(state, "host")).isdisjoint(evidence["host"]))
            guest_returned = await wait(guest_run, lambda state: state.get("character_id") == identity and restored(state, "guest"))
            evidence["returned"] = {"host": projections(host_returned), "guest": projections(guest_returned)}
            assert all(row["state"] == "default_body" for peer in evidence["returned"].values() for row in peer.values()), evidence
        print(json.dumps(evidence, indent=2), flush=True)
        (host_run / "live-avatar-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    finally:
        for process, target in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *target, "stop")
                    await asyncio.wait_for(process.wait(), 12)
                except (RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await process.wait()
        assert before == saved_hashes(vanilla_saved), "Original singleplayer save files changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    parser.add_argument("--lifecycle", action="store_true", help="verify avatar removal upon leaving and restoration upon rejoin")
    parser.add_argument("--equipment", action="store_true", help="verify distinct saved faces and multipart held pistols on both peers")
    options = parser.parse_args()
    work = options.work.resolve(strict=True)
    with installed_probe(work) if options.equipment else nullcontext():
        asyncio.run(capture(work, options.vanilla_saved.resolve(strict=True), options.lifecycle, options.equipment))
