#include "guest_stack_drop.hpp"
#include "guest_regen.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <format>
#include <memory>
#include <string>
#include <string_view>

#include <Helpers/String.hpp>
#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/FFrame.hpp>
#include <Unreal/FWeakObjectPtr.hpp>
#include <Unreal/Hooks.hpp>
#include <Unreal/Property/FEnumProperty.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    namespace H = U::Hook;
    using Iteration = H::TCallbackIterationData<void>;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;
    using Guid = std::array<std::uint32_t, 4>;
    constexpr auto area_path = STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto game_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto ammo_path = STR("/Game/Classes/Items/Ammo_Pistol_BP.Ammo_Pistol_BP_C");
    constexpr auto item_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto row_path = STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto spawn_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:SpawnItemActor");
    constexpr auto converter_path = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto converter_default = STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto transform_path = STR("/Script/CoreUObject.Transform");
    constexpr int max_items = 4096;
    constexpr int max_rounds = 200;

    template<class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* result = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return result && result->GetArrayDim() == 1 ? result : nullptr;
    }

    template<class T> T* parameter(U::UFunction* fn, const U::TCHAR* name, int offset, bool output = false)
    {
        auto* prop = field<T>(fn, name);
        return prop && prop->HasAnyPropertyFlags(U::CPF_Parm) && !prop->HasAnyPropertyFlags(U::CPF_ReturnParm) &&
            prop->HasAnyPropertyFlags(U::CPF_OutParm) == output && prop->GetOffset_Internal() == offset &&
            offset + prop->GetSize() <= fn->GetParmsSize() ? prop : nullptr;
    }

    U::UObject* object_value(U::FObjectProperty* prop, const void* owner)
    {
        return prop && owner ? prop->GetObjectPropertyValue(prop->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name) { return object_value(field<U::FObjectProperty>(owner, name), owner); }

    bool live(U::UObject* value)
    {
        constexpr auto flags = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        return value && !value->IsUnreachable() && !value->HasAnyFlags(flags);
    }

    bool matches(U::UObject* object, Address address) { return reinterpret_cast<Address>(object) == address; }

    bool boolean(U::UObject* owner, const U::TCHAR* name, bool expected)
    {
        auto* value = field<U::FBoolProperty>(owner, name);
        return value && value->GetPropertyValueInContainer(owner) == expected;
    }

    bool byte(U::UObject* owner, const U::TCHAR* name, std::uint8_t expected)
    {
        auto* value = field<U::FByteProperty>(owner, name);
        return value && value->GetPropertyValueInContainer(owner) == expected;
    }

    bool env(const char* name)
    {
        std::array<char, 2> value{};
        std::size_t length{};
        return getenv_s(&length, value.data(), value.size(), name) == 0 && length == value.size() && value[0] == '1';
    }

    bool enabled()
    {
        return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_ZONE") &&
            env("SILVERFISH_COOP_NATIVE_FIXTURES") && env("SILVERFISH_COOP_HEADLESS");
    }

    Guid parse_guid(std::string_view value)
    {
        if (value.size() != 32 || !std::ranges::all_of(value, [](char c) {
            return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'F');
        })) return {};
        Guid result{};
        for (std::size_t i = 0; i < result.size(); ++i)
        {
            const auto* begin = value.data() + i * 8;
            const auto [end, error] = std::from_chars(begin, begin + 8, result[i], 16);
            if (error != std::errc{} || end != begin + 8) return {};
        }
        return result;
    }

    struct FreeValue
    {
        U::FProperty* property{};
        void operator()(void* value) const { property->DestroyAndFreeValue(value); }
    };
    using Value = std::unique_ptr<void, FreeValue>;

    Value copy_value(U::FProperty* property, const void* source)
    {
        Value result(property->AllocateAndInitializeValue(), {property});
        if (result) property->CopyCompleteValue(result.get(), source);
        return result;
    }

    struct Arguments
    {
        alignas(16) std::array<std::byte, 256> storage{};
        U::FProperty* constructed{};

        void initialize(U::FProperty* field)
        {
            field->InitializeValue_InContainer(storage.data());
            constructed = field;
        }

        ~Arguments() { if (constructed) constructed->DestroyValue_InContainer(storage.data()); }
    };

    struct Inventory
    {
        U::UClass* save_class = find<U::UClass>(save_path);
        U::UScriptStruct* type = find<U::UScriptStruct>(row_path);
        U::FArrayProperty* pack = field<U::FArrayProperty>(save_class, STR("PlayerBackpack"));
        U::FArrayProperty* equipment = field<U::FArrayProperty>(save_class, STR("PlayerEquipment"));
        U::FStructProperty* armor = field<U::FStructProperty>(save_class, STR("PlayerArmor"));
        U::FStructProperty* helmet = field<U::FStructProperty>(save_class, STR("PlayerHelmet"));
        U::FStructProperty* row = pack ? U::CastField<U::FStructProperty>(pack->GetInner()) : nullptr;
        U::FClassProperty* klass = field<U::FClassProperty>(type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        U::FIntProperty* quantity = field<U::FIntProperty>(type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        U::FBoolProperty* never_drop = field<U::FBoolProperty>(type, STR("NeverDrop_21_57B44A714ED0B2B1776A5DA7627B4A8D"));
        U::FStructProperty* guid = field<U::FStructProperty>(type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        U::FStructProperty* data_struct = field<U::FStructProperty>(type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));

        bool row_type(U::FProperty* property) const
        {
            auto* structure = property ? U::CastField<U::FStructProperty>(property) : nullptr;
            return type && structure && structure->GetStruct().Get() == type && structure->GetSize() == 0x50;
        }

        bool bound() const
        {
            auto* guid_type = find<U::UScriptStruct>(STR("/Script/CoreUObject.Guid"));
            auto* payload_type = find<U::UScriptStruct>(STR("/Script/StructUtils.InstancedStruct"));
            return save_class && row_type(row) && equipment && row_type(equipment->GetInner()) && row_type(armor) &&
                row_type(helmet) && klass && klass->GetMetaClass().Get() == find<U::UClass>(item_path) &&
                quantity && never_drop && guid && guid_type && guid->GetStruct().Get() == guid_type &&
                guid->GetSize() == sizeof(Guid) && data_struct && payload_type && data_struct->GetStruct().Get() == payload_type;
        }

        auto stores() const { return std::array<U::FProperty*, 4>{pack, equipment, armor, helmet}; }

        Guid identity(const void* entry) const
        {
            Guid result{};
            std::memcpy(result.data(), guid->ContainerPtrToValuePtr<void>(entry), sizeof(result));
            return result;
        }

        const void* exact_row(U::UObject* save, const Guid& id) const
        {
            U::FScriptArrayHelper bag(pack, pack->ContainerPtrToValuePtr<void>(save));
            U::FScriptArrayHelper belt(equipment, equipment->ContainerPtrToValuePtr<void>(save));
            if (bag.Num() < 1 || bag.Num() > max_items || belt.Num() < 0 || belt.Num() > max_items ||
                identity(armor->ContainerPtrToValuePtr<void>(save)) == id ||
                identity(helmet->ContainerPtrToValuePtr<void>(save)) == id) return nullptr;
            for (int i = 0; i < belt.Num(); ++i)
                if (identity(belt.GetRawPtr(i)) == id) return nullptr;
            const void* result{};
            for (int i = 0; i < bag.Num(); ++i)
                if (const auto* entry = bag.GetRawPtr(i); identity(entry) == id)
                {
                    if (result) return nullptr;
                    result = entry;
                }
            return result;
        }

        bool same_row(const void* left, const void* right) const
        {
            return left && right && row->Identical(left, right) && data_struct->Identical(
                data_struct->ContainerPtrToValuePtr<void>(left), data_struct->ContainerPtrToValuePtr<void>(right));
        }
    };

    struct Owners
    {
        U::UObject *pawn{}, *detached{}, *frozen{}, *game{}, *stock{};
        U::UWorld* world{};

        bool current() const
        {
            auto* health = field<U::FDoubleProperty>(detached, STR("PlayerHealth"));
            return live(frozen) && live(game) && live(pawn) && live(detached) && live(stock) && health &&
                object(world, STR("OwningGameInstance")) == game && object(game, STR("SaveSlot_Ref")) == stock &&
                static_cast<U::AActor*>(pawn)->GetWorld() == world && boolean(pawn, STR("Dead"), false) &&
                std::isfinite(health->GetPropertyValueInContainer(detached)) && health->GetPropertyValueInContainer(detached) > 0 &&
                silverfish::native_guest_regen::qualifies_detached_guest(pawn, detached, stock);
        }
    };

    Owners owners(Address pawn_id, Address detached_id, Address frozen_id, const Inventory& inventory)
    {
        Owners result;
        auto* pawn_class = find<U::UClass>(pawn_path);
        auto* game_class = find<U::UClass>(game_path);
        bool frozen_attached = false;
        U::UObjectGlobals::ForEachUObject([&](U::UObject* value, int, int) {
            if (!live(value)) return RC::LoopAction::Continue;
            auto* klass = value->GetClassPrivate();
            if (klass == pawn_class)
            {
                frozen_attached |= matches(object(value, STR("Save")), frozen_id);
                if (matches(value, pawn_id)) result.pawn = value;
            }
            else if (klass == game_class)
                frozen_attached |= matches(object(value, STR("SaveSlot_Ref")), frozen_id);
            else if (klass == inventory.save_class && matches(value, frozen_id))
                result.frozen = value;
            return RC::LoopAction::Continue;
        });
        if (!result.pawn || !result.frozen || frozen_attached || !result.frozen->GetOuterPrivate() ||
            result.frozen->GetOuterPrivate()->GetPathName() != STR("/Engine/Transient")) return {};
        result.world = static_cast<U::AActor*>(result.pawn)->GetWorld();
        result.detached = object(result.pawn, STR("Save"));
        result.game = object(result.world, STR("OwningGameInstance"));
        result.stock = object(result.game, STR("SaveSlot_Ref"));
        if (!matches(result.detached, detached_id) || !live(result.game) || result.game->GetClassPrivate() != game_class ||
            result.world != find<U::UWorld>(area_path) || !result.current()) return {};
        return result;
    }

    struct ProbeScope
    {
        U::FWeakObjectPtr game, world, actor;
        U::UClass* ammo{};
        U::UFunction* spawn{};
        U::FObjectProperty* newborn{};
        U::FFrame* frame{};
        bool active{}, saw_stock_frame{};
    } scope;
    std::array hooks{H::ERROR_ID, H::ERROR_ID, H::ERROR_ID};

    void protect(Iteration& call, U::UObject* receiver, U::UFunction* function, const void* args)
    {
        if (!scope.active || !function || !U::IsInGameThreadRaw() || call.OriginalFunctionCallPrevented()) return;
        auto* guarded = scope.actor.Get();
        if (!guarded && scope.frame) guarded = object_value(scope.newborn, scope.frame->Locals());
        if (!live(guarded) || guarded->GetClassPrivate() != scope.ammo ||
            static_cast<U::AActor*>(guarded)->GetWorld() != scope.world.Get()) return;
        const auto name = function->GetFName();
        static const U::FName interact{STR("SendInteractionToActor")};
        static const U::FName store{STR("PutInInventory")};
        static const U::FName pickup{STR("PickUp_Object")};
        static const U::FName add{STR("AddItemToInventory")};
        if ((receiver == guarded && (name == interact || name == store || name == pickup)) ||
            (receiver == scope.game.Get() && name == add && args &&
             function == find<U::UFunction>(STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:AddItemToInventory")) &&
             object_value(parameter<U::FObjectProperty>(function, STR("Item"), 0), args) == guarded))
            call.PreventOriginalFunctionCall();
    }

    void pre(Iteration& call, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (!scope.active || !frame.Node() || !U::IsInGameThreadRaw()) return;
        if (receiver == scope.game.Get() && frame.Node() == scope.spawn && !scope.saw_stock_frame)
        {
            scope.saw_stock_frame = true;
            scope.frame = &frame;
            return;
        }
        protect(call, receiver, frame.Node(), frame.Locals());
    }

    void post(Iteration&, U::UObject* receiver, U::FFrame& frame, void*)
    {
        if (scope.active && U::IsInGameThreadRaw() && scope.frame == &frame && receiver == scope.game.Get())
        {
            scope.actor = object_value(scope.newborn, frame.Locals());
            scope.frame = nullptr;
        }
    }

    bool install()
    {
        const auto ready = [] { return std::ranges::none_of(hooks, [](auto hook) { return hook == H::ERROR_ID; }); };
        if (ready()) return true;
        constexpr auto owner = STR("SilverfishCoopNative");
        hooks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(pre, {false, false, owner, STR("Fence one isolated stock ammo spawn")});
        hooks[1] = H::RegisterProcessLocalScriptFunctionPostCallback(post, {false, false, owner, STR("Pin the exact isolated stock ammo birth")});
        hooks[2] = H::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* receiver, U::UFunction* function, void* arguments) {
                protect(call, receiver, function, arguments);
            }, {false, false, owner, STR("Fence the isolated stock ammo actor's native events")});
        if (ready()) return true;
        silverfish::native_guest_stack_drop::uninstall_hooks();
        return false;
    }

    bool stock_function(U::UFunction* fn, U::UClass* owner, int count)
    {
        return fn && owner && fn->GetOuterPrivate() == owner && fn->GetNumParms() == count &&
            !fn->GetReturnProperty() && fn->GetParmsSize() <= sizeof(Arguments::storage) &&
            !fn->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net);
    }

    using Position = std::array<double, 3>;

    bool exact_actor(U::UObject* actor, U::UClass* klass, U::UWorld* world)
    {
        return live(actor) && actor->GetClassPrivate() == klass && !actor->HasAnyFlags(U::RF_WasLoaded) &&
            static_cast<U::AActor*>(actor)->GetWorld() == world && byte(actor, STR("Role"), 3);
    }

    struct Scene
    {
        std::array<U::FWeakObjectPtr, 256> actors{};
        std::size_t count{};

        bool collect(U::UClass* klass, U::UWorld* world)
        {
            bool complete = true;
            U::UObjectGlobals::ForEachUObject([&](U::UObject* actor, int, int) {
                if (!exact_actor(actor, klass, world) || boolean(actor, STR("bActorIsBeingDestroyed"), true))
                    return RC::LoopAction::Continue;
                if (count == actors.size())
                {
                    complete = false;
                    return RC::LoopAction::Break;
                }
                actors[count++] = actor;
                return RC::LoopAction::Continue;
            });
            return complete;
        }

        bool contains(U::UObject* actor) const
        {
            return std::any_of(actors.begin(), actors.begin() + count, [actor](const auto& saved) { return saved.Get() == actor; });
        }
    };

    bool lower_arrow(U::UObject* pawn, void* destination, Position& origin)
    {
        auto* arrow = object(pawn, STR("Arrow_Lower"));
        auto* fn = find<U::UFunction>(STR("/Script/Engine.SceneComponent:K2_GetComponentToWorld"));
        auto* output = fn ? U::CastField<U::FStructProperty>(fn->GetReturnProperty()) : nullptr;
        auto* transform = find<U::UScriptStruct>(transform_path);
        auto* translation = field<U::FStructProperty>(transform, STR("Translation"));
        auto* scale = field<U::FStructProperty>(transform, STR("Scale3D"));
        auto* vector = find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"));
        if (!live(arrow) || arrow->GetClassPrivate() != find<U::UClass>(STR("/Script/Engine.ArrowComponent")) ||
            arrow->GetOuterPrivate() != pawn || !fn || fn->GetNumParms() != 1 || fn->GetParmsSize() != 0x60 ||
            !fn->HasAnyFunctionFlags(U::FUNC_Native) || !output || !transform || output->GetStruct().Get() != transform ||
            output->GetOffset_Internal() != 0 || !translation || !scale || !vector ||
            translation->GetStruct().Get() != vector || translation->GetOffset_Internal() != 0x20 ||
            scale->GetStruct().Get() != vector || scale->GetOffset_Internal() != 0x40) return false;
        arrow->ProcessEvent(fn, destination);
        std::memcpy(origin.data(), translation->ContainerPtrToValuePtr<void>(destination), sizeof(origin));
        constexpr Position unit{1, 1, 1};
        std::memcpy(scale->ContainerPtrToValuePtr<void>(destination), unit.data(), sizeof(unit));
        return std::ranges::all_of(origin, [](auto axis) { return std::isfinite(axis) && std::abs(axis) < 1e8; });
    }

    bool near_arrow(U::UObject* actor, const Position& origin, Position& result)
    {
        auto* call = find<U::UFunction>(STR("/Script/Engine.Actor:K2_GetActorLocation"));
        auto* output = call ? U::CastField<U::FStructProperty>(call->GetReturnProperty()) : nullptr;
        if (!call || !call->HasAnyFunctionFlags(U::FUNC_Native) || call->GetNumParms() != 1 ||
            call->GetParmsSize() != sizeof(Position) || !output || output->GetOffset_Internal() ||
            output->GetStruct().Get() != find<U::UScriptStruct>(STR("/Script/CoreUObject.Vector"))) return false;
        actor->ProcessEvent(call, result.data());
        return std::hypot(result[0] - origin[0], result[1] - origin[1], result[2] - origin[2]) <= 180;
    }

    Value converted(const Inventory& inventory, U::UObject* actor, U::UObject* pawn)
    {
        auto* library = find<U::UObject>(converter_default);
        auto* fn = find<U::UFunction>(converter_path);
        auto* input = parameter<U::FObjectProperty>(fn, STR("ItemActor"), 0);
        auto* context = parameter<U::FObjectProperty>(fn, STR("__WorldContext"), 8);
        auto* output = parameter<U::FStructProperty>(fn, STR("Item"), 0x10, true);
        if (!library || !library->HasAnyFlags(U::RF_ClassDefaultObject) || !stock_function(fn, library->GetClassPrivate(), 3) ||
            !input || !context || !inventory.row_type(output)) return {};
        Arguments args;
        args.initialize(output);
        auto* buffer = args.storage.data();
        input->SetObjectPropertyValue(input->ContainerPtrToValuePtr<void>(buffer), actor);
        context->SetObjectPropertyValue(context->ContainerPtrToValuePtr<void>(buffer), pawn);
        library->ProcessEvent(fn, buffer);
        return copy_value(inventory.row, output->ContainerPtrToValuePtr<void>(buffer));
    }

    bool retired(U::UObject* item, U::UWorld* world)
    {
        if (!live(item) || boolean(item, STR("bActorIsBeingDestroyed"), true)) return true;
        auto* destroy = find<U::UFunction>(STR("/Script/Engine.Actor:K2_DestroyActor"));
        if (!destroy || !destroy->HasAnyFunctionFlags(U::FUNC_Native) || destroy->GetNumParms() ||
            destroy->GetParmsSize() || static_cast<U::AActor*>(item)->GetWorld() != world) return false;
        const U::FWeakObjectPtr pending{item};
        item->ProcessEvent(destroy, nullptr);
        auto* remaining = pending.Get();
        return !live(remaining) || boolean(remaining, STR("bActorIsBeingDestroyed"), true);
    }

    int reply(const Lua& lua, bool ok, std::string_view status)
    {
        lua.set_bool(ok);
        lua.set_string(status);
        return 2;
    }

    int probe(const Lua& lua)
    {
        if (!enabled() || !U::IsInGameThreadRaw() || scope.active || lua.get_stack_size() != 5 ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4) || !lua.is_integer(5))
            return reply(lua, false, "native_guest_stack_drop_scope_unavailable");
        const auto pawn = lua.get_integer(), detached = lua.get_integer(), frozen = lua.get_integer();
        const auto id = parse_guid(lua.get_string());
        const auto rounds = lua.get_integer();
        if (pawn <= 0 || detached <= 0 || frozen <= 0 || frozen == detached ||
            id == Guid{} || rounds < 1 || rounds > max_rounds)
            return reply(lua, false, "native_guest_stack_drop_arguments_unqualified");
        Inventory inventory;
        if (!inventory.bound()) return reply(lua, false, "native_guest_stack_drop_inventory_schema");
        const auto owner = owners(pawn, detached, frozen, inventory);
        if (!owner.pawn) return reply(lua, false, "native_guest_stack_drop_exact_owner_unavailable");
        auto* ammo = find<U::UClass>(ammo_path);
        auto* defaults = ammo ? ammo->GetClassDefaultObject().Get() : nullptr;
        const auto* row = inventory.exact_row(owner.frozen, id);
        if (!ammo || !defaults ||
            !defaults->HasAnyFlags(U::RF_ClassDefaultObject) || !boolean(defaults, STR("Stacks"), true) ||
            !boolean(defaults, STR("DespawnIfPlayerOwns"), false) || !boolean(defaults, STR("bReplicates"), false))
            return reply(lua, false, "native_guest_stack_drop_stock_9mm_default_flags_unqualified");
        if (!row || !inventory.same_row(row, inventory.exact_row(owner.detached, id)) ||
            inventory.klass->GetObjectPropertyValue(inventory.klass->ContainerPtrToValuePtr<void>(row)) != ammo ||
            inventory.never_drop->GetPropertyValueInContainer(row) || inventory.quantity->GetPropertyValueInContainer(row) != rounds)
            return reply(lua, false, "native_guest_stack_drop_stock_9mm_row_unqualified");

        std::array<Value, 4> stock_before;
        auto stores = inventory.stores();
        for (std::size_t i = 0; i < stores.size(); ++i)
        {
            auto* store = stores[i];
            const auto* guest_frame = store->ContainerPtrToValuePtr<void>(owner.frozen);
            if (auto* array = U::CastField<U::FArrayProperty>(store))
            {
                std::array<const void*, 3> first_rows{};
                const std::array saves{owner.frozen, owner.detached, owner.stock};
                for (std::size_t index = 0; index < saves.size(); ++index)
                {
                    U::FScriptArrayHelper rows(array, array->ContainerPtrToValuePtr<void>(saves[index]));
                    if (rows.Num() < 0 || rows.Num() > max_items)
                        return reply(lua, false, "native_guest_stack_drop_inventory_bounds");
                    if (rows.Num() > 0)
                    {
                        first_rows[index] = rows.GetRawPtr(0);
                        if (std::find(first_rows.begin(), first_rows.begin() + index, first_rows[index]) != first_rows.begin() + index)
                            return reply(lua, false, "native_guest_stack_drop_inventory_storage_alias");
                    }
                }
            }
            if (!store->Identical(guest_frame, store->ContainerPtrToValuePtr<void>(owner.detached)))
                return reply(lua, false, "native_guest_stack_drop_frozen_inventory_changed");
            stock_before[i] = copy_value(store, store->ContainerPtrToValuePtr<void>(owner.stock));
            if (!stock_before[i]) return reply(lua, false, "native_guest_stack_drop_stock_inventory_snapshot");
        }

        auto* spawn = find<U::UFunction>(spawn_path);
        auto* item = parameter<U::FStructProperty>(spawn, STR("Item"), 0);
        auto* transform = parameter<U::FStructProperty>(spawn, STR("SpawnTransform"), 0x50);
        auto* collision = parameter<U::FEnumProperty>(spawn, STR("CollisionHandlingOverride"), 0xB0);
        auto* mode = parameter<U::FByteProperty>(spawn, STR("Item_RespawnMode"), 0xB1);
        auto* instigator = parameter<U::FObjectProperty>(spawn, STR("Instigator"), 0xB8);
        auto* actor_owner = parameter<U::FObjectProperty>(spawn, STR("Owner"), 0xC0);
        auto* output = parameter<U::FObjectProperty>(spawn, STR("Actor"), 0xC8, true);
        auto* newborn = field<U::FObjectProperty>(spawn, STR("CallFunc_BeginDeferredActorSpawnFromClass_ReturnValue"));
        auto* collision_byte = collision ? U::CastField<U::FByteProperty>(collision->GetUnderlyingProperty()) : nullptr;
        if (!stock_function(spawn, owner.game->GetClassPrivate(), 7) || !inventory.row_type(item) || !transform ||
            transform->GetSize() != 0x60 || transform->GetStruct().Get() != find<U::UScriptStruct>(transform_path) ||
            !collision_byte || collision->GetEnum().Get() != find<U::UEnum>(STR("/Script/Engine.ESpawnActorCollisionHandlingMethod")) ||
            !mode || !instigator || !actor_owner || !output || !newborn || newborn->GetOffset_Internal() != 0xE0 ||
            newborn->HasAnyPropertyFlags(U::CPF_Parm) || !install())
            return reply(lua, false, "native_guest_stack_drop_spawn_schema");

        Arguments args;
        Position origin{};
        auto* buffer = args.storage.data();
        if (!lower_arrow(owner.pawn, transform->ContainerPtrToValuePtr<void>(buffer), origin))
            return reply(lua, false, "native_guest_stack_drop_exact_arrow_unavailable");
        args.initialize(item);
        item->CopyCompleteValue(item->ContainerPtrToValuePtr<void>(buffer), row);
        collision_byte->SetPropertyValue(collision->ContainerPtrToValuePtr<void>(buffer), 2);
        mode->SetPropertyValueInContainer(buffer, 0);
        instigator->SetObjectPropertyValue(instigator->ContainerPtrToValuePtr<void>(buffer), nullptr);
        actor_owner->SetObjectPropertyValue(actor_owner->ContainerPtrToValuePtr<void>(buffer), nullptr);
        Scene before;
        if (!before.collect(ammo, owner.world) || before.count == before.actors.size())
            return reply(lua, false, "native_guest_stack_drop_scene_bounds");
        scope = {owner.game, owner.world, {}, ammo, spawn, newborn, nullptr, true, false};
        owner.game->ProcessEvent(spawn, buffer);
        scope.frame = nullptr;

        auto* spawned = object_value(output, buffer);
        auto* birth = scope.actor.Get();
        if (!birth && exact_actor(spawned, ammo, owner.world) && !before.contains(spawned)) scope.actor = spawned;
        Scene after;
        if (!after.collect(ammo, owner.world)) return reply(lua, false, "mutation_unverified_native_guest_stack_drop_stock_census");
        U::UObject* added{};
        for (std::size_t i = 0; i < after.count; ++i)
        {
            auto* actor = after.actors[i].Get();
            if (!actor || before.contains(actor)) continue;
            if (added) return reply(lua, false, "mutation_unverified_native_guest_stack_drop_multiple_births");
            added = actor;
        }
        if (added) scope.actor = added;
        Position location{};
        std::string_view qualification;
        if (!added) qualification = "native_guest_stack_drop_no_stock_birth";
        else if (!scope.saw_stock_frame || spawned != added || birth != added)
            qualification = "native_guest_stack_drop_stock_birth_frame_unqualified";
        else if (!near_arrow(spawned, origin, location)) qualification = "native_guest_stack_drop_stock_transform_unqualified";
        else if (object(spawned, STR("As Game Instance FP")) != owner.game || object(spawned, STR("Owner")) ||
            object(spawned, STR("Instigator")) || !byte(spawned, STR("Item_RespawnMode"), 0) ||
            !boolean(spawned, STR("Stacks"), true) || !boolean(spawned, STR("Physics"), true) || !boolean(spawned, STR("bReplicates"), false))
            qualification = "native_guest_stack_drop_stock_actor_flags_unqualified";
        auto reflected = qualification.empty() ? converted(inventory, spawned, owner.pawn) : Value{};
        const auto fresh = reflected ? inventory.identity(reflected.get()) : Guid{};
        if (reflected)
            inventory.guid->CopyCompleteValue(inventory.guid->ContainerPtrToValuePtr<void>(reflected.get()),
                inventory.guid->ContainerPtrToValuePtr<void>(row));
        if (qualification.empty() && (!reflected || fresh == Guid{} || fresh == id))
            qualification = "native_guest_stack_drop_stock_converter_guid_unqualified";
        if (qualification.empty() && !inventory.same_row(reflected.get(), row))
            qualification = "native_guest_stack_drop_stock_converter_payload_changed";
        const auto name = qualification.empty() ? RC::to_utf8_string(spawned->GetFullName()) : std::string{};
        if (added && !retired(added, owner.world)) return reply(lua, false, "mutation_unverified_native_guest_stack_drop_retirement");
        if ((live(spawned) && spawned != added) || (live(birth) && birth != added))
            return reply(lua, false, "mutation_unverified_native_guest_stack_drop_birth_identity");
        Scene final;
        if (!final.collect(ammo, owner.world) || final.count != before.count ||
            !std::all_of(final.actors.begin(), final.actors.begin() + final.count, [&](const auto& item) { return before.contains(item.Get()); }))
            return reply(lua, false, "mutation_unverified_native_guest_stack_drop_scene_readback");
        scope = {};
        for (std::size_t i = 0; i < stores.size(); ++i)
        {
            auto* store = stores[i];
            if (!store->Identical(stock_before[i].get(), store->ContainerPtrToValuePtr<void>(owner.stock)) ||
                !store->Identical(store->ContainerPtrToValuePtr<void>(owner.frozen), store->ContainerPtrToValuePtr<void>(owner.detached)))
                return reply(lua, false, "mutation_unverified_native_guest_stack_drop_inventory_changed");
        }
        if (!owner.current()) return reply(lua, false, "mutation_unverified_native_guest_stack_drop_owner_changed");
        if (!qualification.empty()) return reply(lua, false, qualification);
        lua.set_bool(true);
        lua.set_string("stock_spawn_verified_and_retired");
        lua.set_string(name);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", fresh[0], fresh[1], fresh[2], fresh[3]));
        lua.set_integer(rounds);
        for (auto axis : location) lua.set_number(axis);
        return 8;
    }
}

void silverfish::native_guest_stack_drop::register_lua(Lua& lua)
{
    if (enabled()) lua.register_function("SilverfishProbeNativeGuestStackDrop", probe);
}

void silverfish::native_guest_stack_drop::uninstall_hooks()
{
    for (auto& hook : hooks)
    {
        if (hook != H::ERROR_ID) H::UnregisterCallback(hook);
        hook = H::ERROR_ID;
    }
    scope = {};
}
