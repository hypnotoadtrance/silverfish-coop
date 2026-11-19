#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
}

namespace silverfish::native_quest_rewards
{
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    bool active_crossroads_dead_drop(RC::Unreal::UObject* canonical_save);
}
