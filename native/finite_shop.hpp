#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
    class UFunction;
}

namespace silverfish::finite_shop
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void observe_stock_save(RC::Unreal::UObject* shop, RC::Unreal::UFunction* action);
}
