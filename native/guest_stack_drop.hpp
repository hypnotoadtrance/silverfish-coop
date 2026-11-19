#pragma once

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::native_guest_stack_drop
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void uninstall_hooks();
}
