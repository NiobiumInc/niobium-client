// Copyright (C) 2023-2026, All rights reserved by Niobium Microsystems.
// The contents of this file and all related materials provided herein (the
// "Product") may not be used except pursuant to a separate written
// agreement signed by a duly authorized officer of Niobium Microsystems,
// Inc. (a "License Agreement").
// Without limiting the foregoing, you may not, at any time or for any
// reason, directly or indirectly, in whole or in part: (i) copy, modify,
// or create derivative works of the Product; (ii) rent, lease, lend, sell,
// sublicense, assign, distribute, publish, transfer, or otherwise make
// available the Product; (iii) reverse engineer, disassemble, decompile,
// decode, or adapt the Product; or (iv) remove any proprietary notices
// from the Product.

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
// Mark polynomials as inputs or outputs so the optimizer knows which
// addresses are observable and must not be eliminated.

/// Mark a single-residue polynomial as a named input.
/// The name is used as the key in the inputs JSON file and registers the
/// polynomial data for serialization during replay index creation.
void tag_input(const std::string& name, const Polynomial& p);

/// Mark a single-residue polynomial as a named output (probe point).
/// The name is used as the key in the outputs JSON file.
void tag_output(const std::string& name, const Polynomial& p);

/// Mark all residues of an MRP as named inputs.
void tag_input(const std::string& name, const MRP& m);

/// Mark all residues of an MRP as named outputs.
void tag_output(const std::string& name, const MRP& m);

/// Mark all elements of an SRP array as named inputs.
/// SRPArray is indexed by position (not modulus), so it can hold
/// polynomials with duplicate moduli — unlike MRP.
void tag_input(const std::string& name, const SRPArray& arr);

/// Mark all elements of an SRP array as named outputs.
void tag_output(const std::string& name, const SRPArray& arr);

/// Mark all elements of an MRP array as named inputs.
void tag_input(const std::string& name, const MRPArray& arr);

/// Mark all elements of an MRP array as named outputs.
void tag_output(const std::string& name, const MRPArray& arr);

/// Reset FHETCH state for a new epoch.
/// Clears input/output registries, resets address allocator to 0,
/// and invalidates the replay output cache.
void reset_for_epoch();

/// Get the ring dimension from the first registered input (0 if none).
/// Used internally to populate synthetic crypto_context for replay.
uint64_t get_input_ring_dimension();

/// Save all named input data to JSON files.
/// Writes per-input files and a master index to the program directory
/// in the format expected by the replay engine.
/// Called automatically during create_replay_index() in FHETCH mode.
void save_input_data();

/// Save all named probe outputs to a JSON file.
/// Writes to <program_dir>/<program_name>.outputs.json using the same
/// format as the regular compiler flow.
/// Called automatically during Compiler::stop() in FHETCH mode, or
/// can be called explicitly.
void save_probe_outputs();

// ============================================================================
//  RESULT RETRIEVAL (after replay)
// ============================================================================

/// Retrieve a single-residue polynomial result from replay output.
/// Reads from replay_outputs_integer.json, finds the named output,
/// and populates the Polynomial with computed coefficient values.
/// @param name  The output name used in tag_output().
/// @param p     Polynomial to populate with result data.
/// @return true if the result was found and populated successfully.
bool result(const std::string& name, Polynomial& p);

/// Retrieve a multi-residue polynomial result from replay output.
/// @param name  The output name used in tag_output().
/// @param m     MRP to populate with result data.
/// @return true if the result was found and populated successfully.
bool result(const std::string& name, MRP& m);

/// Retrieve an MRP array result from replay output.
/// @param name  The output name used in tag_output().
/// @param arr   MRPArray to populate with result data.
/// @return true if the result was found and populated successfully.
bool result(const std::string& name, MRPArray& arr);

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
/// This is the polynomial product only when both a and b are in evaluation
/// representation.
Polynomial sr_mulp(const Polynomial& a, const Polynomial& b, uint64_t q);

/// Polynomial scalar multiplication: f_i = (a_i * s) mod q
Polynomial sr_mulps(const Polynomial& a, const Scalar& s, uint64_t q);

/// Negacyclic Number Theoretic Transform.
/// Takes a from coefficient representation to evaluation representation
/// relative to modulus q, using a 2N-th primitive root of unity psi mod q.
Polynomial sr_ntt(const Polynomial& a, uint64_t q);

/// Inverse Negacyclic Number Theoretic Transform.
/// Takes a from evaluation representation to coefficient representation
/// relative to modulus q.
Polynomial sr_intt(const Polynomial& a, uint64_t q);

/// General permutation with sign flips.
/// @param a     Input polynomial.
/// @param srcs  Source index for each output position (values in [0, N-1]).
/// @param signs Sign flip for each position (+1 or -1).
/// @param q     Modulus (used for sign flip via q - value).
/// @return      f_i = signs[i] * a[srcs[i]]  (mod q when signs[i] == -1).
Polynomial sr_permute(const Polynomial& a,
                      const std::vector<uint64_t>& srcs,
                      const std::vector<int>& signs,
                      uint64_t q);

/// Halt: signal the hardware to stop and notify the host.
void halt();

// ============================================================================
//  OPTIONAL OPERATIONS (scheme-specific, not required)
// ============================================================================

// --- Non-integer polynomial arithmetic (no modulus) ---

/// Non-integer polynomial addition: f_i = a_i + b_i
Polynomial sr_addp_ni(const Polynomial& a, const Polynomial& b);

/// Non-integer polynomial scalar addition (evaluation): f_i = a_i + s
Polynomial sr_addps_ni(const Polynomial& a, const Scalar& s);

/// Non-integer polynomial scalar addition (coefficient):
///   f_0 = a_0 + s,  f_{i>0} = a_i
Polynomial sr_addps_coeff_ni(const Polynomial& a, const Scalar& s);

/// Non-integer polynomial negation: f_i = -a_i
Polynomial sr_negp_ni(const Polynomial& a);

/// Non-integer polynomial subtraction: f_i = a_i - b_i
Polynomial sr_subp_ni(const Polynomial& a, const Polynomial& b);

/// Non-integer polynomial scalar subtraction (evaluation): f_i = a_i - s
Polynomial sr_subps_ni(const Polynomial& a, const Scalar& s);

/// Non-integer polynomial scalar subtraction (coefficient):
///   f_0 = a_0 - s,  f_{i>0} = a_i
Polynomial sr_subps_coeff_ni(const Polynomial& a, const Scalar& s);

/// Non-integer polynomial multiplication: f_i = a_i * b_i
Polynomial sr_mulp_ni(const Polynomial& a, const Polynomial& b);

/// Non-integer polynomial scalar multiplication: f_i = a_i * s
Polynomial sr_mulps_ni(const Polynomial& a, const Scalar& s);

// --- Fourier Transforms (alternative to NTT for TFHE/FHEW) ---

/// Negacyclic Fourier Transform (complex-valued).
/// Alternative to sr_ntt for hardware supporting only FHEW/TFHE schemes.
Polynomial sr_ft(const Polynomial& a);

/// Inverse Negacyclic Fourier Transform (complex-valued).
/// Must be used if and only if sr_ft is used in place of sr_ntt.
Polynomial sr_ift(const Polynomial& a);

// --- Coefficient access ---

/// Extract the i-th component of polynomial p as a scalar.
Scalar sr_coeff_extract(const Polynomial& p, uint64_t i);

/// Return a new polynomial with component at position i set to val.
Polynomial sr_coeff_assign(const Polynomial& p, uint64_t i, const Scalar& val);

// --- Torus and sample operations (TFHE/FHEW) ---

/// Torus modular reduction.
/// Returns a polynomial with coefficients in [c-0.5, c+0.5) equal to
/// those of p up to an integer polynomial.
Polynomial sr_torus_mod_reduce(const Polynomial& p, double c);

/// Sample extraction (TFHE/FHEW).
/// Takes an RLWE cipher (a, b) as an SRPArray of length 2 and returns
/// an LWE cipher as a vector of integer components.
std::vector<uint64_t> sr_sample_extract(const SRPArray& rlwe, uint64_t lwe_dim);

// ============================================================================
//  GADGETS — Polynomial level (Class I: simple, verifiable by inspection)
// ============================================================================

/// Galois automorphism in evaluation representation.
/// @param x  Integer polynomial in evaluation form.
/// @param k  Odd integer in [1, 2N-1].
Polynomial sr_automorph_eval(const Polynomial& x, uint64_t k);

/// Galois automorphism in coefficient representation.
/// @param x  Integer polynomial in coefficient form.
/// @param k  Odd integer in [1, 2N-1].
/// @param q  Modulus.
Polynomial sr_automorph_coeff(const Polynomial& x, uint64_t k, uint64_t q);

/// Negacyclic rotation automorphism in coefficient representation.
/// @param x       Integer polynomial in coefficient form.
/// @param offset  Rotation offset in [0, N-1].
/// @param q       Modulus.
Polynomial sr_rot_automorph_coeff(const Polynomial& x, uint64_t offset, uint64_t q);

/// Batch forward Fourier/NTT transform over an SRP array.
SRPArray sr_batch_ft(const SRPArray& x);

/// Batch inverse Fourier/NTT transform over an SRP array.
SRPArray sr_batch_ift(const SRPArray& x);

// ============================================================================
//  GADGETS — Multi-Residue basic arithmetic
// ============================================================================

/// MRP addition: for each q in base, z[q] = sr_addp(x[q], y[q], q).
/// Both operands must share the same base.
MRP mr_addp(const MRP& x, const MRP& y);

/// MRP subtraction: for each q in base, z[q] = sr_subp(x[q], y[q], q).
MRP mr_subp(const MRP& x, const MRP& y);

/// MRP multiplication: for each q in base, z[q] = sr_mulp(x[q], y[q], q).
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

/// Append a single-residue polynomial to an MRP under modulus q_a.
/// Returns a new MRP with base = x.base ∪ {q_a}.
MRP mr_append_srp(const MRP& x, const Polynomial& a, uint64_t q_a);

/// Union of two MRPs with mutually exclusive bases.
/// Returns a new MRP with base = x.base ∪ y.base.
MRP mr_union(const MRP& x, const MRP& y);

/// Subset of an MRP restricted to a sub-base.
/// @param subbase  Must be a subset of x.base().
MRP mr_subset(const MRP& x, const ModuliBase& subbase);

// ============================================================================
//  GADGETS — Fast Base Conversion and CKKS Rescale
// ============================================================================

/// Fast base conversion.
/// Converts a coefficient-mode MRP from its current base to target_base
/// using the CRT-based approximate conversion algorithm.
/// @param x            Coefficient-mode MRP in source base.
/// @param target_base  Target moduli base.
MRP fast_base_convert(const MRP& x, const ModuliBase& target_base);

/// CKKS rescale using fast base conversion.
/// Removes the residues in rescale_base from x and rescales.
/// @param x              Coefficient-mode MRP.
/// @param rescale_base   Moduli to remove (subset of x.base()).
/// @return MRP in base = x.base() \ rescale_base.
MRP rescale_fbc(const MRP& x, const ModuliBase& rescale_base);

// ============================================================================
//  GADGETS — MRP Array operations
// ============================================================================

/// MRPA dot-product.
/// Returns a single MRP that is the sum of element-wise MRP products:
///   z = sum_i mr_mulp(x[i], y[i])
/// Both arrays must have the same length. All MRPs must share the same base.
MRP mrpa_dotproduct(const MRPArray& x, const MRPArray& y);

// ============================================================================
//  GADGETS — Decomposition (key-switching and TFHE)
// ============================================================================

/// CKKS/BGV/BFV digit decomposition for hybrid key-switching.
/// @param x            Coefficient-mode MRP in RNS base representing Q.
/// @param digit_bases  List of d mutually exclusive moduli sub-bases.
/// @param p_base       RNS base for the temporary modulus P.
/// @return MRPA of length d; element i is base-extended from digit_bases[i]
///         to (x.base() ∪ p_base).
MRPArray dig_decomp(const MRP& x,
                     const std::vector<ModuliBase>& digit_bases,
                     const ModuliBase& p_base);

/// TFHE gadget decomposition (unsigned integer version).
/// @param x         Single-residue polynomial with integer coefficients.
/// @param base      Decomposition basis B.
/// @param n_levels  Number of decomposition levels l.
/// @return SRPArray of length n_levels.
SRPArray gadget_decomp(const Polynomial& x, uint64_t base, uint64_t n_levels);

/// TFHE gadget decomposition for power-of-two base.
/// @param x          Single-residue polynomial with integer coefficients.
/// @param log_base   log2 of the decomposition basis.
/// @param n_levels   Number of decomposition levels l.
/// @return SRPArray of length n_levels.
SRPArray gadget_decomp_pow2(const Polynomial& x, uint64_t log_base, uint64_t n_levels);

// ============================================================================
//  GADGETS — GSW/RLWE External Product (requires optional TFHE ops)
// ============================================================================

/// GSW/RLWE external product.
/// @param gsw       GSW ciphertext as SRPArray of 4*l polynomials.
/// @param rlwe_in   RLWE ciphertext as SRPArray of 2 polynomials.
/// @param l         Number of gadget decomposition levels.
/// @param base      Decomposition basis B.
/// @return RLWE ciphertext as SRPArray of 2 polynomials.
SRPArray gsw_rlwe_ext_prod(const SRPArray& gsw,
                           const SRPArray& rlwe_in,
                           uint64_t l,
                           uint64_t base);

// ============================================================================
//  CKKS Bootstrapping (optional high-level operation)
// ============================================================================

/// CKKS bootstrapping.
/// @param ct_in     Input ciphertext as MRPA of length 2.
/// @param aux_data  Auxiliary data (key-switching keys, plaintext polynomials).
/// @return Refreshed ciphertext as MRPA of length 2.
MRPArray ckks_bootstrap(const MRPArray& ct_in, const MRPArray& aux_data);

// ============================================================================
//  FILE I/O — Single objects
// ============================================================================

/// Save a single polynomial to a binary file.
/// @return true on success.
bool save_polynomial(const Polynomial& p, const std::filesystem::path& file);

/// Load a single polynomial from a binary file.
/// @return true on success (p is populated).
bool load_polynomial(Polynomial& p, const std::filesystem::path& file);

/// Save a single scalar to a binary file.
bool save_scalar(const Scalar& s, const std::filesystem::path& file);

/// Load a single scalar from a binary file.
bool load_scalar(Scalar& s, const std::filesystem::path& file);

/// Save a single MRP to a binary file (all residues + base metadata).
bool save_mrp(const MRP& m, const std::filesystem::path& file);

/// Load a single MRP from a binary file.
bool load_mrp(MRP& m, const std::filesystem::path& file);

/// Save a single MRS to a binary file.
bool save_mrs(const MRS& m, const std::filesystem::path& file);

/// Load a single MRS from a binary file.
bool load_mrs(MRS& m, const std::filesystem::path& file);

// ============================================================================
//  FILE I/O — Arrays / groups
// ============================================================================

/// Save an SRP array (group of single-residue polynomials) to a binary file.
bool save_srp_array(const SRPArray& arr, const std::filesystem::path& file);

/// Load an SRP array from a binary file.
bool load_srp_array(SRPArray& arr, const std::filesystem::path& file);

/// Save an MRP array (group of multi-residue polynomials) to a binary file.
bool save_mrp_array(const MRPArray& arr, const std::filesystem::path& file);

/// Load an MRP array from a binary file.
bool load_mrp_array(MRPArray& arr, const std::filesystem::path& file);

// ============================================================================
//  FILE I/O — Directory-based (one file per residue/tower)
// ============================================================================

/// Save an MRP to a directory with one file per residue polynomial.
/// Creates files named <dir>/residue_<modulus>.bin plus a manifest.json.
bool save_mrp_dir(const MRP& m, const std::filesystem::path& dir);

/// Load an MRP from a directory written by save_mrp_dir.
bool load_mrp_dir(MRP& m, const std::filesystem::path& dir);

/// Save an MRP array to a directory with one subdirectory per element.
/// Creates <dir>/mrp_<index>/ subdirectories, each containing per-residue files.
bool save_mrp_array_dir(const MRPArray& arr, const std::filesystem::path& dir);

/// Load an MRP array from a directory written by save_mrp_array_dir.
bool load_mrp_array_dir(MRPArray& arr, const std::filesystem::path& dir);

// ============================================================================
//  FILE I/O — JSON (human-readable, for debugging)
// ============================================================================

/// Save a polynomial in JSON format.
bool save_polynomial_json(const Polynomial& p, const std::filesystem::path& file);

/// Load a polynomial from JSON format.
bool load_polynomial_json(Polynomial& p, const std::filesystem::path& file);

/// Save an MRP in JSON format (all residues + base metadata).
bool save_mrp_json(const MRP& m, const std::filesystem::path& file);

/// Load an MRP from JSON format.
bool load_mrp_json(MRP& m, const std::filesystem::path& file);

/// Save an MRP array in JSON format.
bool save_mrp_array_json(const MRPArray& arr, const std::filesystem::path& file);

/// Load an MRP array from JSON format.
bool load_mrp_array_json(MRPArray& arr, const std::filesystem::path& file);

}  // namespace niobium::fhetch
