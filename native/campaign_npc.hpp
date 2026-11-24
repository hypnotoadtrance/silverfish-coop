#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
}

namespace silverfish::campaign_npc
{
    bool is_spawned_stock_npc(RC::Unreal::UObject* actor);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
