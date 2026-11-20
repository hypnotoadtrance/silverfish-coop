"""Decode the staged stock title-screen entry point."""

import re


TAG = "native-title-entry"
KEY = "native_title_entry"
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
