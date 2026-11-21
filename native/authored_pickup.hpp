#pragma once

#include <array>
#include <cstdint>
#include <string_view>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal { class UObject; class UWorld; }

namespace silverfish::native_authored_pickup
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
    bool queue_nearby_pickup(RC::Unreal::UObject* screen, RC::Unreal::UObject* pawn,
                             const std::array<std::uint32_t, 4>& guid);
    RC::Unreal::UWorld* qualify_mindcore_results(std::uintptr_t pawn, std::uintptr_t detached, std::uintptr_t canonical,
                                               std::string_view guid);
}
