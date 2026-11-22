#include "guest_movement.hpp"
#include "enemy_loot.hpp"
#include "guest_regen.hpp"
#include "guest_sickness.hpp"

#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>

#include <Helpers/String.hpp>
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
    using Clock = std::chrono::steady_clock;
    using Iteration = Hook::TCallbackIterationData<void>;

    struct Effect
    {
        const U::TCHAR* caller;
        const U::TCHAR* name;
    };

    constexpr std::array effects{
        Effect{STR("ApplyDynamicStats"), STR("SpeedBoost")},
        Effect{STR("Calc_CrouchSpeed"), STR("SwiftPaw")},
        Effect{STR("DetermineMoveSpeed"), STR("Nudist")},
        Effect{STR("Action_Sprint"), STR("BadSwimmer")},
    };

    struct Readback
    {
        U::FWeakObjectPtr pawn, save, stock;
        std::array<int, effects.size()> counts{};
        Clock::time_point expires{};
    };

    std::array<Readback, 4> cache;
    U::FName passive_name{};
    Hook::GlobalCallbackId callback = Hook::ERROR_ID;

    template<typename T>
    T* parameter(U::UFunction* function, const U::TCHAR* name, int offset, bool out = false)
    {
        auto* property = U::CastField<T>(function->GetPropertyByNameInChain(name));
        return property && property->GetArrayDim() == 1 && property->GetOffset_Internal() == offset
            && property->HasAnyPropertyFlags(U::CPF_Parm) && property->HasAnyPropertyFlags(U::CPF_OutParm) == out
            ? property : nullptr;
    }

    U::UObject* object(U::UObject* owner, const U::TCHAR* name)
    {
        auto* property = owner ? U::CastField<U::FObjectProperty>(owner->GetPropertyByNameInChain(name)) : nullptr;
        return property ? property->GetObjectPropertyValue(property->ContainerPtrToValuePtr<void>(owner)) : nullptr;
    }

    void* output(U::FFrame& frame, U::FProperty* property)
    {
        void* result{};
        auto* entry = frame.OutParms();
        for (int remaining = 4; entry && remaining > 0; --remaining, entry = entry->NextOutParm)
        {
            if (entry->Property != property) continue;
            if (result) return nullptr;
            result = entry->PropAddr;
        }
        return entry ? nullptr : result;
    }

    const Readback* readback(U::UObject* pawn, U::UObject* save, U::UObject* stock, bool recalculate)
    {
        const auto now = Clock::now();
        Readback* slot{};
        for (auto& entry : cache)
        {
            if (entry.pawn.Get() == pawn)
            {
                if (!recalculate && entry.expires > now && entry.save.Get() == save && entry.stock.Get() == stock)
                    return &entry;
                slot = &entry;
                break;
            }
            if (entry.expires <= now || !entry.pawn.Get()) slot = &entry;
        }
        if (!slot || !silverfish::native_guest_regen::qualifies_detached_guest(pawn, save, stock))
            return nullptr;

        Readback next;
        next.pawn = pawn;
        next.save = save;
        next.stock = stock;
        next.expires = now + std::chrono::milliseconds(250);
        std::int32_t fatigue{};
        if (!silverfish::native_guest_sickness::read_fatigue(save, fatigue)) return nullptr;
        int attuned{};
        if (fatigue >= 75 && !silverfish::native_enemy_loot::count_save_passive(
                save, U::FName{STR("AttunedMadness"), U::FNAME_Find}, attuned)) return nullptr;
        for (std::size_t i = 0; i < effects.size(); ++i)
        {
            const U::FName name{effects[i].name, U::FNAME_Find};
            auto& count = next.counts[i];
            if (name.IsNone() || !silverfish::native_enemy_loot::count_save_passive(save, name, count)) return nullptr;
            if (count > 0 && attuned > 0) ++count;
        }
        *slot = next;
        return slot;
    }

    void observe(Iteration& call, U::UObject* instance, U::FFrame& frame, void*)
    {
        auto* function = frame.Node();
        if (!function || !U::IsInGameThreadRaw()) return;
        if (passive_name.IsNone()) passive_name = U::FName{STR("FindPassiveEffect"), U::FNAME_Find};
        if (passive_name.IsNone() || function->GetFName() != passive_name
            || call.OriginalFunctionCallPrevented() || !instance || frame.Object() != instance
            || function->GetOuterPrivate() != instance->GetClassPrivate() || function->GetNumParms() != 4
            || function->GetParmsSize() != 16 || !frame.Locals()) return;
        auto* caller = frame.PreviousFrame();
        auto* pawn = caller ? caller->Object() : nullptr;
        auto* origin = caller ? caller->Node() : nullptr;
        if (!pawn || !origin || origin->GetOuterPrivate() != pawn->GetClassPrivate()) return;
        auto* save = object(pawn, STR("Save"));
        auto* stock = object(instance, STR("SaveSlot_Ref"));
        if (!save || !stock || save == stock) return;
        auto* player = U::UObjectGlobals::StaticFindObject<U::UClass*>(
            nullptr, nullptr, STR("/Game/Classes/Player/Player_FP.Player_FP_C"));
        if (pawn->GetClassPrivate() != player) return;
        auto* world = static_cast<U::AActor*>(pawn)->GetWorld();
        if (!world || object(world, STR("OwningGameInstance")) != instance) return;

        auto* name = parameter<U::FNameProperty>(function, STR("EffectName"), 0);
        auto* count = parameter<U::FBoolProperty>(function, STR("CountInstances"), 8);
        auto* active = parameter<U::FBoolProperty>(function, STR("EffectActive"), 9, true);
        auto* instances = parameter<U::FIntProperty>(function, STR("Instances"), 12, true);
        if (!name || !count || !active || !instances) return;
        const auto effect = name->GetPropertyValueInContainer(frame.Locals());
        for (std::size_t i = 0; i < effects.size(); ++i)
        {
            if (origin->GetFName() != U::FName{effects[i].caller, U::FNAME_Find}
                || effect != U::FName{effects[i].name, U::FNAME_Find}) continue;
            auto* found = output(frame, active);
            auto* total = output(frame, instances);
            if (!found || !total || found == total || reinterpret_cast<std::uintptr_t>(total) % alignof(std::int32_t))
                return;
            // A new ApplyDynamicStats run can follow a change to the save. Its
            // SpeedBoost query refreshes the other movement counts for that run.
            const auto* verified = readback(pawn, save, stock, i == 0);
            if (!verified) return;
            const auto owned = verified->counts[i];
            active->SetPropertyValue(found, owned > 0);
            instances->SetPropertyValue(total, count->GetPropertyValueInContainer(frame.Locals()) ? owned : (owned > 0));
            return;
        }
    }
}

void silverfish::native_guest_movement::install_hooks()
{
    if (callback != Hook::ERROR_ID) return;
    char enabled[2]{};
    std::size_t size{};
    if (getenv_s(&size, enabled, sizeof(enabled), "SILVERFISH_COOP_NATIVE_GUEST_SAVE") != 0
        || size != sizeof(enabled) || enabled[0] != '1') return;
    callback = Hook::RegisterProcessLocalScriptFunctionPostCallback(
        observe, {false, false, STR("SilverfishCoopNative"), STR("Use each guest's stock movement passives")});
}

void silverfish::native_guest_movement::uninstall_hooks()
{
    if (callback != Hook::ERROR_ID) Hook::UnregisterCallback(callback);
    callback = Hook::ERROR_ID;
    cache = {};
}
