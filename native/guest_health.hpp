#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>
#include <string>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; }

namespace silverfish::native_guest_health
{
    struct ClientSeat
    {
        RC::Unreal::UObject *pawn{}, *controller{}, *world{}, *instance{};
    };

    // Borrow the live seat certified by the client damage guard.
    std::optional<ClientSeat> client_damage_seat(std::string_view session, std::string_view epoch);
    void register_lua(RC::LuaMadeSimple::Lua& lua);

    struct Prepared
    {
        std::array<std::optional<double>, 9> values;
        std::size_t count{};
        std::uint64_t binding{};
    };

    std::optional<Prepared> prepare(std::span<const std::string_view> fields);
    // True game readback fields, starting with a tab and ending with a newline.
    std::optional<std::string> apply(const Prepared& operation);
}
