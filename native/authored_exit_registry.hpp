#pragma once

// Generated from tools/silverfish/authored_routes.py.
// Regenerate with: python -m tools.silverfish.generate_authored_exit_registry

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <span>

#include <String/StringType.hpp>

namespace silverfish::authored_exits
{
    enum class Mode : std::uint8_t
    {
        overlap,
        door,
    };

    namespace detail
    {
        struct World
        {
            RC::StringViewType path;
            RC::StringViewType package;
        };

        inline constexpr World Anex_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Anex_01_P.Anex_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Anex_01_P")};
        inline constexpr World Anom_Maze_01{STR("/Game/Classes/Maps/Maps_Dungeons/Anom_Maze_01.Anom_Maze_01"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Anom_Maze_01")};
        inline constexpr World ArtifactForge_P{STR("/Game/Classes/Maps/Maps_SafeZones/ArtifactForge_P.ArtifactForge_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/ArtifactForge_P")};
        inline constexpr World Beach_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Beach_01_P.Beach_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Beach_01_P")};
        inline constexpr World Borough{STR("/Game/Classes/Maps/Maps_SafeZones/Borough.Borough"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Borough")};
        inline constexpr World Cistern_P{STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P.Cistern_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Cistern_P")};
        inline constexpr World City_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P.City_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/City_01_P")};
        inline constexpr World CrashSite_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/CrashSite_01_P.CrashSite_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/CrashSite_01_P")};
        inline constexpr World CrossRoads_P{STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P.CrossRoads_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/CrossRoads_P")};
        inline constexpr World DataSilo_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P.DataSilo_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/DataSilo_01_P")};
        inline constexpr World DeadBridge_P{STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P.DeadBridge_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadBridge_P")};
        inline constexpr World DeadEnd_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P.DeadEnd_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/DeadEnd_01_P")};
        inline constexpr World Docks_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Docks_01_P.Docks_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Docks_01_P")};
        inline constexpr World DryDock_P{STR("/Game/Classes/Maps/Maps_Dungeons/DryDock_P.DryDock_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/DryDock_P")};
        inline constexpr World Factory_SB_P{STR("/Game/Classes/Maps/Maps_Dungeons/Factory_SB_P.Factory_SB_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Factory_SB_P")};
        inline constexpr World Forest_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P.Forest_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Forest_01_P")};
        inline constexpr World FortLeto_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P.FortLeto_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/FortLeto_01_P")};
        inline constexpr World Funnel_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Funnel_01_P.Funnel_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Funnel_01_P")};
        inline constexpr World Gorge_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P.Gorge_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Gorge_01_P")};
        inline constexpr World Lab_E206_P{STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P.Lab_E206_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Lab_E206_P")};
        inline constexpr World Lab_EastWing_P{STR("/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P.Lab_EastWing_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Lab_EastWing_P")};
        inline constexpr World Lake_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P.Lake_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Lake_01_P")};
        inline constexpr World NorthEast_Rail_P{STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P.NorthEast_Rail_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/NorthEast_Rail_P")};
        inline constexpr World Observitory_P{STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P.Observitory_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Observitory_P")};
        inline constexpr World OldPipes_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P.OldPipes_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/OldPipes_01_P")};
        inline constexpr World Perdition_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Perdition_01_P.Perdition_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Perdition_01_P")};
        inline constexpr World PineRidge_P{STR("/Game/Classes/Maps/Maps_OverWorld/PineRidge_P.PineRidge_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/PineRidge_P")};
        inline constexpr World Quarry_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P.Quarry_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Quarry_01_P")};
        inline constexpr World RailYard_P{STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P.RailYard_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RailYard_P")};
        inline constexpr World Refinery_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P.Refinery_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Refinery_01_P")};
        inline constexpr World Rev_NorthBase_P{STR("/Game/Classes/Maps/Maps_SafeZones/Rev_NorthBase_P.Rev_NorthBase_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Rev_NorthBase_P")};
        inline constexpr World RuinFeilds_P{STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P.RuinFeilds_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/RuinFeilds_P")};
        inline constexpr World SavloEstate_P{STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P.SavloEstate_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/SavloEstate_P")};
        inline constexpr World Shop_BlackMarket_01{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01.Shop_BlackMarket_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_BlackMarket_01")};
        inline constexpr World Shop_Craftsman_01{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Craftsman_01.Shop_Craftsman_01"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Craftsman_01")};
        inline constexpr World Shop_Gunsmith_P{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P.Shop_Gunsmith_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Gunsmith_P")};
        inline constexpr World Shop_ScavBar_P{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P.Shop_ScavBar_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ScavBar_P")};
        inline constexpr World Shop_ShopKeep_P{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P.Shop_ShopKeep_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_ShopKeep_P")};
        inline constexpr World Shop_Smuggler_P{STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P.Shop_Smuggler_P"),
            STR("/Game/Classes/Maps/Maps_SafeZones/Shop_Smuggler_P")};
        inline constexpr World Slums_P{STR("/Game/Classes/Maps/Maps_OverWorld/Slums_P.Slums_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Slums_P")};
        inline constexpr World Slums_Tunnel_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P.Slums_Tunnel_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Slums_Tunnel_01_P")};
        inline constexpr World SouthEast_Road_P{STR("/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P.SouthEast_Road_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/SouthEast_Road_P")};
        inline constexpr World StoneMaze_P{STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P.StoneMaze_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/StoneMaze_P")};
        inline constexpr World Subway_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P.Subway_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Subway_01_P")};
        inline constexpr World Swamp_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P.Swamp_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Swamp_01_P")};
        inline constexpr World TheThroat_P{STR("/Game/Classes/Maps/Maps_Dungeons/TheThroat_P.TheThroat_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/TheThroat_P")};
        inline constexpr World TheWall_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/TheWall_01_P.TheWall_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/TheWall_01_P")};
        inline constexpr World TrashPit_P{STR("/Game/Classes/Maps/Maps_OverWorld/TrashPit_P.TrashPit_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/TrashPit_P")};
        inline constexpr World Trench_01_P{STR("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P.Trench_01_P"),
            STR("/Game/Classes/Maps/Maps_OverWorld/Trench_01_P")};
        inline constexpr World TutTnl_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/TutTnl_01_P.TutTnl_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/TutTnl_01_P")};
        inline constexpr World Tut_Dock_P{STR("/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P.Tut_Dock_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/Tut_Dock_P")};
        inline constexpr World UtilityTunnels_01_P{STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P.UtilityTunnels_01_P"),
            STR("/Game/Classes/Maps/Maps_Dungeons/UtilityTunnels_01_P")};
    }

    struct Route
    {
        RC::StringViewType source_world;
        RC::StringViewType source_actor;
        Mode mode;
        RC::StringViewType destination_world;
        RC::StringViewType destination_package;
        RC::StringViewType entrance;
        RC::StringViewType arrival_actor;
        // Door child names identify cooked exports. Match live ownership and
        // component pointers when qualifying the two ChildActor instances.
        RC::StringViewType source_child;
        RC::StringViewType arrival_child;
        std::optional<std::int32_t> travel_time_step;

        constexpr Route(detail::World source, detail::World destination, Mode mode,
                        RC::StringViewType actor, RC::StringViewType entrance, RC::StringViewType arrival,
                        std::optional<std::int32_t> time_step = std::nullopt,
                        RC::StringViewType travel_child = {}, RC::StringViewType arrival_child = {}) noexcept
            : source_world{source.path}, source_actor{actor}, mode{mode},
              destination_world{destination.path}, destination_package{destination.package},
              entrance{entrance}, arrival_actor{arrival}, source_child{travel_child},
              arrival_child{arrival_child}, travel_time_step{time_step}
        {
        }
    };

    inline constexpr std::array routes{
        Route{detail::Anex_01_P, detail::TheWall_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("Wall_Annex"), STR("Dungeon_TransitionDoor_BP_C_3"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_364")},
        Route{detail::Anex_01_P, detail::StoneMaze_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Maze_Annex"), STR("Dungeon_TransitionDoor_BP_C_4"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_1"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_750")},
        Route{detail::Anex_01_P, detail::Trench_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_3"), STR("Annex_Trench"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_93"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_10")},
        Route{detail::Anex_01_P, detail::ArtifactForge_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_4"), STR("Anex_Forge"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_638"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_188")},
        Route{detail::Anom_Maze_01, detail::NorthEast_Rail_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("M35_Warren"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_158"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_264")},
        Route{detail::Cistern_P, detail::CrossRoads_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("CR_Cstrn"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_178"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_268")},
        Route{detail::CrashSite_01_P, detail::TutTnl_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Crash_Tut"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_150"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_186")},
        Route{detail::DataSilo_01_P, detail::TheWall_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Wall_Data"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_286"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_246")},
        Route{detail::DryDock_P, detail::Refinery_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("Refine_DD"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_236")},
        Route{detail::DryDock_P, detail::Docks_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Docks_DD"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_4"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_134")},
        Route{detail::Factory_SB_P, detail::SouthEast_Road_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("SB_Factory"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_52")},
        Route{detail::FortLeto_01_P, detail::Beach_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Beach_Leto"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_221"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_134")},
        Route{detail::Funnel_01_P, detail::Trench_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Trench_Funnel"), STR("Dungeon_TransitionDoor_BP_C_4"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_358"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_126")},
        Route{detail::Lab_E206_P, detail::RailYard_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("RY_Lab206"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_176"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_601")},
        Route{detail::Lab_EastWing_P, detail::RuinFeilds_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("RgEast_Flood"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_136")},
        Route{detail::OldPipes_01_P, detail::NorthEast_Rail_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("NE_Pipe"), STR("Dungeon_TransitionDoor_BP_C_3"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_694")},
        Route{detail::Perdition_01_P, detail::DeadEnd_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Haunt_DeadEnd"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_142"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_190")},
        Route{detail::Perdition_01_P, detail::StoneMaze_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Haunt_Stone"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_238"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_138")},
        Route{detail::Refinery_01_P, detail::DryDock_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Refine_DD"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_236"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::Refinery_01_P, detail::Beach_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_241"), STR("Refine_Beach"), STR("LevelExit_Spawn_BP_C_1"), 12},
        Route{detail::Refinery_01_P, detail::Slums_P, Mode::overlap,
            STR("Level_Exit_BP_C_242"), STR("Slum_Refine"), STR("LevelExit_Spawn_BP_C_1"), 8},
        Route{detail::Slums_Tunnel_01_P, detail::Slums_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Slums_GreenLine"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_92"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_152")},
        Route{detail::Slums_Tunnel_01_P, detail::Subway_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_97"), STR("Subway_Slums"), STR("LevelExit_Spawn_BP_C_201"), 20},
        Route{detail::Subway_01_P, detail::City_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Subway"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_196"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_380")},
        Route{detail::Subway_01_P, detail::Slums_Tunnel_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_201"), STR("Subway_Slums"), STR("LevelExit_Spawn_BP_C_97")},
        Route{detail::TheThroat_P, detail::TrashPit_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Maw_Throat"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_188"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_296")},
        Route{detail::TutTnl_01_P, detail::CrashSite_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Crash_Tut"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_186"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_150")},
        Route{detail::TutTnl_01_P, detail::Observitory_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Tut_Obsrv"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_402"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_208")},
        Route{detail::UtilityTunnels_01_P, detail::RailYard_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("Rail_Utility"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_212")},
        Route{detail::Beach_01_P, detail::FortLeto_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Beach_Leto"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_134"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_221")},
        Route{detail::Beach_01_P, detail::RuinFeilds_P, Mode::overlap,
            STR("Level_Exit_BP_C_0"), STR("Flood_Beach"), STR("LevelExit_Spawn_BP_C_198"), 15},
        Route{detail::Beach_01_P, detail::Refinery_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Refine_Beach"), STR("LevelExit_Spawn_BP_C_241"), 12},
        Route{detail::City_01_P, detail::Subway_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Subway"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_380"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_196")},
        Route{detail::City_01_P, detail::RailYard_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Rail_City"), STR("LevelExit_Spawn_BP_C_220"), 5},
        Route{detail::City_01_P, detail::Lake_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_387"), STR("City_Lake"), STR("LevelExit_Spawn_BP_C_3")},
        Route{detail::City_01_P, detail::Borough, Mode::overlap,
            STR("Level_Exit_BP_C_388"), STR("City_Borough"), STR("LevelExit_Spawn_BP_C_1"), 2},
        Route{detail::City_01_P, detail::Docks_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_389"), STR("Metro_Dock"), STR("LevelExit_Spawn_BP_C_2"), 7},
        Route{detail::CrossRoads_P, detail::Cistern_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("CR_Cstrn"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_268"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_178")},
        Route{detail::CrossRoads_P, detail::RailYard_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Depot_Cross_Rail"), STR("LevelExit_Spawn_BP_C_0"), 20},
        Route{detail::CrossRoads_P, detail::Observitory_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("Obsv_Etr"), STR("LevelExit_Spawn_BP_C_1"), 15},
        Route{detail::CrossRoads_P, detail::RuinFeilds_P, Mode::overlap,
            STR("Level_Exit_BP_C_273"), STR("CR_RF"), STR("LevelExit_Spawn_BP_C_1"), 20},
        Route{detail::CrossRoads_P, detail::NorthEast_Rail_P, Mode::overlap,
            STR("Level_Exit_BP_C_274"), STR("Cross_NE"), STR("LevelExit_Spawn_BP_C_269"), 20},
        Route{detail::CrossRoads_P, detail::DeadBridge_P, Mode::overlap,
            STR("Level_Exit_BP_C_275"), STR("DB_CR"), STR("LevelExit_Spawn_BP_C_4"), 15},
        Route{detail::CrossRoads_P, detail::Forest_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_276"), STR("CR_Forest"), STR("LevelExit_Spawn_BP_C_1"), 8},
        Route{detail::CrossRoads_P, detail::Lake_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_277"), STR("Lake_Cave"), STR("LevelExit_Spawn_BP_C_2"), 7},
        Route{detail::DeadBridge_P, detail::Lake_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("DB_Lake"), STR("LevelExit_Spawn_BP_C_5"), 2},
        Route{detail::DeadBridge_P, detail::StoneMaze_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("DB_SM"), STR("LevelExit_Spawn_BP_C_720"), 10},
        Route{detail::DeadBridge_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_3"), STR("DB_CR"), STR("LevelExit_Spawn_BP_C_275"), 15},
        Route{detail::DeadEnd_01_P, detail::Perdition_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Haunt_DeadEnd"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_190"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_142")},
        Route{detail::DeadEnd_01_P, detail::NorthEast_Rail_P, Mode::overlap,
            STR("Level_Exit_BP_C_194"), STR("M35_End"), STR("LevelExit_Spawn_BP_C_706"), 10},
        Route{detail::Docks_01_P, detail::DryDock_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Docks_DD"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_134"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_4")},
        Route{detail::Docks_01_P, detail::Shop_ScavBar_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Bar_Docks"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_536"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::Docks_01_P, detail::Slums_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Slum_Dock"), STR("LevelExit_Spawn_BP_C_3"), 7},
        Route{detail::Docks_01_P, detail::City_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("Metro_Dock"), STR("LevelExit_Spawn_BP_C_391"), 7},
        Route{detail::Forest_01_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("CR_Forest"), STR("LevelExit_Spawn_BP_C_277"), 8},
        Route{detail::Forest_01_P, detail::NorthEast_Rail_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("M34_Forest"), STR("LevelExit_Spawn_BP_C_703"), 12},
        Route{detail::Forest_01_P, detail::SouthEast_Road_P, Mode::overlap,
            STR("Level_Exit_BP_C_3"), STR("Goldwood_South"), STR("LevelExit_Spawn_BP_C_178"), 3},
        Route{detail::Forest_01_P, detail::SavloEstate_P, Mode::overlap,
            STR("Level_Exit_BP_C_4"), STR("Manor_Forest"), STR("LevelExit_Spawn_BP_C_233"), 10},
        Route{detail::Gorge_01_P, detail::Swamp_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Swamp_Scar"), STR("LevelExit_Spawn_BP_C_109"), 15},
        Route{detail::Lake_01_P, detail::Shop_BlackMarket_01, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("BlackMarket"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_50"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_284")},
        Route{detail::Lake_01_P, detail::RailYard_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Depot_Lake"), STR("LevelExit_Spawn_BP_C_610"), 2},
        Route{detail::Lake_01_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("Lake_Cave"), STR("LevelExit_Spawn_BP_C_274"), 7},
        Route{detail::Lake_01_P, detail::City_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_3"), STR("City_Lake"), STR("LevelExit_Spawn_BP_C_387")},
        Route{detail::Lake_01_P, detail::DeadBridge_P, Mode::overlap,
            STR("Level_Exit_BP_C_5"), STR("DB_Lake"), STR("LevelExit_Spawn_BP_C_2"), 2},
        Route{detail::NorthEast_Rail_P, detail::Anom_Maze_01, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("M35_Warren"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_264"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_158")},
        Route{detail::NorthEast_Rail_P, detail::Shop_ShopKeep_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("ShopKeep"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_476"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::NorthEast_Rail_P, detail::OldPipes_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_3"), STR("NE_Pipe"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_694"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::NorthEast_Rail_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_269"), STR("Cross_NE"), STR("LevelExit_Spawn_BP_C_276"), 20},
        Route{detail::NorthEast_Rail_P, detail::Forest_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_703"), STR("M34_Forest"), STR("LevelExit_Spawn_BP_C_2"), 12},
        Route{detail::NorthEast_Rail_P, detail::TrashPit_P, Mode::overlap,
            STR("Level_Exit_BP_C_704"), STR("M35_Maw"), STR("LevelExit_Spawn_BP_C_169"), 8},
        Route{detail::NorthEast_Rail_P, detail::DeadEnd_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_705"), STR("M35_End"), STR("LevelExit_Spawn_BP_C_194"), 10},
        Route{detail::NorthEast_Rail_P, detail::SouthEast_Road_P, Mode::overlap,
            STR("Level_Exit_BP_C_706"), STR("M35_South"), STR("LevelExit_Spawn_BP_C_177"), 3},
        Route{detail::Observitory_P, detail::TutTnl_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Tut_Obsrv"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_208"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_402")},
        Route{detail::Observitory_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Obsv_Etr"), STR("LevelExit_Spawn_BP_C_2"), 15},
        Route{detail::Observitory_P, detail::Tut_Dock_P, Mode::overlap,
            STR("Level_Exit_BP_C_213"), STR("Obs_Dock"), STR("LevelExit_Spawn_BP_C_1"), 10},
        Route{detail::PineRidge_P, detail::Shop_Smuggler_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Pine_Fence"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_240"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::PineRidge_P, detail::StoneMaze_P, Mode::overlap,
            STR("Level_Exit_BP_C_245"), STR("Stone_Pine"), STR("LevelExit_Spawn_BP_C_760")},
        Route{detail::PineRidge_P, detail::Quarry_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_246"), STR("Pine_Quarry"), STR("LevelExit_Spawn_BP_C_1"), 10},
        Route{detail::Quarry_01_P, detail::PineRidge_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Pine_Quarry"), STR("LevelExit_Spawn_BP_C_246"), 10},
        Route{detail::RailYard_P, detail::UtilityTunnels_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Rail_Utility"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_212"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::RailYard_P, detail::Lab_E206_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("RY_Lab206"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_601"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_176")},
        Route{detail::RailYard_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_0"), STR("Depot_Cross_Rail"), STR("LevelExit_Spawn_BP_C_1"), 20},
        Route{detail::RailYard_P, detail::City_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_220"), STR("Rail_City"), STR("LevelExit_Spawn_BP_C_1"), 5},
        Route{detail::RailYard_P, detail::RuinFeilds_P, Mode::overlap,
            STR("Level_Exit_BP_C_221"), STR("Ry_Rf"), STR("LevelExit_Spawn_BP_C_2"), 8},
        Route{detail::RailYard_P, detail::Lake_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_610"), STR("Depot_Lake"), STR("LevelExit_Spawn_BP_C_1"), 2},
        Route{detail::RailYard_P, detail::Slums_P, Mode::overlap,
            STR("Level_Exit_BP_C_611"), STR("Slum_Depot"), STR("LevelExit_Spawn_BP_C_0"), 10},
        Route{detail::RuinFeilds_P, detail::Lab_EastWing_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("RgEast_Flood"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_136"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::RuinFeilds_P, detail::CrossRoads_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("CR_RF"), STR("LevelExit_Spawn_BP_C_273"), 20},
        Route{detail::RuinFeilds_P, detail::Beach_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_198"), STR("Flood_Beach"), STR("LevelExit_Spawn_BP_C_2"), 15},
        Route{detail::RuinFeilds_P, detail::RailYard_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("Ry_Rf"), STR("LevelExit_Spawn_BP_C_221"), 8},
        Route{detail::SavloEstate_P, detail::Forest_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_231"), STR("Manor_Forest"), STR("LevelExit_Spawn_BP_C_4"), 10},
        Route{detail::SavloEstate_P, detail::SouthEast_Road_P, Mode::overlap,
            STR("Level_Exit_BP_C_232"), STR("Manor_SB"), STR("LevelExit_Spawn_BP_C_179"), 10},
        Route{detail::Slums_P, detail::Slums_Tunnel_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Slums_GreenLine"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_152"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_92")},
        Route{detail::Slums_P, detail::Shop_ScavBar_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Bar_Slums"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_280"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_186")},
        Route{detail::Slums_P, detail::RailYard_P, Mode::overlap,
            STR("Level_Exit_BP_C_0"), STR("Slum_Depot"), STR("LevelExit_Spawn_BP_C_611"), 10},
        Route{detail::Slums_P, detail::Refinery_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_3"), STR("Slum_Refine"), STR("LevelExit_Spawn_BP_C_242"), 8},
        Route{detail::Slums_P, detail::Docks_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_5"), STR("Slum_Dock"), STR("LevelExit_Spawn_BP_C_1"), 7},
        Route{detail::SouthEast_Road_P, detail::Factory_SB_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("SB_Factory"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_52"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::SouthEast_Road_P, detail::Shop_Gunsmith_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("South_Gunsmith"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_170"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_270")},
        Route{detail::SouthEast_Road_P, detail::NorthEast_Rail_P, Mode::overlap,
            STR("Level_Exit_BP_C_177"), STR("M35_South"), STR("LevelExit_Spawn_BP_C_707"), 3},
        Route{detail::SouthEast_Road_P, detail::Forest_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_178"), STR("Goldwood_South"), STR("LevelExit_Spawn_BP_C_3"), 3},
        Route{detail::SouthEast_Road_P, detail::SavloEstate_P, Mode::overlap,
            STR("Level_Exit_BP_C_179"), STR("Manor_SB"), STR("LevelExit_Spawn_BP_C_231"), 10},
        Route{detail::StoneMaze_P, detail::Perdition_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Haunt_Stone"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_138"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_238")},
        Route{detail::StoneMaze_P, detail::Shop_Craftsman_01, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("ShopCraft"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_711"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::StoneMaze_P, detail::Anex_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_4"), STR("Maze_Annex"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_750"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_1")},
        Route{detail::StoneMaze_P, detail::DeadBridge_P, Mode::overlap,
            STR("Level_Exit_BP_C_720"), STR("DB_SM"), STR("LevelExit_Spawn_BP_C_1"), 10},
        Route{detail::StoneMaze_P, detail::Swamp_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_722"), STR("SM_Swamp"), STR("LevelExit_Spawn_BP_C_1"), 12},
        Route{detail::StoneMaze_P, detail::PineRidge_P, Mode::overlap,
            STR("Level_Exit_BP_C_760"), STR("Stone_Pine"), STR("LevelExit_Spawn_BP_C_245")},
        Route{detail::Swamp_01_P, detail::TheWall_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Swamp_Wall"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_104"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_238")},
        Route{detail::Swamp_01_P, detail::StoneMaze_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("SM_Swamp"), STR("LevelExit_Spawn_BP_C_722"), 12},
        Route{detail::Swamp_01_P, detail::Gorge_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_109"), STR("Swamp_Scar"), STR("LevelExit_Spawn_BP_C_1"), 15},
        Route{detail::Swamp_01_P, detail::Rev_NorthBase_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("Swamp_Rev"), STR("LevelExit_Spawn_BP_C_1"), 15},
        Route{detail::TheWall_01_P, detail::DataSilo_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("Wall_Data"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_246"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_286")},
        Route{detail::TheWall_01_P, detail::Swamp_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Swamp_Wall"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_238"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_104")},
        Route{detail::TheWall_01_P, detail::Anex_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_3"), STR("Wall_Annex"), STR("Dungeon_TransitionDoor_BP_C_0"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_364"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_0")},
        Route{detail::TrashPit_P, detail::TheThroat_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Maw_Throat"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_296"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_188")},
        Route{detail::TrashPit_P, detail::NorthEast_Rail_P, Mode::overlap,
            STR("Level_Exit_BP_C_169"), STR("M35_Maw"), STR("LevelExit_Spawn_BP_C_704"), 8},
        Route{detail::Trench_01_P, detail::Anex_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Annex_Trench"), STR("Dungeon_TransitionDoor_BP_C_3"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_10"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_93")},
        Route{detail::Trench_01_P, detail::Funnel_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_4"), STR("Trench_Funnel"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_126"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_358")},
        Route{detail::ArtifactForge_P, detail::Anex_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Anex_Forge"), STR("Dungeon_TransitionDoor_BP_C_4"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_188"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_638")},
        Route{detail::Borough, detail::City_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("City_Borough"), STR("LevelExit_Spawn_BP_C_388"), 2},
        Route{detail::Borough, detail::Rev_NorthBase_P, Mode::overlap,
            STR("Level_Exit_BP_C_2"), STR("RevTrain"), STR("LevelExit_Spawn_BP_C_2"), 10},
        Route{detail::Rev_NorthBase_P, detail::Swamp_01_P, Mode::overlap,
            STR("Level_Exit_BP_C_1"), STR("Swamp_Rev"), STR("LevelExit_Spawn_BP_C_2"), 15},
        Route{detail::Rev_NorthBase_P, detail::Borough, Mode::overlap,
            STR("Level_Exit_BP_C_4"), STR("RevTrain"), STR("LevelExit_Spawn_BP_C_2"), 10},
        Route{detail::Shop_BlackMarket_01, detail::Lake_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("BlackMarket"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_284"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_50")},
        Route{detail::Shop_Craftsman_01, detail::StoneMaze_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("ShopCraft"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_711")},
        Route{detail::Shop_Gunsmith_P, detail::SouthEast_Road_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("South_Gunsmith"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_270"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_170")},
        Route{detail::Shop_ScavBar_P, detail::Docks_01_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Bar_Docks"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_536")},
        Route{detail::Shop_ScavBar_P, detail::Slums_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_2"), STR("Bar_Slums"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_186"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_280")},
        Route{detail::Shop_ShopKeep_P, detail::NorthEast_Rail_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_0"), STR("ShopKeep"), STR("Dungeon_TransitionDoor_BP_C_2"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_476")},
        Route{detail::Shop_Smuggler_P, detail::PineRidge_P, Mode::door,
            STR("Dungeon_TransitionDoor_BP_C_1"), STR("Pine_Fence"), STR("Dungeon_TransitionDoor_BP_C_1"), std::nullopt,
            STR("Level_Exit_BP_GEN_VARIABLE_Level_Exit_BP_C_CAT_0"), STR("LevelExit_Spawn_BP_GEN_VARIABLE_LevelExit_Spawn_BP_C_CAT_240")},
    };

    static_assert([] {
        for (std::size_t i = 1; i < routes.size(); ++i)
        {
            const auto& previous = routes[i - 1];
            const auto& current = routes[i];
            if (previous.source_world > current.source_world ||
                (previous.source_world == current.source_world && previous.source_actor >= current.source_actor))
            {
                return false;
            }
        }
        return true;
    }(), "Authored exits must have unique, sorted source identities");

    [[nodiscard]] constexpr auto for_world(RC::StringViewType world) noexcept -> std::span<const Route>
    {
        const auto* end = routes.data() + routes.size();
        const auto* first = std::lower_bound(routes.data(), end, world,
                                            [](const Route& route, auto value) { return route.source_world < value; });
        const auto* last = std::upper_bound(first, end, world,
                                           [](auto value, const Route& route) { return value < route.source_world; });
        return {first, last};
    }

    [[nodiscard]] constexpr auto find(RC::StringViewType world, RC::StringViewType actor) noexcept -> const Route*
    {
        const auto entries = for_world(world);
        const auto found = std::lower_bound(entries.begin(), entries.end(), actor,
                                            [](const Route& route, auto value) { return route.source_actor < value; });
        return found != entries.end() && found->source_actor == actor ? &*found : nullptr;
    }
}
