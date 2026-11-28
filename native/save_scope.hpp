#pragma once

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::native_save_scope
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear();
    void uninstall_hooks();
}
