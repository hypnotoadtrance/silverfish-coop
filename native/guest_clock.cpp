#include "guest_clock.hpp"
#include "world_path.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string_view>
#include <utility>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <LuaType/LuaUObject.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Clock = std::chrono::steady_clock;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto widget_path =
        STR("/Game/Classes/GUI/SubWidgets/Time_Location_Header_Widget.Time_Location_Header_Widget_C");
    constexpr auto graph_path =
        STR("/Game/Classes/GUI/SubWidgets/Time_Location_Header_Widget.Time_Location_Header_Widget_C:ExecuteUbergraph_Time_Location_Header_Widget");
    constexpr auto tick_path =
        STR("/Game/Classes/GUI/SubWidgets/Time_Location_Header_Widget.Time_Location_Header_Widget_C:Tick");
    constexpr auto time_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:GetCanonicalTime");
    constexpr auto owner_path = STR("/Script/UMG.Widget:GetOwningPlayer");
    constexpr auto timespan_path = STR("/Script/CoreUObject.Timespan");
    constexpr auto lease = std::chrono::seconds(2);
    constexpr std::int64_t ticks_per_ms = 10'000;
    constexpr std::int64_t max_ms = (std::numeric_limits<std::int64_t>::max)() / ticks_per_ms;
    constexpr std::int64_t max_sequence = (std::int64_t{1} << 53) - 1;
    constexpr int max_samples = 100'000;

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_CLOCK") && env("SILVERFISH_COOP_NATIVE_ZONE");
    }

    bool fixtures()
    {
        return env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_GUEST_CLOCK_FIXTURES");
    }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = object ? U::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(object, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    bool false_field(U::UObject* object, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property && !property->GetPropertyValueInContainer(object);
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(U::UObject* object, const U::TCHAR* path)
    {
        return live(object) && object->GetClassPrivate() == find<U::UClass>(path);
    }

    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    std::int64_t ticks(const void* source)
    {
        std::int64_t result{};
        std::memcpy(&result, source, sizeof(result));
        return result;
    }

    struct Seat
    {
        U::FWeakObjectPtr pawn, controller, world, driver, connection, local, instance, save;

        bool current() const
        {
            auto* p = pawn.Get();
            auto* c = controller.Get();
            auto* w = world.Get();
            auto* d = driver.Get();
            auto* net = connection.Get();
            auto* l = local.Get();
            auto* game = instance.Get();
            auto* slot = save.Get();
            auto* role = field<U::FByteProperty>(p, STR("Role"));
            auto* player_slots = field<U::FArrayProperty>(game, STR("LocalPlayers"));
            auto* entry = player_slots ? U::CastField<U::FObjectProperty>(player_slots->GetInner()) : nullptr;
            if (!live(p) || !live(c) || !live(w) || !live(d) || !live(net) || !live(l) || !live(game) || !live(slot) ||
                !role || role->GetPropertyValueInContainer(p) != 2 || !entry ||
                !false_field(c, STR("Loading")) || !false_field(p, STR("bActorIsBeingDestroyed")) ||
                !false_field(c, STR("bActorIsBeingDestroyed")) ||
                static_cast<U::AActor*>(p)->GetWorld() != w || static_cast<U::AActor*>(c)->GetWorld() != w ||
                object_field(p, STR("Controller")) != c || object_field(c, STR("Pawn")) != p ||
                object_field(c, STR("Player")) != l || object_field(l, STR("PlayerController")) != c ||
                object_field(w, STR("NetDriver")) != d || object_field(d, STR("World")) != w ||
                object_field(d, STR("ServerConnection")) != net || object_field(net, STR("Driver")) != d ||
                object_field(w, STR("OwningGameInstance")) != game ||
                object_field(game, STR("SaveSlot_Ref")) != slot || object_field(p, STR("Save")) != slot) return false;
            U::FScriptArrayHelper locals(player_slots, player_slots->ContainerPtrToValuePtr<void>(game));
            return locals.Num() == 1 && entry->GetObjectPropertyValue(locals.GetRawPtr()) == l;
        }

        bool bind(Address pawn_address, Address controller_address, Address world_address)
        {
            U::UObject* p{};
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (address(candidate) != pawn_address) return RC::LoopAction::Continue;
                if (exact(candidate, pawn_path)) p = candidate;
                return RC::LoopAction::Break;
            });
            if (!p) return false;
            auto* w = static_cast<U::AActor*>(p)->GetWorld();
            auto* c = object_field(p, STR("Controller"));
            auto* d = object_field(w, STR("NetDriver"));
            auto* net = object_field(d, STR("ServerConnection"));
            auto* l = object_field(c, STR("Player"));
            auto* game = object_field(w, STR("OwningGameInstance"));
            auto* slot = object_field(game, STR("SaveSlot_Ref"));
            auto* local_class = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
            auto* conn_class = find<U::UClass>(STR("/Script/Engine.NetConnection"));
            if (!live(w) || address(w) != world_address ||
                !silverfish::world_path::object(std::wstring_view{w->GetPathName()}) ||
                address(c) != controller_address || !exact(c, controller_path) || !exact(game, instance_path) ||
                !exact(slot, save_path) || !live(l) || !local_class || !l->IsA(local_class) ||
                !live(net) || !conn_class || !net->IsA(conn_class)) return false;
            pawn = p;
            controller = c;
            world = w;
            driver = d;
            connection = net;
            local = l;
            instance = game;
            save = slot;
            return current();
        }
    };

    struct Schema
    {
        U::UClass* widget{};
        U::UFunction* canonical{};
        U::UFunction* graph{};
        U::UFunction* tick{};
        U::UFunction* owning_player{};
        U::FStructProperty* output{};
        U::FStructProperty* display{};
        U::FStructProperty* saved_time{};
        U::FIntProperty* entry{};

        bool bind(const Seat& seat)
        {
            auto* timespan = find<U::UScriptStruct>(timespan_path);
            widget = find<U::UClass>(widget_path);
            canonical = find<U::UFunction>(time_path);
            graph = find<U::UFunction>(graph_path);
            tick = find<U::UFunction>(tick_path);
            owning_player = find<U::UFunction>(owner_path);
            auto* controller = owning_player ? U::CastField<U::FObjectProperty>(owning_player->GetReturnProperty()) : nullptr;
            output = field<U::FStructProperty>(canonical, STR("Timespan"));
            display = field<U::FStructProperty>(graph, STR("CallFunc_GetCanonicalTime_TimeSpan"));
            saved_time = field<U::FStructProperty>(seat.save.Get(), STR("GameTime"));
            entry = field<U::FIntProperty>(graph, STR("EntryPoint"));
            if (!widget || !timespan || !canonical || !graph || !tick || !owning_player || !controller ||
                !entry || entry->GetOffset_Internal() != 0 || !entry->HasAnyPropertyFlags(U::CPF_Parm) ||
                entry->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm) || graph->GetNumParms() != 1 ||
                graph->GetOuterPrivate() != widget || tick->GetOuterPrivate() != widget ||
                graph->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                tick->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                canonical->GetOuterPrivate() != seat.instance.Get()->GetClassPrivate() || canonical->GetReturnProperty() ||
                canonical->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
                canonical->GetNumParms() != 1 || canonical->GetParmsSize() != sizeof(std::int64_t) ||
                !output || output->GetOffset_Internal() != 0 ||
                !output->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm) ||
                !display || display->HasAnyPropertyFlags(U::CPF_Parm) ||
                !owning_player->HasAnyFunctionFlags(U::FUNC_Native) || owning_player->GetNumParms() != 1 ||
                owning_player->GetParmsSize() != sizeof(U::UObject*) || controller->GetOffset_Internal() != 0)
                return false;
            for (auto* property : {output, display, saved_time})
                if (!property || property->GetStruct().Get() != timespan || property->GetSize() != sizeof(std::int64_t))
                    return false;
            return true;
        }

        bool widget_matches(const Seat& seat, U::UObject* object) const
        {
            if (!live(object) || object->GetClassPrivate() != widget || object->GetWorld() != seat.world.Get() ||
                object_field(object, STR("GameInstance")) != seat.instance.Get()) return false;
            U::UObject* controller{};
            object->ProcessEvent(owning_player, &controller);
            return controller && controller == seat.controller.Get();
        }
    };

    struct Scope
    {
        Seat seat{};
        Schema schema{};
        Clock::time_point expires{};
        std::uint64_t generation{};
        std::int64_t sequence{}, milliseconds{}, original{};
        int hits{};
        U::FWeakObjectPtr last_widget{};
    } scope;

    std::atomic<std::uint64_t> generation{1};
    std::atomic<bool> installed{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;

    bool available() { return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire); }

    int saved_game_seconds(const Lua& lua)
    {
        if (!U::IsInGameThreadRaw() || !U::UnrealInitializer::ShouldUseHashTables()) return 0;
        auto* stack = lua.get_lua_state();
        if (lua_gettop(stack) != 1 || !luaL_testudata(stack, 1, "UObject")) return 0;
        lua_getfield(stack, 1, "IsValid");
        if (!lua_iscfunction(stack, -1)) return 0;
        lua_pushvalue(stack, 1);
        if (lua_pcall(stack, 1, 1, 0) != LUA_OK || !lua_toboolean(stack, -1)) return 0;
        lua_pop(stack, 1);
        auto* save = lua.get_userdata<RC::LuaType::UObject>(1, true).get_remote_cpp_object();
        if (!live(save)) return 0;

        static U::FWeakObjectPtr cached_class;
        static U::FStructProperty* saved_time{};
        auto* save_class = cached_class.Get();
        if (!save_class)
        {
            save_class = U::Cast<U::UClass>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, save_path, false, true));
            auto* timespan = U::Cast<U::UScriptStruct>(
                U::FUObjectHashTables::StaticFindObjectByPath(nullptr, timespan_path, false, true));
            auto* property = field<U::FStructProperty>(save_class, STR("GameTime"));
            if (!save_class || !timespan || !property || property->GetStruct().Get() != timespan ||
                property->GetSize() != sizeof(std::int64_t)) return 0;
            cached_class = save_class;
            saved_time = property;
        }
        if (save->GetClassPrivate() != save_class) return 0;
        const auto raw = ticks(saved_time->ContainerPtrToValuePtr<void>(save));
        if (raw < 0) return 0;
        const auto milliseconds = raw / ticks_per_ms;
        const auto seconds = static_cast<double>(milliseconds / 1000) + static_cast<double>(milliseconds % 1000) / 1000;
        if (seconds > 922337203685.0) return 0;
        lua.set_number(seconds);
        return 1;
    }

    int verify_clock_seat(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 6 || !scope.sequence ||
            scope.generation != generation.load(std::memory_order_acquire)) return 0;
        const auto& seat = scope.seat;
        for (auto* object : {seat.world.Get(), seat.pawn.Get(), seat.controller.Get(), seat.instance.Get(), seat.connection.Get()})
            if (!object || !lua.is_integer() || static_cast<Address>(lua.get_integer()) != address(object)) return 0;
        if (!lua.is_integer() || lua.get_integer() != scope.sequence || !seat.current()) return 0;
        lua.set_bool(true);
        return 1;
    }

    bool active()
    {
        return scope.sequence && scope.generation == generation.load(std::memory_order_acquire) &&
            Clock::now() < scope.expires && scope.seat.current();
    }

    void project(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!available() || !scope.sequence || frame.Node() != scope.schema.canonical ||
            call.OriginalFunctionCallPrevented() || receiver != scope.seat.instance.Get() || receiver != frame.Object())
            return;
        auto* parent = frame.PreviousFrame();
        auto* tick = parent ? parent->PreviousFrame() : nullptr;
        auto* widget = parent ? parent->Object() : nullptr;
        if (!parent || parent->Node() != scope.schema.graph || !tick || tick->Node() != scope.schema.tick ||
            tick->Object() != widget || !parent->Locals() ||
            scope.schema.entry->GetPropertyValueInContainer(parent->Locals()) != 10 ||
            !active() || !scope.schema.widget_matches(scope.seat, widget)) return;

        auto* parameter = frame.OutParms();
        if (!parameter || parameter->Property != scope.schema.output || !parameter->PropAddr || parameter->NextOutParm)
            return;
        auto* destination = scope.schema.display->ContainerPtrToValuePtr<void>(parent->Locals());
        auto* saved = scope.schema.saved_time->ContainerPtrToValuePtr<void>(scope.seat.save.Get());
        if (parameter->PropAddr != destination || destination == saved) return;
        const auto original = ticks(saved);
        if (ticks(destination) != original) return;
        const auto replacement = scope.milliseconds * ticks_per_ms;
        std::memcpy(destination, &replacement, sizeof(replacement));
        if (ticks(saved) != original || ticks(destination) != replacement) return;
        scope.original = original / ticks_per_ms;
        scope.last_widget = widget;
        if (scope.hits < max_samples) ++scope.hits;
    }

    int result(const Lua& lua, bool accepted, std::string_view status)
    {
        lua.set_bool(accepted);
        lua.set_string(status);
        return 2;
    }

    int set_scope(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_integer(4) || !lua.is_integer(5)) return result(lua, false, "guest_clock_scope_unavailable");
        const auto pawn = lua.get_integer();
        const auto controller = lua.get_integer();
        const auto world = lua.get_integer();
        const auto sequence = lua.get_integer();
        const auto milliseconds = lua.get_integer();
        if (pawn <= 0 || controller <= 0 || world <= 0 || sequence <= 0 || sequence > max_sequence ||
            milliseconds < 0 || milliseconds > max_ms) return result(lua, false, "guest_clock_frame_out_of_range");
        const auto epoch = generation.load(std::memory_order_acquire);
        if (scope.generation == epoch && address(scope.seat.pawn.Get()) == static_cast<Address>(pawn) &&
            address(scope.seat.controller.Get()) == static_cast<Address>(controller) &&
            address(scope.seat.world.Get()) == static_cast<Address>(world) && scope.seat.current())
        {
            if (sequence <= scope.sequence) return result(lua, false, "guest_clock_frame_stale");
        }
        else
        {
            Scope next;
            if (!next.seat.bind(pawn, controller, world) || !next.schema.bind(next.seat))
                return result(lua, false, "guest_clock_exact_seat_or_widget_schema_unavailable");
            next.generation = epoch;
            scope = std::move(next);
        }
        scope.sequence = sequence;
        scope.milliseconds = milliseconds;
        scope.expires = Clock::now() + lease;
        scope.hits = 0;
        scope.last_widget = U::FWeakObjectPtr{};
        return result(lua, true, "armed");
    }

    int read(const Lua& lua)
    {
        if (!available() || lua.get_stack_size() != 1 || !lua.is_integer(1) ||
            static_cast<Address>(lua.get_integer()) != address(scope.seat.pawn.Get()) || !active()) return 0;
        lua.set_integer(scope.sequence);
        lua.set_integer(scope.milliseconds);
        lua.set_integer(scope.hits);
        if (scope.hits) lua.set_integer(scope.original);
        return scope.hits ? 4 : 3;
    }

    int clear(const Lua&)
    {
        silverfish::native_guest_clock::clear_scope();
        return 0;
    }

    int probe(const Lua& lua)
    {
        if (!available() || !fixtures() || lua.get_stack_size() != 1 || !lua.is_integer(1) || !active())
            return result(lua, false, "guest_clock_fixture_scope_unavailable");
        const auto wanted = lua.get_integer();
        U::UObject* widget{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != static_cast<Address>(wanted)) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == scope.schema.widget) widget = candidate;
            return RC::LoopAction::Break;
        });
        if (!widget || !scope.schema.widget_matches(scope.seat, widget))
            return result(lua, false, "guest_clock_fixture_widget_unqualified");
        auto* function = scope.schema.tick;
        auto* geometry = field<U::FStructProperty>(function, STR("MyGeometry"));
        auto* delta = field<U::FFloatProperty>(function, STR("InDeltaTime"));
        alignas(16) std::array<std::byte, 64> params{};
        if (function->GetNumParms() != 2 || function->GetReturnProperty() ||
            function->GetParmsSize() < 60 || function->GetParmsSize() > params.size() ||
            !geometry || geometry->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/SlateCore.Geometry")) ||
            geometry->GetOffset_Internal() != 0 || geometry->GetSize() != 56 || !geometry->HasAnyPropertyFlags(U::CPF_Parm) ||
            !delta || delta->GetOffset_Internal() != 56 || !delta->HasAnyPropertyFlags(U::CPF_Parm))
            return result(lua, false, "guest_clock_fixture_tick_schema_unavailable");
        const auto before = scope.hits;
        auto* saved = scope.schema.saved_time->ContainerPtrToValuePtr<void>(scope.seat.save.Get());
        const auto original = ticks(saved);
        geometry->InitializeValue_InContainer(params.data());
        delta->SetPropertyValueInContainer(params.data(), 0.0f);
        widget->ProcessEvent(function, params.data());
        geometry->DestroyValue_InContainer(params.data());
        const bool verified = scope.hits == before + 1 && scope.last_widget.Get() == widget && ticks(saved) == original;
        return result(lua, verified, verified ? "stock_widget_tick_verified" : "guest_clock_fixture_tick_unverified");
    }
}

void silverfish::native_guest_clock::install_hooks()
{
    if (!enabled() || installed.load(std::memory_order_acquire)) return;
    callback = Hook::RegisterProcessLocalScriptFunctionPostCallback(
        project, {false, false, STR("SilverfishCoopNative"), STR("Project host clock into exact guest time widget")});
    installed.store(callback != Hook::ERROR_ID, std::memory_order_release);
}

void silverfish::native_guest_clock::uninstall_hooks()
{
    installed.store(false, std::memory_order_release);
    clear_scope();
    if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
    callback = Hook::ERROR_ID;
}

void silverfish::native_guest_clock::clear_scope()
{
    generation.fetch_add(1, std::memory_order_acq_rel);
    if (U::IsInGameThreadRaw()) scope = {};
}

void silverfish::native_guest_clock::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    lua.register_function("SilverfishReadNativeSavedGameSeconds", saved_game_seconds);
    if (!enabled()) return;
    clear_scope();
    lua.register_function("SilverfishSetNativeGuestClock", set_scope);
    lua.register_function("SilverfishVerifyNativeGuestClockSeat", verify_clock_seat);
    lua.register_function("SilverfishReadNativeGuestClock", read);
    lua.register_function("SilverfishClearNativeGuestClock", clear);
    if (fixtures()) lua.register_function("SilverfishProbeNativeGuestClock", probe);
}
