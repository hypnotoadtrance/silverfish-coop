#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::inventory_wipe
{
    void install_screen_hooks();
    void uninstall_screen_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
