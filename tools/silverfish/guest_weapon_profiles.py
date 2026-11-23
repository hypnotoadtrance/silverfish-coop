"""Exact stock guest firearm profiles shared by the launcher and game adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StockAmmo:
    symbol: str
    caliber: int
    row: str
    item: str
    projectile: str
    fixture_units: int
    projectile_count: int = 1

    @property
    def item_path(self) -> str:
        return f"/Game/Classes/Items/{self.item}.{self.item}_C"

    @property
    def projectile_path(self) -> str:
        return f"/Game/Classes/Weapons/Wep_Assets/Projectiles/{self.projectile}.{self.projectile}_C"


NINE_MM_BALL = StockAmmo("nine_mm_ball", 0, "9mm_Ball", "Ammo_Pistol_BP", "Projectile_Pistol_Light", 9)
FORTY_FIVE_BALL = StockAmmo("forty_five_ball", 1, "45Cal_Ball", "Ammo_Magnum_BP", "Projectile_Pistol_Magnum", 10)
FORTY_FIVE_HOLLOW_POINT = StockAmmo(
    "forty_five_hollow_point", 1, "45Cal_HollowPoint", "Ammo_Magnum_HollowPoint_BP", "Projectile_45_HollowPoint", 18,
)
TWELVE_GAUGE_BUCK = StockAmmo(
    "twelve_gauge_buck", 4, "12Gauge_Buck", "Ammo_ShotgunShells_BP", "Projectile_ShotGun_Pellet", 5, projectile_count=9,
)
FIVE_SEVEN_AP = StockAmmo("five_seven_ap", 11, "5-7_AP", "Ammo_5-7_AP_BP", "Projectile_5-7_AP", 7)
FIFTY_CAL_FMJ = StockAmmo("fifty_cal_fmj", 6, "50Cal_FMJ", "Ammo_50Cal_BP", "Projectile_50Cal", 8)
STOCK_AMMO = (
    NINE_MM_BALL, FORTY_FIVE_BALL, FORTY_FIVE_HOLLOW_POINT, TWELVE_GAUGE_BUCK, FIVE_SEVEN_AP, FIFTY_CAL_FMJ,
)


@dataclass(frozen=True)
class Magazine:
    capacity: int
    burst_length: int
    fire_rate: float
    require_default_payload: bool = False
    ammo: StockAmmo = NINE_MM_BALL
    reload_first_stack: bool = False


@dataclass(frozen=True)
class Firearm:
    kind: str
    blueprint: str
    headless_seed: bool = False
    magazine: Magazine | None = None

    @property
    def path(self) -> str:
        return f"/Game/Classes/Weapons/{self.blueprint}.{self.blueprint}_C"


FIREARMS = (
    Firearm("smg", "Wep_SMG_UBGL"),
    Firearm("smg-standard", "Wep_SMG_01", True, Magazine(30, 0, 0.075)),
    Firearm("light-pistol", "Wep_LightPistol", True),
    Firearm("light-pistol-suppressed", "Wep_LightPistol_Suppressed", True, Magazine(14, 1, 0.05)),
    Firearm("nagant-scoped", "Wep_Nagant_Scoped"),
    Firearm("obrez", "Wep_Obrez_01"),
    Firearm("break-action-50", "Wep_50BMP_BreakAction", True,
            Magazine(1, 1, 0.1, ammo=FIFTY_CAL_FMJ, reload_first_stack=True)),
    Firearm("army-pistol", "Wep_ArmyPistol", True, Magazine(12, 1, 0.08, True, reload_first_stack=True)),
    Firearm("pistol", "Wep_Pistol", True, Magazine(12, 1, 0.08, True, reload_first_stack=True)),
    Firearm("rusty-pistol", "Wep_RustyPistol", True, Magazine(8, 1, 0.1, True, reload_first_stack=True)),
    Firearm("pistol-02", "Wep_Pistol02", True, Magazine(12, 1, 0.1, True, reload_first_stack=True)),
    Firearm("tac-pistol", "Wep_TacPistol", True, Magazine(18, 1, 0.07, True, reload_first_stack=True)),
    Firearm("1911", "Wep_1911", True, Magazine(8, 1, 0.1, True, FORTY_FIVE_BALL, reload_first_stack=True)),
    Firearm("1911-fancy", "Wep_1911_Fancy", True,
            Magazine(8, 1, 0.1, True, FORTY_FIVE_BALL, reload_first_stack=True)),
    Firearm("heavy-pistol", "Wep_HeavyPistol", True,
            Magazine(12, 1, 0.15, True, FORTY_FIVE_BALL, reload_first_stack=True)),
    Firearm("revolver-9mm", "Wep_Revolver_9mm", True, Magazine(7, 1, 0.2, True, reload_first_stack=True)),
    # The three long-barrel variants inherit the stock whole-magazine reload.
    Firearm("revolver-01", "Wep_Revolver_01", True, Magazine(6, 1, 0.5, True, FORTY_FIVE_BALL, reload_first_stack=True)),
    Firearm("revolver-02", "Wep_Revolver_02", True,
            Magazine(6, 1, 0.5, True, FORTY_FIVE_HOLLOW_POINT, reload_first_stack=True)),
    Firearm("revolver-03", "Wep_Revolver_03", True,
            Magazine(6, 1, 0.5, True, FORTY_FIVE_HOLLOW_POINT, reload_first_stack=True)),
    Firearm("sg-71-a", "Wep_ShotGun_SemiAuto", True,
            Magazine(10, 1, 0.2, True, TWELVE_GAUGE_BUCK, reload_first_stack=True)),
    Firearm("sg-71-b", "Wep_ShotGun_SemiAuto_02", True,
            Magazine(10, 1, 0.2, True, TWELVE_GAUGE_BUCK, reload_first_stack=True)),
    Firearm("sg-71-a-suppressed", "Wep_ShotGun_SemiAuto_Suppressed", True,
            Magazine(5, 1, 0.2, True, TWELVE_GAUGE_BUCK, reload_first_stack=True)),
    Firearm("model-1931", "Wep_Pistol_Long", True, Magazine(10, 1, 0.1, True, reload_first_stack=True)),
    Firearm("fennec-57", "Wep_Pistol_Fennec_01", True,
            Magazine(17, 1, 0.06, True, FIVE_SEVEN_AP, reload_first_stack=True)),
    Firearm("black-fox", "Wep_Pistol_Fennec_02", True,
            Magazine(17, 1, 0.06, True, FIVE_SEVEN_AP, reload_first_stack=True)),
)

# Registered ordinary magazines use the stock primary-ammunition transaction.
# The underbarrel SMG and other specialized receivers have no magazine profile.
TAKE_AMMO_MAGAZINES = {weapon.path: weapon.magazine for weapon in FIREARMS if weapon.magazine}
MAX_TAKE_AMMO_ROUNDS = max(magazine.capacity for magazine in TAKE_AMMO_MAGAZINES.values())
