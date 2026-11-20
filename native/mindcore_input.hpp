#pragma once

#include <Unreal/Hooks.hpp>

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::mindcore_input
{
    void route(RC::Unreal::Hook::TCallbackIterationData<void>& call, RC::Unreal::UObject* actor,
               RC::Unreal::UFunction* function, const void* parameters);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
}
