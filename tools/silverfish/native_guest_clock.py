"""Deliver fresh host time to the connected guest's stock clock widget."""

import re
import time


FIXTURE = "native-guest-clock-fixture"
APPLY = "native-guest-clock-apply"
REPLY = "native-guest-clock-reply"
KEY = "native_guest_clock"
WORLD_START = "native_guest_world_start"
STATIC_WORLD_SEED = "static_world_seed"
MAX_MILLISECONDS = 922_337_203_685_477


def valid_static_seed(value):
    return value is None or (type(value) is int and -2_147_483_648 <= value <= 2_147_483_647)


def parse_static_seed(value):
    if value == "-":
        return None
    if not re.fullmatch(r"-?[0-9]{1,10}", value) or not valid_static_seed(result := int(value)):
        raise ValueError("Invalid static world seed")
    return result


def parse_report(fields):
    _, area, pawn, epoch, sequence, milliseconds, phase, calls, original = fields
    return {"map": area, "pawn": pawn, "epoch": epoch, "sequence": int(sequence),
            "milliseconds": int(milliseconds), "state": phase, "calls": int(calls),
            "original_client_milliseconds": None if original == "-" else int(original)}


class GuestClockView:
    def __init__(self):
        self.sequence = 0
        self.next_refresh = 0.0

    async def sync(self, client):
        now = time.monotonic()
        if (now < self.next_refresh or now - client.gameplay_seen >= 1.5
                or not client.zone_epoch or not client.host_session):
            return
        game = client.last_game
        area = game.get("map")
        pawn = game.get("pawns", {}).get("local", {}).get("name")
        scene = client.gameplay.get("host_world", {}).get("native_scene", {})
        seconds = scene.get("game_seconds")
        if (game.get("state") != "connected" or not pawn or not area or area != scene.get("active_map")
                or type(seconds) not in (float, int)
                or not 0 <= seconds <= MAX_MILLISECONDS / 1000):
            return
        milliseconds = round(seconds * 1000)
        if milliseconds > MAX_MILLISECONDS:
            return
        self.sequence += 1
        self.next_refresh = now + .65
        snapshot = await client.bridge.command(APPLY, client.zone_epoch, str(self.sequence),
                                               area, str(milliseconds), timeout=2)
        proof = snapshot.get(KEY, {})
        fields = ("map", "pawn", "epoch", "sequence", "milliseconds")
        if (snapshot.get("state") != "connected" or snapshot.get("map") != area
                or tuple(proof.get(key) for key in fields) != (area, pawn, client.zone_epoch, self.sequence, milliseconds)):
            raise RuntimeError("The guest's stock clock did not verify the fresh host time")
        if (sequence := snapshot.get("native_guest_clock_snapshot_sequence")) is None:
            client.last_game = snapshot
            return
        latest = client.last_game
        if (latest.get("sequence", 0) <= sequence
                and (latest.get("state"), latest.get("map"), latest.get("pawns", {}).get("local", {}).get("name"))
                == ("connected", area, pawn)):
            client.last_game = {**latest, KEY: proof}
