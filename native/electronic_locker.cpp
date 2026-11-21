#include "electronic_locker.hpp"
#include "authored_loot_registry.hpp"

#include <array>

#include <Unreal/FWeakObjectPtr.hpp>

namespace
{
    namespace U = RC::Unreal;
    using silverfish::authored_loot::Profile;
    constexpr auto& profiles = silverfish::authored_loot::profiles;

    struct Receipt
    {
        U::FWeakObjectPtr crate;
        std::uint64_t sequence{};
    };

    std::array<Receipt, profiles.size()> receipts;
    std::uint64_t latest{};

    Receipt& receipt(const Profile& profile)
    {
        return receipts[&profile - profiles.data()];
    }
}

namespace silverfish::electronic_locker
{
    bool is_electronic(const Profile& profile)
    {
        return profile.stock_class == &authored_loot::electronic_locker;
    }

    void observe_power_open(U::UObject* crate, const Profile& profile)
    {
        auto& mark = receipt(profile);
        if (mark.crate.Get() == crate || latest >= (std::uint64_t{1} << 53) - 1) return;
        mark = {U::FWeakObjectPtr(crate), ++latest};
    }

    std::uint64_t pending_power_open(U::UObject* crate, const Profile& profile)
    {
        const auto& mark = receipt(profile);
        return mark.crate.Get() == crate ? mark.sequence : 0;
    }

    bool consume_power_open(U::UObject* crate, const Profile& profile, std::uint64_t sequence)
    {
        auto& mark = receipt(profile);
        if (!sequence || mark.sequence != sequence || mark.crate.Get() != crate) return false;
        mark = {};
        return true;
    }

    void clear() { receipts = {}; }
}
