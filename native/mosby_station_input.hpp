#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal
{
    class UObject;
    class UFunction;
    namespace Hook { template <typename> class TCallbackIterationData; }
}

namespace silverfish::mosby_station_input
{
    void route(RC::Unreal::Hook::TCallbackIterationData<void>& call, RC::Unreal::UObject* actor,
        RC::Unreal::UFunction* function, const void* parameters);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
