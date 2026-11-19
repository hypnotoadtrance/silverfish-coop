#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace silverfish::native_shop_view
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
