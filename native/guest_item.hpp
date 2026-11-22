#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
}

namespace silverfish::native_guest_item
{
    bool take_ammo_supported(RC::Unreal::UObject* item_class, int rounds);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void shutdown();
}
