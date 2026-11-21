#pragma once

#include <optional>

#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace silverfish::drop_item_class
{
    [[nodiscard]] inline std::optional<bool> stacks(RC::Unreal::UClass* type)
    {
        namespace U = RC::Unreal;
        auto* base = U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr,
            STR("/Game/Classes/Items/Item_Master_BP.Item_Master_BP_C"));
        if (!type || !base || type == base || !type->IsChildOf(base) || type->IsUnreachable()) return {};
        const auto path = type->GetPathName();
        if (!path.starts_with(STR("/Game/Classes/")) || !path.ends_with(STR("_C")) || path.size() > 256 ||
            U::UObjectGlobals::StaticFindObject<U::UClass*>(nullptr, nullptr, path.c_str()) != type) return {};

        auto* defaults = type->GetClassDefaultObject().Get();
        if (!defaults || defaults->GetClassPrivate() != type || !defaults->HasAnyFlags(U::RF_ClassDefaultObject) ||
            defaults->IsUnreachable()) return {};
        static const U::FName interact{STR("SendInteractionToActor")};
        auto* take = base->GetFunctionByNameInChain(interact);
        if (!take || type->GetFunctionByNameInChain(interact) != take) return {};

        const auto flag = [&](const U::TCHAR* name) -> std::optional<bool> {
            auto* property = U::CastField<U::FBoolProperty>(defaults->GetPropertyByNameInChain(name));
            if (!property || property->GetArrayDim() != 1) return {};
            return property->GetPropertyValueInContainer(defaults);
        };
        if (flag(STR("Important")) != false || flag(STR("DespawnIfPlayerOwns")) != false ||
            flag(STR("bReplicates")) != false) return {};
        return flag(STR("Stacks"));
    }
}
