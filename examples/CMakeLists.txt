# ==============================================================================
# Helper: set RPATH for OpenFHE shared libraries
# ==============================================================================
macro(set_openfhe_rpath TARGET_NAME)
  if(APPLE)
    set_target_properties(${TARGET_NAME} PROPERTIES
        BUILD_RPATH "@loader_path;${OPENFHE_LIBRARY_PATH}")
  else()
    set_target_properties(${TARGET_NAME} PROPERTIES
        BUILD_RPATH "${OPENFHE_LIBRARY_PATH}")
  endif()
endmacro()

# ==============================================================================
# Bootstrap example — client / server / decrypt
# ==============================================================================

add_executable(bootstrap_client  bootstrap/client.cpp)
add_executable(bootstrap_server  bootstrap/server.cpp)
add_executable(bootstrap_decrypt bootstrap/decrypt.cpp)

target_link_libraries(bootstrap_client  PRIVATE niobium_fhetch)
target_link_libraries(bootstrap_server  PRIVATE niobium_fhetch)
target_link_libraries(bootstrap_decrypt PRIVATE niobium_fhetch)

set_openfhe_rpath(bootstrap_client)
set_openfhe_rpath(bootstrap_server)
set_openfhe_rpath(bootstrap_decrypt)

# ==============================================================================
# Multiply example — client / server / decrypt
# ==============================================================================

add_executable(mult_client  mult/client.cpp)
add_executable(mult_server  mult/server.cpp)
add_executable(mult_decrypt mult/decrypt.cpp)

target_link_libraries(mult_client  PRIVATE niobium_fhetch)
target_link_libraries(mult_server  PRIVATE niobium_fhetch)
target_link_libraries(mult_decrypt PRIVATE niobium_fhetch)

set_openfhe_rpath(mult_client)
set_openfhe_rpath(mult_server)
set_openfhe_rpath(mult_decrypt)

# ==============================================================================
# Simple ops example — client / server / decrypt
# ==============================================================================

add_executable(simple_ops_client  simple_ops/client.cpp)
add_executable(simple_ops_server  simple_ops/server.cpp)
add_executable(simple_ops_decrypt simple_ops/decrypt.cpp)

target_link_libraries(simple_ops_client  PRIVATE niobium_fhetch)
target_link_libraries(simple_ops_server  PRIVATE niobium_fhetch)
target_link_libraries(simple_ops_decrypt PRIVATE niobium_fhetch)

set_openfhe_rpath(simple_ops_client)
set_openfhe_rpath(simple_ops_server)
set_openfhe_rpath(simple_ops_decrypt)
