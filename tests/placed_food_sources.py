"""Stock placed food actors used by the loose pickup smoke tests."""

from tools.silverfish.authored_supply import CRASH_SITE
from tools.silverfish.native_guest_consumable import BROWN_MUSHROOM_CLASS, CANNED_BEANS_CLASS
from tools.silverfish.travel import BOROUGH, CITY, CROSSROADS


CITY_PEACHES = "city-canned-peaches"
CITY_BEANS = "city-canned-beans"
CRASH_SITE_BEANS = "crash-site-canned-beans"
CROSSROADS_MUSHROOM = "crossroads-brown-mushroom"
BOROUGH_MRE = "borough-mre"
PLACED_FOOD = {
    CITY_PEACHES: (CITY, f"Item_CannedPeaches_C {CITY}.City_01_P:PersistentLevel.Item_CannedPeaches_C_1",
                   "/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"),
    CITY_BEANS: (CITY, f"Item_CannedBeans_C {CITY}.City_01_P:PersistentLevel.Item_CannedBeans_C_1", CANNED_BEANS_CLASS),
    CRASH_SITE_BEANS: (CRASH_SITE, f"Item_CannedBeans_C {CRASH_SITE}.CrashSite_01_P:PersistentLevel.Item_CannedBeans_C_1",
                      CANNED_BEANS_CLASS),
    CROSSROADS_MUSHROOM: (CROSSROADS, f"Item_BrownMushroom_C {CROSSROADS}.CrossRoads_P:PersistentLevel.Item_BrownMushroom_C_3",
                        BROWN_MUSHROOM_CLASS),
    BOROUGH_MRE: (BOROUGH, f"Item_MRE_C {BOROUGH}.Borough:PersistentLevel.Item_MRE_C_1",
                 "/Game/Classes/Items/Item_MRE.Item_MRE_C"),
}
