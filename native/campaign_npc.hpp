#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::campaign_npc
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
