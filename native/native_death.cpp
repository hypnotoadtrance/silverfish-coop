#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <DynamicOutput/DynamicOutput.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FURL.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

#include "item_roundtrip.hpp"
#include "pickup.hpp"
#include "transport_items.hpp"
#include "save_copy.hpp"
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
    constexpr auto mode_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:K2_OnMovementModeChanged");
    constexpr auto mode_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:K2_OnMovementModeChanged");
    constexpr auto landed_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C:OnLanded");
    constexpr auto landed_full_name = STR("Function /Game/Classes/Player/Player_FP.Player_FP_C:OnLanded");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto probe_timeout = std::chrono::seconds(2);
    constexpr auto scope_timeout = std::chrono::milliseconds(1250);
    constexpr std::uint8_t authority_role = 3; // ENetRole::ROLE_Authority in UE 5.4.
    constexpr std::uint8_t falling_mode = 3; // EMovementMode::MOVE_Falling in UE 5.4.
    constexpr double stock_fall_duration = 0.5;

    // An even generation has no Lua bridge. Refreshing the generation invalidates
    // game-thread authorization without touching Lua from another thread.
    std::atomic<std::uint64_t> bridge_generation{};
    std::atomic<bool> hooks_ready{};
    std::atomic<bool> scope_hooks_ready{};

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

    struct DeathScope
    {
        ScopedPawn local;
        ScopedPawn guest;
        SaveOwnership ownership;
        ControllerBinding local_controller;
        ControllerBinding guest_controller;
        ListenerBinding listener;
        FriendlyFire friendly_fire;
        FallBinding fall;
        FallEpisode fall_episode;
        FallStatus fall_status;
        Unreal::FWeakObjectPtr world;
        Unreal::UFunction* die{};
        std::uint64_t generation{};
        std::int64_t token{};
        Clock::time_point expires{};
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
    thread_local std::array<DeathHit, 8> death_hits;
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
        auto* property = Unreal::CastField<Unreal::FObjectProperty>(object->GetPropertyByNameInChain(name));
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

    [[nodiscard]] bool qualified_guest(const DeathScope& scope)
    {
        auto* pawn = scope.guest.object.Get();
        auto* world = scope.world.Get();
        return pawn && world && world == authority_world(scope.guest) &&
               same_detached_save(scope.ownership, pawn, world) && same_controller(scope.guest_controller, pawn);
    }

    [[nodiscard]] Unreal::FArrayProperty* array_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = Unreal::CastField<Unreal::FArrayProperty>(object->GetPropertyByNameInChain(name));
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
        listener.local_players = array_field(instance, STR("LocalPlayers"));
        listener.driver = driver;
        listener.stock_save = stock;
        return listener.server_connection && listener.client_connections && listener.local_players;
    }

    [[nodiscard]] bool qualified_local(const DeathScope& scope)
    {
        auto* pawn = scope.local.object.Get();
        auto* world = scope.world.Get();
        auto* instance = scope.ownership.instance.Get();
        auto* driver = scope.listener.driver.Get();
        auto* stock = scope.listener.stock_save.Get();
        auto* local_player = scope.local_controller.player.Get();
        auto* guest_connection = scope.guest_controller.player.Get();
        return pawn && world && instance && live_object(driver) && live_object(stock) &&
               local_player && guest_connection && world == authority_world(scope.local) &&
               same_controller(scope.local_controller, pawn) && qualified_guest(scope) &&
               read_object(scope.ownership.pawn_save, pawn) == stock &&
               read_object(scope.ownership.instance_slot, instance) == stock &&
               read_object(scope.listener.world_driver, world) == driver &&
               !read_object(scope.listener.server_connection, driver) &&
               only_object(scope.listener.local_players, instance) == local_player &&
               only_object(scope.listener.client_connections, driver) == guest_connection;
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

    [[nodiscard]] bool set_scope(const Lua& lua)
    {
        const auto generation = bridge_generation.load(std::memory_order_acquire);
        if (!scope_hooks_ready.load(std::memory_order_acquire) || (generation & 1) == 0 ||
            lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
        {
            return false;
        }

        // LuaMadeSimple pops the value at index 1 on each read.
        const auto local = lua.get_integer();
        const auto guest = lua.get_integer();
        if (local < 0 || guest < 0 || (local != 0 && (guest == 0 || local == guest)))
        {
            return false;
        }
        if (guest == 0)
        {
            death_scope = {};
            return true;
        }

        const auto local_address = static_cast<std::uintptr_t>(local);
        const auto guest_address = static_cast<std::uintptr_t>(guest);
        if (death_scope.generation == generation && death_scope.guest.address == guest_address &&
            death_scope.local.address == local_address)
        {
            if (!(local_address ? qualified_local(death_scope) : qualified_guest(death_scope)))
            {
                return false;
            }
            const auto now = Clock::now();
            if (now >= death_scope.expires)
            {
                death_scope.fall_episode = {};
            }
            death_scope.expires = now + scope_timeout;
            return true;
        }

        DeathScope next;
        next.die = find_player_script(die_path, die_full_name);
        if (!next.die)
        {
            return false;
        }
        next.guest = find_scoped_pawn(guest_address, next.die);
        auto* pawn = next.guest.object.Get();
        auto* world = authority_world(next.guest);
        if (!world || !bind_save(next.ownership, pawn, world) || !bind_controller(next.guest_controller, pawn, false))
        {
            return false;
        }
        next.world = world;
        if (local_address)
        {
            next.local = find_scoped_pawn(local_address, next.die);
            auto* host = next.local.object.Get();
            if (!host || world != authority_world(next.local) ||
                !bind_controller(next.local_controller, host, true) || !bind_listener(next, world))
            {
                return false;
            }
        }
        if (!(local_address ? qualified_local(next) : qualified_guest(next)))
        {
            return false;
        }
        if (local_address)
        {
            next.friendly_fire = find_friendly_fire();
            next.fall = bind_fall(pawn, next.ownership.save.Get());
        }
        next.generation = generation;
        next.token = ++next_scope_token;
        next.expires = Clock::now() + scope_timeout;
        death_scope = next;
        return true;
    }

    int lua_set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw())
        {
            accepted = set_scope(lua);
            if (!accepted)
            {
                death_scope = {};
            }
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? death_scope.token : 0);
        return 2;
    }

    int lua_clear_scope(const Lua&)
    {
        if (Unreal::IsInGameThreadRaw())
        {
            death_scope = {};
        }
        return 0;
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
               death_scope.local.address && death_scope.guest.address && Clock::now() < death_scope.expires;
    }

    int lua_read_fall_status(const Lua& lua)
    {
        const bool current = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && fall_scope_current();
        const bool bound = current && death_scope.fall.landed && qualified_local(death_scope) &&
                           death_scope.fall.movement.Get() == read_object(death_scope.fall.pawn_movement, death_scope.guest.object.Get());
        const FallStatus status = current ? death_scope.fall_status : FallStatus{};
        lua.set_bool(bound);
        lua.set_integer(current ? death_scope.token : 0);
        lua.set_integer(current ? static_cast<std::int64_t>(death_scope.guest.address) : 0);
        lua.set_integer(status.mode_changes);
        lua.set_integer(status.starts);
        lua.set_integer(status.landings);
        lua.set_integer(status.forwarded);
        lua.set_integer(status.mode);
        lua.set_number(status.elapsed);
        lua.set_number(status.vertical_speed);
        lua.set_number(status.original_fall_time);
        lua.set_bool(status.actor_tick_enabled);
        lua.set_string(!current ? "inactive_scope" : !bound ? "guest_fall_schema_or_scope_unavailable" : status.reason);
        return 13;
    }

    [[nodiscard]] double game_seconds(const FallBinding& binding, Unreal::UWorld* world)
    {
        GameplayTimeParams params{world, -1.0};
        binding.gameplay_statics->ProcessEvent(binding.time_seconds, &params);
        return params.result;
    }

    void observe_mode_change(DeathScope& scope, Unreal::UObject* pawn, Unreal::UObject* movement, const void* parameters)
    {
        auto& status = scope.fall_status;
        const auto& binding = scope.fall;
        const auto previous = binding.previous_mode->GetPropertyValueInContainer(parameters);
        const auto mode = binding.new_mode->GetPropertyValueInContainer(parameters);
        ++status.mode_changes;
        status.mode = mode;
        if (mode != falling_mode || binding.movement_mode->GetPropertyValueInContainer(movement) != mode)
        {
            scope.fall_episode = {};
            return;
        }
        if (previous == falling_mode)
        {
            return;
        }
        scope.fall_episode = {};
        const auto now = game_seconds(binding, static_cast<Unreal::UWorld*>(scope.world.Get()));
        const auto dilation = binding.time_dilation->GetPropertyValueInContainer(pawn);
        if (std::isfinite(now) && now >= 0 && std::isfinite(dilation) && dilation > 0)
        {
            scope.fall_episode = {now, dilation};
            ++status.starts;
        }
    }

    void observe_landing(DeathScope& scope, Unreal::UObject* pawn, Unreal::UObject* movement, const void* parameters)
    {
        auto& status = scope.fall_status;
        const auto& binding = scope.fall;
        const auto episode = scope.fall_episode;
        scope.fall_episode = {};

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
            const auto elapsed = (game_seconds(binding, static_cast<Unreal::UWorld*>(scope.world.Get())) - episode.start) * dilation;
            if (std::isfinite(elapsed) && elapsed > 0)
            {
                status.elapsed = elapsed;
            }
        }

        const auto* hit = binding.hit->ContainerPtrToValuePtr<void>(parameters);
        const auto health = binding.guest_health->GetPropertyValueInContainer(scope.ownership.save.Get());
        if (status.actor_tick_enabled)
        {
            status.reason = "stock_actor_tick_enabled";
        }
        else if (!std::isfinite(original_fall_time) || original_fall_time < 0 || original_fall_time > stock_fall_duration)
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
        if (!parameters || iteration.OriginalFunctionCallPrevented() || !fall_scope_current() ||
            death_scope.guest.object.Get() != context || !qualified_local(death_scope))
        {
            return;
        }
        const auto& binding = death_scope.fall;
        auto* movement = binding.movement.Get();
        if (!live_object(movement) || read_object(binding.pawn_movement, context) != movement)
        {
            death_scope.fall_episode = {};
            return;
        }
        if (callee == binding.mode_changed)
        {
            observe_mode_change(death_scope, context, movement, parameters);
        }
        else
        {
            observe_landing(death_scope, context, movement, parameters);
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

        const bool local = death_scope.local.address == address;
        const auto& player = local ? death_scope.local : death_scope.guest;
        if (player.address != address || player.object.Get() != context ||
            !(local ? qualified_local(death_scope) : qualified_guest(death_scope)))
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

    void intercept_friendly_fire(Iteration& iteration, Unreal::UObject* context, void* parameters, Unreal::FFrame* frame)
    {
        const auto generation = bridge_generation.load(std::memory_order_acquire);
        if (!context || !death_scope.local.address || death_scope.generation != generation || !ready() ||
            Clock::now() >= death_scope.expires || iteration.OriginalFunctionCallPrevented())
        {
            return;
        }
        auto* host = death_scope.local.object.Get();
        auto* guest = death_scope.guest.object.Get();
        if (!host || !guest || (context != host && context != guest))
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
        const auto* attacker = schema.instigator->GetObjectPropertyValue(schema.instigator->ContainerPtrToValuePtr<void>(parameters));
        if (attacker != (context == host ? guest : host) || !qualified_local(death_scope))
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
        const auto address = reinterpret_cast<std::uintptr_t>(context);
        if (address && (address == probe.pawn_address || address == death_scope.local.address ||
                        address == death_scope.guest.address))
        {
            // ProcessLocalScriptFunction receives the prepared callee frame on
            // UE 5.4. The caller has already consumed the argument expressions.
            intercept_call(iteration, context, frame.Node(), nullptr, &frame);
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
            silverfish::native_zone::uninstall_hooks();
            silverfish::native_transport::uninstall_hooks();
            invalidate_bridge();
            hooks_ready.store(false, std::memory_order_release);
            scope_hooks_ready.store(false, std::memory_order_release);
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
            silverfish::native_zone::install_hooks();
            silverfish::native_transport::install_hooks();
            if (!enabled)
            {
                return;
            }

            hooks[0] = Hook::RegisterProcessEventPreCallback(
                [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void* parameters) {
                    if (Unreal::IsInGameThreadRaw())
                    {
                        if (callee && (callee == death_scope.fall.mode_changed || callee == death_scope.fall.landed))
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
            if (scoped_events && installed)
            {
                hooks[3] = Hook::RegisterLoadMapPreCallback(
                    [](Hook::TCallbackIterationData<bool>&, Unreal::UEngine*, Unreal::FWorldContext&,
                       Unreal::FURL, Unreal::UPendingNetGame*, Unreal::FString&) {
                        if (Unreal::IsInGameThreadRaw())
                        {
                            death_scope = {};
                        }
                    },
                    {false, true, mod_name, STR("Clear death scope before travel")});
                scope_hooks_ready.store(hooks[3] != Hook::ERROR_ID, std::memory_order_release);
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
            if (!enabled)
            {
                return;
            }

            invalidate_bridge();
            lua.register_function("SilverfishNativeDeathReady", lua_ready);
            lua.register_function("SilverfishArmNativeDeath", lua_arm);
            lua.register_function("SilverfishDisarmNativeDeath", lua_disarm);
            lua.register_function("SilverfishTakeNativeDeathHit", lua_take_hit);
            if (scoped_events)
            {
                lua.register_function("SilverfishSetNativeDeathScope", lua_set_scope);
                lua.register_function("SilverfishClearNativeDeathScope", lua_clear_scope);
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

        const bool enabled = environment_equals("SILVERFISH_COOP_NATIVE_DEATH", "cpp");
        const bool scoped_events = enabled && environment_equals("SILVERFISH_COOP_NATIVE_EVENTS", "1");
        std::array<Hook::GlobalCallbackId, 4> hooks{};
    };
}

extern "C" __declspec(dllexport) RC::CppUserModBase* start_mod()
{
    return new SilverfishNativeMod();
}

extern "C" __declspec(dllexport) void uninstall_mod(RC::CppUserModBase* mod)
{
    delete mod;
}
