"""Deliver fresh host time to the connected guest's stock clock widget."""

import time


FIXTURE = "native-guest-clock-fixture"
MAX_MILLISECONDS = 922_337_203_685_477


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
        snapshot = await client.bridge.command("native-guest-clock-apply", client.zone_epoch, str(self.sequence),
                                               area, str(milliseconds), timeout=2)
        proof = snapshot.get("native_guest_clock", {})
        fields = ("map", "pawn", "epoch", "sequence", "milliseconds")
        if (snapshot.get("state") != "connected" or snapshot.get("map") != area
                or tuple(proof.get(key) for key in fields) != (area, pawn, client.zone_epoch, self.sequence, milliseconds)):
            raise RuntimeError("The guest's stock clock did not verify the fresh host time")
        client.last_game = snapshot
