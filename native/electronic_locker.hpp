#pragma once

#include <cstdint>

namespace RC::Unreal { class UObject; }
namespace silverfish::authored_loot { struct Profile; }

namespace silverfish::electronic_locker
{
    inline constexpr std::int32_t power_open_entry = 0x46;
    inline constexpr std::int32_t power_signal_entry = 0xAE9;

    bool is_electronic(const authored_loot::Profile& profile);
    void observe_power_open(RC::Unreal::UObject* crate, const authored_loot::Profile& profile);
    std::uint64_t pending_power_open(RC::Unreal::UObject* crate, const authored_loot::Profile& profile);
    bool consume_power_open(RC::Unreal::UObject* crate, const authored_loot::Profile& profile, std::uint64_t sequence);
    void clear();
}
