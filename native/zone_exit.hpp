#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class AActor;
    class UWorld;
}

namespace silverfish::native_zone
{
    bool visible_actor_level(RC::Unreal::AActor* actor, RC::Unreal::UWorld* world);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
