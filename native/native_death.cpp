#include <array>
#include <atomic>
#include <chrono>
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

#include "save_copy.hpp"

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
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto probe_timeout = std::chrono::seconds(2);
    constexpr auto scope_timeout = std::chrono::milliseconds(1250);
    constexpr std::uint8_t authority_role = 3; // ENetRole::ROLE_Authority in UE 5.4.

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

    struct GuestController
    {
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr connection;
        Unreal::FObjectProperty* pawn_controller{};
        Unreal::FObjectProperty* controller_player{};
    };

    struct DeathScope
    {
        ScopedPawn guest;
        SaveOwnership ownership;
        GuestController controller;
        Unreal::FWeakObjectPtr world;
        Unreal::UFunction* die{};
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool pending{};
    };

    thread_local Probe probe;
    thread_local DeathScope death_scope;

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

    [[nodiscard]] Unreal::UFunction* find_die()
    {
        auto* die = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(
            nullptr, nullptr, die_path);
        if (!die || die->GetFullName() != die_full_name || die->GetReturnProperty() ||
            die->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            die->HasAnyFunctionFlags(Unreal::FUNC_Net))
        {
            return nullptr;
        }
        return die;
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

        auto* die = find_die();
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

    [[nodiscard]] bool bind_guest_controller(GuestController& ownership, Unreal::UObject* pawn)
    {
        auto* controller_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(
            nullptr, nullptr, STR("/Script/Engine.PlayerController"));
        auto* connection_class = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(
            nullptr, nullptr, STR("/Script/Engine.NetConnection"));
        if (!controller_class || !connection_class)
        {
            return false;
        }
        ownership.pawn_controller = object_field(pawn, STR("Controller"));
        ownership.controller_player = object_field(controller_class, STR("Player"));
        auto* controller = read_object(ownership.pawn_controller, pawn);
        if (!live_object(controller) || !controller->IsA(controller_class))
        {
            return false;
        }
        auto* player = read_object(ownership.controller_player, controller);
        if (!live_object(player) || !player->IsA(connection_class))
        {
            return false;
        }
        ownership.controller = controller;
        ownership.connection = player;
        return true;
    }

    [[nodiscard]] bool same_remote_controller(const GuestController& ownership, Unreal::UObject* pawn)
    {
        auto* controller = ownership.controller.Get();
        auto* connection = ownership.connection.Get();
        return live_object(controller) && live_object(connection) &&
               read_object(ownership.pawn_controller, pawn) == controller &&
               read_object(ownership.controller_player, controller) == connection;
    }

    [[nodiscard]] bool qualified_guest(const DeathScope& scope)
    {
        auto* pawn = scope.guest.object.Get();
        auto* world = scope.world.Get();
        return pawn && world && world == authority_world(scope.guest) &&
               same_detached_save(scope.ownership, pawn, world) && same_remote_controller(scope.controller, pawn);
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
        if (local != 0 || guest < 0)
        {
            return false;
        }
        if (guest == 0)
        {
            death_scope = {};
            return true;
        }

        const auto address = static_cast<std::uintptr_t>(guest);
        if (death_scope.generation == generation && death_scope.guest.address == address)
        {
            if (!qualified_guest(death_scope))
            {
                return false;
            }
            death_scope.expires = Clock::now() + scope_timeout;
            return true;
        }

        DeathScope next;
        next.die = find_die();
        if (!next.die)
        {
            return false;
        }
        next.guest = find_scoped_pawn(address, next.die);
        auto* pawn = next.guest.object.Get();
        auto* world = authority_world(next.guest);
        if (!world || !bind_save(next.ownership, pawn, world) || !bind_guest_controller(next.controller, pawn))
        {
            return false;
        }
        next.world = world;
        if (!qualified_guest(next))
        {
            return false;
        }
        next.generation = generation;
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
        return 1;
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
        if (Unreal::IsInGameThreadRaw() && ready() && lua.get_stack_size() == 1 && lua.is_integer() &&
            death_scope.generation == bridge_generation.load(std::memory_order_acquire))
        {
            const auto address = lua.get_integer();
            if (address > 0 && death_scope.guest.address == static_cast<std::uintptr_t>(address) && death_scope.pending)
            {
                // A valid hit remains readable if the lease expired between ticks.
                hit = qualified_guest(death_scope);
                death_scope.pending = false;
            }
        }
        lua.set_bool(hit);
        return 1;
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

        if (death_scope.die != callee || death_scope.generation != generation || now >= death_scope.expires ||
            death_scope.guest.address != address || death_scope.guest.object.Get() != context || !qualified_guest(death_scope))
        {
            return;
        }

        iteration.PreventOriginalFunctionCall();
        death_scope.pending = death_scope.pending || iteration.OriginalFunctionCallPrevented();
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw())
        {
            return;
        }
        const auto address = reinterpret_cast<std::uintptr_t>(context);
        if (address && (address == probe.pawn_address || address == death_scope.guest.address))
        {
            // ProcessLocalScriptFunction receives the prepared callee frame on
            // UE 5.4. The caller has already consumed the argument expressions.
            intercept_death(iteration, context, frame.Node());
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
            if (!enabled)
            {
                return;
            }

            hooks[0] = Hook::RegisterProcessEventPreCallback(
                [](Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee, void*) {
                    if (Unreal::IsInGameThreadRaw())
                    {
                        intercept_death(iteration, context, callee);
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
            if (!enabled || name != lua_mod_name)
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
            }
            silverfish::register_native_save_copy_lua(lua);
            bridge_generation.fetch_add(1, std::memory_order_release);
        }

        auto on_lua_stop(RC::StringViewType name, Lua&, Lua&, Lua&, Lua*) -> void override
        {
            if (enabled && name == lua_mod_name)
            {
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
