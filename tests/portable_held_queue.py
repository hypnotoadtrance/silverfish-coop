"""Prove one stock resident SMG stroke uses the process-local input bridge."""

import asyncio
import re
import uuid

from tools.silverfish import native_guest_ui, native_smg_hold, runtime
from tools.silverfish.control import submit
from tools.silverfish.travel import OBSERVATORY

from portable_guest_world_start import resident_events, wait_resident_status


KIND = "smg-standard"
ROUNDS = 7
FIXTURE = "native_guest_held_fire_fixture"
PUBLICATION = "native_guest_primary_publication"
TERMINAL = "native_guest_smg_hold_accepted"
GUEST_EDGE = "native_guest_held_fire_input"


async def capture_prior_selection(host, pawn, character, old_guid, new_guid):
    nonce = uuid.uuid4().hex
    path = host.bridge.directory / f"native-guest-ui-{nonce}.tsv"
    try:
        await host.bridge.command(native_guest_ui.ACTION, pawn, nonce, timeout=4)
        contents = native_guest_ui.validate(path.read_bytes(), OBSERVATORY, character)
    finally:
        path.unlink(missing_ok=True)
    rows = [line.split("\t") for line in contents.splitlines()]
    assert rows[1][0] == "inventory-layout", rows[1]
    source_slot = int(rows[1][3])
    slots = {"".join(f"{int(word) & 0xffffffff:08X}" for word in row[8:12]): (int(row[2]) - 1, int(row[4]))
             for row in rows if row[:2] == ["item", "equipment"]}
    assert slots[old_guid][0] == source_slot and slots[new_guid][0] != source_slot and slots[new_guid][1] == ROUNDS, slots
    return contents, source_slot, slots[new_guid][0]


async def replay_prior_selection(guest, character, pawn, guid, contents, source_slot, target_slot):
    nonce = uuid.uuid4().hex
    path = guest.bridge.directory / f"native-guest-ui-{nonce}.tsv"
    try:
        runtime.write_text(path, contents, newline="")
        frame = await guest.bridge.command("portable-stale-quick-slot-reply", character, nonce, pawn, guid,
                                           str(target_slot), timeout=4)
    finally:
        path.unlink(missing_ok=True)
    receipt = frame["ack"]["message"]
    assert receipt == f"preserved selected slot {target_slot} with GUID {guid} and {ROUNDS} rounds", receipt
    return {"captured_slot": source_slot, "selected_slot": target_slot, "held_guid": guid, "receipt": receipt}


async def verify_stock_held_queue(host, guest, pawn, probe, *, stale_quick_slot=False, bizon=False):
    kind, load = ("bizon-01", 64) if bizon else (KIND, ROUNDS)
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_frame = await wait_resident_status(host_run, lambda status:
        status.get("local_bridge") == "memory" and status.get("game", {}).get("map") == OBSERVATORY)
    character = host_frame["gameplay"]["players"]["guest"]["character_id"]
    assert host_frame["gameplay"]["native_pawns"]["guest"] == pawn, host_frame["gameplay"]["native_pawns"]
    guest_frame = await wait_resident_status(guest_run, lambda status:
        status.get("local_bridge") == "memory" and status.get("gameplay_fresh")
        and status.get("game", {}).get("native_guest_primary_scope", {}).get("map") == OBSERVATORY)
    previous = guest_frame["game"]["native_guest_primary_scope"]

    async def control(run, action, **arguments):
        result = await submit(run, action, **arguments)
        assert result["ok"], result
        return result

    async def scoped(guid, kind):
        return await wait_resident_status(guest_run, lambda status:
            status.get("state") == "connected" and status.get("gameplay_fresh") and status.get("local_bridge") == "memory"
            and (scope := status.get("game", {}).get("native_guest_primary_scope", {})).get("guid") == guid
            and (scope.get("map"), scope.get("pawn"), scope.get("kind")) == (OBSERVATORY, previous["pawn"], kind), timeout=10)

    async def stock_select(guid):
        last = None
        try:
            async with asyncio.timeout(8):
                while True:
                    last = await probe(guest.bridge, "portable-held-queue", f"select\t{previous['pawn']}\t{guid}")
                    if last.startswith("selected;"):
                        return last
                    assert not last.startswith("conflict;"), (guid, last)
                    await asyncio.sleep(.04)
        except TimeoutError as error:
            raise AssertionError(("The exact projected guest weapon did not equip locally", guid, last)) from error

    async def accepted_selection(guid):
        async with asyncio.timeout(8):
            while True:
                for event in await resident_events(guest_run, guest_frame):
                    weapon = event.get("native_guest_weapon", {})
                    if event["event"] == "native_guest_equipment_selected" and weapon.get("guid") == guid:
                        return weapon
                await asyncio.sleep(.04)

    async def matching_events():
        async with asyncio.timeout(8):
            while True:
                guest_rows, host_rows = await asyncio.gather(resident_events(guest_run, guest_frame),
                                                             resident_events(host_run, host_frame))
                edges = [row for row in guest_rows if row.get("stroke") == pressed["stroke"]
                         and row["event"] in (GUEST_EDGE, GUEST_EDGE + "_rejected")]
                terminals = [row for row in host_rows if row["event"] == TERMINAL and row.get("guid") == guid]
                assert all(row["event"] == GUEST_EDGE for row in edges), edges
                if terminals and any(row.get("edge") == "release" for row in edges):
                    assert [row["edge"] for row in edges] == ["press", "release"] and len(terminals) == 1, (edges, terminals)
                    return edges, terminals[0]
                await asyncio.sleep(.04)

    guid = await probe(host.bridge, "portable-held-queue", f"seed\t{pawn}\t" + ("bizon" if bizon else "standard"))
    assert re.fullmatch(r"[0-9A-F]{32}", guid), guid
    prior = await capture_prior_selection(host, pawn, character, previous["guid"], guid) if stale_quick_slot else None

    try:
        selected = await stock_select(guid)
        stale_reply = None
        if prior:
            stale_reply = await replay_prior_selection(guest, character, previous["pawn"], guid, *prior)
        equipped = await accepted_selection(guid)
        assert (equipped["guid"], equipped["pawn"], equipped["actor_ammo"], equipped["stored_ammo"]) == (guid, pawn, load, load), equipped
        await scoped(guid, kind)
        selector = f"\t{previous['pawn']}\t{guid}"
        semi = await probe(guest.bridge, "portable-held-queue", "semi" + selector)
        if semi == "semi":
            try:
                denied = await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press")
                if denied["ok"]:
                    await submit(guest_run, native_smg_hold.LOCAL_FIXTURE, target="release")
                assert denied["ok"] is False and "native_guest_held_fire_fixture_requires_automatic_mode" in denied["error"], denied
            finally:
                mode = await probe(guest.bridge, "portable-held-queue", "auto" + selector)
                assert mode == "auto", mode
        else:
            assert bizon and semi == "fixed-automatic", semi
            mode = semi
        pressed = (await control(guest_run, native_smg_hold.LOCAL_FIXTURE, target="press", milliseconds=400))[FIXTURE]
        assert (pressed["map"], pressed["pawn"], pressed["guid"], pressed["edge"], pressed["sequence"]) == (
            OBSERVATORY, previous["pawn"], guid, "press", pressed["stroke"]), pressed

        released_frame = await wait_resident_status(guest_run, lambda status:
            (release := status.get("game", {}).get(FIXTURE, {})).get("stroke") == pressed["stroke"]
            and release.get("edge") == "release", timeout=6)
        released = released_frame["game"][FIXTURE]
        assert released["sequence"] > pressed["sequence"], released
        expected = {field: released[field] for field in ("map", "pawn", "guid", "sequence")}
        expected["lane"] = "memory"
        assert released_frame["game"].get(PUBLICATION, {}).get("held") == expected, released_frame["game"].get(PUBLICATION)
        edges, terminal = await matching_events()
        started = edges[0][native_smg_hold.KEY]
        assert (started["map"], started["pawn"], started["guid"], started["serial"]) == (
            OBSERVATORY, pawn, guid, terminal["serial"]), started
        assert (terminal["map"], terminal["pawn"], terminal["character_id"], terminal["status"], terminal["before"]) == (
            OBSERVATORY, pawn, character, "released", load), terminal
        assert 0 < terminal["rounds"] == terminal["projectiles"] == load - terminal["after"] < load, terminal
        assert terminal["revision"] is None, terminal
        held = (await control(guest_run, "native-guest-weapon-inspect", target=guid))["native_guest_weapon"]
        assert held["actor_ammo"] == held["stored_ammo"] == terminal["after"], held
        for seat, names in ((guest, ("native-guest-primary-events", "native-guest-held-fire-events")),
                            (host, ("held-fire-command.tsv", "held-fire-reply.tsv"))):
            for filename in names:
                assert not (seat.bridge.directory / filename).exists(), filename
        # The press fixture returns only after publishing its exact sequence. The accepted host edge and
        # absent mailbox files prove that press used the same in-process bridge as the release receipt.
        proof = {"press": pressed, "release": released, "release_publication": expected,
                 "guest_selection": selected, "host_selection": equipped, "guest_edges": edges,
                 "fire_mode": {"semi_hold_blocked": semi == "semi", "restored": mode}, "host_terminal": terminal}
        if stale_reply:
            proof["stale_quick_slot_reply"] = stale_reply
        return {("stock_bizon_hold" if bizon else "stock_held_queue"): proof}
    finally:
        await stock_select(previous["guid"])
        await scoped(previous["guid"], previous["kind"])
