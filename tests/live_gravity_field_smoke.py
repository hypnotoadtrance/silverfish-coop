"""Verify scoped stock Crossroads anomaly families with muted peers."""

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid

from live_ai_smoke import status, wait
from live_campaign_npc_smoke import MAP
from live_guest_held_fire_smoke import events


@contextmanager
def probe_mod(work):
    folder = work / "stage/SilverFish/Binaries/Win64/ue4ss/Mods/SilverfishGravityProbe"
    script, enabled = folder / "Scripts/main.lua", folder / "enabled.txt"
    script.parent.mkdir(parents=True)
    try:
        script.write_bytes((Path(__file__).with_name("fixtures") / "gravity_field_probe.lua").read_bytes())
        enabled.touch()
        yield
    finally:
        script.unlink(missing_ok=True)
        enabled.unlink(missing_ok=True)
        script.parent.rmdir()
        folder.rmdir()


def family_actors(view, family):
    return [actor for actor in view.get("game", {}).get("native_anomaly_positions", []) if actor["kind"] == family]


def row(raw):
    point = []
    for item in filter(None, raw.split(";")):
        x, y, z, role, collision, tick, fx, *attachment = item.split("|")
        observation = {"location": [float(x), float(y), float(z)], "role": int(role),
                       "collision": collision == "true", "tick": tick == "true", "idle_effect": fx == "true"}
        if attachment:
            parent, yaw, asset, replicates = attachment
            observation.update(attachment=parent == "true", yaw=float(yaw), asset=asset,
                               replicates=replicates == "true")
        point.append(observation)
    return point


def blocked_triggers(view):
    return view.get("game", {}).get("native_anomaly_field", {}).get("blocked_triggers", 0)


async def verify_pyrogeist(host, guest, host_box, guest_box, probe):
    before_body = blocked_triggers(status(guest))
    assert await probe(guest_box, "guestguard") == "0"
    body_guard = await wait(guest, lambda view: blocked_triggers(view) >= before_body + 4, timeout=5)
    await asyncio.sleep(1.6)
    assert not await probe(guest_box, "jets") and not (guest_box / "pyrogeist-damage").exists()
    before = float(await probe(host_box, "enter"))
    async with asyncio.timeout(12):
        while not (host_jets := row(await probe(host_box, "jets"))) or not any(jet["replicates"] for jet in host_jets):
            await asyncio.sleep(.08)
        assert all(jet["role"] == 3 and jet["collision"] and jet["attachment"]
                   for jet in host_jets), host_jets
        while not (guest_jets := row(await probe(guest_box, "jets"))) or not any(
                jet["attachment"] and jet["idle_effect"] and not jet["tick"] for jet in guest_jets):
            await asyncio.sleep(.08)
    host_jets = row(await probe(host_box, "jets"))
    assert host_jets and all(jet["replicates"] for jet in host_jets), host_jets
    for jet in guest_jets:
        assert (jet["role"] == 1 and not jet["collision"] and not jet["tick"]
                and jet["idle_effect"] and jet["attachment"]), jet
        assert any(math.dist(jet["location"], source["location"]) < 150 and
                   abs((jet["yaw"] - source["yaw"] + 180) % 360 - 180) < 4 for source in host_jets), jet
    before_jet = blocked_triggers(status(guest))
    attempted_hits = int(await probe(guest_box, "jetguard"))
    jet_guard = await wait(guest, lambda view: blocked_triggers(view) >= before_jet + attempted_hits, timeout=5)
    async with asyncio.timeout(7):
        while await probe(host_box, "jets") or await probe(guest_box, "jets"):
            await asyncio.sleep(.1)
    assert not (guest_box / "pyrogeist-damage").exists()
    hits = [line.split("|") for line in (host_box / "pyrogeist-damage").read_text().splitlines()]
    assert hits and all(seat == "guest" and role == "3" and 30 <= float(fire) <= 60
                        for seat, _, role, fire, _ in hits), hits
    after = await wait(host, lambda view: view.get("game", {}).get("pawns", {}).get("guest", {}).get("health", before) < before)
    remote = after["game"]["pawns"]["guest"]["health"]
    host_health = after["game"]["pawns"]["local"]["health"]
    def reports_damage(view):
        players = view.get("gameplay", {}).get("players", {})
        return (players.get("guest", {}).get("health") == remote and players.get("host", {}).get("health") == host_health)
    host_report, guest_report = await asyncio.gather(wait(host, reports_damage, timeout=6),
                                                     wait(guest, reports_damage, timeout=6))
    return {"host_jets": host_jets, "guest_jets": guest_jets, "retired_on_both": True,
            "guest_native_vetoes_after_body_probe": blocked_triggers(body_guard) - before_body,
            "guest_native_vetoes_after_hit_probe": blocked_triggers(jet_guard) - before_jet,
            "stock_authority_damage": hits, "guest_health_before": before, "guest_health_on_host": remote,
            "host_health": host_health,
            "shared_health_report": {"host": host_report["gameplay"]["players"]["guest"]["health"],
                                     "guest": guest_report["gameplay"]["players"]["guest"]["health"]}}


def shadow_rows(raw):
    result = []
    for item in filter(None, raw.split(";")):
        enabled, strength, scale, distance, radius, blend = item.split("|")
        result.append((enabled == "true", *map(float, (strength, scale, distance, radius, blend))))
    return result


def angler_rows(raw):
    result = []
    for item in filter(None, raw.split(";")):
        *values, enabled, alpha, yaw, pitch = item.split("|")
        coordinates = list(map(float, values))
        result.append({"location": coordinates[:3], "colors": coordinates[3:],
                       "lure": enabled == "true", "alpha": float(alpha), "aim": [float(yaw), float(pitch)]})
    return result


async def verify_observer(guest, host_box, guest_box, probe):
    vetoes = blocked_triggers(status(guest))
    assert await probe(guest_box, "guestguard") == "0"
    guarded = await wait(guest, lambda view: blocked_triggers(view) >= vetoes + 5, timeout=5)

    async def visible_pair(is_expected):
        async with asyncio.timeout(8):
            while True:
                first, second = await asyncio.gather(
                    probe(host_box, "observervisual"), probe(guest_box, "observervisual"))
                if first and second:
                    authority, host_parts = map(float, first.split("|"))
                    replica, guest_parts = map(float, second.split("|"))
                    if host_parts == guest_parts == 3 and is_expected(authority) and is_expected(replica):
                        return {"host_opacity": authority, "guest_opacity": replica,
                                "host_meshes": int(host_parts), "guest_meshes": int(guest_parts)}
                await asyncio.sleep(.1)

    async def check_pose(pose):
        facing = await probe(host_box, "observerpose", pose)
        await asyncio.sleep(.6)
        stock_target = await probe(host_box, "observercheck")
        current = list(map(float, (await probe(host_box, "shadowstate")).split("|")))
        return facing, stock_target, current

    initial = list(map(float, (await probe(host_box, "shadowstate")).split("|")))
    visible = await visible_pair(lambda opacity: opacity >= .95)
    far = await check_pose("far")
    away = await check_pose("away")
    assert far[2] == initial and away[2] == initial, (initial, far, away)
    face = await check_pose("guest")
    assert face[2][:2] == initial[:2] and face[2][2] > initial[2] and face[2][3] < initial[3], (initial, face)
    attack_log = host_box / "observer-gaze"
    guest_hits = attack_log.read_text().splitlines()
    assert guest_hits and all(row.startswith("guest|") for row in guest_hits), guest_hits
    await probe(host_box, "observerpose", "far")
    await asyncio.sleep(1)
    faded = await visible_pair(lambda opacity: opacity <= .06)
    retreated = list(map(float, (await probe(host_box, "shadowstate")).split("|")))
    await asyncio.sleep(8.2)
    host_looks = await check_pose("host")
    assert host_looks[2][0] > retreated[0] and host_looks[2][1] < retreated[1], (retreated, host_looks)
    assert host_looks[2][2:] == retreated[2:] and far[1] == away[1] == face[1] == host_looks[1], (retreated, host_looks)
    return {"guest_native_vetoes": blocked_triggers(guarded) - vetoes, "initial": initial,
            "out_of_range": far, "facing_away": away, "guest_gaze": face, "retreated": retreated, "host_gaze": host_looks,
            "before_gaze": visible, "after_gaze": faded,
            "stock_psy_attack_recipients": attack_log.read_text().splitlines()}


async def verify_observer_offering(host, guest, host_box, guest_box, probe, approach, character):
    await probe(host_box, "observerpose", "guest" if approach in ("flight", "loot") else "far")
    source = await probe(host_box, "observeroffering", approach)
    assert source.startswith("Item_Jewely_C ")
    async with asyncio.timeout(8):
        while await probe(guest_box, "observerrewards") != "1|":
            await asyncio.sleep(.15)
    assert await probe(host_box, "observerrewards") == "1|"
    if approach == "input":
        launch = await stock_observer_throw(host, guest, host_box, guest_box, probe, source)
    elif approach != "contact":
        launch = await probe(host_box, "observerlaunch")
        assert launch.startswith("true|"), launch
    else:
        launch = await probe(host_box, "observerimpact")
        assert launch == "entered stock capsule"
    if approach in ("loot", "input"):
        return await verify_observer_reward_pickup(host, guest, host_box, guest_box, probe, character, source, launch)
    host_result = guest_result = ""
    try:
        async with asyncio.timeout(10):
            while True:
                host_result, guest_result = await asyncio.gather(
                    probe(host_box, "observerrewards"), probe(guest_box, "observerrewards"))
                head, _, tail = host_result.partition("|")
                if head == "0" and tail.endswith(",3,true") and ";" not in tail:
                    tier = tail.split(",", 1)[0]
                    if guest_result == f"0|{tier},1,true":
                        await asyncio.sleep(.6)
                        assert await probe(host_box, "observerabsorptions") == "1"
                        assert await probe(host_box, "observerrewards") == host_result
                        assert await probe(guest_box, "observerrewards") == guest_result
                        return {"offering": source, "launch": launch, "reward": tier, "host": host_result, "guest": guest_result,
                                "stock_reward_count": 1}
                await asyncio.sleep(.15)
    except TimeoutError as error:
        extra = await probe(host_box, "observerofferingprobe")
        raise RuntimeError(f"Stock offering did not settle on both peers. Host {host_result!r}. Guest {guest_result!r}. Stock {extra}.") from error


async def stock_observer_throw(host, guest, host_box, guest_box, probe, source):
    from tools.silverfish import native_prop_carry
    from tools.silverfish.control import submit

    async def fixture(run, target):
        reply = await submit(run, native_prop_carry.FIXTURE, target=target)
        if not reply.get("ok"):
            raise RuntimeError(f"{target}: {reply.get('error')}")
        return reply

    def carries():
        return [entry for entry in events(host, "native_prop_carry_action") if entry.get("actor") == source]

    await fixture(host, "select@" + source.partition(" ")[2])
    coordinates = await probe(host_box, "observerofferpoint")
    location = list(map(float, coordinates.split("|")))
    await wait(guest, lambda view: math.dist(view["game"]["pawns"]["local"]["location"], location) < 175, timeout=7)
    count, name = (await probe(guest_box, "observerofferidentity", coordinates.replace("|", ","))).split("|")
    await probe(guest_box, "observerlootcamera", "position:30")
    try:
        await fixture(guest, f"pickup@/Game/Classes/Items/Item_Jewely.Item_Jewely_C|{count}|{name}")
        await wait(host, lambda _: any(entry["edge"] == "grab" for entry in carries()), timeout=8)
        assert await probe(host_box, "observerthrowaim") == "ready"
        assert await probe(guest_box, "observerthrowaim") == "aimed"
        await fixture(guest, "throw")
        await wait(host, lambda _: any(entry["edge"] == "throw" for entry in carries()), timeout=8)
    finally:
        assert await probe(guest_box, "observerlootcamera", "reset") == "restored"
    return carries()


async def verify_observer_reward_pickup(host, guest, host_box, guest_box, probe, character, source, launch):
    from tools.silverfish import native_stock_enemy_drops, native_world_loot
    from tools.silverfish.control import submit

    def banked():
        return [entry for entry in events(host, "native_stock_enemy_loot_banked") if ".Observers_BP_C_" in entry["source"]]

    async def action(run, command, target=None):
        reply = await submit(run, command, target=target)
        if not reply.get("ok"):
            raise RuntimeError(f"{command}: {reply.get('error')}")
        return reply

    try:
        async with asyncio.timeout(15):
            while not (rewards := banked()):
                await asyncio.sleep(.15)
    except TimeoutError as error:
        extra = await probe(host_box, "observerofferingprobe")
        raise RuntimeError(f"The stock Observer reward was not banked. Stock {extra}.") from error
    reward, = rewards
    guid, kind = reward["guid"], reward["class_path"]
    assert "/Artifacts/Art_Tesseract_" in kind, reward
    cue_kind = "observer" + kind.rsplit(".", 1)[0].rsplit("_", 1)[1].lower()
    await wait(guest, lambda _: events(guest, "native_observer_absorb_played"), timeout=5)
    cue, = events(guest, "native_observer_absorb_played")
    assert cue["cue"] == cue_kind and cue["particles"], cue
    identity = native_world_loot.stock_enemy_identity(guid)

    def projected(view):
        return view.get("game", {}).get(native_world_loot.KEY, {}).get(identity, {})

    for run in (host, guest):
        await wait(run, lambda view: projected(view).get("class_path") == kind, timeout=12)
    await action(host, native_world_loot.APPROACH, f"guest@{identity}")
    await wait(guest, lambda view: math.dist(
        view["game"]["pawns"]["local"]["location"], reward["transform"]["position"]) < 180, timeout=7)
    eye = list(map(float, (await probe(guest_box, "observerlootcamera", "position")).split("|")))
    try:
        assert math.dist(eye, reward["transform"]["position"]) < 210
        press = (await action(guest, native_world_loot.FIXTURE, identity))[native_world_loot.KEY][identity]
    finally:
        assert await probe(guest_box, "observerlootcamera", "reset") == "restored"
    assert press["sequence"] > 0

    def claim():
        return next((entry for entry in events(host, "native_stock_enemy_loot_claimed") if entry.get("guid") == guid), None)

    await wait(host, lambda _: claim(), timeout=12)
    assert claim()["claimed_by"] == character
    for run in (host, guest):
        await wait(run, lambda view: not projected(view), timeout=12)
    rival = await submit(host, native_stock_enemy_drops.CLAIM, target=guid)
    assert rival.get("error") == "stock_enemy_loot_already_claimed", rival
    inventory = (await action(guest, "native-inventory-list"))["native_inventory"]["items"]
    item, = [item for item in inventory if item["guid"] == guid and item["class_path"] == kind]
    assert await probe(host_box, "observerabsorptions") == "1"
    assert len(banked()) == 1 and events(guest, "native_observer_absorb_played") == [cue]
    assert (await probe(guest_box, "observerrewards")).startswith("0|")
    return {"offering": source, "launch": launch, "stock_reward_count": 1, "reward": reward, "claim": claim(), "absorb_cue": cue,
            "guest_inventory_item": item, "competing_claim": rival["error"]}


async def verify_angler(guest, host_box, guest_box, probe):
    parking = list(map(float, (await probe(host_box, "anglerholdguest")).split("|")))
    await wait(guest, lambda view: math.dist(
        view.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), parking) < 70)
    vetoes = blocked_triggers(status(guest))
    assert await probe(guest_box, "guestguard") == "0"
    guarded = await wait(guest, lambda view: blocked_triggers(view) >= vetoes + 5, timeout=5)
    parked = angler_rows(await probe(guest_box, "anglervisual"))
    assert parked and not any(row["lure"] for row in parked), parked
    aim = parked[0]["aim"]
    await probe(host_box, "anglerpresent", await probe(guest_box, "anglerviewpoint"))
    expected = [.8, .2, .45, .4, .6, .12]
    async with asyncio.timeout(12):
        while True:
            host_rows = angler_rows(await probe(host_box, "anglervisual"))
            guest_rows = angler_rows(await probe(guest_box, "anglervisual"))
            host_lamp = next((row for row in host_rows if math.dist(row["colors"], expected) < .035), None)
            replica = next((row for row in guest_rows if host_lamp and math.dist(row["location"], host_lamp["location"]) < 4), None)
            if replica and replica["lure"] and replica["alpha"] > .025 and math.dist(replica["colors"], host_lamp["colors"]) < .03:
                break
            await asyncio.sleep(.12)
    assert math.dist(aim, replica["aim"]) > .2, (aim, replica)
    await probe(host_box, "viewrestore")
    async with asyncio.timeout(6):
        while any(row["lure"] for row in angler_rows(await probe(guest_box, "anglervisual"))):
            await asyncio.sleep(.1)
    return {"host_lamp": host_lamp, "guest_lamp": replica, "guest_aim_before": aim, "lure_cleared": True,
            "guest_native_vetoes": blocked_triggers(guarded) - vetoes}


async def verify_shadow(guest, host_box, guest_box, probe):
    vetoes = blocked_triggers(status(guest))
    assert await probe(guest_box, "guestguard") == "0"
    await wait(guest, lambda view: blocked_triggers(view) >= vetoes + 4, timeout=5)
    parked = shadow_rows(await probe(guest_box, "shadowvisual"))
    assert parked and all(not enabled for enabled, *_ in parked), parked
    await probe(host_box, "viewinside", await probe(guest_box, "viewpoint"))
    async with asyncio.timeout(8):
        while True:
            nearby = [item for item in shadow_rows(await probe(guest_box, "shadowvisual")) if item[0]]
            if nearby and any(scale > 1 for _, _, scale, *_ in nearby):
                break
            await asyncio.sleep(.1)
    assert len(nearby) == 1
    _, strength, _, distance, radius, blend = nearby[0]
    assert abs(strength - .97 * max(0, min(1, (radius - distance) / blend))) < .05, nearby
    await probe(host_box, "viewrestore")
    async with asyncio.timeout(6):
        while any(enabled for enabled, *_ in shadow_rows(await probe(guest_box, "shadowvisual"))):
            await asyncio.sleep(.1)
    initial = (await probe(host_box, "shadowstate")).split("|")
    await probe(host_box, "shadowinside")
    for _ in range(10):
        later = (await probe(host_box, "shadowstate")).split("|")
        if float(later[2]) > float(initial[2]):
            break
        await probe(host_box, "shadowhit")
        await asyncio.sleep(.22)
    else:
        raise RuntimeError(f"No authoritative ShadowSphere fatigue debit. Initial {initial}, later {later}")
    assert list(map(float, later[:2])) == list(map(float, initial[:2])), (initial, later)
    assert float(later[3]) < float(initial[3]), (initial, later)
    async with asyncio.timeout(8):
        while not any(item.get("sound_mask") == 16 for item in events(guest, "native_guest_hurt_played")):
            await asyncio.sleep(.1)
    await probe(host_box, "shadowleave")
    return {"parked_guest_effects": parked, "active_guest_effect": nearby[0],
            "host_fatigue_health_guest_fatigue_health_before": initial,
            "host_fatigue_health_guest_fatigue_health_after": later,
            "guest_stock_psychic_feedback": True, "guest_darkness_cleared": True}


async def verify(work, family, observer_offering=None):
    profile = f"{family}-proof-" + uuid.uuid4().hex[:8]
    host, guest = [work / role / profile / "run" for role in ("campaigns", "clients")]
    host.mkdir(parents=True)
    cli = (sys.executable, "-B", "-u", "-m", "tools.silverfish", "--work", str(work))
    flags = subprocess.CREATE_NO_WINDOW
    processes = []

    async def start(run, *args):
        env = {**os.environ, "SILVERFISH_COOP_RENDER_CAPTURE": "guest"}
        if family == "shadow-sphere":
            env["SILVERFISH_COOP_SHADOW_SPHERE_FIXTURE"] = "1"
        if family == "angler-lamp" and args[0] == "join":
            env["SILVERFISH_COOP_ANGLER_LAMP_FIXTURE"] = "1"
        if family == "observer":
            if observer_offering in ("contact", "flight", "input"):
                env["SILVERFISH_COOP_OBSERVER_FIXTURE"] = "1"
            if observer_offering in ("contact", "flight"):
                env["SILVERFISH_COOP_OBSERVER_REWARD_REPLICATION_FIXTURE"] = "1"
            if observer_offering == "input":
                env["SILVERFISH_COOP_OBSERVER_THROW_FIXTURE"] = "1"
        with (host / f"{args[0]}.log").open("wb") as log:
            child = await asyncio.create_subprocess_exec(*cli, *args, stdout=log, stderr=log,
                creationflags=flags, env=env)
        processes.append((child, run))

    async def command(*args):
        child = await asyncio.create_subprocess_exec(*cli, *args, stdout=asyncio.subprocess.PIPE,
                                                      stderr=asyncio.subprocess.PIPE, creationflags=flags)
        stdout, stderr = await asyncio.wait_for(child.communicate(), 30)
        if child.returncode:
            raise RuntimeError(stderr.decode(errors="replace"))
        return json.loads(stdout)

    async def probe(mailbox, operation, argument=""):
        reply = mailbox / "gravity-reply"
        reply.unlink(missing_ok=True)
        (mailbox / "gravity-probe").write_text(f"{operation}|{uuid.uuid4().hex}|{argument}\n", encoding="ascii", newline="")
        async with asyncio.timeout(6):
            while True:
                try:
                    code, message = reply.read_text().rstrip().split("|", 1)
                except (OSError, ValueError):
                    await asyncio.sleep(.05)
                    continue
                if code != "ok":
                    raise RuntimeError(f"{operation}: {message}")
                return message

    with probe_mod(work):
        try:
            await start(host, "host", "--campaign", profile, "--port", "0", "--headless", "--fixtures",
                        "--native-zones", "--native-events", "--native-guest", "--start-zone", MAP)
            await wait(host, lambda view: view.get("state") == "hosting" and view.get("game", {}).get("map") == MAP)
            host_box, = (host / "game-bridge").iterdir()
            for attempt in range(6):
                director = await probe(host_box, "roll")
                try:
                    await wait(host, lambda view: family_actors(view, family), timeout=3)
                    break
                except RuntimeError:
                    if attempt == 5:
                        raise
            anchor = list(map(float, (await probe(host_box, "prepare")).split("|")))
            spawn_limit = 3100 if family == "observer" else 1500
            await wait(host, lambda view: math.dist(
                view.get("game", {}).get("pawns", {}).get("local", {}).get("location", [0, 0, 0]), anchor) < spawn_limit)
            invite = await command("invite", "--campaign", profile, "--name", "Anomaly observer", "--character", "Observer")
            await start(guest, "join", "--profile", profile, "--invite", invite["invite_file"], "--headless", "--native-zones")
            guest_state = await wait(guest, lambda view: view.get("state") == "connected" and view.get("gameplay_fresh"))
            guest_box, = (guest / "game-bridge").iterdir()
            old = guest_state["game"]["native_anomaly_field"]["suppressed"]
            attempted = await probe(guest_box, "roll")
            before, after = map(int, attempted.split("|"))
            assert before == after, attempted
            await wait(guest, lambda view: view.get("game", {}).get("native_anomaly_field", {}).get("suppressed", 0) > old)
            tolerance = 150 if family != "gravity" else 3
            async with asyncio.timeout(12):
                while True:
                    host_view, guest_view = await asyncio.gather(probe(host_box, "view"), probe(guest_box, "view"))
                    host_wells, guest_wells = row(host_view), row(guest_view)
                    matching = [well for well in guest_wells if any(
                        math.dist(well["location"], source["location"]) < tolerance for source in host_wells)]
                    if family in {"pyrogeist", "shadow-sphere"}:
                        matching = [well for well in matching if math.dist(well["location"], anchor) < 160]
                    if matching:
                        guest_wells = matching
                        break
                    await asyncio.sleep(.15)
            assert host_wells and guest_wells and all(well["role"] == 3 and well["tick"] and well["collision"]
                                                       for well in host_wells)
            for well in guest_wells:
                assert (well["role"] == 1 and not well["tick"] and not well["collision"]
                        and well["idle_effect"] == (family in {"gravity", "pyrogeist"})), well
            common = {"stock_director_counts": director, "guest_duplicate_counts": attempted,
                      "host_actors": host_wells, "guest_actors": guest_wells}
            if family == "pyrogeist":
                common.update(await verify_pyrogeist(host, guest, host_box, guest_box, probe))
                evidence = host / "live-pyrogeist-field.json"
                evidence.write_text(json.dumps(common, indent=2), encoding="utf-8")
                print(evidence, flush=True)
                return
            if family == "shadow-sphere":
                common.update(await verify_shadow(guest, host_box, guest_box, probe))
                evidence = host / "live-shadow-sphere-field.json"
                evidence.write_text(json.dumps(common, indent=2), encoding="utf-8")
                print(evidence, flush=True)
                return
            if family == "angler-lamp":
                common.update(await verify_angler(guest, host_box, guest_box, probe))
                evidence = host / "live-angler-lamp-field.json"
                evidence.write_text(json.dumps(common, indent=2), encoding="utf-8")
                print(evidence, flush=True)
                return
            if family == "observer":
                if observer_offering:
                    if observer_offering == "loot":
                        common["gaze"] = await verify_observer(guest, host_box, guest_box, probe)
                    common.update(await verify_observer_offering(
                        host, guest, host_box, guest_box, probe, observer_offering, invite["character_id"]))
                    evidence = host / "live-observer-offering.json"
                else:
                    common.update(await verify_observer(guest, host_box, guest_box, probe))
                    evidence = host / "live-observer-gaze.json"
                evidence.write_text(json.dumps(common, indent=2), encoding="utf-8")
                print(evidence, flush=True)
                return
            local_effects = await probe(guest_box, "guestguard")
            assert local_effects == "0|0"
            launch_origin = await probe(host_box, "lift")
            async with asyncio.timeout(6):
                while not (host_box / "gravity-launch").exists():
                    await asyncio.sleep(.1)
            pawn, force = (host_box / "gravity-launch").read_text().strip().split("|")
            host_state = await wait(host, lambda view: view.get("game", {}).get("pawns", {}).get("guest", {}).get("name") == pawn)
            assert float(force) > 0 and not (host_box / "gravity-host-launch").exists()
            async with asyncio.timeout(6):
                while True:
                    authority = list(map(float, (await probe(host_box, "position")).split("|")))
                    recipient = list(map(float, (await probe(guest_box, "position")).split("|")))
                    if math.dist(authority, recipient) < 75:
                        break
            result = {**common, "guest_local_effect_arrays": local_effects,
                      "guest_host_pawn": pawn, "host_launch_velocity_z": float(force),
                      "guest_position_on_host": authority, "guest_local_position": recipient,
                      "host_health": host_state["game"]["pawns"]["local"]["health"],
                      "launch_origin": list(map(float, launch_origin.split("|")))}
            evidence = host / "live-gravity-field.json"
            evidence.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(evidence, flush=True)
        finally:
            for child, run in reversed(processes):
                if run.is_dir():
                    (run / "stop").touch()
                try:
                    await asyncio.wait_for(child.wait(), 8)
                except TimeoutError:
                    child.kill()
                    await child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--family", choices=("gravity", "pyrogeist", "shadow-sphere", "angler-lamp", "observer"), default="gravity")
    parser.add_argument("--observer-offering", nargs="?", const="contact", choices=("contact", "flight", "loot", "input"))
    args = parser.parse_args()
    if args.observer_offering and args.family != "observer":
        parser.error("--observer-offering requires --family observer")
    asyncio.run(verify(args.work.resolve(), args.family, args.observer_offering))
