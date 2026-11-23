"""Telemetry and local controls for the isolated stock Bandit fixture."""

import math
import os
import time


MAP = "/Game/Classes/Maps/PlaytestMap_01"
ROW = "native-bandit"
HOST_ACTIONS = ("native-bandit-position", "native-bandit-spawn")
CLIENT_ACTION = "native-bandit-client-spawner-probe"


def parse_bandit(fields: list[str]) -> dict:
    if len(fields) not in (29, 37) or fields[:2] != [ROW, MAP] or fields[2] not in {"hosting", "connected"}:
        raise ValueError("Invalid native Bandit report")
    boolean_indices = (5, 12, 13, 15, 16, 19, 20, 21, 25, 26)
    if any(fields[index] not in {"0", "1"} for index in boolean_indices):
        raise ValueError("Invalid native Bandit Boolean field")
    authority_count, simulated_count, role, remote_role, spawner_denials, replica_denials = (
        int(fields[index]) for index in (7, 8, 10, 11, 27, 28)
    )
    if min(authority_count, simulated_count, spawner_denials, replica_denials) < 0 or not (
        -1 <= role <= 3 and -1 <= remote_role <= 3
    ):
        raise ValueError("Invalid native Bandit role or count")
    location = [float(value) for value in fields[22:25]]
    if any(not math.isfinite(value) or abs(value) > 100_000_000 for value in location):
        raise ValueError("Invalid native Bandit location")

    def name(index: int) -> str | None:
        return None if fields[index] == "-" else fields[index]

    report = {
        "map": fields[1], "phase": fields[2], "local_pawn": fields[3], "watched_pawn": name(4),
        "ok": fields[5] == "1", "status": fields[6], "authority_count": authority_count,
        "simulated_count": simulated_count, "npc": name(9), "role": role, "remote_role": remote_role,
        "replicates": fields[12] == "1", "replicate_movement": fields[13] == "1", "controller": name(14),
        "initialized": fields[15] == "1", "alive": fields[16] == "1", "combat_target": name(17),
        "spotted_actor": name(18), "perception_available": fields[19] == "1", "perceives_local": fields[20] == "1",
        "perceives_watched": fields[21] == "1", "location": location, "default_replicates": fields[25] == "1",
        "default_movement": fields[26] == "1", "spawner_denials": spawner_denials, "replica_ai_denials": replica_denials,
    }
    if len(fields) == 37:
        if fields[2] != "hosting" or fields[29] not in {"0", "1"}:
            raise ValueError("Invalid native Bandit hostility report")
        host_relation, guest_relation, host_hits, guest_hits = map(int, fields[31:35])
        host_loss, guest_loss = map(float, fields[35:37])
        if (not -1 <= host_relation <= 6 or not -1 <= guest_relation <= 6 or min(host_hits, guest_hits) < 0
                or any(not math.isfinite(loss) or loss < 0 for loss in (host_loss, guest_loss))):
            raise ValueError("Invalid native Bandit combat counter")
        report["combat"] = {
            "armed": fields[29] == "1", "status": fields[30],
            "host_relation": host_relation, "guest_relation": guest_relation,
            "host_hits": host_hits, "guest_hits": guest_hits,
            "host_health_lost": host_loss, "guest_health_lost": guest_loss,
        }
    return report


def _require_instrumented(game: dict, phase: str) -> dict:
    if os.environ.get("SILVERFISH_COOP_NATIVE_NPC_TEST") != "1" or game.get("map") != MAP or game.get("state") != phase:
        raise ValueError("The stock Bandit fixture requires an isolated Playtest session")
    report = game.get("native_bandit", {})
    if report.get("phase") != phase or not report.get("ok"):
        raise ValueError(f"The native Bandit observer is unavailable ({report.get('status', 'no report')})")
    return report


async def host_fixture(service, bridge, action: str, target: str | None) -> dict:
    from . import native_zone_lease

    before = _require_instrumented(service.last_game, "hosting")
    if action not in HOST_ACTIONS or not service.fixtures or set(service.roles()) != {"host", "guest"} or not before.get("watched_pawn"):
        raise ValueError("The stock Bandit fixture requires one enrolled guest and a fixture host")
    if before["status"] != "waiting_for_stock_bandit":
        raise ValueError("The stock Bandit fixture requires an empty Bandit baseline")
    if action == "native-bandit-position":
        if target not in {"host", "guest"}:
            raise ValueError("Choose which enrolled player to expose to the Bandit")
        arguments = (target,)
    else:
        if target is not None:
            raise ValueError("The stock Bandit spawn does not take a target")
        arguments = ()

    native_zone_lease.renew(bridge)
    snapshot = await bridge.command(action, *arguments, timeout=5)
    service.last_game, service.last_seen = snapshot, time.monotonic()
    return {"native_bandit": snapshot.get("native_bandit", {})}


async def client_fixture(service, action: str) -> dict:
    before = _require_instrumented(service.last_game, "connected")
    if action != CLIENT_ACTION or not service.transport.connected or before["status"] != "waiting_for_stock_bandit":
        raise ValueError("The Bandit spawner denial requires a connected client with no Bandit")
    snapshot = await service.bridge.command(action, timeout=5)
    service.last_game = snapshot
    report = snapshot.get("native_bandit", {})
    if report.get("spawner_denials", 0) <= before["spawner_denials"] or report.get("authority_count") != 0:
        raise RuntimeError("The stock client spawner denial was not verified in telemetry")
    return {"native_bandit": report}
