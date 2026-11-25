#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_datasilo_door
{
    bool guest_hinged_lock(RC::Unreal::UObject* actor);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
