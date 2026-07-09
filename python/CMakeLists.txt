# niobium_client Python package — native bits + assembly.
#
# Dual-mode:
#   * Standalone (cmake -S python -B build/python): fast submit-only build — just
#     the _archive binding + the pure-Python sources, no OpenFHE. Backs the
#     `test-submit-python-release` dev target.
#   * Component (add_subdirectory(python) from the top-level CMakeLists, under
#     NIOBIUM_CLIENT_WITH_PYTHON): the full wheel assembly — pulls niobium-fhetch's
#     openfhe + niobium_session, adds _archive, and assembles the importable
#     niobium_client/ tree, installed as the `python_wheel` component (see
#     PYTHON_WHEEL_PLAN). The parent has already found Python + pybind11.
cmake_minimum_required(VERSION 3.16)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(NB_PY_STANDALONE ON)
    project(niobium_client_python CXX)
    set(CMAKE_CXX_STANDARD 17)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    find_package(pybind11 CONFIG REQUIRED)
else()
    set(NB_PY_STANDALONE OFF)
endif()

set(NB_PKG "${CMAKE_BINARY_DIR}/niobium_client")
set(NB_TRANSPORT "${CMAKE_CURRENT_SOURCE_DIR}/../src/fhetch_transport")

# _archive — TLV "NBAR" pack/unpack. Compiles archive.cpp directly (same source as
# niobium_fhetch_archive) so it tracks the C++ wire format; pure stdlib, no OpenFHE.
pybind11_add_module(_archive
    archive_binding.cpp
    ${NB_TRANSPORT}/archive.cpp)
target_include_directories(_archive PRIVATE ${NB_TRANSPORT})
set_target_properties(_archive PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${NB_PKG}")

if(NB_PY_STANDALONE)
    # Submit-only: _archive + the pure-Python sources next to it.
    add_custom_command(TARGET _archive POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
                ${CMAKE_CURRENT_SOURCE_DIR}/niobium_client ${NB_PKG}
        COMMENT "Assembling niobium_client package (submit-only)")
    return()
endif()

# --- Full assembly (component mode) -------------------------------------------
# niobium-fhetch (WITH_PYTHON) already built openfhe + niobium_session into
# ${CMAKE_BINARY_DIR}/python. Assemble the importable package:
#   ${CMAKE_BINARY_DIR}/niobium_client/
#     __init__.py client.py session.py VERSION       (niobium_client/)
#     openfhe*.so niobium_session*.so                 (from ${CMAKE_BINARY_DIR}/python)
#     _archive*.so                                    (built above)
#     libnbfhetch.*                                   (RTLD_GLOBAL-preloaded by __init__)
#     fhetch_sim                                       (bundled; local replay())
#     nbc/                                            (vendored dsl_fhe/xcomp)
add_custom_target(niobium_client_pkg ALL
    DEPENDS _archive niobium_session openfhe_python fhetch_sim
    COMMENT "Assembling niobium_client package -> ${NB_PKG}")

# Pure-Python sources + VERSION.
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/niobium_client ${NB_PKG})
# Crypto + session extensions (both installed to ${CMAKE_BINARY_DIR}/python).
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy
            ${CMAKE_BINARY_DIR}/python/openfhe*.so
            ${CMAKE_BINARY_DIR}/python/niobium_session*.so
            ${NB_PKG}/)
# Runtime lib (preloaded) + local simulator. Copy BOTH the versioned file and the
# soname (libnbfhetch.so.1) — niobium_session's DT_NEEDED is the soname, which is
# what auditwheel resolves (and the loader/preload) at repair/runtime.
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy
            $<TARGET_FILE:niobium_fhetch>
            $<TARGET_SONAME_FILE:niobium_fhetch>
            $<TARGET_FILE:fhetch_sim> ${NB_PKG}/)
# Vendored DSL compiler (pure Python). Drop dev-only tests + stale bytecode.
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/../dsl_fhe/xcomp ${NB_PKG}/nbc
    COMMAND ${CMAKE_COMMAND} -E rm -rf
            ${NB_PKG}/nbc/tests ${NB_PKG}/nbc/__pycache__ ${NB_PKG}/__pycache__)

# Wheel packaging (scikit-build-core installs only this component; niobium-fhetch's
# lib/header install rules stay out of the wheel). delocate/auditwheel then graft
# the OpenFHE dylibs next to the extensions.
install(DIRECTORY ${NB_PKG}/ DESTINATION niobium_client COMPONENT python_wheel)
