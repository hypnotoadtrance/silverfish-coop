#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_host_dig
{
    void install_hooks();
    void uninstall_hooks();
    void clear_scope();
    bool manages_buried_source(RC::Unreal::UObject* source);
    void register_lua(RC::LuaMadeSimple::Lua&);
}
