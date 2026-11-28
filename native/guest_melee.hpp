#pragma once

#include <array>
#include <optional>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; class UWorld; }

namespace silverfish::native_death
{
    bool friendly_fire_pair(RC::Unreal::UObject* attacker, RC::Unreal::UObject* target);
}

namespace silverfish::native_guest_melee
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void uninstall_hooks();
    bool ordinary_world(RC::Unreal::UWorld* world);
    bool ordinary_weapon(RC::Unreal::UObject* weapon);
    bool present_local_input(RC::Unreal::UObject* pawn, RC::Unreal::UObject* weapon, bool down,
                             std::string_view guid = {});
    struct StockCameraPose
    {
        std::array<double, 3> rotation{};
        std::optional<std::array<double, 3>> location;
    };

    bool apply_stock_camera_pose(RC::Unreal::UObject* camera, RC::Unreal::UObject* controller,
                                 const std::optional<StockCameraPose>& pose, StockCameraPose& previous);
    bool restore_stock_camera_pose(RC::Unreal::UObject* camera, const StockCameraPose& previous);
}
