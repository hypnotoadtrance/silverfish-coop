#pragma once

#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace RC::Unreal
{
    class AActor;
    class UObject;
    class UWorld;
}

namespace silverfish::session_observation
{
    enum class Seat { host, guest };

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
