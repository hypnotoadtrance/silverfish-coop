"""Check the stock offer and optionally finish the Dead Bridge turret photo contract."""

import argparse
import asyncio
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

from live_ai_smoke import wait
from live_multi_smoke import campaign_save_hashes, saved_hashes

BOROUGH = "/Game/Classes/Maps/Maps_SafeZones/Borough"
SCIENTIST = "/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01"
CITY = "/Game/Classes/Maps/Maps_OverWorld/City_01_P"
RAIL = "/Game/Classes/Maps/Maps_OverWorld/RailYard_P"
RUINS = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
CROSSROADS = "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P"
DEAD_BRIDGE = "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P"
LAKE = "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P"
CAMERA = "/Game/Classes/Equipment/Eqp_Camera.Eqp_Camera_C"
QUEST = "Photo_DB_Turrets"


async def validate(work: Path, vanilla_saved: Path | None, *, complete=False, resume=None, camera_input=False, host_camera=False):
    profile = resume or "turret-photo-" + uuid.uuid4().hex[:8]
    host = ("--campaign", profile)
    guest = ("--profile", profile)
    run = work / "campaigns" / profile / "run"
    guest_run = work / "clients" / profile / "run"
    run.mkdir(parents=True, exist_ok=True)
    print(run, flush=True)
    original = saved_hashes(vanilla_saved) if vanilla_saved else None
    cli = (sys.executable, "-B", "-m", "tools.silverfish", "--work", str(work))
    processes = []
    first_report = run / "live-turret-photo-offer.json"
    report_file = run / f"live-turret-photo-resume-{uuid.uuid4().hex[:8]}.json" if resume else first_report
    evidence = json.loads(first_report.read_text(encoding="utf-8")) if resume else {}
    evidence.pop("failure", None)

    async def command(*arguments, expected=True):
        process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        output, errors = await asyncio.wait_for(process.communicate(), 35)
        result = json.loads(output)
        if result.get("ok", True) != expected or bool(process.returncode) == expected:
            raise RuntimeError(f"{arguments}: {result} {errors.decode(errors='replace')}")
        return result

    async def launch(owner, label, *arguments):
        with (run / f"{label}.txt").open("wb") as log:
            process = await asyncio.create_subprocess_exec(*cli, *arguments, stdout=log, stderr=log,
                                                            creationflags=subprocess.CREATE_NO_WINDOW)
        processes.append((process, owner))
        return process

    async def enrolled(area):
        await wait(guest_run, lambda state: state.get("state") == "connected"
                   and state.get("game", {}).get("map") == area)
        await wait(run, lambda state: state.get("state") == "hosting"
                   and state.get("game", {}).get("map") == area
                   and state.get("game", {}).get("native_guest_save", {}).get("state") == "bound")

    async def enroll():
        await enrolled(BOROUGH)
        await command("action", *host, "native-wire-quest-approach", "--target", "guest")

    async def offers():
        more, stock = 0, {}
        for _ in range(16):
            page = (await command("action", *guest, "native-bounty-board-list", "--target", str(more)))["native_bounty_board"]
            assert page["map"] == BOROUGH and page["source"] == "board" and page["offset"] == more, page
            for item in page["quests"]:
                assert item["id"] not in stock and item["state"] == "offered", item
                stock[item["id"]] = item
            more = page["next_offset"]
            if more is None:
                return stock
        raise RuntimeError("The stock board produced too many offer pages")

    async def cross(destination, tag, door=False):
        entry = ("--entrance", tag)
        position = "native-door-position" if door else "native-exit-position"
        for role in ("host", "guest"):
            await command("action", *host, position, "--target", role, "--exit-position", "near", *entry)
        if door:
            await command("action", *host, "native-door-probe", "--target", "host", *entry)
        else:
            await command("action", *host, position, "--target", "host", "--exit-position", "inside", *entry)
        await enrolled(destination)
        evidence.setdefault("stock_routes", []).append({"map": destination, "entrance": tag, "door": door})

    async def finish_contract():
        async def buy_camera(owner):
            shop = (await command("action", *owner, "native-finite-shop-list"))["native_finite_shop"]
            offer, = [row for row in shop["offers"] if row["class_path"] == CAMERA]
            quote = f"{offer['guid']}@{offer['price']}"
            purchase = (await command("action", *owner, "native-finite-shop-buy", "--target", quote))["native_finite_purchase"]
            assert purchase["stock_source"] == "stock_shelf" and purchase["offers"][0]["guid"] != offer["guid"], purchase
            return purchase

        if not resume:
            for destination, tag, door in ((CITY, "City_Borough", False), (RAIL, "Rail_City", False),
                                           (RUINS, "Ry_Rf", False), (SCIENTIST, "Market_Flood", True)):
                await cross(destination, tag, door)
            await command("action", *host, "native-finite-shop-approach")
            await command("action", *host, "native-finite-shop-wallet-fixture", "--amount", "1000")
            evidence["stock_camera_purchase"] = await buy_camera(guest)
            if host_camera:
                await command("action", *host, "native-finite-shop-approach", "--target", "host")
                evidence["stock_host_camera_purchase"] = await buy_camera(host)
            for destination, tag, door in ((RUINS, "Market_Flood", True), (CROSSROADS, "CR_RF", False),
                                           (DEAD_BRIDGE, "DB_CR", False)):
                await cross(destination, tag, door)
        purchase = evidence["stock_camera_purchase"]
        camera = purchase["offers"][0]["guid"]
        host_purchase = evidence.get("stock_host_camera_purchase")
        if host_camera:
            assert host_purchase, "Start this independent lab with --host-camera for both stock purchases"
            host_guid = host_purchase["offers"][0]["guid"]
            assert host_guid != camera, (host_guid, camera)
        equipped = (await command("action", *guest, "native-guest-weapon-equip", "--target", camera))["native_guest_weapon"]
        assert (equipped["status"], equipped["guid"], equipped["kind"]) == ("camera_equipped", camera, "camera"), equipped
        evidence["stock_camera_equipped"] = equipped
        state = await wait(run, lambda current: current.get("state") == "hosting"
                           and current.get("game", {}).get("map") == DEAD_BRIDGE
                           and current.get("gameplay", {}).get("native_quests", {}).get(QUEST))
        recorded = state["gameplay"]["native_quests"][QUEST]["photos"]
        if camera_input and len(recorded) < 2:
            await wait(guest_run, lambda state: (scope := state.get("game", {}).get("native_guest_primary_scope", {}))
                       and (scope.get("kind"), scope.get("guid")) == ("camera", camera))
            press = (await command("action", *guest, "native-guest-primary-input-fixture"))["native_guest_primary_fixture"]
            assert press["guid"] == camera and press["stock_wrappers"] == 2, press
            for _ in range(25):
                records = (await command("events", *guest, "--tail", "30"))["events"]
                event = next((row for row in records if row.get("sequence") == press["sequence"]
                              and row["event"] == "native_guest_camera_shutter_rejected"), None)
                if event:
                    assert event["reason"] == "native_turret_photo_no_target_in_frame", event
                    evidence["unframed_stock_shutter"] = {"press": press, "receipt": event}
                    break
                await asyncio.sleep(.2)
            else:
                raise RuntimeError("The stock camera input did not reach the host")
        evidence["stock_photographs"] = []
        for step, turret in enumerate(("turret-13", "turret-15"), start=2):
            photo = recorded.get(turret)
            by_host = host_camera and turret == "turret-15"
            if not photo:
                if by_host:
                    equipped = (await command("action", *host, "native-host-camera-equip-fixture", "--target", host_guid))[
                        "native_host_camera_equip_fixture"]
                    assert equipped["guid"] == host_guid and equipped["state"] == "stock_camera_equipped", equipped
                    await wait(run, lambda state: state.get("game", {}).get("native_host_camera_scope", {}).get("guid") == host_guid)
                    aim = (await command("action", *host, "native-host-camera-aim-fixture", "--target", turret))["native_turret_photo"]
                    assert (aim["camera"], aim["step_before"], aim["step_after"]) == (host_guid, step - 1, step - 1), aim
                    save_files = campaign_save_hashes(run.parent)
                    press = (await command("action", *host, "native-host-camera-input-fixture"))["native_host_camera_fixture"]
                    assert press["guid"] == host_guid and press["stock_wrappers"] == 2, press
                    for _ in range(40):
                        rows = (await command("events", *host, "--tail", "35"))["events"]
                        event = next((row for row in rows if row.get("sequence") == press["sequence"]
                                      and row["event"] in ("native_host_camera_shutter", "native_host_camera_shutter_rejected")), None)
                        if event:
                            assert event["event"] == "native_host_camera_shutter" and event["step"] == step, event
                            break
                        await asyncio.sleep(.2)
                    else:
                        raise RuntimeError("The stock host shutter did not reach the quest coordinator")
                    settled = await wait(run, lambda state: state.get("gameplay", {})
                                         .get("native_quests", {}).get(QUEST, {}).get("step") == step)
                    photo = settled["gameplay"]["native_quests"][QUEST]["photos"][turret]
                    assert campaign_save_hashes(run.parent) == save_files, "The stock camera saved when taking a photo"
                    evidence["host_stock_shutter"] = {"equip": equipped, "aim": aim, "press": press, "event": event}
                else:
                    capture = await command("action", *host, "native-turret-photo-capture-fixture", "--target", turret)
                    assert capture["step"] == step, capture
                    photo = capture["photos"][turret]
            assert (photo["target"], photo["camera"]) == (turret, host_guid if by_host else camera), photo
            assert photo["actor"] == ("host" if by_host else invite["character_id"]), photo
            assert photo["visibility"] >= .75, photo
            evidence["stock_photographs"].append(photo)
            repeat = await command("action", *guest, "native-turret-photo-capture", "--target", turret)
            assert repeat["effect"] == "native_turret_photo_already_captured" and repeat["photo"] == photo, repeat
        for destination, tag in ((LAKE, "DB_Lake"), (CITY, "City_Lake"), (BOROUGH, "City_Borough")):
            await cross(destination, tag)
        await enroll()
        save_files = campaign_save_hashes(run.parent)
        paid = await command("action", *guest, "native-quest-turnin", "--target", QUEST)
        last_purchase = host_purchase if host_camera else purchase
        assert (paid["step"], paid["cash"], paid["xp"], paid["wallet"]) == (-10, 700, 1, last_purchase["wallet_after"] + 700), paid
        assert len(paid["recipients"]) == 2, paid
        repeat = await command("action", *guest, "native-quest-turnin", "--target", QUEST)
        assert repeat["effect"] == "native_quest_already_settled" and repeat["wallet"] == paid["wallet"], repeat
        assert campaign_save_hashes(run.parent) == save_files, "The stock camera contract saved while settling its reward"
        evidence["stock_turn_in"] = paid
        return paid

    common = ("--headless", "--fixtures", "--native-events", "--native-guest", "--native-zones")
    if complete:
        common += ("--native-expanded-zones",)
    try:
        saved_invite = None
        if resume:
            saved_invite, = (run / "invites").glob("*.invite.json")
            invitation = json.loads(saved_invite.read_text(encoding="utf-8"))
            host_port = str(invitation["port"])
            entry = ("--port", host_port)
        else:
            entry = ("--start-zone", BOROUGH, "--port", "0")
        authority = await launch(host, "host-resume" if resume else "host", "host", *host, *entry, *common)
        ready = await wait(run, lambda state: state.get("state") == "hosting")
        port = ready["endpoint"]["port"]
        if saved_invite:
            invite = await command("action", *host, "refresh-invite", "--target", invitation["guest_id"])
            assert invite["character_id"] == invitation["character_id"], invite
        else:
            invite = await command("invite", *host, "--name", "Dead Bridge photographer", "--character", "Photographer")
        inputs = ("--native-guest-primary-input",) if camera_input else ()
        await launch(guest, "guest-resume" if resume else "guest", "join", *guest, "--headless", "--native-zones",
                     "--invite", invite["invite_file"], *inputs)
        if resume:
            assert ready["game"]["map"] == DEAD_BRIDGE, ready
            await enrolled(DEAD_BRIDGE)
        else:
            await enroll()
            refused = await command("action", *guest, "native-turret-photo-preflight", expected=False)
            assert refused["error"] == "stock_turret_photo_quest_not_offered", refused
            evidence["stock_offer_at_zero_xp"] = refused["error"]
            before = await offers()
            assert "Clt_Wire" in before and QUEST not in before, before
            evidence["board_offers_at_zero_xp"] = sorted(before)
            staged = (await command("action", *host, "native-turret-photo-xp-fixture"))["native_turret_photo"]
            assert (staged["xp_before"], staged["xp_after"]) == (0, 4), staged
            evidence["stock_xp_fixture"] = staged
            offered = (await command("action", *guest, "native-turret-photo-preflight"))["native_turret_photo"]
            assert (offered["step_before"], offered["step_after"], offered["xp_after"]) == (None, None, 4), offered
            evidence["stock_board_offer"] = offered
            visible = await offers()
            assert visible[QUEST]["cash"] == 700 and visible[QUEST]["co_op"] == "verified", visible
            evidence["board_offers_at_four_xp"] = sorted(visible)
            save_files = campaign_save_hashes(run.parent)
            accepted = await command("action", *guest, "native-quest-start", "--target", QUEST)
            assert (accepted["quest_id"], accepted["step"], accepted["actor"], accepted["photos"]) == (
                QUEST, 1, invite["character_id"], {}
            ), accepted
            assert campaign_save_hashes(run.parent) == save_files, "The stock camera contract saved while accepting"
            evidence["stock_acceptance"] = accepted
            early = await command("action", *guest, "native-quest-turnin", "--target", QUEST, expected=False)
            assert early["error"] == "native_turret_photo_objectives_incomplete", early
            evidence["incomplete_turn_in_denied"] = early["error"]
        if complete:
            paid = await finish_contract()
        await command("action", *host, "stop")
        await asyncio.wait_for(authority.wait(), 15)
        await launch(host, "host-restart", "host", *host, "--port", str(port), *common)
        await enroll()
        restored = (await command("action", *guest, "native-turret-photo-preflight"))["native_turret_photo"]
        expected = (-10, -10, 5) if complete else (1, 1, 4)
        assert (restored["step_before"], restored["step_after"], restored["xp_after"]) == expected, restored
        if complete:
            assert (restored["cash_after"], restored["rep_after"]) == (paid["wallet"], paid["commune_rep"]), restored
        evidence.update(stock_restart=restored, offer_acceptance_verified=True, full_contract_verified=complete)
    except BaseException as error:
        evidence.update(offer_acceptance_verified=False, failure=str(error))
        raise
    finally:
        for process, owner in reversed(processes):
            if process.returncode is None:
                try:
                    await command("action", *owner, "stop")
                    await asyncio.wait_for(process.wait(), 15)
                except (OSError, RuntimeError, TimeoutError):
                    if process.returncode is None:
                        process.terminate()
                    await asyncio.wait_for(process.wait(), 12)
        if original is not None:
            evidence["original_saves_verified"] = saved_hashes(vanilla_saved) == original
        report_file.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence), flush=True)
        if original is not None:
            assert evidence["original_saves_verified"], "The original single-player directory changed during the run"


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("--work", type=Path, required=True)
    options.add_argument("--vanilla-saved", type=Path)
    options.add_argument("--complete", action="store_true", help="buy the stock camera and finish both photographed targets")
    options.add_argument("--resume", help="resume a previous Dead Bridge photo smoke after its camera purchase")
    options.add_argument("--camera-input", action="store_true", help="exercise the stock guest shutter outside both target frames")
    options.add_argument("--host-camera", action="store_true", help="buy and equip a second camera and use the stock host shutter on turret 15")
    arguments = options.parse_args()
    if arguments.resume and not re.fullmatch(r"turret-photo-[0-9a-f]{8}", arguments.resume):
        options.error("--resume requires the name of an existing smoke campaign")
    asyncio.run(validate(arguments.work.resolve(), arguments.vanilla_saved,
                         complete=arguments.complete or bool(arguments.resume) or arguments.host_camera, resume=arguments.resume,
                         camera_input=arguments.camera_input, host_camera=arguments.host_camera))
