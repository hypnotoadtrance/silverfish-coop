"""Read the Observatory lamp's movement for the stock physics fixture."""

from .travel import OBSERVATORY


TAG = "native-observatory-lantern"
KEY = "native_observatory_lantern"
FIXTURE = "native-observatory-lantern-fixture"
ACTOR = f"Prop_Lantern_01_C {OBSERVATORY}.Observitory_P:PersistentLevel.Prop_Lantern_01_C_1"


def parse(fields, phase, area):
    if (len(fields) != 9 or phase not in ("hosting", "connected") or area != OBSERVATORY
            or fields[:3] != [TAG, OBSERVATORY, ACTOR] or any(value not in ("0", "1") for value in fields[3:6])):
        raise ValueError("Invalid Observatory lantern report")
    position = list(map(float, fields[6:]))
    if any(not -1e7 <= axis <= 1e7 for axis in position):
        raise ValueError("Invalid Observatory lantern position")
    lit, held, movement = (value == "1" for value in fields[3:6])
    return dict(map=area, actor=ACTOR, lit=lit, held=held, movement=movement, position=position)
