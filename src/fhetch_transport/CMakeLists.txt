# ==============================================================================
# FHETCH transport — client-side nbcc_fhetch_replay forwarder + server daemon.
#
# Two executables, one shared archive helper. Client-side installs the
# forwarder ahead of niobium-compiler's `nbcc_fhetch_replay` on PATH; the
# server daemon runs alongside the real compiler binary on whichever host
# owns the FHETCH backend and exec's it per request.
# ==============================================================================

add_library(niobium_fhetch_archive STATIC archive.cpp)
target_include_directories(niobium_fhetch_archive PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(niobium_fhetch_archive PROPERTIES
    POSITION_INDEPENDENT_CODE ON)

# cpp-httplib is a single header; add the vendored include dir directly.
set(HTTPLIB_INCLUDE_DIR
    ${CMAKE_SOURCE_DIR}/vendor/cpp-httplib
    CACHE INTERNAL "cpp-httplib include dir")

# Client-side forwarder. Named exactly like the compiler-side binary so that
# when it's installed first on the client host, fhetch::Compiler::replay()'s
# PATH lookup picks this up transparently.
add_executable(nbcc_fhetch_replay_transport_client client.cpp)
set_target_properties(nbcc_fhetch_replay_transport_client PROPERTIES
    OUTPUT_NAME nbcc_fhetch_replay)
target_include_directories(nbcc_fhetch_replay_transport_client PRIVATE
    ${HTTPLIB_INCLUDE_DIR})
target_link_libraries(nbcc_fhetch_replay_transport_client PRIVATE
    niobium_fhetch_archive Threads::Threads)

# Server daemon that sits in front of the compiler-side nbcc_fhetch_replay.
add_executable(nbcc_fhetch_replay_server server.cpp)
target_include_directories(nbcc_fhetch_replay_server PRIVATE
    ${HTTPLIB_INCLUDE_DIR})
target_link_libraries(nbcc_fhetch_replay_server PRIVATE
    niobium_fhetch_archive Threads::Threads)

install(TARGETS nbcc_fhetch_replay_transport_client nbcc_fhetch_replay_server
        RUNTIME DESTINATION bin)
