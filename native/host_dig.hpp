#pragma once

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::native_host_dig
{
    void install_hooks();
    void uninstall_hooks();
    void clear_scope();
    void register_lua(RC::LuaMadeSimple::Lua&);
}
