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
// FHETCH Polynomial IR API
//
// Complete FHETCH instruction set for the Niobium client.
// These functions are called by the probe mechanism inside the
// Niobium-instrumented OpenFHE branch — not by user code directly.
// Each function records one FHETCH operation into the instruction trace.
//
// Specification reference: https://fhetch.org
//   "Polynomial Intermediate Representation for Fully Homomorphic Encryption"
//   (FHETCH Consortium, 2025)

#pragma once

#include <cstdint>
#include <filesystem>
#include <initializer_list>
#include <memory>
#include <string>
#include <vector>

namespace niobium::fhetch {

// ============================================================================
// Forward declarations (opaque implementations)
// ============================================================================

struct PolynomialImpl;
struct ScalarImpl;
struct SRPArrayImpl;
struct MRPImpl;
struct MRSImpl;
struct MRPArrayImpl;

// ============================================================================
// Enumerations
// ============================================================================

/// Whether polynomial/scalar components are integer or non-integer.
/// Integer: components modulo prime/composite integer moduli.
/// NonInteger: fixed-point or floating-point components.
enum class NumberType { Integer, NonInteger };

/// Polynomial representation domain.
/// Coefficient: time-domain (standard polynomial coefficients).
/// Evaluation: frequency-domain (NTT/FFT point values).
enum class Format { Coefficient, Evaluation };

// ============================================================================
// Type aliases
// ============================================================================

/// An ordered set of prime moduli defining an RNS base.
using ModuliBase = std::vector<uint64_t>;

// ============================================================================
// Hardware capabilities (information flowing upward from hardware)
// ============================================================================

/// Capabilities advertised by a hardware target to the compiler/library.
struct HardwareCapabilities {
    std::vector<uint64_t> supported_ring_dimensions;
    int max_modulus_bits = 63;
    int max_modulus_chain_length = 64;
    std::vector<std::string> supported_gadgets;
    std::vector<std::string> supported_optional_ops;
};

// ============================================================================
// Program parameters (information flowing downward to hardware)
// ============================================================================

/// Parameter choices flowing from the compiler/library to the hardware.
struct ProgramParameters {
    uint64_t ring_dimension = 0;
    ModuliBase rns_primes;
    Format representation = Format::Evaluation;
    int coefficient_precision_bits = 64;
    uint64_t prime_congruence = 0;
};

// ============================================================================
// Basic Data Type: Polynomial (single-residue polynomial, SRP)
// ============================================================================

/// Opaque polynomial type representing a vector of components (coefficients
/// or evaluation-point values) under a single modulus.
///
/// Corresponds to the "Polynomial" basic data type in the FHETCH Polynomial IR
/// specification. Integer polynomials operate modulo a prime q; non-integer
/// polynomials use fixed-point or floating-point components.
class Polynomial {
public:
    /// Construct an empty (invalid) polynomial.
    Polynomial();
    ~Polynomial();

    Polynomial(const Polynomial& other);
    Polynomial& operator=(const Polynomial& other);
    Polynomial(Polynomial&& other) noexcept;
    Polynomial& operator=(Polynomial&& other) noexcept;

    /// Construct a zero-initialized integer polynomial.
    /// @param ring_dim  Number of components (ring dimension N).
    /// @param fmt       Representation domain.
    static Polynomial zeros(uint64_t ring_dim, Format fmt = Format::Evaluation);

    /// Construct a zero-initialized non-integer polynomial.
    static Polynomial zeros_ni(uint64_t ring_dim, Format fmt = Format::Evaluation);

    /// Construct an integer polynomial from raw component data.
    /// @param components  Vector of uint64_t values (length = ring_dim).
    /// @param ring_dim    Ring dimension N.
    /// @param fmt         Representation domain.
    static Polynomial from_data(const std::vector<uint64_t>& components,
                                uint64_t ring_dim,
                                Format fmt = Format::Evaluation);

    /// Construct a non-integer polynomial from raw component data.
    static Polynomial from_data_ni(const std::vector<double>& components,
                                   uint64_t ring_dim,
                                   Format fmt = Format::Evaluation);

    /// @return Ring dimension N.
    uint64_t ring_dimension() const;

    /// @return Whether this is an integer or non-integer polynomial.
    NumberType number_type() const;

    /// @return Current representation domain (coefficient or evaluation).
    Format format() const;

    /// @return true if the polynomial holds valid data.
    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    /// Access the internal implementation (for Niobium internals only).
    PolynomialImpl* impl() const;

private:
    std::shared_ptr<PolynomialImpl> impl_;
    explicit Polynomial(std::shared_ptr<PolynomialImpl> p);
    friend struct PolynomialImpl;
};

// ============================================================================
// Basic Data Type: Scalar
// ============================================================================

/// Opaque scalar type representing a single value under a modulus (integer)
/// or a fixed-point/floating-point value (non-integer).
class Scalar {
public:
    Scalar();
    ~Scalar();

    Scalar(const Scalar& other);
    Scalar& operator=(const Scalar& other);
    Scalar(Scalar&& other) noexcept;
    Scalar& operator=(Scalar&& other) noexcept;

    /// Construct an integer scalar with a given value.
    static Scalar from_int(uint64_t value);

    /// Construct a non-integer scalar with a given value.
    static Scalar from_double(double value);

    /// @return Whether this is an integer or non-integer scalar.
    NumberType number_type() const;

    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    ScalarImpl* impl() const;

private:
    std::shared_ptr<ScalarImpl> impl_;
    explicit Scalar(std::shared_ptr<ScalarImpl> p);
    friend struct ScalarImpl;
};

// ============================================================================
// SRP Array (SRPA) — array of single-residue polynomials
// ============================================================================

/// Ordered array of single-residue polynomials.
class SRPArray {
public:
    /// Construct an empty array (length 0).
    SRPArray();
    ~SRPArray();

    SRPArray(const SRPArray& other);
    SRPArray& operator=(const SRPArray& other);
    SRPArray(SRPArray&& other) noexcept;
    SRPArray& operator=(SRPArray&& other) noexcept;

    /// Construct an array of n default-initialized polynomials.
    explicit SRPArray(size_t n);

    /// Construct from a list of polynomials.
    SRPArray(std::initializer_list<Polynomial> polys);

    /// @return Number of elements.
    size_t length() const;

    /// Access element at index i. Throws std::out_of_range if i >= length().
    Polynomial& operator[](size_t i);
    const Polynomial& operator[](size_t i) const;

    /// Append a polynomial to the end of the array.
    void append(const Polynomial& p);

    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    SRPArrayImpl* impl() const;

private:
    std::shared_ptr<SRPArrayImpl> impl_;
    explicit SRPArray(std::shared_ptr<SRPArrayImpl> p);
    friend struct SRPArrayImpl;
};

// ============================================================================
// Multi-Residue Polynomial (MRP)
// ============================================================================

/// A set of single-residue polynomials together with a set of moduli (the
/// "base"), where each polynomial is associated with one modulus.
///
/// Indexed by modulus value: mrp[q] returns the polynomial for prime q.
class MRP {
public:
    /// Construct an empty MRP with an empty base.
    MRP();
    ~MRP();

    MRP(const MRP& other);
    MRP& operator=(const MRP& other);
    MRP(MRP&& other) noexcept;
    MRP& operator=(MRP&& other) noexcept;

    /// Construct an MRP with a given base (zero-initialized polynomials).
    /// @param base     Ordered set of prime moduli.
    /// @param ring_dim Ring dimension N for each polynomial.
    explicit MRP(const ModuliBase& base, uint64_t ring_dim = 0);

    /// Construct an MRP from explicit (polynomial, modulus) pairs.
    static MRP from_pairs(const std::vector<std::pair<Polynomial, uint64_t>>& pairs);

    /// @return The moduli base (ordered set of primes).
    const ModuliBase& base() const;

    /// @return Number of residues (== base().size()).
    size_t num_residues() const;

    /// Access the polynomial for modulus q. Throws if q is not in the base.
    Polynomial& operator[](uint64_t q);
    const Polynomial& operator[](uint64_t q) const;

    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    MRPImpl* impl() const;

private:
    std::shared_ptr<MRPImpl> impl_;
    explicit MRP(std::shared_ptr<MRPImpl> p);
    friend struct MRPImpl;
    friend MRP mr_append_srp(const MRP& x, const Polynomial& a, uint64_t q_a);
    friend MRP mr_union(const MRP& x, const MRP& y);
    friend MRP mr_subset(const MRP& x, const ModuliBase& subbase);
};

// ============================================================================
// Multi-Residue Scalar (MRS)
// ============================================================================

/// A set of scalars together with a set of moduli, analogous to MRP but
/// for scalar values.
class MRS {
public:
    MRS();
    ~MRS();

    MRS(const MRS& other);
    MRS& operator=(const MRS& other);
    MRS(MRS&& other) noexcept;
    MRS& operator=(MRS&& other) noexcept;

    /// Construct an MRS with a given base (zero-initialized scalars).
    explicit MRS(const ModuliBase& base);

    /// Construct from explicit (scalar, modulus) pairs.
    static MRS from_pairs(const std::vector<std::pair<Scalar, uint64_t>>& pairs);

    /// @return The moduli base.
    const ModuliBase& base() const;

    /// @return Number of residues.
    size_t num_residues() const;

    /// Access the scalar for modulus q.
    Scalar& operator[](uint64_t q);
    const Scalar& operator[](uint64_t q) const;

    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    MRSImpl* impl() const;

private:
    std::shared_ptr<MRSImpl> impl_;
    explicit MRS(std::shared_ptr<MRSImpl> p);
    friend struct MRSImpl;
};

// ============================================================================
// MRP Array (MRPA)
// ============================================================================

/// An array of multi-residue polynomials. Each element may have a different
/// base and number of residues.
class MRPArray {
public:
    /// Construct an empty array (length 0).
    MRPArray();
    ~MRPArray();

    MRPArray(const MRPArray& other);
    MRPArray& operator=(const MRPArray& other);
    MRPArray(MRPArray&& other) noexcept;
    MRPArray& operator=(MRPArray&& other) noexcept;

    /// Construct an array of n default-initialized MRPs.
    explicit MRPArray(size_t n);

    /// Construct from a list of MRPs.
    MRPArray(std::initializer_list<MRP> mrps);

    /// @return Number of elements.
    size_t length() const;

    /// Access element at index i. Throws std::out_of_range if i >= length().
    MRP& operator[](size_t i);
    const MRP& operator[](size_t i) const;

    /// Append an MRP to the end of the array.
    void append(const MRP& m);

    bool is_valid() const;
    explicit operator bool() const { return is_valid(); }

    MRPArrayImpl* impl() const;

private:
    std::shared_ptr<MRPArrayImpl> impl_;
    explicit MRPArray(std::shared_ptr<MRPArrayImpl> p);
    friend struct MRPArrayImpl;
};

// ============================================================================
//  INPUT / OUTPUT TAGGING
// ============================================================================
// Mark polynomials as inputs or outputs so the server-side optimizer knows
// which addresses are observable and must not be eliminated.

/// Mark a single-residue polynomial as a named input.
void tag_input(const std::string& name, const Polynomial& p);

/// Mark a single-residue polynomial as a named output (probe point).
void tag_output(const std::string& name, const Polynomial& p);

/// Mark all residues of an MRP as named inputs.
void tag_input(const std::string& name, const MRP& m);

/// Mark all residues of an MRP as named outputs.
void tag_output(const std::string& name, const MRP& m);

/// Mark all elements of an SRP array as named inputs.
void tag_input(const std::string& name, const SRPArray& arr);

/// Mark all elements of an SRP array as named outputs.
void tag_output(const std::string& name, const SRPArray& arr);

/// Mark all elements of an MRP array as named inputs.
void tag_input(const std::string& name, const MRPArray& arr);

/// Mark all elements of an MRP array as named outputs.
void tag_output(const std::string& name, const MRPArray& arr);

/// Reset FHETCH state for a new epoch.
/// Clears input/output registries and resets address allocator to 0.
void reset_for_epoch();

/// Get the ring dimension from the first registered input (0 if none).
uint64_t get_input_ring_dimension();

/// Save all named input data to JSON files in the program directory.
/// Called automatically during Compiler::stop() in FHETCH mode.
void save_input_data();

/// Save all named probe outputs to a JSON file in the program directory.
/// Called automatically during Compiler::stop() in FHETCH mode.
void save_probe_outputs();

// ============================================================================
//  BASELINE INSTRUCTIONS (required by all compliant hardware)
// ============================================================================

/// Polynomial addition: f_i = (a_i + b_i) mod q
Polynomial sr_addp(const Polynomial& a, const Polynomial& b, uint64_t q);

/// Polynomial scalar addition (evaluation representation):
///   f_i = (a_i + s) mod q
Polynomial sr_addps(const Polynomial& a, const Scalar& s, uint64_t q);

/// Polynomial scalar addition (coefficient representation):
///   f_0 = (a_0 + s) mod q,  f_{i>0} = a_i
Polynomial sr_addps_coeff(const Polynomial& a, const Scalar& s, uint64_t q);

/// Polynomial negation: f_i = (-a_i) mod q
Polynomial sr_negp(const Polynomial& a, uint64_t q);

/// Polynomial subtraction: f_i = (a_i - b_i) mod q
Polynomial sr_subp(const Polynomial& a, const Polynomial& b, uint64_t q);

/// Polynomial scalar subtraction (evaluation representation):
///   f_i = (a_i - s) mod q
Polynomial sr_subps(const Polynomial& a, const Scalar& s, uint64_t q);

/// Polynomial scalar subtraction (coefficient representation):
///   f_0 = (a_0 - s) mod q,  f_{i>0} = a_i
Polynomial sr_subps_coeff(const Polynomial& a, const Scalar& s, uint64_t q);

/// Polynomial (component-wise) multiplication: f_i = (a_i * b_i) mod q
Polynomial sr_mulp(const Polynomial& a, const Polynomial& b, uint64_t q);

/// Polynomial scalar multiplication: f_i = (a_i * s) mod q
Polynomial sr_mulps(const Polynomial& a, const Scalar& s, uint64_t q);

/// Negacyclic Number Theoretic Transform.
/// Coefficient representation → evaluation representation mod q.
Polynomial sr_ntt(const Polynomial& a, uint64_t q);

/// Inverse Negacyclic Number Theoretic Transform.
/// Evaluation representation → coefficient representation mod q.
Polynomial sr_intt(const Polynomial& a, uint64_t q);

/// General permutation with sign flips.
/// @param a     Input polynomial.
/// @param srcs  Source index for each output position (values in [0, N-1]).
/// @param signs Sign flip for each position (+1 or -1).
/// @param q     Modulus (used for sign flip via q - value).
Polynomial sr_permute(const Polynomial& a,
                      const std::vector<uint64_t>& srcs,
                      const std::vector<int>& signs,
                      uint64_t q);

/// Halt: signal end of instruction trace.
void halt();

// ============================================================================
//  OPTIONAL OPERATIONS (scheme-specific, not required)
// ============================================================================

// --- Non-integer polynomial arithmetic (no modulus) ---

Polynomial sr_addp_ni(const Polynomial& a, const Polynomial& b);
Polynomial sr_addps_ni(const Polynomial& a, const Scalar& s);
Polynomial sr_addps_coeff_ni(const Polynomial& a, const Scalar& s);
Polynomial sr_negp_ni(const Polynomial& a);
Polynomial sr_subp_ni(const Polynomial& a, const Polynomial& b);
Polynomial sr_subps_ni(const Polynomial& a, const Scalar& s);
Polynomial sr_subps_coeff_ni(const Polynomial& a, const Scalar& s);
Polynomial sr_mulp_ni(const Polynomial& a, const Polynomial& b);
Polynomial sr_mulps_ni(const Polynomial& a, const Scalar& s);

// --- Fourier Transforms (alternative to NTT for TFHE/FHEW) ---

Polynomial sr_ft(const Polynomial& a);
Polynomial sr_ift(const Polynomial& a);

// --- Coefficient access ---

Scalar sr_coeff_extract(const Polynomial& p, uint64_t i);
Polynomial sr_coeff_assign(const Polynomial& p, uint64_t i, const Scalar& val);

// --- Torus and sample operations (TFHE/FHEW) ---

Polynomial sr_torus_mod_reduce(const Polynomial& p, double c);
std::vector<uint64_t> sr_sample_extract(const SRPArray& rlwe, uint64_t lwe_dim);

// ============================================================================
//  GADGETS — Polynomial level
// ============================================================================

/// Galois automorphism in evaluation representation.
Polynomial sr_automorph_eval(const Polynomial& x, uint64_t k);

/// Galois automorphism in coefficient representation.
Polynomial sr_automorph_coeff(const Polynomial& x, uint64_t k, uint64_t q);

/// Negacyclic rotation automorphism in coefficient representation.
Polynomial sr_rot_automorph_coeff(const Polynomial& x, uint64_t offset, uint64_t q);

/// Batch forward Fourier/NTT transform over an SRP array.
SRPArray sr_batch_ft(const SRPArray& x);

/// Batch inverse Fourier/NTT transform over an SRP array.
SRPArray sr_batch_ift(const SRPArray& x);

// ============================================================================
//  GADGETS — Multi-Residue basic arithmetic
// ============================================================================

/// MRP addition: z[q] = sr_addp(x[q], y[q], q) for each q in base.
MRP mr_addp(const MRP& x, const MRP& y);

/// MRP subtraction.
MRP mr_subp(const MRP& x, const MRP& y);

/// MRP multiplication.
MRP mr_mulp(const MRP& x, const MRP& y);

/// MRP-scalar multiplication: z[q] = sr_mulps(x[q], s[q], q).
MRP mr_mulps(const MRP& x, const MRS& s);

/// MRP-scalar addition: z[q] = sr_addps(x[q], s[q], q).
MRP mr_addps(const MRP& x, const MRS& s);

/// MRP NTT: apply sr_ntt to each residue.
MRP mr_ntt(const MRP& x);

/// MRP inverse NTT: apply sr_intt to each residue.
MRP mr_intt(const MRP& x);

/// Construct a zero-initialized MRP with the given base and ring dimension.
MRP mr_zeros(const ModuliBase& target_base, uint64_t ring_dim);

// ============================================================================
//  GADGETS — MRP residue manipulation
// ============================================================================

/// Append a single-residue polynomial under modulus q_a.
MRP mr_append_srp(const MRP& x, const Polynomial& a, uint64_t q_a);

/// Union of two MRPs with mutually exclusive bases.
MRP mr_union(const MRP& x, const MRP& y);

/// Subset of an MRP restricted to a sub-base.
MRP mr_subset(const MRP& x, const ModuliBase& subbase);

// ============================================================================
//  GADGETS — Fast Base Conversion and CKKS Rescale
// ============================================================================

/// Fast base conversion (CRT-based approximate conversion).
MRP fast_base_convert(const MRP& x, const ModuliBase& target_base);

/// CKKS rescale using fast base conversion.
MRP rescale_fbc(const MRP& x, const ModuliBase& rescale_base);

// ============================================================================
//  GADGETS — MRP Array operations
// ============================================================================

/// MRPA dot-product: z = sum_i mr_mulp(x[i], y[i]).
MRP mrpa_dotproduct(const MRPArray& x, const MRPArray& y);

// ============================================================================
//  GADGETS — Decomposition (key-switching and TFHE)
// ============================================================================

/// CKKS/BGV/BFV digit decomposition for hybrid key-switching.
MRPArray dig_decomp(const MRP& x,
                     const std::vector<ModuliBase>& digit_bases,
                     const ModuliBase& p_base);

/// TFHE gadget decomposition (unsigned integer version).
SRPArray gadget_decomp(const Polynomial& x, uint64_t base, uint64_t n_levels);

/// TFHE gadget decomposition for power-of-two base.
SRPArray gadget_decomp_pow2(const Polynomial& x, uint64_t log_base, uint64_t n_levels);

// ============================================================================
//  GADGETS — GSW/RLWE External Product
// ============================================================================

/// GSW/RLWE external product.
SRPArray gsw_rlwe_ext_prod(const SRPArray& gsw,
                           const SRPArray& rlwe_in,
                           uint64_t l,
                           uint64_t base);

// ============================================================================
//  CKKS Bootstrapping (optional high-level operation)
// ============================================================================

/// CKKS bootstrapping.
MRPArray ckks_bootstrap(const MRPArray& ct_in, const MRPArray& aux_data);

// ============================================================================
//  FILE I/O — JSON (human-readable)
// ============================================================================

bool save_polynomial_json(const Polynomial& p, const std::filesystem::path& file);
bool load_polynomial_json(Polynomial& p, const std::filesystem::path& file);
bool save_mrp_json(const MRP& m, const std::filesystem::path& file);
bool load_mrp_json(MRP& m, const std::filesystem::path& file);
bool save_mrp_array_json(const MRPArray& arr, const std::filesystem::path& file);
bool load_mrp_array_json(MRPArray& arr, const std::filesystem::path& file);

}  // namespace niobium::fhetch
