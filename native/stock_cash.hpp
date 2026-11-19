#pragma once

#include <algorithm>
#include <array>

#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace silverfish::stock_cash
{
    namespace Unreal = RC::Unreal;

    inline bool is_family(Unreal::UClass* type)
    {
        auto* base = Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr,
            STR("/Game/Classes/Items/Item_Money_Master.Item_Money_Master_C"));
        return type && base && type->IsChildOf(base);
    }

    inline bool qualified_value(Unreal::UClass* type, int amount, Unreal::UObject* placed = nullptr)
    {
        struct CashProfile { const Unreal::TCHAR* path; int minimum; int maximum; };
        constexpr std::array profiles{
            CashProfile{STR("/Game/Classes/Items/Item_Money_CoupleBills.Item_Money_CoupleBills_C"), 6, 10},
            CashProfile{STR("/Game/Classes/Items/Item_Money_SmallStack.Item_Money_SmallStack_C"), 30, 50},
            CashProfile{STR("/Game/Classes/Items/Item_Money_LargeStack.Item_Money_LargeStack_C"), 100, 200},
            CashProfile{STR("/Game/Classes/Items/Item_Money_MassivePile.Item_Money_MassivePile_C"), 3000, 5000},
        };
        if (!type) return false;
        const auto path = type->GetPathName();
        const auto profile = std::ranges::find_if(profiles, [&](const auto& entry) { return path == entry.path; });
        if (profile == profiles.end()) return false;
        auto* defaults = type->GetClassDefaultObject().Get();
        if (!defaults) return false;
        auto* minimum = Unreal::CastField<Unreal::FIntProperty>(defaults->GetPropertyByNameInChain(STR("Value_Min")));
        auto* maximum = Unreal::CastField<Unreal::FIntProperty>(defaults->GetPropertyByNameInChain(STR("Value_Max")));
        if (!defaults->HasAnyFlags(Unreal::RF_ClassDefaultObject) ||
            defaults->GetClassPrivate() != type || !minimum || !maximum ||
            minimum->GetArrayDim() != 1 || maximum->GetArrayDim() != 1 ||
            minimum->GetPropertyValueInContainer(defaults) != profile->minimum ||
            maximum->GetPropertyValueInContainer(defaults) != profile->maximum) return false;
        if (placed && (placed->GetClassPrivate() != type ||
            minimum->GetPropertyValueInContainer(placed) != profile->minimum ||
            maximum->GetPropertyValueInContainer(placed) != profile->maximum)) return false;
        return amount >= profile->minimum && amount <= profile->maximum;
    }
}
