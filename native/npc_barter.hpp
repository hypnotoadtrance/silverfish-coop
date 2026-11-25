#pragma once

#include <Unreal/AActor.hpp>
#include <Unreal/CoreUObject/UObject/Class.hpp>
#include <Unreal/CoreUObject/UObject/UnrealType.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace silverfish::npc_barter
{
    namespace Unreal = RC::Unreal;

    inline constexpr auto trader_class = STR("/Game/Classes/Actors/Barter_BP.Barter_BP_C");
    inline constexpr auto npc_class = STR("/Game/Classes/NPCs/NPC_Assets/NPC_Master_BP.NPC_Master_BP_C");
    inline constexpr auto merchant_class = STR("/Game/Classes/Libraries/DataAssets/Merchants/MerchantData.MerchantData_C");
    inline constexpr auto screen_class = STR("/Game/Classes/GUI/New/BarterScreen.BarterScreen_C");

    inline Unreal::UClass* type(const Unreal::TCHAR* path)
    {
        return Unreal::UObjectGlobals::StaticFindObject<Unreal::UClass*>(nullptr, nullptr, path);
    }

    inline bool live(Unreal::UObject* object)
    {
        constexpr auto excluded = static_cast<Unreal::EObjectFlags>(Unreal::RF_ClassDefaultObject | Unreal::RF_ArchetypeObject |
            Unreal::RF_BeginDestroyed | Unreal::RF_FinishDestroyed);
        return object && !object->IsUnreachable() && !object->HasAnyFlags(excluded);
    }

    template<typename T>
    inline T* property(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = object ? Unreal::CastField<T>(object->GetPropertyByNameInChain(name)) : nullptr;
        return value && value->GetArrayDim() == 1 ? value : nullptr;
    }

    inline Unreal::UObject* child(Unreal::UObject* object, const Unreal::TCHAR* name)
    {
        auto* value = property<Unreal::FObjectProperty>(object, name);
        return value ? value->GetObjectPropertyValue(value->ContainerPtrToValuePtr<void>(object)) : nullptr;
    }

    inline Unreal::UObject* linked_npc(Unreal::UObject* trader, Unreal::UWorld* world, bool require_alive = true)
    {
        if (!live(world) || !live(trader) || trader->GetClassPrivate() != type(trader_class) ||
            static_cast<Unreal::AActor*>(trader)->GetWorld() != world || child(trader, STR("LinkedShop"))) return nullptr;
        auto* npc = child(trader, STR("LinkedNPC"));
        auto* base = type(npc_class);
        auto* merchant = child(trader, STR("MerchantData"));
        auto* alive = property<Unreal::FBoolProperty>(npc, STR("Alive"));
        if (!live(npc) || !base || !npc->IsA(base) || child(npc, STR("BarterRef")) != trader ||
            static_cast<Unreal::AActor*>(npc)->GetWorld() != world || !alive ||
            (require_alive && !alive->GetPropertyValueInContainer(npc)) ||
            !live(merchant) || merchant->GetClassPrivate() != type(merchant_class)) return nullptr;
        return npc;
    }
}
