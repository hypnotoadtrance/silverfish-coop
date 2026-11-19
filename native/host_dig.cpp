#include "host_dig.hpp"
#include "guest_primary_input.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
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
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Clock = std::chrono::steady_clock;
    using Point = std::array<double, 3>;

    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto source_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.BurriedItem_BP_C_1");
    constexpr auto source_class = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C");
    constexpr auto spade_class = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C");
    constexpr auto package_class = STR("/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C");
    constexpr auto hole_class = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C");
    constexpr auto dig_function = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C:DigForItem");
    constexpr auto spade_graph = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C:ExecuteUbergraph_MeleeWep_Spade");
    constexpr Point source_position{1665.2142939210705, 5811.305953931523, 339.15110679941967};

    // Stock weighted UnearthedItems classes. They are not counted as the quest package.
    constexpr std::array incidental_classes{
        STR("/Game/Classes/Items/Item_GoldNugget.Item_GoldNugget_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_TinCan.Prop_TinCan_C"),
        STR("/Game/Classes/Equipment/Eqp_Bolt.Eqp_Bolt_C"),
        STR("/Game/Classes/Equipment/Eqp_Lighter.Eqp_Lighter_C"),
        STR("/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C"),
        STR("/Game/Classes/Items/Item_GoldCoin.Item_GoldCoin_C"),
        STR("/Game/Classes/Equipment/Gren_PipeBomb.Gren_PipeBomb_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_SmallRock.Prop_SmallRock_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Bone.Prop_Bone_C"),
        STR("/Game/Assets/StaticMeshes/Mesh_Props/Prop_Skull.Prop_Skull_C"),
    };

    bool enabled()
    {
        for (const auto* name : {"SILVERFISH_COOP_HEADLESS", "SILVERFISH_COOP_NATIVE_FIXTURES", "SILVERFISH_COOP_NATIVE_ZONE",
                                 "SILVERFISH_COOP_NATIVE_GUEST_SAVE"})
        {
            std::array<char, 2> value{};
            std::size_t size{};
            if (getenv_s(&size, value.data(), value.size(), name) != 0 || size != value.size() || value[0] != '1')
                return false;
        }
        return true;
    }

    template<class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = field<U::FObjectProperty>(owner, name);
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    bool live(U::UObject* value)
    {
        constexpr auto disallowed = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(disallowed) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool authority(U::UObject* actor)
    {
        auto* role = field<U::FByteProperty>(actor, STR("Role"));
        return live(actor) && role && role->GetPropertyValueInContainer(actor) == 3 &&
            boolean(actor, STR("bActorIsBeingDestroyed"), false);
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    double squared_distance(const Point& a, const Point& b)
    {
        const auto x = a[0] - b[0], y = a[1] - b[1], z = a[2] - b[2];
        return x * x + y * y + z * z;
    }

    Point location(U::UObject* actor)
    {
        auto v = static_cast<U::AActor*>(actor)->K2_GetActorLocation();
        return {v.GetX(), v.GetY(), v.GetZ()};
    }

    struct Context
    {
        U::UWorld* world{};
        U::UObject* level{};
        U::UObject* pawn{};
        U::UObject* driver{};
        U::UObject* save{};
        U::UObject* spade{};
    };

    Context qualify(Address world_address, Address pawn_address)
    {
        auto* world = find<U::UWorld>(world_path);
        if (!live(world) || address(world) != world_address) return {};
        auto* level = object(world, STR("PersistentLevel"));
        auto* driver = object(world, STR("NetDriver"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* pawn = object(instance, STR("As Player FP"));
        auto* save = object(instance, STR("SaveSlot_Ref"));
        auto* controller = object(pawn, STR("Controller"));
        auto* local = object(controller, STR("Player"));
        auto* local_class = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        auto* spade = object(pawn, STR("EquipedItem"));
        if (!live(level) || !live(driver) || object(driver, STR("World")) != world ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
            !exact(instance, STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")) ||
            !exact(pawn, STR("/Game/Classes/Player/Player_FP.Player_FP_C")) || address(pawn) != pawn_address ||
            !exact(controller, STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")) ||
            !authority(controller) || !authority(pawn) || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || object(controller, STR("Pawn")) != pawn ||
            !live(local) || !local_class || !local->IsA(local_class) || object(local, STR("PlayerController")) != controller ||
            !exact(save, STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")) || object(pawn, STR("Save")) != save ||
            !boolean(pawn, STR("Dead"), false) || !boolean(controller, STR("Loading"), false) ||
            !exact(spade, spade_class) || !authority(spade) || static_cast<U::AActor*>(spade)->GetWorld() != world ||
            object(spade, STR("Owner")) != pawn || object(spade, STR("Instigator")) != pawn ||
            object(spade, STR("Player_Ref")) != pawn || object(spade, STR("User")) != pawn ||
            !boolean(spade, STR("HeldByPlayer"), true)) return {};
        return {world, level, pawn, driver, save, spade};
    }

    bool qualified_source(const Context& context, U::UObject* actor)
    {
        if (!exact(actor, source_class) || !authority(actor) || actor->GetPathName() != source_path ||
            !actor->HasAnyFlags(U::RF_WasLoaded) || static_cast<U::AActor*>(actor)->GetWorld() != context.world ||
            static_cast<U::AActor*>(actor)->GetLevel() != context.level ||
            !(squared_distance(location(actor), source_position) <= 4.0)) return false;
        auto* buried = field<U::FClassProperty>(actor, STR("BurriedActor"));
        auto* mode = field<U::FByteProperty>(actor, STR("Replenish_Mode"));
        auto* damage = field<U::FIntProperty>(actor, STR("PsyDmg"));
        auto* root = object(actor, STR("DefaultSceneRoot"));
        auto* sphere = object(actor, STR("Sphere"));
        auto* radius = field<U::FFloatProperty>(sphere, STR("SphereRadius"));
        auto* product = find<U::UClass>(package_class);
        return product && buried && buried->GetObjectPropertyValue(buried->ContainerPtrToValuePtr<void>(actor)) == product &&
            mode && mode->GetPropertyValueInContainer(actor) == 2 && damage && damage->GetPropertyValueInContainer(actor) == 0 &&
            exact(root, STR("/Script/Engine.SceneComponent")) && root == object(actor, STR("RootComponent")) &&
            root->GetOuterPrivate() == actor && exact(sphere, STR("/Script/Engine.SphereComponent")) &&
            sphere->GetOuterPrivate() == actor && object(sphere, STR("AttachParent")) == root &&
            radius && std::abs(radius->GetPropertyValueInContainer(sphere) - 25.0f) <= 0.01f;
    }

    struct Census
    {
        U::UObject* package{};
        int packages{}, holes{}, incidental{};
    };

    Census scene(const Context& context, const Point& impact)
    {
        auto* product = find<U::UClass>(package_class);
        auto* hole = find<U::UClass>(hole_class);
        std::array<U::UClass*, incidental_classes.size()> candidates{};
        for (std::size_t i = 0; i < candidates.size(); ++i) candidates[i] = find<U::UClass>(incidental_classes[i]);
        Census result;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (!live(candidate)) return RC::LoopAction::Continue;
            auto* klass = candidate->GetClassPrivate();
            bool incidental = false;
            for (const auto* allowed : candidates) incidental |= allowed && klass == allowed;
            if (klass != product && klass != hole && !incidental) return RC::LoopAction::Continue;
            auto* actor = static_cast<U::AActor*>(candidate);
            if (actor->GetWorld() != context.world || actor->GetLevel() != context.level || !authority(actor))
                return RC::LoopAction::Continue;
            if (klass == product)
            {
                result.package = candidate;
                ++result.packages;
            }
            else if (klass == hole && squared_distance(location(candidate), impact) <= 16.0)
                ++result.holes;
            else if (incidental && squared_distance(location(candidate), {impact[0], impact[1], impact[2] + 20.0}) <= 169.0)
                ++result.incidental;
            return RC::LoopAction::Continue;
        });
        return result;
    }

    struct Watch
    {
        U::FWeakObjectPtr world, level, pawn, driver, save, spade, source;
        Clock::time_point expires{};
        Point expected{}, impact{};
        int contacts{};
        bool pressed{};
        const char* fault{};
    };

    Watch watch;
    std::atomic<bool> installed{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;

    bool current(const Context& context)
    {
        return context.world && watch.world.Get() == context.world && watch.level.Get() == context.level &&
            watch.pawn.Get() == context.pawn && watch.driver.Get() == context.driver && watch.save.Get() == context.save &&
            watch.spade.Get() == context.spade && Clock::now() < watch.expires;
    }

    void observe_contact(Hook::TCallbackIterationData<void>& iteration, U::UObject* source, U::FFrame& frame, void*)
    {
        if (!U::IsInGameThreadRaw() || !watch.pressed || source != watch.source.Get() ||
            iteration.OriginalFunctionCallPrevented() || frame.Object() != source) return;
        auto* function = find<U::UFunction>(dig_function);
        if (!function || frame.Node() != function) return;
        const auto context = qualify(address(watch.world.Get()), address(watch.pawn.Get()));
        auto* input = field<U::FStructProperty>(function, STR("DigLocation"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        auto* caller = frame.PreviousFrame();
        if (watch.contacts || !current(context) || !qualified_source(context, source) ||
            function->GetOuterPrivate() != source->GetClassPrivate() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetNumParms() != 1 ||
            function->GetParmsSize() != 24 || function->GetReturnProperty() || !input || !vector ||
            input->GetStruct().Get() != vector || input->GetSize() != 24 || input->GetOffset_Internal() != 0 ||
            !frame.Locals() || !caller || caller->Object() != context.spade ||
            caller->Node() != find<U::UFunction>(spade_graph))
        {
            watch.fault = "mutation_unverified_stock_host_dig_ancestry";
            iteration.PreventOriginalFunctionCall();
            return;
        }
        const auto* axes = input->ContainerPtrToValuePtr<double>(frame.Locals());
        const Point hit{axes[0], axes[1], axes[2]};
        if (!(squared_distance(hit, watch.expected) <= 9.0 && squared_distance(hit, source_position) <= 45.1 * 45.1))
        {
            watch.fault = "mutation_unverified_stock_host_dig_landmark";
            iteration.PreventOriginalFunctionCall();
            return;
        }
        watch.impact = hit;
        ++watch.contacts;
    }

    int result(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    bool ready() { return installed.load(std::memory_order_acquire) && U::IsInGameThreadRaw(); }

    int press(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 5 || !lua.is_integer(1) || !lua.is_integer(2) ||
            !lua.is_number(3) || !lua.is_number(4) || !lua.is_number(5))
            return result(lua, false, "stock_host_dig_trace_arguments");
        const auto world = lua.get_integer();
        const auto pawn = lua.get_integer();
        const Point expected{lua.get_number(), lua.get_number(), lua.get_number()};
        const auto context = world > 0 && pawn > 0 ? qualify(static_cast<Address>(world), static_cast<Address>(pawn)) : Context{};
        auto* source = find<U::AActor>(source_path);
        if (!context.world || !(squared_distance(expected, source_position) <= 45.1 * 45.1) ||
            !qualified_source(context, source) || (watch.pressed && Clock::now() < watch.expires))
            return result(lua, false, "stock_host_dig_exact_source_unavailable");
        const auto baseline = scene(context, expected);
        if (baseline.packages || baseline.holes || baseline.incidental)
            return result(lua, false, "stock_host_dig_baseline_contaminated");
        watch = {context.world, context.level, context.pawn, context.driver, context.save, context.spade, source,
                 Clock::now() + std::chrono::seconds(4), expected, {}, 0, true, nullptr};
        if (!silverfish::native_guest_primary_input::dispatch_crossroads_spade_fixture(context.pawn, context.spade, true))
        {
            watch = {};
            return result(lua, false, "stock_host_dig_primary_unqualified");
        }
        return result(lua, true, "stock_pressed");
    }

    int inspect(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "stock_host_dig_inspect_arguments");
        const auto world = lua.get_integer();
        const auto pawn = lua.get_integer();
        const auto context = world > 0 && pawn > 0 ? qualify(static_cast<Address>(world), static_cast<Address>(pawn)) : Context{};
        if (!watch.pressed || !current(context)) return result(lua, false, "mutation_unverified_stock_host_dig_expired");
        if (watch.fault) return result(lua, false, watch.fault);
        if (watch.contacts == 0) return result(lua, true, "pending_contact");
        const auto observed = scene(context, watch.impact);
        if (!observed.packages || !observed.holes || authority(watch.source.Get()))
            return result(lua, true, "pending_spawn");
        auto* actor = observed.package;
        if (observed.packages != 1 || observed.holes != 1 || observed.incidental > 1 ||
            actor->HasAnyFlags(U::RF_WasLoaded) || object(actor, STR("Owner")) ||
            !(squared_distance(location(actor), {watch.impact[0], watch.impact[1], watch.impact[2] + 10.0}) <= 16.0))
            return result(lua, false, "mutation_unverified_stock_host_dig_spawn");
        if (!silverfish::native_guest_primary_input::dispatch_crossroads_spade_fixture(context.pawn, context.spade, false))
            return result(lua, false, "mutation_unverified_stock_host_dig_release");
        watch.pressed = false;
        result(lua, true, "observed");
        lua.set_integer(static_cast<std::int64_t>(address(actor)));
        for (const auto axis : watch.impact) lua.set_number(axis);
        lua.set_integer(observed.holes);
        lua.set_integer(observed.incidental);
        return 8;
    }
}

namespace silverfish::native_host_dig
{
    void install_hooks()
    {
        if (!enabled() || installed.load(std::memory_order_acquire)) return;
        callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(observe_contact,
            {false, false, STR("SilverfishCoopNative"), STR("Verify the stock CrossRoads buried quest contact")});
        installed.store(callback != Hook::ERROR_ID, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (callback != Hook::ERROR_ID)
        {
            Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
        clear_scope();
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) watch = {};
    }

    void register_lua(Lua& lua)
    {
        if (!enabled()) return;
        lua.register_function("SilverfishPressNativeHostDigFixture", press);
        lua.register_function("SilverfishInspectNativeHostDigFixture", inspect);
    }
}
