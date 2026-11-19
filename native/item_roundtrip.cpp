#include "item_roundtrip.hpp"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <format>
#include <memory>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/Core/Containers/FString.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UnrealInitializer.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace
{
    namespace Unreal = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Text = std::basic_string_view<Unreal::TCHAR>;

    constexpr auto weapon_path = STR("/Game/Classes/Weapons/Wep_SMG_UBGL.Wep_SMG_UBGL_C");
    constexpr auto item_actor_path = STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C");
    constexpr auto pawn_path = STR("/Game/Classes/Player/Player_FP.Player_FP_C");
    constexpr auto save_path = STR("/Game/Classes/Global/SaveGame_BP.SaveGame_BP_C");
    constexpr auto instance_path = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C");
    constexpr auto converter_path =
        STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.FL_Inventory_C:ItemActorToStruct");
    constexpr auto library_path =
        STR("/Game/Classes/Libraries/Fuctions_Lib/FL_Inventory.Default__FL_Inventory_C");
    constexpr auto item_struct_path =
        STR("/Game/Classes/Libraries/Structs/InventoryItem_Struct.InventoryItem_Struct");
    constexpr auto weapon_data_path =
        STR("/Game/Classes/Libraries/Structs/WeaponData_Struct.WeaponData_Struct");
    constexpr auto instanced_struct_path = STR("/Script/StructUtils.InstancedStruct");
    constexpr auto guid_struct_path = STR("/Script/CoreUObject.Guid");
    constexpr std::array guid_names{STR("A"), STR("B"), STR("C"), STR("D")};
    constexpr std::size_t parameter_capacity = 256;
    constexpr int max_text_characters = 8192;

    template <typename T>
    T* find(const Unreal::TCHAR* name)
    {
        return Unreal::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, name);
    }

    template <typename T>
    T* field(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* property = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return property && property->GetArrayDim() == 1 ? property : nullptr;
    }

    template <typename T>
    T* parameter(Unreal::UFunction* fn, const Unreal::TCHAR* name, bool output)
    {
        auto* property = field<T>(fn, name);
        if (!property || !property->HasAnyPropertyFlags(Unreal::CPF_Parm) ||
            property->HasAnyPropertyFlags(Unreal::CPF_OutParm) != output ||
            property->HasAnyPropertyFlags(Unreal::CPF_ReturnParm) ||
            (output && property->HasAnyPropertyFlags(Unreal::CPF_ConstParm)))
        {
            return nullptr;
        }
        const auto offset = property->GetOffset_Internal();
        const auto size = property->GetSize();
        return offset >= 0 && size > 0 && offset + size <= fn->GetParmsSize() ? property : nullptr;
    }

    bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(
            Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return !object->HasAnyFlags(excluded) && !object->IsUnreachable();
    }

    struct Context
    {
        Unreal::UClass* weapon = find<Unreal::UClass>(weapon_path);
        Unreal::UObject* world_context{};
        Unreal::UObject* source{};
        Unreal::UObject* scratch_owner{};
    };

    bool resolve(std::int64_t pawn_address, std::int64_t save_address, Context& context)
    {
        auto* save_class = find<Unreal::UClass>(save_path);
        auto* pawn_class = find<Unreal::UClass>(pawn_path);
        auto* instance = find<Unreal::UClass>(instance_path);
        auto* slot = field<Unreal::FObjectProperty>(instance, STR("SaveSlot_Ref"));
        auto* pawn_save = field<Unreal::FObjectProperty>(pawn_class, STR("Save"));
        if (pawn_address <= 0 || save_address <= 0 || !context.weapon || !save_class ||
            !slot || !pawn_save || slot->GetPropertyClass().Get() != save_class ||
            pawn_save->GetPropertyClass().Get() != save_class)
        {
            return false;
        }
        context.source = context.weapon->GetClassDefaultObject().Get();
        if (!context.source || context.source->GetClassPrivate() != context.weapon ||
            !context.source->HasAnyFlags(Unreal::RF_ClassDefaultObject))
        {
            return false;
        }
        bool canonical_save = false;
        Unreal::UObjectGlobals::ForEachUObject([&](Unreal::UObject* object, int, int) {
            const auto address = reinterpret_cast<std::uintptr_t>(object);
            auto* type = object->GetClassPrivate();
            if (type == instance && live(object))
            {
                const auto current = reinterpret_cast<std::uintptr_t>(
                    slot->GetObjectPropertyValue(slot->ContainerPtrToValuePtr<void>(object)));
                canonical_save |= current == static_cast<std::uintptr_t>(save_address);
            }
            else if (address == static_cast<std::uintptr_t>(pawn_address) && type == pawn_class && live(object))
            {
                context.world_context = object;
            }
            else if (address == static_cast<std::uintptr_t>(save_address) && type == save_class && live(object))
            {
                context.scratch_owner = object;
            }
            return RC::LoopAction::Continue;
        });
        return context.world_context && context.scratch_owner && !canonical_save &&
               pawn_save->GetObjectPropertyValue(pawn_save->ContainerPtrToValuePtr<void>(context.world_context)) ==
                   context.scratch_owner;
    }

    class ConversionParams
    {
    public:
        explicit ConversionParams(std::array<Unreal::FProperty*, 3> parameters) : parameters(parameters)
        {
            for (auto* property : parameters)
            {
                property->InitializeValue_InContainer(storage.data());
            }
        }

        ~ConversionParams()
        {
            for (auto* property : parameters)
            {
                property->DestroyValue_InContainer(storage.data());
            }
        }

        ConversionParams(const ConversionParams&) = delete;
        ConversionParams& operator=(const ConversionParams&) = delete;

        void* data() { return storage.data(); }

    private:
        alignas(std::max_align_t) std::array<std::byte, parameter_capacity> storage{};
        std::array<Unreal::FProperty*, 3> parameters;
    };

    int error(const Lua& lua, std::string_view message)
    {
        lua.set_bool(false);
        lua.set_string(message);
        return 2;
    }

    bool complete(const Unreal::TCHAR* tail, Text input)
    {
        return tail && tail == input.data() + input.size();
    }

    bool exact_payload_type(Text exported)
    {
        const Text expected = weapon_data_path;
        const auto start = exported.find(expected);
        if (start == Text::npos || start + expected.size() == exported.size())
        {
            return false;
        }
        const auto end = exported[start + expected.size()];
        return end == STR('\'') || end == STR('"') || end == STR(' ') || end == STR('(');
    }

    int round_trip(const Lua& lua)
    {
        if (!Unreal::IsInGameThreadRaw() || lua.get_stack_size() != 2 || !lua.is_integer(1) || !lua.is_integer(2))
        {
            return error(lua, "weapon_guest_and_detached_save_required_on_game_thread");
        }
        const auto save_address = lua.get_integer(2);
        const auto pawn_address = lua.get_integer(1);
        Context context;
        if (!resolve(pawn_address, save_address, context))
        {
            return error(lua, "stock_weapon_or_detached_save_unavailable");
        }

        auto* stock_function = find<Unreal::UFunction>(converter_path);
        auto* library = find<Unreal::UObject>(library_path);
        auto* item_base = find<Unreal::UClass>(item_actor_path);
        auto* item_type = find<Unreal::UScriptStruct>(item_struct_path);
        const auto* weapon_data = find<Unreal::UScriptStruct>(weapon_data_path);
        auto* instanced_type = find<Unreal::UScriptStruct>(instanced_struct_path);
        auto* guid_type = find<Unreal::UScriptStruct>(guid_struct_path);
        if (!stock_function || !library || library->GetClassPrivate() != stock_function->GetOuterPrivate() ||
            !library->HasAnyFlags(Unreal::RF_ClassDefaultObject) || stock_function->GetNumParms() != 3 ||
            stock_function->GetParmsSize() > parameter_capacity || !item_base || !item_type ||
            !weapon_data || !instanced_type || !guid_type ||
            !instanced_type->HasAllStructFlags(static_cast<Unreal::EStructFlags>(
                Unreal::STRUCT_ExportTextItemNative | Unreal::STRUCT_ImportTextItemNative)))
        {
            return error(lua, "stock_weapon_converter_schema_unavailable");
        }

        auto* actor_arg = parameter<Unreal::FObjectProperty>(stock_function, STR("ItemActor"), false);
        auto* world_arg = parameter<Unreal::FObjectProperty>(stock_function, STR("__WorldContext"), false);
        auto* item_arg = parameter<Unreal::FStructProperty>(stock_function, STR("Item"), true);
        auto* backpack = field<Unreal::FArrayProperty>(context.scratch_owner, STR("PlayerBackpack"));
        auto* item_property = backpack ? Unreal::CastField<Unreal::FStructProperty>(backpack->GetInner()) : nullptr;
        auto* item_class = field<Unreal::FClassProperty>(item_type, STR("Item_Class_3_2813641E42AA35FC0693029E4EE49F81"));
        auto* ammo = field<Unreal::FIntProperty>(item_type, STR("Ammo_6_4B455DAC4579E0B5AA2B1794FFE3E788"));
        auto* instanced = field<Unreal::FStructProperty>(item_type, STR("DataStruct_30_7A0DD7674F2FE505BF65209B6B2BCA59"));
        auto* guid = field<Unreal::FStructProperty>(item_type, STR("GUID_27_E1095FFF4FE869D46C601BAD3A1A0F9A"));
        auto* actor_ammo = field<Unreal::FIntProperty>(context.source, STR("Ammo"));
        if (!actor_arg || actor_arg->GetPropertyClass().Get() != item_base || !context.weapon->IsChildOf(item_base) ||
            !world_arg || !world_arg->GetPropertyClass().Get() ||
            !context.world_context->GetClassPrivate()->IsChildOf(world_arg->GetPropertyClass().Get()) ||
            !item_arg || item_arg->GetStruct().Get() != item_type || !item_property ||
            item_property->GetArrayDim() != 1 || item_property->GetStruct().Get() != item_type ||
            !item_class || item_class->GetMetaClass().Get() != item_base || !ammo || !actor_ammo ||
            !instanced || instanced->GetStruct().Get() != instanced_type ||
            !guid || guid->GetStruct().Get() != guid_type)
        {
            return error(lua, "stock_inventory_item_schema_unavailable");
        }

        ConversionParams params({actor_arg, world_arg, item_arg});
        actor_arg->SetObjectPropertyValue(actor_arg->ContainerPtrToValuePtr<void>(params.data()), context.source);
        world_arg->SetObjectPropertyValue(world_arg->ContainerPtrToValuePtr<void>(params.data()), context.world_context);
        library->ProcessEvent(stock_function, params.data());
        void* original = item_arg->ContainerPtrToValuePtr<void>(params.data());
        const auto stock_ammo = actor_ammo->GetPropertyValueInContainer(context.source);
        if (item_class->GetObjectPropertyValue(item_class->ContainerPtrToValuePtr<void>(original)) != context.weapon ||
            ammo->GetPropertyValueInContainer(original) != stock_ammo)
        {
            return error(lua, "stock_weapon_conversion_did_not_preserve_class_and_ammo");
        }
        // Use a known non-default value only in the converted scratch item.
        constexpr int sample_ammo = 7;
        ammo->SetPropertyValueInContainer(original, sample_ammo);
        std::array<std::uint32_t, 4> identity{};
        const auto* guid_value = guid->ContainerPtrToValuePtr<void>(original);
        for (std::size_t index = 0; index < identity.size(); ++index)
        {
            auto* part = field<Unreal::FIntProperty>(guid_type, guid_names[index]);
            if (!part)
            {
                return error(lua, "stock_item_guid_schema_unavailable");
            }
            identity[index] = static_cast<std::uint32_t>(part->GetPropertyValueInContainer(guid_value));
        }
        if (std::ranges::all_of(identity, [](auto part) { return part == 0; }))
        {
            return error(lua, "stock_weapon_conversion_did_not_assign_a_guid");
        }

        constexpr auto flags = Unreal::PPF_Delimited;
        Unreal::FString payload_text;
        const auto* original_payload = instanced->ContainerPtrToValuePtr<void>(original);
        instanced->ExportTextItem_Direct(payload_text, original_payload, nullptr, context.scratch_owner, flags);
        if (payload_text.Len() <= 0 || payload_text.Len() > max_text_characters ||
            !exact_payload_type(Text{*payload_text, static_cast<std::size_t>(payload_text.Len())}))
        {
            return error(lua, "stock_weapon_data_type_was_not_exported");
        }

        Unreal::FString exported;
        item_property->ExportTextItem_Direct(exported, original, nullptr, context.scratch_owner, flags);
        if (exported.Len() <= 0 || exported.Len() > max_text_characters)
        {
            return error(lua, "weapon_inventory_export_out_of_bounds");
        }
        auto cleanup = [item_property](void* scratch) { item_property->DestroyAndFreeValue(scratch); };
        std::unique_ptr<void, decltype(cleanup)> imported(item_property->AllocateAndInitializeValue(), cleanup);
        if (!imported)
        {
            return error(lua, "weapon_inventory_scratch_allocation_failed");
        }
        const Text text{*exported, static_cast<std::size_t>(exported.Len())};
        const auto* tail = item_property->ImportText_Direct(text.data(), imported.get(), context.scratch_owner, flags, nullptr);
        if (!complete(tail, text) || !item_property->Identical(original, imported.get()) ||
            !instanced->Identical(original_payload, instanced->ContainerPtrToValuePtr<void>(imported.get())))
        {
            return error(lua, "weapon_inventory_text_restore_not_identical");
        }

        lua.set_bool(true);
        lua.set_string(std::format("{:08X}{:08X}{:08X}{:08X}", identity[0], identity[1], identity[2], identity[3]));
        lua.set_integer(sample_ammo);
        lua.set_integer(exported.Len());
        lua.set_integer(payload_text.Len());
        return 5;
    }

    int run(const Lua& lua)
    {
        try
        {
            return round_trip(lua);
        }
        catch (const std::exception&)
        {
            return error(lua, "weapon_inventory_round_trip_unavailable");
        }
    }

}

void silverfish::register_native_item_roundtrip_lua(RC::LuaMadeSimple::Lua& lua)
{
    lua.register_function("SilverfishRoundTripWeaponItem", run);
}
