"""Verify automatic recovery after the guest alone loses its stock world."""

import asyncio
import time

from live_multi_smoke import campaign_save_hashes
from live_unified_title_smoke import bound_pawn
from portable_guest_world_start import wait_resident_status
from tools.silverfish.runtime import MENU
from tools.silverfish.travel import OBSERVATORY


async def verify_stock_guest_title_recovery(host, guest, pawn, *, probe=None):
    host_run, guest_run = host.campaign / "run", guest.client / "run"
    host_before, guest_before = await asyncio.gather(
        wait_resident_status(host_run, lambda frame: frame.get("state") == "hosting"),
        wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected"
                             and frame.get("gameplay_fresh") is True),
    )
    host_id, guest_id = host_before["generation"], guest_before["generation"]
    epoch, character = guest_before["zone_epoch"], guest_before["character_id"]
    scope = character.replace("-", "")
    host_game, guest_game = host_before["game"], guest_before["game"]
    assert host_game["map"] == guest_game["map"] == OBSERVATORY
    assert guest_game["save_scope"] == scope and bound_pawn(host_before, character) == pawn
    assert (host_id, host_before["zone_epoch"]) == (guest_before["host_session"], epoch)
    revision = host_before["campaign"]["revision"]
    hashes = campaign_save_hashes(host.campaign)
    assert hashes
    census_before = {}
    if probe:
        from portable_crossroads_profile import read_session_census, read_session_title, session_profile_request

        census_before = await read_session_census(host, guest, probe, OBSERVATORY)
        if census_before:
            armed = await session_profile_request(guest, probe, "watchsessiontitle")
            assert armed == "armed", armed

    started = time.monotonic()
    # The resident worker may rejoin as soon as the stock title becomes ready.
    # Start observing before the test injects the guest-only world departure.
    watch_title = asyncio.create_task(guest.bridge.wait_for(
        lambda game: game["sequence"] > guest_game["sequence"] and game["map"] == MENU
        and game.get("server_connection") is False and game.get("save_scope") == scope,
        timeout=20, interval=.03,
    ))
    try:
        left = await guest.bridge.command("leave", timeout=12)
        assert left["ack"]["message"] == "leaving session"
        title = await watch_title
    finally:
        if not watch_title.done():
            watch_title.cancel()
        await asyncio.gather(watch_title, return_exceptions=True)

    def guest_rejoined(frame):
        game = frame.get("game", {})
        return (frame.get("state") == "connected" and frame.get("gameplay_fresh") is True
                and frame.get("link_connected") is True and game.get("server_connection") is True
                and game.get("map") == OBSERVATORY and game.get("sequence", 0) > title["sequence"]
                and game.get("pawns", {}).get("local", {}).get("name"))

    def host_still_listening(frame):
        game = frame.get("game", {})
        return (frame.get("state") == "hosting" and game.get("map") == OBSERVATORY
                and game.get("sequence", 0) > host_game["sequence"] and bound_pawn(frame, character))

    guest_after = await wait_resident_status(guest_run, guest_rejoined, timeout=45)
    host_after = await wait_resident_status(host_run, host_still_listening)
    assert guest_after["game"]["save_scope"] == scope
    assert (guest_after["generation"], guest_after["host_session"], guest_after["zone_epoch"],
            guest_after["character_id"], guest_after["pid"]) == (guest_id, host_id, epoch, character, guest_before["pid"])
    assert (host_after["generation"], host_after["zone_epoch"], host_after["pid"]) == (host_id, epoch, host_before["pid"])
    assert host_after["campaign"]["revision"] == revision and campaign_save_hashes(host.campaign) == hashes
    elapsed = round(time.monotonic() - started, 3)
    census = {}
    if census_before:
        census = {"native_session_census": {"before": census_before,
                                            "guest_title": await read_session_title(guest, probe, watched=True),
                                            "rejoined": await read_session_census(host, guest, probe, OBSERVATORY)}}
        assert all(census["native_session_census"].values()), census
    return {"stock_guest_title_recovery": {
        "character_id": character, "host_session": host_id, "guest_generation": guest_id, "zone_epoch": epoch,
        "map": OBSERVATORY, "title_sequence": title["sequence"], "rejoined_sequence": guest_after["game"]["sequence"],
        "host_pawn_before": pawn, "host_pawn_after": bound_pawn(host_after, character),
        "guest_pawn_after": guest_after["game"]["pawns"]["local"]["name"],
        "revision": revision, "checkpoint_hashes_match": True, "elapsed_seconds": elapsed, **census,
    }}
