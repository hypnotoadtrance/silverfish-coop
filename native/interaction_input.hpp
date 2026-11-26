#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
    class UWorld;
}

namespace silverfish::native_interaction_input
{
    bool has_current_host_press(RC::Unreal::UObject* pawn);
    RC::Unreal::UWorld* current_listen_world();
    bool consume_current_press(RC::Unreal::UObject* pawn);
    RC::Unreal::UObject* current_local_pawn(std::uintptr_t address);
    RC::Unreal::UObject* current_guest_pawn(std::uintptr_t address);
    bool local_interaction_in_reach(RC::Unreal::UObject* pawn, RC::Unreal::UObject* actor, RC::Unreal::UObject* component);
    bool guest_interaction_in_reach(RC::Unreal::UObject* pawn, RC::Unreal::UObject* actor, RC::Unreal::UObject* component);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
