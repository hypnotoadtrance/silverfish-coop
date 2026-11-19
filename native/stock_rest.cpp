#include "stock_rest.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    namespace Hook = Unreal::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
    constexpr auto bed_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.SavePoint_BP_C_3");
    constexpr auto bed_class = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C");
    constexpr auto sleep_path = STR("/Game/Classes/Actors/SavePoint_BP.SavePoint_BP_C:GoToSleep");
    constexpr auto screen_class = STR("/Game/Classes/GUI/New/SleepScreen.SleepScreen_C");
    constexpr auto screen_script = STR("/Game/Classes/GUI/New/SleepScreen.SleepScreen_C:ExecuteUbergraph_SleepScreen");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto scope_lease = std::chrono::milliseconds(1250);
    constexpr auto request_lease = std::chrono::seconds(2);
    constexpr std::int32_t max_hours = 10;

    struct Scope
    {
        std::uint64_t generation{};
        Clock::time_point expires{};
        Unreal::FWeakObjectPtr pawn;
        Unreal::FWeakObjectPtr bed;
        Unreal::FWeakObjectPtr controller;
        Unreal::FWeakObjectPtr player;
        Unreal::FWeakObjectPtr world;
        Unreal::FWeakObjectPtr driver;
        Unreal::FWeakObjectPtr instance;
        Unreal::FWeakObjectPtr save;
        Unreal::UFunction* sleep{};
        Unreal::FIntProperty* hours{};
        bool pending{};
        Clock::time_point request_expires{};
        std::int32_t selection{};
        std::string_view status{"unbound"};
    };

    std::atomic<bool> installed{};
    std::atomic<std::uint64_t> generation{1};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;
    thread_local Scope scope;

    bool enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_ZONE") == 0 &&
               size == value.size() && value[0] == '1';
    }

    template <typename T>
    T* find(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(Unreal::UObject* owner, const Unreal::TCHAR* name)
    {
        auto* value = owner ? Unreal::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    Unreal::UObject* object_field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FObjectProperty>(object, name);
        return value ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool is_live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool is_false(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = field<Unreal::FBoolProperty>(object, name);
        return value && !value->GetPropertyValueInContainer(object);
    }

    bool authority(Unreal::UObject* object, Unreal::UObject* world)
    {
        auto* role = field<Unreal::FByteProperty>(object, STR("Role"));
        return is_live(object) && role && role->GetPropertyValueInContainer(object) == 3 &&
               is_false(object, STR("bActorIsBeingDestroyed")) &&
               static_cast<Unreal::AActor*>(object)->GetWorld() == world;
    }

    Address address(Unreal::UObject* object)
    {
        return reinterpret_cast<Address>(object);
    }

    bool sole_local_player(Unreal::UObject* instance, Unreal::UObject* player)
    {
        auto* array = field<Unreal::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = array ? Unreal::CastField<Unreal::FObjectProperty>(array->GetInner()) : nullptr;
        if (!entry) return false;
        Unreal::FScriptArrayHelper players(array, array->ContainerPtrToValuePtr<void>(instance));
        return players.Num() == 1 && entry->GetObjectPropertyValue(players.GetRawPtr()) == player;
    }

    bool live_scope(const Scope& current)
    {
        auto* world = current.world.Get();
        auto* pawn = current.pawn.Get();
        auto* bed = current.bed.Get();
        auto* controller = current.controller.Get();
        auto* instance = current.instance.Get();
        auto* save = current.save.Get();
        auto* net = current.driver.Get();
        auto* local = current.player.Get();
        if (!is_live(world) || !is_live(instance) || !is_live(save) || !is_live(net) || !is_live(local) ||
            !authority(pawn, world) || !authority(controller, world) || !authority(bed, world) ||
            !is_false(pawn, STR("Dead")) || !is_false(controller, STR("Loading"))) return false;

        auto* level = object_field(world, STR("PersistentLevel"));
        return level && static_cast<Unreal::AActor*>(bed)->GetLevel() == level &&
               object_field(world, STR("NetDriver")) == net && object_field(net, STR("World")) == world &&
               field<Unreal::FObjectProperty>(net, STR("ServerConnection")) && !object_field(net, STR("ServerConnection")) &&
               object_field(world, STR("OwningGameInstance")) == instance &&
               object_field(bed, STR("As Game Instance FP")) == instance &&
               object_field(instance, STR("SaveSlot_Ref")) == save && object_field(pawn, STR("Save")) == save &&
               object_field(pawn, STR("Controller")) == controller && object_field(controller, STR("Pawn")) == pawn &&
               object_field(controller, STR("Player")) == local && sole_local_player(instance, local);
    }

    bool configure(Address pawn_address, Address bed_address)
    {
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && address(scope.pawn.Get()) == pawn_address && address(scope.bed.Get()) == bed_address)
        {
            if (!live_scope(scope)) return false;
            scope.expires = Clock::now() + scope_lease;
            return true;
        }

        auto* bed = find<Unreal::UObject>(bed_path);
        auto* stock_bed_class = find<Unreal::UClass>(bed_class);
        auto* stock_pawn_class = find<Unreal::UClass>(pawn_class);
        auto* world = find<Unreal::UWorld>(world_path);
        if (!stock_bed_class || !stock_pawn_class || !is_live(bed) || address(bed) != bed_address ||
            bed->GetClassPrivate() != stock_bed_class || !is_live(world)) return false;

        Scope next;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* candidate, int, int) {
            if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
            if (candidate->GetClassPrivate() == stock_pawn_class && is_live(candidate)) next.pawn = candidate;
            return RC::LoopAction::Break;
        });
        auto* pawn = next.pawn.Get();
        auto* controller = object_field(pawn, STR("Controller"));
        auto* player = object_field(controller, STR("Player"));
        auto* instance = object_field(world, STR("OwningGameInstance"));
        auto* save = object_field(instance, STR("SaveSlot_Ref"));
        auto* local_player_class = find<Unreal::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!is_live(controller) || controller->GetClassPrivate() != find<Unreal::UClass>(controller_class) ||
            !is_live(instance) || instance->GetClassPrivate() != find<Unreal::UClass>(instance_class) ||
            !is_live(save) || save->GetClassPrivate() != find<Unreal::UClass>(save_class) ||
            !local_player_class || !is_live(player) || !player->IsA(local_player_class)) return false;

        next.bed = bed;
        next.world = world;
        next.controller = controller;
        next.player = player;
        next.driver = object_field(world, STR("NetDriver"));
        next.instance = instance;
        next.save = save;
        next.sleep = find<Unreal::UFunction>(sleep_path);
        next.hours = field<Unreal::FIntProperty>(next.sleep, STR("Hours"));
        if (!next.hours || !next.hours->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            next.hours->HasAnyPropertyFlags(Unreal::CPF_OutParm | Unreal::CPF_ReturnParm) ||
            next.hours->GetOffset_Internal() != 0 || next.sleep->GetNumParms() != 1 ||
            next.sleep->GetParmsSize() != sizeof(std::int32_t) || next.sleep->GetReturnProperty() ||
            next.sleep->GetOuterPrivate() != stock_bed_class || !live_scope(next)) return false;

        next.generation = epoch;
        next.expires = Clock::now() + scope_lease;
        next.status = "armed";
        scope = next;
        return true;
    }

    Unreal::UObject* qualified_screen(Unreal::FFrame& stack, Unreal::UObject* bed)
    {
        auto* caller = stack.PreviousFrame();
        auto* screen = caller ? caller->Object() : nullptr;
        auto* type = find<Unreal::UClass>(screen_class);
        auto* script = find<Unreal::UFunction>(screen_script);
        return is_live(screen) && type && script && caller->Node() == script && screen->GetClassPrivate() == type &&
               object_field(screen, STR("Bed")) == bed &&
               object_field(screen, STR("As Game Instance FP")) == scope.instance.Get() ? screen : nullptr;
    }

    void intercept(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (!Unreal::IsInGameThreadRaw() || !context || !installed.load(std::memory_order_acquire) ||
            !scope.sleep || frame.Node() != scope.sleep || scope.bed.Get() != context ||
            scope.generation != generation.load(std::memory_order_acquire) || iteration.OriginalFunctionCallPrevented())
            return;
        if (!live_scope(scope) || object_field(context, STR("Player")) != scope.pawn.Get()) return;
        auto* screen = qualified_screen(frame, context);
        if (!screen) return;

        iteration.PreventOriginalFunctionCall();
        if (!iteration.OriginalFunctionCallPrevented()) return;
        const auto now = Clock::now();
        if (now >= scope.expires)
        {
            scope.status = "stale_veto";
            return;
        }
        auto* count = field<Unreal::FIntProperty>(screen, STR("Count"));
        auto* maximum = field<Unreal::FIntProperty>(screen, STR("Max"));
        const auto* locals = frame.Locals();
        const auto hours = locals ? scope.hours->GetPropertyValueInContainer(locals) : -1;
        if (!count || !maximum || maximum->GetPropertyValueInContainer(screen) != max_hours ||
            hours < 0 || hours > max_hours || count->GetPropertyValueInContainer(screen) != hours)
        {
            scope.status = "unqualified_selection_veto";
            return;
        }
        if (scope.pending && now < scope.request_expires)
        {
            scope.status = "pending_selection_veto";
            return;
        }
        scope.pending = true;
        scope.selection = hours;
        scope.request_expires = now + request_lease;
        scope.status = "selected";
    }

    bool bound()
    {
        return installed.load(std::memory_order_acquire) && scope.generation == generation.load(std::memory_order_acquire) &&
               scope.sleep && live_scope(scope);
    }

    int lua_set_scope(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && installed.load(std::memory_order_acquire) &&
            lua.get_stack_size() == 2 && lua.is_integer(1) && lua.is_integer(2))
        {
            const auto pawn = lua.get_integer();
            const auto bed = lua.get_integer();
            accepted = pawn > 0 && bed > 0 && configure(static_cast<Address>(pawn), static_cast<Address>(bed));
        }
        lua.set_bool(accepted);
        return 1;
    }

    int lua_take_request(const Lua& lua)
    {
        bool accepted = false;
        if (Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && scope.pending)
        {
            accepted = bound() && Clock::now() < scope.expires && Clock::now() < scope.request_expires;
            scope.pending = false;
            scope.status = accepted ? "selection_taken" : "selection_expired";
        }
        lua.set_bool(accepted);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.pawn.Get())) : 0);
        lua.set_integer(accepted ? static_cast<std::int64_t>(address(scope.bed.Get())) : 0);
        lua.set_integer(accepted ? scope.selection : 0);
        return 4;
    }

    int lua_read_status(const Lua& lua)
    {
        const bool current = Unreal::IsInGameThreadRaw() && lua.get_stack_size() == 0 && bound();
        lua.set_bool(installed.load(std::memory_order_acquire));
        lua.set_bool(current);
        lua.set_bool(current && Clock::now() < scope.expires);
        lua.set_string(current ? scope.status : "unbound");
        return 4;
    }

    int lua_clear_scope(const Lua&)
    {
        silverfish::native_stock_rest::clear_scope();
        return 0;
    }
}

namespace silverfish::native_stock_rest
{
    void install_hooks()
    {
        if (!enabled() || callback != Hook::ERROR_ID) return;
        callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            intercept, {false, false, owner, STR("Stock Observatory sleep selection")});
        installed.store(callback != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        clear_scope();
        if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
        callback = Hook::ERROR_ID;
    }

    void register_lua(RC::LuaMadeSimple::Lua& lua)
    {
        if (!enabled()) return;
        clear_scope();
        lua.register_function("SilverfishSetNativeStockRestScope", lua_set_scope);
        lua.register_function("SilverfishTakeNativeStockRestRequest", lua_take_request);
        lua.register_function("SilverfishReadNativeStockRestStatus", lua_read_status);
        lua.register_function("SilverfishClearNativeStockRestScope", lua_clear_scope);
    }

    void clear_scope()
    {
        generation.fetch_add(1, std::memory_order_acq_rel);
        if (Unreal::IsInGameThreadRaw()) scope = {};
    }
}
