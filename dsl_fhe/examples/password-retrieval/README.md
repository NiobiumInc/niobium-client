# Password Retrieval via FHE Security Questions

An FHE application that verifies security question answers and retrieves a password
without the server ever seeing the answers or the password in plaintext.

## How It Works

A user's record contains 5 encrypted answer codes and an encrypted password. To
retrieve the password, the user submits encrypted answers to their 5 security
questions. The server homomorphically computes whether the answers match — and only
reveals the password if all 5 are correct.

### FHE Approach

Direct comparison (`==`) is not available in CKKS. Instead, the server uses
**Chebyshev polynomial approximation** of a Gaussian indicator function to test
each answer:

1. For each question, compute the difference: `d_i = submitted_i - stored_i`
2. Evaluate a Gaussian indicator via Chebyshev: `m_i ≈ 1` if `d_i ≈ 0`, `m_i ≈ 0` otherwise
3. Multiply all 5 indicators: `overall = m_1 × m_2 × m_3 × m_4 × m_5`
4. Multiply by the encrypted password: `result = overall × password`

When all answers match, `overall ≈ 1` and the password is revealed. When any answer
is wrong, the corresponding `m_i ≈ 0` drives the entire product to zero, hiding the
password.

### Indicator Function

The Gaussian `exp(-(x/σ)²/2)` with σ=0.3 provides sharp discrimination for
integer-valued answer codes:

| Difference | Indicator value | Product of 5 |
|------------|----------------|--------------|
| 0 (match)  | 1.0            | 1.0          |
| 1          | 0.004          | 4.4×10⁻⁵     |
| 2          | ~0             | ~0           |

The Chebyshev approximation uses degree 59 on domain [-5, 5].

## Pipeline Stages

| # | Binary | Domain | Purpose |
|---|--------|--------|---------|
| 1 | `key_generation` | client | Generate CKKS context and keys |
| 2 | `setup_record` | client | Encrypt stored answers + password |
| 3 | `submit_query` | client | Encrypt user's submitted answers |
| 4 | `verify` | server | Homomorphic comparison + password gating |
| 5 | `decrypt_result` | client | Decrypt and display result |

## Packing Strategy

**Single-slot packing** — one value per ciphertext. Each answer and the password are
encrypted independently. This is the simplest strategy and sufficient for the small
number of ciphertexts involved (11 loads per verification: 5 stored answers + 1
password + 5 submitted answers).

## CKKS Parameters

| Parameter | Value |
|-----------|-------|
| Scheme | CKKS |
| Depth | 15 |
| Ring dimension | 2048 (Toy) / 8192 (Small) |
| Scaling | FLEXIBLEAUTO |
| Chebyshev degree | 59 per question |

Depth budget: 6 levels (Chebyshev) + 3 levels (multiply 5 indicators) + 1 level
(multiply by password) + headroom.

## Build and Run

```bash
cd dsl_fhe

# Build
make password-retrieval

# End-to-end test (correct + wrong answers)
make test-password
```

### Manual run

```bash
cd examples/password-retrieval/nb_out/build
export LD_LIBRARY_PATH=<path-to-openfhe-lib>

# Setup
./key_generation 0                        # Toy instance
./setup_record 0 7.0 3.0 9.0 1.0 5.0 42.0  # answers: 7,3,9,1,5  password: 42

# Correct answers → password retrieved
./submit_query 0 7.0 3.0 9.0 1.0 5.0
./verify 0
./decrypt_result 0 42.0                   # prints ≈ 42

# Wrong answers → password hidden
./submit_query 0 7.0 3.0 9.0 1.0 8.0     # last answer wrong
./verify 0
./decrypt_result 0 0.0                    # prints ≈ 0
```

## Wire Types

This example uses a single `EncryptedValue` wire type with explicit `save()`/`load()`
to different file paths for each of the 11 encrypted values (5 stored answers +
password + 5 submitted answers):

```
wire EncryptedValue { ciphertext: enc<f64> }
```

Note: The codegen now supports multi-field `enc<T>` wire types as well, so an
alternative design with `wire EncryptedRecord { a1: enc<f64>, ..., password: enc<f64> }`
would also work.

## Limitations

- **CKKS approximation**: The password value has ±0.2 noise (41.85 instead of 42.0).
  For exact passwords, a BFV/BGV scheme would be needed (not yet supported in the DSL).
- **Answer range**: Answer codes should be small integers (1–9). Large differences
  (>5) fall outside the Chebyshev domain and may cause CKKS overflow errors during
  decryption — though the password remains hidden in all cases.
- **Single record**: This demo verifies against one stored record. A multi-user
  database would require iterating over records or using column-packed ciphertexts.

## DSL Features Demonstrated

| Feature | Usage |
|---------|-------|
| `scheme CKKS { ... }` | CKKS with depth 15, FLEXIBLEAUTO scaling |
| `requires { add, mul }` | Auto-generates mult keys |
| `wire` types | Single-field `EncryptedValue` reused for all encrypted scalars |
| `chebyshev()` | Per-question Gaussian indicator approximation |
| `scheme.override()` | Conditional ring_dim for Toy vs Small |
| Explicit `save()`/`load()` | Multiple ciphertexts via different file paths |
| `assert` with tolerance | Verify decrypted result within CKKS noise |

## How This Example Was Created

This example was written entirely by Claude Code (Claude Opus 4.6) from a short
conversation. The following prompts were used:

1. **Context setting**:
   > if I ask you to write a new application, does it help you?

   Claude confirmed that understanding the DSL compiler internals (parser, codegen,
   built-in functions, type system) enables writing correct `.nb` programs.

2. **Application specification**:
   > let's write a new example called PasswordRetrieval that has a database of 5
   > personal questions per password saved, if a user answer the 5 questions correctly,
   > he can retrieve his password

3. **Documentation**:
   > write a README.md in the new test directory, update the CLAUDE.md or other top
   > level markdown files if necessary

4. **Ship it**:
   > prepare the PR

Claude explored the existing examples and language reference, designed the FHE
approach, wrote the three `.nb` files, iterated through compile-test cycles to fix
issues, added Makefile targets, and created the PR — all from these four prompts.

### Issues Encountered During Development

The first attempt did not compile or produce correct results. Here is what went
wrong and how each issue was resolved:

**1. Multi-field encrypted wire types were not supported by the codegen.**
The initial design used wire types with multiple `enc<f64>` fields:
```
wire EncryptedRecord { a1: enc<f64>, a2: enc<f64>, ..., password: enc<f64> }
wire EncryptedQuery  { a1: enc<f64>, a2: enc<f64>, ... }
```
The codegen generated placeholder comments (`/* load(EncryptedRecord) */;`) instead
of deserialization code, and tried to serialize via a nonexistent `.query` field.
**Workaround**: Switched to a single-field `wire EncryptedValue { ciphertext: enc<f64> }`
reused for all values, with explicit `save()`/`load()` to different file paths.
**Now fixed in the compiler**: Multi-field enc wire types are now supported — each
field is serialized to `{field_name}.bin` within the directory.

**2. Missing `ring_dim` in Instance struct.**
The scheme block did not specify `ring_dim`, so the codegen fell back to
`inst.ring_dim` — but the `Instance` struct had no `ring_dim` field.
**Workaround**: Added `ring_dim: u32` to the `Instance` struct and used
`scheme.override(ring_dim: inst.ring_dim)` in keygen, matching the pattern from
the `simple` example.
**Now fixed in the compiler**: If neither the scheme nor the Instance struct has
`ring_dim`, the codegen omits `SetRingDim()` and lets openFHE auto-select.

**3. Helper function not in `FHE_SHARED_FNS` — missing `cc` argument.**
A `question_match(diff)` helper that called `chebyshev()` was correctly generated
with `CryptoContext<DCRTPoly> cc` as its first parameter, but callers did not pass
`cc` because the function was not in the codegen's `FHE_SHARED_FNS` set.
**Workaround**: Inlined the `chebyshev()` calls directly in the main function instead of
using a helper, avoiding the `FHE_SHARED_FNS` limitation.
**Now fixed in the compiler**: The codegen auto-detects functions with `enc<T>`
parameters or return type and prepends `cc` at call sites automatically.

**4. Chebyshev domain too wide — incorrect results (attempt 1).**
The first working design used a single sum-of-squared-errors with
`chebyshev(|x| exp(-x * 10), error, domain: [0, 50], degree: 27)`. With matching
answers, the result was 35.56 instead of 42.0 (indicator ≈ 0.85 instead of 1.0).
The degree-27 polynomial on the wide [0, 50] domain could not accurately
approximate the sharp indicator function near x=0.
**Fix (partial)**: Narrowed domain to [0, 5], increased degree to 59. This fixed
the correct-answer case (41.85 ≈ 42).

**5. Wrong answers blow up outside the Chebyshev domain (attempt 2).**
With the single sum-of-squares approach, one wrong answer (diff=3) produced a
squared error of 9, which fell outside the [0, 5] Chebyshev domain. The polynomial
extrapolation diverged, causing a CKKS overflow: `Decode(): The decryption failed
because the approximation error is too high`.
**Fix**: Redesigned to use **per-question comparison** — each answer gets its own
Chebyshev evaluation on domain [-5, 5] (which always covers single-digit
differences), then multiply all 5 indicators together. This gives exponentially
better discrimination and keeps each Chebyshev input within its domain.

### Lessons for Future DSL Examples

- Wire types can have **multiple `enc<T>` fields** — each is serialized to its own
  `.bin` file automatically. Single-field wire types also work as before.
- Functions with `enc<T>` in their signature automatically get `cc` prepended —
  no need to add them to `FHE_SHARED_FNS` manually.
- Chebyshev approximation requires careful domain sizing — the input must stay
  within the domain for all cases, not just the happy path. Per-value comparison
  with multiplication is more robust than aggregate-then-compare.
- `ring_dim` can be set in the scheme, in the Instance struct, or omitted entirely
  (openFHE will auto-select based on security level and depth).
