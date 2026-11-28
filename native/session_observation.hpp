#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace RC::LuaMadeSimple { class Lua; }

namespace RC::Unreal
{
    class AActor;
    class UObject;
    class UWorld;
}

namespace silverfish::session_observation
{
    enum class Seat { host, guest };

    enum class Role { idle, host, client };
    enum class Phase { opening, ready, hosting, joining, connected, disconnected, leaving };
    enum class PhaseAction { host, join, host_travel, leave, title_idle };

    struct PhaseFacts
    {
        std::string_view map;
        bool controller_valid{}, listening{}, server{}, has_local_pawn{}, controller_present{};
    };

    // The map view is borrowed from the current game-thread session owner.
    struct PhaseScope
    {
        Role role{};
        std::string_view expected_map;
        std::optional<std::uint16_t> listen_port;
        bool leaving{};
    };

    struct PhaseObservation
    {
        PhaseScope scope;
        Phase phase{};
    };

    std::optional<PhaseScope> update_phase(PhaseAction action, std::string_view map = {},
                                          std::optional<std::uint16_t> port = {});
    std::optional<PhaseObservation> observe_phase(const PhaseFacts& facts);
    void reset_phase();
    void register_phase_lua(RC::LuaMadeSimple::Lua& lua);

    // These object pointers are only for the synchronous game-thread caller.
    struct Pawn
    {
        RC::Unreal::AActor* actor{};
        RC::Unreal::UObject* controller{};
        RC::Unreal::UObject* save{};
        std::string name, controller_name;
        double x{}, y{}, z{}, max_health{};
        std::optional<double> health;
        int guest_slot{};
    };

    struct Snapshot
    {
        RC::Unreal::UWorld* world{};
        RC::Unreal::UObject* driver{};
        RC::Unreal::UObject* server_connection{};
        RC::Unreal::UObject* controller{};
        std::string map;
        int clients{};
        double connection_timeout{};
        // Local pawn first, followed by the stock connection or actor-query order.
        // Nonzero guest slots are assigned independently in name order.
        std::vector<Pawn> pawns;
    };

    bool read_pawn(RC::Unreal::UWorld* world, RC::Unreal::AActor* actor, bool read_health, Pawn& result);
    std::optional<Snapshot> observe(RC::Unreal::UWorld* world, Seat seat, std::string_view expected_map, bool read_health);
}
