// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Stub implementations for niobium_auto::* hook functions and globals
// required by the Niobium-instrumented OpenFHE branch.
//
// In the full niobium-compiler, AutoFacade.cpp provides config-driven
// record/replay orchestration. Here in the client we provide minimal
// stubs so that the instrumented OpenFHE links and the probes fire.

#include "niobium/compiler.h"

#include "openfhe.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

#include <atomic>
#include <memory>
#include <string>

using DCRTPoly = lbcrypto::DCRTPoly;

// ============================================================================
// Global flags read by instrumented OpenFHE headers
// (declared extern in niobium_auto_hooks.h)
// ============================================================================

bool g_replay_mode = false;
std::atomic<uint64_t> g_replay_noop_count{0};

// ============================================================================
// niobium_auto::* hooks — signatures must match niobium_auto_hooks.h exactly
// ============================================================================

namespace niobium_auto {

void on_deserialize_crypto_context(
    lbcrypto::CryptoContext<DCRTPoly>& /*cc*/) {
    // No-op in client — crypto context capture is explicit via compiler API.
}

void on_deserialize_ciphertext(
    const std::string& /*filepath*/,
    lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // No-op in client.
}

void lazy_init(const lbcrypto::CryptoContext<DCRTPoly>& /*cc*/) {
    // No-op in client — init is explicit via compiler().init().
}

void lazy_init() {
    // No-arg overload — no-op in client.
}

bool on_serialize_ciphertext(
    const std::string& /*filepath*/,
    const lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // Return false: caller proceeds with normal file write.
    return false;
}

bool is_recording() {
    return niobium::compiler().running_p();
}

bool on_decrypt(lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // Return false: caller proceeds with normal Decrypt.
    return false;
}

bool is_replaying() {
    return g_replay_mode;
}

std::shared_ptr<lbcrypto::SchemeBase<DCRTPoly>> unwrap_scheme(
    const std::shared_ptr<lbcrypto::SchemeBase<DCRTPoly>>& scheme) {
    // Client doesn't use the NiobiumAutoScheme proxy — return as-is.
    return scheme;
}

}  // namespace niobium_auto

// ============================================================================
// Additional probe functions referenced by the instrumented OpenFHE
// ============================================================================

extern "C" {

void openfhe_cporbe_with_openmp(bool /*with_openmp*/) {
    // Signals OpenFHE's OpenMP state. No-op in client.
}

void openfhe_cprobe_save_dcrt_poly(const void* /*dcrt_poly_ptr*/) {
    // DATA_TRACKING feature only — no-op in client.
}

}  // extern "C"

// ============================================================================
// Explicit template instantiations for Compiler methods with OpenFHE types
// ============================================================================

namespace niobium {

template<>
void Compiler::capture_crypto_context<lbcrypto::CryptoContext<DCRTPoly>>(
    const lbcrypto::CryptoContext<DCRTPoly>& cc) {
    // Store ring dimension for the simulator.
    uint64_t rd = cc->GetRingDimension();
    set_ring_dimension(rd);
    std::cout << "[NIOBIUM] Captured crypto context: ring_dim=" << rd << std::endl;
}

template<>
void Compiler::tag_input<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& /*input_name*/,
    lbcrypto::Ciphertext<DCRTPoly>& /*value*/,
    std::optional<std::filesystem::path> /*file*/) {
    // Input tagging is recorded via the probe mechanism.
}

template<>
void Compiler::tag_input<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& /*input_name*/,
    const lbcrypto::Ciphertext<DCRTPoly>& /*value*/,
    std::optional<std::filesystem::path> /*file*/) {
    // Const overload.
}

template<>
void Compiler::probe<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& /*var_name*/,
    const lbcrypto::Ciphertext<DCRTPoly>& /*value*/) {
    // Output probing is recorded via the probe mechanism.
}

}  // namespace niobium
