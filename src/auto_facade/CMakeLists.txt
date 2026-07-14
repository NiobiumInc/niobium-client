# ==============================================================================
# libnbclient_autofacade — transparent record/replay facade for unmodified
# OpenFHE programs.
#
# Provides strong definitions for the niobium_auto::* hooks whose weak
# stubs live in libnbfhetch (vendor/niobium-fhetch/src/auto_facade.cpp). When
# a user binary links this library, the Niobium-instrumented OpenFHE branch
# will route Serial::{De,}SerializeFromFile, Eval*, and friends through the
# recording / replay pipeline — with no niobium:: calls required in user
# code. Driven by a YAML config file discovered at run time.
#
# Ported from niobium-compiler's src/AutoFacade.cpp (PR #1035). The
# NIOBIUM_CLIENT_WITH_AUTO_FACADE CMake option gates this library so hosts
# that don't want the auto-facade can drop the yaml-cpp dependency.
# ==============================================================================

# yaml-cpp: try system install first (libyaml-cpp-dev / brew yaml-cpp); fall
# back to FetchContent so CI and fresh checkouts work without a global apt
# install. yaml-cpp 0.8.0 is small and compiles in a few seconds.
find_package(yaml-cpp QUIET)
if(NOT yaml-cpp_FOUND)
    message(STATUS "yaml-cpp not found system-wide — fetching 0.8.0 via FetchContent")
    include(FetchContent)
    set(YAML_CPP_BUILD_TESTS    OFF CACHE BOOL "" FORCE)
    set(YAML_CPP_BUILD_TOOLS    OFF CACHE BOOL "" FORCE)
    set(YAML_CPP_BUILD_CONTRIB  OFF CACHE BOOL "" FORCE)
    set(YAML_CPP_INSTALL        OFF CACHE BOOL "" FORCE)
    # yaml-cpp 0.8.0 ships a CMakeLists with cmake_minimum_required(2.8.12)
    # which CMake 4.x rejects. Force the policy version to 3.5 during its
    # configure step so we don't need to patch the vendor source.
    set(CMAKE_POLICY_VERSION_MINIMUM 3.5 CACHE INTERNAL "" FORCE)
    FetchContent_Declare(
        yaml_cpp
        GIT_REPOSITORY https://github.com/jbeder/yaml-cpp.git
        GIT_TAG        0.8.0
        GIT_SHALLOW    TRUE
    )
    FetchContent_MakeAvailable(yaml_cpp)
    # yaml-cpp 0.8.0's emitterutils.cpp uses uint16_t/uint32_t without
    # #include <cstdint>, which newer clang/gcc libstdc++ headers no longer
    # supply transitively. Force-include it for this target only, leaving the
    # vendored source untouched (mirrors the -Wno-restrict scoping elsewhere).
    # Scoped to C++ TUs via COMPILE_LANGUAGE. (MSVC would need "/FI cstdint"
    # instead of "-include cstdint" should Windows ever be supported.)
    target_compile_options(yaml-cpp PRIVATE
        "$<$<COMPILE_LANGUAGE:CXX>:SHELL:-include cstdint>")
endif()

add_library(niobium_client_autofacade STATIC
    AutoFacade.cpp
)

set_target_properties(niobium_client_autofacade PROPERTIES
    POSITION_INDEPENDENT_CODE ON
)

target_include_directories(niobium_client_autofacade
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/include>
        # niobium_auto_scheme.h (shipped inside OpenFHE) does
        # #include "Utils/ScopedPause.h" — expose the subdir so that resolves.
        $<BUILD_INTERFACE:${CMAKE_SOURCE_DIR}/include/niobium>
)

# OpenFHE headers come from the same install tree libnbfhetch uses.
foreach(_dir
    "${OPENFHE_INSTALL_DIR}/include/openfhe"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/core"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/pke"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/binfhe"
)
    target_include_directories(niobium_client_autofacade
        PRIVATE $<BUILD_INTERFACE:${_dir}>)
endforeach()

target_link_libraries(niobium_client_autofacade
    PUBLIC niobium_fhetch  # for niobium::compiler() + OpenFHE via its PUBLIC link
    PRIVATE yaml-cpp
)

# The auto-facade has to see OPENFHE_CPROBES so it compiles the
# niobium_auto_hooks.h hook signatures consistently with the OpenFHE build
# that calls them. niobium_fhetch already sets this PUBLIC, but we repeat
# it here for clarity in case the library is used standalone.
target_compile_definitions(niobium_client_autofacade PUBLIC OPENFHE_CPROBES)
