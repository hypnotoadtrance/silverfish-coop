"""Validate cooked Blueprint identities carried by the shared item paths."""

import re

from .native_map_preflight import valid_campaign_package


_ITEM = re.compile(r"/Game/(?:[A-Za-z0-9_-]+/)*([A-Za-z0-9_-]+)\.\1_C")


def item_class_name(path: object) -> str | None:
    if isinstance(path, str) and len(path) <= 256 and (match := _ITEM.fullmatch(path)):
        return match[1] + "_C"
    return None


def named_actor(area: object, name: object, actor_class: str) -> bool:
    if not valid_campaign_package(area) or not isinstance(name, str):
        return False
    leaf = area.rsplit("/", 1)[-1]
    prefix = f"{actor_class} {area}.{leaf}:PersistentLevel.{actor_class}_"
    return name.startswith(prefix) and re.fullmatch(r"[0-9]{1,10}", name[len(prefix):]) is not None
