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
# Runtime lib (preloaded) + local simulator. Copy the SONAME file only
# (libnbfhetch.so.1) — that's niobium_session's DT_NEEDED and what the loader/preload
# resolve; the versioned .so.1.0.0 is redundant weight in the wheel.
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy
            $<TARGET_SONAME_FILE:niobium_fhetch>
            $<TARGET_FILE:fhetch_sim> ${NB_PKG}/)
# Vendored DSL compiler (pure Python). Drop dev-only tests + stale bytecode.
add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/../dsl_fhe/xcomp ${NB_PKG}/nbc
    COMMAND ${CMAKE_COMMAND} -E rm -rf
            ${NB_PKG}/nbc/tests ${NB_PKG}/nbc/__pycache__ ${NB_PKG}/__pycache__)

# --- Third-party license notices ---------------------------------------------
# The wheel bundles/redistributes native code from BSD/Apache projects, so their
# license texts must travel with it (the BSD-2/BSD-3 binary-redistribution clause).
# Shipped as package data under niobium_client/licenses/:
#   OpenFHE + openfhe-python  BSD-2-Clause  (compiled into openfhe*.so)
#   pybind11                  BSD-3-Clause  (headers compiled into the extensions)
#   libnbfhetch               Apache-2.0    (bundled .so)
#   niobium-client            Apache-2.0    (this project)
if(NIOBIUM_CLIENT_FHETCH_DIR)
    set(NB_FHETCH_SRC "${NIOBIUM_CLIENT_FHETCH_DIR}")
else()
    set(NB_FHETCH_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/niobium-fhetch")
endif()
set(NB_LIC "${NB_PKG}/licenses")

# pybind11 is a build dependency (not vendored); locate its LICENSE relative to the
# pybind11 CMake package. pybind11_DIR is an absolute cache var set by find_package
# (<site-packages>/pybind11/share/cmake/pybind11), so walk up to site-packages and
# glob the dist-info. Best-effort — a miss warns rather than failing the build.
set(NB_PYBIND11_LICENSE "")
if(pybind11_DIR)
    get_filename_component(_NB_SITE_PACKAGES "${pybind11_DIR}/../../../.." ABSOLUTE)
    file(GLOB _NB_PB_LIC
        "${_NB_SITE_PACKAGES}/pybind11-*.dist-info/licenses/LICENSE"
        "${_NB_SITE_PACKAGES}/pybind11-*.dist-info/LICENSE"
        "${_NB_SITE_PACKAGES}/pybind11/LICENSE")
    if(_NB_PB_LIC)
        list(GET _NB_PB_LIC 0 NB_PYBIND11_LICENSE)
    endif()
endif()

add_custom_command(TARGET niobium_client_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${NB_LIC}
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_FHETCH_SRC}/vendor/openfhe/LICENSE        ${NB_LIC}/LICENSE.OpenFHE
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_FHETCH_SRC}/vendor/openfhe-python/LICENSE ${NB_LIC}/LICENSE.openfhe-python
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_FHETCH_SRC}/LICENSE                       ${NB_LIC}/LICENSE.libnbfhetch
    COMMAND ${CMAKE_COMMAND} -E copy ${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE         ${NB_LIC}/LICENSE.niobium-client
    COMMENT "Bundling third-party license notices -> ${NB_LIC}")

if(NB_PYBIND11_LICENSE AND EXISTS "${NB_PYBIND11_LICENSE}")
    add_custom_command(TARGET niobium_client_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy "${NB_PYBIND11_LICENSE}" ${NB_LIC}/LICENSE.pybind11)
else()
    message(WARNING
        "pybind11 LICENSE not found via importlib.metadata; the wheel will omit "
        "licenses/LICENSE.pybind11 (BSD-3-Clause). Ensure pybind11 ships its "
        "dist-info, or add the notice manually.")
endif()

# Wheel packaging (scikit-build-core installs only this component; niobium-fhetch's
# lib/header install rules stay out of the wheel). delocate/auditwheel then graft
# the OpenFHE dylibs next to the extensions.
install(DIRECTORY ${NB_PKG}/ DESTINATION niobium_client COMPONENT python_wheel)
