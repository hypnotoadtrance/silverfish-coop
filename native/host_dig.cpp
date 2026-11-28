#include "host_dig.hpp"
#include "guest_primary_input.hpp"
#include "guest_melee.hpp"
#include "guest_regen.hpp"
#include "interaction_input.hpp"
#include "loot_escrow.hpp"
#include "quest_rewards.hpp"
#include "stock_wallet.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <limits>
#include <memory>
#include <optional>
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
    constexpr auto black_market_world = STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01");
    constexpr auto pawn_class = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto save_class = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto source_path = STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.BurriedItem_BP_C_1");
    constexpr auto source_class = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C");
    constexpr auto spade_class = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C");
    constexpr auto package_class = STR("/Game/Classes/Items/Key_Items/Package_Item_FarmHouse.Package_Item_FarmHouse_C");
    constexpr auto hole_class = STR("/Game/Assets/StaticMeshes/Mesh_Misc/DigHole_BP.DigHole_BP_C");
    constexpr auto dig_function = STR("/Game/Classes/Actors/BurriedItem_BP.BurriedItem_BP_C:DigForItem");
    constexpr auto spade_graph = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C:ExecuteUbergraph_MeleeWep_Spade");
    constexpr auto spade_primary = STR("/Game/Classes/Equipment/MeleeWep_Spade.MeleeWep_Spade_C:PrimaryPress");
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
                    const char** reason = nullptr, const U::TCHAR* expected_map = world_path)
    {
        const auto unavailable = [reason](const char* detail) {
            if (reason) *reason = detail;
            return Context{};
        };
        auto* world = find<U::UWorld>(expected_map);
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
            !exact(pawn, pawn_class) || address(pawn) != pawn_address)
            return unavailable("stock_live_dig_native_pawn_unqualified");
        if (!exact(controller, STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C")) ||
            !authority(controller) || !authority(pawn) || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || object(controller, STR("Pawn")) != pawn ||
            !live(local) || !local_class || !local->IsA(local_class) || object(local, STR("PlayerController")) != controller)
            return unavailable("stock_live_dig_native_local_controller_unqualified");
        if (!exact(save, save_class) || object(pawn, STR("Save")) != save)
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
        U::FWeakObjectPtr world, pawn, driver, save, source, package, collector, collector_save;
        Clock::time_point lease{}, pickup_until{};
        LiveState state{LiveState::Arming}, before_pickup{LiveState::Unclaimed};
        Point impact{};
        std::int64_t serial{};
        bool inventory_called{}, dispatching_guest_pickup{};
        int wallet{}, artifact_value{}, incidental{};
        std::string spade_guid, package_name, package_guid, escrow_guid, material;
        std::wstring package_flag;
        std::string_view issue;
    } live_dig;

    std::int64_t live_serial{};
    bool ordinary{};
    std::array ordinary_callbacks{Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID, Hook::ERROR_ID};

    using silverfish::native_guest_melee::StockCameraPose;

    struct GuestDigAttempt
    {
        U::FWeakObjectPtr pawn, save, spade, camera;
        Clock::time_point expires{};
        U::FFrame* frame{};
        StockCameraPose relative;
        std::optional<StockCameraPose> aim;
        std::string guid;
    };

    std::array<GuestDigAttempt, 8> guest_digs;

    bool apply_dig_aim(U::UObject* camera, U::UObject* pawn, const std::optional<StockCameraPose>& aim,
                       StockCameraPose& previous)
    {
        return silverfish::native_guest_melee::apply_stock_camera_pose(
            camera, aim ? nullptr : object(pawn, STR("Controller")), aim, previous);
    }

    bool guest_dig_graph(U::UObject* receiver, U::FFrame& frame)
    {
        static const U::FName graph_name{STR("ExecuteUbergraph_MeleeWep_Spade")};
        return frame.Node() && frame.Node()->GetFName() == graph_name && frame.Object() == receiver &&
            U::IsInGameThreadRaw() && exact(receiver, spade_class) && frame.Node() == find<U::UFunction>(spade_graph);
    }

    void aim_guest_dig_frame(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (call.OriginalFunctionCallPrevented() || !guest_dig_graph(receiver, frame)) return;
        auto found = std::ranges::find_if(guest_digs, [receiver](const auto& attempt) { return attempt.spade.Get() == receiver; });
        if (found == guest_digs.end() || found->frame) return;
        auto* pawn = found->pawn.Get();
        auto* camera = found->camera.Get();
        if (Clock::now() >= found->expires || !authority(pawn) || !boolean(pawn, STR("Dead"), false) ||
            object(pawn, STR("Save")) != found->save.Get() || object(pawn, STR("EquipedItem")) != receiver ||
            object(pawn, STR("Camera")) != camera ||
            !apply_dig_aim(camera, pawn, found->aim, found->relative))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        // The stock graph resumes its soil trace after a latent delay. The
        // renderer normally aligns the camera only on the owning machine.
        found->frame = &frame;
    }

    void restore_guest_dig_frame(Iteration&, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!guest_dig_graph(receiver, frame)) return;
        for (auto& attempt : guest_digs)
        {
            if (attempt.frame != &frame || attempt.spade.Get() != receiver) continue;
            silverfish::native_guest_melee::restore_stock_camera_pose(attempt.camera.Get(), attempt.relative);
            attempt.frame = nullptr;
            return;
        }
    }

    int start_guest_dig(const Lua& lua)
    {
        const auto arguments = lua.get_stack_size();
        if (!ready() || !ordinary || (arguments != 3 && arguments != 6 && arguments != 9) || !lua.is_string(1) ||
            !lua.is_integer(2) || !lua.is_string(3))
            return result(lua, false, "stock_guest_dig_arguments");
        const std::string path{lua.get_string()};
        const auto save_address = lua.get_integer();
        const std::string expected{lua.get_string()};
        if (!path.starts_with("/Game/Classes/Maps/") || path.size() > 512 || save_address <= 0 || expected.size() != 32)
            return result(lua, false, "stock_guest_dig_arguments");
        std::optional<StockCameraPose> aim;
        if (arguments >= 6)
        {
            if (!lua.is_number(1) || !lua.is_number(2) || !lua.is_number(3))
                return result(lua, false, "stock_guest_dig_camera_unqualified");
            aim.emplace();
            aim->rotation = {lua.get_number(), lua.get_number(), lua.get_number()};
            if (!std::ranges::all_of(aim->rotation, [](double angle) { return std::isfinite(angle) && std::abs(angle) <= 360; }))
                return result(lua, false, "stock_guest_dig_camera_unqualified");
        }
        if (arguments == 9)
        {
            if (!lua.is_number(1) || !lua.is_number(2) || !lua.is_number(3))
                return result(lua, false, "stock_guest_dig_camera_unqualified");
            aim->location = Point{lua.get_number(), lua.get_number(), lua.get_number()};
            if (!std::ranges::all_of(*aim->location, [](double axis) { return std::isfinite(axis) && std::abs(axis) <= 100'000'000; }))
                return result(lua, false, "stock_guest_dig_camera_unqualified");
        }

        auto* pawn = find<U::AActor>(RC::to_wstring(path).c_str());
        if (!exact(pawn, pawn_class) || !authority(pawn) || !boolean(pawn, STR("Dead"), false) ||
            !boolean(pawn, STR("IsWeaponBlocked"), false) || !boolean(pawn, STR("UsingItem"), false) ||
            object(pawn, STR("HeldObject"))) return result(lua, false, "stock_guest_dig_pawn_unavailable");
        auto* world = pawn->GetWorld();
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        auto* save = object(pawn, STR("Save"));
        auto* canonical = object(instance, STR("SaveSlot_Ref"));
        if (!live(world) || !live(driver) || object(driver, STR("World")) != world ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")) ||
            address(save) != static_cast<Address>(save_address) ||
            !silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, canonical))
            return result(lua, false, "stock_guest_dig_owner_unqualified");

        auto* spade = object(pawn, STR("EquipedItem"));
        std::string guid;
        if (!exact(spade, spade_class) || !authority(spade) || static_cast<U::AActor*>(spade)->GetWorld() != world ||
            object(spade, STR("Owner")) != pawn || object(spade, STR("Instigator")) != pawn ||
            object(spade, STR("Player_Ref")) != pawn || object(spade, STR("User")) != pawn ||
            !boolean(spade, STR("HeldByPlayer"), true) || !loot_escrow::stock_spade_guid(save, guid) || guid != expected)
            return result(lua, false, "stock_guest_dig_equipment_changed");

        auto* primary = find<U::UFunction>(spade_primary);
        if (!primary || primary->GetOuterPrivate() != spade->GetClassPrivate() || primary->GetNumParms() != 0 ||
            primary->GetParmsSize() != 0 || primary->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net))
            return result(lua, false, "stock_guest_dig_primary_unavailable");
        const auto now = Clock::now();
        auto found = std::ranges::find_if(guest_digs, [spade](const auto& attempt) { return attempt.spade.Get() == spade; });
        if (found == guest_digs.end())
            found = std::ranges::find_if(guest_digs, [now](const auto& attempt) { return !attempt.frame && now >= attempt.expires; });
        if (found == guest_digs.end() || found->frame) return result(lua, false, "stock_guest_dig_busy");
        auto* camera = object(pawn, STR("Camera"));
        StockCameraPose relative;
        if (!exact(camera, STR("/Script/Engine.CameraComponent")) || camera->GetOuterPrivate() != pawn ||
            !apply_dig_aim(camera, pawn, aim, relative))
            return result(lua, false, "stock_guest_dig_camera_unavailable");
        *found = {pawn, save, spade, camera, now + std::chrono::seconds(2), nullptr, {}, aim, std::move(guid)};
        std::uint8_t args{};
        spade->ProcessEvent(primary, &args);
        silverfish::native_guest_melee::restore_stock_camera_pose(camera, relative);
        return result(lua, true, "stock_guest_dig_dispatched");
    }

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
            static_cast<U::AActor*>(package)->GetLevel() == ctx.level && !object(package, STR("Owner"));
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
        if (live_dig.state != LiveState::Arming || !caller || !guest_dig_graph(caller->Object(), *caller))
        {
            call.PreventOriginalFunctionCall();
            return;
        }
        const auto standing = bound_live(true, true);
        const bool host_contact = standing.world && standing.spade == caller->Object();
        auto* dig_save = ctx.save;
        std::string expected_spade;
        if (!host_contact)
        {
            const auto guest = std::ranges::find_if(guest_digs, [&](const auto& attempt) {
                return attempt.frame == caller && attempt.spade.Get() == caller->Object() && now < attempt.expires;
            });
            if (guest == guest_digs.end() ||
                !silverfish::native_guest_regen::qualifies_detached_guest(guest->pawn.Get(), guest->save.Get(), ctx.save))
            {
                call.PreventOriginalFunctionCall();
                return;
            }
            dig_save = guest->save.Get();
            expected_spade = guest->guid;
        }
        auto* input = field<U::FStructProperty>(function, STR("DigLocation"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        if (function->GetNumParms() != 1 ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || function->GetParmsSize() != 24 ||
            function->GetReturnProperty() || !input || !vector || input->GetStruct().Get() != vector ||
            input->GetSize() != 24 || input->GetOffset_Internal() != 0 || !frame.Locals())
        {
            live_dig.issue = "stock_live_dig_contact_abi_unqualified";
            call.PreventOriginalFunctionCall();
            return;
        }
        const auto* axes = input->ContainerPtrToValuePtr<double>(frame.Locals());
        const Point hit{axes[0], axes[1], axes[2]};
        if (!(squared_distance(hit, source_position) <= 45.1 * 45.1))
        {
            live_dig.issue = "stock_live_dig_contact_landmark_unverified";
            call.PreventOriginalFunctionCall();
            return;
        }
        stock_wallet::Wallet flags;
        std::string now_spade, owned;
        if (live_serial >= (std::int64_t{1} << 53) - 1 || !flags.bind(ctx.instance) ||
            flags.flag_value(ctx.instance, buried_flag).second || !loot_escrow::stock_spade_guid(dig_save, now_spade) ||
            (!host_contact && now_spade != expected_spade) ||
            !loot_escrow::sealed_package_guid(ctx.save, owned) || !owned.empty())
        {
            live_dig.issue = "stock_live_dig_contact_ownership_unverified";
            call.PreventOriginalFunctionCall();
            return;
        }
        live_dig.material = material_from_stock_frame(*caller);
        live_dig.impact = hit;
        if (live_dig.material.empty())
        {
            live_dig.issue = "stock_contact_proof_unavailable";
            call.PreventOriginalFunctionCall();
            return;
        }
        live_dig.serial = ++live_serial;
        live_dig.spade_guid = std::move(now_spade);
        live_dig.issue = {};
        live_dig.state = LiveState::Contact;
        live_dig.pickup_until = now + std::chrono::seconds(3);
    }

    void settle_live_spawn(const Context& ctx)
    {
        if (live_dig.state != LiveState::Contact) return;
        stock_wallet::Wallet flags;
        auto* package = live_dig.package.Get();
        std::string_view problem;
        if (!flags.bind(ctx.instance) || flags.flag_value(ctx.instance, buried_flag) != std::pair{1, true})
            problem = "stock_live_dig_spawn_flag_unverified";
        else if (authority(live_dig.source.Get()))
            problem = "stock_live_dig_source_retirement_unverified";
        else if (!exact(package, package_class) || !authority(package) ||
                 static_cast<U::AActor*>(package)->GetWorld() != ctx.world ||
                 static_cast<U::AActor*>(package)->GetLevel() != ctx.level)
            problem = "stock_live_dig_exact_package_unverified";
        if (problem.empty()) live_dig.state = LiveState::Unclaimed;
        else if (Clock::now() >= live_dig.pickup_until) fail_live(problem);
    }

    void sync_hole_begin_play(Iteration& call, U::UObject* receiver, U::UFunction* function)
    {
        static const U::FName begin_play{STR("ReceiveBeginPlay")};
        static const U::FName hole_name{STR("DigHole_BP_C")};
        if (function->GetFName() != begin_play) return;
        auto* type = receiver->GetClassPrivate();
        if (!type || type->GetFName() != hole_name || type != function->GetOuterPrivate() ||
            !exact(receiver, hole_class) || function->GetNumParms() != 0) return;
        auto* actor = static_cast<U::AActor*>(receiver);
        auto* world = actor->GetWorld();
        auto* driver = object(world, STR("NetDriver"));
        if (!live(driver) || object(driver, STR("World")) != world || !field<U::FObjectProperty>(driver, STR("ServerConnection")))
            return;
        auto* connection = object(driver, STR("ServerConnection"));
        if (connection)
        {
            auto* role = field<U::FByteProperty>(actor, STR("Role"));
            // The stock begin event runs the weighted loot roll. A network replica only shows the hole.
            if (live(connection) && object(connection, STR("Driver")) == driver &&
                role && role->GetPropertyValueInContainer(actor) == 1)
                call.PreventOriginalFunctionCall();
            return;
        }
        if (!authority(actor) || boolean(actor, STR("bReplicates"), true)) return;
        auto* set = find<U::UFunction>(STR("/Script/Engine.Actor:SetReplicates"));
        auto* enabled = field<U::FBoolProperty>(set, STR("bInReplicates"));
        if (!set || !enabled || set->GetNumParms() != 1 || set->GetParmsSize() != 1 ||
            enabled->GetOffset_Internal() != 0 || !set->HasAnyFunctionFlags(U::FUNC_Native)) return;
        std::uint8_t args{};
        enabled->SetPropertyValueInContainer(&args, true);
        actor->ProcessEvent(set, &args);
    }

    void observe_live_event(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!ordinary || !U::IsInGameThreadRaw() || !function || !receiver ||
            call.OriginalFunctionCallPrevented()) return;
        sync_hole_begin_play(call, receiver, function);
        if (!args || watch.pressed || call.OriginalFunctionCallPrevented()) return;
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
        const bool guest_input = live_dig.dispatching_guest_pickup && pawn == live_dig.collector.Get();
        const bool host_input = pawn == ctx.pawn && object(pawn, STR("InteractionTarget_Component")) == box &&
            native_interaction_input::has_current_host_press(pawn);
        if (!box || box != object(receiver, STR("Box")) || (!host_input && !guest_input))
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
        aim_guest_dig_frame(call, receiver, frame, nullptr);
        observe_live_event(call, receiver, frame.Node(), frame.Locals());
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

    std::string_view transfer_package_row(const Context& host, U::UObject* guest, U::UObject* detached,
                                          std::string_view identity, bool require_quest);

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
        const auto buried = flags.flag_value(ctx.instance, buried_flag);
        const auto artifact = flags.flag_value(ctx.instance, STR("Artifact_Value")).first;
        const auto wallet = flags.amount(ctx.instance);
        auto* package = live_dig.package.Get();
        // The retired stock actor can remain in the weak object table until GC.
        // Require loss of live authority alongside the stock inventory and save proofs.
        const bool retired = !authority(package);
        if (live_dig.inventory_called && !guid.empty() && retired && flag == std::pair{1, true} &&
            buried == std::pair{1, true} && wallet == live_dig.wallet &&
            std::int64_t{artifact} == std::int64_t{live_dig.artifact_value} + 50)
        {
            if (auto* collector = live_dig.collector.Get())
            {
                if (transfer_package_row(ctx, collector, live_dig.collector_save.Get(), guid, false) != "transferred")
                {
                    fail_live("stock_guest_package_transfer_unverified");
                    return;
                }
            }
            else if (live_dig.collector_save.Get())
            {
                fail_live("stock_guest_package_recipient_changed");
                return;
            }
            live_dig.package_guid = std::move(guid);
            live_dig.state = LiveState::Collected;
        }
        else if (Clock::now() >= live_dig.pickup_until)
        {
            if (guid.empty() && !flag.second && valid_package(ctx, package) &&
                buried == std::pair{1, true} && wallet == live_dig.wallet && artifact == live_dig.artifact_value)
            {
                live_dig.state = live_dig.before_pickup;
                live_dig.collector.Reset();
                live_dig.collector_save.Reset();
            }
            else if (!live_dig.inventory_called) fail_live("stock_pickup_inventory_callback_unverified");
            else if (guid.empty()) fail_live("stock_pickup_canonical_package_missing");
            else if (!retired) fail_live("stock_pickup_actor_not_retired");
            else if (flag != std::pair{1, true}) fail_live("stock_pickup_actor_permanent_flag_unverified");
            else if (buried != std::pair{1, true}) fail_live("stock_pickup_buried_permanent_flag_unverified");
            else if (wallet != live_dig.wallet) fail_live("stock_pickup_wallet_changed");
            else fail_live("stock_pickup_artifact_value_unverified");
        }
    }

    constexpr int handoff_inventory_limit = 4096;

    struct HandoffInventory
    {
        U::UClass* save = find<U::UClass>(save_class);
        U::UClass* package = find<U::UClass>(package_class);
        U::UScriptStruct* item = find<U::UScriptStruct>(STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct"));
        U::FArrayProperty* backpack = field<U::FArrayProperty>(save, STR("PlayerBackpack"));
        U::FArrayProperty* equipment = field<U::FArrayProperty>(save, STR("PlayerEquipment"));
        U::FStructProperty* armor = field<U::FStructProperty>(save, STR("PlayerArmor"));
        U::FStructProperty* helmet = field<U::FStructProperty>(save, STR("PlayerHelmet"));
        U::FMapProperty* flags = field<U::FMapProperty>(save, STR("Flags"));
        U::FStructProperty* row = backpack ? U::CastField<U::FStructProperty>(backpack->GetInner()) : nullptr;
        U::FClassProperty* klass = field<U::FClassProperty>(item, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FStructProperty* guid = field<U::FStructProperty>(item, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        U::FStructProperty* data = field<U::FStructProperty>(item, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        U::FBoolProperty* never_drop = field<U::FBoolProperty>(item, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));

        bool valid() const
        {
            auto* base = find<U::UClass>(STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"));
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            auto* payload = find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
            const auto is_row = [&](U::FProperty* property) {
                auto* typed = U::CastField<U::FStructProperty>(property);
                return typed && typed->GetArrayDim() == 1 && typed->GetStruct().Get() == item;
            };
            return save && item && package && base && package->IsChildOf(base) && equipment && flags && is_row(row) &&
                is_row(equipment->GetInner()) && is_row(armor) && is_row(helmet) && klass && klass->GetMetaClass().Get() == base &&
                guid && guid_type && guid->GetStruct().Get() == guid_type && guid->GetSize() == 16 &&
                data && payload && data->GetStruct().Get() == payload && never_drop;
        }

        bool is_package(const void* entry) const
        {
            return klass->GetObjectPropertyValue(klass->ContainerPtrToValuePtr<void>(entry)) == package;
        }

        bool same_row(const void* before, const void* after) const
        {
            return row->Identical(before, after) && data->Identical(data->ContainerPtrToValuePtr<void>(before),
                data->ContainerPtrToValuePtr<void>(after));
        }

        bool visit(U::UObject* owner, const auto& visitor) const
        {
            for (auto* property : {backpack, equipment})
            {
                U::FScriptArrayHelper rows(property, property->ContainerPtrToValuePtr<void>(owner));
                if (rows.Num() < 0 || rows.Num() > handoff_inventory_limit) return false;
                for (int i = 0; i < rows.Num(); ++i) visitor(rows.GetRawPtr(i));
            }
            for (auto* property : {armor, helmet}) visitor(property->ContainerPtrToValuePtr<void>(owner));
            return true;
        }

        bool pickup_flags(U::UObject* owner) const
        {
            auto* key = U::CastField<U::FNameProperty>(flags->GetKeyProp());
            auto* value = U::CastField<U::FIntProperty>(flags->GetValueProp());
            if (!key || key->GetOffset_Internal() != 0 || !value) return false;
            const auto layout = U::FScriptMap::GetScriptLayout(key->GetSize(), key->GetMinAlignment(),
                                                               value->GetSize(), value->GetMinAlignment());
            if (value->GetOffset_Internal() != layout.ValueOffset) return false;
            const auto* map = flags->ContainerPtrToValuePtr<U::FScriptMap>(owner);
            if (map->Num() < 2 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > 65536) return false;
            const U::FName buried{buried_flag, U::FNAME_Find};
            if (buried.IsNone()) return false;
            constexpr std::wstring_view prefix = STR("Package_Item_FarmHouse_C_");
            constexpr std::wstring_view suffix = STR("-CrossRoads_P-");
            bool source_saved = false;
            int pickups = 0;
            for (int i = 0; i < map->GetMaxIndex(); ++i)
            {
                if (!map->IsValidIndex(i)) continue;
                const auto* entry = map->GetData(i, layout);
                if (!entry) return false;
                const auto name = key->GetPropertyValue(entry);
                const int state = value->GetPropertyValueInContainer(entry);
                if (name == buried) source_saved = state == 1;
                const auto text = name.ToString();
                const std::wstring_view path{text};
                if (state != 1 || !path.starts_with(prefix) || !path.ends_with(suffix) ||
                    path.size() <= prefix.size() + suffix.size() || path.size() > prefix.size() + suffix.size() + 10) continue;
                const auto ordinal = path.substr(prefix.size(), path.size() - prefix.size() - suffix.size());
                if (std::ranges::all_of(ordinal, [](auto ch) { return ch >= L'0' && ch <= L'9'; })) ++pickups;
            }
            return source_saved && pickups == 1;
        }
    };

    struct HandoffValue
    {
        struct Destroy { U::FProperty* property{}; void operator()(void* value) const { property->DestroyAndFreeValue(value); } };
        std::unique_ptr<void, Destroy> value;

        bool capture(U::FProperty* property, const void* source)
        {
            value = decltype(value){property->AllocateAndInitializeValue(), Destroy{property}};
            if (!value) return false;
            property->CopyCompleteValue(value.get(), source);
            return matches(source);
        }

        bool matches(const void* current) const { return value.get_deleter().property->Identical(value.get(), current); }
    };

    bool handoff_alive(U::UObject* pawn, U::UObject* save)
    {
        auto* health = field<U::FDoubleProperty>(save, STR("PlayerHealth"));
        if (!health || !boolean(pawn, STR("Dead"), false)) return false;
        const auto hp = health->GetPropertyValueInContainer(save);
        return std::isfinite(hp) && hp > 0.0;
    }

    bool handoff_in_reach(U::UObject* host, U::UObject* guest)
    {
        auto* actor = find<U::UClass>(STR("/Script/Engine.Actor"));
        auto* scene = find<U::UClass>(STR("/Script/Engine.SceneComponent"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        auto* function = find<U::UFunction>(STR("/Script/Engine.Actor:K2_GetActorLocation"));
        auto* output = function ? U::CastField<U::FStructProperty>(function->GetReturnProperty()) : nullptr;
        if (!actor || !scene || !vector || !function || function->GetOuterPrivate() != actor ||
            !function->HasAnyFunctionFlags(U::FUNC_Native) || function->GetNumParms() != 1 ||
            function->GetParmsSize() != sizeof(Point) || !output || output->GetOffset_Internal() != 0 ||
            output->GetSize() != sizeof(Point) || output->GetStruct().Get() != vector) return false;
        const auto read = [scene, function](U::UObject* pawn, Point& position) {
            auto* root = object(pawn, STR("RootComponent"));
            if (!live(root) || !root->IsA(scene) || root->GetOuterPrivate() != pawn) return false;
            position.fill(std::numeric_limits<double>::quiet_NaN());
            pawn->ProcessEvent(function, position.data());
            return std::ranges::all_of(position, [](double axis) { return std::isfinite(axis); });
        };
        Point first{}, second{};
        return read(host, first) && read(guest, second) && squared_distance(first, second) <= 300.0 * 300.0;
    }

    std::string_view transfer_package_row(const Context& host, U::UObject* guest, U::UObject* detached,
                                          std::string_view identity, bool require_quest)
    {
        HandoffInventory inventory;
        std::string owned;
        if (!inventory.valid() || !inventory.pickup_flags(host.save)) return "stock_missing_contact_handoff_pickup_unqualified";
        if (!loot_escrow::sealed_package_guid(host.save, owned) || owned != identity)
            return "stock_missing_contact_handoff_source_unqualified";

        auto* from = inventory.backpack->ContainerPtrToValuePtr<void>(host.save);
        auto* to = inventory.backpack->ContainerPtrToValuePtr<void>(detached);
        U::FScriptArrayHelper from_rows(inventory.backpack, from), to_rows(inventory.backpack, to);
        if (to_rows.Num() < 0 || to_rows.Num() >= handoff_inventory_limit || from == to ||
            (to_rows.Num() && from_rows.GetRawPtr() == to_rows.GetRawPtr()))
            return "stock_missing_contact_handoff_destination_unqualified";
        int source = 0;
        while (source < from_rows.Num() && !inventory.is_package(from_rows.GetRawPtr(source))) ++source;
        if (source == from_rows.Num()) return "stock_missing_contact_handoff_source_unqualified";
        const auto* original = from_rows.GetRawPtr(source);
        const auto* original_guid = inventory.guid->ContainerPtrToValuePtr<void>(original);
        auto* defaults = inventory.package->GetClassDefaultObject().Get();
        auto* important = field<U::FBoolProperty>(defaults, STR("NeverDrop"));
        if (!defaults || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) || !boolean(defaults, STR("Stacks"), false) ||
            !important || inventory.never_drop->GetPropertyValueInContainer(original) != important->GetPropertyValueInContainer(defaults))
            return "stock_missing_contact_handoff_stock_row_unqualified";
        const auto has_no_package = [&](U::UObject* owner) {
            bool conflict = false;
            return inventory.visit(owner, [&](const void* row) {
                conflict |= inventory.is_package(row) || inventory.guid->Identical(original_guid,
                    inventory.guid->ContainerPtrToValuePtr<void>(row));
            }) && !conflict;
        };
        if (!has_no_package(detached)) return "stock_missing_contact_handoff_destination_conflict";

        bool foreign_conflict = false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (candidate == host.pawn || candidate == guest || !live(candidate)) return RC::LoopAction::Continue;
            if (candidate->GetClassPrivate() == host.instance->GetClassPrivate() && candidate != host.instance)
            {
                auto* save = object(candidate, STR("SaveSlot_Ref"));
                foreign_conflict = save == host.save || save == detached;
            }
            else if (candidate->GetClassPrivate() == host.pawn->GetClassPrivate())
            {
                auto* save = object(candidate, STR("Save"));
                foreign_conflict = save == host.save || save == detached || (authority(candidate) &&
                    static_cast<U::AActor*>(candidate)->GetWorld() == host.world && exact(save, save_class) && !has_no_package(save));
            }
            return foreign_conflict ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        if (foreign_conflict) return "stock_missing_contact_handoff_owner_conflict";

        HandoffValue from_before, to_before, prepared_from, prepared_to;
        if (!from_before.capture(inventory.backpack, from) || !to_before.capture(inventory.backpack, to) ||
            !prepared_from.capture(inventory.backpack, from) || !prepared_to.capture(inventory.backpack, to))
            return "stock_missing_contact_handoff_inventory_snapshot_unavailable";
        constexpr std::size_t protected_count = 4;
        const std::array<U::FProperty*, protected_count> guarded{inventory.equipment, inventory.armor, inventory.helmet, inventory.flags};
        std::array<std::array<HandoffValue, protected_count>, 2> protected_values;
        const std::array owners{host.save, detached};
        for (std::size_t owner = 0; owner < owners.size(); ++owner)
            for (std::size_t property = 0; property < guarded.size(); ++property)
                if (!protected_values[owner][property].capture(guarded[property], guarded[property]->ContainerPtrToValuePtr<void>(owners[owner])))
                    return "stock_missing_contact_handoff_inventory_snapshot_unavailable";
        const auto protected_unchanged = [&] {
            for (std::size_t owner = 0; owner < owners.size(); ++owner)
                for (std::size_t property = 0; property < guarded.size(); ++property)
                    if (!protected_values[owner][property].matches(guarded[property]->ContainerPtrToValuePtr<void>(owners[owner]))) return false;
            return true;
        };

        U::FScriptArrayHelper planned_from(inventory.backpack, prepared_from.value.get()),
                              planned_to(inventory.backpack, prepared_to.value.get());
        const auto new_index = planned_to.AddValues(1);
        if (new_index != to_rows.Num() || planned_to.Num() != to_rows.Num() + 1)
            return "stock_missing_contact_handoff_append_unqualified";
        inventory.row->CopyCompleteValue(planned_to.GetRawPtr(new_index), original);
        if (!inventory.same_row(original, planned_to.GetRawPtr(new_index))) return "stock_missing_contact_handoff_payload_unqualified";
        planned_from.RemoveValues(source, 1);
        if (planned_from.Num() != from_rows.Num() - 1) return "stock_missing_contact_handoff_debit_unqualified";
        for (int i = 0; i < planned_from.Num(); ++i)
            if (!inventory.same_row(planned_from.GetRawPtr(i), from_rows.GetRawPtr(i + (i >= source))))
                return "stock_missing_contact_handoff_source_changed";
        for (int i = 0; i < new_index; ++i)
            if (!inventory.same_row(planned_to.GetRawPtr(i), to_rows.GetRawPtr(i)))
                return "stock_missing_contact_handoff_destination_changed";

        const auto same_owners_and_quest = [&] {
            const auto current = qualify(address(host.world), address(host.pawn), false, require_quest, nullptr,
                host.world->GetPathName() == black_market_world ? black_market_world : world_path);
            return current.world == host.world && current.driver == host.driver && current.pawn == host.pawn &&
                current.save == host.save && current.instance == host.instance && (!require_quest || handoff_alive(host.pawn, host.save)) &&
                silverfish::native_guest_regen::qualifies_detached_guest(guest, detached, host.save) &&
                static_cast<U::AActor*>(guest)->GetWorld() == host.world && handoff_alive(guest, detached) &&
                (!require_quest || silverfish::native_quest_rewards::active_missing_contact(host.save));
        };
        if (!same_owners_and_quest() || !from_before.matches(from) || !to_before.matches(to) || !protected_unchanged())
            return "stock_missing_contact_handoff_scope_changed";
        inventory.backpack->CopyCompleteValue(from, prepared_from.value.get());
        inventory.backpack->CopyCompleteValue(to, prepared_to.value.get());
        std::string sent, received;
        if (prepared_from.matches(from) && prepared_to.matches(to) && to_rows.Num() == planned_to.Num() &&
            inventory.same_row(planned_to.GetRawPtr(new_index), to_rows.GetRawPtr(new_index)) &&
            protected_unchanged() && same_owners_and_quest() &&
            loot_escrow::sealed_package_guid(host.save, sent) && sent.empty() &&
            loot_escrow::sealed_package_guid(detached, received) && received == identity)
            return "transferred";

        inventory.backpack->CopyCompleteValue(to, to_before.value.get());
        inventory.backpack->CopyCompleteValue(from, from_before.value.get());
        return from_before.matches(from) && to_before.matches(to) && protected_unchanged() && same_owners_and_quest() ?
            "stock_missing_contact_handoff_rolled_back" : "mutation_unverified_missing_contact_handoff_rollback";
    }

    int handoff_package(const Lua& lua)
    {
        const auto fail = [&](std::string_view status) { return result(lua, false, status); };
        if (!ordinary || !ready() || lua.get_stack_size() != 5 || !lua.is_string(5))
            return fail("stock_missing_contact_handoff_arguments");
        std::array<Address, 4> input{};
        for (auto& slot : input)
        {
            if (!lua.is_integer(1)) return fail("stock_missing_contact_handoff_arguments");
            const auto parsed = lua.get_integer();
            if (parsed <= 0) return fail("stock_missing_contact_handoff_arguments");
            slot = static_cast<Address>(parsed);
        }
        const auto identity = lua.get_string();
        if (identity.size() != 32 || !std::ranges::all_of(identity, [](char ch) {
                return (ch >= '0' && ch <= '9') || (ch >= 'A' && ch <= 'F');
            }) || identity == "00000000000000000000000000000000" || input[1] == input[2])
            return fail("stock_missing_contact_handoff_arguments");
        const auto [world_id, host_id, guest_id, detached_id] = input;
        const auto qualify_host = [&] {
            auto ctx = qualify(world_id, host_id, false);
            return ctx.world ? ctx : qualify(world_id, host_id, false, true, nullptr, black_market_world);
        };
        const auto host = qualify_host();
        if (!host.world || !handoff_alive(host.pawn, host.save) || (live_context(host) && live_pending()))
            return fail("stock_missing_contact_handoff_host_unqualified");

        U::UObject* guest{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != guest_id) return RC::LoopAction::Continue;
            if (exact(candidate, pawn_class) && static_cast<U::AActor*>(candidate)->GetWorld() == host.world) guest = candidate;
            return RC::LoopAction::Break;
        });
        auto* detached = object(guest, STR("Save"));
        if (!exact(detached, save_class) || address(detached) != detached_id || !detached->GetOuterPrivate() ||
            detached->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient") ||
            !silverfish::native_guest_regen::qualifies_detached_guest(guest, detached, host.save) || !handoff_alive(guest, detached))
            return fail("stock_missing_contact_handoff_guest_unqualified");
        if (!handoff_in_reach(host.pawn, guest)) return fail("stock_missing_contact_handoff_out_of_reach");
        if (!silverfish::native_quest_rewards::active_missing_contact(host.save))
            return fail("stock_missing_contact_handoff_quest_unqualified");

        const auto status = transfer_package_row(host, guest, detached, identity, true);
        return result(lua, status == "transferred", status);
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
                      !qualified_source(ctx, live_dig.source.Get()))) ||
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
        case LiveState::Arming: return live_dig.issue.empty() ? "ready" : "refused";
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
            auto* collector_save = live_dig.collector_save.Get();
            if (!loot_escrow::sealed_package_guid(collector_save ? collector_save : ctx.save, current) || current != live_dig.package_guid ||
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
        lua.set_integer(static_cast<std::int64_t>(address(live_dig.collector.Get())));
        return 13;
    }

    int collect_guest_package(const Lua& lua)
    {
        if (!ready() || !ordinary || lua.get_stack_size() != 6 || !lua.is_string(6))
            return result(lua, false, "stock_missing_contact_collect_arguments");
        std::array<Address, 4> input{};
        for (auto& value : input)
        {
            if (!lua.is_integer(1)) return result(lua, false, "stock_missing_contact_collect_arguments");
            const auto parsed = lua.get_integer();
            if (parsed <= 0) return result(lua, false, "stock_missing_contact_collect_arguments");
            value = static_cast<Address>(parsed);
        }
        if (!lua.is_integer(1)) return result(lua, false, "stock_missing_contact_collect_arguments");
        const auto serial = lua.get_integer();
        const auto identity = lua.get_string();
        const auto [world_id, host_id, guest_id, save_id] = input;
        const auto ctx = qualify(world_id, host_id, false, false);
        auto* package = live_dig.package.Get();
        if (!live_context(ctx) || live_dig.state != LiveState::Escrowed || !valid_package(ctx, package) ||
            serial <= 0 || live_dig.serial != serial || identity.empty() || live_dig.escrow_guid != identity)
            return result(lua, false, "stock_missing_contact_collect_unavailable");

        U::UObject* guest{};
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != guest_id) return RC::LoopAction::Continue;
            if (exact(candidate, pawn_class) && static_cast<U::AActor*>(candidate)->GetWorld() == ctx.world) guest = candidate;
            return RC::LoopAction::Break;
        });
        auto* save = object(guest, STR("Save"));
        if (address(save) != save_id || !silverfish::native_guest_regen::qualifies_detached_guest(guest, save, ctx.save) ||
            !handoff_alive(guest, save))
            return result(lua, false, "stock_missing_contact_collect_recipient_changed");
        HandoffInventory inventory;
        std::string owned;
        if (!inventory.valid() || !loot_escrow::sealed_package_guid(save, owned) || !owned.empty())
            return result(lua, false, "stock_missing_contact_collect_recipient_inventory");
        U::FScriptArrayHelper destination(inventory.backpack, inventory.backpack->ContainerPtrToValuePtr<void>(save));
        if (destination.Num() >= handoff_inventory_limit)
            return result(lua, false, "stock_missing_contact_collect_backpack_full");

        auto* fn = find<U::UFunction>(item_interact);
        auto* player = field<U::FObjectProperty>(fn, STR("Player"));
        auto* hit = field<U::FObjectProperty>(fn, STR("HitComponent"));
        const U::FName name{STR("SendInteractionToActor")};
        auto* box = object(package, STR("Box"));
        if (!fn || package->GetClassPrivate()->GetFunctionByNameInChain(name) != fn || !player || !hit ||
            !live(box) || box->GetOuterPrivate() != package || fn->GetNumParms() != 2 || fn->GetParmsSize() != 2 * sizeof(void*) ||
            fn->GetReturnProperty() || fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            player->GetOffset_Internal() != 0 || hit->GetOffset_Internal() != sizeof(void*))
            return result(lua, false, "stock_missing_contact_collect_interaction_unavailable");

        live_dig.collector = guest;
        live_dig.collector_save = save;
        live_dig.dispatching_guest_pickup = true;
        std::array arguments{guest, box};
        package->ProcessEvent(fn, arguments.data());
        live_dig.dispatching_guest_pickup = false;
        settle_live_pickup(ctx);
        if (live_dig.state == LiveState::Escrowed)
        {
            live_dig.collector.Reset();
            live_dig.collector_save.Reset();
            return result(lua, false, "stock_missing_contact_collect_interaction_refused");
        }
        if (live_dig.state != LiveState::Collecting && live_dig.state != LiveState::Collected)
            return result(lua, false, "mutation_unverified_missing_contact_collect_stock_result");
        return result(lua, true, "collect_requested");
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
    bool manages_buried_source(U::UObject* source)
    {
        return (ordinary && live_dig.state == LiveState::Contact && live_dig.source.Get() == source) ||
            (watch.pressed && source && source->GetPathName() == source_path);
    }

    void record_buried_product(U::UObject* package)
    {
        if (!ordinary || live_dig.state != LiveState::Contact || live_dig.package.Get()) return;
        const auto ctx = bound_live();
        // The exact stock buried-source graph supplies the spawned actor. Its
        // collision may adjust its pose before escrow or while someone approaches.
        if (ctx.world && valid_package(ctx, package)) remember_package(package);
    }

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
            ordinary_callbacks[2] = Hook::RegisterProcessLocalScriptFunctionPostCallback(restore_guest_dig_frame,
                {false, false, STR("SilverfishCoopNative"), STR("Restore the remote shovel camera after its stock graph")});
            ordinary_callbacks[3] = Hook::RegisterProcessInternalPreCallback(aim_guest_dig_frame,
                {false, false, STR("SilverfishCoopNative"), STR("Align the remote shovel at its deferred stock trace")});
            ordinary_callbacks[4] = Hook::RegisterProcessInternalPostCallback(restore_guest_dig_frame,
                {false, false, STR("SilverfishCoopNative"), STR("Restore the remote shovel camera after its deferred stock trace")});
            if (std::ranges::find(ordinary_callbacks, Hook::ERROR_ID) != ordinary_callbacks.end())
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
        guest_digs = {};
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
            lua.register_function("SilverfishDispatchNativeGuestDig", start_guest_dig);
            lua.register_function("SilverfishRenewNativeHostDigScope", renew_live);
            lua.register_function("SilverfishReadNativeHostDigScope", read_live);
            lua.register_function("SilverfishCollectNativeGuestDigPackage", collect_guest_package);
            lua.register_function("SilverfishCaptureNativeHostDigEscrow", capture_live_escrow);
            lua.register_function("SilverfishVerifyNativeHostDigEscrow", verify_live_escrow);
            lua.register_function("SilverfishAcknowledgeNativeHostDig", acknowledge_live);
            lua.register_function("SilverfishRestoreNativeHostDigEscrow", restore_live_escrow);
            lua.register_function("SilverfishTransferMissingContactPackage", handoff_package);
            if (live_fixture_enabled())
                lua.register_function("SilverfishDispatchNativeHostDigLiveFixture", dispatch_live_fixture);
        }
    }
}
