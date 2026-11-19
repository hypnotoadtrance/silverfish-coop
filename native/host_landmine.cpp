#include "host_landmine.hpp"

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <cstdlib>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;

    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto controller_class = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_class = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto projectile_class = STR("/Game/Classes/Equipment/Equipment_Assets/Equimpents_Projectiles/LandMine_Projectile_BP.LandMine_Projectile_BP_C");
    constexpr auto destroyed_flags = static_cast<U::EObjectFlags>(U::RF_BeginDestroyed | U::RF_FinishDestroyed);
    constexpr auto default_flags = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject);

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), name) == 0 && size == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_HEADLESS") && env("SILVERFISH_COOP_NATIVE_FIXTURES") &&
               env("SILVERFISH_COOP_NATIVE_ZONE") && env("SILVERFISH_COOP_NATIVE_GUEST_SAVE");
    }

    template <typename T>
    T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <typename T>
    T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* value)
    {
        return value && !value->HasAnyFlags(default_flags) && !value->HasAnyFlags(destroyed_flags) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* path)
    {
        auto* type = find<U::UClass>(path);
        return type && live(value) && value->GetClassPrivate() == type;
    }

    bool authority(U::UObject* actor)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        auto* destroying = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
               destroying && !destroying->GetPropertyValueInContainer(actor);
    }

    Address address(U::UObject* object) { return reinterpret_cast<Address>(object); }

    struct Context
    {
        U::UWorld* world{};
        U::UObject* level{};
        U::UObject* pawn{};
        U::UObject* driver{};
        U::UObject* save{};
        U::UClass* projectile{};
    };

    Context qualify(Address requested_world, Address requested_pawn)
    {
        auto* world = find<U::UWorld>(world_path);
        if (!requested_world || !requested_pawn || !live(world) || address(world) != requested_world) return {};
        auto* level = object(world, STR("PersistentLevel"));
        auto* driver = object(world, STR("NetDriver"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* pawn = object(instance, STR("As Player FP"));
        auto* save = object(instance, STR("SaveSlot_Ref"));
        auto* controller = object(pawn, STR("Controller"));
        auto* local = object(controller, STR("Player"));
        auto* local_type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        if (!live(level) || !live(driver) || object(driver, STR("World")) != world ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
            !exact(instance, instance_class) || !exact(pawn, pawn_class) || address(pawn) != requested_pawn ||
            !exact(controller, controller_class) || !authority(controller) || !authority(pawn) ||
            controller->GetWorld() != world || pawn->GetWorld() != world || object(controller, STR("Pawn")) != pawn ||
            !live(local) || !local_type || !local->IsA(local_type) || object(local, STR("PlayerController")) != controller ||
            !exact(save, save_class) || object(pawn, STR("Save")) != save) return {};
        return {world, level, pawn, driver, save, find<U::UClass>(projectile_class)};
    }

    bool retiring(const U::FWeakObjectPtr& weak)
    {
        auto* actor = weak.Get(false);
        if (!actor || actor->HasAnyFlags(destroyed_flags) || actor->IsUnreachable()) return true;
        auto* destroying = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        return destroying && destroying->GetPropertyValueInContainer(actor);
    }

    struct Census
    {
        U::UObject* one{};
        int count{};
    };

    Census census(const Context& context)
    {
        Census seen;
        if (!context.projectile) return seen;  // A live instance cannot predate its exact UClass.
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (!candidate || candidate->GetClassPrivate() != context.projectile ||
                candidate->HasAnyFlags(default_flags) || retiring(U::FWeakObjectPtr{candidate}))
                return RC::LoopAction::Continue;
            auto* actor = static_cast<U::AActor*>(candidate);
            if (actor->GetWorld() == context.world || actor->GetLevel() == context.level)
            {
                seen.one = candidate;
                ++seen.count;
            }
            return seen.count < 2 ? RC::LoopAction::Continue : RC::LoopAction::Break;
        });
        return seen;
    }

    struct Watch
    {
        U::FWeakObjectPtr world, pawn, driver, save, projectile;
        Clock::time_point expires{};

        bool matches(const Context& context) const
        {
            return world.Get() == context.world && pawn.Get() == context.pawn && driver.Get() == context.driver && save.Get() == context.save;
        }
    };

    thread_local Watch current;

    int reply(const Lua& lua, int count, const char* state)
    {
        lua.set_integer(count);
        lua.set_string(state);
        return 2;
    }

    // The optional third argument binds the exact projectile immediately after the ordinary stock release.
    int observe(const Lua& lua)
    {
        const auto argc = lua.get_stack_size();
        if (!U::IsInGameThreadRaw() || (argc != 2 && argc != 3) || !lua.is_integer(1) || !lua.is_integer(2) ||
            (argc == 3 && !lua.is_integer(3))) return reply(lua, -1, "scope_unqualified");
        const auto world = lua.get_integer();
        const auto pawn = lua.get_integer();
        const auto requested = argc == 3 ? lua.get_integer() : 0;
        if (world <= 0 || pawn <= 0 || (argc == 3 && requested <= 0)) return reply(lua, -1, "scope_unqualified");
        const auto context = qualify(static_cast<Address>(world), static_cast<Address>(pawn));
        if (!context.world) return reply(lua, -1, "scope_unqualified");
        if (!current.matches(context)) current = {};
        const auto seen = census(context);

        if (argc == 3)
        {
            if (current.expires != Clock::time_point{} || seen.count != 1 || address(seen.one) != static_cast<Address>(requested) ||
                !authority(seen.one) || object(seen.one, STR("Instigator")) != context.pawn ||
                static_cast<U::AActor*>(seen.one)->GetLevel() != context.level)
                return reply(lua, seen.count, "bind_unqualified");
            current = {U::FWeakObjectPtr{context.world}, U::FWeakObjectPtr{context.pawn}, U::FWeakObjectPtr{context.driver},
                U::FWeakObjectPtr{context.save}, U::FWeakObjectPtr{seen.one}, Clock::now() + std::chrono::seconds(15)};
            return reply(lua, seen.count, "active");
        }
        if (current.expires == Clock::time_point{}) return reply(lua, seen.count, "unwatched");
        if (Clock::now() > current.expires) return reply(lua, seen.count, "watch_expired");
        if (retiring(current.projectile)) return reply(lua, seen.count, "retired");
        auto* exact_projectile = current.projectile.Get(false);
        if (!seen.count) return reply(lua, seen.count, "retirement_pending");
        if (seen.count != 1 || seen.one != exact_projectile) return reply(lua, seen.count, "unexpected_active");
        return reply(lua, seen.count, "active");
    }
}

void silverfish::native_host_landmine::register_lua(Lua& lua)
{
    if (enabled()) lua.register_function("SilverfishObserveNativeHostLandmineProjectile", observe);
}
