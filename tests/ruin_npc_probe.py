"""Private read-only census for the stock Ruin Fields director route."""

import asyncio
from contextlib import contextmanager
import math
from pathlib import Path
import shutil
import uuid

from tools.silverfish import runtime


RUIN = "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P"
RUIN_LEVEL = RUIN + ".RuinFeilds_P:PersistentLevel."
SUPPORTED = {"NPC_Zomb_01_C", "NPC_WildBoar_C"}
UNSUPPORTED = {"NPC_FeralDog_C", "NPC_Survivor_FlowerLady_C", "NPC_MinorStag_C", "NPC_Doe_C",
               "NPC_Survivor_Worker_C", "NPC_Homunculus_C"}


@contextmanager
def installed_probe(work: Path):
    mods = work / "stage" / runtime.SHIPPING.parent / "ue4ss/Mods"
    if "steamapps" in {part.casefold() for part in work.parts} or mods.resolve(strict=True) != mods:
        raise ValueError("Use an ordinary mod directory in a private staged replica")
    suite = mods / "SilverfishRuinNpcProbe"
    suite.mkdir()
    scripts, enabled = suite / "Scripts", suite / "enabled.txt"
    script = scripts / "main.lua"
    try:
        scripts.mkdir()
        shutil.copyfile(Path(__file__).with_name("fixtures") / "ruin_npc_probe.lua", script)
        enabled.touch(exist_ok=False)
        yield
    finally:
        if suite.resolve(strict=True) != suite or scripts.resolve() != scripts:
            raise RuntimeError("The isolated NPC fixture path changed")
        enabled.unlink(missing_ok=True)
        script.unlink(missing_ok=True)
        if scripts.exists():
            scripts.rmdir()
        suite.rmdir()


def decode(lines, viewer):
    if lines[0] != viewer:
        raise AssertionError(f"Unexpected Ruin NPC census seat {lines[0]}")
    actors = {}
    for line in lines[1:]:
        kind, name, role, replicates, alive, x, y, z = line.split("\t")
        point = tuple(map(float, (x, y, z)))
        prefix = f"{kind} {RUIN_LEVEL}"
        assert kind in SUPPORTED | UNSUPPORTED and name.startswith(prefix) and name not in actors, line
        assert role in ("1", "2", "3") and replicates in ("true", "false") and alive in ("true", "false"), line
        assert all(map(math.isfinite, point)), line
        actors[name] = {"class": kind, "role": int(role), "replicates": replicates == "true",
                        "alive": alive == "true", "position": point}
    return actors


def read_director_trace(run: Path, viewer: str):
    """Read the bounded exact native decisions carried in ordinary status frames."""
    paths = tuple((run / "game-bridge").glob("*/status.tsv"))
    if len(paths) != 1:
        return {}
    try:
        lines = paths[0].read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return {}
    trace = {}
    for line in lines:
        fields = line.split("\t")
        if fields[0] != "native-campaign-npc-ruin-director" or fields[1:3] != [RUIN, viewer]:
            continue
        step, kind, stock_day, is_day, only_track_time, ticks, stage, marker, asset = fields[3:]
        assert step in ("initial", "refresh") and kind in ("pool", "draw", "supported"), fields
        assert all(value in ("-", "0", "1") for value in (stock_day, is_day, only_track_time)), fields
        if kind == "pool":
            assert (stage, marker, asset) == ("stock_pool_decision", "-", "-"), fields
        else:
            assert marker.startswith(f"AI_Spawner_Squad_BP_C {RUIN_LEVEL}"), fields
        tick_value = None if ticks == "-" else int(ticks)
        assert tick_value is None or -(1 << 63) <= tick_value < (1 << 63), fields
        decisions = trace.setdefault(step, {})
        assert kind not in decisions, fields
        decisions[kind] = {
            "stock_is_day_argument": stock_day, "sun_controller_is_day": is_day,
            "sun_controller_only_track_time": only_track_time, "raw_game_time_ticks": tick_value,
            "stage": stage, "marker": marker, "asset": asset,
        }
    return trace


async def capture(run: Path, viewer: str):
    bridge, = (run / "game-bridge").iterdir()
    ticket = uuid.uuid4().hex
    request = bridge / "ruin-npc-command.tsv"
    reply = bridge / "ruin-npc-reply.tsv"
    runtime.write_text(request, ticket + "\n", newline="")
    async with asyncio.timeout(5):
        while True:
            try:
                lines = reply.read_text(encoding="ascii").splitlines()
            except (FileNotFoundError, PermissionError):
                lines = []
            if len(lines) >= 3 and lines[0] == ticket and lines[-1] == "end":
                return decode(lines[1:-1], viewer)
            await asyncio.sleep(.05)


def compare(host, guest, required):
    """Match the received set uniquely, including every member of a qualified stock receipt."""
    authority = {name: row for name, row in host.items() if row["class"] in SUPPORTED and row["replicates"]}
    observed = {name: row for name, row in guest.items() if row["class"] in SUPPORTED}
    assert all(row["role"] == 3 for row in authority.values()), authority
    assert all(row["role"] == 1 for row in observed.values()), observed
    unsupported = {side: {name: row for name, row in actors.items() if row["class"] in UNSUPPORTED}
                   for side, actors in (("host", host), ("guest", guest))}

    def distance(owner, observer):
        if (owner["class"], owner["alive"]) != (observer["class"], observer["alive"]):
            return math.inf
        return math.dist(owner["position"], observer["position"])

    pairs = {}
    for name, remote in observed.items():
        eligible = {local: distance(row, remote) for local, row in authority.items()}
        close = {local: cm for local, cm in eligible.items() if cm <= 300}
        if name in close:
            local = name
        elif len(close) == 1:
            local, = close
            if sum(distance(authority[local], row) <= 300 for row in observed.values()) != 1:
                raise ValueError(f"Ambiguous spatial match for {name}")
        else:
            raise ValueError(f"No unique nearby authority match for {name}")
        if local in pairs:
            raise ValueError(f"Authority matched by two guest replicas: {local}")
        pairs[local] = {"replica": name, "distance_cm": close[local], "same_name": local == name}

    if not required or not required <= pairs.keys():
        raise ValueError("Some published members are outside the guest's observed replica set")
    return {"matched": pairs, "unobserved_authorities": sorted(authority.keys() - pairs.keys()),
            "unsupported": unsupported, "identity_basis": "exact UObject name or unambiguous class/position, no NetGUID"}
