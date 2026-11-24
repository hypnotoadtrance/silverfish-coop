"""Check stock weather ownership on a packaged Observatory to Crossroads trip."""

import asyncio
import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.control import current_status
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def enter_crossroads(host, pawn):
    route, = (route for route in AUTHORED_ROUTES if
              (route.source, route.destination, route.entrance) == (OBSERVATORY, CROSSROADS, "Obsv_Etr"))
    for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
        await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)


async def verify_weather_travel(host, guest, pawn, probe, *, profile=False):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def weather(seat, action):
        return (await probe(seat.bridge, "weather-travel", action)).strip()

    initial = await wait_resident_status(guest_run, lambda f: f["game"]["map"] == OBSERVATORY
                                         and f["game"].get("native_world_view", {}).get("weather_verified") is True)
    performance = {}
    if profile:
        from portable_crossroads_profile import profile_world

        performance["observatory"] = await profile_world(host, guest, OBSERVATORY, probe)
    await enter_crossroads(host, pawn)

    async def applied(intensity):
        def matches(frame):
            game = frame.get("game", {})
            view = game.get("native_world_view", {})
            return (frame.get("state") == "connected" and game.get("map") == CROSSROADS
                    and frame.get("zone_epoch") != initial["zone_epoch"] and view.get("epoch") == frame["zone_epoch"]
                    and view.get("map") == CROSSROADS and view.get("mode") == "applied"
                    and view.get("weather_verified") is True
                    and view.get("rain_verified") is (intensity == 0)
                    and bool(view.get("rain_skipped_nullrhi")) is (intensity > 0)
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

    await wait_resident_status(host_run, lambda f: f["game"]["map"] == CROSSROADS
                               and not f.get("awaiting_zone_guest")
                               and f["game"].get("native_guest_save", {}).get("state") == "bound")
    try:
        await weather(host, "dry")
        clear = await applied(0)
        checkpoint = campaign_save_hashes(host.campaign)
        before = await weather(guest, "localrain")
        assert math.isclose(float(before.split(";")[0]), .85, abs_tol=.001), before

        await weather(host, "wet")
        wet = await applied(.55)
        async with asyncio.timeout(5):
            while await weather(guest, "siren") == "0":
                await asyncio.sleep(.12)
        wet_particles = await weather(guest, "particles")
        assert wet_particles == "0", wet_particles
        await weather(host, "dry")
        dry = await applied(0)
        assert await weather(guest, "particles") == "0"
        assert await weather(guest, "siren") == "1"
        local = (await weather(guest, "read")).split(";")
        assert len(local) == 5 and local[1:3] == ["false", "false"] and all(float(v) == 0 for v in local[3:]), local
        assert clear["epoch"] == wet["epoch"] == dry["epoch"] and clear["epoch"] != initial["zone_epoch"]
        assert campaign_save_hashes(host.campaign) == checkpoint, "Weather projection wrote a campaign save"
        if profile:
            performance["crossroads"] = await profile_world(host, guest, CROSSROADS, probe)
    finally:
        assert await weather(host, "resume") == "restored"
    result = {"stock_weather_travel": {"clear": clear, "wet": wet, "dry": dry,
                                      "guest_local_rain_before": before, "guest_local_rain_suppressed": local,
                                      "guest_stock_siren_callbacks": 1, "guest_wet_particle_count": int(wet_particles)}}
    if performance:
        result["crossroads_profile"] = performance
    return result
