#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }
namespace silverfish::shared_loot { struct EscrowOwner; }

namespace silverfish::native_enemy_loot
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    void clear_scope();
    bool verified_ammo_roll(std::uintptr_t bandit, std::uintptr_t ammo, shared_loot::EscrowOwner& owner,
                            RC::Unreal::UObject*& actor, bool consume);
    bool qualify_escrow_claimant(std::uintptr_t pawn, std::uintptr_t save, std::uintptr_t stock,
                                 shared_loot::EscrowOwner& owner);
}
