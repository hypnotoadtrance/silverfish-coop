"""Exact Black Box recording and canonical completion flag."""

QUEST = "Clct_Radio_Scar"
PREFIX = "native-black-box"
KEY = "native_black_box"
COMPLETION_TAG = "native-black-box-completion"
COMPLETION_FLAG = "Simmi_BlackBox"
MAP = "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P"
SOURCE = "gorge-insurrectionist-recorder"
CLASS = "/Game/Classes/Items/Key_Items/Item_InniRadio_01.Item_InniRadio_01_C"
ACTOR = f"Item_InniRadio_01_C {MAP}.Gorge_01_P:PersistentLevel.Item_InniRadio_01_C_3"


def parse_completion(fields: list[str]) -> dict | None:
    if (len(fields) != 6 or fields[:2] != [COMPLETION_TAG, COMPLETION_FLAG]
            or any(value not in ("0", "1") for value in fields[2:])):
        return None
    before, before_found, after, after_found = (int(value) for value in fields[2:])
    if before > before_found or after > after_found:
        return None
    return {"before": before, "before_found": bool(before_found), "after": after, "after_found": bool(after_found)}


def valid_completion(proof: dict) -> bool:
    flag = proof.get("completion")
    if not isinstance(flag, dict):
        return False
    before = flag.get("before"), flag.get("before_found")
    after = flag.get("after"), flag.get("after_found")
    if any(type(value) is not int or value not in (0, 1) or type(found) is not bool or value > found
           for value, found in (before, after)):
        return False
    if before[0] != int(proof["step_before"] == -10) or after[0] != int(proof["step_after"] == -10):
        return False
    if proof["operation"] == f"{PREFIX}-turn-in":
        return proof["step_before"] in (1, 2) and before[0] == 0 and after == (1, True)
    return before == after
