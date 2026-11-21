#pragma once

#include "guest_weapon_registry.hpp"

#include <algorithm>
#include <array>

namespace silverfish::native_guest_drop_items
{
    inline constexpr auto pistol = STR("/Game/Classes/Weapons/Wep_LightPistol.Wep_LightPistol_C");

    enum class StackKind { ammo, food };

    struct StackDisplay
    {
        const RC::CharType* path;
        const RC::CharType* mesh;
        StackKind kind = StackKind::ammo;
    };

    inline constexpr std::array stacks{
        StackDisplay{guest_weapon_profiles::nine_mm_ball.item,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_Pistol.SM_Ammo_Pistol")},
        StackDisplay{guest_weapon_profiles::forty_five_ball.item,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_45_ACP.SM_Ammo_45_ACP")},
        StackDisplay{guest_weapon_profiles::twelve_gauge_buck.item,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_ShotGum.SM_Ammo_ShotGum")},
        StackDisplay{guest_weapon_profiles::five_seven_ap.item,
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Ammo_5-7_AP.SM_Ammo_5-7_AP")},
        StackDisplay{STR("/Game/Classes/Items/Item_MRE.Item_MRE_C"),
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Item_Meal_Pack_01.SM_Item_Meal_Pack_01"),
            StackKind::food},
        StackDisplay{STR("/Game/Classes/Items/Item_CannedPeaches.Item_CannedPeaches_C"),
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_Can_004.SM_Prop_Can_004"),
            StackKind::food},
        StackDisplay{STR("/Game/Classes/Items/Item_CannedBeans.Item_CannedBeans_C"),
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Prop_Can_002.SM_Prop_Can_002"),
            StackKind::food},
        StackDisplay{STR("/Game/Classes/Items/Item_BrownMushroom.Item_BrownMushroom_C"),
            STR("/Game/Assets/StaticMeshes/Mesh_Props/PropActor_Meshes/SM_Env_Plant_Mushroom_03.SM_Env_Plant_Mushroom_03"),
            StackKind::food},
    };

    [[nodiscard]] inline const StackDisplay* find_stack(RC::StringViewType path)
    {
        const auto match = std::ranges::find_if(stacks, [&](const auto& row) { return path == row.path; });
        return match == stacks.end() ? nullptr : &*match;
    }
}
