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
FIVE_SIX_FMJ = StockAmmo("five_six_fmj", 2, "5.6Intr_FMJ", "Ammo_IntrRifle_BP", "Projectile_Rifle_Inter", 7)
TWELVE_GAUGE_BUCK = StockAmmo(
    "twelve_gauge_buck", 4, "12Gauge_Buck", "Ammo_ShotgunShells_BP", "Projectile_ShotGun_Pellet", 5, projectile_count=9,
)
TWELVE_GAUGE_SLUGS = StockAmmo(
    "twelve_gauge_slugs", 4, "12Gauge_Slugs", "Ammo_ShotgunShells_Slugs_BP", "Projectile_SG_Slug", 10,
)
TWELVE_GAUGE_DSAPI = StockAmmo(
    "twelve_gauge_dsapi", 4, "12Gauge_DSAPI", "Ammo_ShotgunShells_SlugsAP_BP", "Projectile_SG_SlugAP", 7,
)
TWELVE_GAUGE_FRAG = StockAmmo(
    "twelve_gauge_frag", 4, "12Gauge_Frag12", "Ammo_ShotgunShells_Frag12_BP", "Projectile_SG_Frag12", 6,
)
TWELVE_GAUGE_DRAGON_BREATH = StockAmmo(
    "twelve_gauge_dragon_breath", 4, "12Gauge_DragonBreath", "Ammo_ShotgunShells_DragonBreath_BP",
    "Projectile_MoltenMetal_Slag", 8, projectile_count=12,
)
TWELVE_GAUGE_HYPERBALL = StockAmmo(
    "twelve_gauge_hyperball", 4, "12Gauge_HyperBall", "Ammo_ShotgunShells_HyperBall_BP",
    "Projectile_SG_HyperBall", 18, projectile_count=3,
)
SHOTGUN_AMMO = (
    TWELVE_GAUGE_BUCK, TWELVE_GAUGE_SLUGS, TWELVE_GAUGE_DSAPI, TWELVE_GAUGE_FRAG,
    TWELVE_GAUGE_DRAGON_BREATH, TWELVE_GAUGE_HYPERBALL,
)
FIVE_SEVEN_AP = StockAmmo("five_seven_ap", 11, "5-7_AP", "Ammo_5-7_AP_BP", "Projectile_5-7_AP", 7)
FIFTY_CAL_FMJ = StockAmmo("fifty_cal_fmj", 6, "50Cal_FMJ", "Ammo_50Cal_BP", "Projectile_50Cal", 8)
THIRTY_06_FMJ = StockAmmo("thirty_06_fmj", 3, "30-06_FMJ", "Ammo_FullRifle_BP", "Projectile_Rifle_Heavy", 5)
THIRTY_06_INCENDIARY = StockAmmo(
    "thirty_06_incendiary", 3, "30-06_Incendiary", "Ammo_30-06_Incendiary_BP", "Projectile_30-06_Incendiary", 5,
)
STOCK_AMMO = (
    NINE_MM_BALL, FORTY_FIVE_BALL, FORTY_FIVE_HOLLOW_POINT, FIVE_SIX_FMJ, *SHOTGUN_AMMO, FIVE_SEVEN_AP, FIFTY_CAL_FMJ,
    THIRTY_06_FMJ, THIRTY_06_INCENDIARY,
)


@dataclass(frozen=True)
class Magazine:
    capacity: int
    burst_length: int
    fire_rate: float
    require_default_payload: bool = False
    ammo: StockAmmo = NINE_MM_BALL
    reload_first_stack: bool = False
    action_on_release: bool = False


@dataclass(frozen=True)
class Firearm:
    kind: str
    blueprint: str
    headless_seed: bool = False
    magazine: Magazine | None = None

    @property
    def path(self) -> str:
        return f"/Game/Classes/Weapons/{self.blueprint}.{self.blueprint}_C"


# Direct Weapon_Master derivatives with the same stock input, ammo debit and
# whole-magazine reload. Capacity and cadence come from cooked class defaults.
ORDINARY_WEAPONS = (
    ("revolver-snub", "Wep_Revolver_SnubNose", 6, 1, 0.2, FORTY_FIVE_BALL),
    ("revolver-snub-02", "Wep_Revolver_SnubNose_02", 6, 1, 0.2, FORTY_FIVE_BALL),
    ("revolver-carbine", "Wep_Revolver_Carbine_01", 6, 1, 0.5, FORTY_FIVE_BALL),
    ("revolver-carbine-scoped", "Wep_Revolver_Carbine_Scoped", 6, 1, 0.5, FORTY_FIVE_BALL),
    ("carbine-45", "Wep_45Carbine", 12, 1, 0.1, FORTY_FIVE_BALL),
    ("carbine-45-scoped", "Wep_45Carbine_Scoped", 12, 1, 0.1, FORTY_FIVE_BALL),
    ("carbine-45-silenced", "Wep_45Carbine_Silenced", 12, 1, 0.1, FORTY_FIVE_BALL),
    ("ktr04", "Wep_KTR04", 16, 1, 0.2, FORTY_FIVE_BALL),
    ("ktr04-scoped", "Wep_KTR04_Scoped", 16, 1, 0.2, FORTY_FIVE_BALL),
    ("bizon-01", "Wep_Bizon_01", 64, 0, 0.085, NINE_MM_BALL),
    ("bizon-02", "Wep_Bizon_02", 64, 0, 0.085, NINE_MM_BALL),
    ("bizon-03", "Wep_Bizon_03", 64, 0, 0.085, NINE_MM_BALL),
    ("pistol-auto", "Wep_Pistol_Auto", 20, 0, 0.06, NINE_MM_BALL),
    ("smg-assault", "Wep_SMG_Assault", 40, 0, 0.075, NINE_MM_BALL),
    ("smg-02", "Wep_SMG_02", 28, 0, 0.1, NINE_MM_BALL),
    ("smg-02-guard", "Wep_SMG_02_Guard", 28, 0, 0.1, NINE_MM_BALL),
    ("smg-03", "Wep_SMG_03", 30, 3, 0.08, NINE_MM_BALL),
    ("smg-03-scoped", "Wep_SMG_03_Scoped", 30, 3, 0.08, NINE_MM_BALL),
    ("smg-03-suppressed", "Wep_SMG_03_Suppressed", 30, 3, 0.08, NINE_MM_BALL),
    ("grease-gun", "Wep_GreaseGun", 28, 0, 0.133333, FORTY_FIVE_BALL),
    ("aek-01", "Wep_AEK_01", 30, 3, 0.04, FORTY_FIVE_BALL),
    ("aek-02", "Wep_AEK_02", 30, 3, 0.04, FORTY_FIVE_BALL),
    ("light-ar-01", "Wep_LightAR_01", 24, 0, 0.1, FIVE_SIX_FMJ),
    ("light-ar-02", "Wep_LightAR_02", 24, 0, 0.1, FIVE_SIX_FMJ),
    ("light-ar-03", "Wep_LightAR_03", 24, 0, 0.1, FIVE_SIX_FMJ),
    ("light-ar-cqc", "Wep_LightAR_CQC", 24, 0, 0.1, FIVE_SIX_FMJ),
    ("light-ar-silenced", "Wep_LightAR_Silenced", 24, 0, 0.1, FIVE_SIX_FMJ),
    ("rifle-57-01", "Wep_5-7AR_01", 50, 4, 0.05, FIVE_SEVEN_AP),
    ("rifle-57-02", "Wep_5-7AR_02", 50, 4, 0.05, FIVE_SEVEN_AP),
    ("rifle-57-03", "Wep_5-7AR_03", 50, 4, 0.05, FIVE_SEVEN_AP),
    ("1911-02", "Wep_1911_02", 16, 4, 0.1, FORTY_FIVE_BALL),
    ("heavy-pistol-silenced", "Wep_HeavyPistol_Silenced", 16, 1, 0.15, FORTY_FIVE_HOLLOW_POINT),
    ("mp28", "Wep_MP28", 20, 0, 0.15, FORTY_FIVE_BALL),
    ("mp28-02", "Wep_MP28_02", 50, 0, 0.15, FORTY_FIVE_BALL),
    ("hmp", "Wep_HMP", 28, 4, 0.08, FORTY_FIVE_BALL),
    ("hmp-03", "Wep_HMP_03", 28, 4, 0.08, FORTY_FIVE_BALL),
    ("hmp-long", "Wep_HMP_Long", 28, 2, 0.08, FORTY_FIVE_BALL),
    ("hmp2", "Wep_HMP2", 24, 0, 0.08, FORTY_FIVE_BALL),
    ("hmp3", "Wep_HMP3", 24, 0, 0.08, FORTY_FIVE_BALL),
    ("tac-hmp2", "Wep_Tac_HMP2", 24, 0, 0.08, FORTY_FIVE_HOLLOW_POINT),
    ("vsk-01", "Wep_VSK_01", 20, 0, 0.0857, FIVE_SEVEN_AP),
    ("rifle-50-auto", "Wep_50Auto", 16, 0, 0.2, FIFTY_CAL_FMJ),
    ("rifle-50-semi", "Wep_50SemiAuto", 6, 1, 0.2, FIFTY_CAL_FMJ),
    ("battle-rifle-01", "Wep_BattleRifle_01", 16, 1, 0.12, THIRTY_06_FMJ),
    ("battle-rifle-02", "Wep_BattleRifle_02", 16, 1, 0.12, THIRTY_06_FMJ),
    ("battle-rifle-03", "Wep_BattleRifle_03", 20, 1, 0.08, THIRTY_06_FMJ),
    ("battle-rifle-03-long", "Wep_BattleRifle_03_Long", 20, 1, 0.08, THIRTY_06_FMJ),
    ("battle-rifle-04", "Wep_BattleRifle_04", 16, 1, 0.12, THIRTY_06_FMJ),
    ("tac-battle-rifle-03", "Wep_Tac_BattleRifle_03", 20, 1, 0.08, THIRTY_06_FMJ),
    ("sg542-01", "Wep_SG542_01", 15, 3, 0.085, THIRTY_06_FMJ),
    ("sg542-02", "Wep_SG542_02", 15, 3, 0.085, THIRTY_06_FMJ),
    ("sg542-03", "Wep_SG542_03", 15, 3, 0.085, THIRTY_06_FMJ),
    ("sg542-04", "Wep_SG542_04", 15, 3, 0.085, THIRTY_06_FMJ),
    ("type33", "Wep_Type33Rifle", 20, 0, 0.1, THIRTY_06_FMJ),
    ("type33-scoped", "Wep_Type33Rifle_Scoped", 20, 0, 0.1, THIRTY_06_FMJ),
    ("type33-tactical", "Wep_Type33Rifle_Tactical", 20, 0, 0.1, THIRTY_06_FMJ),
    ("type33-shorty", "Wep_Type33_Shorty", 20, 0, 0.1, THIRTY_06_FMJ),
    ("type33-lmg", "Wep_Type33Rifle_LMG", 60, 0, 0.1, THIRTY_06_FMJ),
    ("dmr-night-vision", "Wep_NightVis_DMR", 8, 1, 0.2, THIRTY_06_FMJ),
    ("dmr-suppressed", "Wep_Suppressed_DMR", 16, 1, 0.2, THIRTY_06_FMJ),
)

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
    Firearm("bolt-action-shotgun", "Wep_BoltAction_Shotgun", True,
            Magazine(4, 1, 1.3, True, TWELVE_GAUGE_BUCK, reload_first_stack=True, action_on_release=True)),
    Firearm("bolt-action-shotgun-suppressed", "Wep_BoltAction_Shotgun_Suppressed", True,
            Magazine(4, 1, 1.3, True, TWELVE_GAUGE_BUCK, reload_first_stack=True, action_on_release=True)),
    Firearm("sawn-off-shotgun", "Wep_SawnOff_ShotGun", True,
            Magazine(2, 1, 0.1, True, TWELVE_GAUGE_BUCK, reload_first_stack=True)),
    Firearm("model-1931", "Wep_Pistol_Long", True, Magazine(10, 1, 0.1, True, reload_first_stack=True)),
    Firearm("fennec-57", "Wep_Pistol_Fennec_01", True,
            Magazine(17, 1, 0.06, True, FIVE_SEVEN_AP, reload_first_stack=True)),
    Firearm("black-fox", "Wep_Pistol_Fennec_02", True,
            Magazine(17, 1, 0.06, True, FIVE_SEVEN_AP, reload_first_stack=True)),
    *(Firearm(kind, blueprint, True, Magazine(capacity, burst, rate, True, ammo, reload_first_stack=True))
      for kind, blueprint, capacity, burst, rate, ammo in ORDINARY_WEAPONS),
)

# Registered ordinary magazines use the stock primary-ammunition transaction.
TAKE_AMMO_MAGAZINES = {weapon.path: weapon.magazine for weapon in FIREARMS if weapon.magazine}
# The stock graph cycles on a held trigger for automatic and multi-round burst receivers.
# The native input and authority adapters still verify the live receiver and stock functions.
STOCK_HELD_FIRE_MAGAZINES = {
    weapon.kind: magazine for weapon in FIREARMS
    if (magazine := weapon.magazine) and not magazine.action_on_release
    and magazine.burst_length != 1 and magazine.ammo.projectile_count == 1
}
MAX_HELD_FIRE_ROUNDS = max(magazine.capacity for magazine in STOCK_HELD_FIRE_MAGAZINES.values())
BOLT_ACTION_UNLOAD_CAPACITY = 5
BOLT_ACTION_UNLOADS = frozenset(weapon.path for weapon in FIREARMS if weapon.kind in {"nagant-scoped", "obrez"})
BOLT_ACTION_AMMO = frozenset(ammo.item_path for ammo in (THIRTY_06_FMJ, THIRTY_06_INCENDIARY))
MAX_TAKE_AMMO_ROUNDS = max(magazine.capacity for magazine in TAKE_AMMO_MAGAZINES.values())
AMMO_BY_PATH = {ammo.item_path: ammo for ammo in STOCK_AMMO}


def compatible_magazine_round(base: StockAmmo, candidate: StockAmmo) -> bool:
    return base.caliber == candidate.caliber and (
        base == TWELVE_GAUGE_BUCK or base.projectile_count == candidate.projectile_count
    )


AMMO_BY_KIND = {
    firearm.kind: frozenset(ammo.item_path for ammo in STOCK_AMMO
                            if compatible_magazine_round(firearm.magazine.ammo, ammo))
    if firearm.magazine else BOLT_ACTION_AMMO
    for firearm in FIREARMS if firearm.magazine or firearm.path in BOLT_ACTION_UNLOADS
}
