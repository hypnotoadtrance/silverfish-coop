#pragma once

#include <array>
#include <cstdint>
#include <string_view>

namespace RC::LuaMadeSimple
{
    class Lua;
}

namespace RC::Unreal
{
    class UObject;
    class UFunction;
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

    RC::Unreal::UWorld* authored_crate_world(RC::Unreal::UObject* crate, std::string_view* reason = nullptr);
    bool default_crate_settings(RC::Unreal::UObject* crate);
    bool qualify_escrow_owner(std::uintptr_t pawn, std::uintptr_t save, std::uintptr_t stock, EscrowOwner& owner);
    bool qualify_escrow_owner_in_world(RC::Unreal::UWorld* world, std::uintptr_t pawn, std::uintptr_t save,
                                      std::uintptr_t stock, EscrowOwner& owner);
    bool verified_escrow_roll(std::uintptr_t crate, const std::array<std::uintptr_t, 2>& addresses,
                              EscrowOwner& owner, std::array<RC::Unreal::UObject*, 2>& actors, bool consume);
    bool permits_stock_interaction(RC::Unreal::UObject* crate, RC::Unreal::UObject* pawn,
                                   RC::Unreal::UObject* hit, RC::Unreal::UFunction* function);
    bool permits_stock_script(RC::Unreal::UObject* crate, RC::Unreal::UFunction* function);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
