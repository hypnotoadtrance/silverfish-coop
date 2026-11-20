#pragma once

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; class UFunction; }

namespace silverfish::native_guest_primary_input
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
    bool matches_stock_spade_started(RC::Unreal::UObject* pawn, RC::Unreal::UFunction* function, const void* arguments);
    bool dispatch_crossroads_spade_fixture(RC::Unreal::UObject* pawn, RC::Unreal::UObject* spade, bool down);
}
