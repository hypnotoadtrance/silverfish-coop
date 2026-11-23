"""Proof helpers for private portable guest world startup and stock travel."""

import asyncio
import json
import time
import uuid

from tools.silverfish import native_guest_clock, runtime
from tools.silverfish.control import current_status, submit
from tools.silverfish.travel import CROSSROADS, OBSERVATORY, RUIN_FIELDS


async def wait_resident_status(directory, predicate, *, timeout=42):
    """Follow live resident status while its file is between lifecycle updates."""
    deadline = time.monotonic() + timeout
    current = {}
    while time.monotonic() < deadline:
        try:
            current = await current_status(directory)
        except (FileNotFoundError, RuntimeError):
            await asyncio.sleep(0.2)
            continue
        if predicate(current):
            return current
        if current.get("state") == "failed":
            break
        await asyncio.sleep(0.2)
    raise RuntimeError(f"{directory.parent.name} did not reach the expected resident state: {current}")


async def resident_events(run, frame):
    if frame.get("event_source") == "memory":
        result = await submit(run, "events-recent", tail=64)
        assert result["ok"], result
        return result["events"]
    return [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]


class GuestWorldStartProof:
    def __init__(self, guest):
        self.request = guest.bridge.directory / "portable-guest-world-start-request.tsv"
        self.log = guest.bridge.directory / "portable-guest-world-start-reply.tsv"
        self.log_bytes = b""

    @staticmethod
    def _resident_scene(frame, area=OBSERVATORY):
        game = frame.get("game", {})
        scene = game.get("native_scene", {})
        return (frame.get("state") == "hosting" and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and game.get("map") == area
                and scene.get("active_map") == area and isinstance(scene.get("game_seconds"), (int, float))
                and scene["game_seconds"] > 0)

    @staticmethod
    def _resident_guest(frame, area=OBSERVATORY):
        return (frame.get("state") == "connected" and frame.get("gameplay_fresh") is True
                and frame.get("link_connected") is True and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and frame.get("game", {}).get("map") == area)

    def _arm(self, area, arm):
        runtime.write_text(self.request, f"{self.session}\t{arm}\t{area}\t{self.character}\n", newline="\n")

    async def arm(self, host, guest, character):
        baseline = await wait_resident_status(host.campaign / "run", self._resident_scene)
        self.session, self.epoch = baseline["generation"], baseline["zone_epoch"]
        self.seed = baseline["game"]["native_scene"]["instance_seed"]
        self.earliest_ms = round(baseline["game"]["native_scene"]["game_seconds"] * 1000)
        self.character = uuid.UUID(character).hex
        assert all(uuid.UUID(value).hex == value for value in (self.session, self.epoch, self.character))
        assert not self.request.exists() and not self.log.exists()
        self._arm(OBSERVATORY, self.epoch)
        self.armed_at = time.monotonic()

    def _read(self, host_status, guest_status, area, epoch, arm, earliest_ms, *, require_begin_play=False):
        assert (guest_status["host_session"], guest_status["zone_epoch"]) == (self.session, epoch)
        assert (host_status["generation"], host_status["zone_epoch"]) == (self.session, epoch)
        assert guest_status["game"]["save_scope"] == uuid.UUID(guest_status["character_id"]).hex == self.character
        receipt = guest_status["game"].get(native_guest_clock.WORLD_START)
        assert receipt and (receipt["session"], receipt["epoch"], receipt["map"], receipt["seed"]) == (
            self.session, epoch, area, self.seed,
        ), f"Missing or incorrect typed pre-OpenLevel receipt for {area}: {receipt}"
        latest = host_status["game"]["native_scene"]
        assert latest["instance_seed"] == self.seed
        latest_ms = round(latest["game_seconds"] * 1000)
        assert earliest_ms - 2_000 <= receipt["milliseconds"] <= latest_ms + 2_000

        assert self.log.is_file(), f"The exact stock {area} day actor produced no private startup read"
        raw = self.log.read_bytes()
        assert raw.startswith(self.log_bytes)
        samples = {}
        for row in raw[len(self.log_bytes):].decode("ascii").splitlines():
            phase, session, sample_arm, sample_map, slot, seed, milliseconds, step = row.split("\t")
            assert phase in {"begin-play-pre", "load-map-post"} and phase not in samples
            assert (session, sample_arm, sample_map, slot) == (self.session, arm, area, f"SFCoopGuest_{self.character}")
            assert int(seed) == receipt["seed"]
            samples[phase] = {"seed": int(seed), "milliseconds": int(milliseconds), "travel_step": float(step)}
        assert samples, f"The stock {area} startup recorded no read for the armed visit"
        self.log_bytes = raw
        if require_begin_play:
            assert "begin-play-pre" in samples, f"The stock {area} startup only recorded a post-load read"
        phase = "begin-play-pre" if "begin-play-pre" in samples else "load-map-post"
        first = samples[phase]
        delta_ms = first["milliseconds"] - receipt["milliseconds"]
        if phase == "begin-play-pre":
            assert delta_ms == 0 and first["travel_step"] == 0, first
        else:
            assert 0 <= delta_ms <= 120_000, first
        return {"receipt": receipt, "first_stock_read": phase, "stock_samples": samples,
                "clock_advance_ms": delta_ms, "host_clock_window_ms": [earliest_ms, latest_ms],
                "resident_memory_both_seats": True}

    async def joined(self, host, guest, pawn):
        guest_status = await wait_resident_status(guest.client / "run", self._resident_guest)
        host_status = await wait_resident_status(host.campaign / "run", self._resident_scene)
        self.evidence = self._read(host_status, guest_status, OBSERVATORY, self.epoch, self.epoch, self.earliest_ms)
        self.evidence["arm_to_connected_seconds"] = round(time.monotonic() - self.armed_at, 3)
        self.receipt = self.evidence["receipt"]
        self.local_pawn = guest_status["game"]["pawns"]["local"]["name"]
        assert self.local_pawn
        return {"guest_world_start": self.evidence}

    async def reconnected(self, guest, started):
        status = await wait_resident_status(guest.client / "run", lambda frame:
                                            self._resident_guest(frame) and frame["updated_unix"] >= started)
        assert (status["host_session"], status["zone_epoch"]) == (self.session, self.epoch)
        assert status["game"]["save_scope"] == self.character
        assert status["game"]["pawns"]["local"]["name"] == self.local_pawn
        assert status["game"].get(native_guest_clock.WORLD_START) == self.receipt
        assert self.log.read_bytes() == self.log_bytes
        self.evidence["same_guest_pawn_visit_and_startup_after_reconnect"] = True

    async def travel(self, host, guest, probe_command):
        host_run, guest_run = host.campaign / "run", guest.client / "run"
        visits = {self.epoch}
        transitions = []
        self.evidence["stock_travel"] = transitions
        previous = self.epoch
        local_pawn = self.local_pawn

        def host_ready(frame, area):
            game = frame.get("game", {})
            return (self._resident_scene(frame, area) and not frame.get("awaiting_zone_guest")
                    and game.get("native_zone", {}).get("state") == "ready"
                    and game.get("native_guest_save", {}).get("state") == "bound")

        for source, destination, entrance, actor in (
            (OBSERVATORY, CROSSROADS, "Obsv_Etr", "Level_Exit_BP_C_1"),
            (CROSSROADS, RUIN_FIELDS, "CR_RF", "Level_Exit_BP_C_273"),
        ):
            baseline = await wait_resident_status(host_run, lambda frame: host_ready(frame, source))
            assert (baseline["generation"], baseline["zone_epoch"]) == (self.session, previous)
            pawn = baseline["game"]["native_guest_save"]["pawn"]
            earliest_ms = round(baseline["game"]["native_scene"]["game_seconds"] * 1000)
            arm = uuid.uuid4().hex
            self._arm(destination, arm)
            if source == OBSERVATORY:
                for player, position in ((pawn, "near"), ("local", "near"), ("local", "inside")):
                    await host.bridge.command("native-exit-position", player, position, entrance)
            else:
                expected = f"Level_Exit_BP_C {source}.CrossRoads_P:PersistentLevel.{actor}"
                for step in ("guest-near", "host-near", "host-inside"):
                    actual = await probe_command(host.bridge, "portable-ruin-exit", f"{step}\t{pawn}")
                    # The stock lookup can resolve a load-qualified instance name.
                    assert actual in (expected, expected + "0"), actual

            arrived = await wait_resident_status(host_run, lambda frame: host_ready(frame, destination), timeout=60)
            epoch = arrived["zone_epoch"]
            assert epoch not in visits and uuid.UUID(epoch).hex == epoch
            visits.add(epoch)
            guest_arrived = await wait_resident_status(
                guest_run, lambda frame: self._resident_guest(frame, destination)
                and (frame.get("host_session"), frame.get("zone_epoch")) == (self.session, epoch), timeout=35,
            )
            next_pawn = guest_arrived["game"]["pawns"]["local"]["name"]
            assert next_pawn != local_pawn and arrived["game"]["native_guest_save"]["pawn"] != pawn
            startup = self._read(arrived, guest_arrived, destination, epoch, arm, earliest_ms, require_begin_play=True)
            transitions.append({"source": source, "destination": destination, "entrance": entrance, "actor": actor,
                                "guest_pawn": next_pawn, "startup": startup})

            def clock_ready(frame):
                clock = frame.get("game", {}).get("native_guest_clock", {})
                return (self._resident_guest(frame, destination) and frame["zone_epoch"] == epoch
                        and (clock.get("map"), clock.get("epoch"), clock.get("pawn")) == (destination, epoch, next_pawn)
                        and type(clock.get("milliseconds")) is int)

            await wait_resident_status(guest_run, clock_ready, timeout=25)
            drawn = await submit(guest_run, native_guest_clock.FIXTURE)
            assert drawn["ok"], drawn
            clock, widget = (drawn[key] for key in ("native_guest_clock", "native_guest_clock_fixture"))
            for record in (clock, widget):
                assert (record["map"], record["epoch"], record["pawn"]) == (destination, epoch, next_pawn)
            keys = ("sequence", "milliseconds", "calls")
            assert tuple(clock[key] for key in keys) == tuple(widget[key] for key in keys)
            minutes = widget["milliseconds"] // 60_000
            day, minute_of_day = divmod(minutes, 1440)
            hour, minute = divmod(minute_of_day, 60)
            assert widget["calls"] >= 1 and widget["text"].endswith(f"DAY {day}  {hour:02d}:{minute:02d}")
            live_host = await wait_resident_status(host_run, lambda frame: host_ready(frame, destination))
            draw_host_ms = round(live_host["game"]["native_scene"]["game_seconds"] * 1000)
            assert abs(widget["milliseconds"] - draw_host_ms) <= 60_000
            transitions[-1]["stock_clock_draw"] = widget
            transitions[-1]["draw_host_ms"] = draw_host_ms

            host_events, guest_events = await asyncio.gather(
                resident_events(host_run, live_host), resident_events(guest_run, guest_arrived),
            )
            transition, = [event for event in host_events if event["event"] == "zone_travel_ready"
                           and event.get("generation") == self.session and event.get("epoch") == epoch]
            assert (transition["source"], transition["destination"], transition["entrance"]) == (source, destination, entrance)
            leave, = [event for event in guest_events if event["event"] == "native_zone_loading_transition"
                      and event.get("generation") == guest_arrived["generation"] and event.get("destination") == destination]
            transitions[-1]["host_travel_receipt"] = transition
            transitions[-1]["guest_title_rejoin"] = leave
            previous, local_pawn = epoch, next_pawn
