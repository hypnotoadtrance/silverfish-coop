#include "host_dig.hpp"
#include "guest_primary_input.hpp"
#include "interaction_input.hpp"
#include "loot_escrow.hpp"
#include "stock_wallet.hpp"

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <utility>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Rotator.hpp>
#include <Unreal/UnrealCoreStructs.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace Hook = U::Hook;
    namespace stock_wallet = silverfish::stock_wallet;
    namespace loot_escrow = silverfish::loot_escrow;
    namespace native_guest_primary_input = silverfish::native_guest_primary_input;
    namespace native_interaction_input = silverfish::native_interaction_input;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Clock = std::chrono::steady_clock;
    using Point = std::array<double, 3>;
    using Iteration = Hook::TCallbackIterationData<void>;

    constexpr auto world_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P");
    constexpr auto source_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.BurriedItem_BP_C_1");
    constexpr auto source_class = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C");
    constexpr auto spade_class = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C");
    constexpr auto package_class = STR("/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C");
    constexpr auto hole_class = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C");
    constexpr auto dig_function = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C:DigForItem");
    constexpr auto spade_graph = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C:ExecuteUbergraph_MeleeWep_Spade");
    constexpr auto spade_primary = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C:PrimaryPress");
    constexpr auto melee_graph = STR("/Game/Classes/Weapons/MeleeWeapons/MeleeWep_Master_BP.MeleeWep_Master_BP_C:ExecuteUbergraph_MeleeWep_Master_BP");
    constexpr auto buried_flag = STR("BurriedItem_BP_C_1-CrossRoads_P-");
    constexpr auto item_interact = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto put_item = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:PutInInventory");
    constexpr auto pickup_item = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:PickUp_Object");
    constexpr auto add_item = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
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

    bool live_enabled()
    {
        for (const auto* name : {"SILVERFISH_COOP_NATIVE_HOST_DIG", "SILVERFISH_COOP_NATIVE_ZONE",
                                 "SILVERFISH_COOP_NATIVE_GUEST_SAVE"})
        {
            std::array<char, 2> value{};
            std::size_t size{};
            if (getenv_s(&size, value.data(), value.size(), name) != 0 || size != value.size() || value[0] != '1')
                return false;
        }
        return true;
    }

    bool live_fixture_enabled()
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return enabled() && getenv_s(&size, value.data(), value.size(), "SILVERFISH_COOP_NATIVE_HOST_DIG_LIVE_FIXTURES") == 0 &&
            size == value.size() && value[0] == '1';
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
        U::UObject* instance{};
    };

    Context qualify(Address world_address, Address pawn_address, bool require_spade = true, bool standing = true,
                    const char** reason = nullptr)
    {
        const auto unavailable = [reason](const char* detail) {
            if (reason) *reason = detail;
            return Context{};
        };
        auto* world = find<U::UWorld>(world_path);
        if (!live(world) || address(world) != world_address) return unavailable("stock_live_dig_native_world_unqualified");
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
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")))
            return unavailable("stock_live_dig_native_listen_unqualified");
        if (!exact(instance, STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C")) ||
            !exact(pawn, STR("/Game/Classes/Player/Player_FP.Player_FP_C")) || address(pawn) != pawn_address)
            return unavailable("stock_live_dig_native_pawn_unqualified");
        if (!exact(controller, STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")) ||
            !authority(controller) || !authority(pawn) || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || object(controller, STR("Pawn")) != pawn ||
            !live(local) || !local_class || !local->IsA(local_class) || object(local, STR("PlayerController")) != controller)
            return unavailable("stock_live_dig_native_local_controller_unqualified");
        if (!exact(save, STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C")) || object(pawn, STR("Save")) != save)
            return unavailable("stock_live_dig_native_save_unqualified");
        if ((standing && !boolean(pawn, STR("Dead"), false)) || !boolean(controller, STR("Loading"), false))
            return unavailable("stock_live_dig_native_host_not_ready");
        if (require_spade && (!exact(spade, spade_class) || !authority(spade) || static_cast<U::AActor*>(spade)->GetWorld() != world ||
            object(spade, STR("Owner")) != pawn || object(spade, STR("Instigator")) != pawn ||
            object(spade, STR("Player_Ref")) != pawn || object(spade, STR("User")) != pawn ||
            !boolean(spade, STR("HeldByPlayer"), true))) return unavailable("stock_live_dig_native_spade_unqualified");
        return {world, level, pawn, driver, save, spade, instance};
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

    enum class LiveState { Arming, Contact, Unclaimed, Escrowed, Collecting, Collected, Owned, Failed };

    struct LiveDig
    {
        U::FWeakObjectPtr world, pawn, driver, save, held, source, package;
        Clock::time_point lease{}, press_until{}, pickup_until{};
        LiveState state{LiveState::Arming}, before_pickup{LiveState::Unclaimed};
        Census before{};
        Point impact{};
        std::int64_t serial{};
        bool spade_called{}, inventory_called{};
        int wallet{}, artifact_value{}, incidental{};
        std::string spade_guid, package_name, package_guid, escrow_guid, material;
        std::wstring package_flag;
        std::string_view issue;
    } live_dig;

    std::int64_t live_serial{};
    bool ordinary{};
    std::array ordinary_callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool live_context(const Context& ctx)
    {
        return ctx.world && live_dig.world.Get() == ctx.world && live_dig.pawn.Get() == ctx.pawn &&
            live_dig.driver.Get() == ctx.driver && live_dig.save.Get() == ctx.save;
    }

    Context bound_live(bool require_spade = false, bool standing = false)
    {
        const auto ctx = qualify(address(live_dig.world.Get()), address(live_dig.pawn.Get()), require_spade, standing);
        return live_context(ctx) ? ctx : Context{};
    }

    void fail_live(std::string_view issue)
    {
        live_dig.issue = issue;
        live_dig.state = LiveState::Failed;
    }

    bool live_pending()
    {
        return live_dig.state != LiveState::Arming && live_dig.state != LiveState::Escrowed &&
            live_dig.state != LiveState::Owned;
    }

    bool valid_package(const Context& ctx, U::UObject* package)
    {
        return exact(package, package_class) && authority(package) && !package->HasAnyFlags(U::RF_WasLoaded) &&
            static_cast<U::AActor*>(package)->GetWorld() == ctx.world &&
            static_cast<U::AActor*>(package)->GetLevel() == ctx.level && !object(package, STR("Owner")) &&
            squared_distance(location(package), {live_dig.impact[0], live_dig.impact[1], live_dig.impact[2] + 10.0}) <= 16.0;
    }

    bool remember_package(U::UObject* package)
    {
        const auto path = package->GetPathName();
        const auto marker = path.find_last_of(STR("."));
        if (marker == std::wstring::npos) return false;
        const auto local = path.substr(marker + 1);
        constexpr std::wstring_view prefix = STR("Package_Item_FarmHouse_C_");
        if (!std::wstring_view{local}.starts_with(prefix) || local.size() == prefix.size() || local.size() > prefix.size() + 10)
            return false;
        for (const auto ch : std::wstring_view{local}.substr(prefix.size()))
            if (ch < L'0' || ch > L'9') return false;
        live_dig.package = package;
        live_dig.package_name = RC::to_utf8_string(package->GetFullName());
        live_dig.package_flag = local + STR("-CrossRoads_P-");
        return true;
    }

    std::string material_from_stock_frame(U::FFrame& caller)
    {
        auto* material = field<U::FObjectProperty>(caller.Node(), STR("CallFunc_BreakHitResult_PhysMat"));
        auto* blocked = field<U::FBoolProperty>(caller.Node(), STR("CallFunc_BreakHitResult_bBlockingHit"));
        if (!material || !blocked || !caller.Locals() || !blocked->GetPropertyValueInContainer(caller.Locals())) return {};
        auto* physical = material->GetObjectPropertyValue(material->ContainerPtrToValuePtr<void>(caller.Locals()));
        if (!exact(physical, STR("/Script/PhysicsCore.PhysicalMaterial"))) return {};
        constexpr std::array approved{
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_Dirt.PM_Dirt"),
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_Debris.PM_Debris"),
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_Grass.PM_Grass"),
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_MossyDirt.PM_MossyDirt"),
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_Mud.PM_Mud"),
            STR("/Game/Classes/Libraries/PhysicMaterials/PM_Sand.PM_Sand"),
        };
        const auto path = physical->GetPathName();
        for (const auto* expected : approved)
            if (path == expected) return RC::to_utf8_string(physical->GetFullName());
        return {};
    }

    void begin_live_contact(Iteration& call, U::UObject* source, U::FFrame& frame)
    {
        const auto now = Clock::now();
        if (!ordinary || watch.pressed || !source || frame.Object() != source ||
            !exact(source, source_class) || source->GetPathName() != source_path) return;
        auto* function = find<U::UFunction>(dig_function);
        if (!function || frame.Node() != function || function->GetOuterPrivate() != source->GetClassPrivate()) return;
        const auto ctx = bound_live();
        if (!ctx.world || source != live_dig.source.Get() || now >= live_dig.lease)
        {
            // An exact listen-host source can receive a stock hit before the
            // coordinator leases it. Preserve the undug checkpoint until it can.
            auto* world = static_cast<U::AActor*>(source)->GetWorld();
            auto* instance = object(world, STR("OwningGameInstance"));
            const auto host = qualify(address(world), address(object(instance, STR("As Player FP"))), false, false);
            if (host.world && qualified_source(host, source)) call.PreventOriginalFunctionCall();
            return;
        }
        if (!qualified_source(ctx, source)) return;
        auto* caller = frame.PreviousFrame();
        const auto standing = bound_live(true, true);
        // This exact stock source enters an authenticated campaign only via
        // the canonical listen host's actual, scoped Spade input.
        if (live_dig.state != LiveState::Arming || now >= live_dig.press_until ||
            !standing.world || standing.spade != live_dig.held.Get() || !live_dig.spade_called ||
            !caller || caller->Object() != standing.spade || caller->Node() != find<U::UFunction>(spade_graph))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        auto* input = field<U::FStructProperty>(function, STR("DigLocation"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        if (function->GetNumParms() != 1 ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetParmsSize() != 24 ||
            function->GetReturnProperty() || !input || !vector || input->GetStruct().Get() != vector ||
            input->GetSize() != 24 || input->GetOffset_Internal() != 0 || !frame.Locals())
        {
            fail_live("stock_live_dig_contact_abi_unqualified");
            call.PreventOriginalFunctionCall();
            return;
        }
        const auto* axes = input->ContainerPtrToValuePtr<double>(frame.Locals());
        const Point hit{axes[0], axes[1], axes[2]};
        if (!(squared_distance(hit, source_position) <= 45.1 * 45.1))
        {
            fail_live("stock_live_dig_contact_landmark_unverified");
            call.PreventOriginalFunctionCall();
            return;
        }
        stock_wallet::Wallet flags;
        std::string now_spade, owned;
        if (!flags.bind(ctx.instance) || flags.flag_value(ctx.instance, buried_flag).second ||
            !loot_escrow::stock_spade_guid(ctx.save, now_spade) || now_spade != live_dig.spade_guid ||
            !loot_escrow::sealed_package_guid(ctx.save, owned) || !owned.empty())
        {
            fail_live("stock_live_dig_contact_ownership_unverified");
            call.PreventOriginalFunctionCall();
            return;
        }
        live_dig.material = material_from_stock_frame(*caller);
        live_dig.before = scene(ctx, hit);
        live_dig.impact = hit;
        if (live_dig.serial == 0 || live_dig.material.empty() || live_dig.before.packages != 0)
        {
            fail_live("stock_contact_proof_unavailable");
            call.PreventOriginalFunctionCall();
            return;
        }
        live_dig.state = LiveState::Contact;
        live_dig.pickup_until = Clock::now() + std::chrono::seconds(3);
    }

    void settle_live_spawn(const Context& ctx)
    {
        if (live_dig.state != LiveState::Contact) return;
        stock_wallet::Wallet flags;
        const auto spawned = scene(ctx, live_dig.impact);
        std::string_view problem;
        if (!flags.bind(ctx.instance) || flags.flag_value(ctx.instance, buried_flag) != std::pair{1, true})
            problem = "stock_live_dig_spawn_flag_unverified";
        else if (authority(live_dig.source.Get()))
            problem = "stock_live_dig_source_retirement_unverified";
        else if (spawned.packages != 1 || !valid_package(ctx, spawned.package))
            problem = "stock_live_dig_exact_package_unverified";
        else if (spawned.holes != live_dig.before.holes + 1)
            problem = "stock_live_dig_hole_unverified";
        else if (spawned.incidental < live_dig.before.incidental || spawned.incidental > live_dig.before.incidental + 1)
            problem = "stock_live_dig_incidental_unverified";
        else if (!remember_package(spawned.package))
            problem = "stock_live_dig_package_identity_unverified";
        if (problem.empty())
        {
            live_dig.incidental = spawned.incidental - live_dig.before.incidental;
            live_dig.state = LiveState::Unclaimed;
        }
        else if (Clock::now() >= live_dig.pickup_until) fail_live(problem);
    }

    void observe_live_event(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!ordinary || !U::IsInGameThreadRaw() || !function || !receiver || !args || watch.pressed ||
            call.OriginalFunctionCallPrevented()) return;
        if (receiver == live_dig.pawn.Get() && live_dig.state == LiveState::Arming &&
            Clock::now() < live_dig.lease && Clock::now() >= live_dig.press_until &&
            native_guest_primary_input::matches_stock_spade_started(receiver, function, args))
        {
            const auto ctx = bound_live(true, true);
            auto* source = live_dig.source.Get();
            if (!ctx.world || !qualified_source(ctx, source) || squared_distance(location(receiver), source_position) > 450.0 * 450.0 ||
                live_serial >= (std::int64_t{1} << 53) - 1 || !loot_escrow::stock_spade_guid(ctx.save, live_dig.spade_guid)) return;
            live_dig.held = ctx.spade;
            live_dig.press_until = Clock::now() + std::chrono::milliseconds(1200);
            live_dig.serial = ++live_serial;
            live_dig.spade_called = false;
            live_dig.issue = {};
            return;
        }
        static const U::FName interact_name{STR("SendInteractionToActor")};
        static const U::FName put_name{STR("PutInInventory")};
        static const U::FName pickup_name{STR("PickUp_Object")};
        const auto name = function->GetFName();
        if (receiver == live_dig.package.Get() && live_dig.state != LiveState::Collecting &&
            ((name == put_name && function == find<U::UFunction>(put_item)) ||
             (name == pickup_name && function == find<U::UFunction>(pickup_item))) && bound_live().world)
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        if (name != interact_name || !exact(receiver, package_class) ||
            (live_dig.state != LiveState::Contact && live_dig.state != LiveState::Unclaimed &&
             live_dig.state != LiveState::Escrowed && live_dig.state != LiveState::Collecting)) return;
        const auto ctx = bound_live();
        if (!ctx.world) return;
        settle_live_spawn(ctx);
        if (receiver != live_dig.package.Get() || (live_dig.state != LiveState::Unclaimed &&
            live_dig.state != LiveState::Escrowed) || function != find<U::UFunction>(item_interact) ||
            function->GetNumParms() != 2 || function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net)) return;
        auto* player = field<U::FObjectProperty>(function, STR("Player"));
        auto* hit = field<U::FObjectProperty>(function, STR("HitComponent"));
        if (!player || !hit || player->GetOffset_Internal() != 0 || hit->GetOffset_Internal() != sizeof(void*)) return;
        auto* pawn = player->GetObjectPropertyValue(player->ContainerPtrToValuePtr<void>(args));
        auto* box = hit->GetObjectPropertyValue(hit->ContainerPtrToValuePtr<void>(args));
        if (pawn != ctx.pawn || !box || box != object(receiver, STR("Box")) ||
            object(pawn, STR("InteractionTarget_Component")) != box ||
            !native_interaction_input::has_current_host_press(pawn))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        stock_wallet::Wallet flags;
        std::string owned;
        if (!flags.bind(ctx.instance) || !loot_escrow::sealed_package_guid(ctx.save, owned) || !owned.empty() ||
            flags.flag_value(ctx.instance, live_dig.package_flag.c_str()).second)
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        live_dig.before_pickup = live_dig.state;
        live_dig.state = LiveState::Collecting;
        live_dig.pickup_until = Clock::now() + std::chrono::seconds(3);
        live_dig.wallet = flags.amount(ctx.instance);
        live_dig.artifact_value = flags.flag_value(ctx.instance, STR("Artifact_Value")).first;
        live_dig.inventory_called = false;
    }

    void observe_live_script(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!ordinary || !U::IsInGameThreadRaw() || call.OriginalFunctionCallPrevented() ||
            !frame.Node() || frame.Object() != receiver) return;
        observe_live_event(call, receiver, frame.Node(), frame.Locals());
        if (receiver == live_dig.held.Get() && live_dig.state == LiveState::Arming &&
            Clock::now() < live_dig.press_until && frame.Node() == find<U::UFunction>(spade_primary))
        {
            auto* caller = frame.PreviousFrame();
            if (caller && caller->Object() == receiver && caller->Node() && caller->Node() == find<U::UFunction>(melee_graph))
                live_dig.spade_called = true;
        }
        static const U::FName dig_name{STR("DigForItem")};
        if (frame.Node()->GetFName() == dig_name) begin_live_contact(call, receiver, frame);
        auto* package = live_dig.package.Get();
        if (!package || !frame.Locals()) return;
        static const U::FName add_name{STR("AddItemToInventory")};
        if (frame.Node()->GetFName() != add_name || frame.Node() != find<U::UFunction>(add_item)) return;
        const auto ctx = bound_live();
        auto* item = field<U::FObjectProperty>(frame.Node(), STR("Item"));
        if (!ctx.world || receiver != ctx.instance || !item ||
            item->GetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(frame.Locals())) != package) return;
        auto* caller = frame.PreviousFrame();
        auto* stack = field<U::FBoolProperty>(frame.Node(), STR("TryStack"));
        if (live_dig.state == LiveState::Collecting && !live_dig.inventory_called && caller &&
            caller->Object() == package && caller->Node() == find<U::UFunction>(put_item) && stack &&
            !stack->GetPropertyValueInContainer(frame.Locals()))
        {
            live_dig.inventory_called = true;
            return;
        }
        call.PreventOriginalFunctionCall();
    }

    void settle_live_pickup(const Context& ctx)
    {
        if (live_dig.state != LiveState::Collecting) return;
        std::string guid;
        stock_wallet::Wallet flags;
        if (!flags.bind(ctx.instance) || !loot_escrow::sealed_package_guid(ctx.save, guid))
        {
            fail_live("stock_pickup_canonical_readback_unavailable");
            return;
        }
        const auto flag = flags.flag_value(ctx.instance, live_dig.package_flag.c_str());
        const auto artifact = flags.flag_value(ctx.instance, STR("Artifact_Value")).first;
        if (live_dig.inventory_called && !guid.empty() && !live_dig.package.Get() && flag == std::pair{1, true} &&
            flags.flag_value(ctx.instance, buried_flag) == std::pair{1, true} && flags.amount(ctx.instance) == live_dig.wallet &&
            std::int64_t{artifact} == std::int64_t{live_dig.artifact_value} + 50)
        {
            live_dig.package_guid = std::move(guid);
            live_dig.state = LiveState::Collected;
        }
        else if (Clock::now() >= live_dig.pickup_until)
        {
            if (guid.empty() && !flag.second && valid_package(ctx, live_dig.package.Get()) &&
                flags.amount(ctx.instance) == live_dig.wallet && artifact == live_dig.artifact_value)
                live_dig.state = live_dig.before_pickup;
            else fail_live("stock_pickup_custody_unverified");
        }
    }

    Context read_live_context(const Lua& lua, const char** reason = nullptr)
    {
        if (!ordinary || !ready() || !lua.is_integer(1) || !lua.is_integer(2)) return {};
        const auto world = lua.get_integer();
        const auto pawn = lua.get_integer();
        return world > 0 && pawn > 0 ? qualify(static_cast<Address>(world), static_cast<Address>(pawn), false, false, reason) : Context{};
    }

    int dispatch_live_fixture(const Lua& lua)
    {
        if (!live_fixture_enabled() || lua.get_stack_size() != 3 || !lua.is_bool(3))
            return result(lua, false, "stock_live_dig_fixture_arguments");
        const auto ctx = read_live_context(lua);
        const auto down = lua.get_bool();
        if (!live_context(ctx) || watch.pressed ||
            (down && (live_dig.state != LiveState::Arming || Clock::now() >= live_dig.lease ||
                      Clock::now() < live_dig.press_until || !qualified_source(ctx, live_dig.source.Get()))) ||
            !native_guest_primary_input::dispatch_crossroads_spade_fixture(ctx.pawn, ctx.spade, down))
            return result(lua, false, "stock_live_dig_fixture_dispatch_unqualified");
        return result(lua, true, down ? "stock_started" : "stock_completed");
    }

    int renew_live(const Lua& lua)
    {
        if (lua.get_stack_size() != 2) return result(lua, false, "stock_live_dig_scope_arguments");
        const char* reason = "stock_live_dig_native_context_unavailable";
        const auto ctx = read_live_context(lua, &reason);
        if (!ctx.world) return result(lua, false, reason);
        if (live_pending() && !live_context(ctx)) return result(lua, false, "stock_live_dig_native_custody_pending");
        if (!live_context(ctx))
        {
            const bool same_scene = live_dig.world.Get() == ctx.world && live_dig.driver.Get() == ctx.driver &&
                live_dig.save.Get() == ctx.save;
            if (!same_scene)
            {
                live_dig = {};
                live_dig.world = ctx.world;
                live_dig.driver = ctx.driver;
                live_dig.save = ctx.save;
            }
            live_dig.pawn = ctx.pawn;
            live_dig.held.Reset();
            live_dig.press_until = {};
        }
        if (live_dig.state == LiveState::Escrowed && !live_dig.package.Get())
        {
            // A committed detached frame survives a stock same-map actor
            // retirement. Ask the coordinator to restore it in this scene.
            live_dig.state = LiveState::Arming;
            live_dig.spade_guid.clear();
            live_dig.material.clear();
            live_dig.package_name.clear();
            live_dig.escrow_guid.clear();
            live_dig.incidental = 0;
        }
        if (live_dig.state == LiveState::Arming)
        {
            auto* source = find<U::AActor>(source_path);
            if (qualified_source(ctx, source)) live_dig.source = source;
        }
        live_dig.lease = Clock::now() + std::chrono::seconds(2);
        return result(lua, true, "stock_live_dig_bound");
    }

    std::string_view live_state_name()
    {
        switch (live_dig.state)
        {
        case LiveState::Arming: return "ready";
        case LiveState::Contact: return "contact";
        case LiveState::Unclaimed: return "unclaimed";
        case LiveState::Escrowed: return "escrowed";
        case LiveState::Collecting: return "collecting";
        case LiveState::Collected: return "collected";
        case LiveState::Owned: return "owned";
        case LiveState::Failed: return "failed";
        }
        return "failed";
    }

    int read_live(const Lua& lua)
    {
        if (lua.get_stack_size() != 2) return 0;
        const auto ctx = read_live_context(lua);
        if (!live_context(ctx)) return 0;
        settle_live_spawn(ctx);
        settle_live_pickup(ctx);
        if (live_dig.state == LiveState::Unclaimed && !valid_package(ctx, live_dig.package.Get()))
            fail_live("stock_live_dig_unclaimed_package_changed");
        if (live_dig.state == LiveState::Escrowed && live_dig.package.Get() && !valid_package(ctx, live_dig.package.Get()))
            fail_live("stock_live_dig_escrow_actor_changed");
        if (live_dig.state == LiveState::Collected)
        {
            std::string current;
            stock_wallet::Wallet flags;
            if (!loot_escrow::sealed_package_guid(ctx.save, current) || current != live_dig.package_guid ||
                !flags.bind(ctx.instance) || flags.flag_value(ctx.instance, buried_flag) != std::pair{1, true} ||
                flags.flag_value(ctx.instance, live_dig.package_flag.c_str()) != std::pair{1, true})
                fail_live("stock_live_dig_collected_package_changed");
        }
        lua.set_string(live_state_name());
        lua.set_integer(live_dig.state == LiveState::Arming ? 0 : live_dig.serial);
        lua.set_string(live_dig.state == LiveState::Arming ? std::string_view{} : live_dig.spade_guid);
        lua.set_string(live_dig.package_name);
        lua.set_string(live_dig.package_guid);
        for (const auto axis : live_dig.impact) lua.set_number(axis);
        lua.set_string(live_dig.material);
        lua.set_integer(live_dig.incidental);
        lua.set_string(live_dig.escrow_guid);
        lua.set_string(live_dig.issue);
        return 12;
    }

    int capture_live_escrow(const Lua& lua)
    {
        if (lua.get_stack_size() != 4 || !lua.is_integer(3) || !lua.is_integer(4))
            return result(lua, false, "stock_live_dig_escrow_arguments");
        const auto ctx = read_live_context(lua);
        if (!live_context(ctx)) return result(lua, false, "stock_live_dig_owner_unqualified");
        const auto serial = lua.get_integer();
        const auto scratch = lua.get_integer();
        if (serial <= 0 || serial != live_dig.serial || scratch <= 0)
            return result(lua, false, "stock_live_dig_escrow_identity");
        settle_live_spawn(ctx);
        settle_live_pickup(ctx);
        if (live_dig.state == LiveState::Collected) return result(lua, true, "collected");
        auto* package = live_dig.package.Get();
        if (live_dig.state != LiveState::Unclaimed || !valid_package(ctx, package))
            return result(lua, false, "stock_live_dig_unclaimed_escrow_unavailable");
        std::string guid;
        if (!loot_escrow::capture_sealed_package(ctx.pawn, package, static_cast<Address>(scratch), guid))
            return result(lua, false, "stock_live_dig_escrow_conversion_unverified");
        live_dig.escrow_guid = guid;
        result(lua, true, "captured");
        lua.set_string(guid);
        return 3;
    }

    int verify_live_escrow(const Lua& lua)
    {
        if (lua.get_stack_size() != 5 || !lua.is_integer(3) || !lua.is_integer(4) || !lua.is_string(5))
            return result(lua, false, "stock_live_dig_escrow_readback_arguments");
        const auto ctx = read_live_context(lua);
        if (!live_context(ctx)) return result(lua, false, "stock_live_dig_owner_unqualified");
        const auto ticket = lua.get_integer();
        const auto scratch = lua.get_integer();
        const auto guid = lua.get_string();
        auto* package = live_dig.package.Get();
        return result(lua, ticket > 0 && ticket == live_dig.serial && scratch > 0 && guid == live_dig.escrow_guid &&
            live_dig.state == LiveState::Unclaimed && valid_package(ctx, package) &&
            loot_escrow::verify_sealed_package(static_cast<Address>(scratch), guid, ctx.pawn, package), "stock_live_dig_escrow_readback");
    }

    int acknowledge_live(const Lua& lua)
    {
        if (lua.get_stack_size() != 5 || !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5))
            return result(lua, false, "stock_live_dig_commit_arguments");
        const auto ctx = read_live_context(lua);
        if (!live_context(ctx)) return result(lua, false, "stock_live_dig_owner_unqualified");
        const auto ticket = lua.get_integer();
        const std::string state{lua.get_string()};
        const auto guid = lua.get_string();
        if (ticket <= 0 || ticket != live_dig.serial || guid.empty())
            return result(lua, false, "stock_live_dig_commit_identity");
        if (state == "escrowed" && guid == live_dig.escrow_guid &&
            (live_dig.state == LiveState::Unclaimed || live_dig.state == LiveState::Escrowed))
        {
            live_dig.state = LiveState::Escrowed;
            return result(lua, true, "escrowed");
        }
        if (state == "owned" && guid == live_dig.package_guid &&
            (live_dig.state == LiveState::Collected || live_dig.state == LiveState::Owned))
        {
            live_dig.state = LiveState::Owned;
            return result(lua, true, "owned");
        }
        return result(lua, false, "stock_live_dig_commit_changed");
    }

    bool disable_product_physics(U::UObject* package)
    {
        auto* box = object(package, STR("Box"));
        auto* fn = find<U::UFunction>(STR("/Script/Engine.PrimitiveComponent:SetSimulatePhysics"));
        auto* type = find<U::UClass>(STR("/Script/Engine.PrimitiveComponent"));
        auto* simulate = field<U::FBoolProperty>(fn, STR("bSimulate"));
        if (!exact(box, STR("/Script/Engine.BoxComponent")) || box->GetOuterPrivate() != package ||
            !fn || !type || fn->GetOuterPrivate() != type || !fn->HasAnyFunctionFlags(U::FUNC_Native) ||
            fn->GetNumParms() != 1 || fn->GetParmsSize() != 1 || !simulate || simulate->GetOffset_Internal() != 0) return false;
        unsigned char param{};
        simulate->SetPropertyValueInContainer(&param, false);
        box->ProcessEvent(fn, &param);
        return true;
    }

    int restore_live_escrow(const Lua& lua)
    {
        if (lua.get_stack_size() != 9 || !lua.is_integer(3) || !lua.is_string(4) || !lua.is_string(5) ||
            !lua.is_number(6) || !lua.is_number(7) || !lua.is_number(8) || !lua.is_string(9))
            return result(lua, false, "stock_live_dig_restore_arguments");
        const auto ctx = read_live_context(lua);
        if (!live_context(ctx)) return result(lua, false, "stock_live_dig_owner_unqualified");
        const auto scratch = lua.get_integer();
        const std::string guid{lua.get_string()};
        const std::string spade{lua.get_string()};
        const Point hit{lua.get_number(), lua.get_number(), lua.get_number()};
        const std::string material{lua.get_string()};
        if (live_dig.state == LiveState::Escrowed && live_dig.escrow_guid == guid && live_dig.spade_guid == spade &&
            live_dig.material == material && squared_distance(live_dig.impact, hit) <= .01 && valid_package(ctx, live_dig.package.Get()))
            return result(lua, true, "escrowed");
        stock_wallet::Wallet flags;
        std::string owned;
        if (live_dig.state != LiveState::Arming || scratch <= 0 || spade.size() != 32 || material.empty() ||
            !(squared_distance(hit, source_position) <= 45.1 * 45.1) ||
            !flags.bind(ctx.instance) || flags.flag_value(ctx.instance, buried_flag) != std::pair{1, true} ||
            authority(find<U::AActor>(source_path)) || scene(ctx, hit).packages != 0 ||
            !loot_escrow::sealed_package_guid(ctx.save, owned) || !owned.empty() ||
            !loot_escrow::verify_sealed_package(static_cast<Address>(scratch), guid) ||
            live_serial >= (std::int64_t{1} << 53) - 1)
            return result(lua, false, "stock_live_dig_escrow_restore_unqualified");

        live_dig.impact = hit;
        live_dig.spade_guid = spade;
        live_dig.material = material;
        live_dig.serial = ++live_serial;
        const U::FVector position{hit[0], hit[1], hit[2] + 10.0};
        const U::FRotator rotation{0.0, 0.0, 0.0};
        auto* package = ctx.world->SpawnActor(find<U::UClass>(package_class), &position, &rotation);
        if (!valid_package(ctx, package) || !disable_product_physics(package) ||
            !loot_escrow::verify_sealed_package(static_cast<Address>(scratch), guid, ctx.pawn, package) ||
            !remember_package(package))
        {
            fail_live("stock_live_dig_escrow_spawn_unverified");
            return result(lua, false, live_dig.issue);
        }
        live_dig.escrow_guid = guid;
        live_dig.state = LiveState::Escrowed;
        return result(lua, true, "escrowed");
    }
}

namespace silverfish::native_host_dig
{
    void install_hooks()
    {
        if (installed.load(std::memory_order_acquire)) return;
        const bool fixture = enabled();
        ordinary = live_enabled();
        if (fixture)
            callback = Hook::RegisterProcessLocalScriptFunctionPreCallback(observe_contact,
                {false, false, STR("SilverfishCoopNative"), STR("Verify the stock CrossRoads buried quest contact")});
        if (ordinary)
        {
            ordinary_callbacks[0] = Hook::RegisterProcessEventPreCallback(observe_live_event,
                {false, false, STR("SilverfishCoopNative"), STR("Observe exact stock host dig and pickup input")});
            ordinary_callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(observe_live_script,
                {false, false, STR("SilverfishCoopNative"), STR("Observe exact stock buried package custody")});
            if (ordinary_callbacks[0] == Hook::ERROR_ID || ordinary_callbacks[1] == Hook::ERROR_ID)
            {
                ordinary = false;
                for (auto& id : ordinary_callbacks)
                {
                    if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
                    id = Hook::ERROR_ID;
                }
            }
        }
        installed.store(callback != Hook::ERROR_ID || ordinary, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        installed.store(false, std::memory_order_release);
        if (callback != Hook::ERROR_ID)
        {
            Hook::UnregisterCallback(callback);
            callback = Hook::ERROR_ID;
        }
        ordinary = false;
        for (auto& id : ordinary_callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
        clear_scope();
    }

    void clear_scope()
    {
        if (!U::IsInGameThreadRaw()) return;
        watch = {};
        live_dig = {};
    }

    void register_lua(Lua& lua)
    {
        if (enabled())
        {
            lua.register_function("SilverfishPressNativeHostDigFixture", press);
            lua.register_function("SilverfishInspectNativeHostDigFixture", inspect);
        }
        if (ordinary)
        {
            lua.register_function("SilverfishRenewNativeHostDigScope", renew_live);
            lua.register_function("SilverfishReadNativeHostDigScope", read_live);
            lua.register_function("SilverfishCaptureNativeHostDigEscrow", capture_live_escrow);
            lua.register_function("SilverfishVerifyNativeHostDigEscrow", verify_live_escrow);
            lua.register_function("SilverfishAcknowledgeNativeHostDig", acknowledge_live);
            lua.register_function("SilverfishRestoreNativeHostDigEscrow", restore_live_escrow);
            if (live_fixture_enabled())
                lua.register_function("SilverfishDispatchNativeHostDigLiveFixture", dispatch_live_fixture);
        }
    }
}
