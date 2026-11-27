#include "guest_stack_drop.hpp"
#include "drop_item_class.hpp"
#include "guest_drop_items.hpp"
#include "guest_drop_maps.hpp"
#include "guest_item.hpp"
#include "guest_regen.hpp"
#include "interaction_input.hpp"

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
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto game_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto weapon_base_path = STR("/Game/Classes/Weapons/Weapon_Master_BP.Weapon_Master_BP_C");
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
        return env("SILVERFISH_COOP_NATIVE_GUEST_SAVE") && env("SILVERFISH_COOP_NATIVE_ZONE");
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

        const void* exact_row(U::UObject* save, const Guid& id, int slot) const
        {
            U::FScriptArrayHelper bag(pack, pack->ContainerPtrToValuePtr<void>(save));
            U::FScriptArrayHelper belt(equipment, equipment->ContainerPtrToValuePtr<void>(save));
            if (bag.Num() < 0 || bag.Num() > max_items || belt.Num() < 0 || belt.Num() > max_items) return nullptr;
            const void* result{};
            for (int i = 0; i < belt.Num(); ++i)
                if (identity(belt.GetRawPtr(i)) == id)
                {
                    if (slot != i || slot >= 8) return nullptr;
                    result = belt.GetRawPtr(i);
                }
            const auto* head = helmet->ContainerPtrToValuePtr<void>(save);
            const auto* chest = armor->ContainerPtrToValuePtr<void>(save);
            if (identity(head) == id)
            {
                if (result || slot != 8) return nullptr;
                result = head;
            }
            if (identity(chest) == id)
            {
                if (result || slot != 9) return nullptr;
                result = chest;
            }
            for (int i = 0; i < bag.Num(); ++i)
                if (const auto* entry = bag.GetRawPtr(i); identity(entry) == id)
                {
                    if (result || slot >= 0) return nullptr;
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
        U::UObject *pawn{}, *live_save{}, *frozen{}, *game{}, *stock{};
        U::UWorld* world{};

        bool canonical_host() const
        {
            if (!byte(pawn, STR("Role"), 3) ||
                silverfish::native_interaction_input::current_local_pawn(reinterpret_cast<Address>(pawn)) != pawn)
                return false;

            bool shared_save = false;
            const auto* pawn_class = pawn->GetClassPrivate();
            const auto* game_class = game->GetClassPrivate();
            U::UObjectGlobals::ForEachUObject([&](U::UObject* value, int, int) {
                if (!live(value)) return RC::LoopAction::Continue;
                const auto* klass = value->GetClassPrivate();
                if ((klass == pawn_class && value != pawn && object(value, STR("Save")) == stock) ||
                    (klass == game_class && value != game && object(value, STR("SaveSlot_Ref")) == stock))
                {
                    shared_save = true;
                    return RC::LoopAction::Break;
                }
                return RC::LoopAction::Continue;
            });
            return !shared_save;
        }

        bool current() const
        {
            if (!live(frozen) || !live(game) || !live(pawn) || !live(live_save) || !live(stock) || !live(world) ||
                object(world, STR("OwningGameInstance")) != game || object(game, STR("SaveSlot_Ref")) != stock ||
                object(pawn, STR("Save")) != live_save || static_cast<U::AActor*>(pawn)->GetWorld() != world ||
                !boolean(pawn, STR("Dead"), false)) return false;
            auto* health = field<U::FDoubleProperty>(live_save, STR("PlayerHealth"));
            if (!health) return false;
            const double hp = health->GetPropertyValueInContainer(live_save);
            return std::isfinite(hp) && hp > 0 && (live_save == stock ? canonical_host() :
                silverfish::native_guest_regen::qualifies_detached_guest(pawn, live_save, stock));
        }
    };

    Owners owners(Address pawn_id, Address save_id, Address frozen_id, const Inventory& inventory)
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
        result.live_save = object(result.pawn, STR("Save"));
        result.game = object(result.world, STR("OwningGameInstance"));
        result.stock = object(result.game, STR("SaveSlot_Ref"));
        if (!matches(result.live_save, save_id) || !live(result.live_save) || !live(result.stock) ||
            result.live_save->GetClassPrivate() != inventory.save_class || result.stock->GetClassPrivate() != inventory.save_class ||
            !live(result.game) || result.game->GetClassPrivate() != game_class ||
            !silverfish::native_guest_drop_maps::stock_map(result.world, false) || !result.current()) return {};
        return result;
    }

    struct ProbeScope
    {
        U::FWeakObjectPtr game, world, actor;
        U::UClass* type{};
        U::UFunction *spawn{}, *item_tick{};
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
        if (!live(guarded) || guarded->GetClassPrivate() != scope.type ||
            static_cast<U::AActor*>(guarded)->GetWorld() != scope.world.Get()) return;
        const auto name = function->GetFName();
        static const U::FName interact{STR("SendInteractionToActor")};
        static const U::FName store{STR("PutInInventory")};
        static const U::FName pickup{STR("PickUp_Object")};
        static const U::FName add{STR("AddItemToInventory")};
        if ((receiver == guarded && (name == interact || name == store || name == pickup || function == scope.item_tick)) ||
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
        hooks[0] = H::RegisterProcessLocalScriptFunctionPreCallback(pre, {false, false, owner, STR("Fence one isolated stock item spawn")});
        hooks[1] = H::RegisterProcessLocalScriptFunctionPostCallback(post, {false, false, owner, STR("Pin the exact isolated stock item birth")});
        hooks[2] = H::RegisterProcessEventPreCallback(
            [](Iteration& call, U::UObject* receiver, U::UFunction* function, void* arguments) {
                protect(call, receiver, function, arguments);
            }, {false, false, owner, STR("Fence the isolated stock item actor's native events")});
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
        const auto arguments = lua.get_stack_size();
        if (!enabled() || !U::IsInGameThreadRaw() || scope.active ||
            (arguments != 5 && (arguments != 6 || !lua.is_integer(6))) ||
            !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) || !lua.is_string(4) || !lua.is_integer(5))
            return reply(lua, false, "native_guest_stack_drop_scope_unavailable");
        const auto slot = arguments == 6 ? lua.get_integer(6) : -1;
        const auto pawn = lua.get_integer(), save = lua.get_integer(), frozen = lua.get_integer();
        const auto id = parse_guid(lua.get_string());
        const auto count = lua.get_integer();
        if (pawn <= 0 || save <= 0 || frozen <= 0 || frozen == save ||
            id == Guid{} || count < 1 || count > max_rounds ||
            (arguments == 6 && (slot < 0 || slot > 9 || count != 1)))
            return reply(lua, false, "native_guest_stack_drop_arguments_unqualified");
        Inventory inventory;
        if (!inventory.bound()) return reply(lua, false, "native_guest_stack_drop_inventory_schema");
        const auto owner = owners(pawn, save, frozen, inventory);
        if (!owner.pawn) return reply(lua, false, "native_guest_stack_drop_exact_owner_unavailable");
        const auto* row = inventory.exact_row(owner.frozen, id, static_cast<int>(slot));
        namespace items = silverfish::native_guest_drop_items;
        auto* type = U::Cast<U::UClass>(object_value(inventory.klass, row));
        const auto stacks = silverfish::drop_item_class::stacks(type);
        if (!stacks || (slot >= 0 && *stacks) ||
            !inventory.same_row(row, inventory.exact_row(owner.live_save, id, static_cast<int>(slot))) ||
            inventory.never_drop->GetPropertyValueInContainer(row))
            return reply(lua, false, "native_guest_stack_drop_stock_item_row_unqualified");
        const bool stack = *stacks;
        const auto* display = items::find_stack(type->GetPathName());
        const bool nine_mm = display == &items::stacks.front();
        const bool food = display && display->kind == items::StackKind::food;
        const auto stored = inventory.quantity->GetPropertyValueInContainer(row);
        const bool quick = slot >= 0 && slot < 8;
        const auto ammo = quick ? silverfish::native_guest_item::quick_drop_ammo(
            owner.pawn, owner.live_save, type, static_cast<int>(slot), stored) : std::optional{stored};
        if (!ammo) return reply(lua, false, "native_guest_stack_drop_live_equipment_unqualified");
        Value updated;
        if (*ammo != stored)
        {
            updated = copy_value(inventory.row, row);
            if (!updated) return reply(lua, false, "native_guest_stack_drop_stock_inventory_snapshot");
            inventory.quantity->SetPropertyValueInContainer(updated.get(), *ammo);
            row = updated.get();
        }
        auto* defaults = type->GetClassDefaultObject().Get();
        if ((stack && stored != count) || (!stack && count != 1))
            return reply(lua, false, food ? "native_guest_stack_drop_stock_food_row_unqualified" :
                nine_mm ? "native_guest_stack_drop_stock_9mm_row_unqualified" :
                display ? "native_guest_stack_drop_stock_ammo_row_unqualified" :
                "native_guest_stack_drop_stock_item_row_unqualified");
        auto* weapon = find<U::UClass>(weapon_base_path);
        auto* magazine = weapon && type->IsChildOf(weapon) ? field<U::FIntProperty>(defaults, STR("MagSize")) : nullptr;
        const auto capacity = magazine ? magazine->GetPropertyValueInContainer(defaults) : 0;
        if (capacity > 0 && (stack || capacity > max_rounds || *ammo < 0 || *ammo > capacity ||
            !field<U::FIntProperty>(defaults, STR("Ammo"))))
            return reply(lua, false, "native_guest_stack_drop_stock_weapon_row_unqualified");
        auto* tick = type->GetFunctionByNameInChain(U::FName(STR("ReceiveTick")));
        if (tick && (!stock_function(tick, U::Cast<U::UClass>(tick->GetOuterPrivate()), 1) ||
                     !parameter<U::FFloatProperty>(tick, STR("DeltaSeconds"), 0)))
            return reply(lua, false, "native_guest_stack_drop_stock_tick_unqualified");

        std::array<Value, 4> stock_before;
        auto stores = inventory.stores();
        for (std::size_t i = 0; i < stores.size(); ++i)
        {
            auto* store = stores[i];
            const auto* frozen_frame = store->ContainerPtrToValuePtr<void>(owner.frozen);
            if (auto* array = U::CastField<U::FArrayProperty>(store))
            {
                std::array<const void*, 3> first_rows{};
                const std::array saves{owner.frozen, owner.live_save, owner.stock};
                for (std::size_t index = 0; index < saves.size(); ++index)
                {
                    // The canonical host's live save and stock save are the same object.
                    if (index == 2 && owner.live_save == owner.stock) continue;
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
            if (!store->Identical(frozen_frame, store->ContainerPtrToValuePtr<void>(owner.live_save)))
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
        scope = {owner.game, owner.world, {}, type, spawn, tick, newborn, nullptr, true, false};
        const auto release_scope = [](ProbeScope* active) { *active = {}; };
        const std::unique_ptr<ProbeScope, decltype(release_scope)> guard(&scope, release_scope);
        owner.game->ProcessEvent(spawn, buffer);
        scope.frame = nullptr;

        // The exact SpawnItemActor invocation records its deferred spawn in the
        // Blueprint frame. Pair it with that invocation's returned actor before
        // reading or retiring the item. Other actors can spawn during the call.
        auto* spawned = object_value(output, buffer);
        auto* birth = scope.actor.Get();
        const bool qualified_birth = exact_actor(birth, type, owner.world) &&
            silverfish::native_guest_drop_maps::persistent_actor(static_cast<U::AActor*>(birth), owner.world);
        Position location{};
        std::string_view qualification;
        if (scope.saw_stock_frame && !spawned && !birth) qualification = "native_guest_stack_drop_no_stock_birth";
        else if (!scope.saw_stock_frame || !qualified_birth || spawned != birth)
            qualification = "mutation_unverified_native_guest_stack_drop_stock_birth_identity";
        else if (!near_arrow(spawned, origin, location)) qualification = "native_guest_stack_drop_stock_transform_unqualified";
        else if (object(spawned, STR("As Game Instance FP")) != owner.game || object(spawned, STR("Owner")) ||
            object(spawned, STR("Instigator")) || !byte(spawned, STR("Item_RespawnMode"), 0) ||
            !boolean(spawned, STR("Stacks"), stack) || !boolean(spawned, STR("Physics"), true) || !boolean(spawned, STR("bReplicates"), false))
            qualification = "native_guest_stack_drop_stock_actor_flags_unqualified";
        if (qualification.empty() && capacity > 0)
        {
            auto* ammo_property = field<U::FIntProperty>(spawned, STR("Ammo"));
            if (!ammo_property || ammo_property->GetPropertyValueInContainer(spawned) != *ammo)
                qualification = "native_guest_stack_drop_stock_weapon_magazine_changed";
        }
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
        if (qualified_birth && !retired(birth, owner.world))
            return reply(lua, false, "mutation_unverified_native_guest_stack_drop_retirement");
        scope = {};
        for (std::size_t i = 0; i < stores.size(); ++i)
        {
            auto* store = stores[i];
            if (!store->Identical(stock_before[i].get(), store->ContainerPtrToValuePtr<void>(owner.stock)) ||
                !store->Identical(store->ContainerPtrToValuePtr<void>(owner.frozen), store->ContainerPtrToValuePtr<void>(owner.live_save)))
                return reply(lua, false, "mutation_unverified_native_guest_stack_drop_inventory_changed");
        }
        if (!owner.current()) return reply(lua, false, "mutation_unverified_native_guest_stack_drop_owner_changed");
        if (!qualification.empty()) return reply(lua, false, qualification);
        lua.set_bool(true);
        lua.set_string("stock_spawn_verified_and_retired");
        lua.set_string(name);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", fresh[0], fresh[1], fresh[2], fresh[3]));
        lua.set_integer(count);
        for (auto axis : location) lua.set_number(axis);
        lua.set_string(RC::to_utf8_string(type->GetPathName()));
        if (quick) lua.set_integer(*ammo);
        return quick ? 10 : 9;
    }

    int generic_drop_capability(const Lua& lua)
    {
        lua.set_bool(lua.get_stack_size() == 0);
        return 1;
    }
}

void silverfish::native_guest_stack_drop::register_lua(Lua& lua)
{
    if (!enabled()) return;
    lua.register_function("SilverfishProbeNativeGuestStackDrop", probe);
    lua.register_function("SilverfishNativeGenericInventoryDrop", generic_drop_capability);
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
