"""Regenerate native and Lua stock container profiles from the cooked catalog."""

import argparse
import json
from pathlib import Path
import re

from .authored_container_placements import TWO_SLOT_PLACEMENTS
from .authored_loot import (CRATE_PROFILES, GUARANTEED_REPLENISHMENT, OFFICE_DESK_CHILD_SALTS,
                            OFFICE_DESK_PROFILE, SMALL_CABINET_CHILD_SALTS, SMALL_CABINET_PROFILE, full_name)
from .authored_routes import MAPS as EXPANDED_MAPS
from .travel import AUTHORED_MAPS


QUALIFIED_MAPS = AUTHORED_MAPS | EXPANDED_MAPS
PLACED_ACTOR = re.compile(r"LootCrate_Small_BP_C_[0-9]+\Z")
OVERRIDE_ROWS = {"Cargo", "Coup_Gear", "Industrial", "Millitary_Common", "Rural"}
GUARANTEED_OVERRIDE_ROWS = OVERRIDE_ROWS | {"Common_Special", "FishCannery", "Luxury"}
PERMANENT_REPLENISHMENT = "RespawnSetting_Enum::NewEnumerator2"
BASE_FIELDS = {"Loot_Spawner": "Loot_Spawner", "Loot_Spawner1": "Loot_Spawner1", "RootComponent": "DefaultSceneRoot"}
SurveyProfile = tuple[int, str | None] | tuple[int, str | None, int | None]
FAMILY_CATALOG = "tools/silverfish/authored_container_placements.py"
TWO_SLOT_CLASSES = {
    "LootCrate_Cult_Small_BP_C": ("cult_crate", 0x376, True, False, None),
    "Loot_Stash_Small_BP_C": ("loot_stash", 0x376, False, True, PERMANENT_REPLENISHMENT),
    "Loot_Trunk_BP_C": ("trunk", 0x376, False, False, None),
    "Loot_SuitCase_BP_C": ("suitcase", 0x3A3, False, False, None),
}
TWO_SLOT_SOURCE_FIELDS = BASE_FIELDS | {"Box": "Box", "SM_Prop_Cabinet_Small_Door_01": "SM_Prop_Cabinet_Small_Door_01"}
TWO_SLOT_SAVE_FIELDS = {"SpawnSalt", "Loot_Override", "GuaranteeSpawn", "Replenish_Mode"}
BOX_GEOMETRY_FIELDS = {"Arrow", "BlueprintCreatedComponents", "DefaultSceneRoot", "SM_Prop_Cabinet_Small_02", "StreamSeed"}
OPEN_POSE = "MoveComponentTo(SM_Prop_Cabinet_Small_Door_01,SM_Prop_Cabinet_Small_Door_01.RelativeLocation,(-95, 0, 0),"
STOCK_ROLL = ("CallFunc_Array_Get_Item.SpawnLoot(Loot Table_Items,SpawnChance_Item,0.5,GuaranteeSpawn,"
              "False,True,False,False,False,CallFunc_SpawnLoot_Object)")


def survey(report: str) -> tuple[set[str], set[tuple[str, str]], dict[tuple[str, str], SurveyProfile]]:
    """Read exact persistent-level crate rows from the local cooked-map audit."""
    area = None
    maps, placed, profiles = set(), set(), {}
    for line in report.splitlines():
        if line.startswith("PACKAGE "):
            package = line.removeprefix("PACKAGE ").replace("\\", "/")
            if not re.fullmatch(r"[A-Za-z0-9_]+/[A-Za-z0-9_]+\.umap", package):
                raise ValueError("Invalid package header in authored crate survey")
            area = "/Game/Classes/Maps/" + package.removesuffix(".umap")
            if area in maps:
                raise ValueError("Duplicate package in authored crate survey")
            maps.add(area)
        elif line.startswith("LootCrate_Small_BP_C LootCrate_Small_BP_C_"):
            _, actor, raw = line.split(" ", 2)
            key = area, actor
            if area is None or not PLACED_ACTOR.fullmatch(actor) or key in placed:
                raise ValueError("Invalid or duplicate placement in authored crate survey")
            placed.add(key)
            reported = raw.split("; ")
            fields = dict(pair.split("=", 1) for pair in reported)
            if len(reported) != len(fields):
                raise ValueError("Duplicate field in authored crate survey")
            required = BASE_FIELDS.keys() | {"SpawnSalt"}
            guaranteed = fields.get("GuaranteeSpawn") == "True"
            allowed = required | {"Loot_Override"}
            if guaranteed:
                allowed |= {"GuaranteeSpawn", "Replenish_Mode"}
            replenish = fields.get("Replenish_Mode")
            if not required <= fields.keys() <= allowed or replenish not in (None, PERMANENT_REPLENISHMENT) or any(
                    fields[name] != value for name, value in BASE_FIELDS.items()):
                continue
            row = None
            if "Loot_Override" in fields:
                match = re.fullmatch(r"DataTableRowHandle\(([A-Za-z0-9_]+)\)", fields["Loot_Override"])
                if not match or match[1] not in (GUARANTEED_OVERRIDE_ROWS if guaranteed else OVERRIDE_ROWS):
                    continue
                row = match[1]
            salt = int(fields["SpawnSalt"])
            if not -2**31 <= salt < 2**31:
                raise ValueError("Invalid SpawnSalt in authored crate survey")
            if area in QUALIFIED_MAPS:
                profiles[key] = (salt, row, 2 if replenish else None) if guaranteed else (salt, row)
    return maps, placed, profiles


def family_placements(audit):
    visited = {"/Game/Classes/" + path.removesuffix(".umap") for path in audit["visited"]}
    if missing := sorted(QUALIFIED_MAPS - visited):
        raise ValueError("Cooked container audit is missing campaign maps: " + ", ".join(missing))
    for family, (_, entry, ping, guaranteed, mode) in TWO_SLOT_CLASSES.items():
        stock = audit["families"][family]
        defaults = {export["name"]: export["fields"] for export in stock["defaults"]}
        actor = defaults[f"Default__{family}"]
        script = f"ExecuteUbergraph_{family[:-2]}"
        functions = {function["name"]: function["calls"] for function in stock["functions"]}
        calls = [call.split(" ", 1)[1] for call in functions[script]]
        if (functions["SendInteractionToActor"] != [f"24 {script}({entry})"]
                or sum(call == STOCK_ROLL for call in calls) != 1
                or sum(call.startswith(OPEN_POSE) for call in calls) != 1
                or "Box.K2_DestroyComponent(Self)" not in calls
                or {name for name in defaults if name.startswith("Loot_Spawner")} !=
                {"Loot_Spawner_GEN_VARIABLE", "Loot_Spawner1_GEN_VARIABLE"}
                or actor.get("Ping_IfOpened", "False") != str(ping)
                or actor.get("GuaranteeSpawn", "False") != str(guaranteed) or actor.get("Replenish_Mode") != mode):
            raise ValueError(f"Cooked container family does not match the stock two-slot interaction: {family}")

    profiles, seen = [], set()
    for row in audit["placements"]:
        family, package, actor = row["type"], row["area"], row["name"]
        if family not in TWO_SLOT_CLASSES:
            continue
        if not re.fullmatch(r"Maps/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.umap", package):
            raise ValueError(f"Invalid stock container package {package}")
        area = "/Game/Classes/" + package.removesuffix(".umap")
        if area not in QUALIFIED_MAPS:
            continue
        fields = row["fields"]
        if any(fields.get(key) != value for key, value in TWO_SLOT_SOURCE_FIELDS.items()):
            continue
        if fields.keys() - TWO_SLOT_SOURCE_FIELDS.keys() - TWO_SLOT_SAVE_FIELDS - BOX_GEOMETRY_FIELDS:
            continue
        if (area, actor) in seen or not re.fullmatch(r"[A-Za-z0-9_]+_C_[0-9]+", actor):
            raise ValueError(f"Invalid or duplicate stock container {area} {actor}")
        seen.add((area, actor))
        default_guarantee = TWO_SLOT_CLASSES[family][3]
        guarantee = fields.get("GuaranteeSpawn", str(default_guarantee))
        mode = fields.get("Replenish_Mode")
        if guarantee not in {"True", "False"} or mode not in {None, PERMANENT_REPLENISHMENT}:
            continue
        handle = fields.get("Loot_Override")
        override = None
        if handle is not None:
            match = re.fullmatch(r"DataTableRowHandle\(([A-Za-z0-9_]+)\)", handle)
            if not match or match[1] not in GUARANTEED_OVERRIDE_ROWS:
                continue
            override = match[1]
        salt = int(fields["SpawnSalt"])
        if not -2**31 <= salt < 2**31:
            raise ValueError(f"Invalid stock container salt {area} {actor}")
        profiles.append((family, area, actor, salt, override, guarantee == "True", 2 if mode else None))
    return tuple(sorted(profiles))


def family_catalog(profiles):
    lines = ["# Generated by tools.silverfish.generate_authored_loot_registry from the cooked container audit.",
             "TWO_SLOT_PLACEMENTS = ("]
    for row in profiles:
        lines.append(f"    {row!r},")
    return "\n".join((*lines, ")", ""))


def outputs(families=TWO_SLOT_PLACEMENTS) -> dict[str, str]:
    if (not CRATE_PROFILES or len(CRATE_PROFILES) != len({(area, actor) for area, actor, *_ in CRATE_PROFILES})
            or any(mode not in (None, 2) for mode in GUARANTEED_REPLENISHMENT.values())):
        raise ValueError("Invalid authored crate placements")
    for area, actor, salt, row in CRATE_PROFILES:
        allowed_rows = GUARANTEED_OVERRIDE_ROWS if (area, actor) in GUARANTEED_REPLENISHMENT else OVERRIDE_ROWS
        if (area not in QUALIFIED_MAPS or not PLACED_ACTOR.fullmatch(actor) or type(salt) is not int
                or not -2**31 <= salt < 2**31 or (row is not None and row not in allowed_rows)):
            raise ValueError(f"Invalid authored crate placement {area} {actor}")
    header = [
        "#pragma once", "",
        "// Generated by python -m tools.silverfish.generate_authored_loot_registry", "",
        "#include <array>", "#include <cstdint>", "#include <String/StringType.hpp>", "",
        "namespace silverfish::authored_loot", "{",
        "    struct StockClass", "    {",
        "        const RC::CharType* actor_class;", "        const RC::CharType* input;",
        "        const RC::CharType* script;", "        std::int32_t input_entry;", "        std::uint8_t slots;",
        "        bool stock_ping{};", "    };", "",
        '    inline constexpr StockClass small_crate{',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:SendInteractionToActor"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:ExecuteUbergraph_LootCrate_Small_BP"),',
        '        0x376, 2};',
        '    inline constexpr StockClass small_cabinet{',
        '        STR("/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C:SendInteractionToActor"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCabnet_Small_BP.LootCabnet_Small_BP_C:ExecuteUbergraph_LootCabnet_Small_BP"),',
        '        0x438, 4};',
        '    inline constexpr StockClass office_desk{',
        '        STR("/Game/Classes/Actors/Lootables/LootCabinet_Desk_BP.LootCabinet_Desk_BP_C"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCabinet_Desk_BP.LootCabinet_Desk_BP_C:SendInteractionToActor"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCabinet_Desk_BP.LootCabinet_Desk_BP_C:ExecuteUbergraph_LootCabinet_Desk_BP"),',
        '        0x449, 4};', "",
    ]
    for family, (symbol, entry, ping, _, _) in TWO_SLOT_CLASSES.items():
        stem = family[:-2]
        path = f"/Game/Classes/Actors/Lootables/{stem}.{family}"
        flags = ", true" if ping else ""
        header.extend((f"    inline constexpr StockClass {symbol}{{",
                       f'        STR("{path}"),',
                       f'        STR("{path}:SendInteractionToActor"),',
                       f'        STR("{path}:ExecuteUbergraph_{stem}"),',
                       f"        0x{entry:X}, 2{flags}}};"))
    names = ["small_crate", "small_cabinet", "office_desk", *(row[0] for row in TWO_SLOT_CLASSES.values())]
    header.extend(("    inline constexpr std::array stock_classes{" + ", ".join("&" + name for name in names) + "};", "",
                   "    struct Profile", "    {",
                   "        const RC::CharType* world;",
                   "        const RC::CharType* actor;",
                   "        std::int32_t salt;", "        const RC::CharType* loot_override{};", "        bool guarantee_spawn{};",
                   "        std::uint8_t replenish_override{};",
                   "        const StockClass* stock_class{&small_crate};",
                   "        std::array<std::int32_t, 4> child_salts{};",
                   "    };", "", "    inline constexpr std::array worlds{"))
    for area in dict.fromkeys((*[area for area, _, _, _ in CRATE_PROFILES], SMALL_CABINET_PROFILE[0], OFFICE_DESK_PROFILE[0],
                               *(area for _, area, *_ in families))):
        header.append(f'        STR("{area}.{area.rsplit("/", 1)[1]}"),')
    header.extend(("    };", "", "    inline constexpr std::array profiles{"))
    lua = ["-- Generated by python -m tools.silverfish.generate_authored_loot_registry", "return {"]
    for index, (area, actor, salt, row) in enumerate(CRATE_PROFILES, 1):
        world = f"{area}.{area.rsplit('/', 1)[1]}"
        name = full_name(area, actor)
        path = name.split(" ", 1)[1]
        header.append(f'        Profile{{STR("{world}"),')
        guaranteed = (area, actor) in GUARANTEED_REPLENISHMENT
        replenish = GUARANTEED_REPLENISHMENT.get((area, actor))
        override = f', STR("{row}")' if row else ", nullptr" if guaranteed else ""
        flags = f", true, {replenish or 0}" if guaranteed else ""
        header.append(f'                STR("{path}"), {salt}{override}{flags}}},')
        lua_flags = (", guarantee_spawn = true" if guaranteed else "") + (f", replenish_mode = {replenish}" if replenish else "")
        lua.extend((f'    {{ index = {index}, map = "{area}",',
                    f'      name = "{name}",',
                    f'      path = "{path}", salt = {salt}{lua_flags} }},'))
    special = (
        (SMALL_CABINET_PROFILE, SMALL_CABINET_CHILD_SALTS, "small_cabinet", None, False),
        (OFFICE_DESK_PROFILE, OFFICE_DESK_CHILD_SALTS, "office_desk", "Office", True),
    )
    for index, ((area, actor, salt), salts, family, override, guaranteed) in enumerate(special, len(CRATE_PROFILES) + 1):
        world = f"{area}.{area.rsplit('/', 1)[1]}"
        name = full_name(area, actor)
        path = name.split(" ", 1)[1]
        child_salts = ", ".join(map(str, salts))
        table = f'STR("{override}")' if override else "nullptr"
        guarantee = "true" if guaranteed else "false"
        header.extend((f'        Profile{{STR("{world}"),',
                       f'                STR("{path}"), {salt}, {table}, {guarantee}, 2, &{family}, {{{child_salts}}}}},'))
        lua_guarantee = ", guarantee_spawn = true" if guaranteed else ""
        lua.extend((f'    {{ index = {index}, map = "{area}",',
                    f'      name = "{name}",',
                    f'      path = "{path}", salt = {salt}, replenish_mode = 2, {family} = true{lua_guarantee} }},'))
    registered = {(area, actor) for area, actor, *_ in (*CRATE_PROFILES, SMALL_CABINET_PROFILE, OFFICE_DESK_PROFILE)}
    for index, (family, area, actor, salt, row, guaranteed, mode) in enumerate(families, len(CRATE_PROFILES) + 3):
        if ((family not in TWO_SLOT_CLASSES or area not in QUALIFIED_MAPS or type(salt) is not int
             or not -2**31 <= salt < 2**31 or type(guaranteed) is not bool or mode not in {None, 2}
             or row is not None and row not in GUARANTEED_OVERRIDE_ROWS)
                or (area, actor) in registered or not re.fullmatch(r"[A-Za-z0-9_]+_C_[0-9]+", actor)):
            raise ValueError(f"Invalid cooked stock container placement {area} {actor}")
        registered.add((area, actor))
        stem = family[:-2]
        world = f"{area}.{area.rsplit('/', 1)[1]}"
        name = full_name(area, actor, family)
        path = name.split(" ", 1)[1]
        table = f'STR("{row}")' if row else "nullptr"
        guarantee = str(guaranteed).lower()
        symbol = TWO_SLOT_CLASSES[family][0]
        header.extend((f'        Profile{{STR("{world}"),',
                       f'                STR("{path}"), {salt}, {table}, {guarantee}, {mode or 0}, &{symbol}}},'))
        lua_flags = (", guarantee_spawn = true" if guaranteed else "") + (f", replenish_mode = {mode}" if mode else "")
        lua.extend((f'    {{ index = {index}, map = "{area}",',
                    f'      name = "{name}",',
                    f'      path = "{path}", salt = {salt}{lua_flags},',
                    f'      actor_class = "/Game/Classes/Actors/Lootables/{stem}.{family}" }},'))
    header.extend(("    };", "}", ""))
    lua.extend(("}", ""))
    return {
        "native/authored_loot_registry.hpp": "\n".join(header),
        "mod/SilverfishCoop/Scripts/authored_loot_registry.lua": "\n".join(lua),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--audit", type=Path, help="validate placements against a local cooked-map crate survey")
    parser.add_argument("--family-audit", type=Path, help="import or verify the additional stock container families")
    arguments = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if arguments.audit:
        maps, placed, stock = survey(arguments.audit.read_text(encoding="utf-8-sig"))
        registered = {(area, actor): (salt, row) for area, actor, salt, row in CRATE_PROFILES}
        for key, mode in GUARANTEED_REPLENISHMENT.items():
            registered[key] = (*registered[key], mode)
        rejected = [full_name(area, actor) for (area, actor), profile in registered.items()
                    if stock.get((area, actor)) != profile]
        missing = [full_name(area, actor) for area, actor in stock.keys() - registered.keys()]
        if rejected or missing:
            errors = [*(f"Unexpected profile {name}" for name in sorted(rejected)),
                      *(f"Missing profile {name}" for name in sorted(missing))]
            parser.exit(1, "Crates failed the cooked placement audit\n" + "\n".join(errors) + "\n")
        print(f"{len(maps)} maps surveyed, {len(placed)} placed small crates, {len(stock)} stock candidates "
              f"on {len({area for area, _ in stock})} qualified maps, {len(CRATE_PROFILES)} registered")
    profiles = TWO_SLOT_PLACEMENTS
    if arguments.family_audit:
        audit = json.loads(arguments.family_audit.read_text(encoding="utf-8-sig"))
        profiles = family_placements(audit)
        catalog = repo / FAMILY_CATALOG
        if arguments.check:
            if catalog.read_text(encoding="utf-8") != family_catalog(profiles):
                parser.exit(1, f"{FAMILY_CATALOG} needs regeneration\n")
        else:
            catalog.write_text(family_catalog(profiles), encoding="utf-8", newline="\n")
        print(f"{len(profiles)} placements of {len(TWO_SLOT_CLASSES)} additional two-slot stock classes")
    for relative, expected in outputs(profiles).items():
        path = repo / relative
        if arguments.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                parser.exit(1, f"{relative} needs regeneration\n")
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
