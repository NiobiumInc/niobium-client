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

# ==============================================================================
# Plaintext-add example — client / server / decrypt
# ==============================================================================

add_executable(plaintext_add_client  plaintext_add/client.cpp)
add_executable(plaintext_add_server  plaintext_add/server.cpp)
add_executable(plaintext_add_decrypt plaintext_add/decrypt.cpp)

target_link_libraries(plaintext_add_client  PRIVATE niobium_fhetch)
target_link_libraries(plaintext_add_server  PRIVATE niobium_fhetch)
target_link_libraries(plaintext_add_decrypt PRIVATE niobium_fhetch)

set_openfhe_rpath(plaintext_add_client)
set_openfhe_rpath(plaintext_add_server)
set_openfhe_rpath(plaintext_add_decrypt)

# ==============================================================================
# Auto-facade example — zero niobium:: boilerplate in user code
# ==============================================================================
# Built only when the auto-facade helper lib is built too (default ON).
# ciphers_ops_server_auto.cpp is line-for-line the same CKKS arithmetic logic
# as the compiler's examples/fetch/ciphers_ops_server.cpp — instrumentation
# is injected entirely via the libnbclient_autofacade / NiobiumAutoScheme
# path, driven by a YAML config file at run time (see docs/AUTO_FACADE.md).

if(TARGET niobium_client_autofacade)
    add_executable(ciphers_ops_cache_keys  auto/ciphers_ops_cache_keys.cpp)
    add_executable(ciphers_ops_client      auto/ciphers_ops_client.cpp)
    add_executable(ciphers_ops_server_auto auto/ciphers_ops_server_auto.cpp)

    # Keygen + client only need OpenFHE — no auto-facade linkage.
    target_link_libraries(ciphers_ops_cache_keys PRIVATE niobium_fhetch)
    target_link_libraries(ciphers_ops_client     PRIVATE niobium_fhetch)
    set_openfhe_rpath(ciphers_ops_cache_keys)
    set_openfhe_rpath(ciphers_ops_client)

    # Server links the auto-facade lib so the weak niobium_auto:: stubs in
    # libnbfhetch are overridden with the real impl.
    target_link_libraries(ciphers_ops_server_auto
        PRIVATE niobium_fhetch niobium_client_autofacade)
    # Whole-archive: AutoFacade.cpp has no callers by name in
    # ciphers_ops_server_auto.cpp — the strong symbols must be kept even
    # though the linker sees nothing referencing them directly.
    if(UNIX AND NOT APPLE)
        target_link_options(ciphers_ops_server_auto PRIVATE
            "LINKER:--whole-archive,$<TARGET_FILE:niobium_client_autofacade>,--no-whole-archive")
    elseif(APPLE)
        target_link_options(ciphers_ops_server_auto PRIVATE
            "LINKER:-force_load,$<TARGET_FILE:niobium_client_autofacade>")
    endif()
    set_openfhe_rpath(ciphers_ops_server_auto)
endif()
