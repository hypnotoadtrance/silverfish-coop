#include "session_observation.hpp"
#include "world_path.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <utility>

#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/GameplayStatics.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace session = silverfish::session_observation;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr std::string_view title = "/Game/Classes/Maps/MainMenu_P";
    constexpr int max_players = 16;

    bool available()
    {
        return U::IsInGameThreadRaw() && U::UnrealInitializer::ShouldUseHashTables();
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::UClass* loaded_class(const U::TCHAR* path)
    {
        return U::Cast<U::UClass>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
    }

    U::UClass* cached_class(U::FWeakObjectPtr& cache, const U::TCHAR* path)
    {
        if (auto* type = cache.Get()) return static_cast<U::UClass*>(type);
        auto* type = loaded_class(path);
        cache = type;
        return type;
    }

    template <typename Property>
    Property* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<Property>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object_value(U::FObjectProperty* property, const void* container)
    {
        return property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container));
    }

    U::UObject* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        return property ? object_value(property, object) : nullptr;
    }

    U::FObjectProperty* array_element(U::FArrayProperty* property)
    {
        auto* element = property ? U::CastField<U::FObjectProperty>(property->GetInner()) : nullptr;
        return element && element->GetArrayDim() == 1 ? element : nullptr;
    }

    bool false_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property && !property->GetPropertyValueInContainer(object);
    }

    U::UClass* stock_pawn_class()
    {
        static U::FWeakObjectPtr type;
        return cached_class(type, pawn_path);
    }

    bool read_stock_pawn(U::UWorld* world, U::AActor* actor, bool read_health, session::Pawn& result)
    {
        struct Schema
        {
            U::FWeakObjectPtr pawn_class, save_class;
            U::FBoolProperty* destroyed{};
            U::FByteProperty* role{};
            U::FDoubleProperty* maximum{};
            U::FObjectProperty* save{};
            U::FDoubleProperty* health{};
        };
        static Schema schema;
        auto* pawn_class = stock_pawn_class();
        if (!pawn_class || !live(actor) || actor->GetClassPrivate() != pawn_class || actor->GetWorld() != world)
            return false;
        if (schema.pawn_class.Get() != pawn_class)
        {
            schema = {};
            schema.pawn_class = pawn_class;
            schema.destroyed = field<U::FBoolProperty>(pawn_class, STR("bActorIsBeingDestroyed"));
            schema.role = field<U::FByteProperty>(pawn_class, STR("Role"));
            schema.maximum = field<U::FDoubleProperty>(pawn_class, STR("MaxHealth"));
            schema.save = field<U::FObjectProperty>(pawn_class, STR("Save"));
        }
        if (!schema.destroyed || schema.destroyed->GetPropertyValueInContainer(actor) || !schema.role || !schema.maximum)
            return false;
        const auto role = schema.role->GetPropertyValueInContainer(actor);
        // Simulated proxies still have a stock location and MaxHealth on a guest.
        if (role < 1 || role > 3) return false;

        const auto point = actor->K2_GetActorLocation();
        const auto maximum = schema.maximum->GetPropertyValueInContainer(actor);
        if (!std::isfinite(point.X()) || !std::isfinite(point.Y()) || !std::isfinite(point.Z()) || !std::isfinite(maximum))
            return false;

        U::UObject* save{};
        std::optional<double> health;
        if (read_health)
        {
            auto* save_class = static_cast<U::UClass*>(schema.save_class.Get());
            if (!save_class)
            {
                save_class = loaded_class(save_path);
                schema.save_class = save_class;
                schema.health = field<U::FDoubleProperty>(save_class, STR("PlayerHealth"));
            }
            if (!save_class || !schema.save || schema.save->GetPropertyClass().Get() != save_class || !schema.health)
                return false;
            save = object_value(schema.save, actor);
            if (save)
            {
                if (!live(save) || save->GetClassPrivate() != save_class) return false;
                const auto value = schema.health->GetPropertyValueInContainer(save);
                if (std::isfinite(value)) health = value;
                else save = nullptr;
            }
        }
        result.actor = actor;
        result.x = point.X();
        result.y = point.Y();
        result.z = point.Z();
        result.max_health = maximum;
        result.save = save;
        result.health = health;
        return true;
    }

    bool controlled_actor(U::UObject* actor, U::UClass* type, U::UWorld* world, int role)
    {
        if (!live(actor) || actor->GetClassPrivate() != type || !false_field(actor, STR("bActorIsBeingDestroyed")) ||
            static_cast<U::AActor*>(actor)->GetWorld() != world) return false;
        auto* actor_role = field<U::FByteProperty>(actor, STR("Role"));
        return actor_role && actor_role->GetPropertyValueInContainer(actor) == role;
    }

    U::UObject* local_controller(U::UWorld* world, U::UClass* type, int role)
    {
        static U::FWeakObjectPtr instance_class, local_class;
        auto* game_type = cached_class(instance_class, instance_path);
        auto* player_type = cached_class(local_class, STR("/Script/Engine.LocalPlayer"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        if (!game_type || !player_type || !live(instance) || instance->GetClassPrivate() != game_type) return nullptr;
        auto* local_players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* element = array_element(local_players);
        if (!element) return nullptr;
        U::FScriptArrayHelper locals(local_players, local_players->ContainerPtrToValuePtr<void>(instance));
        if (locals.Num() != 1) return nullptr;
        auto* player = object_value(element, locals.GetRawPtr());
        if (!live(player) || !player->IsA(player_type)) return nullptr;
        auto* controller = object_field(player, STR("PlayerController"));
        return controlled_actor(controller, type, world, role) && object_field(controller, STR("Player")) == player &&
            false_field(controller, STR("Loading")) ? controller : nullptr;
    }

    U::AActor* possessed_pawn(U::UObject* controller, U::UWorld* world, int role)
    {
        auto* pawn = object_field(controller, STR("Pawn"));
        return controlled_actor(pawn, stock_pawn_class(), world, role) && object_field(pawn, STR("Controller")) == controller
            ? static_cast<U::AActor*>(pawn) : nullptr;
    }

    bool append_pawn(session::Snapshot& snapshot, U::AActor* actor, U::UObject* controller, bool read_health)
    {
        if (snapshot.pawns.size() >= max_players ||
            std::ranges::find(snapshot.pawns, actor, &session::Pawn::actor) != snapshot.pawns.end()) return false;
        session::Pawn pawn;
        if (!read_stock_pawn(snapshot.world, actor, read_health, pawn)) return false;
        // Match UE4SS's LuaUObject GetFullName encoding, including mounted content.
        pawn.name = RC::to_string(actor->GetFullName());
        if (!pawn.name.starts_with("Player_FP_C ") || pawn.name.find("Default__") != pawn.name.npos) return false;
        if (controller)
        {
            pawn.controller_name = RC::to_string(controller->GetFullName());
            if (!pawn.controller_name.starts_with("PlayerControler_FP_C ")) return false;
        }
        pawn.controller = controller;
        snapshot.pawns.push_back(std::move(pawn));
        return true;
    }

    bool append_guest_view(session::Snapshot& snapshot, U::AActor* local, bool read_health)
    {
        auto* type = stock_pawn_class();
        if (!type) return false;
        std::vector<U::AActor*> actors;
        U::UGameplayStatics::GetAllActorsOfClass(snapshot.world, type, actors);
        if (std::ranges::find(actors, local) == actors.end()) return false;
        for (auto* actor : actors)
        {
            if (actor == local || !live(actor) || actor->GetWorld() != snapshot.world) continue;
            auto* actual_type = actor->GetClassPrivate();
            if (actual_type != type)
            {
                // A mounted class could use the same short name. Let the existing census handle it.
                if (actual_type && actual_type->GetFName() == type->GetFName()) return false;
                continue;
            }
            auto* destroying = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
            if (!destroying) return false;
            if (destroying->GetPropertyValueInContainer(actor)) continue;
            // Unreal normally replicates only the guest's own controller.
            if (!append_pawn(snapshot, actor, nullptr, read_health)) return false;
        }
        return true;
    }

    std::optional<double> connection_timeout(U::UObject* driver)
    {
        if (auto* seconds = field<U::FFloatProperty>(driver, STR("ConnectionTimeout")))
            return seconds->GetPropertyValueInContainer(driver);
        if (auto* seconds = field<U::FDoubleProperty>(driver, STR("ConnectionTimeout")))
            return seconds->GetPropertyValueInContainer(driver);
        return {};
    }

    void finish_roster(std::vector<session::Pawn>& pawns)
    {
        std::array<bool, max_players> shared_saves{};
        for (std::size_t first = 0; first < pawns.size(); ++first)
        {
            if (!pawns[first].save) continue;
            for (std::size_t second = first + 1; second < pawns.size(); ++second)
            {
                if (pawns[second].save == pawns[first].save) shared_saves[first] = shared_saves[second] = true;
            }
        }
        std::array<session::Pawn*, max_players> guests{};
        for (std::size_t index = 0; index < pawns.size(); ++index)
        {
            if (shared_saves[index])
            {
                pawns[index].health.reset();
                pawns[index].save = nullptr;
            }
            guests[index] = &pawns[index];
        }
        const auto end = guests.begin() + pawns.size();
        std::sort(guests.begin() + 1, end, [](const auto* first, const auto* second) { return first->name < second->name; });
        for (auto next = guests.begin() + 1; next != end; ++next)
            (*next)->guest_slot = static_cast<int>(next - guests.begin());
    }
}

bool silverfish::session_observation::read_pawn(U::UWorld* world, U::AActor* actor, bool read_health, Pawn& result)
{
    return available() && live(world) && read_stock_pawn(world, actor, read_health, result);
}

std::optional<silverfish::session_observation::Snapshot> silverfish::session_observation::observe(
    U::UWorld* world, Seat seat, std::string_view expected_map, bool read_health)
{
    if (!available() || !live(world)) return {};
    static U::FWeakObjectPtr world_class, controller_class, net_driver_class, net_connection_class;
    const auto* world_type = cached_class(world_class, STR("/Script/Engine.World"));
    if (!world_type || world->GetClassPrivate() != world_type) return {};
    const bool host = seat == Seat::host;
    const auto path = world->GetPathName();
    if (!world_path::object(std::wstring_view{path})) return {};
    auto map = RC::to_string(path.substr(0, path.find('.')));
    if (map == title || (host && expected_map.empty()) || (!expected_map.empty() && map != expected_map)) return {};

    auto* driver_type = cached_class(net_driver_class, STR("/Script/Engine.NetDriver"));
    auto* connection_type = cached_class(net_connection_class, STR("/Script/Engine.NetConnection"));
    auto* driver = object_field(world, STR("NetDriver"));
    if (!driver_type || !connection_type || !live(driver) || !driver->IsA(driver_type) ||
        object_field(driver, STR("World")) != world) return {};
    auto* server_slot = field<U::FObjectProperty>(driver, STR("ServerConnection"));
    auto* clients_slot = field<U::FArrayProperty>(driver, STR("ClientConnections"));
    auto* element = array_element(clients_slot);
    if (!server_slot || !element) return {};
    auto* server = object_value(server_slot, driver);
    const auto current_connection = [&](U::UObject* connection) {
        return live(connection) && connection->IsA(connection_type) && object_field(connection, STR("Driver")) == driver;
    };
    if (host ? server != nullptr : !current_connection(server)) return {};
    U::FScriptArrayHelper clients(clients_slot, clients_slot->ContainerPtrToValuePtr<void>(driver));
    if (clients.Num() < 0 || clients.Num() >= max_players) return {};
    const auto timeout = connection_timeout(driver);
    if (!timeout || !std::isfinite(*timeout)) return {};

    auto* controller_type = cached_class(controller_class, controller_path);
    const auto local_role = host ? 3 : 2;
    auto* controller = controller_type ? local_controller(world, controller_type, local_role) : nullptr;
    auto* local = controller ? possessed_pawn(controller, world, local_role) : nullptr;
    if (!local) return {};

    Snapshot snapshot{world, driver, server, controller, std::move(map), clients.Num(), *timeout};
    snapshot.pawns.reserve(host ? static_cast<std::size_t>(clients.Num()) + 1 : 4);
    if (!append_pawn(snapshot, local, controller, read_health)) return {};
    if (host)
    {
        for (int index = 0; index < clients.Num(); ++index)
        {
            auto* connection = object_value(element, clients.GetRawPtr(index));
            if (!current_connection(connection)) return {};
            auto* peer = object_field(connection, STR("PlayerController"));
            if (!controlled_actor(peer, controller_type, world, 3) || peer == controller ||
                object_field(peer, STR("Player")) != connection) return {};
            auto* pawn = possessed_pawn(peer, world, 3);
            if (!pawn || !append_pawn(snapshot, pawn, peer, read_health)) return {};
        }
    }
    else if (!append_guest_view(snapshot, local, read_health))
    {
        return {};
    }
    finish_roster(snapshot.pawns);
    return snapshot;
}
