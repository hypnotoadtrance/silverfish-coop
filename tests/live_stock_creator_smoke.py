"""Drive the stock creator and saved campaign picker in isolated co-op titles."""

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import re
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.silverfish import embedded_startup, embedded_title_plan, native_host_save, runtime, service
from tools.silverfish.campaign import CampaignStore
from tools.silverfish.udp_ownership import owns_udp_port, verify_loopback_socket
from live_ai_smoke import status, wait
from live_multi_smoke import saved_hashes
from live_resident_host_smoke import stock_profile, stock_save_hashes
from live_same_process_listener_smoke import choose_ports
from live_stock_pause_exit_smoke import stock_pause
from live_unified_title_smoke import Seat, offered


EDITOR = "/Game/Classes/Maps/CharacterSelect_Map_P"
TUTORIAL = "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P"
FORM = "native-title-host-form-fixture"
BUTTON_FIXTURE = "native-title-button-fixture"
MOD = "SilverfishStockCreatorProbe"


@contextmanager
def installed_probe(work: Path):
    mods = work / "stage" / runtime.SHIPPING.parent / "ue4ss" / "Mods"
    if mods.resolve(strict=True) != mods:
        raise ValueError("Use a private mod directory with its own path")
    directory = mods / MOD
    directory.mkdir()
    scripts = directory / "Scripts"
    enabled = directory / "enabled.txt"
    source = Path(__file__).with_name("fixtures") / "stock_creator_probe.lua"
    script = scripts / "main.lua"
    try:
        scripts.mkdir()
        shutil.copyfile(source, script)
        enabled.touch(exist_ok=False)
        yield
    finally:
        if directory.resolve(strict=True) != directory or scripts.resolve() != scripts:
            raise RuntimeError("The private creator fixture path changed during the test")
        enabled.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        directory.rmdir()


def campaign(seat: Seat) -> dict:
    return CampaignStore.read_existing(
        seat.campaign / "save",
        verify_world=service.verify_travel_checkpoint,
        verify_checkpoint=lambda checkpoint: native_host_save.verify_checkpoint(seat.profile, checkpoint),
    )


def events(run: Path, generation: str, name: str) -> list[dict]:
    rows = (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [row for row in map(json.loads, rows) if (row["generation"], row["event"]) == (generation, name)]


async def creator_action(seat: Seat, operation: str) -> str:
    mailbox = seat.bridge.directory
    request, answer = (mailbox / f"stock-creator-probe-{name}.tsv" for name in ("command", "reply"))
    latest = "fixture has not replied"
    deadline = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < deadline:
        ticket = uuid.uuid4().hex
        try:
            answer.unlink(missing_ok=True)
        except PermissionError:  # Windows can still have the previous reply open during its replacement.
            await asyncio.sleep(.08)
            continue
        runtime.write_text(request, f"1\t{ticket}\t{operation}\n", newline="")
        while asyncio.get_running_loop().time() < deadline:
            if seat.bridge.process.poll() is not None:
                raise RuntimeError(f"Stock creator process exited during {operation}: {seat.bridge.process.returncode}")
            try:
                row = answer.read_text(encoding="utf-8").rstrip("\n").split("\t", 3)
            except (FileNotFoundError, PermissionError):
                row = ()
            if len(row) == 4 and row[:2] == ["1", ticket]:
                state, latest = row[2:]
                if state == "ok":
                    return latest
                if state == "waiting":
                    break
                raise RuntimeError(f"Stock creator {operation} failed: {latest}")
            await asyncio.sleep(.08)
        await asyncio.sleep(.08)
    raise TimeoutError(f"Stock creator {operation} did not finish: {latest}")


async def click(seat: Seat, *keys: str) -> None:
    for key in keys:
        queued = await seat.bridge.command(BUTTON_FIXTURE, key)
        assert queued["ack"]["message"] == "title_button_queued", (key, queued["ack"])
        await seat.bridge.wait_for(lambda frame: frame["sequence"] > queued["sequence"], timeout=8)


def created_campaigns(title: Seat) -> set[Path]:
    return {path for path in title.campaign.parent.glob("campaign-*")
            if re.fullmatch(r"campaign-[0-9a-f]{32}", path.name) and path.is_dir() and not path.is_symlink()}


async def enter_creator(title: Seat) -> tuple[Seat, dict]:
    baseline = created_campaigns(title)
    await click(title, "multiplayer", "host", "new")
    try:
        await title.bridge.command(FORM, "campaign", title.campaign.name)
    except RuntimeError as exc:
        assert str(exc) == "title_host_form_fixture_arguments"
    else:
        raise AssertionError("The native New Game form accepted a manual campaign name")
    await title.bridge.command(FORM, "port", str(title.port))
    await click(title, "submit")

    pid = title.bridge.process.pid
    deadline = asyncio.get_running_loop().time() + 30
    observed = {}
    while asyncio.get_running_loop().time() < deadline:
        observed = {path: status(path / "run") for path in created_campaigns(title) - baseline}
        matches = [path for path, frame in observed.items() if frame.get("pid") == pid]
        if matches:
            allocated, = matches
            break
        if title.bridge.process.poll() is not None:
            raise RuntimeError(f"Title exited during New Game ({title.bridge.process.returncode})")
        await asyncio.sleep(.1)
    else:
        seen = {path.name: frame.get("state") for path, frame in observed.items()}
        raise TimeoutError(f"New Game did not allocate an automatic campaign for PID {pid}: {seen}")

    seat = replace(title, campaign=allocated)
    state = await wait(
        seat.campaign / "run",
        lambda frame: frame.get("state") == "character_creation" and frame.get("game", {}).get("map") == EDITOR
        and frame["game"].get("native_stock_creator", {}).get("state") == "creating",
    )
    receipt = state["game"]["native_stock_creator"]
    assert state["pid"] == pid and not owns_udp_port(pid, seat.port)
    assert state["game"]["save_scope"] and receipt["working_slot"].startswith("SFCoopLive_")
    assert await creator_action(seat, "observe") == "page=0"
    return seat, state


async def join_created_campaign(host: Seat, guest: Seat) -> dict:
    async def join(*args):
        return await guest.bridge.command("native-title-join-form-fixture", *args)

    async def screen(expected):
        async with asyncio.timeout(22):
            while (await join("observe"))["ack"]["message"] != f"title_join_form_state={expected}":
                await asyncio.sleep(.1)

    await click(guest, "multiplayer", "join")
    await join("address", "127.0.0.1")
    await join("port", str(host.port))
    await click(guest, "connect")
    await screen("trust_host")
    await click(guest, "trust")
    await screen("catalog;count=0")
    await join("create-editor", "Tutorial Scout")
    await guest.bridge.wait_for(lambda frame: frame.get("native_guest_stock_creator", {}).get("state") == "creating")
    assert await creator_action(guest, "observe") == "page=0"
    face, plumage = (await creator_action(guest, "gueststyle")).split("|")
    assert await creator_action(guest, "guestnext") == "page=3"
    await creator_action(guest, "finish")
    await offered(guest)
    await screen("catalog;count=1")
    characters = campaign(host)["characters"]
    character, = characters
    appearance = characters[character]["state"]["creator_appearance"]
    profile = characters[character]["state"]["creator_profile"]
    assert appearance.split("|")[:2] == [face, plumage]
    assert profile.split("|")[0] == "kit2" and "WeirdLuck" in profile.split("|")[6].split(",")
    assert profile.split("|")[1] == "Fem_02"
    await click(guest, "character:1", "confirm")
    joined = await wait(guest.client / "run", lambda frame: frame.get("state") == "connected"
                        and frame.get("gameplay_fresh") is True)
    assert joined["pid"] == guest.bridge.process.pid and joined["character_id"] == character
    assert joined["game"]["map"] == TUTORIAL and joined["game"]["save_scope"] == uuid.UUID(character).hex
    verify_loopback_socket(joined["pid"], joined["unreal_local_port"])
    authoritative = await host.bridge.wait_for(
        lambda frame: frame.get("native_guest_save", {}).get("state") == "bound" and any(
            outfit.get("appearance") == appearance
            and outfit.get("pawn") == frame["native_guest_save"]["pawn"]
            for outfit in frame.get("native_avatar_outfits", {}).values()))
    pawn = authoritative["native_guest_save"]["pawn"]
    await host.bridge.wait_for(lambda frame: frame.get("native_player_avatars", {}).get(pawn, {}).get("head_roll") == 0)
    nonce = uuid.uuid4().hex
    await host.bridge.command("native-guest-ui-read", pawn, nonce)
    host_view = host.bridge.directory / f"native-guest-ui-{nonce}.tsv"
    rows = host_view.read_text(encoding="ascii").splitlines()
    assert any(row.startswith("item\tbackpack\t") and "Random_GearBag_Starter_C" in row for row in rows)
    assert any(row.startswith("character\t") and "WeirdLuck" in row for row in rows)
    return joined, appearance, profile


async def verify_voice_and_bullet_time(host: Seat, guest: Seat, hosted: dict, joined: dict) -> dict:
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def heard(run, generation, cue):
        async with asyncio.timeout(8):
            while True:
                for event in events(run, generation, "native_voice_played"):
                    if event.get("cue") == cue:
                        return event["personality"]
                await asyncio.sleep(.12)

    async with asyncio.timeout(12):
        while not events(guest_run, joined["generation"], "native_guest_ui_projected"):
            await asyncio.sleep(.12)
    await guest.bridge.command("native-voice-fixture", "cough")
    assert await heard(host_run, hosted["generation"], "cough") == "Fem_02"
    await host.bridge.command("native-voice-fixture", "hurt")
    host_voice = await heard(guest_run, joined["generation"], "hurt")

    async def shift(seat, verb):
        snapshot = await seat.bridge.command("native-time-shift-fixture", verb)
        active, dilation, walk = snapshot["ack"]["message"].split(";")
        return int(active), float(dilation), float(walk)

    host_before, guest_before = await asyncio.gather(shift(host, "observe"), shift(guest, "observe"))
    active, dilation, walk = await shift(host, "start")
    assert active == 1 and dilation == 1 and walk > host_before[2]
    assert await shift(guest, "observe") == guest_before
    async with asyncio.timeout(6):
        while (await shift(host, "observe"))[0]:
            await asyncio.sleep(.15)
    return {"guest_cough": "Fem_02", "host_hurt": host_voice, "bullet_time_world_dilation": dilation,
            "owner_walk_speed_before": host_before[2], "owner_walk_speed_during": walk}


async def leave_tutorial(seat: Seat) -> dict:
    await seat.bridge.wait_for(lambda game: game.get("stock_pause_exit", {}).get("state") == "armed")
    # The stock tutorial offers Exit to Menu here. Its confirmed exit checkpoints
    # the co-op campaign before returning both resident titles.
    for action in ("open", "running", "prompt", "confirm"):
        await stock_pause(seat.bridge, action)
    stopped = await wait(seat.campaign / "run", lambda frame: frame.get("state") == "stopped")
    assert stopped["stop_reason"] == "host_session_ended"
    finished, = events(seat.campaign / "run", stopped["generation"], "stock_pause_exit")
    assert finished["reason"] == "quit_to_menu" and finished["result"] == "ok"
    await offered(seat)
    assert seat.bridge.process.poll() is None and not owns_udp_port(stopped["pid"], seat.port)
    return stopped


async def picker_state(seat: Seat, expected_count: int) -> str | None:
    deadline = asyncio.get_running_loop().time() + 15
    observed = None
    while asyncio.get_running_loop().time() < deadline:
        response = await seat.bridge.command(FORM, "saved-state")
        observed = response["ack"]["message"]
        state = observed.removeprefix("title_host_saved=")
        if expected_count == 0 and state == "empty":
            return None
        count, _, highlighted = state.partition(":")
        if expected_count > 0 and count == str(expected_count) and highlighted:
            return highlighted
        if state == "unavailable":
            await click(seat, "browse")
        await asyncio.sleep(.1)
    raise RuntimeError(f"Saved campaign picker expected {expected_count} campaigns, observed {observed}")


async def empty_picker(seat: Seat) -> None:
    await click(seat, "multiplayer", "host", "browse")
    assert await picker_state(seat, 0) is None
    try:
        await seat.bridge.command(BUTTON_FIXTURE, "load:" + seat.campaign.name)
    except RuntimeError as exc:
        assert str(exc) == "title_panel_fixture_button_unavailable"
    else:
        raise AssertionError("The empty campaign picker accepted a selection")
    await click(seat, "back", "back", "modes")
    await offered(seat)


async def finish_veteran(seat: Seat, attempt: dict) -> tuple[dict, dict]:
    controls = {}
    for operation, expected in (
        ("preset", "page=4"),
        ("veteran", "class=Veteran;starting_cash=350;starting_map=Observitory_P"),
        ("next", "page=3"),
        ("custom", "difficulty=Custom;replenish_loot=0"),
        ("finish", "stock_finish_invoked"),
    ):
        observed = await creator_action(seat, operation)
        assert observed == expected, (operation, observed)
        controls[operation] = observed

    hosted = await wait(seat.campaign / "run", lambda frame: frame.get("state") == "hosting", timeout=55)
    receipt = hosted["game"]["native_stock_creator"]
    assert hosted["generation"] == attempt["generation"] and hosted["pid"] == seat.bridge.process.pid
    assert receipt == dict(attempt["game"]["native_stock_creator"], state="verified", map=TUTORIAL), receipt
    assert hosted["game"]["map"] == TUTORIAL and hosted["game"]["save_scope"] == attempt["game"]["save_scope"]
    assert hosted["game"]["native_rules"]["replenish_loot"] == 0 and hosted["game"]["native_wallet"] == 350
    verify_loopback_socket(hosted["pid"], seat.port)
    return hosted, controls


async def choose_saved(seat: Seat, sibling: Seat) -> list[str]:
    await click(seat, "multiplayer", "host", "browse")
    first = await picker_state(seat, 2)
    assert first in {seat.campaign.name, sibling.campaign.name}, first
    await click(seat, "load:" + sibling.campaign.name)
    await seat.bridge.command(FORM, "port", str(seat.port))
    await click(seat, "back")
    assert await picker_state(seat, 2) == first
    await click(seat, "load:" + seat.campaign.name)
    await seat.bridge.command(FORM, "port", str(seat.port))
    await click(seat, "submit")
    other = sibling.campaign.name if first == seat.campaign.name else seat.campaign.name
    return [first, other]


async def drive_creator(title: Seat, guest: Seat, evidence: dict) -> None:
    await click(title, "singleplayer", "modes")
    baseline = created_campaigns(title)
    cancelled_seat, before = await enter_creator(title)
    assert await creator_action(cancelled_seat, "cancel") == "stock_back_invoked"
    cancelled = await wait(cancelled_seat.campaign / "run", lambda frame: frame.get("state") == "stopped")
    assert cancelled["stop_reason"] == "creator_cancelled" and cancelled["generation"] == before["generation"]
    await offered(title)
    assert not owns_udp_port(cancelled["pid"], title.port)
    assert created_campaigns(title) - baseline <= {cancelled_seat.campaign}
    if (cancelled_seat.campaign / "save/campaign.json").exists():
        assert campaign(cancelled_seat)["world"] == {}
    scoped = native_host_save.directory(native_host_save.SaveScope(title.profile, before["game"]["save_scope"]))
    slot = before["game"]["native_stock_creator"]["working_slot"]
    assert not any(scoped.glob("*.sav")), "Cancelling created a scoped stock save"
    evidence.update(cancelled_without_checkpoint=True, cancelled_auto_campaign=cancelled_seat.campaign.name)
    print("Stock creator cancelled back to the same standalone title", flush=True)

    await empty_picker(cancelled_seat)
    evidence["empty_picker_excluded_solo_and_cancelled_saves"] = True
    print("Saved campaign picker excludes solo saves and the cancelled campaign", flush=True)

    seat, attempt = await enter_creator(title)
    assert seat.campaign != cancelled_seat.campaign and attempt["generation"] != cancelled["generation"]
    run = seat.campaign / "run"
    scope = attempt["game"]["save_scope"]
    assert attempt["game"]["native_stock_creator"]["working_slot"] != slot
    hosted, controls = await finish_veteran(seat, attempt)
    checkpoint = campaign(seat)
    commit, = events(run, hosted["generation"], "native_stock_creator_committed")
    assert commit["map"] == TUTORIAL and 0 < commit["revision"] <= checkpoint["revision"]
    assert checkpoint["world"].get("native_observation", {}).get("native_rules", {}).get("replenish_loot") == 0
    evidence.update(pid=hosted["pid"], map=TUTORIAL, native_creator=hosted["game"]["native_stock_creator"],
                    creator_controls=controls, starting_wallet=350,
                    automatic_campaign=seat.campaign.name,
                    campaign_revision=checkpoint["revision"], live_replenish_loot=0, checkpoint_replenish_loot=0)
    print("Stock Veteran and Custom settings committed to the authoritative tutorial host", flush=True)

    joined, appearance, profile = await join_created_campaign(seat, guest)
    evidence.update(guest_pid=joined["pid"], guest_character=joined["character_id"], direct_ip_joined=True,
                    guest_face_and_plumage=appearance.split("|")[:2], guest_avatar_head_roll=0)
    evidence.update(await verify_voice_and_bullet_time(seat, guest, hosted, joined))
    print("Second stock title joined the created campaign over Direct IP", flush=True)
    stopped = await leave_tutorial(seat)
    guest_stopped = await wait(guest.client / "run", lambda frame: frame.get("state") == "stopped")
    assert guest_stopped["stop_reason"] == "host_session_ended"
    await offered(guest)
    assert not owns_udp_port(joined["pid"], joined["unreal_local_port"])
    saved = campaign(seat)
    assert saved["revision"] > checkpoint["revision"]
    assert saved["characters"][joined["character_id"]]["state"]["native_guest_save"]
    assert saved["characters"][joined["character_id"]]["state"]["creator_appearance"] == appearance
    assert saved["characters"][joined["character_id"]]["state"]["creator_profile"] == profile
    evidence["stock_pause_returned_both_titles"] = True

    sibling, sibling_attempt = await enter_creator(title)
    assert sibling.campaign not in {seat.campaign, cancelled_seat.campaign}
    sibling_hosted, _ = await finish_veteran(sibling, sibling_attempt)
    await leave_tutorial(sibling)
    sibling_saved = campaign(sibling)
    sibling_scope = sibling_hosted["game"]["save_scope"]
    assert sibling_scope != scope and sibling_saved["campaign_id"] != saved["campaign_id"]
    assert sibling_saved["world"]["native_host_save"]["slot"] != saved["world"]["native_host_save"]["slot"]
    sibling_files = native_host_save.directory(native_host_save.SaveScope(seat.profile, sibling_scope))
    sibling_before = (saved_hashes(sibling.campaign), saved_hashes(sibling_files))
    assert all(sibling_before)

    evidence["saved_campaign_picker_rows"] = await choose_saved(seat, sibling)
    resumed = await wait(run, lambda frame: frame.get("generation") != stopped["generation"]
                         and frame.get("state") == "hosting", timeout=55)
    assert resumed["pid"] == hosted["pid"] and resumed["game"]["save_scope"] == scope
    restored, = events(run, resumed["generation"], "native_host_save_restored")
    assert resumed["game"]["map"] == TUTORIAL and restored["map"] == TUTORIAL
    assert restored["state"] == "restored" and restored["slot"] == saved["world"]["native_host_save"]["slot"]
    assert resumed["game"]["native_wallet"] == 350 and resumed["game"]["native_rules"]["replenish_loot"] == 0
    verify_loopback_socket(resumed["pid"], seat.port)
    assert campaign(seat)["campaign_id"] == saved["campaign_id"]
    await leave_tutorial(seat)
    assert (saved_hashes(sibling.campaign), saved_hashes(sibling_files)) == sibling_before
    evidence.update(selected_saved_campaign=seat.campaign.name, sibling_campaign_unchanged=True,
                    native_title_buttons_verified=True,
                    reopened_existing_campaign_and_restored_stock_character=True)
    print("Saved campaign picker restored the selected checkpoint without changing the sibling campaign", flush=True)


async def start_seat(work: Path, port: int, stock_slots: list[Path], seats: list[Seat]):
    name = "title-host-settings-" + uuid.uuid4().hex[:8]
    title = work / "titles" / name
    profile, nonce = title / "game-user", uuid.uuid4().hex
    mailbox = title / "run/game-bridge" / nonce
    mailbox.mkdir(parents=True)
    runtime.write_json(mailbox / embedded_startup.UNIFIED_FILE,
                       {"schema": 1, "campaign": name, "bind": "127.0.0.1", "port": port})
    stock = profile / "Saved/SaveGames"
    stock.mkdir(parents=True)
    for file in stock_slots:
        shutil.copy2(file, stock / file.name)
    hashes = stock_save_hashes(profile)
    del hashes["Config.sav"]
    process = runtime.launch(work, profile, mailbox, True, embedded_engine=True, embedded_startup=nonce,
                             resident_title=True, resident_direct_join=True, unified_title=True,
                             title_fixture=True, pause_fixture=True, **service.unified_title_options(work))
    seat = Seat(profile, work / "campaigns" / name, work / "clients" / name, port, runtime.Bridge(mailbox, process))
    seats.append(seat)
    await offered(seat)
    return seat, hashes, await stock_profile(seat.bridge)


async def verify(work: Path, original: Path) -> None:
    if ("steamapps" in {part.casefold() for part in work.parts} or work.is_relative_to(original)
            or original.is_relative_to(work) or not embedded_title_plan.installed(work)):
        raise ValueError("Use the qualified private co-op replica and the disjoint original save folder")
    before = saved_hashes(original)
    stock_slots = sorted((original / "SaveGames").glob("*.sav"))
    if not before or not any(file.name == "Config.sav" for file in stock_slots) or len(stock_slots) < 2:
        raise ValueError("Supply the original Saved directory with its stock configuration and campaign slots")

    case = work / "title-probes" / ("stock-creator-" + uuid.uuid4().hex[:8])
    case.mkdir(parents=True)
    print(case, flush=True)
    proof = {"verified": False}

    try:
        with installed_probe(work):
            seats = []
            try:
                profiles = []
                for side, port in zip(("host", "guest"), choose_ports(), strict=True):
                    seat, slots, preferences = await start_seat(work, port, stock_slots, seats)
                    profiles.append((seat, slots, preferences))
                    proof[f"isolated_{side}"] = seat.profile.parent.name
                await drive_creator(*seats, proof)
                for seat, stock_before, preferences in profiles:
                    stock_after = stock_save_hashes(seat.profile)
                    del stock_after["Config.sav"]
                    assert stock_after == stock_before and await stock_profile(seat.bridge) == preferences
                proof.update(verified=True, solo_slots_and_settings_preserved=True)
            finally:
                for seat in reversed(seats):
                    process = seat.bridge.process
                    if process.poll() is None:
                        await runtime.stop_game(process)
    finally:
        proof["original_saves_verified"] = saved_hashes(original) == before
        proof["verified"] &= proof["original_saves_verified"]
        runtime.write_json(case / "stock-creator-verification.json", proof)
        assert proof["original_saves_verified"], "The original player saves changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--vanilla-saved", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.work.resolve(strict=True), args.vanilla_saved.resolve(strict=True)))
