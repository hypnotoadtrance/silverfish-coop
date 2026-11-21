#pragma once

#include "guest_weapon_registry.hpp"

#include <algorithm>
#include <array>

namespace silverfish::native_guest_drop_items
{
    inline constexpr auto pistol = STR("/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C");

    struct AmmoDisplay
    {
        const guest_weapon_profiles::StockAmmo* stock;
        const RC::CharType* mesh;
    };

    inline constexpr std::array ammo{
        AmmoDisplay{&guest_weapon_profiles::nine_mm_ball,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_Pistol.SM_Ammo_Pistol")},
        AmmoDisplay{&guest_weapon_profiles::forty_five_ball,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_45_ACP.SM_Ammo_45_ACP")},
        AmmoDisplay{&guest_weapon_profiles::twelve_gauge_buck,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_ShotGum.SM_Ammo_ShotGum")},
        AmmoDisplay{&guest_weapon_profiles::five_seven_ap,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_5-7_AP.SM_Ammo_5-7_AP")},
    };

    [[nodiscard]] inline const AmmoDisplay* find_ammo(RC::StringViewType path)
    {
        const auto match = std::ranges::find_if(ammo, [&](const auto& row) { return path == row.stock->item; });
        return match == ammo.end() ? nullptr : &*match;
    }
}
