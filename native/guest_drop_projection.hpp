#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal { class UObject; }

namespace silverfish::native_guest_drop_projection
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
    bool owns_actor(RC::Unreal::UObject* actor);
    bool queue_nearby_pickup(RC::Unreal::UObject* actor, RC::Unreal::UObject* pawn);
}
