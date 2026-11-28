"""Decode the staged stock title-screen entry point."""

from pathlib import PureWindowsPath
import re


TAG = "native-title-entry"
KEY = "native_title_entry"
PANEL_TAG = "native-title-panel"
PANEL_KEY = "native_title_panel"
STARTUP_TAG = "embedded-engine-startup"
STARTUP_KEY = "embedded_engine_startup"
MAP = "/Game/Classes/Maps/MainMenu_P"
_REASON = re.compile(r"[a-z][a-z0-9_]{0,79}")


def _object(value: str, kind: str | None = None) -> str | None:
    if value == "-":
        return None
    label, space, path = value.partition(" ")
    if not space or not path or not value.isprintable() or len(value) > 512 or (kind and label != kind):
        raise ValueError("Unexpected stock title object name")
    return value


def parse(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) != 11 or (fields[0], fields[1], area) != (TAG, MAP, MAP) or phase not in ("ready", "opening"):
        raise ValueError("Unexpected stock title report")
    _, _, state, controller, widget, viewport, count, top, mounted, visible, reason = fields
    names = (_object(controller, "PlayerControler_FP_C"), _object(widget, "MainMenu_Widget_C"), _object(viewport))
    if (state not in ("ready", "waiting") or not re.fullmatch(r"-|0|[1-9][0-9]?", count)
            or any(value not in ("0", "1", "-") for value in (top, mounted, visible))
            or (reason != "-" and not _REASON.fullmatch(reason))):
        raise ValueError("Unexpected stock title state")
    if state == "ready" and (not all(names) or count in ("0", "-") or (top, mounted, visible, reason) != ("1", "1", "1", "-")):
        raise ValueError("The stock title entry is not ready")
    return dict(map=area, state=state, controller=names[0], widget=names[1], viewport=names[2],
                menu_stack=None if count == "-" else int(count), top=None if top == "-" else top == "1",
                mounted=None if mounted == "-" else mounted == "1", visible=None if visible == "-" else visible == "1",
                reason=None if reason == "-" else reason)


def parse_panel(fields: list[str], phase: str, area: str) -> dict:
    if len(fields) != 6 or (fields[0], fields[1], phase, area) != (PANEL_TAG, MAP, "ready", MAP):
        raise ValueError("Unexpected co-op title panel report")
    _, _, state, actions, mounted, reason = fields
    if (state not in ("ready", "hidden", "pending", "unavailable") or actions not in ("host", "join", "host,join")
            or mounted not in ("0", "1") or (reason != "-" and not _REASON.fullmatch(reason))
            or (state == "ready" and (mounted != "1" or reason != "-"))):
        raise ValueError("Unexpected co-op title panel state")
    return dict(map=area, state=state, actions=actions.split(","), mounted=mounted == "1",
                reason=None if reason == "-" else reason)


def parse_startup(fields: list[str], snapshot: dict) -> dict:
    if (len(fields) != 4 or fields[:2] != [STARTUP_TAG, MAP]
            or (snapshot.get("mode"), snapshot.get("state"), snapshot.get("map")) != ("idle", "ready", MAP)
            or snapshot.get(KEY, {}).get("state") != "ready"
            or not 0 < snapshot.get("sequence", 0) < 2**53 or fields[2] != str(snapshot["sequence"])):
        raise ValueError("Unexpected embedded engine startup frame")
    directory = fields[3]
    if not directory.isprintable() or len(directory) > 4096 or not PureWindowsPath(directory).is_absolute():
        raise ValueError("Invalid effective Unreal save directory")
    return dict(map=MAP, sequence=snapshot["sequence"], saved_directory=directory)


def parse_native_startup(revision: int, text: str) -> dict:
    rows = [line.split("\t") for line in text.splitlines()]
    if (len(rows) != 5 or rows[0] != ["schema", "1"] or rows[1] != ["seq", str(revision)]
            or rows[2] != ["session", "idle", "ready", MAP, "0", "0"]):
        raise ValueError("Malformed native startup snapshot")
    snapshot = dict(schema=1, sequence=revision, mode="idle", state="ready", map=MAP,
                    remote_clients=0, server_connection=False)
    snapshot[KEY] = parse(rows[3], "ready", MAP)
    snapshot[STARTUP_KEY] = parse_startup(rows[4], snapshot)
    return snapshot
