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

# Compiler flags matching niobium-compiler conventions
# -Wno-ignored-qualifiers and -Wno-missing-field-initializers are required for
# OpenFHE headers which trigger these warnings under -Werror.
set(CMAKE_CXX_FLAGS_DEBUG   "-Og -g -Wall -Wextra -Wno-unknown-pragmas -Wno-unused-parameter -Wno-ignored-qualifiers -Wno-missing-field-initializers -Werror"
    CACHE STRING "C++ flags for Debug builds" FORCE)
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -Wall -Wextra -Wno-unknown-pragmas -Wno-unused-parameter -Wno-ignored-qualifiers -Wno-missing-field-initializers -Werror"
    CACHE STRING "C++ flags for Release builds" FORCE)

# Option to select library type
option(BUILD_SHARED_LIBS "Build shared libraries instead of static ones" ON)

# ==============================================================================
# OpenFHE dependency (built and installed separately via Makefile targets)
# ==============================================================================

set(OPENFHE_INSTALL_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/lib/openfhe"
    CACHE PATH "Path to installed OpenFHE (set by Makefile)")

# OpenFHE include paths
set(OPENFHE_INCLUDE_DIRS
    "${OPENFHE_INSTALL_DIR}/include/openfhe"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/core"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/pke"
    "${OPENFHE_INSTALL_DIR}/include/openfhe/binfhe"
)

# OpenFHE library path
set(OPENFHE_LIBRARY_PATH "${OPENFHE_INSTALL_DIR}/lib")

# nlohmann/json (header-only, single_include)
set(JSON_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/vendor/json/single_include")

# OpenFHE libraries
set(OPENFHE_LIBRARIES
    OPENFHEbinfhe
    OPENFHEcore
    OPENFHEpke
)

# ==============================================================================
# Library sources
# ==============================================================================

set(LIB_SOURCES
    src/auto_facade.cpp
    src/compiler.cpp
    src/fhetch_api.cpp
    src/fhetch_sim/instruction.cpp
    src/fhetch_sim/simulator.cpp
    src/probes.cpp
    src/trace_writer.cpp
)

# ==============================================================================
# Library target
# ==============================================================================

add_library(niobium_client ${LIB_SOURCES})

# Output name: libnbclient.so / libnbclient.a
set_target_properties(niobium_client PROPERTIES
    OUTPUT_NAME "nbclient"
    VERSION     ${PROJECT_VERSION}
    SOVERSION   1
    POSITION_INDEPENDENT_CODE ON
)

# Include paths — own headers (public) and internal headers (private)
target_include_directories(niobium_client
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/src/fhetch_sim
        ${JSON_INCLUDE_DIR}
)

# OpenFHE include paths — BUILD_INTERFACE only (not propagated to install tree;
# consumers set their own OpenFHE paths).
foreach(_dir ${OPENFHE_INCLUDE_DIRS})
  target_include_directories(niobium_client
      PUBLIC $<BUILD_INTERFACE:${_dir}>)
endforeach()

# Enable OPENFHE_CPROBES so the instrumented branch activates probes
target_compile_definitions(niobium_client PUBLIC OPENFHE_CPROBES)

# Link threading library (required for std::mutex in trace_writer / probes)
find_package(Threads REQUIRED)
target_link_libraries(niobium_client PRIVATE Threads::Threads)

# Link OpenFHE libraries
# niobium_client directly references OpenFHE symbols (g_hollow_mode, probes)
# so it must link against OpenFHE at the library level.
target_link_directories(niobium_client
    PUBLIC $<BUILD_INTERFACE:${OPENFHE_LIBRARY_PATH}>)
target_link_libraries(niobium_client PUBLIC ${OPENFHE_LIBRARIES})


# RPATH so the executable can find shared libs at runtime
if(APPLE)
  set_target_properties(niobium_client PROPERTIES
      BUILD_RPATH   "@loader_path;${OPENFHE_LIBRARY_PATH}"
      INSTALL_RPATH "@loader_path/../lib;${OPENFHE_LIBRARY_PATH}"
  )
else()
  set_target_properties(niobium_client PROPERTIES
      BUILD_RPATH   "${OPENFHE_LIBRARY_PATH}"
      INSTALL_RPATH "$ORIGIN;${OPENFHE_LIBRARY_PATH}"
  )
endif()

# ==============================================================================
# Install
# ==============================================================================

include(GNUInstallDirs)

install(TARGETS niobium_client
    EXPORT NiobiumClientTargets
    LIBRARY  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    ARCHIVE  DESTINATION ${CMAKE_INSTALL_LIBDIR}
    RUNTIME  DESTINATION ${CMAKE_INSTALL_BINDIR}
)

# Public headers
install(DIRECTORY include/niobium
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
    FILES_MATCHING PATTERN "*.h"
)

# CMake package config
install(EXPORT NiobiumClientTargets
    FILE        NiobiumClientTargets.cmake
    NAMESPACE   Niobium::
    DESTINATION ${CMAKE_INSTALL_LIBDIR}/cmake/NiobiumClient
)

# ==============================================================================
# FHETCH Simulator CLI
# ==============================================================================

add_executable(fhetch_sim src/fhetch_sim/main.cpp)
target_link_libraries(fhetch_sim PRIVATE niobium_client)
if(APPLE)
  set_target_properties(fhetch_sim PROPERTIES
      BUILD_RPATH "@loader_path;${OPENFHE_LIBRARY_PATH}")
else()
  set_target_properties(fhetch_sim PROPERTIES
      BUILD_RPATH "${OPENFHE_LIBRARY_PATH}")
endif()

# ==============================================================================
# Examples (optional)
# ==============================================================================

option(NIOBIUM_CLIENT_WITH_EXAMPLES "Build examples" OFF)

if(NIOBIUM_CLIENT_WITH_EXAMPLES)
    add_subdirectory(examples)
endif()
