#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <string_view>

#include <DynamicOutput/DynamicOutput.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Mod/CppUserModBase.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

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
    constexpr auto probe_timeout = std::chrono::seconds(2);

    // An even generation has no Lua bridge. Refreshing the generation invalidates
    // an outstanding game-thread probe without touching Lua from another thread.
    std::atomic<std::uint64_t> bridge_generation{};
    std::atomic<bool> hooks_ready{};

    struct Probe
    {
        std::uintptr_t pawn_address{};
        Unreal::UFunction* die{};
        std::uint64_t generation{};
        Clock::time_point expires{};
        bool intercepted{};
    };

    thread_local Probe probe;

    [[nodiscard]] bool lab_enabled()
    {
        const auto* mode = std::getenv("SILVERFISH_COOP_NATIVE_DEATH");
        return mode && std::string_view{mode} == "cpp";
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

        auto* die = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(
            nullptr, nullptr, die_path);
        if (!die || die->GetFullName() != die_full_name || die->GetReturnProperty() ||
            die->HasAnyFunctionFlags(Unreal::FUNC_Native) ||
            die->HasAnyFunctionFlags(Unreal::FUNC_Net))
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

    void intercept_death(Iteration& iteration, Unreal::UObject* context, Unreal::UFunction* callee)
    {
        if (probe.pawn_address != reinterpret_cast<std::uintptr_t>(context) ||
            !probe.pawn_address || !probe.die || probe.die != callee ||
            !Unreal::IsInGameThreadRaw() || !ready() ||
            probe.generation != bridge_generation.load(std::memory_order_acquire) ||
            Clock::now() >= probe.expires || iteration.OriginalFunctionCallPrevented())
        {
            return;
        }

        iteration.PreventOriginalFunctionCall();
        if (iteration.OriginalFunctionCallPrevented())
        {
            probe.intercepted = true;
            probe.pawn_address = 0;
            probe.die = nullptr;
        }
    }

    void intercept_script(Iteration& iteration, Unreal::UObject* context, Unreal::FFrame& frame, void*)
    {
        if (probe.pawn_address)
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
                    intercept_death(iteration, context, callee);
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

        const bool enabled = lab_enabled();
        std::array<Hook::GlobalCallbackId, 3> hooks{};
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
