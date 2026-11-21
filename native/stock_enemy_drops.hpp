#pragma once

#include <cstdint>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }
namespace silverfish::shared_loot { struct EscrowOwner; }

namespace silverfish::stock_enemy_drops
{
    void install_hooks();
    void uninstall_hooks();
    void register_lua(RC::LuaMadeSimple::Lua& lua);
    bool verify_live_drop(std::uintptr_t host, std::uintptr_t actor, std::uint64_t sequence,
                          shared_loot::EscrowOwner& context, RC::Unreal::UObject*& drop);
}
