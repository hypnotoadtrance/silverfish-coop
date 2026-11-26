#pragma once

#include "world_path.hpp"

#include <Unreal/AActor.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace silverfish::native_guest_drop_maps
{
    namespace U = RC::Unreal;
    inline bool stock_map(U::UWorld* world, bool allow_playtest = true)
    {
        if (!world) return false;
        const auto path = world->GetPathName();
        return (allow_playtest || !path.starts_with(STR("/Game/Classes/Maps/PlaytestMap_"))) &&
            path != STR("/Game/Classes/Maps/MainMenu_P.MainMenu_P") &&
            silverfish::world_path::object(RC::StringViewType{path}) &&
            U::UObjectGlobals::StaticFindObject<U::UWorld*>(nullptr, nullptr, path.c_str()) == world;
    }

    inline bool persistent_actor(U::AActor* actor, U::UWorld* world)
    {
        if (!actor || actor->GetWorld() != world) return false;
        auto* level = actor->GetLevel();
        return level && level->GetOuterPrivate() == world && level->GetName() == STR("PersistentLevel");
    }
}
