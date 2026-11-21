"""Regenerate native and Lua stock container profiles from the cooked catalog."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re

from .authored_container_placements import CONTAINER_PLACEMENTS
from .authored_loot import (CRATE_PROFILES, GUARANTEED_REPLENISHMENT, OFFICE_DESK_CHILD_SALTS,
                            OFFICE_DESK_PROFILE, SMALL_CABINET_CHILD_SALTS, SMALL_CABINET_PROFILE, full_name)
from .authored_routes import MAPS as EXPANDED_MAPS
from .travel import AUTHORED_MAPS


QUALIFIED_MAPS = AUTHORED_MAPS | EXPANDED_MAPS
PLACED_ACTOR = re.compile(r"LootCrate_Small_BP_C_[0-9]+\Z")
FAMILY_ACTOR = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,95}\Z")
OVERRIDE_ROWS = {"Cargo", "Coup_Gear", "Industrial", "Millitary_Common", "Rural"}
GUARANTEED_OVERRIDE_ROWS = OVERRIDE_ROWS | {"Common_Special", "FishCannery", "Luxury"}
PERMANENT_REPLENISHMENT = "RespawnSetting_Enum::NewEnumerator2"
BASE_FIELDS = {"Loot_Spawner": "Loot_Spawner", "Loot_Spawner1": "Loot_Spawner1", "RootComponent": "DefaultSceneRoot"}
SurveyProfile = tuple[int, str | None] | tuple[int, str | None, int | None]
FAMILY_CATALOG = "tools/silverfish/authored_container_placements.py"
SOURCE_FIELDS = {"RootComponent": "DefaultSceneRoot", "Box": "Box",
                 "SM_Prop_Cabinet_Small_Door_01": "SM_Prop_Cabinet_Small_Door_01"}
SAVE_FIELDS = {"SpawnSalt", "Loot_Override", "GuaranteeSpawn", "Replenish_Mode"}
BOX_GEOMETRY_FIELDS = {"Arrow", "BlueprintCreatedComponents", "DefaultSceneRoot", "SM_Prop_Cabinet_Small_02", "StreamSeed"}
STOCK_ROLL = ("CallFunc_Array_Get_Item.SpawnLoot(Loot Table_Items,SpawnChance_Item,0.5,GuaranteeSpawn,"
              "False,True,False,False,False,CallFunc_SpawnLoot_Object)")
FURNITURE_ROLL = STOCK_ROLL.replace("0.5,", "0.9,")
WEAPON_ROLL = ("CallFunc_Array_Get_Item_1.SpawnLoot(Loot Table_Weapons,SpawnChance_Weapons,0,GuaranteeSpawn,"
               "False,False,False,False,False,CallFunc_SpawnLoot_Object_1)")
SPARE_ROLL = ("CallFunc_Array_Get_Item_2.SpawnLoot(Loot Table_Items,SpawnChance_Item,0,GuaranteeSpawn,"
              "False,False,False,False,False,CallFunc_SpawnLoot_Object_2)")
CRATE_WEAPON_ROLL = ("Loot_SpawnerGun.SpawnLoot(Loot Table_Weapons,1,0,"
                     "CallFunc_RandomBoolWithWeightFromStream_ReturnValue,Seeded,False,False,False,False,CallFunc_SpawnLoot_Object)")
CRATE_AMMO_ROLL = ("CallFunc_Array_Get_Item.SpawnLoot(K2Node_MakeMap_Map,0.8,0,"
                   "True,False,True,False,False,False,CallFunc_SpawnLoot_Object_1)")
CRATE_FALLBACK_ROLL = ("CallFunc_Array_Get_Item_1.SpawnLoot(Loot Table_Items,SpawnChance_Item,0.5,"
                       "GuaranteeSpawn,Seeded,True,False,False,False,CallFunc_SpawnLoot_Object_2)")
CRATE_WEAPON_SPAWNERS = ("Loot_Spawner", "Loot_Spawner1", "Loot_Spawner2", "Loot_Spawner3", "Loot_SpawnerGun")
FAMILY_OVERRIDE_ROWS = GUARANTEED_OVERRIDE_ROWS | {"Office", "Garage", "Marine", "TownHouse", "LabSupplies", "Shop_Food",
                                                   "Uncommon_Basic", "PoliceEquipment", "Uncommon_Special",
                                                   "Weapon_Specialized", "Rare", "Weapon_UltraRare", "Weapon_HighEnd",
                                                   "Weapon_Spec_Mill", "Weapon_Rare", "Weapon_HighEnd_Mill"}


@dataclass(frozen=True)
class ContainerFamily:
    symbol: str
    entry: int
    slots: int = 2
    stock_ping: bool = False
    guaranteed: bool = False
    replenish: str | None = None
    paired_doors: bool = False
    open_roll: int = -95
    open_pitch: int = 0
    open_yaw: int = 0
    door_locations: tuple[tuple[float, float, float], ...] = ()
    prop_components: tuple[str, ...] = ()
    named_spawners: tuple[str, ...] = ()
    rolls: tuple[str, ...] = (STOCK_ROLL,)
    weapon_first: bool = False
    random_lid: tuple[str, int, int] | None = None
    lid_ignores_channel: int | None = None
    keyless_lock: bool = False

    def spawner_names(self):
        return self.named_spawners or ("Loot_Spawner", *(f"Loot_Spawner{i}" for i in range(1, self.slots)))

    def door_calls(self):
        angles = ((0, 0, 95), (0, 0, -95)) if self.paired_doors else ((self.open_roll, self.open_pitch, self.open_yaw),)
        calls = []
        for index, rotation in enumerate(angles):
            name = f"SM_Prop_Cabinet_Small_Door_0{index + 1}"
            location = self.door_locations[index] if self.door_locations else f"{name}.RelativeLocation"
            target = "CallFunc_MakeRotator_ReturnValue" if self.random_lid else rotation
            calls.append(f"MoveComponentTo({name},{location},{target},")
        return calls


CONTAINER_FAMILIES = {
    "LootCrate_Cult_Small_BP_C": ContainerFamily("cult_crate", 0x376, stock_ping=True),
    "Loot_Stash_Small_BP_C": ContainerFamily("loot_stash", 0x376, guaranteed=True, replenish=PERMANENT_REPLENISHMENT),
    "Loot_SuitCase_BP_C": ContainerFamily("suitcase", 0x3A3),
    "Loot_Trunk_BP_C": ContainerFamily("trunk", 0x376),
    "Loot_ToolBox_BP_C": ContainerFamily("toolbox", 0x1FE, slots=1, guaranteed=True,
                                          rolls=(STOCK_ROLL.replace("CallFunc_Array_Get_Item.", "Loot_Spawner."),)),
    "LootCrate_Cargo_BP_C": ContainerFamily("cargo_crate", 0x3E8, slots=4, open_roll=-85,
                                            rolls=(STOCK_ROLL.replace("0.5,", "1,"),)),
    "LootCabnet_Large_BP_C": ContainerFamily("large_cabinet", 0x83D, slots=6, paired_doors=True,
        named_spawners=("Loot_Spawner", "Loot_Spawner1", "Loot_Spawner2", "Loot_Spawner3", "Loot_SpawnerGun", "Loot_SpawnerGun1"),
        rolls=(STOCK_ROLL, WEAPON_ROLL, SPARE_ROLL)),
    "LootCabnet_Small_BP_C": ContainerFamily("small_cabinet", 0x438, slots=4, paired_doors=True,
                                             door_locations=((-49, 23, 68), (49, 23, 68))),
    "LootCabinet_Desk_BP_C": ContainerFamily("office_desk", 0x449, slots=4, paired_doors=True),
    "LootCabinet_Fancy_BP_C": ContainerFamily("fancy_cabinet", 0x46D, slots=6, paired_doors=True, rolls=(FURNITURE_ROLL,)),
    "LootCabinet_LowTable_BP_C": ContainerFamily("low_table", 0x437, slots=3, paired_doors=True,
        prop_components=("SM_TableCabinet_Props",), named_spawners=("Loot_Spawner", "Loot_Spawner2", "Loot_Spawner3"),
        rolls=(FURNITURE_ROLL,)),
    "Loot_ToolCabinet_BP_C": ContainerFamily("tool_cabinet", 0x432, slots=3, paired_doors=True),
    "LootCabnet_Medical_BP_C": ContainerFamily("medical_cabinet", 0x4A2, slots=4, paired_doors=True,
        door_locations=((-49, 23, 22.5), (49, 23, 22.5)), prop_components=("SM_Prop_Sign_WarningSymbol_04",),
        rolls=(STOCK_ROLL.replace("SpawnChance_Item,", "CallFunc_FClamp_ReturnValue,"),)),
    "LootCabnet_Shelf_BP_C": ContainerFamily("shelf_cabinet", 0x444, slots=4, paired_doors=True),
    "Loot_Fridge_BP_C": ContainerFamily("fridge", 0x3E3, slots=7, open_roll=0, open_yaw=-120,
        prop_components=("DFX_Flies", "SM_Prop_Fridge_01_Racks_01"),
        named_spawners=("Loot_Spawner", "Loot_Spawner1", "Loot_Spawner2", "Loot_Spawner3", "Loot_Spawner4", "Loot_Spawner5", "Loot_Spawner7")),
    "Loot_Wardrobe_BP_C": ContainerFamily("wardrobe", 0x5F4, slots=4, paired_doors=True,
        named_spawners=("Loot_Spawner1", "Loot_Spawner2", "Loot_Spawner3", "Loot_Armor"),
        rolls=(FURNITURE_ROLL.replace("CallFunc_Array_Get_Item.", "CallFunc_Array_Get_Item_1."),
               "CallFunc_Array_Get_Item.SpawnLoot(Loot Table_Armor,SpawnChance_Armor,0,GuaranteeSpawn,"
               "False,False,True,True,False,CallFunc_SpawnLoot_Object_1)")),
    "Loot_Wardrobe_Guns_BP_C": ContainerFamily("gun_wardrobe", 0x7E9, slots=4, paired_doors=True,
        named_spawners=("Loot_Spawner1", "Loot_Spawner2", "Loot_SpawnerGun", "Loot_SpawnerGun1"),
        rolls=(FURNITURE_ROLL, WEAPON_ROLL, SPARE_ROLL)),
    "LootCrate_Large_BP_C": ContainerFamily("large_crate", 0x703, slots=5,
        named_spawners=CRATE_WEAPON_SPAWNERS, rolls=(CRATE_WEAPON_ROLL, CRATE_AMMO_ROLL, CRATE_FALLBACK_ROLL), weapon_first=True),
    "LootCrate_Cult_Large_BP_C": ContainerFamily("cult_large_crate", 0xF, slots=5, stock_ping=True,
        named_spawners=CRATE_WEAPON_SPAWNERS, rolls=(CRATE_WEAPON_ROLL, CRATE_AMMO_ROLL, CRATE_FALLBACK_ROLL), weapon_first=True),
    "LootCrate_Weapon_BP_C": ContainerFamily("weapon_crate", 0x48F, slots=5, guaranteed=True, replenish=PERMANENT_REPLENISHMENT,
        named_spawners=CRATE_WEAPON_SPAWNERS, weapon_first=True, rolls=(
            "Loot_SpawnerGun.SpawnLoot(Loot Table_Weapons,1,0,GuaranteeSpawn,Seeded,False,False,True,False,CallFunc_SpawnLoot_Object)",
            CRATE_AMMO_ROLL)),
    "Loot_ArtifactChest_01_C": ContainerFamily("artifact_chest", 0x36C, slots=1, guaranteed=True,
        replenish=PERMANENT_REPLENISHMENT, open_roll=0, open_pitch=100, named_spawners=("Loot_Spawner1",),
        rolls=("CallFunc_Array_Get_Item.SpawnLoot(Loot Table_Items,1,0,GuaranteeSpawn,Seeded,False,True,True,False,CallFunc_SpawnLoot_Object)",)),
    "Loot_Dumpster_BP_C": ContainerFamily("dumpster", 0x450, slots=6, random_lid=("Pitch", 95, 105), lid_ignores_channel=5,
        prop_components=("DFX_Flies", "SM_Prop_TrashSkip_01"), rolls=(STOCK_ROLL.replace("0.5,", "1,"),)),
    "Loot_Car_01_BP_C": ContainerFamily("car_01", 0x8CC, slots=2, random_lid=("Roll", 85, 93), keyless_lock=True,
        prop_components=("Arrow_Latch", "Timeline"), named_spawners=("Loot_Spawner", "Loot_Spawner2")),
    "Loot_Car_02_BP_C": ContainerFamily("car_02", 0x8DE, slots=3, random_lid=("Roll", 85, 91), keyless_lock=True,
        prop_components=("Arrow_Latch", "Timeline"), named_spawners=("Loot_Spawner3", "Loot_Spawner1", "Loot_Spawner2")),
}


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
    for family, spec in CONTAINER_FAMILIES.items():
        stock = audit["families"][family]
        defaults = {export["name"]: export["fields"] for export in stock["defaults"]}
        actor = defaults[f"Default__{family}"]
        script = f"ExecuteUbergraph_{family[:-2]}"
        functions = {function["name"]: function["calls"] for function in stock["functions"]}
        calls = [call.split(" ", 1)[1] for call in functions[script]]
        if (functions["SendInteractionToActor"] != [f"24 {script}({spec.entry})"]
                or [call for call in calls if ".SpawnLoot(" in call] != list(spec.rolls)
                or any(sum(call.startswith(pose) for call in calls) != 1 for pose in spec.door_calls())
                or "Box.K2_DestroyComponent(Self)" not in calls
                or {name for name in defaults if name.endswith("_GEN_VARIABLE")} !=
                {f"{name}_GEN_VARIABLE" for name in spec.spawner_names()}
                or actor.get("Ping_IfOpened", "False") != str(spec.stock_ping)
                or actor.get("GuaranteeSpawn", "False") != str(spec.guaranteed)
                or actor.get("Replenish_Mode") != spec.replenish):
            raise ValueError(f"Cooked container family does not match its stock interaction: {family}")
        if spec.weapon_first:
            slot_loops = [("Loot_Spawner", "Loot_Spawner3")]
            if CRATE_FALLBACK_ROLL in spec.rolls:
                slot_loops.append(CRATE_WEAPON_SPAWNERS[:-1])
            observed = set(re.findall(r"ArrayInnerProp=-, Elements=\[([^\]]*)\]", "\n".join(calls)))
            if (observed != {", ".join(slots) for slots in slot_loops}
                    or "DynamicCast(ClassPtr=Weapon_Master_BP_C, Target=CallFunc_SpawnLoot_Object, RawValue=-)" not in calls):
                raise ValueError(f"Cooked weapon and fallback spawners changed: {family}")
        if spec.random_lid:
            axis, low, high = spec.random_lid
            target = f"CallFunc_MakeRotator_{axis}_ImplicitCast"
            rotation = f"{target},0,0" if axis == "Roll" else f"0,{target},0"
            if f"RandomFloatInRange({low},{high})" not in calls or f"MakeRotator({rotation})" not in calls:
                raise ValueError(f"Cooked random lid rotation changed: {family}")
        if spec.keyless_lock and (actor.get("KeyItem") not in (None, "-") or "Jiggle_Locked()" not in calls or not any(
                call.startswith("Delay(Self,0.8,StructConst(Struct=LatentActionInfo, StructSize=32, RawValue=[SkipOffsetConst(RawValue=0xF),")
                for call in calls)):
            raise ValueError(f"Cooked stock vehicle lock or pry continuation changed: {family}")
        if (spec.lid_ignores_channel is not None and
                f"SM_Prop_Cabinet_Small_Door_01.SetCollisionResponseToChannel(Byte({spec.lid_ignores_channel}),Byte(0))" not in calls):
            raise ValueError(f"Cooked lid collision response changed: {family}")

    profiles, seen = [], set()
    pinned = {SMALL_CABINET_PROFILE[:2], OFFICE_DESK_PROFILE[:2]}
    order = {family: index for index, family in enumerate(CONTAINER_FAMILIES)}
    for row in audit["placements"]:
        family, package, actor = row["type"], row["area"], row["name"]
        spec = CONTAINER_FAMILIES.get(family)
        if not spec:
            continue
        if not re.fullmatch(r"Maps/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.umap", package):
            raise ValueError(f"Invalid stock container package {package}")
        area = "/Game/Classes/" + package.removesuffix(".umap")
        if area not in QUALIFIED_MAPS or (area, actor) in pinned:
            continue
        fields = row["fields"]
        required = {**SOURCE_FIELDS, **{name: name for name in (*spec.spawner_names(), *spec.prop_components)}}
        if spec.paired_doors:
            required["SM_Prop_Cabinet_Small_Door_02"] = "SM_Prop_Cabinet_Small_Door_02"
        if any(fields.get(key) != value for key, value in required.items()):
            continue
        tunable = ({"ContainerExpectedValue", "Seeded"} if spec.weapon_first else
                   {"Material"} if spec.keyless_lock else set())
        if fields.keys() - required.keys() - SAVE_FIELDS - BOX_GEOMETRY_FIELDS - tunable:
            continue
        if spec.weapon_first and (not re.fullmatch(r"[0-9]{1,9}", fields.get("ContainerExpectedValue", "0"))
                                  or fields.get("Seeded", "True") not in {"True", "False"}):
            continue
        if (area, actor) in seen or not FAMILY_ACTOR.fullmatch(actor):
            raise ValueError(f"Invalid or duplicate stock container {area} {actor}")
        seen.add((area, actor))
        guarantee = fields.get("GuaranteeSpawn", str(spec.guaranteed))
        mode = fields.get("Replenish_Mode")
        if guarantee not in {"True", "False"} or mode not in {None, PERMANENT_REPLENISHMENT}:
            continue
        handle = fields.get("Loot_Override")
        override = None
        if handle is not None:
            match = re.fullmatch(r"DataTableRowHandle\(([A-Za-z0-9_]+)\)", handle)
            if not match or match[1] not in FAMILY_OVERRIDE_ROWS:
                continue
            override = match[1]
        salt = int(fields["SpawnSalt"])
        if not -2**31 <= salt < 2**31:
            raise ValueError(f"Invalid stock container salt {area} {actor}")
        profiles.append((family, area, actor, salt, override, guarantee == "True", 2 if mode else None))
    return tuple(sorted(profiles, key=lambda row: (order[row[0]], row[1], row[2])))


def family_catalog(profiles):
    lines = ["# Generated by tools.silverfish.generate_authored_loot_registry from the cooked container audit.",
             "CLASS_CAPACITIES = {"]
    for family, spec in CONTAINER_FAMILIES.items():
        lines.append(f"    {family!r}: {spec.slots},")
    lines.extend(("}", "", "MIXED_GUARANTEE_CLASSES = frozenset({"))
    for family, spec in CONTAINER_FAMILIES.items():
        if spec.weapon_first and CRATE_FALLBACK_ROLL in spec.rolls:
            lines.append(f"    {family!r},")
    lines.extend(("})", "", "RANDOM_LID_CLASSES = {"))
    for family, spec in CONTAINER_FAMILIES.items():
        if spec.random_lid:
            axis, low, high = spec.random_lid
            lines.append(f"    {family!r}: {('lid_' + axis.lower(), low, high)!r},")
    lines.extend(("}", "", "KEYLESS_LOCK_CLASSES = frozenset({"))
    for family, spec in CONTAINER_FAMILIES.items():
        if spec.keyless_lock:
            lines.append(f"    {family!r},")
    lines.extend(("})", "", "CONTAINER_PLACEMENTS = ("))
    for row in profiles:
        lines.append(f"    {row!r},")
    return "\n".join((*lines, ")", ""))


def lua_door_locations(spec):
    if not spec.door_locations:
        return ""
    locations = ", ".join(f"{{X = {x}, Y = {y}, Z = {z}}}" for x, y, z in spec.door_locations)
    return f", door_locations = {{{locations}}}"


def outputs(families=CONTAINER_PLACEMENTS) -> dict[str, str]:
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
        "#include <array>", "#include <cstdint>", "#include <span>", "#include <String/StringType.hpp>", "",
        "namespace silverfish::authored_loot", "{",
        "    inline constexpr std::int32_t vehicle_pry_entry = 0xF;", "",
        "    struct StockClass", "    {",
        "        const RC::CharType* actor_class;", "        const RC::CharType* input;",
        "        const RC::CharType* script;", "        std::int32_t input_entry;", "        std::uint8_t slots;",
        "        bool stock_ping{};", "        bool paired_doors{};",
        "        std::span<const RC::CharType* const> spawner_names{};", "        bool weapon_first{};",
        "        bool keyless_lock{};", "    };", "",
        '    inline constexpr StockClass small_crate{',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:SendInteractionToActor"),',
        '        STR("/Game/Classes/Actors/Lootables/LootCrate_Small_BP.LootCrate_Small_BP_C:ExecuteUbergraph_LootCrate_Small_BP"),',
        '        0x376, 2};', "",
    ]
    for family, spec in CONTAINER_FAMILIES.items():
        stem = family[:-2]
        path = f"/Game/Classes/Actors/Lootables/{stem}.{family}"
        flags = f", {str(spec.stock_ping).lower()}, {str(spec.paired_doors).lower()}"
        if spec.named_spawners:
            names = ", ".join(f'STR("{name}")' for name in spec.named_spawners)
            header.append(f"    inline constexpr std::array {spec.symbol}_spawners{{{names}}};")
            flags += f", {spec.symbol}_spawners"
        if spec.weapon_first:
            flags += ", true"
        if spec.keyless_lock:
            flags += ", false, true"
        header.extend((f"    inline constexpr StockClass {spec.symbol}{{",
                       f'        STR("{path}"),',
                       f'        STR("{path}:SendInteractionToActor"),',
                       f'        STR("{path}:ExecuteUbergraph_{stem}"),',
                       f"        0x{spec.entry:X}, {spec.slots}{flags}}};"))
    names = ["small_crate", *(spec.symbol for spec in CONTAINER_FAMILIES.values())]
    header.extend(("    inline constexpr std::array stock_classes{" + ", ".join("&" + name for name in names) + "};", "",
                   "    struct Profile", "    {",
                   "        const RC::CharType* world;",
                   "        const RC::CharType* actor;",
                   "        std::int32_t salt;", "        const RC::CharType* loot_override{};", "        bool guarantee_spawn{};",
                   "        std::uint8_t replenish_override{};",
                   "        const StockClass* stock_class{&small_crate};",
                   "        std::array<std::int32_t, 4> child_salts{};",
                   "        bool pinned_furniture{};",
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
                       f'                STR("{path}"), {salt}, {table}, {guarantee}, 2, &{family}, {{{child_salts}}}, true}},'))
        lua_guarantee = ", guarantee_spawn = true" if guaranteed else ""
        spec = CONTAINER_FAMILIES[actor.rsplit("_", 1)[0]]
        lua.extend((f'    {{ index = {index}, map = "{area}",',
                    f'      name = "{name}",',
                    f'      path = "{path}", salt = {salt}, replenish_mode = 2, {family} = true{lua_guarantee}'
                    f'{lua_door_locations(spec)} }},'))
    registered = {(area, actor) for area, actor, *_ in (*CRATE_PROFILES, SMALL_CABINET_PROFILE, OFFICE_DESK_PROFILE)}
    for index, (family, area, actor, salt, row, guaranteed, mode) in enumerate(families, len(CRATE_PROFILES) + 3):
        spec = CONTAINER_FAMILIES.get(family)
        if ((not spec or area not in QUALIFIED_MAPS or type(salt) is not int
             or not -2**31 <= salt < 2**31 or type(guaranteed) is not bool or mode not in {None, 2}
             or row is not None and row not in FAMILY_OVERRIDE_ROWS)
                or (area, actor) in registered or not FAMILY_ACTOR.fullmatch(actor)):
            raise ValueError(f"Invalid cooked stock container placement {area} {actor}")
        registered.add((area, actor))
        stem = family[:-2]
        world = f"{area}.{area.rsplit('/', 1)[1]}"
        name = full_name(area, actor, family)
        path = name.split(" ", 1)[1]
        table = f'STR("{row}")' if row else "nullptr"
        guarantee = str(guaranteed).lower()
        header.extend((f'        Profile{{STR("{world}"),',
                       f'                STR("{path}"), {salt}, {table}, {guarantee}, {mode or 0}, &{spec.symbol}}},'))
        lua_flags = (", guarantee_spawn = true" if guaranteed else "") + (f", replenish_mode = {mode}" if mode else "")
        if spec.paired_doors:
            lua_flags += ", paired_doors = true"
        elif spec.random_lid:
            axis, low, high = spec.random_lid
            lua_flags += f', random_lid = {{"{axis}", {low}, {high}}}'
        if spec.keyless_lock:
            lua_flags += ", keyless_lock = true"
        if not (spec.paired_doors or spec.random_lid) and (spec.open_roll != -95 or spec.open_pitch or spec.open_yaw):
            lua_flags += f", open_roll = {spec.open_roll}, open_yaw = {spec.open_yaw}"
            if spec.open_pitch:
                lua_flags += f", open_pitch = {spec.open_pitch}"
        if spec.lid_ignores_channel is not None:
            lua_flags += f", lid_ignores_channel = {spec.lid_ignores_channel}"
        lua_flags += lua_door_locations(spec)
        if spec.named_spawners:
            names = ", ".join(f'"{name}"' for name in spec.named_spawners)
            lua_flags += f", spawner_names = {{{names}}}"
        lua.extend((f'    {{ index = {index}, map = "{area}",',
                    f'      name = "{name}",',
                    f'      path = "{path}", salt = {salt}, capacity = {spec.slots}{lua_flags},',
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
    profiles = CONTAINER_PLACEMENTS
    if arguments.family_audit:
        audit = json.loads(arguments.family_audit.read_text(encoding="utf-8-sig"))
        profiles = family_placements(audit)
        catalog = repo / FAMILY_CATALOG
        if arguments.check:
            if catalog.read_text(encoding="utf-8") != family_catalog(profiles):
                parser.exit(1, f"{FAMILY_CATALOG} needs regeneration\n")
        else:
            catalog.write_text(family_catalog(profiles), encoding="utf-8", newline="\n")
        print(f"{len(profiles)} placements of {len(CONTAINER_FAMILIES)} additional stock container classes")
    for relative, expected in outputs(profiles).items():
        path = repo / relative
        if arguments.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                parser.exit(1, f"{relative} needs regeneration\n")
        else:
            path.write_text(expected, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
