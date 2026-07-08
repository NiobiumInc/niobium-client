# Standalone build of the niobium_client Python package's native bits.
#
# For now this is the _archive binding only (pack/unpack the FHETCH "NBAR" TLV),
# which is pure C++ stdlib — no OpenFHE — so it builds without the rest of the
# tree. Compiles archive.cpp directly (same source as niobium_fhetch_archive, so
# it tracks the C++ wire format). During full wheel assembly this folds into a
# niobium-client WITH_PYTHON option that links the built lib instead.
#
#   cmake -S python -B build/python -Dpybind11_DIR=<...> -DPython_EXECUTABLE=<...>
#   cmake --build build/python
# -> build/python/niobium_client/{__init__,client}.py + _archive*.so
cmake_minimum_required(VERSION 3.16)
project(niobium_client_python CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(_archive
    archive_binding.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/fhetch_transport/archive.cpp)
target_include_directories(_archive PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../src/fhetch_transport)
set_target_properties(_archive PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/niobium_client)

# Assemble the importable package: copy the pure-Python sources next to _archive.
add_custom_command(TARGET _archive POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/niobium_client
            ${CMAKE_BINARY_DIR}/niobium_client
    COMMENT "Assembling niobium_client package")
