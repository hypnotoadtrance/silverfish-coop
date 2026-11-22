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

    struct EffectCount
    {
        U::FName name{};
        int count{};
    };

    struct Readback
    {
        U::FWeakObjectPtr pawn, save, stock;
        std::array<EffectCount, 48> effects{};
        std::int32_t bonus{};
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

    Readback* readback(U::UObject* pawn, U::UObject* save, U::UObject* stock, bool recalculate)
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
        if (!silverfish::native_guest_sickness::read_passive_amplification(save, next.bonus)) return nullptr;
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
        auto* found = output(frame, active);
        auto* total = output(frame, instances);
        if (effect.IsNone() || !found || !total || found == total ||
            reinterpret_cast<std::uintptr_t>(total) % alignof(std::int32_t)) return;

        // Hydrophobic and DoubleJump open the stock dynamic-stat and jump
        // refreshes respectively. Read the guest's current loadout for both.
        static const U::FName dynamic{STR("ApplyDynamicStats")}, hydrophobic{STR("Hydrophobic")};
        static const U::FName jump{STR("Calc_JumpVelocity")}, double_jump{STR("DoubleJump")};
        const auto origin_name = origin->GetFName();
        const bool recalculate = (origin_name == dynamic && effect == hydrophobic) ||
                                 (origin_name == jump && effect == double_jump);
        auto* verified = readback(pawn, save, stock, recalculate);
        if (!verified) return;
        for (auto& row : verified->effects)
        {
            if (row.name.IsNone())
            {
                if (!silverfish::native_enemy_loot::count_save_passive(save, effect, row.count)) return;
                row.name = effect;
                if (row.count > 0) row.count += verified->bonus;
            }
            if (row.name != effect) continue;
            const auto owned = row.count;
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
