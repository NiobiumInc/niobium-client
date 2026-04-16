// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// OpenFHE probe implementations.
//
// These C-linkage functions are called by the Niobium-instrumented OpenFHE
// branch whenever a polynomial operation occurs. Each probe records the
// corresponding FHETCH instruction into the trace via the TraceWriter.

#include "niobium/openfhe/probes.h"
#include "niobium/compiler.h"
#include "compiler_internal.h"

#include <iomanip>
#include <iostream>
#include <mutex>
#include <sstream>
#include <string>
#include <unordered_map>

// ============================================================================
// Address map: OpenFHE polynomial ID → FHETCH trace address
// ============================================================================

static std::mutex g_probe_mutex;
static std::unordered_map<uintptr_t, uintptr_t> g_address_map;
static uintptr_t g_next_fhetch_addr = 0;
static bool g_suppressed = false;
static thread_local bool g_serialization_thread = false;

static uintptr_t map_address(uintptr_t openfhe_id) {
    auto it = g_address_map.find(openfhe_id);
    if (it != g_address_map.end()) return it->second;
    uintptr_t addr = g_next_fhetch_addr++;
    g_address_map[openfhe_id] = addr;
    return addr;
}

static std::string addr(uintptr_t a) {
    return "%" + std::to_string(a);
}

static std::string qhex(uint64_t q) {
    std::ostringstream ss;
    ss << "0x" << std::hex << std::uppercase << q;
    return ss.str();
}

static void emit(const std::string& instruction) {
    niobium::detail::trace_writer().emit(instruction);
}

static bool should_record() {
    return niobium::compiler().running_p() && !g_suppressed && !g_serialization_thread;
}

// ============================================================================
// Recording control
// ============================================================================

extern "C" {

void openfhe_cprobe_execute() {
    // No-op in client — instructions are recorded individually.
}

void openfhe_cprobe_pause_recording() {
    niobium::compiler().pause();
}

void openfhe_cprobe_resume_recording() {
    niobium::compiler().resume();
}

void openfhe_cprobe_annotate(const char* annotation) {
    if (!should_record()) return;
    niobium::detail::trace_writer().comment(annotation);
}

// ============================================================================
// Polynomial identity and address tracking
// ============================================================================

void openfhe_cprobe_id(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

uintptr_t* openfhe_cprobe_address(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
    return nullptr;  // Client doesn't use address pointers
}

uintptr_t* openfhe_cprobe_result(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
    return nullptr;
}

uintptr_t* openfhe_cprobe_cache() {
    return nullptr;
}

// ============================================================================
// Polynomial initialization
// ============================================================================

void openfhe_cprobe_discrete_gaussian(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_discrete_uniform(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_binary_uniform(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_ternary_uniform(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_precompute(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_zero(uintptr_t poly_id, int /*format*/, uint64_t /*modulus*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_max(uintptr_t poly_id, int /*format*/, uint64_t /*modulus*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

// ============================================================================
// Input / output / key classification
// ============================================================================

void openfhe_cprobe_input(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_output(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

void openfhe_cprobe_key(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
}

// ============================================================================
// Polynomial lifecycle
// ============================================================================

void openfhe_cprobe_copy(uintptr_t dst_id, uintptr_t src_id) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t src = map_address(src_id);
    uintptr_t dst = map_address(dst_id);
    // Copy is a no-op at the FHETCH level — the server handles aliasing.
    // But we track the address mapping.
    (void)src; (void)dst;
}

void openfhe_cprobe_move(uintptr_t dst_id, uintptr_t src_id) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t src = map_address(src_id);
    g_address_map[dst_id] = src;
}

void openfhe_cprobe_reassign_id(uintptr_t dst_old, uintptr_t src) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    auto it = g_address_map.find(src);
    if (it != g_address_map.end()) {
        g_address_map[dst_old] = it->second;
    }
}

void openfhe_cprobe_free(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    g_address_map.erase(poly_id);
}

void openfhe_suppress_probes(int suppress) {
    g_suppressed = (suppress != 0);
}

void openfhe_cprobe_set_serialization_thread(bool is_serialization) {
    g_serialization_thread = is_serialization;
}

bool openfhe_cprobe_is_serialization_thread() {
    return g_serialization_thread;
}

// ============================================================================
// Arithmetic operations → FHETCH instructions
// ============================================================================

void openfhe_cprobe_add(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_addp " + addr(map_address(dst)) + ", " +
         addr(map_address(src1)) + ", " + addr(map_address(src2)) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_sub(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_subp " + addr(map_address(dst)) + ", " +
         addr(map_address(src1)) + ", " + addr(map_address(src2)) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_mul(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_mulp " + addr(map_address(dst)) + ", " +
         addr(map_address(src1)) + ", " + addr(map_address(src2)) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_addi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_addps " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", " + std::to_string(immediate) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_subi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_subps " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", " + std::to_string(immediate) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_muli(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_mulps " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", " + std::to_string(immediate) +
         ", q=" + qhex(modulus));
}

// ============================================================================
// Transform and permutation operations
// ============================================================================

void openfhe_cprobe_ntt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                        uint64_t /*omega*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_ntt " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", q=" + qhex(modulus));
}

void openfhe_cprobe_intt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                         uint64_t /*omega*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_intt " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", q=" + qhex(modulus));
}

void openfhe_cprobe_automorphism(uintptr_t dst, uintptr_t src,
                                 uint64_t k, uint64_t modulus,
                                 uint64_t /*ring_dim*/,
                                 uint64_t /*root_of_unity*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_automorph_eval " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", k=" + std::to_string(k) +
         ", q=" + qhex(modulus));
}

void openfhe_cprobe_switchmodulus(uintptr_t dst, uintptr_t src,
                                 uint64_t old_modulus, uint64_t new_modulus,
                                 uint64_t /*root_of_unity_old*/,
                                 uint64_t /*root_of_unity_new*/,
                                 uint64_t /*ring_dim*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("# switchmodulus " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) +
         ", old_q=" + qhex(old_modulus) + ", new_q=" + qhex(new_modulus));
}

}  // extern "C"
