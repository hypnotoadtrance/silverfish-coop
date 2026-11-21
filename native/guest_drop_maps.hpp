#pragma once

#include "authored_exit_registry.hpp"

#include <algorithm>
#include <array>

#include <Unreal/AActor.hpp>
#include <Unreal/UObjectGlobals.hpp>
#include <Unreal/World.hpp>

namespace silverfish::native_guest_drop_maps
{
    namespace U = RC::Unreal;
    inline constexpr std::array dream_worlds{
        STR("/Game/Classes/Maps/Maps_Dungeons/Exchange_01_P.Exchange_01_P"),
        STR("/Game/Classes/Maps/Dreams/TheRook_P.TheRook_P"),
        STR("/Game/Classes/Maps/Dreams/Pyroclast_P.Pyroclast_P"),
        STR("/Game/Classes/Maps/Dreams/Slate_P.Slate_P"),
        STR("/Game/Classes/Maps/Dreams/BlackOcean_P.BlackOcean_P"),
        STR("/Game/Classes/Maps/Dreams/Nothing_P.Nothing_P"),
    };

    inline bool contains(U::UWorld* world)
    {
        if (!world) return false;
        const auto path = world->GetPathName();
        const bool campaign = !authored_exits::for_world(path).empty()
            || std::ranges::find(authored_exits::routes, path, &authored_exits::Route::destination_world) != authored_exits::routes.end()
            || std::ranges::any_of(dream_worlds, [&](const auto* dream) { return path == dream; });
        return campaign && U::UObjectGlobals::StaticFindObject<U::UWorld*>(nullptr, nullptr, path.c_str()) == world;
    }

    inline bool persistent_actor(U::AActor* actor, U::UWorld* world)
    {
        if (!actor || actor->GetWorld() != world) return false;
        auto* level = actor->GetLevel();
        return level && level->GetOuterPrivate() == world && level->GetName() == STR("PersistentLevel");
    }
}
