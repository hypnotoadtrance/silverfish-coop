cmake_minimum_required(VERSION 3.22)
project(SilverfishCoopBootstrap LANGUAGES CXX)

set(SILVERFISH_UE4SS_SOURCE_DIR "" CACHE PATH "Path to the pinned UE4SS v4.0.0 source checkout")
set(SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256 "" CACHE STRING "SHA-256 of the packaged coordinator executable")
option(SILVERFISH_BOOTSTRAP_HEADLESS_PROOF "Build a replica-only headless bootstrap test adapter" OFF)
string(LENGTH "${SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256}" coordinator_hash_length)
if(coordinator_hash_length AND (NOT coordinator_hash_length EQUAL 64 OR
        NOT SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256 MATCHES "^[0-9a-f]+$"))
    message(FATAL_ERROR "Set the coordinator SHA-256 to exactly 64 lowercase hexadecimal characters.")
endif()

if(NOT TARGET UE4SS)
    if(NOT EXISTS "${SILVERFISH_UE4SS_SOURCE_DIR}/deps/first/Unreal/include/Unreal/Hooks.hpp")
        message(FATAL_ERROR "Set SILVERFISH_UE4SS_SOURCE_DIR to the v4.0.0 checkout with its pinned submodules.")
    endif()
    add_subdirectory("${SILVERFISH_UE4SS_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/ue4ss")
endif()

add_library(SilverfishCoopBootstrap SHARED bootstrap.cpp)
target_compile_features(SilverfishCoopBootstrap PRIVATE cxx_std_23)
target_compile_definitions(SilverfishCoopBootstrap PRIVATE
    NOMINMAX WIN32_LEAN_AND_MEAN
    SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256="${SILVERFISH_BOOTSTRAP_COORDINATOR_SHA256}")
if(SILVERFISH_BOOTSTRAP_HEADLESS_PROOF)
    target_compile_definitions(SilverfishCoopBootstrap PRIVATE SILVERFISH_BOOTSTRAP_HEADLESS_PROOF)
endif()
target_link_libraries(SilverfishCoopBootstrap PRIVATE UE4SS bcrypt user32)
set_target_properties(SilverfishCoopBootstrap PROPERTIES
    OUTPUT_NAME main
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/SilverfishCoopBootstrap"
)
