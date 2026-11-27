#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UClass;
    class UObject;
}

namespace silverfish::native_quest_rewards
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    bool active_dead_drop(RC::Unreal::UObject* canonical_save, RC::Unreal::UObject* item);
    bool active_missing_contact(RC::Unreal::UObject* canonical_save);
}
