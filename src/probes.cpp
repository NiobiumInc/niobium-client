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
#include <unordered_set>

// ============================================================================
// Address map: OpenFHE polynomial ID → FHETCH trace address
// ============================================================================

static std::mutex g_probe_mutex;
static std::unordered_map<uintptr_t, uintptr_t> g_address_map;
static uintptr_t g_next_fhetch_addr = 0;

// ----- Compact address recycling (mirrors niobium-compiler Generator) -----
// Polys that OpenFHE destroys return their compact address to this pool;
// later allocations pull from the pool before advancing g_next_fhetch_addr.
// Addresses can also be "pinned" (inputs, keys, bootstrap precompute
// plaintexts): pinned addresses never return to the pool.
static std::vector<uintptr_t> g_compact_free_pool;
static std::unordered_set<uintptr_t> g_pinned_openfhe_ids;
static std::unordered_map<uintptr_t, int> g_refcount;  // openfhe_id -> live refs
static bool g_suppressed = false;
static thread_local bool g_serialization_thread = false;

// Debug: track the lifecycle of a specific FHETCH address.
// Set via NIOBIUM_TRACK_ADDR=65574 to follow what probe activity is
// associated with that address.
static uintptr_t g_track_addr = [](){
    const char* e = std::getenv("NIOBIUM_TRACK_ADDR");
    return e ? static_cast<uintptr_t>(std::strtoull(e, nullptr, 10))
             : static_cast<uintptr_t>(-1);
}();

static void dbg_track(const char* event, uintptr_t openfhe_id, uintptr_t fhetch_addr,
                      const char* note = nullptr) {
    if (g_track_addr == static_cast<uintptr_t>(-1)) return;
    if (fhetch_addr != g_track_addr) return;
    std::cerr << "[TRACK %" << fhetch_addr << "] " << event
              << " openfhe_id=0x" << std::hex << openfhe_id << std::dec
              << (note ? std::string(" ") + note : std::string())
              << std::endl;
}

static uintptr_t map_address(uintptr_t openfhe_id) {
    auto it = g_address_map.find(openfhe_id);
    if (it != g_address_map.end()) {
        dbg_track("map_address (existing)", openfhe_id, it->second);
        return it->second;
    }
    uintptr_t addr;
    bool from_pool = false;
    // Pinned addresses (inputs, keys, precompute) always take a fresh id so
    // they stay stable and don't accidentally inherit a recycled address.
    if (!g_pinned_openfhe_ids.count(openfhe_id) && !g_compact_free_pool.empty()) {
        addr = g_compact_free_pool.back();
        g_compact_free_pool.pop_back();
        from_pool = true;
    } else {
        addr = g_next_fhetch_addr++;
    }
    g_address_map[openfhe_id] = addr;
    dbg_track(from_pool ? "map_address (from free pool)"
                        : "map_address (fresh monotonic)",
              openfhe_id, addr);
    return addr;
}

// (Note: address recycling happens inline inside openfhe_cprobe_reassign_id
//  via refcount decrement. openfhe_cprobe_free stays a no-op — recycling on
//  destruction is known to cause register-spill blow-up in the compiler's
//  equivalent and isn't needed for our uses.)

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
    // A freshly-constructed poly has exactly one reference (itself).
    g_refcount.emplace(poly_id, 1);
}

uintptr_t* openfhe_cprobe_address(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    map_address(poly_id);
    return nullptr;
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

// Number of times precompute probe fired vs. how many of those id's are
// still live in g_address_map (sanity counters for the compaction layer).
static size_t g_precompute_probe_count = 0;
static size_t g_precompute_probe_already_mapped = 0;

void openfhe_cprobe_precompute(uintptr_t poly_id, int /*format*/) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    ++g_precompute_probe_count;
    if (g_address_map.find(poly_id) != g_address_map.end())
        ++g_precompute_probe_already_mapped;
    // Precomputes hold irreproducible data (bootstrap DFT plaintexts,
    // random-distribution polys, etc.); pin the id so refcount-based
    // recycling never touches its compact FHETCH address.
    g_pinned_openfhe_ids.insert(poly_id);
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

    // Only emit a copy instruction while recording — matches the
    // compiler's Generator::copy() which bails out when !running_p().
    // Copies that happen during setup don't belong in the trace;
    // emitting them there would reference polys that die before
    // replay and whose FHETCH addresses may be recycled.
    if (!niobium::compiler().running_p()) return;
    niobium::detail::trace_writer().emit(
        "sr_addps " + addr(dst_addr) + ", " + addr(src_addr) + ", 0, m=0");
}

void openfhe_cprobe_move(uintptr_t dst_id, uintptr_t src_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    if (g_serialization_thread) return;
    uintptr_t src_addr = map_address(src_id);
    g_address_map[dst_id] = src_addr;
    // dst_id now points to the same FHETCH address as src_id
}

// Copy-assignment: `poly_dst = poly_src` causes OpenFHE to set
// dst->m_id = src->m_id. The old dst id is abandoned (OpenFHE no longer
// uses it). Refcount bookkeeping: src gains a reference; dst_old loses one.
// If dst_old's refcount hits zero and it isn't pinned, its compact FHETCH
// address is returned to the free pool so a later allocation can reuse it.
// This is how bootstrap precompute plaintexts, built by reassigning earlier
// intermediates, end up at the same compact addresses the trace reads from.
void openfhe_cprobe_reassign_id(uintptr_t dst_old, uintptr_t src) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);

    // src gains a reference.
    ++g_refcount[src];

    // dst_old loses a reference.
    auto rc = g_refcount.find(dst_old);
    if (rc != g_refcount.end()) {
        if (--rc->second == 0) {
            g_refcount.erase(rc);
            if (!g_pinned_openfhe_ids.count(dst_old)) {
                auto dst_it = g_address_map.find(dst_old);
                if (dst_it != g_address_map.end()) {
                    g_compact_free_pool.push_back(dst_it->second);
                    g_data_parent.erase(dst_it->second);
                    g_address_map.erase(dst_it);
                }
            }
        }
    }
    // Note: we do NOT remap dst_old to src's address. OpenFHE has already
    // overwritten dst->m_id to src.m_id in C++, so any future lookup on
    // dst_old is a bug in the caller, not something we need to paper over.
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

// OpenFHE's poly-impl.h calls this as:
//   openfhe_cprobe_automorphism(result.GetId(), GetId(), q.ConvertToInt(),
//                               mask, logn, k);
// so the third argument is the *modulus*, followed by mask, logn, k.
void openfhe_cprobe_automorphism(uintptr_t dst, uintptr_t src,
                                 uint64_t modulus, uint64_t mask,
                                 uint64_t logn, uint64_t k) {
    if (!should_record()) return;
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    uintptr_t da = map_address(dst);
    uintptr_t sa = resolve_inplace_src(map_address(src), da);
    emit("sr_automorph_eval " + addr(da) + ", " + addr(sa) +
         ", " + midx(modulus) +
         ", mask=" + std::to_string(mask) +
         ", logn=" + std::to_string(logn) +
         ", k=" + std::to_string(k));
    invalidate_clone_parent_on_write(da);
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

void pin_openfhe_id(uintptr_t poly_id) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    g_pinned_openfhe_ids.insert(poly_id);
}

uintptr_t niobium_precompute_probe_count() {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    return g_precompute_probe_count;
}
uintptr_t niobium_precompute_probe_already_mapped_count() {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    return g_precompute_probe_already_mapped;
}

void reserve_fhetch_addresses(uint64_t next_addr) {
    std::lock_guard<std::mutex> lock(g_probe_mutex);
    if (next_addr > g_next_fhetch_addr)
        g_next_fhetch_addr = next_addr;
}

}  // namespace niobium::detail
