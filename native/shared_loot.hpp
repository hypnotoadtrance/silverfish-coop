#pragma once

#include <array>
#include <cstddef>
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

namespace silverfish::authored_loot
{
    struct Profile;
}

namespace silverfish::shared_loot
{
    inline constexpr std::size_t max_container_slots = 7;
    using ContainerAddresses = std::array<std::uintptr_t, max_container_slots>;
    using ContainerActors = std::array<RC::Unreal::UObject*, max_container_slots>;

    struct EscrowOwner
    {
        RC::Unreal::UWorld* world{};
        RC::Unreal::UObject* pawn{};
        RC::Unreal::UObject* save{};
        RC::Unreal::UObject* stock{};
    };

    const authored_loot::Profile* authored_profile(RC::Unreal::UObject* crate);
    RC::Unreal::UWorld* authored_crate_world(RC::Unreal::UObject* crate, std::string_view* reason = nullptr);
    bool qualified_crate_settings(RC::Unreal::UObject* crate, std::string_view* reason = nullptr);
    bool jiggle_vehicle_lock(RC::Unreal::UObject* crate);
    bool read_spawners(RC::Unreal::UObject* crate, RC::Unreal::UObject* instance, ContainerActors& spawners,
                       std::string_view* reason = nullptr);
    int qualified_container_capacity(std::uintptr_t crate, RC::Unreal::UWorld* world = nullptr);
    bool qualify_escrow_owner(std::uintptr_t pawn, std::uintptr_t save, std::uintptr_t stock, EscrowOwner& owner);
    bool qualify_escrow_owner_in_world(RC::Unreal::UWorld* world, std::uintptr_t pawn, std::uintptr_t save,
                                      std::uintptr_t stock, EscrowOwner& owner);
    bool qualify_stock_drop_capture(RC::Unreal::UWorld* world, std::uintptr_t pawn, std::uintptr_t stock,
                                    EscrowOwner& owner);
    bool verified_escrow_roll(std::uintptr_t crate, const ContainerAddresses& addresses, std::size_t slots,
                              EscrowOwner& owner, ContainerActors& actors, bool consume);
    bool permits_stock_interaction(RC::Unreal::UObject* crate, RC::Unreal::UObject* pawn,
                                   RC::Unreal::UObject* hit, RC::Unreal::UFunction* function);
    bool permits_stock_script(RC::Unreal::UObject* crate, RC::Unreal::UFunction* function);
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
