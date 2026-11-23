"""Check stock weather ownership on a packaged Observatory to Crossroads trip."""

import asyncio
import math

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import wait_resident_status
from tools.silverfish.authored_routes import AUTHORED_ROUTES
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


async def verify_weather_travel(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    async def weather(seat, action):
        return (await probe(seat.bridge, "weather-travel", action)).strip()

    initial = await wait_resident_status(guest_run, lambda f: f["game"]["map"] == OBSERVATORY
                                         and f["game"].get("native_world_view", {}).get("weather_verified") is True)
    route, = (route for route in AUTHORED_ROUTES if
              (route.source, route.destination, route.entrance) == (OBSERVATORY, CROSSROADS, "Obsv_Etr"))
    for participant, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
        await host.bridge.command("native-exit-position", participant, position, route.entrance, route.actor)

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
            authority = await weather(host, "diagnose")
            local = await weather(guest, "diagnose")
            raise RuntimeError(f"Guest missed the {intensity} rain update. Host {authority}. Guest stock {local}.") from None

    await wait_resident_status(host_run, lambda f: f["game"]["map"] == CROSSROADS
                               and not f.get("awaiting_zone_guest")
                               and f["game"].get("native_guest_save", {}).get("state") == "bound")
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
    return {"stock_weather_travel": {"clear": clear, "wet": wet, "dry": dry,
                                    "guest_local_rain_before": before, "guest_local_rain_suppressed": local,
                                    "guest_stock_siren_callbacks": 1, "guest_wet_particle_count": int(wet_particles)}}
