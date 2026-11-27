"""Check stock weather ownership on a packaged Observatory to Crossroads trip."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, verify_stock_clock, wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import current_status
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def enter_crossroads(host, pawn, *, verify_wall=False):
    route, = (route for route in AUTHORED_ROUTES if
              (route.source, route.destination, route.entrance) == (OBSERVATORY, CROSSROADS, "Obsv_Etr"))
    blocked = {}
    if verify_wall:
        for participant, position in ((pawn, "near"), (pawn, "far"), ("local", "near")):
            await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
            if position == "near":
                reply = await host.bridge.command("native-exit-wall-probe", participant, route.entrance)
                blocked[participant] = reply["native_zone_wall"]
                assert blocked[participant]["outcome"] == "wall", reply
    for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
        await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)
    return blocked


async def verify_weather_travel(host, guest, pawn, probe, *, profile=False, gc_trial=False, rendered=False):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def weather(seat, action):
        return (await probe(seat.bridge, "weather-travel", action)).strip()

    async def warning_profile(action):
        return (await probe(guest.bridge, "crossroads-profile", action, reply_end="|end")).removesuffix("|end")

    initial = await wait_resident_status(guest_run, lambda f: f["game"]["map"] == OBSERVATORY
                                         and f["game"].get("native_world_view", {}).get("weather_verified") is True)
    performance = {}
    if profile:
        from live_stock_creator_smoke import verify_personal_time_shift
        from portable_crossroads_profile import capture_rendered_frame, profile_world

        performance["observatory"] = await profile_world(host, guest, OBSERVATORY, probe)
        performance["personal_time_shift"] = await verify_personal_time_shift(host, guest)
    before_travel = campaign_save_hashes(host.campaign)
    travel_started = time.time()
    blocked_departures = await enter_crossroads(host, pawn, verify_wall=profile)

    async def applied(intensity):
        skip_particles = intensity > 0 and not rendered

        def matches(frame):
            game = frame.get("game", {})
            view = game.get("native_world_view", {})
            return (frame.get("state") == "connected" and game.get("map") == CROSSROADS
                    and frame.get("zone_epoch") != initial["zone_epoch"] and view.get("epoch") == frame["zone_epoch"]
                    and view.get("map") == CROSSROADS and view.get("mode") == "applied"
                    and view.get("weather_verified") is True
                    and view.get("rain_verified") is (not skip_particles)
                    and bool(view.get("rain_skipped_nullrhi")) is skip_particles
                    and math.isclose(view.get("rain_intensity", -1), intensity, abs_tol=.001))

        try:
            return (await wait_resident_status(guest_run, matches, timeout=18))["game"]["native_world_view"]
        except RuntimeError:
            authority, local, host_frame, guest_frame = await asyncio.gather(
                weather(host, "diagnose"), weather(guest, "diagnose"),
                current_status(host_run), current_status(guest_run))
            fields = ("mode", "map", "sequence", "weather_verified", "rain_intensity", "rain_verified",
                      "rain_skipped_nullrhi")
            host_view, guest_view = (
                {key: view.get(key) for key in fields}
                for view in (frame.get("game", {}).get("native_world_view", {}) for frame in (host_frame, guest_frame))
            )
            raise RuntimeError(f"Guest missed the {intensity} rain update. Host actor {authority}, view {host_view}. "
                               f"Guest actor {local}, view {guest_view}.") from None

    travelled = await wait_resident_status(host_run, lambda f: f["game"]["map"] == CROSSROADS
                                          and not f.get("awaiting_zone_guest")
                                          and f["game"].get("native_guest_save", {}).get("state") == "bound")
    departure, = (event for event in await resident_events(host_run, travelled)
                  if event["event"] == "native_authored_zone_request" and event["source"] == OBSERVATORY
                  and event["destination"] == CROSSROADS and event["entrance"] == "Obsv_Etr")
    assert departure["result"]["ok"] and departure["kind"] == "overlap" and departure["exit_crossing"], departure
    checkpoint = campaign_save_hashes(host.campaign)
    assert checkpoint and checkpoint != before_travel, "Travel did not commit the campaign checkpoint"
    for seat in (host, guest):
        for mailbox in ("native-zone-events", "native-authored-exit-events"):
            assert not (seat.bridge.directory / mailbox).exists(), f"Travel wrote {mailbox}"
    try:
        await weather(host, "dry")
        clear = await applied(0)
        before = await weather(guest, "localrain")
        assert math.isclose(float(before.split(";")[0]), .85, abs_tol=.001), before

        if profile:
            assert await warning_profile("arm") == "armed"
        warning_started = time.time()
        await weather(host, "wet")
        wet = await applied(.55)
        async with asyncio.timeout(5):
            while await weather(guest, "siren") == "0":
                await asyncio.sleep(.12)
        wet_particles = await weather(guest, "particles")
        assert int(wet_particles) == int(rendered), wet_particles
        await weather(host, "dry")
        dry = await applied(0)
        assert await weather(guest, "particles") == "0"
        assert await weather(guest, "siren") == "1"
        if profile:
            from portable_crossroads_profile import read_comparison

            async with asyncio.timeout(7):
                while (sample := await warning_profile("read")) == "sampling":
                    await asyncio.sleep(.2)
            performance["warning_sources"] = read_comparison(sample)["tick_sources"]
        guest_now = await wait_resident_status(guest_run, lambda frame: frame.get("state") == "connected")
        matching = [event for event in await resident_events(guest_run, guest_now)
                    if event.get("event") == "native_weather_siren_played" and event.get("cue") == 3
                    and event.get("time_unix", 0) >= warning_started]
        warning, = matching
        warning = {**warning, "fixture_request_to_playback_ms": round((warning["time_unix"] - warning_started) * 1000)}
        local = (await weather(guest, "read")).split(";")
        assert len(local) == 5 and local[1:3] == ["false", "false"] and all(float(v) == 0 for v in local[3:]), local
        assert clear["epoch"] == wet["epoch"] == dry["epoch"] and clear["epoch"] != initial["zone_epoch"]
        assert campaign_save_hashes(host.campaign) == checkpoint, "Weather projection wrote a campaign save"
        if profile:
            def ready_after_travel(frame):
                game = frame.get("game", {})
                return (frame.get("state") == "connected" and game.get("map") == CROSSROADS
                        and "native_zone_loading_cover" not in game and game.get("pawns", {}).get("local", {}).get("name"))

            settled = await wait_resident_status(guest_run, ready_after_travel)
            loading, = [event for event in await resident_events(guest_run, settled)
                        if event.get("event") == "native_zone_loading_transition"
                        and event.get("destination") == CROSSROADS
                        and event.get("time_unix", 0) >= travel_started]
            assert loading["covered"] and loading["cue"], loading
            performance["crossroads_loading"] = loading
            pawn_name = settled["game"]["pawns"]["local"]["name"]
            performance["crossroads_clock"] = await verify_stock_clock(guest_run, CROSSROADS, settled["zone_epoch"], pawn_name)
            performance["crossroads"] = await profile_world(host, guest, CROSSROADS, probe, gc_trial=gc_trial)
            if rendered:
                performance["rendered_frames"] = dict(zip(("host", "guest"), await asyncio.gather(
                    capture_rendered_frame(host, probe), capture_rendered_frame(guest, probe)), strict=True))
    finally:
        assert await weather(host, "resume") == "restored"
    result = {"stock_weather_travel": {"clear": clear, "wet": wet, "dry": dry,
                                      "departure": departure, "exit_mailboxes": 0, "checkpoint_updated": True,
                                      "guest_local_rain_before": before, "guest_local_rain_suppressed": local,
                                      "guest_stock_siren_callbacks": 1, "siren_receipt": warning,
                                      "guest_wet_particle_count": int(wet_particles)}}
    if performance:
        result["stock_weather_travel"]["blocked_departures"] = blocked_departures
        result["crossroads_profile"] = performance
    return result
