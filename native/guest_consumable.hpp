#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UClass;
}

namespace silverfish::native_guest_consumable
{
    bool qualifies_personal_consumable(RC::Unreal::UClass* type);
    bool qualifies_mail_package(RC::Unreal::UClass* type);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
