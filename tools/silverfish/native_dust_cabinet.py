"""Observe and project the exact stock cabinets shielding authored pickups."""

from dataclasses import dataclass
import re
import time

from .travel import NORTHEAST_RAIL, RAIL_YARD


@dataclass(frozen=True)
class Cabinet:
    area: str
    kind: str
    source: str
    actor: str
    pickup_location: tuple[float, float, float]

    @property
    def tag(self):
        return f"native-{self.kind}-cabinet"

    @property
    def key(self):
        return f"native_{self.kind}_cabinet"

    @property
    def fixture(self):
        return self.tag + "-stock-fixture"


DUST = Cabinet(RAIL_YARD, "dust", "railyard-dust-filter",
               f"LootCabnet_Small_BP_C {RAIL_YARD}.RailYard_P:PersistentLevel.LootCabnet_Small_BP_C_36",
               (-8423.943177, -30074.585423, 264.038044))
MOLASSES = Cabinet(NORTHEAST_RAIL, "molasses", "northeast-rail-molasses",
                   f"LootCabnet_Large_BP_C {NORTHEAST_RAIL}.NorthEast_Rail_P:PersistentLevel.LootCabnet_Large_BP_C_4",
                   (-16087.482436, 5321.048926, 982.455042))
PROFILES = {profile.area: profile for profile in (DUST, MOLASSES)}
FIXTURES = frozenset(profile.fixture for profile in PROFILES.values())
SOURCES = {profile.source: profile for profile in PROFILES.values()}
STATUS_KEYS = {profile.tag: profile.key for profile in PROFILES.values()}
ACTOR, KEY, FIXTURE, PROJECT = DUST.actor, DUST.key, DUST.fixture, DUST.tag + "-project"
FRAME_KEY = DUST.key + "_frame"
_EPOCH = re.compile(r"[0-9a-f]{32}")
_PAWNS = {area: re.compile(re.escape(f"Player_FP_C {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.Player_FP_C_") + r"[0-9]+")
          for area in PROFILES}
_STATES = {"hosting": {"closed", "opened"}, "connected": {"closed", "projecting", "projected"}}


def valid_epoch(epoch) -> bool:
    return isinstance(epoch, str) and epoch != "0" * 32 and _EPOCH.fullmatch(epoch) is not None


def frame_key(area: str) -> str:
    return PROFILES[area].key + "_frame"


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) not in (6, 7):
        raise ValueError("Invalid stock cabinet report size")
    tag, state, report_map, actor, pawn, epoch = fields[:6]
    profile = PROFILES.get(area)
    doors = fields[6] if len(fields) == 7 else None
    projected = state in ("projecting", "projected")
    if (not profile or tag != profile.tag or report_map != area or actor != profile.actor
            or len(pawn) > 512 or _PAWNS[area].fullmatch(pawn) is None or state not in _STATES.get(phase, ())
            or (not valid_epoch(epoch) if projected else epoch != "-")
            or (doors is not None and doors not in (("closed",) if state == "closed" else ("open", "unverified")))):
        raise ValueError("Invalid stock cabinet scope")
    return {"state": state, "map": area, "actor": actor, "pawn": pawn, "epoch": epoch if projected else None,
            "doors": doors}


def observed(game: dict, state: str) -> dict:
    area = game.get("map")
    profile = PROFILES.get(area)
    if not profile:
        return {}
    report = game.get(profile.key, {})
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if ((report.get("map"), report.get("actor"), report.get("state"), report.get("pawn")) ==
            (area, profile.actor, state, pawn) and isinstance(pawn, str) and _PAWNS[area].fullmatch(pawn)):
        return report
    return {}


def frame(host) -> dict:
    if (host.native_zones and not host.stopping and not host.transitioning and not host.native_checkpoint_unsafe
            and host.last_game.get("state") == "hosting" and observed(host.last_game, "opened")
            and valid_epoch(host.zone_epoch) and time.monotonic() - host.last_seen < 2.5):
        profile = PROFILES[host.last_game["map"]]
        return {"map": profile.area, "actor": profile.actor, "epoch": host.zone_epoch, "state": "opened"}
    return {}


async def open_fixture(host, bridge, action=FIXTURE) -> dict:
    game = host.last_game
    area = game.get("map")
    profile = PROFILES.get(area)
    pawn = game.get("pawns", {}).get("local", {}).get("name")
    if (not host.fixtures or not host.native_zones or host.stopping or host.transitioning
            or host.native_checkpoint_unsafe or game.get("state") != "hosting" or not profile or action != profile.fixture
            or not isinstance(pawn, str) or _PAWNS[area].fullmatch(pawn) is None or time.monotonic() - host.last_seen >= 2.5):
        raise ValueError("The headless host stock cabinet fixture is unavailable")
    if host.party.get("players", {}).get("host", {}).get("downed") or observed(game, "opened"):
        raise ValueError("The stock cabinet requires a standing host and an unopened cabinet")
    await bridge.command(action, pawn, timeout=5)
    snapshot = await bridge.wait_for(lambda s: s.get("state") == "hosting" and observed(s, "opened").get("pawn") == pawn, 5)
    host.last_game, host.last_seen = snapshot, time.monotonic()
    proof = observed(snapshot, "opened")
    host.control.event(f"stock_{profile.kind}_cabinet_opened", **proof)
    return {profile.key: proof}


async def project_client(client) -> None:
    game, epoch = client.last_game, client.zone_epoch
    area = game.get("map")
    profile = PROFILES.get(area)
    if not profile:
        return
    report = client.gameplay.get(frame_key(area))
    if (not valid_epoch(epoch) or client.needs_travel or client.server_transitioning or not client.host_session
            or time.monotonic() - client.gameplay_seen >= 2.5 or game.get("state") != "connected"
            or report != {"map": area, "actor": profile.actor, "epoch": epoch, "state": "opened"}):
        return
    current = observed(game, "closed")
    if not current:
        return
    result = await client.bridge.command(profile.tag + "-project", epoch, profile.actor, current["pawn"], timeout=3)
    accepted = observed(result, "projecting") or observed(result, "projected")
    if result.get("state") != "connected" or (accepted.get("pawn"), accepted.get("epoch")) != (current["pawn"], epoch):
        raise RuntimeError("The quiet stock cabinet projection did not verify the exact local visit")
    client.last_game = result
