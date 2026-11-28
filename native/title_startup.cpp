#include "title_startup.hpp"
#include "local_bridge.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks/Hooks.hpp>
#include <Unreal/UEngine.hpp>
#include <Unreal/UObjectHashTables.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/World.hpp>

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>

namespace
{
    namespace U = RC::Unreal;
    namespace Hooks = U::Hook;
    namespace title = silverfish::native_title_entry;
    using Clock = std::chrono::steady_clock;

    constexpr auto mod_name = STR("SilverfishCoopNative");
    constexpr auto sample_period = std::chrono::milliseconds{250};
    constexpr auto freshness = std::chrono::milliseconds{500};
    constexpr std::size_t frame_limit = 8192;
    constexpr std::uint64_t sequence_limit = std::uint64_t{1} << 53;
    constexpr auto title_world = STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P");

    Hooks::GlobalCallbackId tick_hook = Hooks::ERROR_ID, travel_hook = Hooks::ERROR_ID;
    std::atomic<bool> installed{};
    Clock::time_point next_sample;
    std::mutex state_mutex;
    struct Publication
    {
        std::string frame;
        std::uint64_t sequence{}, generation{};
        Clock::time_point expires{};
    } publication;

    bool enabled(const wchar_t* name)
    {
        wchar_t value[2]{};
        return GetEnvironmentVariableW(name, value, 2) == 1 && value[0] == L'1';
    }

    bool qualified_boot()
    {
        if (!enabled(L"SILVERFISH_COOP_EMBEDDED_ENGINE") || !enabled(L"SILVERFISH_COOP_RESIDENT_TITLE") ||
            !enabled(L"SILVERFISH_COOP_INSTALLED_TITLE")) return false;
        std::array<wchar_t, 33> nonce{};
        if (GetEnvironmentVariableW(L"SILVERFISH_COOP_EMBEDDED_STARTUP", nonce.data(),
            static_cast<DWORD>(nonce.size())) != nonce.size() - 1) return false;
        const std::wstring_view token{nonce.data(), nonce.size() - 1};
        return token.find_first_not_of(L'0') != token.npos && std::ranges::all_of(token, [](wchar_t c) {
            return (c >= L'0' && c <= L'9') || (c >= L'a' && c <= L'f');
        });
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(
            U::RF_ClassDefaultObject | U::RF_ArchetypeObject | U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::UClass* loaded_class(U::FWeakObjectPtr& cache, const U::TCHAR* path)
    {
        if (auto* type = cache.Get()) return static_cast<U::UClass*>(type);
        auto* type = U::Cast<U::UClass>(U::FUObjectHashTables::StaticFindObjectByPath(nullptr, path, false, true));
        cache = type;
        return type;
    }

    template <typename Property>
    Property* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<Property>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    std::optional<title::Input> current_title(U::UEngine* engine)
    {
        if (!U::UnrealInitializer::ShouldUseHashTables() || !live(engine)) return {};
        static U::FWeakObjectPtr engine_class, viewport_class, world_class, instance_class, local_class;
        if (engine->GetClassPrivate() != loaded_class(engine_class, STR("/Script/Engine.GameEngine"))) return {};
        auto* viewport = object(engine, STR("GameViewport"));
        auto* instance = object(engine, STR("GameInstance"));
        if (!live(viewport) || !live(instance) ||
            viewport->GetClassPrivate() != loaded_class(viewport_class, STR("/Script/Engine.GameViewportClient")) ||
            instance->GetClassPrivate() != loaded_class(instance_class, STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")) ||
            viewport->GetOuterPrivate() != engine || instance->GetOuterPrivate() != engine ||
            object(viewport, STR("GameInstance")) != instance) return {};

        auto* world = object(viewport, STR("World"));
        if (!live(world) || world->GetClassPrivate() != loaded_class(world_class, STR("/Script/Engine.World")) ||
            world->GetPathName() != title_world || object(world, STR("OwningGameInstance")) != instance)
            return {};

        auto* players = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* player_field = players ? U::CastField<U::FObjectProperty>(players->GetInner()) : nullptr;
        if (!player_field || player_field->GetArrayDim() != 1) return {};
        U::FScriptArrayHelper locals(players, players->ContainerPtrToValuePtr<void>(instance));
        if (locals.Num() != 1) return {};
        auto* player = player_field->GetObjectPropertyValue(locals.GetRawPtr(0));
        const auto* type = loaded_class(local_class, STR("/Script/Engine.LocalPlayer"));
        if (!live(player) || !type || !player->IsA(type) || object(player, STR("ViewportClient")) != viewport)
            return {};
        auto* controller = object(player, STR("PlayerController"));
        if (!live(controller) || object(controller, STR("Player")) != player) return {};

        // The shared observer qualifies the controller, empty session, exact
        // menu ownership and the top, mounted and visible stock widget.
        return title::Input{static_cast<U::UWorld*>(world), controller, title::Phase::ready};
    }

    void retire()
    {
        std::lock_guard lock(state_mutex);
        ++publication.generation;
        publication.frame.clear();
    }

    void sample(U::UEngine* engine, bool idle)
    {
        if (idle || !U::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire)) return;
        const auto now = Clock::now();
        if (now < next_sample) return;
        next_sample = now + sample_period;
        std::uint64_t generation, sequence;
        {
            std::lock_guard lock(state_mutex);
            generation = publication.generation;
            sequence = publication.sequence + 1;
        }
        if (sequence >= sequence_limit) return;
        const auto input = current_title(engine);
        const auto rows = input ? title::rows(*input, sequence) : std::nullopt;
        std::string frame;
        if (rows && rows->startup)
        {
            frame.append("schema\t1\nseq\t").append(std::to_string(sequence));
            frame.append("\nsession\tidle\tready\t/Game/Classes/Maps/MainMenu_P\t0\t0\n");
            frame.append(rows->title).append("\n").append(*rows->startup).append("\n");
        }
        const auto completed = Clock::now();
        std::lock_guard lock(state_mutex);
        if (generation != publication.generation || !installed.load(std::memory_order_relaxed)) return;
        if (frame.empty() || frame.size() > frame_limit || completed - now >= sample_period)
        {
            publication.frame.clear();
            return;
        }
        publication.frame = std::move(frame);
        publication.sequence = sequence;
        publication.expires = now + freshness;
    }
}

extern "C" __declspec(dllexport) std::uint32_t SilverfishCopyNativeTitleStartupV1(
    std::uint64_t previous, char* buffer, std::uint32_t capacity, std::uint64_t* revision)
{
    if (!buffer || !revision) return 0;
    std::lock_guard lock(state_mutex);
    if (!installed.load(std::memory_order_acquire) || publication.frame.empty() ||
        previous == publication.sequence || Clock::now() >= publication.expires) return 0;
    const auto size = static_cast<std::uint32_t>(publication.frame.size());
    if (capacity >= size)
    {
        std::memcpy(buffer, publication.frame.data(), size);
        *revision = publication.sequence;
    }
    return size;
}

bool silverfish::native_title_startup::command_attachment_ready()
{
    if (!enabled(L"SILVERFISH_COOP_HEADLESS") || !enabled(L"SILVERFISH_COOP_TITLE_FIXTURES")) return false;
    std::lock_guard lock(state_mutex);
    return installed.load(std::memory_order_acquire) && !publication.frame.empty() && Clock::now() < publication.expires;
}

std::optional<title::Input> silverfish::native_title_startup::command_context(U::UEngine* engine)
{
    if (!U::IsInGameThreadRaw() || !installed.load(std::memory_order_acquire)) return {};
    const auto input = current_title(engine);
    const auto observation = input ? title::observe(*input) : std::nullopt;
    return observation && observation->ready() ? input : std::nullopt;
}

void silverfish::native_title_startup::install_hooks()
{
    if (installed.load(std::memory_order_relaxed) || !qualified_boot()) return;
    next_sample = {};
    travel_hook = Hooks::RegisterLoadMapPreCallback(
        [](auto&, U::UEngine*, U::FWorldContext&, U::FURL, U::UPendingNetGame*, U::FString&) {
            retire();
            local_bridge::retire_native_title();
        },
        {false, true, mod_name, STR("Retire native startup title before travel")});
    if (travel_hook == Hooks::ERROR_ID) return;
    tick_hook = Hooks::RegisterEngineTickPostCallback(
        [](auto&, U::UEngine* engine, float, bool idle) {
            if (!idle) local_bridge::tick_native_title(engine);
            sample(engine, idle);
        },
        {false, true, mod_name, STR("Publish stock title readiness before command attachment")});
    if (tick_hook == Hooks::ERROR_ID)
    {
        Hooks::UnregisterCallback(travel_hook);
        travel_hook = Hooks::ERROR_ID;
        return;
    }
    installed.store(true, std::memory_order_release);
}

void silverfish::native_title_startup::uninstall_hooks()
{
    installed.store(false, std::memory_order_release);
    retire();
    if (tick_hook != Hooks::ERROR_ID) Hooks::UnregisterCallback(tick_hook);
    if (travel_hook != Hooks::ERROR_ID) Hooks::UnregisterCallback(travel_hook);
    tick_hook = travel_hook = Hooks::ERROR_ID;
}
