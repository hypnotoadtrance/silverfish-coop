cmake_minimum_required(VERSION 3.22)
project(SilverfishCoopEmbeddedRuntime LANGUAGES CXX)

if(NOT WIN32 OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
    message(FATAL_ERROR "The embedded runtime add-on requires Windows x64.")
endif()
set(SILVERFISH_UE4SS_SOURCE_DIR "" CACHE PATH "Path to the pinned UE4SS v4.0.0 source checkout")
set(SILVERFISH_CPYTHON_INCLUDE_DIR "" CACHE PATH "Headers for the exact packaged CPython 3.14 build")
set(SILVERFISH_RUNTIME_MANIFEST_SHA256 "" CACHE STRING "SHA-256 of the packaged runtime-manifest.json")
if(NOT EXISTS "${SILVERFISH_CPYTHON_INCLUDE_DIR}/Python.h")
    message(FATAL_ERROR "Supply the packaged CPython build's headers.")
endif()
string(LENGTH "${SILVERFISH_RUNTIME_MANIFEST_SHA256}" manifest_hash_length)
if(manifest_hash_length AND (NOT manifest_hash_length EQUAL 64 OR
        NOT SILVERFISH_RUNTIME_MANIFEST_SHA256 MATCHES "^[0-9a-f]+$"))
    message(FATAL_ERROR "The manifest SHA-256 must contain 64 lowercase hexadecimal characters.")
endif()

if(NOT TARGET UE4SS)
    if(NOT EXISTS "${SILVERFISH_UE4SS_SOURCE_DIR}/deps/first/Unreal/include/Unreal/Hooks.hpp")
        message(FATAL_ERROR "Set SILVERFISH_UE4SS_SOURCE_DIR to the v4.0.0 checkout with its pinned submodules.")
    endif()
    add_subdirectory("${SILVERFISH_UE4SS_SOURCE_DIR}" "${CMAKE_CURRENT_BINARY_DIR}/ue4ss")
endif()

add_library(SilverfishCoopEmbeddedRuntime SHARED embedded_runtime.cpp)
target_compile_features(SilverfishCoopEmbeddedRuntime PRIVATE cxx_std_23)
target_include_directories(SilverfishCoopEmbeddedRuntime PRIVATE "${SILVERFISH_CPYTHON_INCLUDE_DIR}")
target_compile_definitions(SilverfishCoopEmbeddedRuntime PRIVATE
    NOMINMAX WIN32_LEAN_AND_MEAN Py_NO_LINK_LIB
    SILVERFISH_RUNTIME_MANIFEST_SHA256="${SILVERFISH_RUNTIME_MANIFEST_SHA256}")
target_link_libraries(SilverfishCoopEmbeddedRuntime PRIVATE UE4SS bcrypt shell32)
set_target_properties(SilverfishCoopEmbeddedRuntime PROPERTIES
    OUTPUT_NAME main
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/$<CONFIG>/SilverfishCoopEmbeddedRuntime"
)
