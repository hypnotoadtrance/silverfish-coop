cmake_minimum_required(VERSION 3.22)
project(SilverfishCoopNative LANGUAGES CXX)

set(SILVERFISH_UE4SS_SOURCE_DIR "" CACHE PATH "Path to the UE4SS v4.0.0 source checkout")
if(NOT TARGET UE4SS)
    if(NOT EXISTS "${SILVERFISH_UE4SS_SOURCE_DIR}/deps/first/Unreal/include/Unreal/Hooks.hpp")
        message(FATAL_ERROR "Set SILVERFISH_UE4SS_SOURCE_DIR to the v4.0.0 checkout with its pinned submodules.")
    endif()
    add_subdirectory("${SILVERFISH_UE4SS_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/ue4ss")
endif()

add_library(SilverfishCoopNative SHARED
    native_death.cpp anomaly_fields.cpp authored_pickup.cpp bag_projection.cpp black_stone.cpp broker_input.cpp broker_menu.cpp
    campaign_npc.cpp enemy_loot.cpp stock_enemy_drops.cpp finite_shop.cpp flood_jammer.cpp lenny_quest.cpp fresh_clock.cpp quest_rewards.cpp borough_barter.cpp
    guest_armor.cpp guest_clock.cpp guest_consumable.cpp guest_health.cpp guest_item.cpp guest_melee.cpp guest_movement.cpp guest_npc.cpp guest_primary_input.cpp guest_regen.cpp guest_reload_input.cpp guest_sickness.cpp
    interaction_input.cpp critter_view.cpp inventory_census.cpp inventory_wipe.cpp local_bridge.cpp session_observation.cpp
    guest_stack_drop.cpp guest_drop_projection.cpp
    inventory_view.cpp item_roundtrip.cpp kill_volume.cpp electronic_locker.cpp loot_crate_input.cpp loot_escrow.cpp mindcore_input.cpp mosby_station_input.cpp
    mindcore.cpp mindcore_carry.cpp datasilo_lift.cpp datasilo_door.cpp nothing_return.cpp pickup.cpp save_copy.cpp shared_loot.cpp shop_view.cpp
    host_landmine.cpp host_dig.cpp host_invitation_clipboard.cpp host_weather.cpp session_exit_input.cpp listener_bind.cpp
    stock_rest.cpp title_entry.cpp title_startup.cpp transport_items.cpp world_scene.cpp zone_exit.cpp)
target_compile_features(SilverfishCoopNative PRIVATE cxx_std_23)
target_link_libraries(SilverfishCoopNative PRIVATE UE4SS)
if(MSVC)
    target_link_options(SilverfishCoopNative PRIVATE "/PDBALTPATH:SilverfishCoopNative.pdb")
endif()
set_target_properties(SilverfishCoopNative PROPERTIES
    OUTPUT_NAME main
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/SilverfishCoopNative"
)
