#include "session_exit_input.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

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
#include <Windows.h>

namespace
{
    namespace U = RC::Unreal;
    namespace Hooks = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Iteration = Hooks::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto owner = STR("SilverfishCoopNative");
    constexpr auto pause_path = STR("/Game/Classes/GUI/Menu_Pause_Widget.Menu_Pause_Widget_C");
    constexpr auto confirm_path = STR("/Game/Classes/GUI/New/ConfirmationScreen.ConfirmationScreen_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto quit_name = STR("QuitToMenu");
    constexpr auto save_name =
        STR("BndEvt__Menu_Pause_Widget_Button_SaveAndQuit_K2Node_ComponentBoundEvent_5_Pressed__DelegateSignature");
    constexpr auto lease_limit = std::chrono::seconds(5);
    constexpr auto pending_limit = std::chrono::seconds(3);
    constexpr auto repeat_limit = std::chrono::seconds(15);
    constexpr auto max_serial = (std::uint64_t{1} << 53) - 1;

    enum class Role : unsigned { host = 1, client = 2 };
    enum class Intent : int { quit_to_menu = 1, save_and_quit = 2 };

    struct Lease
    {
        std::string token;
        Role role;
        Address world, controller, pawn;

        bool operator==(const Lease&) const = default;
    };

    template <typename String>
    bool exact_token(String token)
    {
        using Char = typename String::value_type;
        if (token.size() != 32) return false;
        bool nonzero = false;
        for (const Char unit : token)
        {
            if (!((unit >= '0' && unit <= '9') || (unit >= 'a' && unit <= 'f'))) return false;
            nonzero |= unit != '0';
        }
        return nonzero;
    }

    bool enabled(const wchar_t* name)
    {
        wchar_t value[2]{};
        return GetEnvironmentVariableW(name, value, 2) == 1 && value[0] == L'1';
    }

    struct Offer
    {
        std::string token;
        Role role{};
        Clock::time_point expires{};
        std::uint64_t press{};
        Intent intent{};
        bool taken{};
    };

    std::mutex offer_mutex;
    Offer offer;
    std::atomic<bool> installed{};
    // The scene binds on the game thread. A role token cannot move to a different world or pawn.
    std::optional<Lease> binding;
    bool bound{};

    bool active_offer()
    {
        return !offer.token.empty() && Clock::now() < offer.expires;
    }

    int bind_scene(const Lua& lua)
    {
        if (!installed.load(std::memory_order_acquire) || !U::IsInGameThreadRaw()) return 0;
        bound = false;
        if (lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_integer(3) || !lua.is_integer(4)) return 0;
        const auto role = static_cast<Role>(lua.get_integer());
        const auto world = lua.get_integer();
        const auto controller = lua.get_integer();
        const auto pawn = lua.get_integer();
        if (world <= 0 || controller <= 0 || pawn <= 0) return 0;

        std::string token;
        std::uint64_t press{};
        {
            std::lock_guard lock(offer_mutex);
            if (!active_offer() || offer.role != role) return 0;
            token = offer.token;
            press = offer.press;
        }
        auto lease = Lease{token, role, static_cast<Address>(world), static_cast<Address>(controller),
            static_cast<Address>(pawn)};
        if (binding && binding->token == token && *binding != lease) return 0;
        binding = std::move(lease);
        bound = true;
        lua.set_string(token);
        lua.set_integer(static_cast<std::int64_t>(press));
        return 2;
    }

    std::optional<Lease> read_lease()
    {
        if (!bound || !binding) return {};
        std::lock_guard lock(offer_mutex);
        return active_offer() && offer.token == binding->token && offer.role == binding->role ? binding : std::nullopt;
    }

    bool publish(const Lease& lease, std::uint64_t serial, Intent intent)
    {
        std::lock_guard lock(offer_mutex);
        if (!active_offer() || offer.token != lease.token || offer.role != lease.role || offer.press) return false;
        offer.press = serial;
        offer.intent = intent;
        return true;
    }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    bool exact(U::UObject* object, const U::TCHAR* path)
    {
        return live(object) && object->GetClassPrivate() == find<U::UClass>(path);
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

    bool bool_field(U::UObject* object, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(object, name);
        return property && property->GetPropertyValueInContainer(object) == expected;
    }

    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    struct ObjectArray
    {
        U::FObjectProperty* entry{};
        std::optional<U::FScriptArrayHelper> values;

        ObjectArray(U::UObject* receiver, const U::TCHAR* name)
        {
            if (auto* property = field<U::FArrayProperty>(receiver, name))
            {
                entry = U::CastField<U::FObjectProperty>(property->GetInner());
                if (entry) values.emplace(property, property->ContainerPtrToValuePtr<void>(receiver));
            }
        }

        int size() const { return values ? values->Num() : -1; }

        U::UObject* at(int index)
        {
            return index >= 0 && index < size() ? entry->GetObjectPropertyValue(values->GetRawPtr(index)) : nullptr;
        }

        int count(U::UObject* object)
        {
            int matches{};
            for (int index = 0; index < size(); ++index)
                if (at(index) == object) ++matches;
            return matches;
        }
    };

    bool stock_actor(U::UObject* actor, U::UObject* world, int expected_role)
    {
        if (!live(actor) || static_cast<U::AActor*>(actor)->GetWorld() != world ||
            !bool_field(actor, STR("bActorIsBeingDestroyed"), false)) return false;
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return role && role->GetPropertyValueInContainer(actor) == expected_role;
    }

    struct Seat
    {
        U::FWeakObjectPtr world{}, instance{}, controller{}, pawn{}, driver{}, local{}, viewport{}, connection{};

        bool current(const Lease& lease) const
        {
            auto* w = world.Get();
            auto* game = instance.Get();
            auto* c = controller.Get();
            auto* p = pawn.Get();
            auto* d = driver.Get();
            auto* l = local.Get();
            auto* v = viewport.Get();
            auto* net = connection.Get();
            if (!live(w) || !live(game) || !live(d) || !live(l) || !live(v) ||
                address(w) != lease.world || address(c) != lease.controller || address(p) != lease.pawn ||
                !stock_actor(p, w, lease.role == Role::host ? 3 : 2) ||
                (lease.role == Role::host && !stock_actor(c, w, 3)) ||
                !live(c) || static_cast<U::AActor*>(c)->GetWorld() != w ||
                !bool_field(c, STR("bActorIsBeingDestroyed"), false) || !bool_field(c, STR("Loading"), false) ||
                object_field(w, STR("OwningGameInstance")) != game ||
                object_field(w, STR("NetDriver")) != d || object_field(d, STR("World")) != w ||
                object_field(c, STR("Player")) != l || object_field(l, STR("PlayerController")) != c ||
                object_field(l, STR("ViewportClient")) != v || object_field(v, STR("World")) != w ||
                object_field(v, STR("GameInstance")) != game ||
                object_field(c, STR("Pawn")) != p || object_field(p, STR("Controller")) != c ||
                !field<U::FObjectProperty>(d, STR("ServerConnection")) || object_field(d, STR("ServerConnection")) != net)
                return false;
            if (lease.role == Role::host ? net != nullptr : (!live(net) || object_field(net, STR("Driver")) != d))
                return false;
            ObjectArray players(game, STR("LocalPlayers"));
            return players.size() == 1 && players.at(0) == l;
        }

        bool bind(U::UObject* widget, const Lease& lease)
        {
            auto* w = widget->GetWorld();
            if (!live(w) || address(w) != lease.world ||
                !std::wstring_view{w->GetPathName()}.starts_with(STR("/Game/Classes/Maps/"))) return false;
            auto* game = object_field(w, STR("OwningGameInstance"));
            if (!exact(game, instance_path)) return false;
            ObjectArray players(game, STR("LocalPlayers"));
            if (players.size() != 1) return false;
            auto* l = players.at(0);
            auto* c = object_field(l, STR("PlayerController"));
            auto* p = object_field(c, STR("Pawn"));
            auto* d = object_field(w, STR("NetDriver"));
            auto* v = object_field(l, STR("ViewportClient"));
            auto* net = object_field(d, STR("ServerConnection"));
            if (!exact(l, STR("/Script/Engine.LocalPlayer")) || !exact(c, controller_path) ||
                !exact(p, pawn_path) || !live(d) || !exact(v, STR("/Script/Engine.GameViewportClient"))) return false;
            const auto* net_type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
            if (lease.role == Role::client && (!live(net) || !net_type || !net->IsA(net_type))) return false;
            world = w;
            instance = game;
            controller = c;
            pawn = p;
            driver = d;
            local = l;
            viewport = v;
            connection = net;
            return current(lease);
        }
    };

    U::UFunction* script(const U::TCHAR* class_path, const U::TCHAR* name)
    {
        const auto path = RC::StringType{class_path} + STR(":") + name;
        auto* function = find<U::UFunction>(path.c_str());
        return function && function->GetOuterPrivate() == find<U::UClass>(class_path) &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ? function : nullptr;
    }

    U::FBoolProperty* bool_result(U::UFunction* function, int offset)
    {
        auto* result = function ? U::CastField<U::FBoolProperty>(function->GetReturnProperty()) : nullptr;
        return result && result->GetArrayDim() == 1 && result->GetOffset_Internal() == offset ? result : nullptr;
    }

    std::optional<bool> widget_status(U::UObject* widget, const U::TCHAR* path)
    {
        auto* function = find<U::UFunction>(path);
        auto* result = bool_result(function, 0);
        if (!result || function->GetNumParms() != 1 || function->GetParmsSize() != 1 ||
            !function->HasAnyFunctionFlags(U::FUNC_Native)) return {};
        std::uint8_t parameters{};
        widget->ProcessEvent(function, &parameters);
        return result->GetPropertyValueInContainer(&parameters);
    }

    bool mounted(U::UObject* widget)
    {
        return widget_status(widget, STR("/Script/UMG.Widget:IsInViewport")) == true &&
            widget_status(widget, STR("/Script/UMG.Widget:IsVisible")) == true;
    }

    std::optional<U::UObject*> widget_player(U::UObject* widget)
    {
        auto* function = find<U::UFunction>(STR("/Script/UMG.Widget:GetOwningPlayer"));
        auto* result = function ? U::CastField<U::FObjectProperty>(function->GetReturnProperty()) : nullptr;
        if (!result || result->GetOffset_Internal() != 0 || function->GetNumParms() != 1 ||
            function->GetParmsSize() != sizeof(U::UObject*) || !function->HasAnyFunctionFlags(U::FUNC_Native))
            return {};
        U::UObject* controller{};
        widget->ProcessEvent(function, &controller);
        return controller;
    }

    bool pause_belongs(U::UObject* widget, const Seat& seat)
    {
        return live(widget) && widget->GetWorld() == seat.world.Get() &&
            widget->GetOuterPrivate() == seat.instance.Get() && widget_player(widget) == seat.controller.Get();
    }

    bool confirmation_context(U::UObject* dialog, const Seat& seat)
    {
        if (dialog->GetWorld() != seat.world.Get()) return false;
        const auto player = widget_player(dialog);
        if (!player) return false;
        if (*player) return *player == seat.controller.Get();
        // The stock graph supplies no explicit owner. Without a resolved player, require a local outer.
        auto* outer = dialog->GetOuterPrivate();
        return outer == seat.instance.Get() || outer == seat.world.Get();
    }

    bool confirm_binds(U::UObject* dialog, U::UObject* pause)
    {
        auto* property = field<U::FMulticastInlineDelegateProperty>(dialog, STR("Confirmed"));
        auto* signature = script(confirm_path, STR("Confirmed__DelegateSignature"));
        if (!signature || signature->GetNumParms() != 0 || !property ||
            property->GetSignatureFunction().Get() != signature || property->GetSize() != sizeof(U::FMulticastScriptDelegate))
            return false;
        const auto* delegate = property->ContainerPtrToValuePtr<U::FMulticastScriptDelegate>(dialog);
        const U::FName target{quit_name, U::FNAME_Find};
        return !target.IsNone() && delegate->Num() == 1 && delegate->Contains(pause, target);
    }

    bool stock_close(U::UObject* controller)
    {
        auto* function = script(controller_path, STR("CloseMenu"));
        auto* success = field<U::FBoolProperty>(function, STR("Success"));
        if (!success || success->GetOffset_Internal() != 0 ||
            !success->HasAllPropertyFlags(U::CPF_Parm | U::CPF_OutParm) || function->GetReturnProperty() ||
            function->GetNumParms() != 1 || function->GetParmsSize() != 1) return false;
        std::uint8_t parameters{};
        controller->ProcessEvent(function, &parameters);
        return success->GetPropertyValueInContainer(&parameters);
    }

    bool host_unpaused(U::UObject* world)
    {
        auto* statics = find<U::UObject>(STR("/Script/Engine.Default__GameplayStatics"));
        auto* function = find<U::UFunction>(STR("/Script/Engine.GameplayStatics:IsGamePaused"));
        auto* context = field<U::FObjectProperty>(function, STR("WorldContextObject"));
        auto* result = bool_result(function, sizeof(U::UObject*));
        struct Parameters { U::UObject* world; std::uint8_t paused; } parameters{world, 0};
        if (!statics || !context || context->GetOffset_Internal() != 0 || !result ||
            function->GetNumParms() != 2 || function->GetParmsSize() < sizeof(U::UObject*) + 1 ||
            function->GetParmsSize() > sizeof(parameters) || !function->HasAnyFunctionFlags(U::FUNC_Native))
            return false;
        statics->ProcessEvent(function, &parameters);
        return !result->GetPropertyValueInContainer(&parameters);
    }

    struct Exit
    {
        Lease lease;
        Seat seat;
        U::FWeakObjectPtr pause;
        Intent intent;
        Clock::time_point expires;
        bool cleanup_only{};
        bool closed{};
    };

    std::optional<Exit> pending, published;
    std::uint64_t serial{};
    std::array<Hooks::GlobalCallbackId, 3> hooks{Hooks::ERROR_ID, Hooks::ERROR_ID, Hooks::ERROR_ID};
    U::FName quit_event{}, save_event{};

    void intercept(Iteration& call, U::UObject* widget, U::UFunction* function)
    {
        if (!installed.load(std::memory_order_acquire) || !U::IsInGameThreadRaw() ||
            !function || call.OriginalFunctionCallPrevented()) return;
        const auto name = function->GetFName();
        if (name != quit_event && name != save_event) return;
        auto lease = read_lease();
        if ((!lease && !published) || !exact(widget, pause_path)) return;
        const auto intent = name == quit_event ? Intent::quit_to_menu : Intent::save_and_quit;
        if (function != script(pause_path, intent == Intent::quit_to_menu ? quit_name : save_name) ||
            function->GetNumParms() != 0 || function->GetParmsSize() != 0 || function->GetReturnProperty()) return;

        const auto now = Clock::now();
        const bool repeat = published && published->expires > now &&
            (!lease || published->lease == *lease) &&
            published->seat.world.Get() == widget->GetWorld() &&
            published->seat.current(published->lease);
        if (!lease && repeat) lease = published->lease;
        Seat seat;
        if (!lease || !seat.bind(widget, *lease) || !pause_belongs(widget, seat) ||
            object_field(widget, STR("As Game Instance FP")) != seat.instance.Get()) return;
        ObjectArray stack(seat.controller.Get(), STR("MenuStack"));
        const int count = stack.size();
        if (count < 1 || count > 32 || stack.count(widget) != 1) return;
        auto* top = stack.at(count - 1);
        if (intent == Intent::save_and_quit)
        {
            if (top != widget || !mounted(widget)) return;
        }
        else if (count < 2 || stack.at(count - 2) != widget || !exact(top, confirm_path) || stack.count(top) != 1 ||
            !confirmation_context(top, seat) || !mounted(top) || !bool_field(top, STR("Dismissed"), true) ||
            !confirm_binds(top, widget)) return;

        // Both callbacks must be stopped before the stock save or level preparation can run.
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || pending || serial >= max_serial) return;
        const bool cleanup_only = published && published->lease == *lease;
        pending = Exit{std::move(*lease), seat, U::FWeakObjectPtr{widget}, intent, now + pending_limit, cleanup_only};
    }

    void tick()
    {
        if (!installed.load(std::memory_order_acquire) || !U::IsInGameThreadRaw() || !pending) return;
        const auto now = Clock::now();
        auto& exit = *pending;
        const auto lease = read_lease();
        if (now >= exit.expires || (lease && *lease != exit.lease) || !exit.seat.current(exit.lease))
        {
            pending.reset();
            return;
        }
        const bool repeat_cleanup = exit.cleanup_only && published && published->expires > now;
        if (!lease && !repeat_cleanup) { pending.reset(); return; }
        auto* controller = exit.seat.controller.Get();
        auto* menu = exit.pause.Get();
        ObjectArray stack(controller, STR("MenuStack"));
        const int count = stack.size();
        if (count < 0 || count > 32) return;
        if (!exit.closed)
        {
            // The stock confirmation click must finish its own stock CloseMenu first.
            if (!menu || count == 0 || stack.at(count - 1) != menu || stack.count(menu) != 1 ||
                !pause_belongs(menu, exit.seat) || !mounted(menu)) return;
            if (!stock_close(controller))
            {
                pending.reset();
                return;
            }
            exit.closed = true;
            return;
        }
        if ((menu && (stack.count(menu) != 0 ||
            widget_status(menu, STR("/Script/UMG.Widget:IsInViewport")) != false)) ||
            (exit.lease.role == Role::host && !host_unpaused(exit.seat.world.Get()))) return;

        if (exit.cleanup_only)
        {
            pending.reset();
            return;
        }

        if (!publish(exit.lease, serial + 1, exit.intent)) return;
        ++serial;
        exit.expires = now + repeat_limit;
        published = std::move(pending);
        pending.reset();
    }
}

extern "C" __declspec(dllexport) int SilverfishPauseOfferV1(const char* token, unsigned role)
{
    std::lock_guard lock(offer_mutex);
    if (!role)
    {
        offer = {};
        return 1;
    }
    if (!installed.load(std::memory_order_acquire) ||
        (role != static_cast<unsigned>(Role::host) && role != static_cast<unsigned>(Role::client)) ||
        !token || strnlen_s(token, 33) != 32 || !exact_token(std::string_view{token, 32})) return 0;
    const auto seat = static_cast<Role>(role);
    if (offer.token != token || offer.role != seat) offer = Offer{token, seat};
    offer.expires = Clock::now() + lease_limit;
    return 1;
}

extern "C" __declspec(dllexport) int SilverfishPauseTakeV1(const char* token, unsigned role, std::uint64_t* press)
{
    std::lock_guard lock(offer_mutex);
    // The serialized worker may have been waiting for a normal save when the native press arrived.
    if (!token || !press || offer.token.empty() || offer.token != token || role != static_cast<unsigned>(offer.role) ||
        !offer.press || offer.taken || Clock::now() >= offer.expires + 2 * lease_limit) return 0;
    offer.taken = true;
    *press = offer.press;
    return static_cast<int>(offer.intent);
}

namespace silverfish::native_session_exit_input
{
    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishBindStockPauseExit", bind_scene);
    }

    void install_hooks()
    {
        if (installed.load(std::memory_order_acquire) || !enabled(L"SILVERFISH_COOP_EMBEDDED_ENGINE") ||
            !enabled(L"SILVERFISH_COOP_NATIVE_ZONE")) return;
        quit_event = U::FName{quit_name, U::FNAME_Add};
        save_event = U::FName{save_name, U::FNAME_Add};
        hooks[0] = Hooks::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* widget, U::UFunction* function, void*) {
                intercept(call, widget, function);
            }, {false, false, owner, STR("Route exact stock Pause exit input")});
        hooks[1] = Hooks::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* widget, U::FFrame& frame, void*) {
                if (widget == frame.Object()) intercept(call, widget, frame.Node());
            }, {false, false, owner, STR("Route exact Blueprint Pause exit input")});
        hooks[2] = Hooks::RegisterEngineTickPreCallback(
            [](Iteration&, U::UEngine*, float, bool) { tick(); },
            {false, false, owner, STR("Commit exact stock Pause exit intent")});
        for (const auto hook : hooks)
        {
            if (hook != Hooks::ERROR_ID) continue;
            uninstall_hooks();
            return;
        }
        installed.store(true, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        for (auto& hook : hooks)
        {
            if (hook != Hooks::ERROR_ID) Hooks::UnregisterCallback(hook);
            hook = Hooks::ERROR_ID;
        }
        if (U::IsInGameThreadRaw())
        {
            pending.reset();
            published.reset();
            binding.reset();
            bound = false;
        }
        std::lock_guard lock(offer_mutex);
        offer = {};
    }
}
