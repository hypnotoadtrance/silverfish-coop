#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UWorld; }

namespace silverfish::native_guest_melee
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void uninstall_hooks();
    bool ordinary_world(RC::Unreal::UWorld* world);
}
