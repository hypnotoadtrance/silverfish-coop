#include "mindcore_input.hpp"
#include "interaction_input.hpp"

#include <chrono>
#include <cstdint>

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
    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P");
    constexpr auto cradle_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.MindCore_Cradle_BP_C_1");
    constexpr auto cradle_class = STR("/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C");
    constexpr auto callback_path = STR("/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C:SendInteractionToActor");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto box_class = STR("/Script/Engine.BoxComponent");
    constexpr auto component_class = STR("/Script/Engine.SceneComponent");
    constexpr std::uint64_t limit = (1ULL << 53) - 1;

    struct Scope
    {
        U::FWeakObjectPtr pawn{}, cradle{}, box{};
        Clock::time_point expires{};
        bool accepting{};
        std::uint64_t pending{};
    };
    Scope scope;
    std::uint64_t sequence{};

    template<class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::FObjectProperty* property, const void* owner)
    {
        return property && owner ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool live(U::UObject* object)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return object && !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    U::UObject* exact_box(U::UObject* cradle, U::UObject* pawn)
    {
        if (!pawn || !live(cradle) || cradle->GetClassPrivate() != find<U::UClass>(cradle_class) ||
            cradle->GetPathName() != cradle_path) return nullptr;
        auto* actor = static_cast<U::AActor*>(cradle);
        auto* world = actor->GetWorld();
        auto* status = field<U::FIntProperty>(actor, STR("Status"));
        auto* destroying = field<U::FBoolProperty>(actor, STR("bActorIsBeingDestroyed"));
        if (!world || world != static_cast<U::AActor*>(pawn)->GetWorld() || world->GetPathName() != world_path ||
            actor->GetLevel() != object(field<U::FObjectProperty>(world, STR("PersistentLevel")), world) ||
            !destroying || destroying->GetPropertyValueInContainer(actor) || !status) return nullptr;
        // A joined client may still have the stock stage-zero actor. A live stage-two
        // branch must always pass through, even if its host readiness lease is stale.
        const int stage = status->GetPropertyValueInContainer(actor);
        if (stage != 0 && stage != 1) return nullptr;
        auto* property = field<U::FObjectProperty>(actor, STR("Button_Tape"));
        auto* box = object(property, actor);
        auto* type = find<U::UClass>(box_class);
        return live(box) && type && property->GetPropertyClass().Get() == type &&
            box->GetClassPrivate() == type && box->GetOuterPrivate() == actor ? box : nullptr;
    }

    U::FObjectProperty* parameter(U::UFunction* function, const U::TCHAR* name, int offset, const U::TCHAR* type)
    {
        auto* property = field<U::FObjectProperty>(function, name);
        return property && property->GetOffset_Internal() == offset && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            !property->HasAnyPropertyFlags(U::CPF_ReturnParm | U::CPF_OutParm) &&
            property->GetPropertyClass().Get() == find<U::UClass>(type) ? property : nullptr;
    }

    int set_scope(const Lua& lua)
    {
        const auto reply = [&](bool ok) { lua.set_bool(ok); return 1; };
        if (lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2)) return reply(false);
        const auto address = lua.get_integer();
        const bool accepting = lua.get_bool();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(static_cast<std::uintptr_t>(address)) : nullptr;
        auto* cradle = pawn ? find<U::UObject>(cradle_path) : nullptr;
        auto* box = exact_box(cradle, pawn);
        if (!box) { silverfish::mindcore_input::clear_scope(); return reply(false); }
        const auto now = Clock::now();
        if (!accepting || scope.pawn.Get() != pawn || scope.cradle.Get() != cradle || scope.box.Get() != box ||
            now >= scope.expires) scope.pending = 0;
        scope.pawn = pawn;
        scope.cradle = cradle;
        scope.box = box;
        scope.accepting = accepting;
        scope.expires = now + std::chrono::milliseconds(950);
        return reply(true);
    }

    int take_press(const Lua& lua)
    {
        if (lua.get_stack_size() != 1 || !lua.is_integer(1)) return 0;
        const auto address = lua.get_integer();
        auto* pawn = address > 0 ? silverfish::native_interaction_input::current_guest_pawn(static_cast<std::uintptr_t>(address)) : nullptr;
        if (!pawn || pawn != scope.pawn.Get() || !scope.pending || Clock::now() >= scope.expires ||
            !exact_box(scope.cradle.Get(), pawn)) return 0;
        lua.set_integer(static_cast<std::int64_t>(scope.pending));
        scope.pending = 0;
        return 1;
    }

    int clear(const Lua&)
    {
        silverfish::mindcore_input::clear_scope();
        return 0;
    }
}

namespace silverfish::mindcore_input
{
    void route(U::Hook::TCallbackIterationData<void>& call, U::UObject* actor, U::UFunction* function, const void* parameters)
    {
        if (!actor || actor != scope.cradle.Get() || !function || !parameters || Clock::now() >= scope.expires ||
            call.OriginalFunctionCallPrevented() || function != find<U::UFunction>(callback_path)) return;
        auto* bound = scope.pawn.Get();
        auto* pawn = bound ? native_interaction_input::current_guest_pawn(reinterpret_cast<std::uintptr_t>(bound)) : nullptr;
        auto* box = exact_box(actor, pawn);
        if (!box || box != scope.box.Get() || function->GetOuterPrivate() != actor->GetClassPrivate() ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* player = parameter(function, STR("Player"), 0, pawn_class);
        auto* component = parameter(function, STR("HitComponent"), sizeof(void*), component_class);
        if (object(player, parameters) != pawn || object(component, parameters) != box ||
            object(field<U::FObjectProperty>(pawn, STR("InteractionTarget_Component")), pawn) != box ||
            !native_interaction_input::guest_interaction_in_reach(pawn, actor, box) ||
            !native_interaction_input::consume_current_press(pawn)) return;
        call.PreventOriginalFunctionCall();
        if (call.OriginalFunctionCallPrevented() && scope.accepting && !scope.pending && sequence < limit)
            scope.pending = ++sequence;
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeMindCoreInput", set_scope);
        lua.register_function("SilverfishTakeNativeMindCorePress", take_press);
        lua.register_function("SilverfishClearNativeMindCoreInput", clear);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) scope = {};
    }
}
