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

#include <iostream>
#include <mutex>
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

static std::string midx(uint64_t q) {
    uint32_t idx = niobium::detail::trace_writer().register_modulus(q);
    return "m=" + std::to_string(idx);
}

static void emit(const std::string& instruction) {
    niobium::detail::trace_writer().emit(instruction);
}

// Data inheritance: tracks which FHETCH address was derived from which.
// Forward-declared here; defined in the copy/move probe section below.
static std::unordered_map<uint64_t, uint64_t> g_data_parent;

static bool should_record() {
    return niobium::compiler().running_p() && !g_suppressed && !g_serialization_thread;
}

// Resolve an in-place source: if src_addr == dst_addr and there is a
// copy-parent for dst_addr, return the parent instead. This turns
// in-place ops like "add %8, %8, %4" (from clone+operator+=) into
// "add %8, %0, %4" so the simulator sees the real data dependency.
static uintptr_t resolve_inplace_src(uintptr_t src_addr, uintptr_t dst_addr) {
    if (src_addr != dst_addr) return src_addr;
    auto it = g_data_parent.find(src_addr);
    if (it != g_data_parent.end()) return it->second;
    return src_addr;
}

// Any arithmetic op that writes to `dst_addr` invalidates the clone-parent
// link for that address: the destination now holds computed data, not a
// pristine copy of anything. Without this, later in-place ops resolve
// to a stale parent and read the wrong polynomial. (The bug that broke
// EvalMult's d[1] = a0*b1 + a1*b0 — after `tmp = clone(cv1[0])` set
// parent[tmp]=cv1[0], the subsequent mul overwrote tmp with a0*b1, but
// the parent link persisted and the later `tmp += a1*b0` resolved src1
// back to cv1[0] instead of the mul result.)
static void invalidate_clone_parent_on_write(uintptr_t dst_addr) {
    g_data_parent.erase(dst_addr);
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

void openfhe_cprobe_zero(uintptr_t poly_id, int /*format*/, uint64_t modulus) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t a = map_address(poly_id);

    // Emit muli by 0 unconditionally to initialize the address to zero.
    // emit_preamble works even before start().
    std::string mi = (modulus != 0) ? midx(modulus) : "m=0";
    niobium::detail::trace_writer().emit_preamble(
        "sr_mulps " + addr(a) + ", " + addr(a) + ", 0, " + mi);
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
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    if (g_serialization_thread) return;
    uintptr_t src_addr = map_address(src_id);
    uintptr_t dst_addr = map_address(dst_id);
    g_data_parent[dst_addr] = src_addr;

    // Emit explicit copy unconditionally (even before start()).
    niobium::detail::trace_writer().emit_preamble(
        "sr_addps " + addr(dst_addr) + ", " + addr(src_addr) + ", 0, m=0");
}

void openfhe_cprobe_move(uintptr_t dst_id, uintptr_t src_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    if (g_serialization_thread) return;
    uintptr_t src_addr = map_address(src_id);
    g_address_map[dst_id] = src_addr;
    // dst_id now points to the same FHETCH address as src_id
}

void openfhe_cprobe_reassign_id(uintptr_t dst_old, uintptr_t src) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    // Record the data lineage: the old dst address inherits from the src address.
    // This captures the ID aliasing from operator= so the simulator can
    // propagate data to addresses created by future map_address calls
    // for the same OpenFHE ID.
    auto dst_it = g_address_map.find(dst_old);
    auto src_it = g_address_map.find(src);
    if (dst_it != g_address_map.end() && src_it != g_address_map.end()) {
        // dst_old's FHETCH addr inherits data from src's FHETCH addr
        g_data_parent[dst_it->second] = src_it->second;
    }
    // Remap dst_old to src's address
    if (src_it != g_address_map.end()) {
        g_address_map[dst_old] = src_it->second;
    }
}

void openfhe_cprobe_free(uintptr_t /*poly_id*/) {
    // Don't erase from the address map — the address mapping must persist
    // for the lifetime of the trace. Erasing causes IDs to be re-allocated
    // to different addresses when encountered again (e.g., after a temporary
    // DCRTPoly from ApproxSwitchCRTBasis is destroyed and its ID reappears
    // in the digit element during key-switching).
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
    uintptr_t da = map_address(dst);
    uintptr_t s1 = resolve_inplace_src(map_address(src1), da);
    uintptr_t s2 = map_address(src2);
    emit("sr_addp " + addr(da) + ", " + addr(s1) + ", " + addr(s2) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

void openfhe_cprobe_sub(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t s1 = resolve_inplace_src(map_address(src1), da);
    uintptr_t s2 = map_address(src2);
    emit("sr_subp " + addr(da) + ", " + addr(s1) + ", " + addr(s2) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

void openfhe_cprobe_mul(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t s1 = resolve_inplace_src(map_address(src1), da);
    uintptr_t s2 = map_address(src2);
    emit("sr_mulp " + addr(da) + ", " + addr(s1) + ", " + addr(s2) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

void openfhe_cprobe_addi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_addps " + addr(da) + ", " + addr(sa) + ", " + std::to_string(immediate) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

void openfhe_cprobe_subi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_subps " + addr(da) + ", " + addr(sa) + ", " + std::to_string(immediate) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

void openfhe_cprobe_muli(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_mulps " + addr(da) + ", " + addr(sa) + ", " + std::to_string(immediate) +
         ", " + midx(modulus));
    invalidate_clone_parent_on_write(da);
}

// ============================================================================
// Transform and permutation operations
// ============================================================================

void openfhe_cprobe_ntt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                        uint64_t omega) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_ntt " + addr(da) + ", " + addr(sa) + ", " + midx(modulus) +
         ", omega=" + std::to_string(omega));
}

void openfhe_cprobe_intt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                         uint64_t omega) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_intt " + addr(da) + ", " + addr(sa) + ", " + midx(modulus) +
         ", omega=" + std::to_string(omega));
}

void openfhe_cprobe_automorphism(uintptr_t dst, uintptr_t src,
                                 uint64_t k, uint64_t modulus,
                                 uint64_t /*ring_dim*/,
                                 uint64_t /*root_of_unity*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    emit("sr_automorph_eval " + addr(map_address(dst)) + ", " +
         addr(map_address(src)) + ", k=" + std::to_string(k) +
         ", " + midx(modulus));
}

void openfhe_cprobe_switchmodulus(uintptr_t dst, uintptr_t src,
                                 uint64_t old_modulus, uint64_t new_modulus,
                                 uint64_t /*root_of_unity_old*/,
                                 uint64_t /*root_of_unity_new*/,
                                 uint64_t /*ring_dim*/) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);

    // SwitchModulus expands to muli-addi-muli-addi (same as compiler's
    // SwitchModulus::expand() with non-HW immediates).
    //   imm[0] = 1
    //   imm[1] = (old_modulus - 1) / 2
    //   imm[2] = 1
    //   imm[3] = -(old_modulus-1)/2 mod new_modulus
    uint64_t half_om = (old_modulus - 1) >> 1;
    uint64_t x = half_om % new_modulus;
    uint64_t neg_half = (x == 0) ? 0 : new_modulus - x;

    uintptr_t d = map_address(dst);
    uintptr_t s = map_address(src);
    std::string da = addr(d);
    std::string sa = addr(s);

    emit("# switchmodulus " + da + ", " + sa +
         ", old_mod=" + std::to_string(old_modulus) +
         ", new_mod=" + std::to_string(new_modulus));

    // muli dst, src, 1, old_modulus
    emit("sr_mulps " + da + ", " + sa + ", 1, " + midx(old_modulus));
    // addi dst, dst, half_om, old_modulus
    emit("sr_addps " + da + ", " + da + ", " + std::to_string(half_om) + ", " + midx(old_modulus));
    // muli dst, dst, 1, new_modulus
    emit("sr_mulps " + da + ", " + da + ", 1, " + midx(new_modulus));
    // addi dst, dst, neg_half, new_modulus
    emit("sr_addps " + da + ", " + da + ", " + std::to_string(neg_half) + ", " + midx(new_modulus));
}

}  // extern "C"

// ============================================================================
// Internal helper: look up FHETCH address for an OpenFHE poly ID
// ============================================================================

namespace niobium::detail {

uint64_t lookup_fhetch_address(uintptr_t openfhe_poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    auto it = g_address_map.find(openfhe_poly_id);
    if (it != g_address_map.end()) return it->second;
    return static_cast<uint64_t>(-1);
}

const std::unordered_map<uint64_t, uint64_t>& get_data_parent_map() {
    return g_data_parent;
}

void reserve_fhetch_addresses(uint64_t next_addr) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    if (next_addr > g_next_fhetch_addr)
        g_next_fhetch_addr = next_addr;
}

}  // namespace niobium::detail
