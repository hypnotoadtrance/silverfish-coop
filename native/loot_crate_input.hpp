#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal { class UObject; }

namespace silverfish::native_loot_crate_input
{
    void observe_hinged_pick(RC::Unreal::UObject* actor);
    bool consume_vehicle_pry(RC::Unreal::UObject* crate, RC::Unreal::UObject* pawn);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
