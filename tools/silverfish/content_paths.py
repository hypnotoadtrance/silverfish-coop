"""Validate cooked Blueprint identities carried by the shared item paths."""

import re


_ITEM = re.compile(r"/Game/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.\1_C")


def item_class_name(path: object) -> str | None:
    if isinstance(path, str) and len(path) <= 256 and (match := _ITEM.fullmatch(path)):
        return match[1] + "_C"
    return None
