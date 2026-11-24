"""Exercise both stock wipe submissions and the read-only waiting screen."""

from portable_guest_world_start import wait_resident_status
from tools.silverfish.control import submit
from tools.silverfish.native_gold import valid_guid


async def verify_stock_wipe_chooser(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    guest_frame = await wait_resident_status(guest_run, lambda frame: frame.get("character_id") and frame.get("gameplay_fresh"))
    character = guest_frame["character_id"]
    guid = await probe(host.bridge, "portable-wipe-chooser", "configure")
    assert valid_guid(guid), guid
    await wait_resident_status(host_run, lambda frame: (
        (rules := frame.get("game", {}).get("native_rules", {})).get("extraction_mode") == 1
        and rules.get("items_kept_on_death") == 1 and rules.get("drop_bone_pile") == 1))

    for seat, blocker in ((host, "pause"), (guest, "journal")):
        assert await probe(seat.bridge, "portable-wipe-chooser", blocker) == blocker
    guest_hit = await host.bridge.command("native-guest-damage", "guest")
    assert guest_hit["ack"]["message"] == "stock direct damage delivered to the enrolled pawn", guest_hit
    host_hit = await host.bridge.command("native-host-damage", "local")
    assert host_hit["ack"]["message"] == "stock direct damage delivered to the enrolled pawn", host_hit

    def chooser(frame, state):
        view = frame.get("game", {}).get("native_wipe_view", {})
        return (view.get("screen"), view.get("state")) == ("stock", state)

    host_view, guest_view = [
        (await wait_resident_status(run, lambda frame: chooser(frame, "selecting")))["game"]["native_wipe_view"]
        for run in (host_run, guest_run)
    ]
    assert host_view["id"] == guest_view["id"] and host_view["owner"] == "host" and guest_view["owner"] == character

    async def press(seat, view, action, state):
        snapshot = await seat.bridge.command("native-wipe-view-key-fixture", view["id"], view["map"],
                                             view["pawn"], view["owner"], action)
        observed = snapshot["native_wipe_view"]
        assert (observed["id"], observed["state"], observed["screen"]) == (view["id"], state, "stock"), observed
        return observed

    selected = await press(guest, guest_view, f"stock-keep-{guid}", "selecting")
    assert selected["selected"] == 1, selected
    await press(guest, guest_view, "stock-submit", "submitting")
    waiting = await wait_resident_status(host_run, lambda frame: (
        (frame.get("gameplay", {}).get("wipe") or {}).get("choices", {}).get(character) == {guid: 1}))
    assert chooser(waiting, "selecting") and waiting["gameplay"]["wipe"]["deadline"] is None, waiting
    await wait_resident_status(guest_run, lambda frame: chooser(frame, "accepted"))
    await press(guest, guest_view, "stock-waiting", "accepted")
    await press(guest, guest_view, "stock-reopen-journal", "accepted")
    accepted = await press(guest, guest_view, "stock-waiting", "accepted")
    assert accepted["selected"] == 1, accepted

    await press(host, host_view, "stock-submit", "submitting")

    def respawned(frame):
        gameplay, game = frame.get("gameplay", {}), frame.get("game", {})
        return ("wipe" in gameplay and gameplay["wipe"] is None and not gameplay.get("pending_spawns")
                and game.get("map") == host_view["map"] and not game.get("native_wipe_view"))

    for run in (host_run, guest_run):
        await wait_resident_status(run, respawned, timeout=30)
    inventory = await submit(guest_run, "native-inventory-list")
    assert inventory["ok"], inventory
    assert [row["guid"] for row in inventory["native_inventory"]["items"]] == [guid], inventory
    return {"stock_wipe_chooser": {"wipe_id": host_view["id"], "guest_pawn_before_wipe": pawn,
                                  "retained_guid": guid, "host_pause_closed": True, "guest_journal_closed": True,
                                  "confirmed_guest_view_reopened": accepted, "both_confirmations_required": True}}
