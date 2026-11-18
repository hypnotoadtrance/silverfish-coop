cmake_minimum_required(VERSION 3.22)
project(SilverfishCoopNative LANGUAGES CXX)

set(UE4SS_SOURCE_DIR "" CACHE PATH "Path to the UE4SS v4.0.0 source checkout")
if(NOT TARGET UE4SS)
    if(NOT EXISTS "${UE4SS_SOURCE_DIR}/deps/first/Unreal/include/Unreal/Hooks.hpp")
        message(FATAL_ERROR "Set UE4SS_SOURCE_DIR to the v4.0.0 checkout with its pinned submodules.")
    endif()
    add_subdirectory("${UE4SS_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/ue4ss")
endif()

add_library(SilverfishCoopNative SHARED native_death.cpp)
target_compile_features(SilverfishCoopNative PRIVATE cxx_std_23)
target_link_libraries(SilverfishCoopNative PRIVATE UE4SS)
set_target_properties(SilverfishCoopNative PROPERTIES
    OUTPUT_NAME main
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/SilverfishCoopNative"
)
