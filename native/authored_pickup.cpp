#include "authored_pickup.hpp"
#include "interaction_input.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <bit>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <memory>
#include <optional>
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
    using Iteration = Hook::TCallbackIterationData<void>;
    using Clock = std::chrono::steady_clock;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;

    struct Profile
    {
        std::string_view source;
        const U::TCHAR* world;
        const U::TCHAR* actor;
        const U::TCHAR* item_class;
        double x, y, z;
        int item_type;
        std::optional<int> stack_units{};
        bool physics{};
    };

    constexpr std::array profiles{
        Profile{
            "observatory-comp-board",
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Item_CompBoard_C_2"),
            STR("/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"),
            -5680.476689, -849.040347, 128.496287, 11,
        },
        Profile{
            "city-copper-wire",
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_CopperWire_C_3"),
            STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"),
            1299.7165908306, 3418.79597614179, 1239.49197257439, 11,
        },
        Profile{
            "dead-end-coup-report",
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P:PersistentLevel.Note_DeadEnd_01_C_1"),
            STR("/Game/Classes/Items/Key_Items/Note_DeadEnd_01.Note_DeadEnd_01_C"),
            63519.2477725694, 36406.1978875462, 849.5514682383, 9,
        },
        Profile{
            "railyard-depot-documents",
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P:PersistentLevel.Documents_DepotOffice_BP_C_1"),
            STR("/Game/Classes/Items/Key_Items/Documents_DepotOffice_BP.Documents_DepotOffice_BP_C"),
            3297.7888314760, -24282.0923970261, 990.5693125502, 9,
        },
        Profile{
            "city-paladin-brandy",
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_VintageWine_C_1"),
            STR("/Game/Classes/Items/Item_VintageWine.Item_VintageWine_C"),
            -571.3226728526, -8316.4123004860, -199.9998970174, 1,
        },
        Profile{
            "scientist-blank-data-tape",
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Scientist_01.Shop_Scientist_01:PersistentLevel.DataTape_Blank_C_3"),
            STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C"),
            7580.6051850203, 5202.1434900782, -642.6426753447, 9,
        },
        Profile{
            "crossroads-pocket-watch",
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P:PersistentLevel.Item_PocketWatch_C_1"),
            STR("/Game/Classes/Items/Item_PocketWatch.Item_PocketWatch_C"),
            9338.318763111682, 8669.320108298598, 391.93632416768173, 11, 1, true,
        },
        Profile{
            "savlo-estate-evil-eye-ring",
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P:PersistentLevel.Art_EvilEyeRing_C_1"),
            STR("/Game/Classes/Items/Artifacts/Art_EvilEyeRing.Art_EvilEyeRing_C"),
            125.08236158222627, 2704.0395701013076, 474.9005913197349, 0,
        },
        Profile{
            "borough-mre",
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough:PersistentLevel.Item_MRE_C_1"),
            STR("/Game/Classes/Items/Item_MRE.Item_MRE_C"),
            73.05513264620163, 2566.409056792115, 307.29826126640774, 1, 0, true,
        },
    };
    constexpr auto base_class = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto interact_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C:SendInteractionToActor");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto pawn_script = STR("/Game/Classes/Player/Player_FP.Player_FP_C:ExecuteUbergraph_Player_FP");
    constexpr auto controller_path = STR("/Game/Classes/Player/PlayerControler_FP.PlayerControler_FP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto add_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto inventory_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto default_item_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:MakeDefaultItem");
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto tape_station_class = STR("/Game/Classes/Actors/DataTape_DownloadStation_BP.DataTape_DownloadStation_BP_C");
    constexpr auto blank_tape_path = STR("/Game/Classes/Items/DataTapes/DataTape_Blank.DataTape_Blank_C");
    struct TapeExchangeProfile
    {
        const U::TCHAR* world;
        const U::TCHAR* station;
        const U::TCHAR* product;
    };
    constexpr TapeExchangeProfile stone_maze_tape{
        STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_StoneMaze_01.DataTape_StoneMaze_01_C"),
    };
    constexpr TapeExchangeProfile eather_pump_tape{
        STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P"),
        STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P:PersistentLevel.DataTape_DownloadStation_BP_C_1"),
        STR("/Game/Classes/Items/DataTapes/DataTape_EatherPumps.DataTape_EatherPumps_C"),
    };
    constexpr std::array guid_members{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr std::size_t parameter_capacity = 256;
    constexpr double maximum_range_squared = 250.0 * 250.0;

    template <class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template <class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    Address address(U::UObject* value) { return reinterpret_cast<Address>(value); }

    template <class T> T* parameter(U::UFunction* fn, const U::TCHAR* name, int offset, bool output = false)
    {
        auto* property = field<T>(fn, name);
        return property && property->HasAnyPropertyFlags(U::CPF_Parm) &&
            property->HasAnyPropertyFlags(U::CPF_OutParm) == output && !property->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            property->GetOffset_Internal() == offset && offset + property->GetSize() <= fn->GetParmsSize() ? property : nullptr;
    }

    U::UObject* read(U::FObjectProperty* property, const void* container)
    {
        return property && container ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(container)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        return read(field<U::FObjectProperty>(owner, name), owner);
    }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* property = field<U::FBoolProperty>(owner, name);
        return property && property->GetPropertyValueInContainer(owner) == expected;
    }

    int role(U::UObject* actor)
    {
        auto* property = field<U::FByteProperty>(actor, STR("Role"));
        return property ? property->GetPropertyValueInContainer(actor) : -1;
    }

    bool live(U::UObject* value)
    {
        constexpr auto excluded = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->HasAnyFlags(excluded) && !value->IsUnreachable();
    }

    bool exact(U::UObject* value, const U::TCHAR* type)
    {
        return live(value) && value->GetClassPrivate() == find<U::UClass>(type);
    }

    bool env(const char* variable)
    {
        std::array<char, 2> value{};
        std::size_t size{};
        return getenv_s(&size, value.data(), value.size(), variable) == 0 && size == value.size() && value[0] == '1';
    }

    bool headless() { return env("SILVERFISH_COOP_HEADLESS"); }

    struct Observation
    {
        Clock::time_point expires{};
        std::uint64_t sequence{};
        bool fixture{}, input_pending{};
    };

    struct Scope
    {
        U::FWeakObjectPtr world, pawn, actor, box, controller, driver;
        const Profile* profile{};
        bool host{}, claimed{}, original_collision{}, original_hidden{};
        Observation observed{};
    };

    std::array<Scope, profiles.size()> scopes{};
    std::uint64_t sequence{};
    Scope* fixture_scope{};
    std::atomic<bool> installed{};
    std::array callbacks{Hook::ERROR_ID, Hook::ERROR_ID};

    bool ready() { return U::IsInGameThreadRaw() && installed.load(std::memory_order_acquire); }

    bool local_player(U::UObject* controller, U::UObject* player)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.LocalPlayer"));
        return live(player) && type && player->IsA(type) && object(player, STR("PlayerController")) == controller;
    }

    bool connection(U::UObject* driver, U::UObject* player)
    {
        auto* type = find<U::UClass>(STR("/Script/Engine.NetConnection"));
        return live(player) && type && player->IsA(type) && object(player, STR("Driver")) == driver;
    }

    bool local_scope(const Scope& state)
    {
        auto* world = state.world.Get();
        auto* pawn = state.pawn.Get();
        auto* controller = state.controller.Get();
        auto* driver = state.driver.Get();
        if (!live(world) || !state.profile || world->GetPathName() != state.profile->world ||
            !exact(pawn, pawn_path) || !exact(controller, controller_path) || !live(driver) ||
            object(world, STR("NetDriver")) != driver || object(driver, STR("World")) != world ||
            !exact(object(world, STR("OwningGameInstance")), instance_path) ||
            static_cast<U::AActor*>(pawn)->GetWorld() != world || static_cast<U::AActor*>(controller)->GetWorld() != world ||
            object(pawn, STR("Controller")) != controller || object(controller, STR("Pawn")) != pawn ||
            !local_player(controller, object(controller, STR("Player"))) || role(pawn) != (state.host ? 3 : 2)) return false;
        auto* server_prop = field<U::FObjectProperty>(driver, STR("ServerConnection"));
        auto* server = read(server_prop, driver);
        return server_prop && (state.host ? !server && role(controller) == 3 : connection(driver, server));
    }

    Scope* select_source(const Lua& lua)
    {
        if (!lua.is_string()) return nullptr;
        const auto source = lua.get_string();
        const auto match = std::ranges::find_if(profiles, [&source](const Profile& profile) { return profile.source == source; });
        return match == profiles.end() ? nullptr : &scopes[static_cast<std::size_t>(match - profiles.begin())];
    }

    Scope* actor_scope(U::UObject* actor)
    {
        if (!actor) return nullptr;
        const auto match = std::ranges::find_if(scopes, [actor](const Scope& scope) { return scope.actor.Get() == actor; });
        return match != scopes.end() && local_scope(*match) ? &*match : nullptr;
    }

    const char* source_fault(U::UObject* actor, U::UObject* world, const Profile& profile, bool require_placement = true)
    {
        if (!exact(actor, profile.item_class) || actor->GetPathName() != profile.actor ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false)) return "authored_pickup_stock_actor_identity_unqualified";
        if (!boolean(actor, STR("Physics"), profile.physics)) return "authored_pickup_stock_physics_unqualified";
        if (!boolean(actor, STR("Stacks"), profile.stack_units.has_value())) return "authored_pickup_stock_stacking_unqualified";
        if (profile.stack_units)
        {
            auto* count = field<U::FIntProperty>(actor, STR("Ammo"));
            if (!count || count->GetPropertyValueInContainer(actor) != *profile.stack_units)
                return "authored_pickup_stock_amount_unqualified";
        }
        auto* item = static_cast<U::AActor*>(actor);
        auto* category = field<U::FByteProperty>(actor, STR("ItemType"));
        if (item->GetWorld() != world || item->GetLevel() != object(world, STR("PersistentLevel")))
            return "authored_pickup_stock_world_unqualified";
        if (!category || category->GetPropertyValueInContainer(actor) != profile.item_type)
            return "authored_pickup_stock_category_unqualified";
        // Physics-enabled items can settle below their authored placement under vanilla simulation.
        // A claimed item may fall when collision is disabled. Its exact scoped object stays hidden.
        if (profile.physics && !require_placement) return nullptr;
        auto position = item->K2_GetActorLocation();
        const auto dx = position.GetX() - profile.x;
        const auto dy = position.GetY() - profile.y;
        const auto dz = position.GetZ() - profile.z;
        const auto horizontal = dx * dx + dy * dy;
        const bool located = profile.physics ?
            horizontal <= 75.0 * 75.0 && dz >= -100.0 && dz <= 12.0 :
            horizontal + dz * dz <= 25.0;
        return located ? nullptr : "authored_pickup_stock_placement_unqualified";
    }

    bool exact_source(U::UObject* actor, U::UObject* world, const Profile& profile, bool require_placement = true)
    {
        return source_fault(actor, world, profile, require_placement) == nullptr;
    }

    U::UObject* source_box(U::UObject* actor)
    {
        auto* value = object(actor, STR("Box"));
        return exact(value, STR("/Script/Engine.BoxComponent")) && value->GetOuterPrivate() == actor &&
            value == object(actor, STR("RootComponent")) ? value : nullptr;
    }

    bool in_range(U::UObject* pawn, U::UObject* source)
    {
        auto player = static_cast<U::AActor*>(pawn)->K2_GetActorLocation();
        auto item = static_cast<U::AActor*>(source)->K2_GetActorLocation();
        const auto dx = player.GetX() - item.GetX();
        const auto dy = player.GetY() - item.GetY();
        const auto dz = player.GetZ() - item.GetZ();
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(squared) && squared <= maximum_range_squared;
    }

    U::UFunction* interaction()
    {
        auto* function = find<U::UFunction>(interact_path);
        auto* passed_player = parameter<U::FObjectProperty>(function, STR("Player"), 0);
        auto* hit = parameter<U::FObjectProperty>(function, STR("HitComponent"), sizeof(void*));
        if (!function || function->GetOuterPrivate() != find<U::UClass>(base_class) || function->GetNumParms() != 2 ||
            function->GetParmsSize() != 2 * sizeof(void*) || function->GetReturnProperty() ||
            function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) ||
            !passed_player || passed_player->GetPropertyClass().Get() != find<U::UClass>(pawn_path) ||
            !hit || hit->GetPropertyClass().Get() != find<U::UClass>(STR("/Script/Engine.SceneComponent"))) return nullptr;
        return function;
    }

    bool available(const Scope& scope)
    {
        auto* actor = scope.actor.Get();
        return !scope.claimed && local_scope(scope) && exact_source(actor, scope.world.Get(), *scope.profile) &&
            source_box(actor) == scope.box.Get() && static_cast<U::AActor*>(actor)->GetActorEnableCollision() &&
            boolean(actor, STR("bHidden"), false);
    }

    bool set_visibility(Scope& scope, bool claimed)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (!local_scope(scope) || !exact_source(actor, scope.world.Get(), *scope.profile, !scope.claimed || !claimed))
            return false;
        const bool hidden = claimed || scope.original_hidden;
        const bool collision = !claimed && scope.original_collision;
        actor->SetActorHiddenInGame(hidden);
        actor->SetActorEnableCollision(collision);
        if (actor->GetActorEnableCollision() != collision || !boolean(actor, STR("bHidden"), hidden)) return false;
        scope.claimed = claimed;
        if (claimed) scope.observed = {};
        return true;
    }

    void clear(Scope& scope)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (scope.claimed && live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false))
        {
            actor->SetActorHiddenInGame(scope.original_hidden);
            actor->SetActorEnableCollision(scope.original_collision);
        }
        if (fixture_scope == &scope) fixture_scope = nullptr;
        scope = {};
    }

    void clear()
    {
        for (auto& scope : scopes) clear(scope);
    }

    bool admit_input(const Scope& scope, U::UObject* pawn, U::UObject* hit)
    {
        return pawn && pawn == scope.pawn.Get() && hit && hit == scope.box.Get() && available(scope) &&
            boolean(pawn, STR("Dead"), false) && in_range(pawn, scope.actor.Get());
    }

    void intercept(Iteration& call, U::UObject* context, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!ready() || !context || !function || call.OriginalFunctionCallPrevented()) return;
        static const U::FName interact_name{STR("SendInteractionToActor")};
        static const U::FName put_name{STR("PutInInventory")};
        static const U::FName pickup_name{STR("PickUp_Object")};
        static const U::FName add_name{STR("AddItemToInventory")};
        const auto name = function->GetFName();
        if (name != interact_name && name != put_name && name != pickup_name && name != add_name) return;
        if (name == add_name)
        {
            if (function != find<U::UFunction>(add_path)) return;
            const auto* scope = actor_scope(read(parameter<U::FObjectProperty>(function, STR("Item"), 0), args));
            if (scope && context == object(scope->world.Get(), STR("OwningGameInstance"))) call.PreventOriginalFunctionCall();
            return;
        }
        auto* scope = actor_scope(context);
        if (!scope || function->GetOuterPrivate() != find<U::UClass>(base_class)) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || name != interact_name || function != interaction()) return;

        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), args);
        auto* hit = read(field<U::FObjectProperty>(function, STR("HitComponent")), args);
        if (!admit_input(*scope, pawn, hit)) return;
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        const bool fixture = !frame && fixture_scope == scope;
        const bool valid_caller = !frame ||
            (caller && caller->Object() == pawn && caller->Node() == find<U::UFunction>(pawn_script));
        const bool stock = !fixture && valid_caller && object(pawn, STR("InteractionTarget_Component")) == hit &&
            boolean(pawn, STR("Holding_Interact"), true) &&
            silverfish::native_interaction_input::consume_current_press(pawn);
        if (fixture || stock) scope->observed = {Clock::now() + std::chrono::seconds(4), ++sequence, fixture, stock};
    }

    U::UObject* live_address(Address pointer, U::UClass* type)
    {
        U::UObject* result{};
        if (!pointer || !type) return nullptr;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* candidate, int, int) {
            if (address(candidate) != pointer) return RC::LoopAction::Continue;
            if (live(candidate) && candidate->GetClassPrivate() == type) result = candidate;
            return RC::LoopAction::Break;
        });
        return result;
    }

    bool authorized(const Scope& scope, U::UObject* pawn, U::UObject* target)
    {
        if (!scope.host || !available(scope) || !exact(pawn, pawn_path) || role(pawn) != 3 ||
            !exact(target, save_path) || object(pawn, STR("Save")) != target || !boolean(pawn, STR("Dead"), false) ||
            role(scope.actor.Get()) != 3 || !in_range(pawn, scope.actor.Get())) return false;
        auto* world = scope.world.Get();
        auto* controller = object(pawn, STR("Controller"));
        auto* canonical = object(object(world, STR("OwningGameInstance")), STR("SaveSlot_Ref"));
        if (!exact(canonical, save_path) || !exact(controller, controller_path) || role(controller) != 3 ||
            object(controller, STR("Pawn")) != pawn || static_cast<U::AActor*>(pawn)->GetWorld() != world ||
            static_cast<U::AActor*>(controller)->GetWorld() != world) return false;
        if (pawn == scope.pawn.Get()) return target == canonical && controller == scope.controller.Get();
        auto* player = object(controller, STR("Player"));
        return target != canonical && connection(scope.driver.Get(), player) && object(player, STR("PlayerController")) == controller;
    }

    struct RowSchema
    {
        U::UClass* save = find<U::UClass>(save_path);
        U::UScriptStruct* type = find<U::UScriptStruct>(inventory_path);
        U::FArrayProperty* array = field<U::FArrayProperty>(save, STR("PlayerBackpack"));
        U::FStructProperty* row = array ? U::CastField<U::FStructProperty>(array->GetInner()) : nullptr;
        U::FClassProperty* klass = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FStructProperty* payload = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        U::FStructProperty* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        std::array<U::FIntProperty*, 4> words{};

        bool bind()
        {
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            if (!type || !row || row->GetStruct().Get() != type || !guid || !guid_type || guid->GetStruct().Get() != guid_type ||
                !klass || klass->GetMetaClass().Get() != find<U::UClass>(base_class) || !payload ||
                payload->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"))) return false;
            for (std::size_t index = 0; index < words.size(); ++index)
            {
                words[index] = field<U::FIntProperty>(guid_type, guid_members[index]);
                if (!words[index] || words[index]->GetOffset_Internal() != static_cast<int>(index * 4)) return false;
            }
            return true;
        }

        static bool same(U::FProperty* property, const void* first, const void* second)
        {
            return property->Identical(property->ContainerPtrToValuePtr<void>(first),
                                       property->ContainerPtrToValuePtr<void>(second));
        }

        bool identical(const void* first, const void* second) const
        {
            return row->Identical(first, second) && same(payload, first, second);
        }

        Guid identity(const void* value) const
        {
            const auto* data = guid->ContainerPtrToValuePtr<void>(value);
            Guid result{};
            for (std::size_t index = 0; index < words.size(); ++index)
                result[index] = static_cast<std::uint32_t>(words[index]->GetPropertyValueInContainer(data));
            return result;
        }

        void set_identity(void* value, const Guid& identity) const
        {
            auto* data = guid->ContainerPtrToValuePtr<void>(value);
            for (std::size_t index = 0; index < words.size(); ++index)
                words[index]->SetPropertyValueInContainer(data, std::bit_cast<std::int32_t>(identity[index]));
        }

        bool authored(const void* value, U::UClass* item_class) const
        {
            return item_class && read(klass, value) == item_class && identity(value) != Guid{};
        }

        template<class T>
        bool matches(const void* value, U::UObject* actor, const U::TCHAR* row_name, const U::TCHAR* actor_name) const
        {
            auto* left = field<T>(type, row_name);
            auto* right = field<T>(actor, actor_name);
            return left && right && left->GetPropertyValueInContainer(value) == right->GetPropertyValueInContainer(actor);
        }

        bool matches_actor(const void* value, U::UObject* actor) const
        {
            auto* actor_payload = field<U::FStructProperty>(actor, STR("DataStruct"));
            return authored(value, actor->GetClassPrivate()) && actor_payload && actor_payload->GetStruct().Get() == payload->GetStruct().Get() &&
                matches<U::FIntProperty>(value, actor, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"), STR("Ammo")) &&
                matches<U::FDoubleProperty>(value, actor, STR("Weight_12_11AB9ED547504E78A849A692A36DC938"), STR("Weight")) &&
                matches<U::FIntProperty>(value, actor, STR("Value_15_5D96101F4BADB44D9DC5F0BD5F8A35C9"), STR("Value")) &&
                matches<U::FNameProperty>(value, actor, STR("DataTag_22_F7E8E22945F4F943ADE1AE85C4A2B99D"), STR("DataTag")) &&
                matches<U::FBoolProperty>(value, actor, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"), STR("NeverDrop")) &&
                payload->Identical(payload->ContainerPtrToValuePtr<void>(value), actor_payload->ContainerPtrToValuePtr<void>(actor));
        }
    };

    U::UObject* isolated(Address pointer, const RowSchema& rows)
    {
        auto* save = live_address(pointer, rows.save);
        if (!save || !save->GetOuterPrivate() || save->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient")) return nullptr;
        auto* instance = find<U::UClass>(instance_path);
        auto* pawn = find<U::UClass>(pawn_path);
        if (!instance || !pawn) return nullptr;
        bool attached = false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* object_ptr, int, int) {
            if (!live(object_ptr)) return RC::LoopAction::Continue;
            const auto* kind = object_ptr->GetClassPrivate();
            attached = (kind == pawn && object(object_ptr, STR("Save")) == save) ||
                (kind == instance && object(object_ptr, STR("SaveSlot_Ref")) == save);
            return attached ? RC::LoopAction::Break : RC::LoopAction::Continue;
        });
        return attached ? nullptr : save;
    }

    const char* convert(const RowSchema& rows, U::UObject* pawn, U::UObject* actor, U::UObject* scratch, Guid& guid)
    {
        auto* fn = find<U::UFunction>(converter_path);
        auto* library = find<U::UObject>(library_path);
        auto* item = parameter<U::FObjectProperty>(fn, STR("ItemActor"), 0);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), sizeof(void*));
        auto* output = parameter<U::FStructProperty>(fn, STR("Item"), 2 * sizeof(void*), true);
        if (!fn || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            fn->GetNumParms() != 3 || fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !item ||
            item->GetPropertyClass().Get() != find<U::UClass>(base_class) || !context || !context->GetPropertyClass().Get() ||
            !pawn->IsA(context->GetPropertyClass().Get()) || !output || output->GetStruct().Get() != rows.type)
            return "stock_item_conversion_schema_unavailable";
        U::FScriptArrayHelper array(rows.array, rows.array->ContainerPtrToValuePtr<void>(scratch));
        if (array.Num() != 0) return "empty_pickup_escrow_required";

        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        auto* storage = buffer.data();
        output->InitializeValue_InContainer(storage);
        const auto destroy = [output](std::byte* memory) { output->DestroyValue_InContainer(memory); };
        const std::unique_ptr<std::byte, decltype(destroy)> lifetime(storage, destroy);
        item->SetObjectPropertyValue(item->ContainerPtrToValuePtr<void>(storage), actor);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(storage), pawn);
        library->ProcessEvent(fn, storage);
        const auto* generated = output->ContainerPtrToValuePtr<void>(storage);
        if (array.Num() != 0 || !rows.matches_actor(generated, actor)) return "stock_item_row_unverified";
        const auto index = array.AddValue();
        if (index != 0 || array.Num() != 1) return "stock_item_escrow_append_unverified";
        auto* row = array.GetRawPtr(0);
        rows.row->CopyCompleteValue(row, generated);
        if (!rows.identical(row, generated))
        {
            array.RemoveValues(0, 1);
            return "stock_item_escrow_payload_unverified";
        }
        guid = rows.identity(row);
        return "captured";
    }

    int result(const Lua& lua, bool ok, std::string_view detail)
    {
        lua.set_bool(ok);
        lua.set_string(detail);
        return 2;
    }

    bool parse_guid(std::string_view text, Guid& identity)
    {
        if (text.size() != 32 || text.find_first_not_of("0123456789ABCDEF") != std::string_view::npos) return false;
        for (std::size_t index = 0; index < identity.size(); ++index)
        {
            const auto* part = text.data() + index * 8;
            const auto parsed = std::from_chars(part, part + 8, identity[index], 16);
            if (parsed.ec != std::errc{} || parsed.ptr != part + 8) return false;
        }
        return identity != Guid{};
    }

    struct TapeStores
    {
        const RowSchema& rows;
        U::FArrayProperty* equipment = field<U::FArrayProperty>(rows.save, STR("PlayerEquipment"));
        U::FStructProperty* armor = field<U::FStructProperty>(rows.save, STR("PlayerArmor"));
        U::FStructProperty* helmet = field<U::FStructProperty>(rows.save, STR("PlayerHelmet"));

        bool valid() const
        {
            auto* equipment_row = equipment ? U::CastField<U::FStructProperty>(equipment->GetInner()) : nullptr;
            return equipment_row && equipment_row->GetStruct().Get() == rows.type &&
                armor && armor->GetStruct().Get() == rows.type && helmet && helmet->GetStruct().Get() == rows.type;
        }

        struct Found
        {
            int count{};
            void* backpack_row{};
        };

        bool scan(U::UObject* save, const Guid& id, Found& found) const
        {
            found = {};
            for (auto* property : {rows.array, equipment})
            {
                U::FScriptArrayHelper items(property, property->ContainerPtrToValuePtr<void>(save));
                if (items.Num() < 0 || items.Num() > 1024) return false;
                for (int index = 0; index < items.Num(); ++index)
                {
                    auto* row = items.GetRawPtr(index);
                    if (rows.identity(row) != id) continue;
                    ++found.count;
                    if (property == rows.array) found.backpack_row = row;
                }
            }
            for (auto* property : {armor, helmet})
                if (rows.identity(property->ContainerPtrToValuePtr<void>(save)) == id) ++found.count;
            return true;
        }
    };

    U::UWorld* tape_owner_world(U::UObject* pawn, U::UObject* target, U::UObject* canonical,
                              const TapeExchangeProfile& profile)
    {
        if (!pawn || !target || !canonical || !boolean(pawn, STR("Dead"), false) ||
            !boolean(pawn, STR("bActorIsBeingDestroyed"), false) || role(pawn) != 3 || object(pawn, STR("Save")) != target)
            return nullptr;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        auto* controller = object(pawn, STR("Controller"));
        auto* instance = object(world, STR("OwningGameInstance"));
        auto* driver = object(world, STR("NetDriver"));
        if (!live(world) || world->GetPathName() != profile.world ||
            !exact(instance, instance_path) || object(instance, STR("SaveSlot_Ref")) != canonical ||
            !exact(controller, controller_path) || !boolean(controller, STR("bActorIsBeingDestroyed"), false) ||
            role(controller) != 3 || object(controller, STR("Pawn")) != pawn ||
            static_cast<U::AActor*>(controller)->GetWorld() != world || !live(driver) || object(driver, STR("World")) != world ||
            !field<U::FObjectProperty>(driver, STR("ServerConnection")) || object(driver, STR("ServerConnection")))
            return nullptr;
        auto* player = object(controller, STR("Player"));
        const bool owned = target == canonical ? local_player(controller, player) :
            connection(driver, player) && object(player, STR("PlayerController")) == controller &&
            object(player, STR("OwningActor")) == controller;
        return owned ? world : nullptr;
    }

    bool tape_station_reachable(U::UWorld* world, U::UObject* pawn, const TapeExchangeProfile& profile)
    {
        auto* station = find<U::UObject>(profile.station);
        auto* box = object(station, STR("Box"));
        return exact(station, tape_station_class) && role(station) == 3 &&
            boolean(station, STR("bActorIsBeingDestroyed"), false) && exact(box, STR("/Script/Engine.BoxComponent")) &&
            box->GetOuterPrivate() == station && static_cast<U::AActor*>(station)->GetWorld() == world &&
            static_cast<U::AActor*>(station)->GetLevel() == object(world, STR("PersistentLevel")) && in_range(pawn, station);
    }

    bool exclusive_tape_ids(const TapeStores& stores, U::UWorld* world, U::UObject* pawn, U::UObject* target,
                            U::UObject* canonical, const Guid& blank, const Guid& filled)
    {
        const auto free = [&](U::UObject* save) {
            TapeStores::Found left, right;
            return stores.scan(save, blank, left) && stores.scan(save, filled, right) &&
                left.count == 0 && right.count == 0;
        };
        if (target != canonical && !free(canonical)) return false;
        auto* pawn_type = pawn->GetClassPrivate();
        bool unique = true;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* other, int, int) {
            if (other == pawn || !live(other) || other->GetClassPrivate() != pawn_type || role(other) != 3 ||
                static_cast<U::AActor*>(other)->GetWorld() != world) return RC::LoopAction::Continue;
            auto* controller = object(other, STR("Controller"));
            if (!exact(controller, controller_path) || object(controller, STR("Pawn")) != other)
                return RC::LoopAction::Continue;
            auto* save = object(other, STR("Save"));
            unique = save != target && (!exact(save, save_path) || save == canonical || free(save));
            return unique ? RC::LoopAction::Continue : RC::LoopAction::Break;
        });
        return unique;
    }

    template<const TapeExchangeProfile& Profile>
    int exchange_tape(const Lua& lua)
    {
        const auto fail = [&](std::string_view reason) {
            if constexpr (&Profile == &eather_pump_tape)
            {
                if (const auto at = reason.find("stone_maze"); at != std::string_view::npos)
                    return result(lua, false, std::format("{}eather_pump{}", reason.substr(0, at), reason.substr(at + 10)));
            }
            return result(lua, false, reason);
        };
        if (!ready() || !env("SILVERFISH_COOP_NATIVE_ZONE") || lua.get_stack_size() != 5 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) ||
            !lua.is_string(4) || !lua.is_string(5)) return fail("stone_maze_tape_arguments");
        const auto pawn_address = lua.get_integer();
        const auto target_address = lua.get_integer();
        const auto canonical_address = lua.get_integer();
        Guid blank, filled;
        if (pawn_address <= 0 || target_address <= 0 || canonical_address <= 0 || !parse_guid(lua.get_string(), blank))
            return fail("stone_maze_tape_arguments");
        const auto supplied_guid = lua.get_string();
        if (!parse_guid(supplied_guid, filled) || blank == filled) return fail("stone_maze_tape_arguments");

        RowSchema rows;
        TapeStores stores{rows};
        if (!rows.bind() || !stores.valid()) return fail("stone_maze_tape_inventory_schema_unavailable");
        auto* pawn = live_address(static_cast<Address>(pawn_address), find<U::UClass>(pawn_path));
        auto* target = live_address(static_cast<Address>(target_address), rows.save);
        auto* canonical = live_address(static_cast<Address>(canonical_address), rows.save);
        auto* world = tape_owner_world(pawn, target, canonical, Profile);
        if (!world || !tape_station_reachable(world, pawn, Profile) || !exclusive_tape_ids(stores, world, pawn, target, canonical, blank, filled))
            return fail("stone_maze_exact_tape_owner_unavailable");

        auto* blank_class = find<U::UClass>(blank_tape_path);
        auto* filled_class = find<U::UClass>(Profile.product);
        auto* base = find<U::UClass>(base_class);
        auto* filled_cdo = filled_class ? filled_class->GetClassDefaultObject().Get() : nullptr;
        auto* blank_cdo = blank_class ? blank_class->GetClassDefaultObject().Get() : nullptr;
        auto* quantity = field<U::FIntProperty>(rows.type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        const auto stock_tape = [&](U::UObject* cdo, U::UClass* type) {
            auto* category = field<U::FByteProperty>(cdo, STR("ItemType"));
            return cdo && base && cdo->HasAnyFlags(U::RF_ClassDefaultObject) && cdo->GetClassPrivate() == type &&
                type->IsChildOf(base) && category && category->GetPropertyValueInContainer(cdo) == 9 &&
                boolean(cdo, STR("Stacks"), false);
        };
        TapeStores::Found old, replacement;
        if (!quantity || !stock_tape(blank_cdo, blank_class) || !stock_tape(filled_cdo, filled_class) ||
            !stores.scan(target, blank, old) || !stores.scan(target, filled, replacement))
            return fail("stock_stone_maze_tapes_unavailable");

        const auto success = [&](std::string_view status) {
            result(lua, true, status);
            lua.set_string(supplied_guid);
            return 3;
        };
        if (old.count == 0 && replacement.count == 1 && replacement.backpack_row &&
            rows.matches_actor(replacement.backpack_row, filled_cdo)) return success("already_exchanged");
        if (old.count != 1 || !old.backpack_row || !rows.authored(old.backpack_row, blank_class) ||
            quantity->GetPropertyValueInContainer(old.backpack_row) != 0 || replacement.count != 0)
            return fail("stone_maze_exact_blank_tape_required");

        auto* fn = find<U::UFunction>(default_item_path);
        auto* library = find<U::UObject>(library_path);
        auto* type = parameter<U::FClassProperty>(fn, STR("Item_Class"), 0);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), sizeof(void*));
        auto* output = parameter<U::FStructProperty>(fn, STR("Item"), 2 * sizeof(void*), true);
        if (!fn || !library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || fn->GetOuterPrivate() != library->GetClassPrivate() ||
            fn->GetNumParms() != 3 || fn->GetReturnProperty() || fn->GetParmsSize() > parameter_capacity ||
            fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) || !type || type->GetMetaClass().Get() != base ||
            !context || !context->GetPropertyClass().Get() || !pawn->IsA(context->GetPropertyClass().Get()) ||
            !output || output->GetStruct().Get() != rows.type) return result(lua, false, "stock_tape_conversion_schema_unavailable");
        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> buffer{};
        output->InitializeValue_InContainer(buffer.data());
        const auto release_output = [output](std::byte* memory) { output->DestroyValue_InContainer(memory); };
        const std::unique_ptr<std::byte, decltype(release_output)> initialized(buffer.data(), release_output);
        type->SetObjectPropertyValue(type->ContainerPtrToValuePtr<void>(buffer.data()), filled_class);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(buffer.data()), pawn);
        library->ProcessEvent(fn, buffer.data());
        auto* generated = output->ContainerPtrToValuePtr<void>(buffer.data());
        if (!rows.matches_actor(generated, filled_cdo) || tape_owner_world(pawn, target, canonical, Profile) != world ||
            !tape_station_reachable(world, pawn, Profile) ||
            !stores.scan(target, blank, old) || old.count != 1 || !old.backpack_row ||
            !rows.authored(old.backpack_row, blank_class) || quantity->GetPropertyValueInContainer(old.backpack_row) != 0 ||
            !stores.scan(target, filled, replacement) || replacement.count != 0)
            return result(lua, false, "stock_filled_tape_unverified");
        rows.set_identity(generated, filled);

        const auto destroy_row = [&rows](void* value) { rows.row->DestroyAndFreeValue(value); };
        const std::unique_ptr<void, decltype(destroy_row)> original(rows.row->AllocateAndInitializeValue(), destroy_row);
        if (!original) return result(lua, false, "stock_blank_tape_backup_unavailable");
        auto* slot = old.backpack_row;
        rows.row->CopyCompleteValue(original.get(), slot);
        if (!rows.identical(original.get(), slot)) return result(lua, false, "stock_blank_tape_backup_unavailable");
        rows.row->CopyCompleteValue(slot, generated);
        if (stores.scan(target, blank, old) && old.count == 0 && stores.scan(target, filled, replacement) &&
            replacement.count == 1 && replacement.backpack_row == slot && rows.identical(slot, generated))
            return success("exchanged");
        rows.row->CopyCompleteValue(slot, original.get());
        return result(lua, false, rows.identical(slot, original.get()) ? "stock_filled_tape_copy_rolled_back" :
            "mutation_unverified_stone_maze_tape_copy");
    }

    int set_scope(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_bool(2))
            return result(lua, false, "authored_pickup_scope_arguments");
        const auto ptr = static_cast<Address>(lua.get_integer());
        const auto host = lua.get_bool();
        auto* selected = select_source(lua);
        if (!selected) return result(lua, false, "authored_pickup_source_unknown");
        auto& scope = *selected;
        if (address(scope.pawn.Get()) == ptr && host == scope.host && local_scope(scope))
            return result(lua, true, "authored_pickup_scope_bound");
        clear(scope);
        auto* pawn = live_address(ptr, find<U::UClass>(pawn_path));
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        const auto& profile = profiles[static_cast<std::size_t>(selected - scopes.data())];
        if (!live(world) || world->GetPathName() != profile.world)
            return result(lua, false, "authored_pickup_source_world_unavailable");
        auto* actor = find<U::AActor>(profile.actor);
        if (const auto* fault = source_fault(actor, world, profile))
        {
            if (live(actor) && std::string_view(fault) == "authored_pickup_stock_placement_unqualified")
            {
                auto point = actor->K2_GetActorLocation();
                return result(lua, false, std::format("{}:{:.1f},{:.1f},{:.1f}", fault, point.GetX(), point.GetY(), point.GetZ()));
            }
            return result(lua, false, fault);
        }
        auto* box = source_box(actor);
        if (!box) return result(lua, false, "authored_pickup_stock_root_box_unqualified");
        if (!interaction()) return result(lua, false, "authored_pickup_stock_interaction_unqualified");
        if (!actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false))
            return result(lua, false, "authored_pickup_stock_visibility_unqualified");
        Scope next{world, pawn, actor, box, object(pawn, STR("Controller")), object(world, STR("NetDriver")),
            &profile, host, false, actor->GetActorEnableCollision(), boolean(actor, STR("bHidden"), true)};
        if (!local_scope(next)) return result(lua, false, "authored_pickup_local_controller_unavailable");
        scope = next;
        return result(lua, true, "authored_pickup_scope_bound");
    }

    int take_press(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_integer()) return 0;
        const auto pawn = static_cast<Address>(lua.get_integer());
        const auto now = Clock::now();
        Scope* selected{};
        for (auto& scope : scopes)
        {
            if (!scope.observed.input_pending || now >= scope.observed.expires || address(scope.pawn.Get()) != pawn ||
                !available(scope)) continue;
            if (!selected || scope.observed.sequence < selected->observed.sequence) selected = &scope;
        }
        if (!selected) return 0;
        selected->observed.input_pending = false;
        lua.set_string(selected->profile->source);
        return 1;
    }

    int project(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_bool()) return result(lua, false, "authored_pickup_project_arguments");
        const auto claimed = lua.get_bool();
        auto* scope = select_source(lua);
        const bool verified = scope && set_visibility(*scope, claimed);
        return result(lua, verified, verified ? "authored_pickup_projected" : "authored_pickup_projection_unverified");
    }

    int status(const Lua& lua)
    {
        auto* scope = ready() && lua.get_stack_size() == 1 ? select_source(lua) : nullptr;
        const bool active = scope && local_scope(*scope) &&
            exact_source(scope->actor.Get(), scope->world.Get(), *scope->profile, !scope->claimed);
        const bool emitted = active && Clock::now() < scope->observed.expires;
        lua.set_bool(active);
        lua.set_bool(active && scope->claimed);
        lua.set_integer(emitted ? static_cast<std::int64_t>(scope->observed.sequence) : 0);
        lua.set_bool(emitted && scope->observed.fixture);
        return 4;
    }

    int capture(const Lua& lua)
    {
        std::array<Address, 3> supplied{};
        if (!ready() || lua.get_stack_size() != supplied.size() + 1)
            return result(lua, false, "authored_pickup_capture_arguments");
        for (auto& pointer : supplied)
        {
            if (!lua.is_integer()) return result(lua, false, "authored_pickup_capture_arguments");
            pointer = static_cast<Address>(lua.get_integer());
            if (!pointer) return result(lua, false, "authored_pickup_capture_arguments");
        }
        const auto* scope = select_source(lua);
        RowSchema rows;
        auto* pawn = live_address(supplied[0], find<U::UClass>(pawn_path));
        auto* target = object(pawn, STR("Save"));
        if (!scope || address(target) != supplied[1] || !authorized(*scope, pawn, target))
            return result(lua, false, "authored_pickup_exact_claimant_unavailable");
        if (!rows.bind()) return result(lua, false, "authored_pickup_inventory_schema_unavailable");
        if (scope->profile->stack_units)
        {
            auto* equipment = field<U::FArrayProperty>(target, STR("PlayerEquipment"));
            auto* equipment_row = equipment ? U::CastField<U::FStructProperty>(equipment->GetInner()) : nullptr;
            if (!equipment_row || equipment_row->GetStruct().Get() != rows.type)
                return result(lua, false, "authored_pickup_inventory_schema_unavailable");
            auto* item_class = scope->actor.Get()->GetClassPrivate();
            for (auto* property : {rows.array, equipment})
            {
                U::FScriptArrayHelper items(property, property->ContainerPtrToValuePtr<void>(target));
                if (items.Num() < 0 || items.Num() > 1024)
                    return result(lua, false, "authored_pickup_inventory_schema_unavailable");
                for (int index = 0; index < items.Num(); ++index)
                    if (read(rows.klass, items.GetRawPtr(index)) == item_class)
                        return result(lua, false, "authored_pickup_stock_stack_merge_unqualified");
            }
        }
        auto* scratch = isolated(supplied[2], rows);
        if (!scratch || scratch == target) return result(lua, false, "authored_pickup_isolated_escrow_required");
        Guid guid{};
        const std::string_view detail = convert(rows, pawn, scope->actor.Get(), scratch, guid);
        if (detail != "captured") return result(lua, false, detail);
        result(lua, true, detail);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        return 3;
    }

    int verify_escrow(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 3 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "authored_pickup_verify_arguments");
        const auto initial = static_cast<Address>(lua.get_integer());
        const auto reloaded = static_cast<Address>(lua.get_integer());
        const auto* scope = select_source(lua);
        RowSchema rows;
        if (!scope || initial == reloaded || !available(*scope) || !scope->host || !rows.bind())
            return result(lua, false, "authored_pickup_verify_schema_unavailable");
        auto* item_class = find<U::UClass>(scope->profile->item_class);
        auto* first = isolated(initial, rows);
        auto* second = isolated(reloaded, rows);
        if (!first || !second) return result(lua, false, "authored_pickup_isolated_escrow_required");
        U::FScriptArrayHelper a(rows.array, rows.array->ContainerPtrToValuePtr<void>(first));
        U::FScriptArrayHelper b(rows.array, rows.array->ContainerPtrToValuePtr<void>(second));
        const bool ok = a.Num() == 1 && b.Num() == 1 && a.GetRawPtr(0) != b.GetRawPtr(0) &&
            rows.authored(a.GetRawPtr(0), item_class) && rows.identical(a.GetRawPtr(0), b.GetRawPtr(0));
        return result(lua, ok, ok ? "identical" : "authored_pickup_escrow_payload_changed");
    }

    int probe(const Lua& lua)
    {
        auto* scope = ready() && lua.get_stack_size() == 1 ? select_source(lua) : nullptr;
        if (!scope || fixture_scope || !headless() || (scope->host && !env("SILVERFISH_COOP_NATIVE_FIXTURES")) ||
            !admit_input(*scope, scope->pawn.Get(), scope->box.Get()))
            return result(lua, false, "authored_pickup_fixture_unavailable");
        auto* fn = interaction();
        if (!fn) return result(lua, false, "authored_pickup_fixture_schema_unavailable");
        auto* actor = scope->actor.Get();
        const auto before = sequence;
        std::array arguments{scope->pawn.Get(), scope->box.Get()};
        fixture_scope = scope;
        actor->ProcessEvent(fn, arguments.data());
        fixture_scope = nullptr;
        const bool ok = sequence == before + 1 && scope->observed.fixture && available(*scope);
        return result(lua, ok, ok ? "authored_pickup_stock_event_vetoed" : "authored_pickup_fixture_unverified");
    }

    int reset(const Lua&)
    {
        if (U::IsInGameThreadRaw()) clear();
        return 0;
    }
}

namespace silverfish::native_authored_pickup
{
    void install_hooks()
    {
        if (!env("SILVERFISH_COOP_NATIVE_ZONE") || installed.load(std::memory_order_acquire)) return;
        callbacks[0] = Hook::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* context, U::UFunction* fn, void* parms) {
                intercept(call, context, fn, parms, nullptr);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup event")});
        callbacks[1] = Hook::RegisterProcessLocalScriptFunctionPreCallback(
            [](Iteration& call, U::UObject* context, U::FFrame& frame, void*) {
                intercept(call, context, frame.Node(), frame.Locals(), &frame);
            }, {false, false, STR("SilverfishCoopNative"), STR("Qualify authored loose pickup script")});
        const bool ok = std::ranges::all_of(callbacks, [](auto id) { return id != Hook::ERROR_ID; });
        if (!ok) uninstall_hooks();
        installed.store(ok, std::memory_order_release);
    }

    void uninstall_hooks()
    {
        clear_scope();
        installed.store(false, std::memory_order_release);
        for (auto& id : callbacks)
        {
            if (id != Hook::ERROR_ID) Hook::UnregisterCallback(id);
            id = Hook::ERROR_ID;
        }
    }

    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishSetNativeAuthoredPickupScope", set_scope);
        lua.register_function("SilverfishTakeNativeAuthoredPickupPress", take_press);
        lua.register_function("SilverfishProjectNativeAuthoredPickup", project);
        lua.register_function("SilverfishReadNativeAuthoredPickup", status);
        lua.register_function("SilverfishCaptureNativeAuthoredPickup", capture);
        lua.register_function("SilverfishVerifyNativeAuthoredPickup", verify_escrow);
        lua.register_function("SilverfishExchangeStoneMazeTape", exchange_tape<stone_maze_tape>);
        lua.register_function("SilverfishExchangeEatherPumpTape", exchange_tape<eather_pump_tape>);
        lua.register_function("SilverfishClearNativeAuthoredPickup", reset);
        if (headless()) lua.register_function("SilverfishProbeNativeAuthoredPickup", probe);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) clear();
    }
}
