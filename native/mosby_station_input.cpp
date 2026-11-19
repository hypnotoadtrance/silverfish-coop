#include "mosby_station_input.hpp"
#include "interaction_input.hpp"

#include <array>
#include <chrono>
#include <cstdint>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;
    struct Profile
    {
        const U::TCHAR* world;
        const U::TCHAR* station;
    };
    constexpr std::array profiles{
        Profile{STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P:PersistentLevel.DataTape_DownloadStation_BP_C_1")},
        Profile{STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P:PersistentLevel.DataTape_DownloadStation_BP_C_1")},
    };
    constexpr auto station_class = STR("/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C");
    constexpr auto callback_path = STR("/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C:SendInteractionToActor");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto box_class = STR("/Script/Engine.BoxComponent");
    constexpr auto component_class = STR("/Script/Engine.SceneComponent");
    constexpr auto lease = std::chrono::milliseconds(950);
    constexpr auto receipt_lifetime = std::chrono::seconds(2);
    constexpr std::uint64_t max_sequence = (1ULL << 53) - 1;

    struct Scope
    {
        U::FWeakObjectPtr pawn{}, station{}, box{};
        Clock::time_point expires{}, receipt_expires{};
        std::uint64_t pending{};
        bool accepting{};
        const Profile* profile{};
    };
    Scope scope;
    std::uint64_t sequence{};

    template<class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* value = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    U::UObject* read(U::FObjectProperty* prop, const void* owner)
    {
        return prop && owner ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::UObject* stock_box(U::UObject* station, U::UObject* pawn, const Profile& profile)
    {
        if (!live(station) || station->GetClassPrivate() != find<U::UClass>(station_class) || station->GetPathName() != profile.station)
            return nullptr;
        auto* world = static_cast<U::AActor*>(station)->GetWorld();
        auto* destroying = field<U::FBoolProperty>(station, STR("bActorIsBeingDestroyed"));
        if (!live(world) || !pawn || static_cast<U::AActor*>(pawn)->GetWorld() != world || world->GetPathName() != profile.world ||
            !destroying || destroying->GetPropertyValueInContainer(station)) return nullptr;
        auto* type = find<U::UClass>(box_class);
        auto* property = field<U::FObjectProperty>(station, STR("Box"));
        if (!type || !property || property->GetPropertyClass().Get() != type) return nullptr;
        auto* box = read(property, station);
        return live(box) && box->GetClassPrivate() == type && box->GetOuterPrivate() == station ? box : nullptr;
    }

    U::FObjectProperty* parameter(U::UFunction* function, const U::TCHAR* name, int offset, const U::TCHAR* type)
    {
        auto* property = field<U::FObjectProperty>(function, name);
        return property && property->GetOffset_Internal() == offset && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_ReturnParm | U::CPF_OutParm) &&
            property->GetPropertyClass().Get() == find<U::UClass>(type) ? property : nullptr;
    }

    U::UObject* active_guest(Address address)
    {
        return silverfish::native_interaction_input::current_guest_pawn(address);
    }

    int set_scope(const Lua& lua)
    {
        const auto respond = [&](bool armed) { lua.set_bool(armed); return 1; };
        if (lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2)) return respond(false);
        const auto requested = lua.get_integer();
        const bool accepting = lua.get_bool();
        auto* pawn = requested > 0 ? active_guest(static_cast<Address>(requested)) : nullptr;
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        if (!live(world)) return respond(false);
        const Profile* selected{};
        for (const auto& profile : profiles)
            if (world->GetPathName() == profile.world) { selected = &profile; break; }
        auto* station = selected ? find<U::AActor>(selected->station) : nullptr;
        auto* box = station ? stock_box(station, pawn, *selected) : nullptr;
        if (!box) return respond(false);
        const auto now = Clock::now();
        if (scope.pawn.Get() != pawn || scope.station.Get() != station || scope.box.Get() != box ||
            !accepting || now >= scope.expires) scope.pending = 0;
        scope.pawn = pawn;
        scope.station = station;
        scope.box = box;
        scope.accepting = accepting;
        scope.profile = selected;
        scope.expires = now + lease;
        return respond(true);
    }

    int take_press(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto requested = lua.get_integer();
        auto* pawn = requested > 0 ? active_guest(static_cast<Address>(requested)) : nullptr;
        const auto now = Clock::now();
        if (!pawn || pawn != scope.pawn.Get() || !scope.accepting || now >= scope.expires ||
            now >= scope.receipt_expires || !scope.pending || !scope.box.Get()) return 0;
        lua.set_integer(static_cast<std::int64_t>(scope.pending));
        scope.pending = 0;
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::mosby_station_input::clear_scope();
        return 0;
    }
}

namespace silverfish::mosby_station_input
{
    void route(U::Hook::TCallbackIterationData<void>& call, U::UObject* actor, U::UFunction* function, const void* parameters)
    {
        const auto now = Clock::now();
        if (!actor || actor != scope.station.Get() || !function || !parameters || now >= scope.expires ||
            call.OriginalFunctionCallPrevented() || function != find<U::UFunction>(callback_path)) return;
        auto* scoped = scope.pawn.Get();
        auto* pawn = scoped ? active_guest(reinterpret_cast<Address>(scoped)) : nullptr;
        auto* box = pawn && scope.profile ? stock_box(actor, pawn, *scope.profile) : nullptr;
        if (!box || box != scope.box.Get() || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* player = parameter(function, STR("Player"), 0, pawn_class);
        auto* component = parameter(function, STR("HitComponent"), sizeof(void*), component_class);
        if (!player || !component || read(player, parameters) != pawn) return;
        // The host keeps the original stock trade. The guest's player-0 menu cannot authorize a campaign exchange.
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || !scope.accepting || read(component, parameters) != box ||
            read(field<U::FObjectProperty>(pawn, STR("InteractionTarget_Component")), pawn) != box ||
            sequence == max_sequence || !native_interaction_input::guest_interaction_in_reach(pawn, actor, box) ||
            !native_interaction_input::consume_current_press(pawn)) return;
        scope.pending = ++sequence;
        scope.receipt_expires = now + receipt_lifetime;
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeMosbyStationScope", set_scope);
        lua.register_function("SilverfishTakeNativeMosbyStationPress", take_press);
        lua.register_function("SilverfishClearNativeMosbyStationScope", clear);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) scope = {};
    }
}
