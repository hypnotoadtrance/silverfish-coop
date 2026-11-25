#pragma once

#include <array>
#include <optional>
#include <span>
#include <vector>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal { class AActor; class UObject; }

namespace silverfish::native_actor_pose
{
    bool set_location_rotation(RC::Unreal::AActor* actor, const std::array<double, 3>& position,
                               const std::array<double, 3>& rotation);
}

namespace silverfish::native_guest_drop_projection
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
    bool owns_actor(RC::Unreal::UObject* actor);
    std::vector<RC::Unreal::UObject*> visible_nearby_actors(RC::Unreal::UObject* pawn,
                                                           std::span<RC::Unreal::UObject* const> shown);
    std::optional<int> nearby_stack_count(RC::Unreal::UObject* actor, RC::Unreal::UObject* pawn);
    bool queue_nearby_pickup(RC::Unreal::UObject* actor, RC::Unreal::UObject* pawn);
}
