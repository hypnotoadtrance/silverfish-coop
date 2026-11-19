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
    native_death.cpp black_stone.cpp guest_item.cpp guest_npc.cpp guest_reload_input.cpp inventory_census.cpp inventory_wipe.cpp
    item_roundtrip.cpp loot_escrow.cpp
    pickup.cpp save_copy.cpp shared_loot.cpp
    stock_rest.cpp transport_items.cpp zone_exit.cpp)
target_compile_features(SilverfishCoopNative PRIVATE cxx_std_23)
target_link_libraries(SilverfishCoopNative PRIVATE UE4SS)
set_target_properties(SilverfishCoopNative PROPERTIES
    OUTPUT_NAME main
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/SilverfishCoopNative"
)
