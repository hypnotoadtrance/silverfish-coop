"""Proof helpers for one portable guest world-start visit."""

import asyncio
import time
import uuid

from tools.silverfish import native_guest_clock, runtime
from tools.silverfish.control import current_status
from tools.silverfish.travel import OBSERVATORY


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


class GuestWorldStartProof:
    def __init__(self, guest):
        self.request = guest.bridge.directory / "portable-guest-world-start-request.tsv"
        self.log = guest.bridge.directory / "portable-guest-world-start-reply.tsv"

    @staticmethod
    def _resident_scene(frame):
        game = frame.get("game", {})
        scene = game.get("native_scene", {})
        return (frame.get("state") == "hosting" and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and game.get("map") == OBSERVATORY
                and scene.get("active_map") == OBSERVATORY and isinstance(scene.get("game_seconds"), (int, float))
                and scene["game_seconds"] > 0)

    @staticmethod
    def _resident_guest(frame):
        return (frame.get("state") == "connected" and frame.get("gameplay_fresh") is True
                and frame.get("link_connected") is True and frame.get("local_bridge") == "memory"
                and frame.get("local_status_source") == "memory" and frame.get("game", {}).get("map") == OBSERVATORY)

    async def arm(self, host, guest, character):
        baseline = await wait_resident_status(host.campaign / "run", self._resident_scene)
        self.session, self.epoch = baseline["generation"], baseline["zone_epoch"]
        self.seed = baseline["game"]["native_scene"]["instance_seed"]
        self.earliest_ms = round(baseline["game"]["native_scene"]["game_seconds"] * 1000)
        self.character = uuid.UUID(character).hex
        assert all(uuid.UUID(value).hex == value for value in (self.session, self.epoch, self.character))
        assert not self.request.exists() and not self.log.exists()
        runtime.write_text(self.request, f"{self.session}\t{self.epoch}\t{self.character}\n", newline="\n")
        self.armed_at = time.monotonic()

    async def joined(self, host, guest, pawn):
        guest_status = await wait_resident_status(guest.client / "run", self._resident_guest)
        host_status = await wait_resident_status(host.campaign / "run", self._resident_scene)
        assert (guest_status["host_session"], guest_status["zone_epoch"]) == (self.session, self.epoch)
        assert (host_status["generation"], host_status["zone_epoch"]) == (self.session, self.epoch)
        assert guest_status["game"]["save_scope"] == self.character
        receipt = guest_status["game"].get(native_guest_clock.WORLD_START)
        assert receipt and (receipt["session"], receipt["epoch"], receipt["map"], receipt["seed"]) == (
            self.session, self.epoch, OBSERVATORY, self.seed,
        ), f"Missing or incorrect typed pre-OpenLevel receipt: {receipt}"
        latest = host_status["game"]["native_scene"]
        assert latest["instance_seed"] == self.seed
        latest_ms = round(latest["game_seconds"] * 1000)
        assert self.earliest_ms - 2_000 <= receipt["milliseconds"] <= latest_ms + 2_000

        assert self.log.is_file(), "The exact stock Observatory day actor produced no private startup read"
        self.log_bytes = self.log.read_bytes()
        samples = {}
        for row in self.log_bytes.decode("ascii").splitlines():
            phase, session, epoch, area, slot, seed, milliseconds, step = row.split("\t")
            assert phase in {"begin-play-pre", "load-map-post"} and phase not in samples
            assert (session, epoch, area, slot) == (self.session, self.epoch, OBSERVATORY, f"SFCoopGuest_{self.character}")
            assert int(seed) == receipt["seed"]
            samples[phase] = {"seed": int(seed), "milliseconds": int(milliseconds), "travel_step": float(step)}
        assert samples
        phase = "begin-play-pre" if "begin-play-pre" in samples else "load-map-post"
        first = samples[phase]
        delta_ms = first["milliseconds"] - receipt["milliseconds"]
        if phase == "begin-play-pre":
            assert delta_ms == 0 and first["travel_step"] == 0, first
        else:
            assert 0 <= delta_ms <= 120_000, first

        self.receipt = receipt
        self.local_pawn = guest_status["game"]["pawns"]["local"]["name"]
        assert self.local_pawn
        self.evidence = {"receipt": receipt, "first_stock_read": phase, "stock_samples": samples,
                         "clock_advance_ms": delta_ms, "host_clock_window_ms": [self.earliest_ms, latest_ms],
                         "arm_to_connected_seconds": round(time.monotonic() - self.armed_at, 3),
                         "resident_memory_both_seats": True}
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
