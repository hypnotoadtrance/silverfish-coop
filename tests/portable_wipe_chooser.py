"""Exercise stock wipe confirmation order and equipped weapon state after respawn."""

import asyncio

from portable_guest_world_start import wait_resident_status
from tools.silverfish import native_inventory
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid


async def verify_stock_wipe_chooser(host, guest, pawn, probe, *, first):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("character_id") and frame.get("gameplay_fresh"))
    character = guest_frame["character_id"]

    async def command(seat, action):
        return await probe(seat.bridge, "portable-wipe-chooser", action)

    bandage = await command(host, "configure")
    assert valid_guid(bandage), bandage
    await wait_resident_status(host_run, lambda frame: (
        (rules := frame.get("game", {}).get("native_rules", {})).get("extraction_mode") == 1
        and rules.get("items_kept_on_death") == 1 and rules.get("drop_bone_pile") == 1))

    seeded = await host.bridge.command("fixture-stock-take-ammo-seed", pawn, "revolver-9mm", "1")
    weapon = seeded["ack"]["message"]
    assert valid_guid(weapon), seeded["ack"]
    stored = await submit(guest_run, native_inventory.STORE_EQUIPMENT, target=weapon)
    assert stored["ok"], stored
    slot = stored[native_inventory.STORE_KEY]["slot"]

    async def held(seat):
        return (await command(seat, "held")).split(",")

    async with asyncio.timeout(8):
        while True:
            equipped = await guest.bridge.command("fixture-stock-take-ammo-loadout", str(slot), weapon, "equip")
            if equipped["ack"]["message"] == "1,1":
                break
            await asyncio.sleep(.1)
        while (before := await held(host))[1] != weapon:
            await asyncio.sleep(.1)
    assert before[0] != "-" and before[2] == "lowered" and await held(guest) == before
    assert await command(guest, "aim") == "aiming"

    for seat, blocker in ((host, "pause"), (guest, "journal")):
        assert await command(seat, blocker) == blocker
    guest_hit = await host.bridge.command("native-guest-damage", "guest")
    assert guest_hit["ack"]["message"] == "stock direct damage delivered to the enrolled pawn", guest_hit
    await wait_resident_status(guest_run, lambda frame: (
        (effects := frame.get("game", {}).get("player_effects", {}).get("local", {})).get("state") == "downed"
        and effects.get("crouched")))
    assert (await held(guest))[2] == "lowered"
    host_hit = await host.bridge.command("native-host-damage", "local")
    assert host_hit["ack"]["message"] == "stock direct damage delivered to the enrolled pawn", host_hit

    def chooser(frame, state):
        view = frame.get("game", {}).get("native_wipe_view", {})
        return (view.get("screen"), view.get("state")) == ("stock", state)

    host_view, guest_view = [
        (await wait_resident_status(run, lambda frame: chooser(frame, "selecting")))["game"]["native_wipe_view"]
        for run in (host_run, guest_run)
    ]
    assert not (host.bridge.directory / "death.tsv").exists(), "The resident downed events wrote a mailbox"
    assert host_view["id"] == guest_view["id"] and host_view["owner"] == "host" and guest_view["owner"] == character

    async def press(seat, view, action, state):
        snapshot = await seat.bridge.command("native-wipe-view-key-fixture", view["id"], view["map"],
                                             view["pawn"], view["owner"], action)
        observed = snapshot["native_wipe_view"]
        assert (observed["id"], observed["state"], observed["screen"]) == (view["id"], state, "stock"), observed
        return observed

    guest_first = first == "guest"
    retained = bandage if guest_first else weapon
    selected = await press(guest, guest_view, f"stock-keep-{retained}", "selecting")
    assert selected["selected"] == 1, selected

    if guest_first:
        first_seat, first_view, first_run = guest, guest_view, guest_run
        other_seat, other_view, other_run = host, host_view, host_run
    else:
        first_seat, first_view, first_run = host, host_view, host_run
        other_seat, other_view, other_run = guest, guest_view, guest_run
    choice = {retained: 1} if guest_first else {}
    await press(first_seat, first_view, "stock-submit", "submitting")
    waiting = await wait_resident_status(other_run, lambda frame: (
        (frame.get("gameplay", {}).get("wipe") or {}).get("choices", {}).get(first_view["owner"]) == choice))
    assert chooser(waiting, "selecting") and waiting["gameplay"]["wipe"]["deadline"] is None, waiting
    await wait_resident_status(first_run, lambda frame: chooser(frame, "accepted"))
    await press(first_seat, first_view, "stock-waiting", "accepted")
    await press(first_seat, first_view, "stock-reopen-journal", "accepted")
    accepted = await press(first_seat, first_view, "stock-waiting", "accepted")
    assert accepted["selected"] == len(choice), accepted
    await press(other_seat, other_view, "stock-submit", "submitting")

    def respawned(frame):
        gameplay, game = frame.get("gameplay", {}), frame.get("game", {})
        return ("wipe" in gameplay and gameplay["wipe"] is None and not gameplay.get("pending_spawns")
                and game.get("map") == host_view["map"] and not game.get("native_wipe_view"))

    for run in (host_run, guest_run):
        await wait_resident_status(run, respawned, timeout=30)
    inventory = await submit(guest_run, "native-inventory-list")
    assert inventory["ok"], inventory
    assert [row["guid"] for row in inventory["native_inventory"]["items"]] == [retained], inventory
    expected = ["-", "-", "lowered"] if guest_first else before
    async with asyncio.timeout(10):
        while True:
            after_host, after_guest = await asyncio.gather(held(host), held(guest))
            if after_host == after_guest:
                break
            await asyncio.sleep(.15)
    assert after_host == after_guest == expected, (expected, after_host, after_guest)
    return {"stock_wipe_chooser": {"wipe_id": host_view["id"], "guest_pawn_before_wipe": pawn,
                                  "retained_guid": retained, "host_pause_closed": True, "guest_journal_closed": True,
                                  "confirmed_view_reopened": accepted, "first_confirmation": first,
                                  "both_confirmations_required": True, "guest_aim_released_on_down": True,
                                  "native_death_mailbox_absent": True,
                                  "weapon_before_wipe": before, "host_view_after_respawn": after_host,
                                  "guest_view_after_respawn": after_guest}}
