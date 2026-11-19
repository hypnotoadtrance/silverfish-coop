#include "authored_pickup.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <format>
#include <memory>
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
        const U::TCHAR* world;
        const U::TCHAR* actor;
        const U::TCHAR* item_class;
        double x, y, z;
    };

    constexpr std::array profiles{
        Profile{
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P:PersistentLevel.Item_CompBoard_C_2"),
            STR("/Game/Classes/Items/Item_CompBoard.Item_CompBoard_C"),
            -5680.476689, -849.040347, 128.496287,
        },
        Profile{
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P:PersistentLevel.Item_CopperWire_C_3"),
            STR("/Game/Classes/Items/Item_CopperWire.Item_CopperWire_C"),
            1299.7165908306, 3418.79597614179, 1239.49197257439,
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
    constexpr auto library_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
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

    struct Scope
    {
        U::FWeakObjectPtr world, pawn, actor, box, controller, driver;
        const Profile* profile{};
        bool host{}, claimed{}, original_collision{}, original_hidden{};
    } scope;

    struct Observation
    {
        Clock::time_point expires{};
        std::uint64_t sequence{};
        bool fixture{};
    } observed;

    std::uint64_t sequence{};
    bool fixture_call{};
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

    const Profile* profile_for(U::UWorld* world)
    {
        if (!live(world)) return nullptr;
        const auto path = world->GetPathName();
        const auto match = std::ranges::find_if(profiles, [&path](const auto& profile) { return path == profile.world; });
        return match != profiles.end() ? &*match : nullptr;
    }

    bool exact_source(U::UObject* actor, U::UObject* world, const Profile& profile)
    {
        if (!exact(actor, profile.item_class) || actor->GetPathName() != profile.actor ||
            !boolean(actor, STR("bActorIsBeingDestroyed"), false) || !boolean(actor, STR("Physics"), false) ||
            !boolean(actor, STR("Stacks"), false)) return false;
        auto* item = static_cast<U::AActor*>(actor);
        auto* category = field<U::FByteProperty>(actor, STR("ItemType"));
        if (item->GetWorld() != world || item->GetLevel() != object(world, STR("PersistentLevel")) ||
            !category || category->GetPropertyValueInContainer(actor) != 11) return false;
        auto position = item->K2_GetActorLocation();
        const auto dx = position.GetX() - profile.x;
        const auto dy = position.GetY() - profile.y;
        const auto dz = position.GetZ() - profile.z;
        const auto squared = dx * dx + dy * dy + dz * dz;
        return std::isfinite(squared) && squared <= 25.0;
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

    bool available()
    {
        auto* actor = scope.actor.Get();
        return !scope.claimed && local_scope(scope) && exact_source(actor, scope.world.Get(), *scope.profile) &&
            source_box(actor) == scope.box.Get() && static_cast<U::AActor*>(actor)->GetActorEnableCollision() &&
            boolean(actor, STR("bHidden"), false);
    }

    bool set_visibility(bool claimed)
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (!local_scope(scope) || !exact_source(actor, scope.world.Get(), *scope.profile)) return false;
        const bool hidden = claimed || scope.original_hidden;
        const bool collision = !claimed && scope.original_collision;
        actor->SetActorHiddenInGame(hidden);
        actor->SetActorEnableCollision(collision);
        if (actor->GetActorEnableCollision() != collision || !boolean(actor, STR("bHidden"), hidden)) return false;
        scope.claimed = claimed;
        if (claimed) observed = {};
        return true;
    }

    void clear()
    {
        auto* actor = static_cast<U::AActor*>(scope.actor.Get());
        if (scope.claimed && live(actor) && boolean(actor, STR("bActorIsBeingDestroyed"), false))
        {
            actor->SetActorHiddenInGame(scope.original_hidden);
            actor->SetActorEnableCollision(scope.original_collision);
        }
        scope = {};
        observed = {};
        fixture_call = false;
    }

    bool admit_input(U::UObject* pawn, U::UObject* hit)
    {
        return pawn && pawn == scope.pawn.Get() && hit && hit == scope.box.Get() && available() &&
            boolean(pawn, STR("Dead"), false) && in_range(pawn, scope.actor.Get());
    }

    void intercept(Iteration& call, U::UObject* context, U::UFunction* function, const void* args, U::FFrame* frame)
    {
        if (!ready() || !context || !function || call.OriginalFunctionCallPrevented() || !scope.actor.Get()) return;
        static const U::FName interact_name{STR("SendInteractionToActor")};
        static const U::FName put_name{STR("PutInInventory")};
        static const U::FName pickup_name{STR("PickUp_Object")};
        static const U::FName add_name{STR("AddItemToInventory")};
        const auto name = function->GetFName();
        if (name != interact_name && name != put_name && name != pickup_name && name != add_name) return;
        auto* source = scope.actor.Get();
        if (name == add_name)
        {
            if (function == find<U::UFunction>(add_path) && context == object(scope.world.Get(), STR("OwningGameInstance")) &&
                read(parameter<U::FObjectProperty>(function, STR("Item"), 0), args) == source) call.PreventOriginalFunctionCall();
            return;
        }
        if (context != source || function->GetOuterPrivate() != find<U::UClass>(base_class)) return;
        call.PreventOriginalFunctionCall();
        if (!call.OriginalFunctionCallPrevented() || name != interact_name || function != interaction()) return;

        auto* pawn = read(field<U::FObjectProperty>(function, STR("Player")), args);
        auto* hit = read(field<U::FObjectProperty>(function, STR("HitComponent")), args);
        if (!admit_input(pawn, hit)) return;
        auto* caller = frame ? frame->PreviousFrame() : nullptr;
        const bool fixture = !frame && fixture_call;
        const bool stock = caller && caller->Object() == pawn && caller->Node() == find<U::UFunction>(pawn_script) &&
            object(pawn, STR("InteractionTarget_Component")) == hit && boolean(pawn, STR("Holding_Interact"), true);
        if (fixture || stock) observed = {Clock::now() + std::chrono::seconds(4), ++sequence, fixture};
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

    bool authorized(U::UObject* pawn, U::UObject* target)
    {
        if (!scope.host || !available() || !exact(pawn, pawn_path) || role(pawn) != 3 ||
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

    int set_scope(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_bool(2))
            return result(lua, false, "authored_pickup_scope_arguments");
        const auto ptr = static_cast<Address>(lua.get_integer());
        const auto host = lua.get_bool();
        if (address(scope.pawn.Get()) == ptr && host == scope.host && local_scope(scope))
            return result(lua, true, "authored_pickup_scope_bound");
        clear();
        auto* pawn = live_address(ptr, find<U::UClass>(pawn_path));
        auto* world = pawn ? static_cast<U::AActor*>(pawn)->GetWorld() : nullptr;
        const auto* profile = profile_for(world);
        auto* actor = profile ? find<U::AActor>(profile->actor) : nullptr;
        auto* box = source_box(actor);
        if (!profile || !box || !exact_source(actor, world, *profile) || !interaction() ||
            !actor->GetActorEnableCollision() || !boolean(actor, STR("bHidden"), false))
            return result(lua, false, "authored_pickup_stock_actor_unavailable");
        Scope next{world, pawn, actor, box, object(pawn, STR("Controller")), object(world, STR("NetDriver")),
            profile, host, false, actor->GetActorEnableCollision(), boolean(actor, STR("bHidden"), true)};
        if (!local_scope(next)) return result(lua, false, "authored_pickup_local_controller_unavailable");
        scope = next;
        return result(lua, true, "authored_pickup_scope_bound");
    }

    int focus(const Lua& lua)
    {
        auto* pawn = ready() ? scope.pawn.Get() : nullptr;
        const auto qualified = pawn && lua.get_stack_size() == 1 && lua.is_integer() &&
            static_cast<Address>(lua.get_integer()) == address(pawn) &&
            admit_input(pawn, object(pawn, STR("InteractionTarget_Component")));
        lua.set_bool(qualified);
        return 1;
    }

    int project(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 1 || !lua.is_bool()) return result(lua, false, "authored_pickup_project_arguments");
        const auto claimed = lua.get_bool();
        const bool verified = set_visibility(claimed);
        return result(lua, verified, verified ? "authored_pickup_projected" : "authored_pickup_projection_unverified");
    }

    int status(const Lua& lua)
    {
        const bool active = ready() && local_scope(scope) && exact_source(scope.actor.Get(), scope.world.Get(), *scope.profile);
        const bool emitted = active && Clock::now() < observed.expires;
        lua.set_bool(active);
        lua.set_bool(active && scope.claimed);
        lua.set_integer(emitted ? static_cast<std::int64_t>(observed.sequence) : 0);
        lua.set_bool(emitted && observed.fixture);
        return 4;
    }

    int capture(const Lua& lua)
    {
        std::array<Address, 3> supplied{};
        if (!ready() || lua.get_stack_size() != supplied.size())
            return result(lua, false, "authored_pickup_capture_arguments");
        for (auto& pointer : supplied)
        {
            if (!lua.is_integer()) return result(lua, false, "authored_pickup_capture_arguments");
            pointer = static_cast<Address>(lua.get_integer());
            if (!pointer) return result(lua, false, "authored_pickup_capture_arguments");
        }
        RowSchema rows;
        auto* pawn = live_address(supplied[0], find<U::UClass>(pawn_path));
        auto* target = object(pawn, STR("Save"));
        if (address(target) != supplied[1] || !authorized(pawn, target))
            return result(lua, false, "authored_pickup_exact_claimant_unavailable");
        if (!rows.bind()) return result(lua, false, "authored_pickup_inventory_schema_unavailable");
        auto* scratch = isolated(supplied[2], rows);
        if (!scratch || scratch == target) return result(lua, false, "authored_pickup_isolated_escrow_required");
        Guid guid{};
        const std::string_view detail = convert(rows, pawn, scope.actor.Get(), scratch, guid);
        if (detail != "captured") return result(lua, false, detail);
        result(lua, true, detail);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", guid[0], guid[1], guid[2], guid[3]));
        return 3;
    }

    int verify_escrow(const Lua& lua)
    {
        if (!ready() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
            return result(lua, false, "authored_pickup_verify_arguments");
        const auto initial = static_cast<Address>(lua.get_integer());
        const auto reloaded = static_cast<Address>(lua.get_integer());
        RowSchema rows;
        if (initial == reloaded || !available() || !scope.host || !rows.bind())
            return result(lua, false, "authored_pickup_verify_schema_unavailable");
        auto* item_class = find<U::UClass>(scope.profile->item_class);
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
        if (!ready() || !headless() || (scope.host && !env("SILVERFISH_COOP_NATIVE_FIXTURES")) ||
            lua.get_stack_size() != 0 || !admit_input(scope.pawn.Get(), scope.box.Get()))
            return result(lua, false, "authored_pickup_fixture_unavailable");
        auto* fn = interaction();
        if (!fn) return result(lua, false, "authored_pickup_fixture_schema_unavailable");
        auto* actor = scope.actor.Get();
        const auto before = sequence;
        std::array arguments{scope.pawn.Get(), scope.box.Get()};
        fixture_call = true;
        actor->ProcessEvent(fn, arguments.data());
        fixture_call = false;
        const bool ok = sequence == before + 1 && observed.fixture && available();
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
        lua.register_function("SilverfishFocusedNativeAuthoredPickup", focus);
        lua.register_function("SilverfishProjectNativeAuthoredPickup", project);
        lua.register_function("SilverfishReadNativeAuthoredPickup", status);
        lua.register_function("SilverfishCaptureNativeAuthoredPickup", capture);
        lua.register_function("SilverfishVerifyNativeAuthoredPickup", verify_escrow);
        lua.register_function("SilverfishClearNativeAuthoredPickup", reset);
        if (headless()) lua.register_function("SilverfishProbeNativeAuthoredPickup", probe);
    }

    void clear_scope()
    {
        if (U::IsInGameThreadRaw()) clear();
    }
}
