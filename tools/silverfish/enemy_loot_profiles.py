"""Exact placed NFTA CQC encounters qualified for typed shotgun shell loot."""

from dataclasses import dataclass

from .travel import DEAD_BRIDGE, REFINERY


ENEMY_CLASS = "NPC_State_Infantry_CQC_C"
SHELL_CLASS = "Ammo_ShotgunShells_BP_C"
SHELLS = f"/Game/Classes/Items/Ammo_ShotgunShells_BP.{SHELL_CLASS}"


@dataclass(frozen=True)
class Profile:
    id: str
    map: str
    actor_indices: tuple[int, ...]

    @property
    def world(self) -> str:
        return self.map + "." + self.map.rsplit("/", 1)[1]

    @property
    def actors(self) -> tuple[str, ...]:
        return tuple(f"{ENEMY_CLASS} {self.world}:PersistentLevel.{ENEMY_CLASS}_{index}" for index in self.actor_indices)

    @property
    def shell_prefix(self) -> str:
        return f"{SHELL_CLASS} {self.world}:PersistentLevel.{SHELL_CLASS}_"

    def owns_shell(self, actor) -> bool:
        if not isinstance(actor, str) or not actor.startswith(self.shell_prefix):
            return False
        suffix = actor.removeprefix(self.shell_prefix)
        return suffix.isascii() and suffix.isdecimal()


PROFILES = (
    Profile("dead-bridge-cqc-v1", DEAD_BRIDGE, (0, 1)),
    Profile("refinery-cqc-v1", REFINERY, (1, 3)),
)
BY_MAP = {profile.map: profile for profile in PROFILES}
BY_ACTOR = {actor: profile for profile in PROFILES for actor in profile.actors}


def for_roll(roll) -> Profile | None:
    area = roll.get("map")
    profile = BY_MAP.get(area) if isinstance(area, str) else None
    if profile and roll.get("bandit") in profile.actors and (
            roll.get("profile") == profile.id or ("profile" not in roll and profile.map == DEAD_BRIDGE)):
        return profile
    return None
