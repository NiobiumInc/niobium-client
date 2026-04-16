// Copyright 2024-present Niobium Microsystems, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// OpenFHE Probe Interface
//
// C-linkage callback functions called by the Niobium-instrumented OpenFHE
// branch. Each probe fires when OpenFHE performs a polynomial operation
// and records the corresponding FHETCH instruction into the trace.
//
// These functions are NOT called by user code — they are called internally
// by the instrumented OpenFHE library. The niobium-client library provides
// the implementations.

#ifndef NIOBIUM_OPENFHE_PROBES_H
#define NIOBIUM_OPENFHE_PROBES_H

#include <cstdint>

#ifdef __cplusplus
extern "C" {
#endif

// ============================================================================
// Recording control
// ============================================================================

/// Trigger instruction execution (flush pending state).
void openfhe_cprobe_execute();

/// Temporarily pause recording (e.g., during deserialization).
void openfhe_cprobe_pause_recording();

/// Resume recording after pause.
void openfhe_cprobe_resume_recording();

/// Annotate the trace with a human-readable comment.
void openfhe_cprobe_annotate(const char* annotation);

// ============================================================================
// Polynomial identity and address tracking
// ============================================================================

/// Register a polynomial ID in the trace.
void openfhe_cprobe_id(uintptr_t poly_id);

/// Map a polynomial ID to a memory address. Returns the address slot.
uintptr_t* openfhe_cprobe_address(uintptr_t poly_id);

/// Map a polynomial ID to a result address. Returns the address slot.
uintptr_t* openfhe_cprobe_result(uintptr_t poly_id);

/// Get the cache address slot for temporary storage.
uintptr_t* openfhe_cprobe_cache();

// ============================================================================
// Polynomial initialization (random distributions and constants)
// ============================================================================

/// Mark a polynomial as sampled from discrete Gaussian distribution.
void openfhe_cprobe_discrete_gaussian(uintptr_t poly_id, int format);

/// Mark a polynomial as sampled from discrete uniform distribution.
void openfhe_cprobe_discrete_uniform(uintptr_t poly_id, int format);

/// Mark a polynomial as sampled from binary uniform distribution.
void openfhe_cprobe_binary_uniform(uintptr_t poly_id, int format);

/// Mark a polynomial as sampled from ternary uniform distribution.
void openfhe_cprobe_ternary_uniform(uintptr_t poly_id, int format);

/// Mark a polynomial as precomputed data (e.g., bootstrap tables).
void openfhe_cprobe_precompute(uintptr_t poly_id, int format);

/// Mark a polynomial as zero-initialized.
void openfhe_cprobe_zero(uintptr_t poly_id, int format, uint64_t modulus);

/// Mark a polynomial as max-value initialized.
void openfhe_cprobe_max(uintptr_t poly_id, int format, uint64_t modulus);

// ============================================================================
// Input / output / key classification
// ============================================================================

/// Mark a polynomial as an input.
void openfhe_cprobe_input(uintptr_t poly_id, int format);

/// Mark a polynomial as an output.
void openfhe_cprobe_output(uintptr_t poly_id, int format);

/// Mark a polynomial as part of an evaluation key.
void openfhe_cprobe_key(uintptr_t poly_id, int format);

// ============================================================================
// Polynomial lifecycle (copy, move, free)
// ============================================================================

/// Record a polynomial copy (dst = src).
void openfhe_cprobe_copy(uintptr_t dst_id, uintptr_t src_id);

/// Record a polynomial move (dst = move(src)).
void openfhe_cprobe_move(uintptr_t dst_id, uintptr_t src_id);

/// Reassign a polynomial ID (dst_old is replaced by src).
void openfhe_cprobe_reassign_id(uintptr_t dst_old, uintptr_t src);

/// Record polynomial deallocation.
void openfhe_cprobe_free(uintptr_t poly_id);

/// Suppress or unsuppress probe firing (e.g., during internal cloning).
void openfhe_suppress_probes(int suppress);

/// Mark the calling thread as a serialization thread.
/// Suppresses spurious probes from DCRTPoly cloning during serialization.
void openfhe_cprobe_set_serialization_thread(bool is_serialization);

/// Check if the calling thread is a serialization thread.
bool openfhe_cprobe_is_serialization_thread();

// ============================================================================
// Arithmetic operations (record FHETCH instructions)
// ============================================================================

/// Record: dst = src1 + src2 (mod modulus)  →  sr_addp
void openfhe_cprobe_add(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus);

/// Record: dst = src1 - src2 (mod modulus)  →  sr_subp
void openfhe_cprobe_sub(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus);

/// Record: dst = src1 * src2 (mod modulus)  →  sr_mulp
void openfhe_cprobe_mul(uintptr_t dst, uintptr_t src1, uintptr_t src2,
                        uint64_t modulus);

/// Record: dst = src + immediate (mod modulus)  →  sr_addps
void openfhe_cprobe_addi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus);

/// Record: dst = src - immediate (mod modulus)  →  sr_subps
void openfhe_cprobe_subi(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus);

/// Record: dst = src * immediate (mod modulus)  →  sr_mulps
void openfhe_cprobe_muli(uintptr_t dst, uintptr_t src, uint64_t immediate,
                         uint64_t modulus);

// ============================================================================
// Transform and permutation operations
// ============================================================================

/// Record: dst = NTT(src) mod modulus  →  sr_ntt
/// @param omega  Primitive root of unity for the NTT.
void openfhe_cprobe_ntt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                        uint64_t omega);

/// Record: dst = INTT(src) mod modulus  →  sr_intt
/// @param omega  Primitive root of unity for the INTT.
void openfhe_cprobe_intt(uintptr_t dst, uintptr_t src, uint64_t modulus,
                         uint64_t omega);

/// Record: dst = automorphism(src, k) with modulus parameters  →  sr_permute
void openfhe_cprobe_automorphism(uintptr_t dst, uintptr_t src,
                                 uint64_t k, uint64_t modulus,
                                 uint64_t ring_dim, uint64_t root_of_unity);

/// Record: modulus switch operation.
void openfhe_cprobe_switchmodulus(uintptr_t dst, uintptr_t src,
                                 uint64_t old_modulus, uint64_t new_modulus,
                                 uint64_t root_of_unity_old,
                                 uint64_t root_of_unity_new,
                                 uint64_t ring_dim);

#ifdef __cplusplus
}
#endif

#endif  // NIOBIUM_OPENFHE_PROBES_H
