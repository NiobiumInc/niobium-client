"""Single source of truth for nb built-in functions.

Every built-in is declared exactly once here with its classification facts.
Both the semantic analyzer (return types, depth-opacity) and the code
generator (encrypted-ness, plaintext-ness, vector-ness) derive their tables
from this registry — adding a built-in is a one-line change, and the two
phases can no longer drift apart.

User-defined functions are NOT listed here: their classification comes from
their declared signatures (see codegen's `_fn_sigs` lookup).

Return kinds:
  'enc'       — returns a ciphertext (Ciphertext<DCRTPoly> or enc<T>)
  'plain'     — returns plaintext data whose precise type isn't modeled
                (vectors/matrices of cleartext values, paths into data, ...)
  'u32'/'i64'/'f64'/'string'/'path'/'void' — concrete plaintext scalars
  'keybundle'/'secretkey' — key objects
  'unknown'   — genuinely polymorphic (e.g. load() of an arbitrary wire type)

Flags:
  enc_hint      — codegen treats the result as a ciphertext even though the
                  semantic return type stays unknown (e.g. reduce over a
                  ciphertext collection).
  vector_return — generates a std::vector<double> (plaintext vector); drives
                  MakeCKKSPackedPlaintext wrapping in mixed ct/vector ops.
  depth_opaque  — performs homomorphic multiplications whose depth is not
                  statically modeled; suppresses the depth over-provision
                  warning for any program using it.
"""

from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Builtin:
    name: str
    returns: str
    enc_hint: bool = False
    vector_return: bool = False
    depth_opaque: bool = False


BUILTINS: list[Builtin] = [
    # ── Keys and encryption ────────────────────────────────────────────
    Builtin("keygen", "keybundle"),
    Builtin("encrypt", "enc"),
    Builtin("decrypt", "plain"),            # plaintext vector of slot values
    Builtin("save_secret_key", "void"),
    Builtin("load_secret_key", "secretkey"),

    # ── Serialization / data loading ───────────────────────────────────
    Builtin("load", "unknown"),             # wire-type dependent
    Builtin("load_all", "unknown"),
    Builtin("load_matrix", "plain", vector_return=False),
    Builtin("load_vec", "plain"),
    Builtin("load_model", "unknown"),
    Builtin("save", "void"),

    # ── Ciphertext operations ──────────────────────────────────────────
    Builtin("clone", "enc"),
    Builtin("zero", "enc"),
    Builtin("relin", "enc"),
    Builtin("rotate", "enc"),
    Builtin("negate", "enc"),
    Builtin("mul_monomial", "enc"),
    Builtin("chebyshev", "enc", depth_opaque=True),
    Builtin("slot_sum", "enc"),
    Builtin("total_sums", "enc"),
    Builtin("running_sums", "void", depth_opaque=True),
    Builtin("slot_replicator", "unknown"),
    Builtin("extern_call", "unknown", depth_opaque=True),

    # ── Collection combinators (closure depth not modeled) ─────────────
    Builtin("reduce", "unknown", enc_hint=True, depth_opaque=True),
    Builtin("map", "unknown", depth_opaque=True),
    Builtin("zip_map", "unknown", depth_opaque=True),

    # ── Plaintext data shaping ─────────────────────────────────────────
    Builtin("tile", "plain", vector_return=True),
    Builtin("slot_mask", "plain", vector_return=True),
    Builtin("to_matrix_form", "plain", vector_return=True),
    Builtin("transpose", "plain"),
    Builtin("batch", "plain"),
    Builtin("scale", "plain"),
    Builtin("prepend_column", "plain"),
    Builtin("vec_zeros", "unknown"),        # vec_zeros<enc<...>> can hold cts
    Builtin("mat_zeros", "plain"),
    Builtin("sort", "plain"),
    Builtin("argmax", "plain"),
    Builtin("enumerate", "unknown"),
    Builtin("stride", "plain"),

    # ── Plaintext scalar helpers ───────────────────────────────────────
    Builtin("len", "u32"),
    Builtin("rows", "u32"),
    Builtin("ceil_div", "u32"),
    Builtin("log2", "u32"),
    Builtin("round", "i64"),
    Builtin("int", "i64"),
    Builtin("str", "string"),
    Builtin("abs", "f64"),
    Builtin("exp", "f64"),
    Builtin("tanh", "f64"),
    Builtin("root", "path"),

    # ── I/O / debug ─────────────────────────────────────────────────────
    Builtin("print", "void"),
]

BY_NAME: dict[str, Builtin] = {b.name: b for b in BUILTINS}

# Return kinds that are definitely NOT ciphertexts.
_PLAIN_KINDS = {"plain", "u32", "i64", "f64", "string", "path", "void",
                "keybundle", "secretkey"}

# Derived tables (consumed by codegen) ---------------------------------------

# Built-ins whose result is a ciphertext for codegen purposes.
ENCRYPTED_RETURN_FNS = {b.name for b in BUILTINS
                        if b.returns == "enc" or b.enc_hint}

# Built-ins whose result is definitely plaintext.
PLAINTEXT_RETURN_FNS = {b.name for b in BUILTINS if b.returns in _PLAIN_KINDS}

# Built-ins generating a std::vector<double> plaintext vector.
VECTOR_RETURN_FNS = {b.name for b in BUILTINS if b.vector_return}

# Built-ins with unmodeled internal multiplicative depth.
DEPTH_OPAQUE_FNS = {b.name for b in BUILTINS if b.depth_opaque}
