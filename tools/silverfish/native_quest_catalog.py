"""Parse the read-only host quest catalog from a single complete game bridge frame."""

from collections.abc import Iterable, Sequence
import re
from typing import TypedDict


_LIMITS = {"active": 64, "stock": 128, "board": 128}
_TOTAL_LIMIT = 1024
_ITEM_LIMIT = 8
_PAGE_SIZE = 8
_TAGS = {"native-quest-catalog": 6, "native-quest-entry": 9, "native-quest-reward-item": 5}
_REASON = re.compile(r"[a-z_]{1,64}\Z")
_ASSET = re.compile(r"/Game/[A-Za-z0-9_/-]+\.[A-Za-z0-9_-]+\Z")
_INTEGER = re.compile(r"(?:0|-?[1-9][0-9]{0,9})\Z")


class RewardItem(TypedDict):
    index: int
    class_path: str


class QuestEntry(TypedDict):
    state: str
    step: int
    final_step: int
    cash: int
    xp: int
    item_count: int
    items: list[RewardItem]


class CatalogSource(TypedDict):
    state: str
    total: int | None
    emitted: int
    reason: str
    entries: dict[str, QuestEntry]


class QuestCatalog(TypedDict):
    sequence: int
    active: CatalogSource
    stock: CatalogSource


class BoardCatalog(TypedDict):
    sequence: int
    board: CatalogSource


def _integer(text: str, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    if len(text) > 11 or not _INTEGER.fullmatch(text):
        raise ValueError("Invalid quest catalog integer")
    value = int(text)
    if not minimum <= value <= maximum:
        raise ValueError("Quest catalog integer is out of range")
    return value


def _quest_id(value: str) -> str:
    if not 0 < len(value) <= 96 or value.casefold() == "none" or not value.isprintable():
        raise ValueError("Invalid quest catalog ID")
    return value


def _summary(source: str, fields: Sequence[str]) -> CatalogSource:
    state, raw_total, raw_emitted, reason = fields[2:]
    if reason and not _REASON.fullmatch(reason):
        raise ValueError("Invalid quest catalog diagnostic")
    emitted = _integer(raw_emitted, maximum=_LIMITS[source])
    if state == "unavailable":
        if raw_total != "-" or emitted or not reason:
            raise ValueError("Unavailable quest catalog carries data")
        total = None
    elif state in ("ready", "partial"):
        total = _integer(raw_total, maximum=_TOTAL_LIMIT)
        if total < emitted:
            raise ValueError("Inconsistent quest catalog count")
    else:
        raise ValueError("Invalid quest catalog state")
    return {"state": state, "total": total, "emitted": emitted, "reason": reason, "entries": {}}


def _entry(source: str, fields: Sequence[str]) -> QuestEntry:
    state, raw_step, raw_final, raw_cash, raw_xp, raw_items = fields[3:]
    step = _integer(raw_step, minimum=-2_147_483_648)
    final_step = _integer(raw_final)

    if source == "stock":
        expected = "template"
    elif source == "board":
        expected = "offered"
    elif step == -10:
        expected = "completed"
    elif 0 < step <= final_step:
        expected = "ready" if step == final_step else "active"
    else:
        expected = "inactive"
    if state != expected:
        raise ValueError("Quest catalog progress disagrees with its stock step")

    return {"state": state, "step": step, "final_step": final_step, "cash": _integer(raw_cash),
            "xp": _integer(raw_xp), "item_count": _integer(raw_items), "items": []}


def _append_item(entry: QuestEntry, index_text: str, class_path: str) -> None:
    index = _integer(index_text, maximum=_ITEM_LIMIT - 1)
    items = entry["items"]
    if index >= entry["item_count"] or any(item["index"] == index for item in items):
        raise ValueError("Quest catalog reward index is out of range or duplicated")
    if len(class_path) > 256 or not _ASSET.fullmatch(class_path):
        raise ValueError("Invalid stock quest reward class")
    items.append({"index": index, "class_path": class_path})


def _verify(source: str, report: CatalogSource) -> None:
    entries = report["entries"]
    if report["emitted"] != len(entries):
        raise ValueError("Incomplete quest catalog frame")
    total = report["total"]
    if total is None:
        return

    for entry in entries.values():
        entry["items"].sort(key=lambda item: item["index"])
    if total > _LIMITS[source]:
        reason = "entry_limit"
    elif len(entries) != total:
        reason = "unqualified_entries"
    elif any(len(entry["items"]) != entry["item_count"] for entry in entries.values()):
        reason = "reward_summary_limited"
    else:
        reason = ""
    state = "partial" if reason else "ready"
    if report["state"] != state or report["reason"] != reason:
        raise ValueError("Quest catalog completeness disagrees with its summary")


def parse(rows: Iterable[Sequence[str]], *, sequence: int | None, board=False) -> QuestCatalog | BoardCatalog | None:
    """Read the requested catalog from one complete bridge frame."""
    reports: dict[str, CatalogSource] = {}
    seen: dict[str, set[str]] = {source: set() for source in _LIMITS}
    for fields in rows:
        if not fields or not isinstance(fields[0], str) or fields[0] not in _TAGS:
            continue
        tag = fields[0]
        if len(fields) != _TAGS[tag] or any(type(field) is not str for field in fields):
            raise ValueError("Invalid quest catalog wire row")
        source = fields[1]
        if source not in _LIMITS:
            raise ValueError("Unknown quest catalog source")
        if tag == "native-quest-catalog":
            if source in reports:
                raise ValueError("Duplicate quest catalog summary")
            reports[source] = _summary(source, fields)
            continue

        report = reports.get(source)
        if not report or report["state"] == "unavailable":
            raise ValueError("Quest catalog row has no qualified summary")
        quest_id = _quest_id(fields[2])
        entries = report["entries"]
        if tag == "native-quest-entry":
            identity = quest_id.casefold()
            if identity in seen[source] or len(entries) >= report["emitted"]:
                raise ValueError("Duplicate or excess quest catalog entry")
            seen[source].add(identity)
            entries[quest_id] = _entry(source, fields)
        else:
            entry = entries.get(quest_id)
            if entry is None:
                raise ValueError("Quest reward has no exact quest entry")
            _append_item(entry, fields[3], fields[4])

    if not reports:
        return None
    if reports.keys() != ({"board"} if board else {"active", "stock"}) or type(sequence) is not int or sequence < 1:
        raise ValueError("Quest catalog has no complete current bridge frame")
    for source, report in reports.items():
        _verify(source, report)
    if board:
        return {"sequence": sequence, "board": reports["board"]}
    return {"sequence": sequence, "active": reports["active"], "stock": reports["stock"]}


def page(catalog: QuestCatalog | BoardCatalog, selection: str | None, *, board=False) -> dict:
    if selection is None:
        selection = "active@0"
    if not isinstance(selection, str):
        raise ValueError("Choose active or stock and an optional quest catalog offset")
    source, separator, start = selection.partition("@")
    if source not in _LIMITS or source not in catalog or source == "board" and not board:
        raise ValueError("Choose active or stock for the quest catalog")
    offset = _integer(start, maximum=_LIMITS[source]) if separator else 0
    report = catalog[source]
    entries = sorted(report["entries"].items(), key=lambda row: row[0].casefold())
    rows = entries[offset:offset + _PAGE_SIZE]
    return {"source": source, "state": report["state"], "total": report["total"], "reported": report["emitted"],
            "reason": report["reason"], "sequence": catalog["sequence"], "offset": offset,
            "next_offset": offset + len(rows) if offset + len(rows) < len(entries) else None,
            "quests": [{"id": name, **entry} for name, entry in rows]}
