# niobium_sdk Python package — native bits + assembly.
#
# Dual-mode:
#   * Standalone (cmake -S python -B build/python): fast submit-only build — just
#     the _archive binding + the pure-Python sources, no OpenFHE. Backs the
#     `test-submit-python-release` dev target.
#   * Component (add_subdirectory(python) from the top-level CMakeLists, under
#     NIOBIUM_CLIENT_WITH_PYTHON): the full wheel assembly — pulls niobium-fhetch's
#     openfhe + niobium_session, adds _archive, and assembles the importable
#     niobium_sdk/ tree, installed as the `python_wheel` component (see
#     PYTHON_WHEEL_PLAN). The parent has already found Python + pybind11.
cmake_minimum_required(VERSION 3.16)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(NB_PY_STANDALONE ON)
    project(niobium_sdk_python CXX)
    set(CMAKE_CXX_STANDARD 17)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    find_package(pybind11 CONFIG REQUIRED)
else()
    set(NB_PY_STANDALONE OFF)
endif()

set(NB_PKG "${CMAKE_BINARY_DIR}/niobium_sdk")
set(NB_TRANSPORT "${CMAKE_CURRENT_SOURCE_DIR}/../src/fhetch_transport")

# _archive — TLV "NBAR" pack/unpack. Compiles archive.cpp directly (same source as
# niobium_fhetch_archive) so it tracks the C++ wire format; pure stdlib, no OpenFHE.
pybind11_add_module(_archive
    archive_binding.cpp
    ${NB_TRANSPORT}/archive.cpp)
target_include_directories(_archive PRIVATE ${NB_TRANSPORT})
set_target_properties(_archive PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${NB_PKG}")

# Vendored Fog CLI: scripts/fog copied verbatim as niobium_sdk/_fog.py (single
# source of truth — same file the C++ stack ships; the wheel never forks it).
# niobium_sdk.fog reuses its control plane and adds an in-process submit.
set(NB_FOG_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/fog")

if(NB_PY_STANDALONE)
    # Submit-only: _archive + the pure-Python sources next to it.
    add_custom_command(TARGET _archive POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
                ${CMAKE_CURRENT_SOURCE_DIR}/niobium_sdk ${NB_PKG}
        COMMAND ${CMAKE_COMMAND} -E copy ${NB_FOG_SRC} ${NB_PKG}/_fog.py
        COMMENT "Assembling niobium_sdk package (submit-only)")
    return()
endif()

# --- Full assembly ------------------------------------------------------------
# The bindings (openfhe + niobium_session) were built into ${CMAKE_BINARY_DIR}/python
# by niobium-fhetch's binding recipe — from-source (WITH_PYTHON), or against the prebuilt
# runtime when NIOBIUM_RUNTIME_PREFIX is set. Assemble the importable package:
#   ${CMAKE_BINARY_DIR}/niobium_sdk/
#     __init__.py client.py session.py VERSION       (niobium_sdk/)
#     openfhe*.so niobium_session*.so                 (from ${CMAKE_BINARY_DIR}/python)
#     _archive*.so                                    (built above)
#     libnbfhetch.* (+ OpenFHE in runtime mode)        (RTLD_GLOBAL-preloaded by __init__)
#     fhetch_sim                                       (bundled; local replay())
#     nbc/                                            (vendored dsl_fhe/xcomp)
if(NOT NIOBIUM_RUNTIME_PREFIX)
    set(NIOBIUM_RUNTIME_PREFIX "$ENV{NIOBIUM_RUNTIME_PREFIX}")
endif()
# fhetch_sim is a from-source target only; in runtime mode it is a prebuilt binary.
set(NB_PKG_DEPS _archive niobium_session openfhe_python)
if(NOT NIOBIUM_RUNTIME_PREFIX)
    list(APPEND NB_PKG_DEPS fhetch_sim)
endif()
add_custom_target(niobium_sdk_pkg ALL
    DEPENDS ${NB_PKG_DEPS}
    COMMENT "Assembling niobium_sdk package -> ${NB_PKG}")

# Pure-Python sources + VERSION, plus the vendored Fog CLI (scripts/fog -> _fog.py).
add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/niobium_sdk ${NB_PKG}
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_FOG_SRC} ${NB_PKG}/_fog.py)
# Crypto + session extensions (both installed to ${CMAKE_BINARY_DIR}/python).
add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy
            ${CMAKE_BINARY_DIR}/python/openfhe*.so
            ${CMAKE_BINARY_DIR}/python/niobium_session*.so
            ${NB_PKG}/)
# Native libs (RTLD_GLOBAL-preloaded by __init__) + the local simulator.
if(NIOBIUM_RUNTIME_PREFIX)
    # Runtime mode: libnbfhetch, the OpenFHE dylibs, and fhetch_sim come from the runtime
    # (no from-source targets exist). Bundle them so the assembled tree is self-contained.
    file(GLOB NB_RT_LIBS
        "${NIOBIUM_RUNTIME_PREFIX}/lib/libnbfhetch*"
        "${NIOBIUM_RUNTIME_PREFIX}/lib/libOPENFHE*")
    add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy ${NB_RT_LIBS} ${NB_PKG}/
        COMMAND ${CMAKE_COMMAND} -E copy ${NIOBIUM_RUNTIME_PREFIX}/bin/fhetch_sim ${NB_PKG}/)
else()
    # From-source: copy the libnbfhetch SONAME file (libnbfhetch.so.1 — niobium_session's
    # DT_NEEDED, what the loader/preload resolve; the versioned .so.1.0.0 is redundant
    # weight) + fhetch_sim. The OpenFHE dylibs are grafted by delocate/auditwheel from
    # OPENFHE_INSTALL_DIR at repair time.
    add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy
                $<TARGET_SONAME_FILE:niobium_fhetch>
                $<TARGET_FILE:fhetch_sim> ${NB_PKG}/)
endif()
# Vendored DSL compiler (pure Python). Drop dev-only tests + stale bytecode.
add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/../dsl_fhe/xcomp ${NB_PKG}/nbc
    COMMAND ${CMAKE_COMMAND} -E rm -rf
            ${NB_PKG}/nbc/tests ${NB_PKG}/nbc/__pycache__ ${NB_PKG}/__pycache__)

# --- Third-party license notices ---------------------------------------------
# The wheel bundles/redistributes native code from BSD/Apache projects, so their
# license texts must travel with it (the BSD-2/BSD-3 binary-redistribution clause).
# Shipped as package data under niobium_sdk/licenses/:
#   OpenFHE + openfhe-python  BSD-2-Clause  (compiled into openfhe*.so)
#   pybind11                  BSD-3-Clause  (headers compiled into the extensions)
#   libnbfhetch               Apache-2.0    (bundled .so)
#   niobium-sdk               Apache-2.0    (this project)
if(NIOBIUM_CLIENT_FHETCH_DIR)
    set(NB_FHETCH_SRC "${NIOBIUM_CLIENT_FHETCH_DIR}")
else()
    set(NB_FHETCH_SRC "${CMAKE_CURRENT_SOURCE_DIR}/../vendor/niobium-fhetch")
endif()
set(NB_LIC "${NB_PKG}/licenses")

# OpenFHE + libnbfhetch notices: from the runtime's share/licenses in runtime mode (the
# nested vendor/openfhe fork isn't checked out then), else from the fhetch source tree.
if(NIOBIUM_RUNTIME_PREFIX)
    set(NB_LIC_OPENFHE     "${NIOBIUM_RUNTIME_PREFIX}/share/licenses/LICENSE.OpenFHE")
    set(NB_LIC_LIBNBFHETCH "${NIOBIUM_RUNTIME_PREFIX}/share/licenses/LICENSE.niobium-fhetch")
else()
    set(NB_LIC_OPENFHE     "${NB_FHETCH_SRC}/vendor/openfhe/LICENSE")
    set(NB_LIC_LIBNBFHETCH "${NB_FHETCH_SRC}/LICENSE")
endif()

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

add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory ${NB_LIC}
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_LIC_OPENFHE}                             ${NB_LIC}/LICENSE.OpenFHE
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_FHETCH_SRC}/vendor/openfhe-python/LICENSE ${NB_LIC}/LICENSE.openfhe-python
    COMMAND ${CMAKE_COMMAND} -E copy ${NB_LIC_LIBNBFHETCH}                          ${NB_LIC}/LICENSE.libnbfhetch
    COMMAND ${CMAKE_COMMAND} -E copy ${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE         ${NB_LIC}/LICENSE.niobium-sdk
    COMMENT "Bundling third-party license notices -> ${NB_LIC}")

if(NB_PYBIND11_LICENSE AND EXISTS "${NB_PYBIND11_LICENSE}")
    add_custom_command(TARGET niobium_sdk_pkg POST_BUILD
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
install(DIRECTORY ${NB_PKG}/ DESTINATION niobium_sdk COMPONENT python_wheel)
