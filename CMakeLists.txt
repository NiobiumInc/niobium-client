cmake_minimum_required(VERSION 3.16)

# Version is read from the root VERSION file — the single source of truth shared
# with the packaging channels and the source-tarball workflow.
file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" NIOBIUM_CLIENT_VERSION)
project(NiobiumClient VERSION ${NIOBIUM_CLIENT_VERSION} LANGUAGES CXX)

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
option(NIOBIUM_CLIENT_WITH_PYTHON
    "Build + assemble the niobium_sdk Python package: openfhe + niobium_session (via niobium-fhetch WITH_PYTHON) + _archive + vendored nbc + fhetch_sim. Requires pybind11 + Python dev headers (pass -Dpybind11_DIR and -DPython_EXECUTABLE)."
    OFF)

# The crypto/session extensions come from niobium-fhetch. Two modes:
#  * NIOBIUM_RUNTIME_PREFIX set -> consume a prebuilt niobium-runtime (no OpenFHE build);
#    only the bindings compile against it (bindings/python runtime mode).
#  * else -> from-source: niobium-fhetch WITH_PYTHON builds OpenFHE + libnbfhetch +
#    fhetch_sim + the bindings, landing them in ${CMAKE_BINARY_DIR}/python.
if(NOT NIOBIUM_RUNTIME_PREFIX)
    set(NIOBIUM_RUNTIME_PREFIX "$ENV{NIOBIUM_RUNTIME_PREFIX}")
endif()
if(NIOBIUM_CLIENT_WITH_PYTHON AND NOT NIOBIUM_RUNTIME_PREFIX)
    set(WITH_PYTHON ON CACHE BOOL "niobium-fhetch: build the Python bindings" FORCE)
endif()

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
#
# When this project is built from a parent (e.g. niobium-compiler) that owns
# its own niobium-fhetch checkout, set NIOBIUM_CLIENT_FHETCH_DIR to that path.
# The parent's checkout is then used directly; our own vendor/niobium-fhetch
# submodule does not need to be initialized. JSON_INCLUDE_DIR and
# OPENFHE_INSTALL_DIR are honored by niobium-fhetch's CMakeLists, so injecting
# them here covers the full transitive dep set.
set(NIOBIUM_CLIENT_FHETCH_DIR "" CACHE PATH
    "External niobium-fhetch source tree to use instead of vendor/niobium-fhetch (empty = use vendored submodule)")

if(NIOBIUM_RUNTIME_PREFIX)
    # Runtime mode: consume the prebuilt core. Build only the fhetch bindings against it
    # (bindings/python sees NIOBIUM_RUNTIME_PREFIX -> runtime mode); OpenFHE, libnbfhetch,
    # and fhetch_sim come from the runtime, not from a source build.
    add_subdirectory(vendor/niobium-fhetch/bindings/python
                     ${CMAKE_BINARY_DIR}/_fhetch_bindings)
elseif(NIOBIUM_CLIENT_FHETCH_DIR)
    add_subdirectory(${NIOBIUM_CLIENT_FHETCH_DIR}
                     ${CMAKE_BINARY_DIR}/_deps/niobium-fhetch-build)
else()
    add_subdirectory(vendor/niobium-fhetch)
endif()

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

# ==============================================================================
# Python package (optional; see PYTHON_WHEEL_PLAN)
# ==============================================================================
# niobium-fhetch (above, WITH_PYTHON) built openfhe + niobium_session into
# ${CMAKE_BINARY_DIR}/python. python/CMakeLists.txt adds the _archive binding and
# assembles + installs the importable niobium_sdk/ package (component mode).

if(NIOBIUM_CLIENT_WITH_PYTHON)
    find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(pybind11 CONFIG REQUIRED)
    add_subdirectory(python)
endif()
