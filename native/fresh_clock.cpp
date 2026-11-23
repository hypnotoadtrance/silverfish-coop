#include "fresh_clock.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/FStrProperty.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    constexpr auto title = STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P");
    constexpr auto mode = STR("/Game/Classes/Player/GameMode_Menu.GameMode_Menu_C");
    constexpr auto controller_type = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto pawn_type = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto instance_type = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_type = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto local_type = STR("/Script/Engine.LocalPlayer");
    constexpr auto timespan_type = STR("/Script/CoreUObject.Timespan");
    constexpr auto track_type = STR("/Game/Classes/Weather/StormTrack.StormTrack");
    constexpr auto working_slot_prefix = STR("SFCoopLive_");
    constexpr auto guest_slot_prefix = STR("SFCoopGuest_");
    constexpr std::int64_t ticks_per_millisecond = 10'000;
    constexpr std::int64_t game_ticks = 1'107'000'000'000LL;
    constexpr std::int64_t storm_ticks = 2'268'000'000'000LL;

    bool opt_in(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        std::size_t bridge_size{};
        return getenv_s(&bridge_size, nullptr, 0, "SILVERFISH_COOP_BRIDGE") == 0 && bridge_size > 1 &&
            opt_in("SILVERFISH_COOP_NATIVE_ZONE") && opt_in("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    template <typename T>
    T* find(const U::TCHAR* path) { return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path); }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* value = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool is_false(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && !property->GetPropertyValueInContainer(owner);
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* object, const U::TCHAR* path)
    {
        return live(object) && object->GetClassPrivate() == find<U::UClass>(path);
    }

    bool authority(U::UObject* actor, U::UWorld* world)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            is_false(actor, STR("bActorIsBeingDestroyed")) && static_cast<U::AActor*>(actor)->GetWorld() == world;
    }

    bool stock_text(U::UObject* owner, const U::TCHAR* name, RC::StringViewType expected)
    {
        auto* property = field<U::FStrProperty>(owner, name);
        if (!property) return false;
        const auto& value = property->GetPropertyValueInContainer(owner);
        return value.Len() == static_cast<int>(expected.size()) && RC::StringViewType{*value} == expected;
    }

    bool reserved_slot(U::UObject* instance)
    {
        auto* property = field<U::FStrProperty>(instance, STR("CurrentSaveSlot"));
        if (!property) return false;
        const auto& value = property->GetPropertyValueInContainer(instance);
        if (value.Len() != 43) return false;
        const RC::StringViewType name{*value};
        return name.size() == 43 && name.starts_with(working_slot_prefix) &&
            std::ranges::all_of(name.substr(11), [](U::TCHAR letter) {
                return (letter >= '0' && letter <= '9') || (letter >= 'a' && letter <= 'f');
            });
    }

    struct Context
    {
        U::FWeakObjectPtr controller, world, instance, save, local_player;

        bool current_title() const
        {
            auto* c = controller.Get();
            auto* w = U::Cast<U::UWorld>(world.Get());
            auto* gi = instance.Get();
            auto* profile = save.Get();
            auto* local = local_player.Get();
            auto* player_class = find<U::UClass>(local_type);
            auto* game_mode = object(w, STR("AuthorityGameMode"));
            if (!live(w) || w->GetPathName() != title || !field<U::FObjectProperty>(w, STR("NetDriver")) ||
                object(w, STR("NetDriver")) || !exact(game_mode, mode) || !authority(game_mode, w) ||
                !exact(c, controller_type) || !authority(c, w) || exact(object(c, STR("Pawn")), pawn_type) ||
                !player_class || !live(local) || !local->IsA(player_class) || object(c, STR("Player")) != local ||
                object(local, STR("PlayerController")) != c || object(w, STR("OwningGameInstance")) != gi ||
                !exact(gi, instance_type) || exact(object(gi, STR("As Player FP")), pawn_type) ||
                !exact(profile, save_type) || object(gi, STR("SaveSlot_Ref")) != profile ||
                !profile->GetOuterPrivate() || profile->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient") ||
                !is_false(gi, STR("SaveAfterTransition"))) return false;
            auto* locals = field<U::FArrayProperty>(gi, STR("LocalPlayers"));
            auto* entry = locals ? U::CastField<U::FObjectProperty>(locals->GetInner()) : nullptr;
            if (!entry) return false;
            U::FScriptArrayHelper seats(locals, locals->ContainerPtrToValuePtr<void>(gi));
            return seats.Num() == 1 && entry->GetObjectPropertyValue(seats.GetRawPtr()) == local;
        }

        bool current() const
        {
            auto* gi = instance.Get();
            auto* profile = save.Get();
            return current_title() && reserved_slot(gi) && is_false(gi, STR("Respawning")) &&
                stock_text(profile, STR("Level"), STR("Observitory_P")) &&
                stock_text(profile, STR("RespawnLevel"), STR("Observitory_P")) &&
                stock_text(profile, STR("Entrance"), STR("MainMenu"));
        }

        bool bind(Address controller_address, Address save_address)
        {
            U::UObject* c{};
            U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
                if (reinterpret_cast<Address>(candidate) != controller_address) return RC::LoopAction::Continue;
                if (exact(candidate, controller_type)) c = candidate;
                return RC::LoopAction::Break;
            });
            if (!c) return false;
            auto* w = static_cast<U::AActor*>(c)->GetWorld();
            auto* gi = object(w, STR("OwningGameInstance"));
            auto* profile = object(gi, STR("SaveSlot_Ref"));
            if (!profile || reinterpret_cast<Address>(profile) != save_address) return false;
            controller = c;
            world = w;
            instance = gi;
            save = profile;
            local_player = object(c, STR("Player"));
            return current();
        }
    };

    Context guest_title(Address controller_address, Address save_address)
    {
        auto* world = find<U::UWorld>(title);
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* locals = field<U::FArrayProperty>(instance, STR("LocalPlayers"));
        auto* entry = locals ? U::CastField<U::FObjectProperty>(locals->GetInner()) : nullptr;
        if (!entry) return {};
        U::FScriptArrayHelper seats(locals, locals->ContainerPtrToValuePtr<void>(instance));
        if (seats.Num() != 1) return {};
        auto* local = entry->GetObjectPropertyValue(seats.GetRawPtr());
        auto* controller = object(local, STR("PlayerController"));
        auto* save = object(instance, STR("SaveSlot_Ref"));
        if (reinterpret_cast<Address>(controller) != controller_address ||
            reinterpret_cast<Address>(save) != save_address) return {};

        Context scope;
        scope.world = world;
        scope.instance = instance;
        scope.local_player = local;
        scope.controller = controller;
        scope.save = save;
        return scope;
    }

    struct TimespanArguments
    {
        std::int32_t days, hours, minutes, seconds, milliseconds;
        std::int64_t result;
    };

    struct Clock
    {
        U::FStructProperty* game{};
        U::FStructProperty* track{};
        U::FStructProperty* storm{};
        U::UObject* maths{};
        U::UFunction* make_timespan{};

        bool bind(U::UObject* profile)
        {
            auto* type = find<U::UScriptStruct>(timespan_type);
            auto* storm_type = find<U::UScriptStruct>(track_type);
            game = field<U::FStructProperty>(profile, STR("GameTime"));
            track = field<U::FStructProperty>(profile, STR("StormTrack"));
            storm = field<U::FStructProperty>(storm_type, STR("TimeZero_10_E5159B6049F6FE6711AEBFB70381881E"));
            if (!type || !storm_type || !game || !track || !storm || track->GetStruct().Get() != storm_type ||
                game->GetStruct().Get() != type || storm->GetStruct().Get() != type ||
                game->GetSize() != sizeof(std::int64_t) || storm->GetSize() != sizeof(std::int64_t) ||
                storm->GetOffset_Internal() < 0 || storm->GetOffset_Internal() + storm->GetSize() > track->GetSize() ||
                game->GetOffset_Internal() + game->GetSize() > track->GetOffset_Internal()) return false;

            maths = find<U::UObject>(STR("/Script/Engine.Default__KismetMathLibrary"));
            make_timespan = find<U::UFunction>(STR("/Script/Engine.KismetMathLibrary:MakeTimespan"));
            const auto flags = static_cast<U::EFunctionFlags>(U::FUNC_Native | U::FUNC_Static);
            if (!maths || !maths->HasAnyFlags(U::RF_ClassDefaultObject) || !make_timespan ||
                make_timespan->GetOuterPrivate() != maths->GetClassPrivate() || !make_timespan->HasAllFunctionFlags(flags) ||
                make_timespan->GetNumParms() != 6 || make_timespan->GetParmsSize() != sizeof(TimespanArguments)) return false;
            const auto* output = U::CastField<U::FStructProperty>(make_timespan->GetReturnProperty());
            if (!output || output->GetStruct().Get() != type || output->GetSize() != sizeof(std::int64_t) ||
                output->GetOffset_Internal() != offsetof(TimespanArguments, result)) return false;
            constexpr std::array names{STR("Days"), STR("Hours"), STR("Minutes"), STR("Seconds"), STR("Milliseconds")};
            for (std::size_t index = 0; index < names.size(); ++index)
            {
                auto* input = field<U::FIntProperty>(make_timespan, names[index]);
                if (!input || input->GetOffset_Internal() != static_cast<int>(index * sizeof(std::int32_t)) ||
                    !input->HasAnyPropertyFlags(U::CPF_Parm) || input->HasAnyPropertyFlags(U::CPF_OutParm | U::CPF_ReturnParm))
                    return false;
            }
            return true;
        }

        std::int64_t make(int days, int hours, int minutes) const
        {
            TimespanArguments arguments{days, hours, minutes, 0, 0, -1};
            maths->ProcessEvent(make_timespan, &arguments);
            return arguments.result;
        }

        void* game_address(U::UObject* profile) const { return game->ContainerPtrToValuePtr<void>(profile); }
        void* storm_address(U::UObject* profile) const
        {
            return storm->ContainerPtrToValuePtr<void>(track->ContainerPtrToValuePtr<void>(profile));
        }
    };

    std::int64_t ticks(const void* address)
    {
        std::int64_t value{};
        std::memcpy(&value, address, sizeof(value));
        return value;
    }

    int reply(const Lua& lua, std::string_view status)
    {
        lua.set_string(status);
        return 1;
    }

    int set_clock(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return reply(lua, "refused_scope");
        const auto controller = lua.get_integer();
        const auto profile = lua.get_integer();
        Context scope;
        if (controller <= 0 || profile <= 0 || !scope.bind(controller, profile)) return reply(lua, "refused_title_authority");
        auto* save = scope.save.Get();
        Clock clock;
        if (!clock.bind(save)) return reply(lua, "refused_timespan_schema");
        const auto next_game = clock.make(1, 6, 45);
        const auto next_storm = clock.make(2, 15, 0);
        if (next_game != game_ticks || next_storm != storm_ticks || !scope.current())
            return reply(lua, "refused_stock_timespan_values");

        auto* game = clock.game_address(save);
        auto* storm = clock.storm_address(save);
        const auto previous_game = ticks(game);
        const auto previous_storm = ticks(storm);
        clock.game->CopyCompleteValue(game, &next_game);
        clock.storm->CopyCompleteValue(storm, &next_storm);
        const auto applied_game = ticks(game);
        const auto applied_storm = ticks(storm);
        if (applied_game != next_game || applied_storm != next_storm || !scope.current())
        {
            if (scope.save.Get() != save) return reply(lua, "mutation_unverified_clock_scope");
            clock.game->CopyCompleteValue(game, &previous_game);
            clock.storm->CopyCompleteValue(storm, &previous_storm);
            return reply(lua, ticks(game) == previous_game && ticks(storm) == previous_storm && scope.current()
                ? "rolled_back_clock_readback" : "mutation_unverified_clock_readback");
        }
        reply(lua, "applied");
        lua.set_integer(previous_game);
        lua.set_integer(applied_game);
        lua.set_integer(previous_storm);
        lua.set_integer(applied_storm);
        return 5;
    }

    int set_guest_world(const Lua& lua)
    {
        if (!enabled() || !opt_in("SILVERFISH_COOP_EMBEDDED_ENGINE") || !U::IsInGameThreadRaw() ||
            lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_string(3) || !lua.is_integer(4) || !lua.is_integer(5))
            return reply(lua, "refused_scope");
        const auto controller = lua.get_integer();
        const auto profile = lua.get_integer();
        const auto id = lua.get_string();
        const auto seed = lua.get_integer();
        const auto milliseconds = lua.get_integer();
        if (controller <= 0 || profile <= 0 || id.size() != 32 ||
            !std::ranges::all_of(id, [](char ch) { return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'); }) ||
            id.find_first_not_of('0') == std::string::npos ||
            seed < (std::numeric_limits<std::int32_t>::min)() || seed > (std::numeric_limits<std::int32_t>::max)() ||
            milliseconds <= 0 || milliseconds > (std::numeric_limits<std::int64_t>::max)() / ticks_per_millisecond)
            return reply(lua, "refused_values");

        const auto scope = guest_title(controller, profile);
        auto* save = scope.save.Get();
        auto* instance = scope.instance.Get();
        const auto slot = RC::StringType(guest_slot_prefix) + RC::to_wstring(id);
        if (!scope.current_title() || !stock_text(instance, STR("CurrentSaveSlot"), slot))
            return reply(lua, "refused_private_guest_title");
        auto* game = field<U::FStructProperty>(save, STR("GameTime"));
        auto* seed_field = field<U::FIntProperty>(save, STR("InstanceSeed"));
        auto* step = field<U::FDoubleProperty>(instance, STR("Travel_TimeStep"));
        auto* type = find<U::UScriptStruct>(timespan_type);
        if (!type || !game || !seed_field || !step || game->GetStruct().Get() != type ||
            game->GetSize() != sizeof(std::int64_t))
            return reply(lua, "refused_world_schema");

        auto* address = game->ContainerPtrToValuePtr<void>(save);
        const auto prior_time = ticks(address);
        const auto prior_seed = seed_field->GetPropertyValueInContainer(save);
        const auto prior_step = step->GetPropertyValueInContainer(instance);
        if (!std::isfinite(prior_step)) return reply(lua, "refused_world_schema");
        const std::int64_t requested_time = milliseconds * ticks_per_millisecond;
        game->CopyCompleteValue(address, &requested_time);
        seed_field->SetPropertyValueInContainer(save, static_cast<std::int32_t>(seed));
        // The host's destination clock already includes the authored travel advance.
        step->SetPropertyValueInContainer(instance, 0);

        const auto applied_seed = seed_field->GetPropertyValueInContainer(save);
        const auto applied_time = ticks(address);
        if (applied_seed != seed || applied_time != requested_time || step->GetPropertyValueInContainer(instance) != 0 ||
            !scope.current_title() || !stock_text(instance, STR("CurrentSaveSlot"), slot))
        {
            if (scope.save.Get() != save || scope.instance.Get() != instance)
                return reply(lua, "mutation_unverified_guest_world_scope");
            game->CopyCompleteValue(address, &prior_time);
            seed_field->SetPropertyValueInContainer(save, prior_seed);
            step->SetPropertyValueInContainer(instance, prior_step);
            return reply(lua, ticks(address) == prior_time && seed_field->GetPropertyValueInContainer(save) == prior_seed &&
                step->GetPropertyValueInContainer(instance) == prior_step
                ? "rolled_back_guest_world_readback" : "mutation_unverified_guest_world_readback");
        }
        reply(lua, "applied");
        lua.set_integer(applied_seed);
        lua.set_integer(applied_time / ticks_per_millisecond);
        lua.set_number(prior_step);
        return 4;
    }
}

void silverfish::native_fresh_clock::register_lua(RC::LuaMadeSimple::Lua& lua)
{
    if (enabled()) lua.register_function("SilverfishSetNativeFreshClock", set_clock);
    if (enabled() && opt_in("SILVERFISH_COOP_EMBEDDED_ENGINE"))
        lua.register_function("SilverfishSetNativeGuestWorldStart", set_guest_world);
}
