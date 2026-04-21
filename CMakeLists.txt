cmake_minimum_required(VERSION 3.16)

project(NiobiumClient VERSION 1.0.0 LANGUAGES CXX)

# Use ccache if available
find_program(CCACHE_PROGRAM ccache)
if(CCACHE_PROGRAM)
  set_property(GLOBAL PROPERTY RULE_LAUNCH_COMPILE "${CCACHE_PROGRAM}")
endif()

# C++17 standard
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Default build type
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Debug CACHE STRING "Debug, Release, RelWithDebInfo or MinSizeRel" FORCE)
endif()

# Compiler flags
# -Wno-ignored-qualifiers and -Wno-missing-field-initializers are required for
# OpenFHE headers which trigger these warnings under -Werror.
# -Wunused-but-set-variable is in clang's -Wall but not in GCC's, so add it
# explicitly so both toolchains catch the same class of dead-code bugs locally
# before CI (macOS runners use clang; Linux dev machines often use GCC).
set(CMAKE_CXX_FLAGS_DEBUG   "-Og -g -Wall -Wextra -Wunused-but-set-variable -Wno-unknown-pragmas -Wno-unused-parameter -Wno-ignored-qualifiers -Wno-missing-field-initializers -Werror"
    CACHE STRING "C++ flags for Debug builds" FORCE)
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -Wall -Wextra -Wunused-but-set-variable -Wno-unknown-pragmas -Wno-unused-parameter -Wno-ignored-qualifiers -Wno-missing-field-initializers -Werror"
    CACHE STRING "C++ flags for Release builds" FORCE)

option(BUILD_SHARED_LIBS "Build shared libraries instead of static ones" ON)
option(NIOBIUM_CLIENT_WITH_AUTO_FACADE
    "Build libnbclient_autofacade (transparent record/replay for unmodified OpenFHE programs). Requires yaml-cpp."
    ON)
option(NIOBIUM_CLIENT_WITH_FHETCH_TRANSPORT
    "Build the HTTP-based FHETCH transport (client forwarder + server daemon). Requires pthreads."
    ON)

# Threads are a hard dep for the transport (cpp-httplib spawns worker threads).
find_package(Threads)

# ==============================================================================
# Dependencies
# ==============================================================================
# The FHETCH library (libnbfhetch), the session/compiler API, and the simulator
# live in vendor/niobium-fhetch. OpenFHE is vendored as a nested submodule
# inside niobium-fhetch (vendor/niobium-fhetch/vendor/openfhe). The Makefile
# builds and installs OpenFHE, then passes OPENFHE_INSTALL_DIR down.

set(OPENFHE_INSTALL_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/lib/openfhe"
    CACHE PATH "Path to installed OpenFHE (set by Makefile)")

# OpenFHE library path (used by examples below for RPATH).
set(OPENFHE_LIBRARY_PATH "${OPENFHE_INSTALL_DIR}/lib")

# Pull in the fhetch library — this defines the niobium_fhetch target and the
# fhetch_sim CLI. We forward OPENFHE_INSTALL_DIR so its CMakeLists can find
# OpenFHE headers/libs from the same install tree the client built.
add_subdirectory(vendor/niobium-fhetch)

# ==============================================================================
# Optional: auto-facade helper library (transparent record/replay for
# unmodified OpenFHE programs; see docs/AUTO_FACADE.md).
# ==============================================================================

if(NIOBIUM_CLIENT_WITH_AUTO_FACADE)
    add_subdirectory(src/auto_facade)
endif()

if(NIOBIUM_CLIENT_WITH_FHETCH_TRANSPORT)
    add_subdirectory(src/fhetch_transport)
endif()

# ==============================================================================
# Examples (optional)
# ==============================================================================

option(NIOBIUM_CLIENT_WITH_EXAMPLES "Build examples" OFF)

if(NIOBIUM_CLIENT_WITH_EXAMPLES)
    add_subdirectory(examples)
endif()
