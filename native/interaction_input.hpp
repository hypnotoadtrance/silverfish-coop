#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
}

namespace silverfish::native_interaction_input
{
    bool consume_current_press(RC::Unreal::UObject* pawn);
    RC::Unreal::UObject* current_guest_pawn(std::uintptr_t address);
    bool guest_interaction_in_reach(RC::Unreal::UObject* pawn, RC::Unreal::UObject* actor, RC::Unreal::UObject* component);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
