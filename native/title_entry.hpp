#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace RC::LuaMadeSimple { class Lua; }
namespace RC::Unreal { class UObject; class UWorld; }

namespace silverfish::native_title_entry
{
    enum class Phase { opening, ready };

    // The current game-thread observation owns these objects.
    struct Input
    {
        RC::Unreal::UWorld* world{};
        RC::Unreal::UObject* controller{};
        Phase phase{};
        bool listening{}, server{}, has_local_pawn{};
        std::int64_t clients{};
    };

    struct Observation
    {
        RC::Unreal::UObject *controller{}, *menu{}, *viewport{};
        std::optional<int> count;
        std::optional<bool> top, mounted, visible;
        std::string_view reason;

        bool ready() const { return reason.empty(); }
    };

    struct Rows
    {
        std::string title;
        std::optional<std::string> startup;
    };

    struct SaveRoutingPaths
    {
        std::string raw_root;
        std::string absolute_root;
        std::string directory;
    };

    std::optional<SaveRoutingPaths> resolve_save_routing_paths(std::string_view id, std::string_view& error);
    std::optional<Observation> observe(const Input& input);
    std::optional<Rows> rows(const Input& input, std::optional<std::uint64_t> startup_sequence);
    void register_lua(RC::LuaMadeSimple::Lua& lua);
}
