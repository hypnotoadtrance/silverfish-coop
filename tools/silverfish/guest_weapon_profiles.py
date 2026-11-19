"""Exact stock guest firearm profiles shared by the launcher and game adapters."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BallAmmo:
    symbol: str
    caliber: int
    row: str
    item: str
    projectile: str
    fixture_units: int

    @property
    def item_path(self) -> str:
        return f"/Game/Classes/Items/{self.item}.{self.item}_C"

    @property
    def projectile_path(self) -> str:
        return f"/Game/Classes/Weapons/Wep_Assets/Projectiles/{self.projectile}.{self.projectile}_C"


NINE_MM_BALL = BallAmmo("nine_mm_ball", 0, "9mm_Ball", "Ammo_Pistol_BP", "Projectile_Pistol_Light", 9)
FORTY_FIVE_BALL = BallAmmo("forty_five_ball", 1, "45Cal_Ball", "Ammo_Magnum_BP", "Projectile_Pistol_Magnum", 10)
BALL_AMMO = (NINE_MM_BALL, FORTY_FIVE_BALL)


@dataclass(frozen=True)
class Magazine:
    capacity: int
    burst_length: int
    fire_rate: float
    require_default_payload: bool = False
    ammo: BallAmmo = NINE_MM_BALL


@dataclass(frozen=True)
class Firearm:
    kind: str
    blueprint: str
    headless_seed: bool = False
    ball_magazine: Magazine | None = None

    @property
    def path(self) -> str:
        return f"/Game/Classes/Weapons/{self.blueprint}.{self.blueprint}_C"


FIREARMS = (
    Firearm("smg", "Wep_SMG_UBGL"),
    Firearm("smg-standard", "Wep_SMG_01", True, Magazine(30, 0, 0.075)),
    Firearm("light-pistol", "Wep_LightPistol", True),
    Firearm("light-pistol-suppressed", "Wep_LightPistol_Suppressed", True, Magazine(14, 1, 0.05)),
    Firearm("nagant-scoped", "Wep_Nagant_Scoped"),
    Firearm("army-pistol", "Wep_ArmyPistol", True, Magazine(12, 1, 0.08, True)),
    Firearm("pistol", "Wep_Pistol", True, Magazine(12, 1, 0.08, True)),
    Firearm("pistol-02", "Wep_Pistol02", True, Magazine(12, 1, 0.1, True)),
    Firearm("tac-pistol", "Wep_TacPistol", True, Magazine(18, 1, 0.07, True)),
    Firearm("1911", "Wep_1911", True, Magazine(8, 1, 0.1, True, FORTY_FIVE_BALL)),
    Firearm("1911-fancy", "Wep_1911_Fancy", True, Magazine(8, 1, 0.1, True, FORTY_FIVE_BALL)),
    Firearm("heavy-pistol", "Wep_HeavyPistol", True, Magazine(12, 1, 0.15, True, FORTY_FIVE_BALL)),
)
