#pragma once

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
}

namespace silverfish::native_guest_regen
{
    bool qualifies_detached_guest(RC::Unreal::UObject* pawn, RC::Unreal::UObject* detached, RC::Unreal::UObject* stock);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
