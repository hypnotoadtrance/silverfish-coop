#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class AActor;
    class UObject;
    class UWorld;
}

namespace silverfish::campaign_npc
{
    bool is_spawned_stock_npc(RC::Unreal::UObject* actor);
    bool is_actor_in_visible_world(RC::Unreal::AActor* actor, RC::Unreal::UWorld* world);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
