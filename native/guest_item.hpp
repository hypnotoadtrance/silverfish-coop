#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class FStructProperty;
    class UObject;
}

namespace silverfish::native_guest_item
{
    bool take_ammo_supported(RC::Unreal::UObject* item_class, int rounds);
    bool split_stock_stack(RC::Unreal::UObject* context, RC::Unreal::FStructProperty* row_type, const void* input,
                           int amount, void* portion, void* remainder);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void shutdown();
}
