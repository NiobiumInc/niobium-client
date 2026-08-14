# ==============================================================================
# FHETCH transport — client-side nbcc_fhetch_replay forwarder + server daemon.
#
# Two executables, one shared archive helper. Client-side installs the
# forwarder ahead of niobium-compiler's `nbcc_fhetch_replay` on PATH; the
# server daemon runs alongside the real compiler binary on whichever host
# owns the FHETCH backend and exec's it per request.
# ==============================================================================

# libcrypto is a hard dependency of the archive library: per-artifact SHA-1
# fingerprints (archive.h) go through OpenSSL's EVP interface, which reaches the
# CPU's crypto extensions and is the audited implementation. REQUIRED here, so
# both executables below inherit it — there is no OpenSSL-less build any more.
# On macOS, hint Homebrew's copy: -DOPENSSL_ROOT_DIR=$(brew --prefix openssl@3).
find_package(OpenSSL REQUIRED)

add_library(niobium_fhetch_archive STATIC archive.cpp)
target_include_directories(niobium_fhetch_archive PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR})
# PUBLIC: archive.h itself includes <openssl/evp.h> (Sha1 holds an EVP_MD_CTX*),
# so every consumer needs the headers, not just the link.
target_link_libraries(niobium_fhetch_archive PUBLIC OpenSSL::Crypto)
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

# The forwarder POSTs to the assigned worker through HAProxy's TLS edge
# (https://…), so cpp-httplib needs its OpenSSL backend — without it httplib
# throws "'https' scheme is not supported". Unconditional now: OpenSSL is
# REQUIRED above for artifact fingerprinting, so libssl comes along for free and
# there is no longer an http-only build to guard against.
target_compile_definitions(nbcc_fhetch_replay_transport_client PRIVATE
    CPPHTTPLIB_OPENSSL_SUPPORT)
target_link_libraries(nbcc_fhetch_replay_transport_client PRIVATE OpenSSL::SSL)
if(APPLE)
    # httplib's load_system_certs() reads the macOS keychain via the
    # Security / CoreFoundation frameworks — link them or the OpenSSL build
    # fails with undefined _SecTrustCopyAnchorCertificates / _CF* symbols.
    target_link_libraries(nbcc_fhetch_replay_transport_client PRIVATE
        "-framework CoreFoundation" "-framework Security")
endif()
message(STATUS "fhetch_transport: OpenSSL ${OPENSSL_VERSION} "
    "(client TLS + artifact SHA-1)")

# Server daemon that sits in front of the compiler-side nbcc_fhetch_replay.
add_executable(nbcc_fhetch_replay_server server.cpp)
target_include_directories(nbcc_fhetch_replay_server PRIVATE
    ${HTTPLIB_INCLUDE_DIR})
target_link_libraries(nbcc_fhetch_replay_server PRIVATE
    niobium_fhetch_archive Threads::Threads)

install(TARGETS nbcc_fhetch_replay_transport_client nbcc_fhetch_replay_server
        RUNTIME DESTINATION bin)
