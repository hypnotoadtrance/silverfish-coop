"""Exact stock persistent-level small crates qualified for shared loot."""

import math

from .authored_container_placements import (CLASS_CAPACITIES, CONTAINER_PLACEMENTS, KEY_UNLOCK_ONLY_CLASSES,
                                            KEYED_LOCK_CLASSES, KEYLESS_LOCK_CLASSES, MIXED_GUARANTEE_CLASSES, RANDOM_LID_CLASSES)
from .travel import CITY, CISTERN, CROSSROADS, DEAD_BRIDGE, LAB_E206, LAKE, RAIL_YARD


PLACEMENTS = (
    (CROSSROADS, "LootCrate_Small_BP_C_3", -1911374248),
    (CROSSROADS, "LootCrate_Small_BP_C_9", -564104064),
    (DEAD_BRIDGE, "LootCrate_Small_BP_C_1", -1960753196),
    (CITY, "LootCrate_Small_BP_C_5", -1685293152),
    (CITY, "LootCrate_Small_BP_C_7", -436292608),
    (CITY, "LootCrate_Small_BP_C_9", -140629248),
    (CITY, "LootCrate_Small_BP_C_11", -1132847040),
    (CITY, "LootCrate_Small_BP_C_13", -1857173376),
    (CITY, "LootCrate_Small_BP_C_15", -1228919296),
    (CITY, "LootCrate_Small_BP_C_17", -1284951360),
    (RAIL_YARD, "LootCrate_Small_BP_C_0", -1578844576),
    (RAIL_YARD, "LootCrate_Small_BP_C_1", -1652760384),
    (RAIL_YARD, "LootCrate_Small_BP_C_3", -1449629184),
    (RAIL_YARD, "LootCrate_Small_BP_C_4", -619281664),
    (RAIL_YARD, "LootCrate_Small_BP_C_11", -451063552),
    (RAIL_YARD, "LootCrate_Small_BP_C_14", -1014618368),
    (RAIL_YARD, "LootCrate_Small_BP_C_15", -780724480),
    (CISTERN, "LootCrate_Small_BP_C_1", -1486678656),
    (CISTERN, "LootCrate_Small_BP_C_3", -400280704),
    (CISTERN, "LootCrate_Small_BP_C_4", -1515671264),
    (CISTERN, "LootCrate_Small_BP_C_5", -1430707776),
    (CISTERN, "LootCrate_Small_BP_C_7", -1247535616),
)

# Default placements from the cooked small-crate survey. Keep the original
# profiles first to preserve indices understood by the matching native bridge.
_ADDITIONAL_DEFAULTS = {
    "/Game/Classes/Maps/Maps_Dungeons/Anex_01_P": (
        (7, -34608000),
        (9, -166570112),
        (11, -1629322176),
        (13, -720175744),
        (15, -1310403776),
        (17, -1020294784),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/DryDock_P": (
        (0, -472853760),
        (1, -655903872),
        (3, -411999872),
        (5, -992584000),
        (7, -739524480),
        (9, -1649769568),
        (11, -152165248),
        (13, -1196630784),
        (15, -1844538720),
        (17, -1001678528),
        (19, -755394048),
        (21, -273079680),
        (23, -1328470720),
        (25, -1814081232),
        (27, -705343872),
        (29, -1544785920),
        (31, -37293696),
        (33, -721213440),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/Factory_SB_P": (
        (1, -769493760),
        (3, -547685120),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P": (
        (1, -1837641536),
        (2, -908475008),
        (3, -1081759104),
        (5, -1293374400),
        (6, -278695040),
        (7, -457960704),
        (9, -1921628464),
        (11, -1934385204),
        (13, -599749760),
        (15, -648091136),
        (17, -1905941952),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P": (
        (1, -1665150912),
        (5, -1381817088),
        (7, -674520192),
        (9, -1548875392),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P": (
        (1, -677266688),
        (3, -342600832),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/Perdition_01_P": (
        (1, -495864704),
        (2, -722312064),
        (3, -1882503744),
        (5, -1010467840),
        (6, -1141026048),
        (7, -949552896),
        (9, -1669301408),
        (11, -582842496),
        (13, -944181696),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P": (
        (1, -417127040),
        (3, -898281856),
        (5, -1470015584),
        (7, -1634632416),
        (9, -589129344),
        (11, -1334147136),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/Subway_01_P": (
        (1, -967680960),
        (7, -1877254552),
    ),
    "/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P": (
        (1, -1475875136),
        (2, -1587755968),
        (4, -365184512),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Beach_01_P": (
        (2, -1636890784),
        (3, -879421312),
        (5, -185491584),
        (6, -899929728),
        (7, -1554063520),
        (9, -126834816),
        (10, -436841984),
        (11, -1801141392),
        (13, -1100436416),
        (15, -166386944),
        (17, -1463057344),
        (19, -1877803888),
        (21, -1046540736),
        (23, -341502080),
        (25, -359935232),
        (27, -1625659968),
        (29, -1601062048),
        (31, -1802484208),
        (33, -1076021632),
        (35, -1227149248),
        (37, -927518464),
        (39, -1477706240),
        (41, -244636416),
        (43, -1953001496),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P": (
        (1, -956877376),
        (8, -565569024),
        (15, -1105258368),
        (18, -1108127040),
        (21, -1015350848),
        (23, -1925473800),
        (27, -699789440),
        (28, -1287942080),
        (30, -1578417312),
        (32, -1862361520),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P": (
        (3, -1935850092),
        (5, -825708800),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P": (
        (0, -1578844576),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P": (
        (1, -1786187328),
        (2, -1786187328),
        (3, -1786187328),
        (4, -1786187328),
        (5, -1786187328),
        (13, -265205888),
        (21, -472853760),
        (22, -1563463232),
        (24, -130008832),
        (25, -655903872),
        (26, -472853760),
        (27, -655903872),
        (28, -1758354448),
        (29, -472853760),
        (30, -353892736),
        (31, -655903872),
        (32, -472853760),
        (33, -655903872),
        (34, -655903872),
        (35, -472853760),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P": (
        (1, -747703424),
        (5, -1184667520),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P": (
        (2, -883938048),
        (6, -1433820608),
        (10, -119754624),
        (14, -679647232),
        (16, -115482112),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P": (
        (0, -1578844576),
        (4, -1035432000),
        (8, -927274368),
        (10, -1848384048),
        (12, -822534912),
        (16, -1005890048),
        (18, -1028107520),
        (19, -1814081232),
        (20, -332224512),
        (21, -578691968),
        (22, -1121311040),
        (25, -1249977088),
        (27, -454481664),
        (29, -1955442976),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P": (
        (1, -1239967040),
        (2, -276680832),
        (3, -152775680),
        (4, -329111552),
        (6, -37782016),
        (9, -1815973392),
        (10, -1942075872),
        (11, -1734672064),
        (13, -155888512),
        (14, -1414960128),
        (19, -1645619072),
        (23, -277901568),
        (26, -1894405960),
        (28, -658345344),
        (30, -1268471296),
        (32, -811975424),
        (34, -1712088384),
        (36, -418103552),
        (38, -637653760),
        (40, -32715776),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/PineRidge_P": (
        (2, -1624744416),
        (3, -1501205472),
        (5, -1158787776),
        (7, -1546800128),
        (9, -1904049808),
        (11, -1126682368),
        (13, -1369121344),
        (15, -1818842128),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P": (
        (0, -1578844576),
        (1, -172063360),
        (3, -1057344256),
        (4, -1639393312),
        (5, -843775744),
        (7, -154118400),
        (9, -365306624),
        (11, -1608508544),
        (12, -468031872),
        (15, -1222571520),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P": (
        (0, -1578844576),
        (1, -1940733052),
        (2, -1523117792),
        (3, -1668813152),
        (4, -266304512),
        (5, -1413434240),
        (6, -478530304),
        (7, -930875520),
        (8, -1050630208),
        (9, -799218816),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Slums_P": (
        (1, -767418368),
        (3, -432142080),
        (5, -1293801664),
        (7, -1014008000),
        (9, -222540928),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P": (
        (0, -1578844576),
        (1, -183168),
        (2, -617206272),
        (3, -749351552),
        (5, -811182080),
        (6, -1031403520),
        (7, -1679616704),
        (8, -691549440),
        (9, -619098368),
        (10, -1505233920),
        (15, -104617472),
        (25, -584734720),
        (27, -1283852608),
        (29, -545731968),
        (31, -264656512),
        (33, -1326822720),
        (35, -761131648),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P": (
        (1, -1374980928),
        (3, -1139255936),
        (5, -1943479720),
        (7, -1298074304),
        (9, -1398663296),
        (11, -1841364784),
        (13, -474440704),
        (15, -458326912),
        (17, -13916544),
        (19, -337778816),
        (21, -536759552),
        (23, -1548814368),
        (25, -181463040),
        (27, -343455360),
        (29, -1069307584),
        (31, -688619648),
        (33, -95828224),
        (35, -1164403200),
        (37, -967070592),
        (39, -1371684928),
        (41, -1001129216),
        (43, -140812416),
        (45, -447157120),
        (47, -1121860416),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P": (
        (1, -1458479552),
        (7, -1563890496),
        (9, -69032960),
        (11, -552934400),
        (13, -1180333824),
        (17, -718039552),
        (19, -46266112),
        (21, -556108288),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/TheWall_01_P": (
        (1, -1905758840),
        (3, -15991680),
        (5, -831080064),
        (7, -782067328),
        (9, -837611008),
        (11, -1548020864),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/TrashPit_P": (
        (1, -1694021440),
        (3, -477431552),
        (5, -106875776),
        (7, -495376512),
        (9, -236213248),
        (11, -1517685472),
        (13, -1240821568),
        (15, -1315897088),
        (19, -1615649888),
        (21, -1286477248),
        (23, -1300759936),
        (25, -1914975432),
        (27, -710043648),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Trench_01_P": (
        (1, -128910208),
        (3, -1344950720),
        (5, -1226783040),
    ),
}

PLACEMENTS += tuple(
    (area, f"LootCrate_Small_BP_C_{index}", salt)
    for area, placements in _ADDITIONAL_DEFAULTS.items()
    for index, salt in placements
)


_OVERRIDDEN_DEFAULTS = {
    "/Game/Classes/Maps/Maps_Dungeons/Anex_01_P": (
        (1, -996673472, "Millitary_Common"),
        (3, -909390592, "Millitary_Common"),
        (5, -1292397888, "Millitary_Common"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Beach_01_P": (
        (1, -246955776, "Millitary_Common"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P": (
        (2, -239753344, "Cargo"),
        (4, -902249216, "Industrial"),
        (6, -1543687264, "Cargo"),
        (7, -925138176, "Industrial"),
        (10, -6347904, "Cargo"),
        (11, -143681152, "Rural"),
        (12, -692648064, "Industrial"),
        (13, -1220496256, "Rural"),
        (14, -220099456, "Industrial"),
        (19, -806909440, "Industrial"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P": (
        (4, -1573595360, "Millitary_Common"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P": (
        (1, -208075136, "Industrial"),
        (2, -1385479296, "Industrial"),
        (3, -1620166624, "Industrial"),
        (6, -383739648, "Industrial"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Forest_01_P": (
        (7, -223334400, "Coup_Gear"),
        (9, -826685440, "Rural"),
        (11, -1185644096, "Coup_Gear"),
        (13, -1622669152, "Coup_Gear"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P": (
        (1, -1202734464, "Coup_Gear"),
        (3, -55848832, "Coup_Gear"),
        (5, -608233984, "Coup_Gear"),
        (7, -806176896, "Coup_Gear"),
        (9, -1935789056, "Coup_Gear"),
        (11, -1641102304, "Coup_Gear"),
        (13, -245368832, "Coup_Gear"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P": (
        (13, -621539968, "Rural"),
        (15, -1822565392, "Millitary_Common"),
        (17, -1578600416, "Rural"),
        (23, -894009216, "Millitary_Common"),
        (24, -651509120, "Millitary_Common"),
        (28, -651509120, "Millitary_Common"),
        (30, -651509120, "Millitary_Common"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P": (
        (5, -809533952, "Millitary_Common"),
        (7, -1952085944, "Millitary_Common"),
        (15, -107058944, "Millitary_Common"),
        (21, -1607104704, "Rural"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P": (
        (1, -1728019072, "Coup_Gear"),
        (2, -1728019072, "Coup_Gear"),
        (3, -1728019072, "Coup_Gear"),
        (4, -1728019072, "Coup_Gear"),
        (6, -412244096, "Coup_Gear"),
        (7, -412244096, "Coup_Gear"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P": (
        (11, -1061311680, "Millitary_Common"),
        (13, -968047168, "Millitary_Common"),
        (14, -1526779968, "Coup_Gear"),
        (17, -1643787936, "Coup_Gear"),
        (19, -1458418560, "Coup_Gear"),
        (21, -1213904256, "Coup_Gear"),
        (23, -995696896, "Coup_Gear"),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P": (
        (3, -319223680, "Cargo"),
    ),
}


OVERRIDDEN_PLACEMENTS = tuple(
    (area, f"LootCrate_Small_BP_C_{index}", salt, row)
    for area, placements in _OVERRIDDEN_DEFAULTS.items()
    for index, salt, row in placements
)

# GuaranteeSpawn still uses both exact stock child spawners. Cooked mode 2
# makes the parent save exhaustion permanently by actor name and map.
_GUARANTEED_DEFAULT_MODE = {
    "/Game/Classes/Maps/Maps_OverWorld/PineRidge_P": ((1, -1175450944),),
    "/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P": ((5, -268135680),),
}
_GUARANTEED_PERMANENT = {
    "/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P": ((2, -1403973504, None), (4, -1728202176, None)),
    "/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P": ((1, -1629383200, "Common_Special"),),
    "/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P": ((0, -526139136, None), (1, -428724096, None), (3, -356456192, None)),
    "/Game/Classes/Maps/Maps_Dungeons/TutTnl_01_P": ((1, -1029511424, None),),
    "/Game/Classes/Maps/Maps_OverWorld/Docks_01_P": tuple(
        (index, -421338496, "FishCannery") for index in (6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18, 19, 20)
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Lake_01_P": ((3, -1826471760, "Industrial"),),
    "/Game/Classes/Maps/Maps_OverWorld/Observitory_P": ((1, -616840064, None),),
    "/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P": ((13, -1771477408, None),),
    RAIL_YARD: (
        (2, -894558592, None), (6, -1634937600, "Luxury"), (7, -512833024, "Industrial"),
        (8, -577593344, "Industrial"), (9, -621784064, None), (10, -964140800, None),
        (13, -122112, "Millitary_Common"), (19, -751670784, None), (21, -46571264, None),
    ),
    "/Game/Classes/Maps/Maps_OverWorld/Slums_P": ((2, -1379009344, None),),
}

GUARANTEED_PLACEMENTS = tuple(
    (area, f"LootCrate_Small_BP_C_{index}", salt, None, None)
    for area, entries in _GUARANTEED_DEFAULT_MODE.items()
    for index, salt in entries
) + tuple(
    (area, f"LootCrate_Small_BP_C_{index}", salt, row, 2)
    for area, entries in _GUARANTEED_PERMANENT.items()
    for index, salt, row in entries
)
GUARANTEED_REPLENISHMENT = {(area, actor): replenish for area, actor, _, _, replenish in GUARANTEED_PLACEMENTS}
CRATE_PROFILES = tuple((*placement, None) for placement in PLACEMENTS) + OVERRIDDEN_PLACEMENTS + tuple(
    (area, actor, salt, row) for area, actor, salt, row, _ in GUARANTEED_PLACEMENTS
)


def full_name(area: str, actor: str, actor_class: str | None = None) -> str:
    if actor_class is None:
        actor_class = actor.rsplit("_", 1)[0]
    return f"{actor_class} {area}.{area.rsplit('/', 1)[1]}:PersistentLevel.{actor}"


SMALL_CABINET_PROFILE = (LAB_E206, "LootCabnet_Small_BP_C_5", -595904512)
SMALL_CABINET_CHILD_SALTS = (-34893800, -46555604, -43386008, -81274330)
SMALL_CABINET = full_name(*SMALL_CABINET_PROFILE[:2])
OFFICE_DESK_PROFILE = (LAKE, "LootCabinet_Desk_BP_C_3", -205206528)
OFFICE_DESK_CHILD_SALTS = (-7851936, -32237128, -43421604, -7392800)
OFFICE_DESK = full_name(*OFFICE_DESK_PROFILE[:2])
CRATES = {full_name(area, actor): area for area, actor, _, _ in CRATE_PROFILES}
CRATES.update({SMALL_CABINET: LAB_E206, OFFICE_DESK: LAKE})
CRATES.update({full_name(area, actor, family): area for family, area, actor, *_ in CONTAINER_PLACEMENTS})
CONTAINER_CAPACITIES = {full_name(area, actor, family): CLASS_CAPACITIES[family]
                        for family, area, actor, *_ in CONTAINER_PLACEMENTS}
CONTAINER_CAPACITIES.update({SMALL_CABINET: 4, OFFICE_DESK: 4})
GUARANTEED_CRATES = frozenset(full_name(area, actor) for area, actor, *_ in GUARANTEED_PLACEMENTS) | {OFFICE_DESK}
GUARANTEED_CRATES |= {full_name(area, actor, family) for family, area, actor, _, _, guaranteed, *_ in CONTAINER_PLACEMENTS
                      if guaranteed}
LEGACY_CRATE = full_name(*PLACEMENTS[0][:2])
LOCKABLE_CONTAINER_CLASSES = KEYLESS_LOCK_CLASSES | KEYED_LOCK_CLASSES


def loot_capacity(container: str) -> int:
    return CONTAINER_CAPACITIES.get(container, 2 if container in CRATES else 0)


def valid_loot_trait_calls(container: str, calls: object) -> bool:
    if type(calls) is not int:
        return False
    if container in GUARANTEED_CRATES:
        # The first gun slot can run one independent karma query even when
        # the container guarantees its fallback items.
        return 0 <= calls <= int(container.partition(" ")[0] in MIXED_GUARANTEE_CLASSES)
    return 1 <= calls <= 1 + loot_capacity(container)


def random_lid(container: str) -> tuple[str, int, int] | None:
    return RANDOM_LID_CLASSES.get(container.partition(" ")[0])


def valid_lid_angle(lid: tuple[str, int, int], angle: object) -> bool:
    return type(angle) in (int, float) and lid[1] <= angle <= lid[2]


def vehicle_lock(container: str, area: str | None) -> bool:
    return area is not None and CRATES.get(container) == area and container.partition(" ")[0] in KEYLESS_LOCK_CLASSES


def lockable_container(container: str, area: str | None) -> bool:
    return (area is not None and CRATES.get(container) == area
            and container.partition(" ")[0] in LOCKABLE_CONTAINER_CLASSES)


def key_unlock_only(container: str, area: str | None) -> bool:
    return area is not None and CRATES.get(container) == area and container.partition(" ")[0] in KEY_UNLOCK_ONLY_CLASSES


def valid_item_transform(transform: object) -> bool:
    """Verify a stock loot actor's captured position, rotation, and scale."""
    return (
        type(transform) is dict and transform.keys() == {"position", "rotation", "scale"}
        and all(type(transform[field]) is list and len(transform[field]) == 3
                and all(type(axis) in (int, float) and abs(axis) <= limit and math.isfinite(axis)
                        for axis in transform[field])
                for field, limit in (("position", 1e7), ("rotation", 360), ("scale", 100)))
        and all(axis != 0 for axis in transform["scale"])
    )
