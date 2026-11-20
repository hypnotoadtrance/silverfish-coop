#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class AActor; class UObject; class UWorld; }

namespace silverfish::native_mindcore
{
    RC::Unreal::AActor* qualified_instructions_cradle(RC::Unreal::UWorld* world, RC::Unreal::UObject* canonical,
                                                     RC::Unreal::UObject* station);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
