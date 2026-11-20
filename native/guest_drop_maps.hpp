#pragma once

#include <array>

#include <Unreal/AActor.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace silverfish::native_guest_drop_maps
{
    namespace U = RC::Unreal;
    inline constexpr std::array worlds{
        STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
        STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
    };

    inline bool contains(U::UWorld* world)
    {
        if (!world) return false;
        const auto path = world->GetPathName();
        for (const auto* stock : worlds)
            if (path == stock)
                return U::UObjectGlobals::StaticFindObject<U::UWorld*>(nullptr, nullptr, stock) == world;
        return false;
    }

    inline bool persistent_actor(U::AActor* actor, U::UWorld* world)
    {
        if (!actor || actor->GetWorld() != world) return false;
        auto* level = actor->GetLevel();
        return level && level->GetOuterPrivate() == world && level->GetName() == STR("PersistentLevel");
    }
}
