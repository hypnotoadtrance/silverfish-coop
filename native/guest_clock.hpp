#pragma once

#include <cstdint>
#include <optional>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }

namespace silverfish::native_guest_clock
{
    void install_hooks();
    void uninstall_hooks();
    void clear_scope();
    void register_lua(RC::LuaMadeSimple::Lua& lua);

    struct PreparedRefresh
    {
        // These views are valid during the synchronous bridge dispatch only.
        std::string_view epoch, map, pawn, refusal;
        std::int64_t sequence{}, milliseconds{}, previous_sequence{};
        std::uint64_t generation{};
    };

    std::optional<PreparedRefresh> prepare_refresh(std::string_view epoch, std::string_view map,
        std::int64_t sequence, std::int64_t milliseconds);
    bool apply_refresh(const PreparedRefresh& refresh);
}
