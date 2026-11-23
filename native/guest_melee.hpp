#pragma once

#include <array>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; class UWorld; }

namespace silverfish::native_guest_melee
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void uninstall_hooks();
    bool ordinary_world(RC::Unreal::UWorld* world);
    bool ordinary_weapon(RC::Unreal::UObject* weapon);
    bool apply_stock_control_rotation(RC::Unreal::UObject* camera, RC::Unreal::UObject* controller,
                                      std::array<double, 3>& previous);
    bool restore_stock_camera_rotation(RC::Unreal::UObject* camera, const std::array<double, 3>& previous);
}
