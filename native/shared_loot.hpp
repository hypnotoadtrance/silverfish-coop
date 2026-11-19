#pragma once

#include <array>
#include <cstdint>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
    class UWorld;
}

namespace silverfish::shared_loot
{
    struct EscrowOwner
    {
        RC::Unreal::UWorld* world{};
        RC::Unreal::UObject* pawn{};
        RC::Unreal::UObject* save{};
        RC::Unreal::UObject* stock{};
    };

    bool qualify_escrow_owner(std::uintptr_t pawn, std::uintptr_t save, std::uintptr_t stock, EscrowOwner& owner);
    bool verified_escrow_roll(std::uintptr_t crate, const std::array<std::uintptr_t, 2>& addresses,
                              EscrowOwner& owner, std::array<RC::Unreal::UObject*, 2>& actors, bool consume);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
