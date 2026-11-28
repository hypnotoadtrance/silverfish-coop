#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <span>
#include <string>
#include <string_view>
#include <utility>

#include <DynamicOutput/DynamicOutput.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/UFunctionStructs.hpp>
#include <Unreal/World.hpp>

#include "black_stone.hpp"
#include "local_bridge.hpp"
#include "guest_item.hpp"
#include "guest_armor.hpp"
#include "guest_melee.hpp"
#include "host_landmine.hpp"
#include "host_dig.hpp"
#include "host_invitation_clipboard.hpp"
#include "session_exit_input.hpp"
#include "listener_bind.hpp"
#include "inventory_census.hpp"
#include "inventory_wipe.hpp"
#include "guest_stack_drop.hpp"
#include "guest_drop_projection.hpp"
#include "inventory_view.hpp"
#include "bag_projection.hpp"
#include "finite_shop.hpp"
#include "interaction_input.hpp"
#include "authored_pickup.hpp"
#include "mindcore.hpp"
#include "mindcore_carry.hpp"
#include "datasilo_lift.hpp"
#include "datasilo_door.hpp"
#include "broker_menu.hpp"
#include "broker_input.hpp"
#include "campaign_npc.hpp"
#include "flood_jammer.hpp"
#include "lenny_quest.hpp"
#include "guest_clock.hpp"
#include "guest_health.hpp"
#include "guest_primary_input.hpp"
#include "guest_consumable.hpp"
#include "guest_regen.hpp"
#include "guest_movement.hpp"
#include "guest_sickness.hpp"
#include "loot_crate_input.hpp"
#include "enemy_loot.hpp"
#include "stock_enemy_drops.hpp"
#include "fresh_clock.hpp"
#include "quest_rewards.hpp"
#include "borough_barter.hpp"
#include "shop_view.hpp"
#include "nothing_return.hpp"
#include "kill_volume.hpp"
#include "anomaly_fields.hpp"
#include "guest_npc.hpp"
#include "guest_reload_input.hpp"
#include "item_roundtrip.hpp"
#include "loot_escrow.hpp"
#include "pickup.hpp"
#include "transport_items.hpp"
#include "save_copy.hpp"
#include "shared_loot.hpp"
#include "stock_rest.hpp"
#include "zone_exit.hpp"

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto mod_name = STR("SilverfishCoopNative");
    constexpr auto lua_mod_name = STR("SilverfishCoop");
    constexpr auto die_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Die");
    constexpr auto die_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:Die");
    constexpr auto damage_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:DealDamage");
    constexpr auto damage_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:DealDamage");
    constexpr auto direct_damage_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:Deal_DirectDamage");
    constexpr auto direct_damage_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:Deal_DirectDamage");
    constexpr auto radiation_damage_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:RadDamage");
    constexpr auto radiation_damage_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:RadDamage");
    constexpr auto offhand_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:OffhandFlashlight");
    constexpr auto offhand_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:OffhandFlashlight");
    constexpr auto mode_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:K2_OnMovementModeChanged");
    constexpr auto mode_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:K2_OnMovementModeChanged");
    constexpr auto landed_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:OnLanded");
    constexpr auto landed_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:OnLanded");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto probe_timeout = std::chrono::seconds(2);
    constexpr auto scope_timeout = std::chrono::milliseconds(1250);
    constexpr auto fall_sample_interval = std::chrono::milliseconds(50);
    constexpr std::uint8_t simulated_role = 1; // ENetRole::ROLE_SimulatedProxy in UE 5.4.
    constexpr std::uint8_t autonomous_role = 2; // ENetRole::ROLE_AutonomousProxy in UE 5.4.
    constexpr std::uint8_t authority_role = 3; // ENetRole::ROLE_Authority in UE 5.4.
    constexpr std::uint8_t falling_mode = 3; // EMovementMode::MOVE_Falling in UE 5.4.
    constexpr double stock_fall_duration = 0.5;
    constexpr std::size_t max_guests = 4;

    // An even generation has no Lua bridge. Refreshing the generation invalidates
    // game-thread authorization without touching Lua from another thread.
    std::atomic<std::uint64_t> bridge_generation{};
    std::atomic<bool> hooks_ready{};
    std::atomic<bool> scope_hooks_ready{};
    std::atomic<bool> client_damage_hooks_ready{};

    struct Probe
    {
        std::uintptr_t pawn_address{};
        Unreal::UFunction* die{};
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool intercepted{};
    };

    struct ScopedPawn
    {
        std::uintptr_t address{};
        Unreal::FWeakObjectPtr object;
        Unreal::FByteProperty* role{};
    };

    struct SaveOwnership
    {
        Unreal::FWeakObjectPtr save;
        Unreal::FWeakObjectPtr instance;
        Unreal::UClass* save_class{};
        Unreal::FObjectProperty* pawn_save{};
        Unreal::FObjectProperty* world_instance{};
        Unreal::FObjectProperty* instance_slot{};
    };

    struct ControllerBinding
    {
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FObjectProperty* pawn_controller{};
        Unreal::FObjectProperty* controller_player{};
        Unreal::FObjectProperty* controller_pawn{};
    };

    struct ListenerBinding
    {
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr stock_save;
        Unreal::FObjectProperty* world_driver{};
        Unreal::FObjectProperty* server_connection{};
        Unreal::FArrayProperty* client_connections{};
        Unreal::FObjectProperty* connection_entry{};
        Unreal::FArrayProperty* local_players{};
    };

    struct FriendlyFire
    {
        Unreal::UFunction* damage{};
        Unreal::FObjectProperty* instigator{};
        Unreal::FDoubleProperty* total_damage{};
        Unreal::FBoolProperty* lethal{};
    };

    struct FallBinding
    {
        Unreal::UFunction* mode_changed{};
        Unreal::UFunction* landed{};
        Unreal::UFunction* time_seconds{};
        Unreal::UObject* gameplay_statics{};
        Unreal::FByteProperty* previous_mode{};
        Unreal::FByteProperty* new_mode{};
        Unreal::FObjectProperty* pawn_movement{};
        Unreal::FByteProperty* movement_mode{};
        Unreal::FStructProperty* velocity{};
        Unreal::FDoubleProperty* z{};
        Unreal::FBoolProperty* just_teleported{};
        Unreal::FStructProperty* hit{};
        Unreal::FBoolProperty* blocking_hit{};
        Unreal::FBoolProperty* start_penetrating{};
        Unreal::FDoubleProperty* fall_time{};
        Unreal::FFloatProperty* time_dilation{};
        Unreal::FDoubleProperty* guest_health{};
        Unreal::FBoolProperty* dead{};
        Unreal::FWeakObjectPtr movement;
    };

    struct FallEpisode
    {
        double start{-1.0};
        float time_dilation{};
    };

    struct FallStatus
    {
        std::int64_t mode_changes{};
        std::int64_t starts{};
        std::int64_t landings{};
        std::int64_t forwarded{};
        std::int64_t mode{-1};
        double elapsed{};
        double vertical_speed{};
        double original_fall_time{};
        bool actor_tick_enabled{};
        std::string_view reason{"no_landing"};
    };

    struct ScopedGuest
    {
        ScopedPawn pawn;
        SaveOwnership ownership;
        ControllerBinding controller;
        FallBinding fall;
        FallEpisode fall_episode;
        FallStatus fall_status;
    };

    struct DeathScope
    {
        ScopedPawn local;
        std::array<ScopedGuest, max_guests> remote;
        std::size_t remote_count{};
        SaveOwnership ownership;
        ControllerBinding local_controller;
        ListenerBinding listener;
        FriendlyFire friendly_fire;
        bool allow_friendly_fire{};
        Unreal::FWeakObjectPtr world;
        Unreal::UFunction* fall_mode{};
        Unreal::UFunction* fall_landed{};
        Clock::time_point next_fall_sample{};
        Unreal::UFunction* die{};
        std::uint64_t generation{};
        std::int64_t token{};
        Clock::time_point expires{};

        [[nodiscard]] std::span<const ScopedGuest> guests() const { return {remote.data(), remote_count}; }
        [[nodiscard]] std::span<ScopedGuest> guests() { return {remote.data(), remote_count}; }
    };

    struct DeathHit
    {
        std::uintptr_t address{};
        std::uint64_t generation{};
        std::int64_t token{};
    };

    thread_local Probe probe;
    thread_local DeathScope death_scope;
    thread_local std::int64_t next_scope_token{};
    thread_local std::array<DeathHit, 3 * (max_guests + 1)> death_hits;
    thread_local std::uint64_t friendly_fire_generation{};
    thread_local std::int64_t friendly_fire_count{};
    thread_local bool warned_missing_damage_frame{};

    [[nodiscard]] bool environment_equals(const char* name, std::string_view expected)
    {
        std::array<char, 8> value{};
        std::size_t length{};
        return getenv_s(&length, value.data(), value.size(), name) == 0 &&
               length == expected.size() + 1 && std::string_view{value.data(), length - 1} == expected;
    }

    [[nodiscard]] bool ready()
    {
        return hooks_ready.load(std::memory_order_acquire) &&
               (bridge_generation.load(std::memory_order_acquire) & 1) != 0;
    }

    int lua_ready(const Lua& lua)
    {
        lua.set_bool(lua.get_stack_size() == 0 && ready());
        return 1;
    }

    [[nodiscard]] Unreal::UFunction* find_player_script(const Unreal::TCHAR* path, const Unreal::TCHAR* full_name)
    {
        auto* function = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(nullptr, nullptr, path);
        if (!function || function->GetFullName() != full_name || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(Unreal::FUNC_Native) || function->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return nullptr;
        }
        return function;
    }

    int lua_arm(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw())
        {
            lua.set_bool(false);
            return 1;
        }

        probe = {};
        const auto generation = bridge_generation.load(std::memory_order_acquire);
        if (!hooks_ready.load(std::memory_order_acquire) || (generation & 1) == 0 ||
            lua.get_stack_size() != 1 || !lua.is_integer())
        {
            lua.set_bool(false);
            return 1;
        }

        const auto address = lua.get_integer();
        if (address <= 0)
        {
            lua.set_bool(false);
            return 1;
        }

        auto* die = find_player_script(die_path, die_full_name);
        if (!die)
        {
            lua.set_bool(false);
            return 1;
        }

        // Lua validates the live authority pawn and owns the lifetime of the
        // synchronous call. The address is compared with the engine context.
        probe = {static_cast<std::uintptr_t>(address), die, generation,
                 Clock::now() + probe_timeout, false};
        lua.set_bool(true);
        return 1;
    }

    int lua_disarm(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            probe = {};
        }
        return 0;
    }

    int lua_take_hit(const Lua& lua)
    {
        bool hit = false;
        if (Unreal::IsInGameThreadRaw())
        {
            hit = lua.get_stack_size() == 0 && probe.intercepted &&
                  probe.generation == bridge_generation.load(std::memory_order_acquire);
            probe = {};
        }
        lua.set_bool(hit);
        return 1;
    }

    [[nodiscard]] ScopedPawn find_scoped_pawn(std::uintptr_t address, Unreal::UFunction* die)
    {
        ScopedPawn result;
        // Check against the engine object registry before dereferencing a Lua
        // address. Capture an object serial so later pointer reuse cannot match.
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, auto, auto) {
            if (reinterpret_cast<std::uintptr_t>(object) != address)
            {
                return RC::LoopAction::Continue;
            }
            if (object->GetClassPrivate() == die->GetOuterPrivate() &&
                !object->HasAnyFlags(Unreal::RF_ClassDefaultObject))
            {
                result.address = address;
                result.object = object;
                result.role = Unreal::CastField<Unreal::FByteProperty>(
                    object->GetPropertyByNameInChain(STR("Role")));
            }
            return RC::LoopAction::Break;
        });
        return result;
    }

    [[nodiscard]] Unreal::UWorld* authority_world(const ScopedPawn& pawn)
    {
        auto* object = pawn.object.Get();
        if (!object || !pawn.role ||
            *pawn.role->ContainerPtrToValuePtr<std::uint8_t>(object) != authority_role)
        {
            return nullptr;
        }
        return static_cast<Unreal::AActor*>(object)->GetWorld();
    }

    [[nodiscard]] Unreal::FObjectProperty* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<Unreal::FObjectProperty>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    [[nodiscard]] Unreal::UObject* read_object(Unreal::FObjectProperty* property, Unreal::UObject* object)
    {
        return property && object ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    [[nodiscard]] bool live_object(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    [[nodiscard]] bool bind_save(SaveOwnership& ownership, Unreal::UObject* pawn, Unreal::UWorld* world)
    {
        ownership.save_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, save_path);
        auto* instance_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, instance_path);
        ownership.pawn_save = object_field(pawn, STR("Save"));
        ownership.world_instance = object_field(world, STR("OwningGameInstance"));
        auto* instance = read_object(ownership.world_instance, world);
        auto* save = read_object(ownership.pawn_save, pawn);
        if (!ownership.save_class || !instance_class || !live_object(instance) || !live_object(save) ||
            instance->GetClassPrivate() != instance_class || save->GetClassPrivate() != ownership.save_class ||
            ownership.pawn_save->GetPropertyClass().Get() != ownership.save_class)
        {
            return false;
        }
        ownership.instance_slot = object_field(instance, STR("SaveSlot_Ref"));
        if (!ownership.instance_slot || ownership.instance_slot->GetPropertyClass().Get() != ownership.save_class)
        {
            return false;
        }
        ownership.save = save;
        ownership.instance = instance;
        return true;
    }

    [[nodiscard]] bool same_detached_save(const SaveOwnership& ownership, Unreal::UObject* pawn, Unreal::UObject* world)
    {
        auto* save = ownership.save.Get();
        auto* instance = ownership.instance.Get();
        if (!live_object(save) || !live_object(instance) || read_object(ownership.pawn_save, pawn) != save ||
            read_object(ownership.world_instance, world) != instance)
        {
            return false;
        }
        auto* stock = read_object(ownership.instance_slot, instance);
        return stock != save && live_object(stock) && stock->GetClassPrivate() == ownership.save_class;
    }

    [[nodiscard]] bool bind_controller(ControllerBinding& ownership, Unreal::UObject* pawn, bool local)
    {
        auto* controller_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(
            nullptr, nullptr, STR("/Script/Engine.PlayerController"));
        auto* player_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(
            nullptr, nullptr, local ? STR("/Script/Engine.LocalPlayer") : STR("/Script/Engine.NetConnection"));
        if (!controller_class || !player_class)
        {
            return false;
        }
        ownership.pawn_controller = object_field(pawn, STR("Controller"));
        ownership.controller_player = object_field(controller_class, STR("Player"));
        ownership.controller_pawn = object_field(controller_class, STR("Pawn"));
        auto* controller = read_object(ownership.pawn_controller, pawn);
        if (!live_object(controller) || !controller->IsA(controller_class) ||
            read_object(ownership.controller_pawn, controller) != pawn)
        {
            return false;
        }
        auto* player = read_object(ownership.controller_player, controller);
        if (!live_object(player) || !player->IsA(player_class))
        {
            return false;
        }
        ownership.controller = controller;
        ownership.player = player;
        return true;
    }

    [[nodiscard]] bool same_controller(const ControllerBinding& ownership, Unreal::UObject* pawn)
    {
        auto* controller = ownership.controller.Get();
        auto* player = ownership.player.Get();
        return live_object(controller) && live_object(player) &&
               read_object(ownership.pawn_controller, pawn) == controller &&
               read_object(ownership.controller_pawn, controller) == pawn &&
               read_object(ownership.controller_player, controller) == player;
    }

    [[nodiscard]] bool controller_in_world(const ControllerBinding& controller, Unreal::UObject* pawn,
                                           Unreal::UObject* world)
    {
        return same_controller(controller, pawn) && static_cast<Unreal::AActor*>(controller.controller.Get())->GetWorld() == world;
    }

    [[nodiscard]] const ScopedGuest* find_guest(const DeathScope& scope, std::uintptr_t address)
    {
        const auto guests = scope.guests();
        const auto match = std::ranges::find_if(guests, [address](const ScopedGuest& guest) { return guest.pawn.address == address; });
        return match == guests.end() ? nullptr : &*match;
    }

    [[nodiscard]] bool includes_pawn(const DeathScope& scope, Unreal::UObject* pawn)
    {
        if (!pawn) return false;
        const auto address = reinterpret_cast<std::uintptr_t>(pawn);
        if (scope.local.address == address) return scope.local.object.Get() == pawn;
        const auto* guest = find_guest(scope, address);
        return guest && guest->pawn.object.Get() == pawn;
    }

    [[nodiscard]] Unreal::FArrayProperty* array_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<Unreal::FArrayProperty>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    [[nodiscard]] Unreal::UObject* only_object(Unreal::FArrayProperty* array, Unreal::UObject* owner)
    {
        const auto* inner = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!inner || !owner)
        {
            return nullptr;
        }
        Unreal::FScriptArrayHelper helper(array, array->ContainerPtrToValuePtr<void>(owner));
        return helper.Num() == 1 ? inner->GetObjectPropertyValue(helper.GetRawPtr()) : nullptr;
    }

    [[nodiscard]] bool bind_listener(DeathScope& scope, Unreal::UWorld* world)
    {
        auto* instance = scope.ownership.instance.Get();
        auto& listener = scope.listener;
        listener.world_driver = object_field(world, STR("NetDriver"));
        auto* driver = read_object(listener.world_driver, world);
        auto* stock = read_object(scope.ownership.instance_slot, instance);
        if (!live_object(driver) || !live_object(stock))
        {
            return false;
        }
        listener.server_connection = object_field(driver, STR("ServerConnection"));
        listener.client_connections = array_field(driver, STR("ClientConnections"));
        listener.connection_entry = listener.client_connections ?
            Unreal::CastField<Unreal::FObjectProperty>(listener.client_connections->GetInner()) : nullptr;
        listener.local_players = array_field(instance, STR("LocalPlayers"));
        listener.driver = driver;
        listener.stock_save = stock;
        return listener.server_connection && listener.connection_entry && listener.local_players;
    }

    [[nodiscard]] bool qualified_roster(const DeathScope& scope)
    {
        const auto& listener = scope.listener;
        auto* world = scope.world.Get();
        auto* instance = scope.ownership.instance.Get();
        auto* driver = listener.driver.Get();
        auto* stock = listener.stock_save.Get();
        if (!world || !live_object(instance) || !live_object(driver) || !live_object(stock) ||
            stock->GetClassPrivate() != scope.ownership.save_class ||
            read_object(scope.ownership.world_instance, world) != instance ||
            read_object(scope.ownership.instance_slot, instance) != stock ||
            read_object(listener.world_driver, world) != driver ||
            read_object(listener.server_connection, driver) || !listener.connection_entry)
        {
            return false;
        }
        if (scope.local.address)
        {
            auto* pawn = scope.local.object.Get();
            auto* local_player = scope.local_controller.player.Get();
            if (!live_object(pawn) || !local_player || world != authority_world(scope.local) ||
                !controller_in_world(scope.local_controller, pawn, world) ||
                read_object(scope.ownership.pawn_save, pawn) != stock ||
                only_object(listener.local_players, instance) != local_player)
            {
                return false;
            }
        }

        const auto guests = scope.guests();
        Unreal::FScriptArrayHelper clients(listener.client_connections, listener.client_connections->ContainerPtrToValuePtr<void>(driver));
        if (clients.Num() != static_cast<int>(guests.size())) return false;
        std::array<Unreal::UObject*, max_guests> connections{};
        for (int index = 0; index < clients.Num(); ++index)
        {
            auto* connection = listener.connection_entry->GetObjectPropertyValue(clients.GetRawPtr(index));
            if (!live_object(connection) || std::ranges::find(connections, connection) != connections.end()) return false;
            connections[static_cast<std::size_t>(index)] = connection;
        }

        std::array<Unreal::UObject*, max_guests> saves{};
        std::array<Unreal::UObject*, max_guests> controllers{};
        for (std::size_t index = 0; index < guests.size(); ++index)
        {
            const auto& guest = guests[index];
            auto* pawn = guest.pawn.object.Get();
            auto* save = guest.ownership.save.Get();
            auto* controller = guest.controller.controller.Get();
            auto* connection = guest.controller.player.Get();
            if (!live_object(pawn) || world != authority_world(guest.pawn) ||
                guest.ownership.instance.Get() != instance || !same_detached_save(guest.ownership, pawn, world) ||
                !controller_in_world(guest.controller, pawn, world) ||
                std::ranges::find(saves, save) != saves.end() || std::ranges::find(controllers, controller) != controllers.end())
            {
                return false;
            }
            const auto match = std::ranges::find(connections, connection);
            if (!connection || match == connections.end()) return false;
            *match = nullptr;
            saves[index] = save;
            controllers[index] = controller;
        }
        return true;
    }

    template <typename Property>
    [[nodiscard]] Property* damage_argument(Unreal::UFunction* function, const Unreal::TCHAR* name, bool output)
    {
        auto* argument = Unreal::CastField<Property>(function->GetPropertyByName(name));
        if (!argument || argument->GetArrayDim() != 1 || !argument->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            argument->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) ||
            argument->HasAnyPropertyFlags(Unreal::CPF_OutParm) != output ||
            (output && argument->HasAnyPropertyFlags(Unreal::CPF_ConstParm)))
        {
            return nullptr;
        }
        const auto offset = argument->GetOffset_Internal();
        const auto size = argument->GetSize();
        return offset >= 0 && size > 0 && offset + size <= function->GetParmsSize() ? argument : nullptr;
    }

    [[nodiscard]] FriendlyFire find_friendly_fire()
    {
        auto* damage = find_player_script(damage_path, damage_full_name);
        if (damage && damage->GetNumParms() == 7)
        {
            auto* instigator = damage_argument<Unreal::FObjectProperty>(damage, STR("Instigator"), false);
            auto* total = damage_argument<Unreal::FDoubleProperty>(damage, STR("TotalDamage"), true);
            auto* lethal = damage_argument<Unreal::FBoolProperty>(damage, STR("IsLethal"), true);
            if (instigator && total && lethal)
            {
                return {damage, instigator, total, lethal};
            }
        }
        RC::Output::send<RC::LogLevel::Warning>(
            STR("[SilverfishCoopNative] Stock DealDamage signature unavailable. Friendly fire follows stock behavior.\n"));
        return {};
    }

    template <typename Property>
    [[nodiscard]] Property* reflected_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<Property>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    struct GameplayTimeParams
    {
        Unreal::UObject* world_context;
        double result;
    };

    [[nodiscard]] FallBinding bind_fall(Unreal::UObject* pawn, Unreal::UObject* save)
    {
        FallBinding binding;
        binding.mode_changed = find_player_script(mode_path, mode_full_name);
        binding.landed = find_player_script(landed_path, landed_full_name);
        if (!binding.mode_changed || binding.mode_changed->GetNumParms() != 4 || binding.mode_changed->GetParmsSize() != 4 ||
            !binding.landed || binding.landed->GetNumParms() != 1)
        {
            return {};
        }
        binding.previous_mode = damage_argument<Unreal::FByteProperty>(binding.mode_changed, STR("PrevMovementMode"), false);
        binding.new_mode = damage_argument<Unreal::FByteProperty>(binding.mode_changed, STR("NewMovementMode"), false);
        binding.hit = reflected_field<Unreal::FStructProperty>(binding.landed, STR("Hit"));
        auto* hit_type = binding.hit ? binding.hit->GetStruct().Get() : nullptr;
        constexpr auto hit_flags = static_cast<Unreal::EPropertyFlags>(
            Unreal::CPF_Parm | Unreal::CPF_ReferenceParm | Unreal::CPF_ConstParm);
        if (!binding.previous_mode || binding.previous_mode->GetOffset_Internal() != 0 || !binding.new_mode ||
            binding.new_mode->GetOffset_Internal() != 1 || !hit_type || hit_type->GetPathName() != STR("/Script/Engine.HitResult") ||
            !binding.hit->HasAllPropertyFlags(hit_flags) || binding.hit->GetOffset_Internal() != 0 ||
            binding.hit->GetSize() != binding.landed->GetParmsSize())
        {
            return {};
        }
        binding.blocking_hit = reflected_field<Unreal::FBoolProperty>(hit_type, STR("bBlockingHit"));
        binding.start_penetrating = reflected_field<Unreal::FBoolProperty>(hit_type, STR("bStartPenetrating"));

        binding.pawn_movement = object_field(pawn, STR("CharacterMovement"));
        auto* movement = read_object(binding.pawn_movement, pawn);
        auto* movement_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(
            nullptr, nullptr, STR("/Script/Engine.CharacterMovementComponent"));
        if (!live_object(movement) || !movement_class || !movement->IsA(movement_class))
        {
            return {};
        }
        binding.movement = movement;
        binding.movement_mode = reflected_field<Unreal::FByteProperty>(movement, STR("MovementMode"));
        binding.just_teleported = reflected_field<Unreal::FBoolProperty>(movement, STR("bJustTeleported"));
        binding.velocity = reflected_field<Unreal::FStructProperty>(movement, STR("Velocity"));
        auto* vector = binding.velocity ? binding.velocity->GetStruct().Get() : nullptr;
        if (!vector || vector->GetPathName() != STR("/Script/CoreUObject.Vector"))
        {
            return {};
        }
        binding.z = reflected_field<Unreal::FDoubleProperty>(vector, STR("Z"));
        binding.fall_time = reflected_field<Unreal::FDoubleProperty>(pawn, STR("FallTime"));
        binding.time_dilation = reflected_field<Unreal::FFloatProperty>(pawn, STR("CustomTimeDilation"));
        binding.guest_health = reflected_field<Unreal::FDoubleProperty>(save, STR("PlayerHealth"));
        binding.dead = reflected_field<Unreal::FBoolProperty>(pawn, STR("Dead"));

        binding.time_seconds = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(
            nullptr, nullptr, STR("/Script/Engine.GameplayStatics:GetTimeSeconds"));
        binding.gameplay_statics = Unreal::UObjectGlobals::StaticFindObject<Unreal::UObject*>(
            nullptr, nullptr, STR("/Script/Engine.Default__GameplayStatics"));
        auto* time = binding.time_seconds;
        auto* context = time ? damage_argument<Unreal::FObjectProperty>(time, STR("WorldContextObject"), false) : nullptr;
        auto* result = time ? Unreal::CastField<Unreal::FDoubleProperty>(time->GetReturnProperty()) : nullptr;
        constexpr auto time_flags = static_cast<Unreal::EFunctionFlags>(Unreal::FUNC_Native | Unreal::FUNC_Static);
        if (!binding.blocking_hit || !binding.start_penetrating || !binding.movement_mode || !binding.just_teleported ||
            !binding.z || !binding.fall_time || !binding.time_dilation || !binding.guest_health || !binding.dead ||
            !binding.gameplay_statics || !time || !time->HasAllFunctionFlags(time_flags) ||
            time->GetOuterPrivate() != binding.gameplay_statics->GetClassPrivate() || time->GetNumParms() != 2 ||
            time->GetParmsSize() != sizeof(GameplayTimeParams) || !context || !result ||
            context->GetOffset_Internal() != offsetof(GameplayTimeParams, world_context) ||
            result->GetOffset_Internal() != offsetof(GameplayTimeParams, result))
        {
            return {};
        }
        return binding;
    }

    [[nodiscard]] bool configure_scope(std::uintptr_t host_address, std::span<std::uintptr_t> addresses,
                                        bool allow_friendly_fire)
    {
        const auto generation = bridge_generation.load(std::memory_order_acquire);
        if (!scope_hooks_ready.load(std::memory_order_acquire) || (generation & 1) == 0) return false;
        std::ranges::sort(addresses);
        if (std::ranges::find(addresses, std::uintptr_t{}) != addresses.end() ||
            std::ranges::find(addresses, host_address) != addresses.end() ||
            std::ranges::adjacent_find(addresses) != addresses.end()) return false;
        if (death_scope.generation == generation && death_scope.local.address == host_address &&
            std::ranges::equal(death_scope.guests(), addresses,
                [](const ScopedGuest& guest, std::uintptr_t address) { return guest.pawn.address == address; }))
        {
            if (!qualified_roster(death_scope)) return false;
            const auto now = Clock::now();
            if (now >= death_scope.expires)
            {
                for (auto& guest : death_scope.guests()) guest.fall_episode = {};
            }
            death_scope.allow_friendly_fire = allow_friendly_fire;
            death_scope.expires = now + scope_timeout;
            return true;
        }

        DeathScope next;
        next.die = find_player_script(die_path, die_full_name);
        if (!next.die) return false;
        Unreal::UWorld* world = nullptr;
        if (host_address)
        {
            next.local = find_scoped_pawn(host_address, next.die);
            auto* host = next.local.object.Get();
            world = authority_world(next.local);
            if (!world || !bind_save(next.ownership, host, world) ||
                !bind_controller(next.local_controller, host, true)) return false;
        }
        next.remote_count = addresses.size();
        for (std::size_t index = 0; index < addresses.size(); ++index)
        {
            auto& guest = next.remote[index];
            guest.pawn = find_scoped_pawn(addresses[index], next.die);
            auto* pawn = guest.pawn.object.Get();
            auto* guest_world = authority_world(guest.pawn);
            if (!guest_world || (world && guest_world != world) || !bind_save(guest.ownership, pawn, guest_world) ||
                !bind_controller(guest.controller, pawn, false)) return false;
            world = guest_world;
            if (!host_address) next.ownership = guest.ownership;
        }
        if (!world) return false;
        next.world = world;
        if (!bind_listener(next, world) || !qualified_roster(next)) return false;
        if (host_address)
        {
            if (!addresses.empty()) next.friendly_fire = find_friendly_fire();
            for (auto& guest : next.guests())
            {
                guest.fall = bind_fall(guest.pawn.object.Get(), guest.ownership.save.Get());
                if (guest.fall.mode_changed && guest.fall.landed)
                {
                    next.fall_mode = guest.fall.mode_changed;
                    next.fall_landed = guest.fall.landed;
                }
            }
        }
        next.allow_friendly_fire = allow_friendly_fire;
        next.generation = generation;
        next.token = ++next_scope_token;
        next.expires = Clock::now() + scope_timeout;
        death_scope = next;
        return true;
    }

    [[nodiscard]] bool read_scope(const Lua& lua, bool roster, bool has_rule)
    {
        if (has_rule && !lua.is_bool(1)) return false;
        const bool allow_friendly_fire = has_rule && lua.get_bool();
        const auto count = lua.get_stack_size();
        if (roster ? count < 1 || count > 1 + static_cast<int>(max_guests) : count != 2) return false;
        for (int index = 1; index <= count; ++index)
        {
            if (!lua.is_integer(index)) return false;
        }

        // LuaMadeSimple pops the value at index 1 on each read.
        const auto host = lua.get_integer();
        if (host < 0 || (roster && host == 0)) return false;
        std::array<std::uintptr_t, max_guests> storage{};
        const auto addresses = std::span{storage}.first(static_cast<std::size_t>(count - 1));
        for (auto& address : addresses)
        {
            const auto value = lua.get_integer();
            if (!roster && host == 0 && value == 0)
            {
                const auto generation = bridge_generation.load(std::memory_order_acquire);
                if (!scope_hooks_ready.load(std::memory_order_acquire) || (generation & 1) == 0) return false;
                death_scope = {};
                return true;
            }
            if (value <= 0) return false;
            address = static_cast<std::uintptr_t>(value);
        }
        return configure_scope(static_cast<std::uintptr_t>(host), addresses, allow_friendly_fire);
    }

    int publish_scope(const Lua& lua, bool roster, bool has_rule = false)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw())
        {
            accepted = read_scope(lua, roster, has_rule);
            if (!accepted) death_scope = {};
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? death_scope.token : 0);
        return 2;
    }

    int lua_set_scope(const Lua& lua) { return publish_scope(lua, false); }
    int lua_set_roster(const Lua& lua) { return publish_scope(lua, true); }
    int lua_set_roster_rule(const Lua& lua) { return publish_scope(lua, true, true); }

    int lua_clear_scope(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            death_scope = {};
        }
        return 0;
    }

    int lua_has_scoped_hit(const Lua& lua)
    {
        bool hit = false;
        if (Unreal::IsInGameThreadRaw() && ready() && lua.get_stack_size() == 1 && lua.is_integer(1))
        {
            const auto token = lua.get_integer();
            const auto generation = bridge_generation.load(std::memory_order_acquire);
            hit = token > 0 && std::ranges::any_of(death_hits, [token, generation](const DeathHit& pending) {
                return pending.token == token && pending.generation == generation;
            });
        }
        lua.set_bool(hit);
        return 1;
    }

    int lua_take_scoped_hit(const Lua& lua)
    {
        bool hit = false;
        const auto count = lua.get_stack_size();
        if (Unreal::IsInGameThreadRaw() && ready() && (count == 1 || count == 2) && lua.is_integer(1) &&
            (count == 1 || lua.is_integer(2)))
        {
            const auto address = lua.get_integer();
            const auto token = count == 2 ? lua.get_integer() : death_scope.token;
            const auto generation = bridge_generation.load(std::memory_order_acquire);
            for (auto& pending : death_hits)
            {
                if (address > 0 && token > 0 && pending.address == static_cast<std::uintptr_t>(address) &&
                    pending.token == token && pending.generation == generation)
                {
                    // Authorization was checked when Die was intercepted. The old
                    // token can be consumed after the scope or controller goes away.
                    hit = true;
                    pending = {};
                    break;
                }
            }
        }
        lua.set_bool(hit);
        return 1;
    }

    int lua_friendly_fire_count(const Lua& lua)
    {
        std::int64_t count = 0;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 &&
            friendly_fire_generation == bridge_generation.load(std::memory_order_acquire))
        {
            count = friendly_fire_count;
        }
        lua.set_integer(count);
        return 1;
    }

    [[nodiscard]] bool fall_scope_current()
    {
        return ready() && death_scope.generation == bridge_generation.load(std::memory_order_acquire) &&
               death_scope.local.address && death_scope.remote_count && Clock::now() < death_scope.expires;
    }

    int lua_read_fall_status(const Lua& lua)
    {
        const ScopedGuest* guest = nullptr;
        if (Unreal::IsInGameThreadRaw() && fall_scope_current())
        {
            const auto count = lua.get_stack_size();
            if (count == 0 && death_scope.remote_count == 1) guest = &death_scope.guests().front();
            if (count == 1 && lua.is_integer(1))
            {
                const auto address = lua.get_integer();
                if (address > 0) guest = find_guest(death_scope, static_cast<std::uintptr_t>(address));
            }
        }
        auto* movement = guest ? guest->fall.movement.Get() : nullptr;
        const bool bound = movement && guest->fall.landed && qualified_roster(death_scope) &&
                           movement == read_object(guest->fall.pawn_movement, guest->pawn.object.Get());
        const FallStatus status = guest ? guest->fall_status : FallStatus{};
        lua.set_bool(bound);
        lua.set_integer(guest ? death_scope.token : 0);
        lua.set_integer(guest ? static_cast<std::int64_t>(guest->pawn.address) : 0);
        lua.set_integer(status.mode_changes);
        lua.set_integer(status.starts);
        lua.set_integer(status.landings);
        lua.set_integer(status.forwarded);
        lua.set_integer(status.mode);
        lua.set_number(status.elapsed);
        lua.set_number(status.vertical_speed);
        lua.set_number(status.original_fall_time);
        lua.set_bool(status.actor_tick_enabled);
        lua.set_string(!guest ? "inactive_scope" : !bound ? "guest_fall_schema_or_scope_unavailable" : status.reason);
        return 13;
    }

    [[nodiscard]] double game_seconds(const FallBinding& binding, Unreal::UWorld* world)
    {
        GameplayTimeParams params{world, -1.0};
        binding.gameplay_statics->ProcessEvent(binding.time_seconds, &params);
        return params.result;
    }

    void start_fall(ScopedGuest& guest, Unreal::UWorld* world)
    {
        const auto& binding = guest.fall;
        const auto now = game_seconds(binding, world);
        const auto dilation = binding.time_dilation->GetPropertyValueInContainer(guest.pawn.object.Get());
        if (std::isfinite(now) && now >= 0 && std::isfinite(dilation) && dilation > 0)
        {
            guest.fall_episode = {now, dilation};
            ++guest.fall_status.starts;
        }
    }

    void sample_authoritative_falls()
    {
        if (!Unreal::IsInGameThreadRaw() || !fall_scope_current() || !death_scope.fall_landed) return;
        const auto now = Clock::now();
        if (now < death_scope.next_fall_sample) return;
        death_scope.next_fall_sample = now + fall_sample_interval;
        auto* world = static_cast<Unreal::UWorld*>(death_scope.world.Get());
        for (auto& guest : death_scope.guests())
        {
            const auto& binding = guest.fall;
            if (!binding.landed) continue;
            auto* pawn = guest.pawn.object.Get();
            auto* movement = binding.movement.Get();
            if (!live_object(movement) || read_object(binding.pawn_movement, pawn) != movement) continue;
            const auto mode = binding.movement_mode->GetPropertyValueInContainer(movement);
            guest.fall_status.mode = mode;
            if (mode != falling_mode)
            {
                guest.fall_episode = {};
                continue;
            }
            // A guest may already be airborne when the scope binds or renews.
            // Recover timing from the authority pawn without accepting client damage claims.
            if (guest.fall_episode.start < 0 && !binding.just_teleported->GetPropertyValueInContainer(movement) &&
                qualified_roster(death_scope))
                start_fall(guest, world);
        }
    }

    void observe_mode_change(ScopedGuest& guest, Unreal::UWorld* world, Unreal::UObject* movement, const void* parameters)
    {
        auto& status = guest.fall_status;
        const auto& binding = guest.fall;
        const auto previous = binding.previous_mode->GetPropertyValueInContainer(parameters);
        const auto mode = binding.new_mode->GetPropertyValueInContainer(parameters);
        ++status.mode_changes;
        status.mode = mode;
        if (mode != falling_mode || binding.movement_mode->GetPropertyValueInContainer(movement) != mode)
        {
            guest.fall_episode = {};
            return;
        }
        if (previous != falling_mode && guest.fall_episode.start < 0) start_fall(guest, world);
    }

    void observe_landing(ScopedGuest& guest, Unreal::UWorld* world, Unreal::UObject* movement, const void* parameters)
    {
        auto* pawn = guest.pawn.object.Get();
        auto& status = guest.fall_status;
        const auto& binding = guest.fall;
        const auto episode = guest.fall_episode;
        guest.fall_episode = {};

        ++status.landings;
        const auto original_fall_time = binding.fall_time->GetPropertyValueInContainer(pawn);
        const auto vertical_speed = binding.z->GetPropertyValueInContainer(binding.velocity->ContainerPtrToValuePtr<void>(movement));
        status.original_fall_time = std::isfinite(original_fall_time) ? original_fall_time : 0.0;
        status.vertical_speed = std::isfinite(vertical_speed) ? vertical_speed : 0.0;
        status.actor_tick_enabled = static_cast<Unreal::AActor*>(pawn)->IsActorTickEnabled();
        status.elapsed = 0.0;
        const auto dilation = binding.time_dilation->GetPropertyValueInContainer(pawn);
        if (episode.start >= 0 && dilation == episode.time_dilation)
        {
            const auto elapsed = (game_seconds(binding, world) - episode.start) * dilation;
            if (std::isfinite(elapsed) && elapsed > 0)
            {
                status.elapsed = elapsed;
            }
        }

        const auto* hit = binding.hit->ContainerPtrToValuePtr<void>(parameters);
        const auto health = binding.guest_health->GetPropertyValueInContainer(guest.ownership.save.Get());
        if (!std::isfinite(original_fall_time) || original_fall_time < 0 || original_fall_time > stock_fall_duration)
        {
            status.reason = "stock_fall_time_preserved";
        }
        else if (episode.start < 0 || dilation != episode.time_dilation)
        {
            status.reason = "untracked_fall";
        }
        else if (status.elapsed <= stock_fall_duration)
        {
            status.reason = "short_fall";
        }
        else if (!std::isfinite(health) || health <= 0 || binding.dead->GetPropertyValueInContainer(pawn))
        {
            status.reason = "guest_not_alive";
        }
        else if (binding.movement_mode->GetPropertyValueInContainer(movement) != falling_mode ||
                 binding.just_teleported->GetPropertyValueInContainer(movement) || status.vertical_speed >= 0 ||
                 !binding.blocking_hit->GetPropertyValueInContainer(hit) || binding.start_penetrating->GetPropertyValueInContainer(hit))
        {
            status.reason = "landing_not_verified";
        }
        else
        {
            // Stock Resolve_Jump resets this value after running its own
            // Calc_FallDamage path, including the native impact threshold.
            binding.fall_time->SetPropertyValueInContainer(pawn, status.elapsed);
            ++status.forwarded;
            status.reason = "forwarded";
        }
    }

    void observe_fall_event(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, const void* parameters)
    {
        if (!parameters || iteration.OriginalFunctionCallPrevented() || !fall_scope_current()) return;
        const auto guests = death_scope.guests();
        auto match = std::ranges::find_if(guests, [context](const ScopedGuest& guest) { return guest.pawn.object.Get() == context; });
        if (match == guests.end() || !qualified_roster(death_scope)) return;

        const auto& binding = match->fall;
        if (!binding.landed || (callee != binding.mode_changed && callee != binding.landed)) return;
        auto* movement = binding.movement.Get();
        if (!live_object(movement) || read_object(binding.pawn_movement, context) != movement)
        {
            match->fall_episode = {};
            return;
        }
        auto* world = static_cast<Unreal::UWorld*>(death_scope.world.Get());
        if (callee == binding.mode_changed)
        {
            observe_mode_change(*match, world, movement, parameters);
        }
        else
        {
            observe_landing(*match, world, movement, parameters);
        }
    }

    [[nodiscard]] DeathHit* hit_slot(std::uintptr_t address, std::uint64_t generation, std::int64_t token)
    {
        DeathHit* free = nullptr;
        for (auto& pending : death_hits)
        {
            if (pending.address == address && pending.generation == generation && pending.token == token)
            {
                return &pending;
            }
            if (!free && pending.generation != generation)
            {
                free = &pending;
            }
        }
        return free;
    }

    void intercept_death(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee)
    {
        const auto address = reinterpret_cast<std::uintptr_t>(context);
        const bool probe_match = probe.pawn_address == address && probe.die == callee;
        if (!context || !callee || (!probe_match && death_scope.die != callee) ||
            !ready() || iteration.OriginalFunctionCallPrevented())
        {
            return;
        }

        const auto generation = bridge_generation.load(std::memory_order_acquire);
        const auto now = Clock::now();
        if (probe_match && probe.generation == generation && now < probe.expires)
        {
            iteration.PreventOriginalFunctionCall();
            if (iteration.OriginalFunctionCallPrevented())
            {
                probe.intercepted = true;
                probe.pawn_address = 0;
                probe.die = nullptr;
            }
            return;
        }

        if (death_scope.die != callee || death_scope.generation != generation || now >= death_scope.expires)
        {
            return;
        }

        if (!includes_pawn(death_scope, context) || !qualified_roster(death_scope))
        {
            return;
        }
        auto* pending = hit_slot(address, generation, death_scope.token);
        if (!pending)
        {
            return;
        }
        iteration.PreventOriginalFunctionCall();
        if (iteration.OriginalFunctionCallPrevented())
        {
            *pending = {address, generation, death_scope.token};
        }
    }

    void warn_missing_damage_frame()
    {
        if (!warned_missing_damage_frame)
        {
            RC::Output::send<RC::LogLevel::Warning>(
                STR("[SilverfishCoopNative] Cannot verify a DealDamage parameter or output location. Damage follows stock behavior.\n"));
            warned_missing_damage_frame = true;
        }
    }

    struct DamageOutputs
    {
        void* total{};
        void* lethal{};
    };

    [[nodiscard]] DamageOutputs damage_outputs(const FriendlyFire& schema, void* parameters, Unreal::FFrame* frame)
    {
        if (!frame)
        {
            return {schema.total_damage->ContainerPtrToValuePtr<void>(parameters),
                    schema.lethal->ContainerPtrToValuePtr<void>(parameters)};
        }

        // Blueprint outputs have addresses in the caller's frame, rather than
        // the compiled callee's Locals. Only use an exact reflected property match.
        DamageOutputs outputs;
        auto* out = frame->OutParms();
        for (int remaining = schema.damage->GetNumParms(); out && remaining > 0; --remaining, out = out->NextOutParm)
        {
            if (out->Property == schema.total_damage)
            {
                outputs.total = out->PropAddr;
            }
            if (out->Property == schema.lethal)
            {
                outputs.lethal = out->PropAddr;
            }
        }
        return out ? DamageOutputs{} : outputs;
    }

    struct ClientDamageScope
    {
        ScopedPawn pawn;
        ControllerBinding owner;
        Unreal::FWeakObjectPtr world, instance, driver, connection;
        FriendlyFire damage;
        Unreal::UFunction* direct{};
        Unreal::UFunction* radiation{};
        Unreal::UFunction* die{};
        Unreal::UFunction* offhand{};
        Unreal::FBoolProperty* offhand_equip{};
        std::uint64_t generation{};
        std::string session, epoch;
        std::int64_t deal_blocked{}, direct_blocked{}, die_blocked{}, output_faults{};

        [[nodiscard]] bool accepts(Unreal::UFunction* callee) const
        {
            return callee && (callee == damage.damage || callee == direct || callee == radiation || callee == die);
        }
    };

    thread_local ClientDamageScope client_damage_scope;

    [[nodiscard]] Unreal::UObject* object_value(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        return read_object(object_field(object, name), object);
    }

    void filter_jump_camera_shake(Unreal::UnrealScriptFunctionCallableContext& call, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || (bridge_generation.load(std::memory_order_acquire) & 1) == 0)
            return;
        auto& frame = call.TheStack;
        auto* caller = frame.PreviousFrame();
        auto* source = caller ? caller->Object() : nullptr;
        if (!live_object(source) || !caller->Node() ||
            caller->Node()->GetFullName() != STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:Resolve_Jump") ||
            caller->Node()->GetOuterPrivate() != source->GetClassPrivate() || !frame.Locals()) return;
        auto* function = frame.Node();
        auto* shake = function ? Unreal::CastField<Unreal::FClassProperty>(
            function->GetPropertyByNameInChain(STR("Shake"))) : nullptr;
        if (!shake || !shake->HasAnyPropertyFlags(Unreal::CPF_Parm)) return;
        auto* argument = shake->ContainerPtrToValuePtr<void>(frame.Locals());
        auto* type = shake->GetObjectPropertyValue(argument);
        if (!live_object(type) || type->GetPathName() != STR("/Game/Classes/Player/CS_Jumping.CS_Jumping_C"))
            return;
        auto* world = static_cast<Unreal::AActor*>(source)->GetWorld();
        if (!live_object(world) || !live_object(object_value(world, STR("NetDriver")))) return;
        // Resolve_Jump asks controller zero to play this first-person camera effect.
        // The receiving controller must possess the pawn that originated it.
        const bool remote = object_value(call.Context, STR("Pawn")) != source;
        if (remote) shake->SetObjectPropertyValue(argument, nullptr);
        if (environment_equals("SILVERFISH_COOP_NATIVE_FIXTURES", "1"))
            RC::Output::send<RC::LogLevel::Verbose>(STR("[SilverfishCoopNative] Jump camera shake remote={}\n"), remote);
    }

    [[nodiscard]] bool live_client_actor(Unreal::UObject* object, Unreal::UObject* world,
                                        std::uint8_t expected_role = autonomous_role)
    {
        if (!live_object(object)) return false;
        auto* role = reflected_field<Unreal::FByteProperty>(object, STR("Role"));
        auto* ending = reflected_field<Unreal::FBoolProperty>(object, STR("bActorIsBeingDestroyed"));
        return role && role->GetPropertyValueInContainer(object) == expected_role &&
            ending && !ending->GetPropertyValueInContainer(object) &&
            static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    [[nodiscard]] bool qualified_client_damage(const ClientDamageScope& scope)
    {
        if (!client_damage_hooks_ready.load(std::memory_order_acquire) || !ready() ||
            scope.generation != bridge_generation.load(std::memory_order_acquire)) return false;
        auto* pawn = scope.pawn.object.Get();
        auto* controller = scope.owner.controller.Get();
        auto* player = scope.owner.player.Get();
        auto* world = scope.world.Get();
        auto* instance = scope.instance.Get();
        auto* driver = scope.driver.Get();
        auto* connection = scope.connection.Get();
        return live_object(world) && live_object(instance) && live_object(driver) && live_object(connection) &&
            live_client_actor(pawn, world) && live_client_actor(controller, world) &&
            same_controller(scope.owner, pawn) && object_value(player, STR("PlayerController")) == controller &&
            only_object(array_field(instance, STR("LocalPlayers")), instance) == player &&
            object_value(world, STR("OwningGameInstance")) == instance && object_value(world, STR("NetDriver")) == driver &&
            object_value(driver, STR("World")) == world && object_value(driver, STR("ServerConnection")) == connection &&
            object_value(connection, STR("Driver")) == driver;
    }

    [[nodiscard]] bool bind_client_damage(ClientDamageScope& scope, std::uintptr_t address)
    {
        auto* die = find_player_script(die_path, die_full_name);
        auto* direct = find_player_script(direct_damage_path, direct_damage_full_name);
        if (!die || die->GetNumParms() != 3 || !direct || direct->GetNumParms() != 4 ||
            die->GetOuterPrivate() != direct->GetOuterPrivate() ||
            !damage_argument<Unreal::FDoubleProperty>(direct, STR("Damage"), false) ||
            !damage_argument<Unreal::FBoolProperty>(direct, STR("ForceScopeCancel"), false)) return false;
        const auto damage = find_friendly_fire();
        if (!damage.damage || damage.damage->GetOuterPrivate() != die->GetOuterPrivate()) return false;
        scope.pawn = find_scoped_pawn(address, die);
        auto* pawn = scope.pawn.object.Get();
        if (!live_object(pawn) || !bind_controller(scope.owner, pawn, true)) return false;
        auto* world = static_cast<Unreal::AActor*>(pawn)->GetWorld();
        auto* controller = scope.owner.controller.Get();
        auto* instance = object_value(world, STR("OwningGameInstance"));
        auto* driver = object_value(world, STR("NetDriver"));
        auto* connection = object_value(driver, STR("ServerConnection"));
        auto* controller_type = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr,
            STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C"));
        auto* instance_type = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, instance_path);
        auto* connection_type = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr,
            STR("/Script/Engine.NetConnection"));
        if (!controller_type || !instance_type || !connection_type || controller->GetClassPrivate() != controller_type ||
            !live_object(instance) || instance->GetClassPrivate() != instance_type ||
            !live_object(connection) || !connection->IsA(connection_type)) return false;
        scope.world = world;
        scope.instance = instance;
        scope.driver = driver;
        scope.connection = connection;
        scope.damage = damage;
        scope.direct = direct;
        auto* radiation = find_player_script(radiation_damage_path, radiation_damage_full_name);
        if (radiation && radiation->GetOuterPrivate() == die->GetOuterPrivate() && radiation->GetNumParms() == 0 &&
            !radiation->GetReturnProperty()) scope.radiation = radiation;
        scope.die = die;
        auto* offhand = find_player_script(offhand_path, offhand_full_name);
        if (offhand && offhand->GetOuterPrivate() == die->GetOuterPrivate() && offhand->GetNumParms() == 1)
        {
            auto* equip = damage_argument<Unreal::FBoolProperty>(offhand, STR("Equip"), false);
            if (equip && !equip->HasAnyPropertyFlags(Unreal::CPF_ReferenceParm))
            {
                scope.offhand = offhand;
                scope.offhand_equip = equip;
            }
        }
        scope.generation = bridge_generation.load(std::memory_order_acquire);
        return qualified_client_damage(scope);
    }

    struct ClientDamageRequest
    {
        std::uintptr_t address{};
        std::string session, epoch;
    };

    [[nodiscard]] bool client_damage_token(std::string_view value)
    {
        return value.size() == 32 && value.find_first_not_of('0') != std::string_view::npos &&
            std::ranges::all_of(value, [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
    }

    [[nodiscard]] ClientDamageRequest client_damage_request(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || !client_damage_hooks_ready.load(std::memory_order_acquire) || !ready() ||
            lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_string(2) || !lua.is_string(3)) return {};
        auto address = lua.get_integer();
        std::string session{lua.get_string()};
        std::string epoch{lua.get_string()};
        if (address <= 0 || !client_damage_token(session) || !client_damage_token(epoch)) return {};
        return {static_cast<std::uintptr_t>(address), std::move(session), std::move(epoch)};
    }

    [[nodiscard]] bool current_client_damage(const ClientDamageRequest& request)
    {
        const auto& scope = client_damage_scope;
        return request.address && scope.pawn.address == request.address && scope.session == request.session &&
            scope.epoch == request.epoch && qualified_client_damage(scope);
    }

    int lua_set_client_damage(const Lua& lua)
    {
        auto request = client_damage_request(lua);
        bool active = current_client_damage(request);
        if (!active && request.address)
        {
            ClientDamageScope next;
            next.session = std::move(request.session);
            next.epoch = std::move(request.epoch);
            if ((active = bind_client_damage(next, request.address))) client_damage_scope = std::move(next);
        }
        lua.set_bool(active);
        lua.set_string(active ? "stock_client_damage_guarded" : "stock_client_damage_scope_unavailable");
        return 2;
    }

    int lua_read_client_damage(const Lua& lua)
    {
        const bool active = current_client_damage(client_damage_request(lua));
        const auto& scope = client_damage_scope;
        lua.set_bool(active);
        lua.set_integer(active ? scope.deal_blocked : 0);
        lua.set_integer(active ? scope.direct_blocked : 0);
        lua.set_integer(active ? scope.die_blocked : 0);
        lua.set_integer(active ? scope.output_faults : 0);
        return 5;
    }

    int lua_clear_client_damage(const Lua& lua)
    {
        const bool cleared = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0;
        if (cleared) client_damage_scope = {};
        lua.set_bool(cleared);
        return 1;
    }

    [[nodiscard]] bool active_guest_save_slot(Unreal::UObject* instance)
    {
        auto* slot = reflected_field<Unreal::FStrProperty>(instance, STR("CurrentSaveSlot"));
        constexpr RC::StringViewType prefix{STR("SFCoopGuest_")};
        constexpr auto size = prefix.size() + 32;
        if (!slot) return false;
        const auto& value = slot->GetPropertyValueInContainer(instance);
        if (value.Len() != static_cast<int>(size)) return false;
        const RC::StringViewType name{*value, size};
        const auto id = name.substr(prefix.size());
        return name.starts_with(prefix) && id.find_first_not_of('0') != RC::StringViewType::npos &&
            std::ranges::all_of(id, [](Unreal::TCHAR letter) {
                return (letter >= '0' && letter <= '9') || (letter >= 'a' && letter <= 'f');
            });
    }

    void intercept_replica_offhand(Iteration& iteration, Unreal::UObject* replica,
                                   void* parameters, Unreal::FFrame* frame)
    {
        const auto& scope = client_damage_scope;
        if (iteration.OriginalFunctionCallPrevented() || (frame && frame->Object() != replica)) return;
        const auto* arguments = frame ? frame->Locals() : parameters;
        if (!arguments || scope.offhand_equip->GetPropertyValueInContainer(arguments) ||
            !live_object(replica) || replica->GetClassPrivate() != scope.offhand->GetOuterPrivate() ||
            !live_client_actor(replica, scope.world.Get(), simulated_role) || !qualified_client_damage(scope)) return;
        auto* instance = scope.instance.Get();
        auto* world = static_cast<Unreal::UWorld*>(scope.world.Get());
        SaveOwnership ownership;
        if (!bind_save(ownership, scope.pawn.object.Get(), world) ||
            !active_guest_save_slot(instance) || ownership.instance.Get() != instance) return;
        auto* save = ownership.save.Get();
        auto* offhanding = reflected_field<Unreal::FBoolProperty>(save, STR("PlayerOffhandingFlashlight"));
        if (!save->GetOuterPrivate() || save->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient") ||
            read_object(ownership.instance_slot, instance) != save ||
            read_object(ownership.pawn_save, replica) != save || !offhanding ||
            !offhanding->GetPropertyValueInContainer(save)) return;

        // Stock passive updates notify every Player_FP, including simulated replicas.
        // A replica with no held item must not clear the autonomous player's shared save flag.
        iteration.PreventOriginalFunctionCall();
    }

    // Host damage and downing still run on the enrolled authority pawn. Client
    // Blueprints can call these same wrappers locally before replication arrives.
    bool intercept_client_damage(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee,
                                 void* parameters, Unreal::FFrame* frame)
    {
        auto& scope = client_damage_scope;
        if (!scope.accepts(callee) || iteration.OriginalFunctionCallPrevented() ||
            !qualified_client_damage(scope)) return false;
        // The guest also has simulated copies of the other players. A locally
        // evaluated anomaly cannot damage or kill those pawns on this peer.
        if (scope.pawn.object.Get() != context &&
            (!live_client_actor(context, scope.world.Get(), simulated_role) ||
             context->GetClassPrivate() != scope.die->GetOuterPrivate())) return false;
        if (frame && frame->Object() != context) return false;

        DamageOutputs outputs;
        if (callee == scope.damage.damage && (parameters || frame))
            outputs = damage_outputs(scope.damage, parameters, frame);
        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return false;
        if (callee == scope.damage.damage)
        {
            ++scope.deal_blocked;
            if (outputs.total && outputs.lethal && outputs.total != outputs.lethal &&
                reinterpret_cast<std::uintptr_t>(outputs.total) % alignof(double) == 0)
            {
                scope.damage.total_damage->SetPropertyValue(outputs.total, 0.0);
                scope.damage.lethal->SetPropertyValue(outputs.lethal, false);
            }
            else
            {
                // Leave unknown addresses alone and still fence the duplicate local mutation.
                ++scope.output_faults;
            }
        }
        else if (callee == scope.direct) ++scope.direct_blocked;
        else if (callee == scope.die) ++scope.die_blocked;
        return true;
    }

    void intercept_friendly_fire(Iteration& iteration, Unreal::UObject* context, void* parameters, Unreal::FFrame* frame)
    {
        const auto generation = bridge_generation.load(std::memory_order_acquire);
        if (death_scope.allow_friendly_fire || !context || !death_scope.local.address ||
            death_scope.generation != generation || !ready() || Clock::now() >= death_scope.expires ||
            iteration.OriginalFunctionCallPrevented())
        {
            return;
        }
        if (!includes_pawn(death_scope, context))
        {
            return;
        }
        if (frame)
        {
            parameters = frame->Object() == context ? frame->Locals() : nullptr;
        }
        if (!parameters)
        {
            warn_missing_damage_frame();
            return;
        }

        const auto& schema = death_scope.friendly_fire;
        auto* attacker = schema.instigator->GetObjectPropertyValue(schema.instigator->ContainerPtrToValuePtr<void>(parameters));
        if (attacker == context || !includes_pawn(death_scope, attacker) || !qualified_roster(death_scope))
        {
            return;
        }

        const auto outputs = damage_outputs(schema, parameters, frame);
        if (!outputs.total || !outputs.lethal || outputs.total == outputs.lethal ||
            reinterpret_cast<std::uintptr_t>(outputs.total) % alignof(double) != 0)
        {
            warn_missing_damage_frame();
            return;
        }
        iteration.PreventOriginalFunctionCall();
        if (iteration.OriginalFunctionCallPrevented())
        {
            schema.total_damage->SetPropertyValue(outputs.total, 0.0);
            schema.lethal->SetPropertyValue(outputs.lethal, false);
            if (friendly_fire_generation != generation)
            {
                friendly_fire_generation = generation;
                friendly_fire_count = 0;
            }
            ++friendly_fire_count;
        }
    }

    void intercept_call(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee,
                        void* parameters, Unreal::FFrame* frame = nullptr)
    {
        if (callee && callee == client_damage_scope.offhand)
        {
            intercept_replica_offhand(iteration, context, parameters, frame);
            return;
        }
        if (intercept_client_damage(iteration, context, callee, parameters, frame)) return;
        if (callee && callee == death_scope.friendly_fire.damage)
        {
            intercept_friendly_fire(iteration, context, parameters, frame);
        }
        else
        {
            intercept_death(iteration, context, callee);
        }
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw())
        {
            return;
        }
        auto* callee = frame.Node();
        if (context && callee && (callee == probe.die || callee == death_scope.die || callee == client_damage_scope.offhand ||
                                  callee == death_scope.friendly_fire.damage || client_damage_scope.accepts(callee)))
        {
            // ProcessLocalScriptFunction receives the prepared callee frame on
            // UE 5.4. The caller has already consumed the argument expressions.
            intercept_call(iteration, context, callee, nullptr, &frame);
        }
    }

    class SilverfishNativeMod final : public RC::CppUserModBase
    {
    public:
        SilverfishNativeMod()
        {
            ModName = mod_name;
            ModVersion = STR("0.1.0");
            ModDescription = STR("Scoped UE4SS pre-call canary for the staged Silverfish co-op lab.");
            ModAuthors = STR("Silverfish Co-op");
        }

        ~SilverfishNativeMod() override
        {
            silverfish::local_bridge::uninstall_native_dispatch();
            if (jump_shake) Unreal::UObjectGlobals::UnregisterHook(jump_shake, jump_shake_hook);
            silverfish::local_bridge::stop();
            silverfish::native_guest_item::shutdown();
            silverfish::native_zone::uninstall_hooks();
            silverfish::native_transport::uninstall_hooks();
            silverfish::native_stock_rest::uninstall_hooks();
            silverfish::native_session_exit_input::uninstall_hooks();
            silverfish::native_listener_bind::uninstall_hooks();
            silverfish::native_datasilo_door::uninstall_hooks();
            silverfish::native_datasilo_lift::uninstall_hooks();
            silverfish::native_mindcore_carry::uninstall_hooks();
            silverfish::native_guest_npc::uninstall_hooks();
            silverfish::native_guest_reload_input::uninstall_hooks();
            silverfish::native_inventory_view::uninstall_hooks();
            silverfish::inventory_wipe::uninstall_screen_hooks();
            silverfish::native_guest_stack_drop::uninstall_hooks();
            silverfish::native_guest_drop_projection::uninstall_hooks();
            silverfish::native_bag_projection::uninstall_hooks();
            silverfish::native_interaction_input::uninstall_hooks();
            silverfish::native_authored_pickup::uninstall_hooks();
            silverfish::native_broker_menu::uninstall_hooks();
            silverfish::native_broker_input::uninstall_hooks();
            silverfish::campaign_npc::uninstall_hooks();
            silverfish::native_flood_jammer::uninstall_hooks();
            silverfish::native_lenny_quest::uninstall_hooks();
            silverfish::native_guest_clock::uninstall_hooks();
            silverfish::native_guest_primary_input::uninstall_hooks();
            silverfish::native_host_dig::uninstall_hooks();
            silverfish::native_guest_sickness::uninstall_hooks();
            silverfish::native_guest_melee::uninstall_hooks();
            silverfish::native_guest_consumable::uninstall_hooks();
            silverfish::native_loot_crate_input::uninstall_hooks();
            silverfish::native_enemy_loot::uninstall_hooks();
            silverfish::native_guest_movement::uninstall_hooks();
            silverfish::stock_enemy_drops::uninstall_hooks();
            silverfish::native_shop_view::uninstall_hooks();
            silverfish::native_nothing_return::uninstall_hooks();
            silverfish::native_kill_volume::uninstall_hooks();
            silverfish::native_anomaly_fields::uninstall_hooks();
            silverfish::shared_loot::uninstall_hooks();
            silverfish::native_black_stone::uninstall_hooks();
            invalidate_bridge();
            hooks_ready.store(false, std::memory_order_release);
            scope_hooks_ready.store(false, std::memory_order_release);
            client_damage_hooks_ready.store(false, std::memory_order_release);
            for (const auto id : hooks)
            {
                if (id != Hook::ERROR_ID)
                {
                    Hook::UnregisterCallback(id);
                }
            }
        }

        auto on_unreal_init() -> void override
        {
            // The resident bootstrap publishes its settings while UE4SS loads
            // the native mods. Read them after all startup constructors ran.
            enabled = environment_equals("SILVERFISH_COOP_NATIVE_DEATH", "cpp");
            scoped_events = enabled && environment_equals("SILVERFISH_COOP_NATIVE_EVENTS", "1");
            client_damage = enabled && environment_equals("SILVERFISH_COOP_NATIVE_ZONE", "1");
            silverfish::native_zone::install_hooks();
            silverfish::native_transport::install_hooks();
            silverfish::native_stock_rest::install_hooks();
            silverfish::native_session_exit_input::install_hooks();
            silverfish::native_listener_bind::install_hooks();
            silverfish::native_mindcore_carry::install_hooks();
            silverfish::native_datasilo_lift::install_hooks();
            silverfish::native_datasilo_door::install_hooks();
            silverfish::native_guest_npc::install_hooks();
            silverfish::native_guest_reload_input::install_hooks();
            silverfish::native_inventory_view::install_hooks();
            silverfish::inventory_wipe::install_screen_hooks();
            silverfish::native_bag_projection::install_hooks();
            silverfish::native_interaction_input::install_hooks();
            silverfish::native_guest_drop_projection::install_hooks();
            silverfish::native_authored_pickup::install_hooks();
            silverfish::native_broker_menu::install_hooks();
            silverfish::native_broker_input::install_hooks();
            silverfish::campaign_npc::install_hooks();
            silverfish::native_flood_jammer::install_hooks();
            silverfish::native_lenny_quest::install_hooks();
            silverfish::native_guest_clock::install_hooks();
            silverfish::local_bridge::install_native_dispatch();
            silverfish::native_guest_primary_input::install_hooks();
            silverfish::native_host_dig::install_hooks();
            silverfish::native_guest_sickness::install_hooks();
            silverfish::native_guest_consumable::install_hooks();
            silverfish::native_loot_crate_input::install_hooks();
            silverfish::native_enemy_loot::install_hooks();
            silverfish::native_guest_movement::install_hooks();
            silverfish::stock_enemy_drops::install_hooks();
            silverfish::native_shop_view::install_hooks();
            silverfish::native_nothing_return::install_hooks();
            silverfish::native_kill_volume::install_hooks();
            silverfish::native_anomaly_fields::install_hooks();
            silverfish::shared_loot::install_hooks();
            silverfish::native_black_stone::install_hooks();
            if (!enabled)
            {
                return;
            }
            if (scoped_events || client_damage)
            {
                jump_shake = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(
                    nullptr, nullptr, STR("/Script/Engine.PlayerController:ClientStartCameraShake"));
                if (jump_shake)
                    jump_shake_hook = Unreal::UObjectGlobals::RegisterHook(
                        jump_shake, filter_jump_camera_shake, {}, nullptr);
            }

            hooks[0] = Hook::RegisterProcessEventPreCallback(
                [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void* parameters) {
                    if (Unreal::IsInGameThreadRaw())
                    {
                        if (callee && (callee == death_scope.fall_mode || callee == death_scope.fall_landed))
                        {
                            observe_fall_event(iteration, context, callee, parameters);
                        }
                        intercept_call(iteration, context, callee, parameters);
                    }
                },
                {false, false, mod_name, STR("Die reflected call")});
            hooks[1] = Hook::RegisterProcessInternalPreCallback(
                intercept_script, {false, false, mod_name, STR("Die Blueprint dispatch")});
            hooks[2] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
                intercept_script, {false, false, mod_name, STR("Die Blueprint call")});

            // Direct Blueprint calls require the local-script entry on UE 5.4.
            const bool installed = hooks[2] != Hook::ERROR_ID;
            hooks_ready.store(installed, std::memory_order_release);
            RC::Output::send<RC::LogLevel::Verbose>(
                STR("[SilverfishCoopNative] Death lab hooks: event={}, internal={}, local-script={}\n"),
                hooks[0], hooks[1], hooks[2]);
            if ((scoped_events || client_damage) && installed)
            {
                hooks[3] = Hook::RegisterLoadMapPreCallback(
                    [](Hook::TCallbackIterationData<bool>&, Unreal::UEngine*, Unreal::FWorldContext&,
                       Unreal::FURL, Unreal::UPendingNetGame*, Unreal::FString&) {
                        if (Unreal::IsInGameThreadRaw())
                        {
                            death_scope = {};
                            client_damage_scope = {};
                        }
                    },
                    {false, true, mod_name, STR("Clear death scope before travel")});
                scope_hooks_ready.store(scoped_events && hooks[3] != Hook::ERROR_ID, std::memory_order_release);
                client_damage_hooks_ready.store(client_damage && hooks[0] != Hook::ERROR_ID && hooks[3] != Hook::ERROR_ID,
                    std::memory_order_release);
                if (scoped_events)
                    hooks[4] = Hook::RegisterEngineTickPreCallback(
                        [](Iteration&, Unreal::UEngine*, float, bool) { sample_authoritative_falls(); },
                        {false, false, mod_name, STR("Track authoritative guest falls before stock landing")});
            }
        }

        auto on_lua_start(RC::StringViewType name, Lua& lua, Lua&, Lua&, Lua*) -> void override
        {
            if (name != lua_mod_name)
            {
                return;
            }
            silverfish::native_zone::register_lua(lua);
            silverfish::native_transport::register_lua(lua);
            silverfish::native_guest_item::register_lua(lua);
            silverfish::native_guest_armor::register_lua(lua);
            silverfish::native_listener_bind::register_lua(lua);
            silverfish::native_guest_melee::register_lua(lua);
            silverfish::native_host_landmine::register_lua(lua);
            silverfish::native_host_dig::register_lua(lua);
            silverfish::host_invitation_clipboard::register_lua(lua);
            silverfish::native_stock_rest::register_lua(lua);
            silverfish::native_session_exit_input::register_lua(lua);
            silverfish::shared_loot::register_lua(lua);
            silverfish::loot_escrow::register_lua(lua);
            silverfish::inventory_census::register_lua(lua);
            silverfish::inventory_wipe::register_lua(lua);
            silverfish::native_guest_stack_drop::register_lua(lua);
            silverfish::native_guest_drop_projection::register_lua(lua);
            silverfish::finite_shop::register_lua(lua);
            silverfish::native_guest_npc::register_lua(lua);
            silverfish::native_guest_reload_input::register_lua(lua);
            silverfish::native_inventory_view::register_lua(lua);
            silverfish::native_bag_projection::register_lua(lua);
            silverfish::native_interaction_input::register_lua(lua);
            silverfish::local_bridge::register_lua(lua);
            silverfish::native_authored_pickup::register_lua(lua);
            silverfish::native_mindcore::register_lua(lua);
            silverfish::native_mindcore_carry::register_lua(lua);
            silverfish::native_datasilo_lift::register_lua(lua);
            silverfish::native_datasilo_door::register_lua(lua);
            silverfish::native_broker_menu::register_lua(lua);
            silverfish::native_broker_input::register_lua(lua);
            silverfish::campaign_npc::register_lua(lua);
            silverfish::native_flood_jammer::register_lua(lua);
            silverfish::native_lenny_quest::register_lua(lua);
            silverfish::native_guest_clock::register_lua(lua);
            silverfish::native_guest_health::register_lua(lua);
            silverfish::native_guest_primary_input::register_lua(lua);
            silverfish::native_guest_consumable::register_lua(lua);
            silverfish::native_guest_regen::register_lua(lua);
            silverfish::native_guest_sickness::register_lua(lua);
            silverfish::native_loot_crate_input::register_lua(lua);
            silverfish::native_enemy_loot::register_lua(lua);
            silverfish::stock_enemy_drops::register_lua(lua);
            silverfish::native_fresh_clock::register_lua(lua);
            silverfish::native_quest_rewards::register_lua(lua);
            silverfish::borough_barter::register_lua(lua);
            silverfish::native_shop_view::register_lua(lua);
            silverfish::native_nothing_return::register_lua(lua);
            silverfish::native_kill_volume::register_lua(lua);
            silverfish::native_anomaly_fields::register_lua(lua);
            silverfish::native_black_stone::register_lua(lua);
            if (!enabled)
            {
                return;
            }

            invalidate_bridge();
            lua.register_function("SilverfishNativeDeathReady", lua_ready);
            lua.register_function("SilverfishArmNativeDeath", lua_arm);
            lua.register_function("SilverfishDisarmNativeDeath", lua_disarm);
            lua.register_function("SilverfishTakeNativeDeathHit", lua_take_hit);
            if (client_damage)
            {
                lua.register_function("SilverfishSetNativeClientDamageScope", lua_set_client_damage);
                lua.register_function("SilverfishReadNativeClientDamageScope", lua_read_client_damage);
                lua.register_function("SilverfishClearNativeClientDamageScope", lua_clear_client_damage);
            }
            if (scoped_events)
            {
                lua.register_function("SilverfishSetNativeDeathScope", lua_set_scope);
                lua.register_function("SilverfishSetNativeDeathRoster", lua_set_roster);
                lua.register_function("SilverfishSetNativeDeathRosterRule", lua_set_roster_rule);
                lua.register_function("SilverfishClearNativeDeathScope", lua_clear_scope);
                lua.register_function("SilverfishHasScopedNativeDeathHit", lua_has_scoped_hit);
                lua.register_function("SilverfishTakeScopedNativeDeathHit", lua_take_scoped_hit);
                lua.register_function("SilverfishFriendlyFireBlockedCount", lua_friendly_fire_count);
                lua.register_function("SilverfishReadNativeFallStatus", lua_read_fall_status);
            }
            silverfish::register_native_pickup_lua(lua);
            silverfish::register_native_item_roundtrip_lua(lua);
            silverfish::register_native_save_copy_lua(lua);
            bridge_generation.fetch_add(1, std::memory_order_release);
        }

        auto on_lua_stop(RC::StringViewType name, Lua&, Lua&, Lua&, Lua*) -> void override
        {
            if (name == lua_mod_name)
            {
                silverfish::native_zone::clear_scope();
                silverfish::native_transport::clear_scope();
                silverfish::native_stock_rest::clear_scope();
                silverfish::native_guest_npc::clear_scope();
                silverfish::native_inventory_view::clear_scope();
                silverfish::native_bag_projection::clear_scope();
                silverfish::native_guest_drop_projection::clear_scope();
                silverfish::native_interaction_input::clear_scope();
                silverfish::local_bridge::stop();
                silverfish::native_authored_pickup::clear_scope();
                silverfish::native_broker_menu::clear_scope();
                silverfish::native_broker_input::clear_scope();
                silverfish::campaign_npc::clear_scope();
                silverfish::native_guest_clock::clear_scope();
                silverfish::native_guest_primary_input::clear_scope();
                silverfish::native_host_dig::clear_scope();
                silverfish::native_guest_sickness::clear_scope();
                silverfish::native_loot_crate_input::clear_scope();
                silverfish::native_enemy_loot::clear_scope();
                silverfish::native_shop_view::clear_scope();
                silverfish::native_nothing_return::clear_scope();
                silverfish::native_black_stone::clear_scope();
                invalidate_bridge();
            }
        }

    private:
        static void invalidate_bridge()
        {
            if ((bridge_generation.load(std::memory_order_relaxed) & 1) != 0)
            {
                bridge_generation.fetch_add(1, std::memory_order_release);
            }
        }

        bool enabled{};
        bool scoped_events{};
        bool client_damage{};
        std::array<Hook::GlobalCallbackId, 5> hooks{};
        Unreal::UFunction* jump_shake{};
        std::pair<int, int> jump_shake_hook{};
    };
}

std::optional<silverfish::native_guest_health::ClientSeat> silverfish::native_guest_health::client_damage_seat(
    std::string_view session, std::string_view epoch)
{
    if (!Unreal::IsInGameThreadRaw() || client_damage_scope.session != session ||
        client_damage_scope.epoch != epoch || !qualified_client_damage(client_damage_scope)) return {};
    return ClientSeat{client_damage_scope.pawn.object.Get(), client_damage_scope.owner.controller.Get(),
        client_damage_scope.world.Get(), client_damage_scope.instance.Get()};
}

bool silverfish::native_death::friendly_fire_pair(RC::Unreal::UObject* attacker, RC::Unreal::UObject* target)
{
    return Unreal::IsInGameThreadRaw() && ready() && attacker != target && death_scope.allow_friendly_fire &&
        death_scope.generation == bridge_generation.load(std::memory_order_acquire) &&
        Clock::now() < death_scope.expires && includes_pawn(death_scope, attacker) &&
        includes_pawn(death_scope, target) && qualified_roster(death_scope);
}

extern "C" __declspec(dllexport) RC::CppUserModBase* start_mod()
{
    return new SilverfishNativeMod();
}

extern "C" __declspec(dllexport) void uninstall_mod(RC::CppUserModBase* mod)
{
    delete mod;
}
