#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_host_dig
{
    void install_hooks();
    void uninstall_hooks();
    void clear_scope();
    enum class GuestContactState { waiting, missed, found };

    struct GuestContact
    {
        std::array<double, 3> point{}, normal{};
        std::string material;
    };

    GuestContactState read_guest_contact(RC::Unreal::UObject* pawn, std::uint64_t sequence, GuestContact& result);
    bool present_guest_input(RC::Unreal::UObject* pawn, RC::Unreal::UObject* spade, std::uint64_t sequence);
    bool manages_buried_source(RC::Unreal::UObject* source);
    void record_buried_product(RC::Unreal::UObject* package);
    void register_lua(RC::LuaMadeSimple::Lua&);
}
