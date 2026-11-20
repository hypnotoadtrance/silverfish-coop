#include "mindcore.hpp"
#include "authored_pickup.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

#include <LuaMadeSimple/LuaMadeSimple.hpp>
#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace
{
    namespace U = RC::Unreal;
    using Lua = RC::LuaMadeSimple::Lua;
    using Address = std::uintptr_t;

    constexpr auto cradle_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.MindCore_Cradle_BP_C_1");
    constexpr auto cradle_type = STR("/Game/Classes/Actors/MindCore_Cradle_BP.MindCore_Cradle_BP_C");
    constexpr auto uploader_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.DataTape_Uploader_BP_C_1");
    constexpr auto uploader_type = STR("/Game/Classes/Actors/DataTape_Uploader_BP.DataTape_Uploader_BP_C");
    constexpr auto core_path = STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P:PersistentLevel.MindCore_BP_C_1");
    constexpr auto core_type = STR("/Game/Classes/Actors/MindCore_BP.MindCore_BP_C");
    constexpr auto tape_path = STR("/Game/Classes/Items/DataTapes/DataTape_DrillTestResults.DataTape_DrillTestResults_C");
    constexpr auto tape_master = STR("/Game/Classes/Items/DataTapes/Item_DataTape_Master.Item_DataTape_Master_C");
    constexpr auto insert_path = STR("/Game/Classes/Actors/DataTape_Uploader_BP.DataTape_Uploader_BP_C:Insert_Tape");

    template<class T> T* find(const U::TCHAR* path)
    {
        return U::UObjectGlobals::StaticFindObject<T*>(nullptr, nullptr, path);
    }

    template<class T> T* field(U::UObject* owner, const U::TCHAR* name)
    {
        auto* found = owner ? U::CastField<T>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return found && found->GetArrayDim() == 1 ? found : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* found = field<U::FObjectProperty>(owner, name);
        return found ? found->GetObjectPropertyValue(found->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    bool actor(U::UObject* candidate, const U::TCHAR* type, U::UWorld* world)
    {
        constexpr auto invalid = static_cast<U::EObjectFlags>(U::RF_ClassDefaultObject | U::RF_ArchetypeObject |
            U::RF_BeginDestroyed | U::RF_FinishDestroyed);
        if (!candidate || candidate->GetClassPrivate() != find<U::UClass>(type) || candidate->IsUnreachable() ||
            candidate->HasAnyFlags(invalid)) return false;
        auto* value = static_cast<U::AActor*>(candidate);
        auto* pending_kill = field<U::FBoolProperty>(value, STR("bActorIsBeingDestroyed"));
        return pending_kill && !pending_kill->GetPropertyValueInContainer(value) &&
            value->GetWorld() == world && value->GetLevel() == object(world, STR("PersistentLevel"));
    }

    bool exact_recipe(U::UObject* uploader, U::UObject* cradle, U::UClass* tape)
    {
        auto* field_map = field<U::FMapProperty>(uploader, STR("Tape_OutPuts"));
        auto* key = field_map ? U::CastField<U::FClassProperty>(field_map->GetKeyProp()) : nullptr;
        auto* value = field_map ? U::CastField<U::FObjectProperty>(field_map->GetValueProp()) : nullptr;
        auto* base = key ? key->GetMetaClass().Get() : nullptr;
        if (!key || !value || !tape || !base || !tape->IsChildOf(base) || key->GetOffset_Internal() != 0 ||
            key->GetSize() != sizeof(void*) ||
            value->GetSize() != sizeof(void*) || !value->GetPropertyClass().Get() ||
            !cradle->IsA(value->GetPropertyClass().Get())) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        auto* map = field_map->ContainerPtrToValuePtr<U::FScriptMap>(uploader);
        if (!map || map->Num() != 1 || map->GetMaxIndex() < 1 || map->GetMaxIndex() > 16) return false;
        for (int index = 0; index < map->GetMaxIndex(); ++index)
        {
            if (!map->IsValidIndex(index)) continue;
            const auto* entry = static_cast<const std::byte*>(map->GetData(index, layout));
            return entry && key->GetObjectPropertyValue(entry) == tape &&
                value->GetObjectPropertyValue(entry + layout.ValueOffset) == cradle;
        }
        return false;
    }

    bool stock_stage(U::UObject* cradle, U::UObject* canonical, int requested)
    {
        auto* status = field<U::FIntProperty>(cradle, STR("Status"));
        auto* console = field<U::FNameProperty>(cradle, STR("ConsoleID"));
        const U::FName expected{STR("MC_007_Console"), U::FNAME_Find};
        if (!status || !console || expected.IsNone() || console->GetSize() != sizeof(U::FName) ||
            status->GetPropertyValueInContainer(cradle) != requested ||
            *console->ContainerPtrToValuePtr<U::FName>(cradle) != expected) return false;
        auto* flags = field<U::FMapProperty>(canonical, STR("Flags"));
        auto* key = flags ? U::CastField<U::FNameProperty>(flags->GetKeyProp()) : nullptr;
        auto* value = flags ? U::CastField<U::FIntProperty>(flags->GetValueProp()) : nullptr;
        if (!key || !value || key->GetSize() != sizeof(U::FName) || key->GetOffset_Internal() != 0 ||
            value->GetSize() != sizeof(std::int32_t) || !key->HasAnyPropertyFlags(U::CPF_HasGetValueTypeHash)) return false;
        const auto layout = U::FScriptMap::GetScriptLayout(
            key->GetSize(), key->GetMinAlignment(), value->GetSize(), value->GetMinAlignment());
        if (value->GetOffset_Internal() != layout.ValueOffset) return false;
        auto* map = flags->ContainerPtrToValuePtr<U::FScriptMap>(canonical);
        if (!map || map->Num() < 1 || map->Num() > map->GetMaxIndex() || map->GetMaxIndex() > 4096) return false;
        const auto* found = map->FindValue(&expected, layout, [&](const void* raw) { return key->GetValueTypeHash(raw); },
            [&](const void* a, const void* b) { return key->Identical(a, b); });
        return found && value->GetPropertyValue(found) == requested;
    }

    bool exact_call(U::UObject* owner, U::UFunction* function, U::FClassProperty*& input)
    {
        input = field<U::FClassProperty>(function, STR("DataTape"));
        return function && function->GetOuterPrivate() == owner->GetClassPrivate() &&
            !function->HasAnyFunctionFlags(U::FUNC_Native | U::FUNC_Net) && function->GetNumParms() == 1 &&
            function->GetParmsSize() == sizeof(void*) && !function->GetReturnProperty() && input &&
            input->HasAnyPropertyFlags(U::CPF_Parm) &&
            !input->HasAnyPropertyFlags(U::CPF_ReturnParm | U::CPF_OutParm) && input->GetOffset_Internal() == 0 &&
            input->GetSize() == sizeof(void*) && input->GetMetaClass().Get() == find<U::UClass>(tape_master);
    }

    template<bool Dispatch>
    int results(const Lua& lua)
    {
        const auto report = [&](bool ok, std::string_view reason)
        {
            lua.set_bool(ok);
            lua.set_string(reason);
            return 2;
        };
        if (lua.get_stack_size() != 4 || !lua.is_integer(1) || !lua.is_integer(2) || !lua.is_integer(3) ||
            !lua.is_string(4)) return report(false, "native_mindcore_refused_arguments");
        const auto pawn = lua.get_integer();
        const auto detached = lua.get_integer();
        const auto canonical = lua.get_integer();
        const auto guid = lua.get_string();
        if (pawn <= 0 || detached <= 0 || canonical <= 0) return report(false, "native_mindcore_refused_arguments");
        auto* world = silverfish::native_authored_pickup::qualify_mindcore_results(
            static_cast<Address>(pawn), static_cast<Address>(detached), static_cast<Address>(canonical), guid);
        if (!world) return report(false, "native_mindcore_refused_tape_owner");

        auto* cradle = find<U::UObject>(cradle_path);
        auto* uploader = find<U::UObject>(uploader_path);
        auto* core = find<U::UObject>(core_path);
        auto* tape = find<U::UClass>(tape_path);
        auto* function = find<U::UFunction>(insert_path);
        U::FClassProperty* input{};
        auto* stock = reinterpret_cast<U::UObject*>(static_cast<Address>(canonical));
        if (!actor(cradle, cradle_type, world) || !actor(uploader, uploader_type, world) || !actor(core, core_type, world) ||
            object(cradle, STR("MindCore_Ref")) != core || object(cradle, STR("TapeInserter_Ref")) != uploader ||
            (!stock_stage(cradle, stock, 1) && (Dispatch || !stock_stage(cradle, stock, 2))) ||
            !exact_recipe(uploader, cradle, tape) || !exact_call(uploader, function, input))
            return report(false, "native_mindcore_refused_stock_upload_unqualified");

        if constexpr (!Dispatch) return report(true, "stock_results_in_backpack");
        alignas(void*) std::array<std::byte, sizeof(void*)> arguments{};
        input->SetObjectPropertyValue(input->ContainerPtrToValuePtr<void>(arguments.data()), tape);
        uploader->ProcessEvent(function, arguments.data());
        const auto* still_owned = silverfish::native_authored_pickup::qualify_mindcore_results(
            static_cast<Address>(pawn), static_cast<Address>(detached), static_cast<Address>(canonical), guid);
        return still_owned == world ? report(true, "stock_insert_dispatched") :
            report(false, "mutation_unverified_native_mindcore_tape_custody");
    }
}

namespace silverfish::native_mindcore
{
    void register_lua(Lua& lua)
    {
        lua.register_function("SilverfishReadNativeMindCoreResults", results<false>);
        lua.register_function("SilverfishUploadNativeMindCoreResults", results<true>);
    }
}
