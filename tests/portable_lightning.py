"""Verify one stock lightning strike reaches both peers at the host's actual impact."""

import asyncio
import math
import time

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import resident_events, wait_resident_status
from portable_weather_travel import enter_crossroads
from tools.silverfish.travel import CROSSROADS, OBSERVATORY


def parse_witness(text):
    def coordinates(value):
        return None if value == "-" else [float(axis) for axis in value.split(",")]

    (actors, explosions, damage, thunder, strike, blast, source, rotation,
     damage_schema, thunder_at, volume, strike_at, blast_at) = text.split(";")
    return {
        "stock_bolts": int(actors), "stock_explosions": int(explosions), "local_strike_damage": int(damage),
        "stock_thunder": int(thunder), "stock_bolt_effect": int(strike), "stock_blast_effect": int(blast),
        "impact": coordinates(source), "rotation": coordinates(rotation), "damage_schema": coordinates(damage_schema),
        "thunder_position": coordinates(thunder_at), "thunder_volume": None if volume == "-" else float(volume),
        "bolt_effect_position": coordinates(strike_at), "blast_effect_position": coordinates(blast_at),
    }


def same_point(actual, expected):
    return actual is not None and len(actual) == len(expected) == 3 and math.dist(actual, expected) < .5


def same_rotation(actual, expected):
    return actual is not None and len(actual) == len(expected) == 3 and all(
        abs(math.remainder(first - second, 360)) < .05 for first, second in zip(actual, expected, strict=True))


async def verify_stock_lightning(host, guest, pawn, probe):
    host_run, guest_run = host.campaign / "run", guest.client / "run"

    def host_ready(frame):
        game = frame.get("game", {})
        return (not frame.get("awaiting_zone_guest")
                and game.get("native_guest_save", {}).get("state") == "bound"
                and game.get("map") == CROSSROADS)

    initial = await wait_resident_status(host_run, lambda frame: host_ready(frame)
                                         or frame.get("game", {}).get("map") == OBSERVATORY)
    if not host_ready(initial):
        await enter_crossroads(host, pawn)
    authority = await wait_resident_status(host_run, host_ready)
    epoch = authority["zone_epoch"]
    local = await wait_resident_status(guest_run, lambda frame: (
        frame.get("state") == "connected" and frame.get("zone_epoch") == epoch
        and frame.get("game", {}).get("map") == CROSSROADS
        and frame["game"].get("native_world_view", {}).get("weather_verified") is True))
    checkpoint = campaign_save_hashes(host.campaign)
    assert checkpoint, "Crossroads arrival must be checkpointed before the lightning trial"

    async def lightning(seat, action):
        return (await probe(seat.bridge, "portable-lightning", action)).strip()

    assert await asyncio.gather(lightning(host, "arm"), lightning(guest, "arm")) == ["armed", "armed"]
    # Arming may load the stock effect library. Give the host detector a chance to bind the loaded graph.
    await asyncio.sleep(1.1)
    prior_serials = {row["serial"] for row in authority["game"].get("native_weather_lightning", [])}
    started = time.time()
    authored = [float(axis) for axis in (await lightning(host, "strike")).split(",")]

    def host_strike(frame):
        return next((event for event in frame.get("game", {}).get("native_weather_lightning", ())
                     if event["serial"] not in prior_serials and same_point(event["position"], authored)), None)

    authority = await wait_resident_status(host_run, host_strike, timeout=7)
    captured = host_strike(authority)
    assert 0 < captured["ttl"] <= 3, captured

    def guest_playback(frame):
        viewed = frame.get("game", {}).get("native_weather_lightning_playback", {})
        return viewed.get("epoch") == epoch and viewed.get("serial") == captured["serial"]

    displayed = (await wait_resident_status(guest_run, guest_playback, timeout=8))["game"]["native_weather_lightning_playback"]
    assert displayed["flash_registered"] is True, displayed
    assert same_point(displayed["position"], captured["position"]) and same_rotation(displayed["rotation"], captured["rotation"]), displayed

    await wait_resident_status(host_run, lambda frame: host_ready(frame) and not host_strike(frame), timeout=6)
    host_report, guest_report = map(parse_witness, await asyncio.gather(lightning(host, "read"), lightning(guest, "read")))
    assert await asyncio.gather(lightning(host, "disarm"), lightning(guest, "disarm")) == ["cleared", "cleared"]
    assert (host_report["stock_bolts"], host_report["stock_explosions"], host_report["stock_thunder"],
            host_report["stock_blast_effect"]) == (1, 1, 1, 1), host_report
    assert host_report["damage_schema"] == [50, 100, 200, 100], host_report
    assert same_point(host_report["impact"], captured["position"]) and same_rotation(host_report["rotation"], captured["rotation"]), host_report
    assert (guest_report["stock_bolts"], guest_report["stock_explosions"], guest_report["local_strike_damage"]) == (0, 0, 0), guest_report
    assert (guest_report["stock_thunder"], guest_report["stock_bolt_effect"], guest_report["stock_blast_effect"]) == (1, 1, 1), guest_report
    assert host_report["thunder_volume"] == guest_report["thunder_volume"] == 1, (host_report, guest_report)
    assert all(same_point(host_report[effect], captured["position"])
               for effect in ("thunder_position", "blast_effect_position")), host_report
    for effect in ("thunder_position", "bolt_effect_position", "blast_effect_position"):
        assert same_point(guest_report[effect], captured["position"]), guest_report

    played = [event for event in await resident_events(guest_run, local)
              if event["time_unix"] >= started and event["event"] == "native_weather_lightning_played"]
    assert len(played) == 1 and played[0]["serial"] == captured["serial"], played
    assert same_point(played[0]["position"], captured["position"]) and same_rotation(played[0]["rotation"], captured["rotation"]), played
    assert campaign_save_hashes(host.campaign) == checkpoint, "Lightning projection wrote a campaign save"
    return {"stock_lightning": {"host_capture": captured, "guest_playback": displayed,
                                "host_stock_dispatch": host_report, "guest_stock_effect_calls": guest_report,
                                "guest_service_event": played[0], "campaign_unchanged": True}}
