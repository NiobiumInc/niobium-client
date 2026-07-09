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
    "Build + assemble the niobium_client Python package: openfhe + niobium_session (via niobium-fhetch WITH_PYTHON) + _archive + vendored nbc + fhetch_sim. Requires pybind11 + Python dev headers (pass -Dpybind11_DIR and -DPython_EXECUTABLE)."
    OFF)

# The crypto/session extensions come from niobium-fhetch's own WITH_PYTHON build;
# turn it on before add_subdirectory() below so its openfhe/niobium_session targets
# land in ${CMAKE_BINARY_DIR}/python.
if(NIOBIUM_CLIENT_WITH_PYTHON)
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

if(NIOBIUM_CLIENT_FHETCH_DIR)
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
# Python package assembly (optional; see docs, PYTHON_WHEEL_PLAN)
# ==============================================================================
# niobium-fhetch (above, WITH_PYTHON) already built openfhe + niobium_session into
# ${CMAKE_BINARY_DIR}/python. Here we add the _archive binding (TLV pack/unpack;
# pure stdlib, no OpenFHE) and assemble the importable package tree:
#   ${CMAKE_BINARY_DIR}/niobium_client/
#     __init__.py client.py session.py VERSION       (python/niobium_client/)
#     openfhe*.so niobium_session*.so                 (from ${CMAKE_BINARY_DIR}/python)
#     _archive*.so                                    (built here)
#     libnbfhetch.*                                   (RTLD_GLOBAL-preloaded by __init__)
#     fhetch_sim                                       (bundled; local replay())
#     nbc/                                            (vendored dsl_fhe/xcomp)

if(NIOBIUM_CLIENT_WITH_PYTHON)
    find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)
    find_package(pybind11 CONFIG REQUIRED)

    set(NB_PKG "${CMAKE_BINARY_DIR}/niobium_client")

    pybind11_add_module(_archive
        python/archive_binding.cpp
        src/fhetch_transport/archive.cpp)
    target_include_directories(_archive PRIVATE src/fhetch_transport)
    set_target_properties(_archive PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${NB_PKG}")

    # Assemble once the natives exist. openfhe is an ExternalProject (not a normal
    # target), so depend on the openfhe_python step and copy its installed .so.
    add_custom_target(niobium_client_pkg ALL
        DEPENDS _archive niobium_session openfhe_python fhetch_sim
        COMMENT "Assembling niobium_client package -> ${NB_PKG}")

    # Pure-Python sources + VERSION.
    add_custom_command(TARGET niobium_client_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
                ${CMAKE_CURRENT_SOURCE_DIR}/python/niobium_client ${NB_PKG})
    # Crypto + session extensions (both installed to ${CMAKE_BINARY_DIR}/python).
    add_custom_command(TARGET niobium_client_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy
                ${CMAKE_BINARY_DIR}/python/openfhe*.so
                ${CMAKE_BINARY_DIR}/python/niobium_session*.so
                ${NB_PKG}/)
    # Runtime lib (preloaded) + local simulator.
    add_custom_command(TARGET niobium_client_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy
                $<TARGET_FILE:niobium_fhetch> $<TARGET_FILE:fhetch_sim> ${NB_PKG}/)
    # Vendored DSL compiler (pure Python). Drop its dev-only unit tests and any
    # stale bytecode so the wheel ships only the compiler runtime.
    add_custom_command(TARGET niobium_client_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
                ${CMAKE_CURRENT_SOURCE_DIR}/dsl_fhe/xcomp ${NB_PKG}/nbc
        COMMAND ${CMAKE_COMMAND} -E rm -rf
                ${NB_PKG}/nbc/tests ${NB_PKG}/nbc/__pycache__ ${NB_PKG}/__pycache__)
endif()
