#pragma once

#include <cstddef>
#include <cstdint>
#include <utility>

#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/NameTypes.hpp>
#include <Unreal/UObjectGlobals.hpp>

namespace silverfish::stock_wallet
{
    namespace Unreal = RC::Unreal;

    class Wallet
    {
        static constexpr auto check_cash = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Check_SaveFlag");
        static constexpr auto change_cash = STR("/Game/Classes/Global/GameInstance_FP.GameInstance_FP_C:Give_PlayerCash");

        template<typename T>
        static T* field(Unreal::UFunction* function, const Unreal::TCHAR* name)
        {
            auto* property = function ? Unreal::CastField<T>(function->GetPropertyByNameInChain(name)) : nullptr;
            return property && property->GetArrayDim() == 1 ? property : nullptr;
        }

        Unreal::UFunction* read = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(nullptr, nullptr, check_cash);
        Unreal::UFunction* update = Unreal::UObjectGlobals::StaticFindObject<Unreal::UFunction*>(nullptr, nullptr, change_cash);
        Unreal::FIntProperty* value = field<Unreal::FIntProperty>(read, STR("Value"));
        Unreal::FBoolProperty* found = field<Unreal::FBoolProperty>(read, STR("Found"));

    public:
        bool bind(Unreal::UObject* instance) const
        {
            auto* key = field<Unreal::FNameProperty>(read, STR("Key"));
            auto* amount = field<Unreal::FIntProperty>(update, STR("Amount"));
            auto* subtract = field<Unreal::FBoolProperty>(update, STR("Deduct"));
            auto* type = instance ? instance->GetClassPrivate() : nullptr;
            return type && read && update && read->GetOuterPrivate() == type && update->GetOuterPrivate() == type &&
                key && key->GetOffset_Internal() == 0 && key->GetSize() == sizeof(Unreal::FName) &&
                value && value->GetOffset_Internal() == 12 && found && found->GetOffset_Internal() == 8 &&
                read->GetNumParms() == 3 && read->GetParmsSize() == 16 &&
                amount && amount->GetOffset_Internal() == 0 && subtract && subtract->GetOffset_Internal() == 4 &&
                update->GetNumParms() == 2 && update->GetParmsSize() >= 5 && update->GetParmsSize() <= 8;
        }

        std::pair<int, bool> flag_value(Unreal::UObject* instance, const Unreal::TCHAR* key) const
        {
            struct Parameters { Unreal::FName name; bool found{}; std::int32_t amount{}; };
            static_assert(sizeof(Parameters) == 16 && offsetof(Parameters, amount) == 12);
            Parameters params{Unreal::FName{key}};
            instance->ProcessEvent(read, &params);
            const auto exists = found->GetPropertyValueInContainer(&params);
            return {exists ? value->GetPropertyValueInContainer(&params) : 0, exists};
        }

        int amount(Unreal::UObject* instance) const { return flag_value(instance, STR("Player_Cash")).first; }

        void change(Unreal::UObject* instance, int amount, bool deduct) const
        {
            struct Parameters { std::int32_t amount; bool deduct; } params{amount, deduct};
            static_assert(sizeof(Parameters) == 8 && offsetof(Parameters, deduct) == 4);
            instance->ProcessEvent(update, &params);
        }
    };
}
