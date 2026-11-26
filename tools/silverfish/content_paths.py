"""Validate cooked Blueprint identities carried by the shared item paths."""

import re

from .native_map_preflight import valid_campaign_package


_ITEM = re.compile(r"/(?:[A-Za-z0-9_-]+/)+([A-Za-z0-9_-]+)\.\1_C")
_ASSET = re.compile(r"/(?:[A-Za-z0-9_-]+/)+([A-Za-z0-9_-]+)\.\1")
_QUEST = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}")


def quest_id(value: object) -> bool:
    return isinstance(value, str) and _QUEST.fullmatch(value) is not None and value.lower() != "none"


def item_class_name(path: object, limit: int = 256) -> str | None:
    if isinstance(path, str) and len(path) <= limit and (match := _ITEM.fullmatch(path)):
        return match[1] + "_C"
    return None


def asset_name(path: object) -> str | None:
    if isinstance(path, str) and len(path) <= 256 and (match := _ASSET.fullmatch(path)):
        return match[1]
    return None


def named_actor(area: object, name: object, actor_class: str) -> bool:
    if not valid_campaign_package(area) or not isinstance(name, str):
        return False
    leaf = area.rsplit("/", 1)[-1]
    prefix = f"{actor_class} {area}.{leaf}:PersistentLevel.{actor_class}_"
    return name.startswith(prefix) and re.fullmatch(r"[0-9]{1,10}", name[len(prefix):]) is not None
