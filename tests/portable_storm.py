"""Verify active host storms across a fresh join and a hot reconnect."""

import asyncio

from live_multi_smoke import campaign_save_hashes
from portable_guest_world_start import wait_resident_status
from tools.silverfish.travel import OBSERVATORY


STORMS = "/Game/Classes/Weather/Storms/"
LIGHTNING = STORMS + "LightningStorm_BP.LightningStorm_BP_C"
FOG = STORMS + "TheFog_BP.TheFog_BP_C"
PESTILENCE = STORMS + "PestilenceStorm_BP.PestilenceStorm_BP_C"


def source_report(response):
    variant, actor, *counts = response.split(";")
    return dict(variant=variant, actor=int(actor), **dict(zip(
        ("lightning_warning", "fog_warning", "lightning_spawns", "fog_spawns", "pestilence_spawns",
         "lightning_particles", "lightning_audio"), map(int, counts), strict=True)))


def conflict_report(response):
    variant, actor, ongoing = response.split(";")
    assert variant == FOG and int(actor) == 0 and ongoing == "false", response
    return dict(stock_storm=variant, independent_storm_actor=int(actor), ongoing=False)


class StockStormTrial:
    def __init__(self, probe):
        self.probe = probe

    async def stage(self, seat, action):
        return (await self.probe(seat.bridge, "portable-storm", action)).strip()

    async def wait_projection(self, host, guest, variant, *, guard_after=0, fresh_after=0):
        host_run, guest_run = host.campaign / "run", guest.client / "run"

        def host_ambience(frame):
            game = frame.get("game", {})
            row = game.get("native_weather_ambience", {})
            return (game.get("map") == OBSERVATORY and row.get("variant") == variant
                    and row.get("phase") == "active" and row.get("blend", 0) > 0
                    and (variant != PESTILENCE or row.get("fog")))

        authority = await wait_resident_status(host_run, host_ambience)
        expected = authority["game"]["native_weather_ambience"]
        epoch = authority["zone_epoch"]

        def guest_ambience(frame):
            game = frame.get("game", {})
            row = game.get("native_weather_ambience_guest", {})
            return (frame.get("updated_unix", 0) >= fresh_after and frame.get("state") == "connected"
                    and game.get("map") == OBSERVATORY
                    and frame.get("gameplay_fresh") is True and row.get("epoch") == epoch
                    and row.get("phase") == "active" and row.get("variant") == variant
                    and row.get("blend", 0) > 0 and game.get("native_weather_storm_guard", 0) > guard_after
                    and all(row.get(key) == expected.get(key) for key in ("level", "particle", "sound", "volume"))
                    and (variant != PESTILENCE or row.get("fog")))

        guest_frame = await wait_resident_status(guest_run, guest_ambience)
        viewed = guest_frame["game"]["native_weather_ambience_guest"]
        assert expected["level"] == 1 and expected["fade"] > 0 and expected["volume"] > 0, expected
        if variant == LIGHTNING:
            assert expected["particle"].endswith("/NI_LightningStorm.NI_LightningStorm"), expected
            assert expected["sound"].endswith("/Wind_Strong_01.Wind_Strong_01"), expected
        else:
            expected_fog, viewed_fog = expected["fog"], viewed["fog"]
            assert expected_fog["material"].endswith("/Mat_PestilenceFog.Mat_PestilenceFog"), expected_fog
            assert expected_fog["density"] > 0 and viewed_fog["density"] > 0, (expected_fog, viewed_fog)
            assert all(viewed_fog[key] == expected_fog[key] for key in
                       ("material", "start_distance", "r", "g", "b", "a")), (expected_fog, viewed_fog)
        return {"host_projection": expected, "guest_readback": viewed, "zone_epoch": epoch,
                "guard_denials": guest_frame["game"].get("native_weather_storm_guard", 0)}

    async def prepare(self, host, guest, _character):
        self.checkpoint = campaign_save_hashes(host.campaign)
        assert self.checkpoint, "The private host campaign is required before staging stock weather"
        assert await asyncio.gather(self.stage(host, "arm"), self.stage(guest, "arm")) == ["armed", "armed"]
        assert await self.stage(guest, "queueconflict") == "queued"
        variant, actor = (await self.stage(host, "start")).split(";")
        assert variant == LIGHTNING and int(actor), (variant, actor)
        self.first_actor = int(actor)

        async with asyncio.timeout(8):
            while True:
                witnessed = source_report(await self.stage(host, "read"))
                if witnessed["lightning_warning"] == 1:
                    break
                await asyncio.sleep(.1)
        assert witnessed["actor"] == self.first_actor and witnessed["lightning_spawns"] == 1, witnessed

    async def joined(self, host, guest, _pawn):
        async with asyncio.timeout(12):
            while (attempt := await self.stage(guest, "awaitconflict")) == "pending":
                await asyncio.sleep(.1)
        attempt = conflict_report(attempt)
        projected = await self.wait_projection(host, guest, LIGHTNING)
        host_stock, guest_stock = map(source_report, await asyncio.gather(
            self.stage(host, "read"), self.stage(guest, "read")))
        assert host_stock["actor"] == self.first_actor and host_stock["lightning_warning"] == 1, host_stock
        assert guest_stock["actor"] == 0 and not any(guest_stock[key] for key in
                   ("fog_warning", "lightning_spawns", "fog_spawns", "pestilence_spawns")), guest_stock
        assert guest_stock["lightning_warning"] <= 1, guest_stock
        assert guest_stock["lightning_particles"] == guest_stock["lightning_audio"] == 1, guest_stock
        assert campaign_save_hashes(host.campaign) == self.checkpoint, "Storm join wrote the campaign early"
        self.evidence = {"join": dict(projected, guest_conflict=attempt, host_stock=host_stock, guest_stock=guest_stock)}
        return {"stock_storm_ambience": self.evidence}

    async def reconnected(self, host, guest, started):
        before = await self.wait_projection(host, guest, LIGHTNING, guard_after=-1, fresh_after=started)
        attempt = conflict_report(await self.stage(guest, "conflict"))
        projected = await self.wait_projection(host, guest, LIGHTNING, guard_after=before["guard_denials"])
        host_stock, guest_stock = map(source_report, await asyncio.gather(
            self.stage(host, "read"), self.stage(guest, "read")))
        assert host_stock["actor"] == self.first_actor and host_stock["lightning_spawns"] == 1, host_stock
        assert projected["zone_epoch"] == self.evidence["join"]["zone_epoch"], projected
        assert guest_stock["actor"] == 0 and guest_stock["fog_spawns"] == guest_stock["lightning_spawns"] == 0, guest_stock
        assert guest_stock["lightning_warning"] <= 1 and guest_stock["fog_warning"] == 0, guest_stock
        assert guest_stock["lightning_particles"] == guest_stock["lightning_audio"] == 1, guest_stock
        self.evidence["reconnect"] = dict(projected, guest_conflict=attempt, host_stock=host_stock, guest_stock=guest_stock)

        variant, actor = (await self.stage(host, "fog")).split(";")
        assert variant == PESTILENCE and int(actor) and int(actor) != self.first_actor, (variant, actor)
        self.evidence["custom_fog"] = await self.wait_projection(host, guest, PESTILENCE)
        host_stock, guest_stock = map(source_report, await asyncio.gather(
            self.stage(host, "read"), self.stage(guest, "read")))
        assert host_stock["pestilence_spawns"] == 1 and guest_stock["pestilence_spawns"] == 0, (host_stock, guest_stock)

        assert await self.stage(host, "clear") == "fading"
        host_idle, guest_idle = await asyncio.gather(
            wait_resident_status(host.campaign / "run", lambda frame:
                frame.get("game", {}).get("native_weather_ambience", {}).get("phase") == "idle"),
            wait_resident_status(guest.client / "run", lambda frame:
                frame.get("game", {}).get("native_weather_ambience_guest", {}).get("phase") == "idle"
                and frame["game"]["native_weather_ambience_guest"].get("epoch") == projected["zone_epoch"]))
        idle = guest_idle["game"]["native_weather_ambience_guest"]
        assert not any(idle.get(key) for key in ("particle_registered", "audio_registered", "fog_registered", "fog")), idle
        self.evidence["cleared"] = {"host": host_idle["game"]["native_weather_ambience"], "guest": idle}
        assert campaign_save_hashes(host.campaign) == self.checkpoint, "Storm projection wrote the campaign early"
        self.evidence["campaign_unchanged"] = True
