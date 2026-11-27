#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal { class UObject; }

namespace silverfish::native_loot_crate_input
{
    void observe_hinged_pick(RC::Unreal::UObject* actor);
    bool consume_vehicle_pry(RC::Unreal::UObject* crate, RC::Unreal::UObject* pawn);
    bool consume_guest_open(RC::Unreal::UObject* crate, RC::Unreal::UObject* pawn, std::uint64_t sequence);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
