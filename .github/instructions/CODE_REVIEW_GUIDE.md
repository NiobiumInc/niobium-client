# Code Review Guide — Niobium Client

This guide defines how automated and manual PR reviews should be conducted for the `niobium-client` repository. Reviews must be actionable, concise, and focused on correctness and security.

> **Scope reminder**: This is an FHE *client* — not a compiler. It generates CKKS crypto contexts and keys, encrypts plaintexts, and serializes ciphertexts/keys to disk for consumption by the Niobium hardware accelerator. All code here is FHE application code, so circuit correctness and parameter security apply everywhere.

> **Automated review scope**: Automated reviews perform static file analysis only (Read, Write, Edit tools). Build validation (`make`, `make examples`) and runtime correctness require manual review or dedicated CI workflows.

---

## Project Context

- **What it is**: FHE client-side application code that generates CKKS crypto contexts, key pairs, and ciphertexts to be consumed by the Niobium hardware accelerator (via `niobium-fhetch`).
- **Stack**: C++17, openFHE, CMake.
- **Key subsystems**: Examples (`examples/`), FHETCH vendor library (`vendor/niobium-fhetch`).

---

## 1. Required Review Output

Every review must produce these sections in order:

1. **Summary** (2–4 lines): what changed and why, as inferred from the diff.
2. **Blockers** (must-fix): incorrect crypto parameters, secret key exposure, serialization bugs, broken build.
3. **Risks / Watch-outs**: areas likely to regress silently — insecure parameter choices, incorrect key load order, ciphertext level exhaustion.
4. **Non-blocking suggestions**: code duplication between examples, unnecessary I/O, clarity.
5. **Questions for the author**: missing context, unclear parameter choices, expected behavior.

Label every issue: **Blocker / High / Medium / Low**.

---

## 2. PR Risk Classification

### Blocker — must not merge
- `SetSecurityLevel` set to `HEStd_NotSet` in non-example/non-test code without explicit justification
- Secret key (`secretKey` / `sk.bin`) serialized or exposed in an unintended path
- CKKS parameters that produce an insecure or broken scheme (ring dimension < 2048 without explicit security justification, modulus sizes that violate standard bounds)
- Serialization format change that breaks compatibility with existing key/ciphertext files without a migration path
- Build broken in Debug or Release mode

### High Risk — deep review required
- Changes to `vendor/niobium-fhetch` (submodule bump or direct edits)
- Changes to `CMakeLists.txt` or `Makefile` (build system, OpenFHE dependency, flags)
- New or changed CKKS parameter sets (`CCParams` configuration)
- Changes to key generation flow (`KeyGen`, `EvalMultKeyGen`, `EvalRotateKeyGen`)
- Changes to serialization/deserialization of any crypto material (contexts, keys, ciphertexts)

### Medium Risk — targeted review
- New examples or changes to existing examples
- Changes to plaintext packing logic or slot layout

### Low Risk — light review
- Docs, comments, README updates
- Small isolated refactors with no behavior change

---

## 3. Core Correctness Checklist

### 3.1 CKKS Parameter Security
This is the highest-priority check for any change touching `CCParams` configuration.

- **Security level**: `HEStd_NotSet` disables parameter validation — only acceptable in explicitly marked development/test code. Flag any use in production paths.
- **Ring dimension**: Values below 2048 are insecure for any real workload. Flag ring dims < 4096 unless the justification is clear.
- **Modulus sizes**: `ScalingModSize` and `FirstModSize` affect noise and security. Changes must not weaken the security margin versus standard CKKS recommendations.
- **Multiplicative depth**: Must match the actual circuit depth in the corresponding server code. Mismatches cause decryption failures that are silent at encryption time.
- **Scaling technique**: Changes to `ScalingTechnique` (e.g., `FIXEDMANUAL` → `FLEXIBLEAUTO`) affect noise behavior across the whole circuit — flag any change.

### 3.2 Key Management
- **Secret key serialization**: `secretKey` / `sk.bin` must only be written when explicitly required (e.g., for the decrypt binary). Flag any unintended serialization to shared or public directories.
- **Key load order**: Public key must be available before encrypting; eval keys (mult, rotation) must be generated before the server attempts to use them. Mismatches cause silent failures at the server.
- **Rotation key indices**: `EvalRotateKeyGen` indices must match the rotation offsets actually used in the server/compiler. Missing indices produce incorrect results without error.
- **Key file paths**: Hardcoded paths must use `std::filesystem` safely. No path traversal from user input.

### 3.3 Ciphertext Correctness
- **Slot packing**: The number of slots packed into a ciphertext must match what the server expects. Review the comment or usage context to confirm alignment.
- **Level budget**: The multiplicative depth set in `CCParams` is the level budget. If the server circuit consumes more levels than the client allocated, decryption produces garbage.
- **Plaintext type**: `MakeCKKSPackedPlaintext` is correct for CKKS. Flag use of non-CKKS plaintext constructors.
- **Ciphertext file names**: File names (e.g., `ct_a.bin`, `ct_b.bin`) must match what `fhetch_driver` or server code expects. Mismatches cause silent load failures.

### 3.4 Code and Data Structure Reuse
- Does the new example duplicate boilerplate (param setup, key gen, serialization) that could be shared with an existing example? Flag and suggest a shared helper if the duplication is significant.
- Are two examples using structurally identical `CCParams` blocks? Confirm the parameters are intentionally different before accepting.
- Does the new code reimplement a utility already available in openFHE or the C++17 STL?

### 3.5 C++17 Memory Safety
- No raw owning pointers — use `std::unique_ptr` or `std::shared_ptr`.
- No manual `new`/`delete` unless wrapping a C API.
- RAII for all file handles (`std::ofstream`/`std::ifstream` — close explicitly or use scope).
- No unguarded array or vector accesses — validate indices before use.
- No `strcpy`/`sprintf` without bounds — use `std::string` or bounded alternatives.

### 3.6 File I/O and Paths
- Does the change write files to a directory that may not exist? `std::filesystem::create_directories` must be called before any write.
- Are file open results checked? An unchecked `std::ofstream` that fails silently produces a corrupted or missing key file.
- Is a binary stream opened without `std::ios::binary`? OpenFHE serialization requires binary mode — text mode causes corruption on Windows and subtle bugs elsewhere.
- Are file paths constructed from user input? Validate or sanitize to prevent path traversal.

### 3.7 Build System
- Does the change require OpenFHE to be rebuilt? Flag this explicitly in the review.
- Are Debug and Release builds both correct? Some bugs only appear in Release (optimizations, `NDEBUG`).
- Are new source files added to the correct `CMakeLists.txt`?
- Are new dependencies introduced? They must be documented and available in CI.
- Does a submodule bump to `vendor/niobium-fhetch` change the OpenFHE version transitively? Flag if so.

### 3.8 FHE Circuit Correctness
> Unlike the compiler repo, this section applies to **all code** in this repository.

- **Relinearization**: Every ciphertext multiplication must be followed by relinearization before further multiplications (`EvalMultNoRelin` requires explicit relinearization; `EvalMult` with eval keys handles it automatically — confirm which is used).
- **Level consumption**: Operations that consume levels (multiplication, modswitch, relinearization) must be tracked. The client's `MultiplicativeDepth` must account for all levels consumed by the server circuit.
- **Noise budget**: Does the parameter set leave sufficient noise margin for the circuit depth? Deeper circuits or many rotations require larger ring dimensions.
- **Rotation key coverage**: All rotation offsets used in the server must have corresponding keys generated by the client.

---

## 4. Comment Style

- **Blocker**: `[Blocker] <problem> — <impact> — <fix>`
- **High**: `[High] <problem> — <impact> — <suggested fix>`
- **Medium / Low**: one line is enough unless a fix is non-obvious.
- Prefer proposing a minimal patch over describing the problem abstractly.
- Do not flag style issues unless they affect readability or create ambiguity.
