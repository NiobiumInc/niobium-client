# DSL for FHE: Design Rationale and Compiler Guide

## Quick Start

```bash
cd dsl_fhe
make test-compiler          # Run all unit tests (76 tests)
make examples               # Compile all examples: DSL -> C++ -> binaries
make simple                 # Build just the simple example
make fetch-by-similarity    # Build the fetch-by-similarity example
make fhe-network-monitor    # Build the KitNET anomaly detection example
make ml-inference           # Build the ML inference example
make set-membership         # Build the private name-matching example
```

## Documentation Map

| File | Purpose |
|---|---|
| `CLAUDE.md` | This file — design rationale, codegen internals, known pitfalls |
| `HOWTO.md` | Step-by-step guide for adding a new example |
| `NB_LANGUAGE.md` | Language reference — types, syntax, built-in functions, patterns |
| `GRAMMAR.md` | Formal EBNF grammar |
| `README.md` | User-facing overview, build instructions, example walkthrough |

## Why a Domain-Specific Language for FHE?

### The Problem with Current FHE Programming

Writing FHE applications in C++ with libraries like openFHE requires dealing with
extensive boilerplate that obscures the actual computation. A typical FHE application
spends ~80% of its code on plumbing:

- **Parameter setup**: 30+ lines of `CCParams` configuration with magic numbers
- **Key generation**: Must manually match `EvalMultKeyGen`, `EvalAtIndexKeyGen`, etc.
  to operations used hundreds of lines later
- **Serialization**: 5+ separate binary files, two different serialization APIs
  (`Serial::SerializeToFile` vs `cc->SerializeEvalMultKey`), manual error handling
- **Niobium hardware instrumentation**: `#ifdef NIOBIUM_COMPILER` blocks scattered
  throughout with precise ordering requirements
- **Trust boundary enforcement**: Client/server separation enforced only by convention

### Code Reduction

| Metric | C++ (hand-written) | DSL |
|---|---|---|
| fetch-by-similarity | ~2,900 lines / 13 files | ~590 lines / 3 files |
| `#ifdef NIOBIUM_COMPILER` blocks | 38 | 0 (auto-generated) |
| Manual serialization calls | 25+ | 0 (auto-generated) |
| Duplicate function implementations | 10+ | 0 (shared definitions) |

## Core Design Principles

1. **Trust boundaries are language constructs.** `@client` / `@server` compile to
   separate binaries. The compiler rejects server code that references `SecretKey`.

2. **Declare intent, not mechanism.** `requires { add, mul, rotate }` replaces manual
   key generation. `@hardware(cache_key: [...])` replaces 120 lines of record/replay.

3. **Encryption state is part of the type system.** `enc<f64>` vs `f64` makes the
   boundary explicit. The compiler tracks multiplicative depth.

4. **Wire types enforce serialization boundaries.** `wire CryptoParams { ... }` defines
   what crosses the client-server boundary. The compiler generates all serialization.

## Compilation Pipeline

```
.nb files  ->  Lexer  ->  Parser  ->  Semantic  ->  Codegen  ->  C++ files
                                                                      |
                                                               CMake + make
                                                                      |
                                                              Standalone binaries
```

### What Gets Generated

For each example, the codegen produces:

| File | Content |
|---|---|
| `nb_shared.h` | Header with includes, enums, structs, wire types, function declarations |
| `nb_shared.cpp` | Shared function implementations (helpers, dispatch, etc.) |
| `<stage>.cpp` | One per `@stage` — function impl + `main()` + serialization |
| `CMakeLists.txt` | Auto-generated build file (finds openFHE via directory walk) |

The CMakeLists.txt auto-detects `NIOBIUM_CLIENT_ROOT` by walking up from
`nb_out/build/` looking for `vendor/niobium-fhetch/include/niobium/compiler.h`.
It then links the Niobium-instrumented OpenFHE (`vendor/lib/openfhe`), the
FHETCH client library `libnbfhetch` (`vendor/niobium-fhetch`), and — for
`@hardware` server stages — `libniobium_client_autofacade` (whole-archive) plus
`yaml-cpp`. No hardcoded paths.

### Target: the Niobium *client* API (libnbfhetch), not the compiler

This DSL targets the open-source **niobium-client** record/replay stack
(`niobium::compiler()` from `libnbfhetch`), not the proprietary compiler's
`libnbcc`. The two differ in ways the codegen must respect:

| Concern | Compiler API (old) | Client API (this codegen) |
|---|---|---|
| Build gating | `#ifdef NIOBIUM_COMPILER` everywhere | none — instrumentation always compiled |
| Key registration | `global_key_cache()` + `cached_key()` | deserialize hooks (`tag_keys` via auto-facade) |
| Input capture | auto-discovered by the compiler | deserialize hooks (`tag_input` via auto-facade) |
| Replay target | `replay(Target::FUNC_SIM/FPGA1/...)` | `replay()` (no argument; FHETCH simulator) |
| `--niobium_hw` / `--target` flags | yes | removed |

The codegen uses **cooperative auto-tagging**: each `@hardware` server stage
calls `niobium::compiler().enable_auto_tagging()` right after `init()`, then the
instrumented-OpenFHE deserialize hooks (in `cryptocontext-ser.h` /
`ciphertext-ser.h`, active because the generated CMake defines
`OPENFHE_CPROBES`) capture the crypto context, tag the eval keys, and tag each
input ciphertext as it is deserialized — at deterministic points that keep
record-time and replay-time FHETCH addresses aligned. The host code still owns
the lifecycle: `start()` (after the input `load()`s), `probe()`, `stop()`,
`replay()`, `result()`.

Cooperative replay spawns the `fhetch_driver` helper (built with
niobium-fhetch). Set `NBCC_FHETCH_DRIVER` to its path (the Makefile does this);
otherwise it must be on `PATH`.

## Compiler Module Map

| Module | Purpose |
|---|---|
| `xcomp/lexer.py` | Tokenizer — keywords, operators, literals |
| `xcomp/parser.py` | Recursive-descent parser -> AST |
| `xcomp/ast_nodes.py` | All AST node dataclass definitions |
| `xcomp/nb_types.py` | Type system (TypeKind, NbType, enc depth tracking) |
| `xcomp/semantic.py` | Semantic analysis — domain enforcement, depth tracking |
| `xcomp/codegen.py` | C++ code generation targeting openFHE |
| `xcomp/nbc.py` | CLI driver: `parse`, `check`, `compile` |

## Codegen Internals — Key Mappings

### How FHE Operators Compile

```
enc + enc    ->  NullSafeEvalAdd(cc, a, b)    // null-safe for accumulator init
enc + scalar ->  cc->EvalAdd(a, scalar)        // direct, no null check needed
enc - enc    ->  cc->EvalSub(a, b)
enc * enc    ->  cc->EvalMult(a, b)
enc * scalar ->  cc->EvalMult(a, scalar)
enc *_norelin enc -> cc->EvalMultNoRelin(a, b)
```

`NullSafeEvalAdd` is a generated helper that returns `b` when `a` is null, enabling
the pattern `let acc: enc<T> = zero(); acc = acc + value`.

### How `cc` Is Threaded

Functions that operate on encrypted data need `CryptoContext<DCRTPoly> cc`. The codegen
auto-detects this by checking whether the function has `enc<T>` parameters or return type
(`_fn_uses_fhe()`). Functions detected this way automatically get `cc` prepended as the
first argument at both definition and call sites.

For functions that use FHE internally but don't have `enc<T>` in their signature, a
hardcoded `FHE_SHARED_FNS` set provides a fallback:

```python
FHE_SHARED_FNS = {
    "mat_vec_mult", "mat_vec_mult_single", "compact_and_extract",
    "extract_payload", "total_sums", "dispatch", "large_add_mul",
    "kitnet_ckks", "autoencoder_forward", "anomaly_detector_forward",
}
```

Most new shared functions with `enc<T>` in their signature do **not** need to be added
to this set — auto-detection handles them. Only add functions that use FHE operations
without any `enc<T>` in their signature.

Built-in FHE operations (`rotate`, `negate`, etc.) use `cc->` directly in their
codegen handlers and don't need to be in `FHE_SHARED_FNS`.

### Encrypted-Variable Detection: Structural Flow with a Warned Fallback

`_is_encrypted_expr()` decides whether an expression is a ciphertext — which
drives critical codegen choices (`NullSafeEvalAdd` vs `cc->EvalAdd`, whether to
`const_pointer_cast`, etc.). Classification is **structural first**:

- let-binding flow (`_enc_vars`/`_plain_vars` via `_record_let_enc_state`):
  explicit `enc<T>` annotations, builtin return kinds from the unified
  registry (`builtins_registry.py`), user-function declared return types
  (`_fn_sigs`), and initializer flow;
- loop elements take the iterable's element state (ranges → plain,
  encrypted collections → encrypted, `replicate`/`enumerate` → (plain, elem));
- combinator closure params (`map`/`zip_map`/`reduce`) take the collection's
  element state; `chebyshev` closure params are plain doubles;
- wire-field accesses (`w.field`) resolve from the wire declaration
  (`_wire_vars` records `let w = load(Wire, ...)`);
- destructured tuples from user fns with declared tuple returns record each
  position.

Only when none of that resolves does it fall back to the **name heuristic**
(`ENCRYPTED_PREFIXES` / `ENCRYPTED_EXACT_NAMES`) — and every fallback is
reported as a per-variable warning by `nbc compile`
("encrypted-ness of 'x' ... decided by the variable-name heuristic"). All six
shipped examples compile with **zero** fallbacks. When you see the warning,
add an annotation (`let x: enc<...> = ...`) or rename a plaintext variable —
don't rely on the heuristic for new code. `test_enc_flow_beats_name_heuristic`
and friends in `tests/test_codegen.py` pin the behavior.

### How `encrypt()` Compiles

The encrypt function generates different code depending on the data argument:

- **Array literal** `encrypt(pk, [value_a])` -> `std::vector<double>{value_a}`
- **Variable** `encrypt(pk, row)` -> `std::vector<double>(row.begin(), row.end())`
  (iterator construction handles type conversion, e.g. float -> double)

### How Wire Type Serialization Works

The codegen has type-specific handlers for each wire type:

| Wire Type | Serialization Strategy |
|---|---|
| `CryptoParams` | 4 files: `cc.bin`, `pk.bin` (file-based), `mk.bin`, `rk.bin` (stream-based) |
| Single-ciphertext wire | Direct `SerializeToFile` of the ciphertext field |
| `vec<enc<T>>` wire field | Per-element files: `features_0.bin`, `features_1.bin`, ... |
| `EncryptedDB` | Batch directories with `row_NNNN.bin` / `payload_NNNN.bin` |
| Indexed wire `T[id]` | Per-index files in a subdirectory: `0.bin`, `1.bin`, ... |

For `load()`, the codegen looks up the wire definition to find the ciphertext field name.
For `save()`, it extracts the field from a struct literal.

**Important**: `load_secret_key()` auto-loads the CryptoContext from the same directory
(OpenFHE requires cc to be registered before deserializing keys). `save_secret_key()`
auto-creates parent directories.

### How Stages Become Binaries

Each `@stage("name")` function generates a `.cpp` with:

1. **Function body** — the translated DSL function
2. **`main()`** with:
   - Instance size as first positional arg (integer -> enum cast)
   - Additional positional args for non-bool params (in declaration order)
   - `--flag_name` for bool params
   - `--hollow` for `@hardware` server stages (hollow recording)
3. **Niobium init** (`@hardware`) — `init()`, `enable_auto_tagging()`,
   `cache_parameters()`, `set_program_info()`/`set_build_info()`
4. **Key loading** (server stages) — cc, pk, eval keys from disk (the
   deserialize hooks auto-capture the context and tag the keys)
5. **Record/replay gate** (`@hardware`) — `main()` branches on
   `is_cache_valid()`: on a **record** run the stage function executes (with
   `start()` emitted inside it, after the leading input `load()`s so the hooks
   tag inputs first), then `probe()`/`stop()`; on a **cache-valid** run zero FHE
   ops execute — `replay()` runs the cached trace and `result()` reconstructs
   the output (mirrors the canonical fetch-by-similarity client integration)
6. **Result serialization** — always: OpenFHE's own output on a record run,
   the reconstructed ciphertext on a replay run

### How Match Expressions Compile

Match expressions compile to C++ `switch` statements with an IIFE wrapper when used
as expressions:

```nb
match op { ADD => ct1 + ct2, MUL => ct1 * ct2 }
```

becomes:

```cpp
switch (op) {
    case ADD: { return NullSafeEvalAdd(cc, ct1, ct2); break; }
    case MUL: { return cc->EvalMult(ct1, ct2); break; }
    default: __builtin_unreachable();
}
```

Multi-statement match arms generate proper `return` on the last expression.
Let-rebinding within arms uses assignment (not re-declaration) via `_declared_vars` tracking.

## Known Pitfalls and Past Bugs

These were discovered during end-to-end testing and are now fixed, but document the
fragile areas of the codegen:

### 1. Multiplicative Depth Leaking Across Match Arms

**Problem**: `nb_types.py:common_type()` returned the same mutable `NbType` object for
both arms of a match, so depth accumulated across arms (arm1 depth + arm2 depth).

**Fix**: `common_type()` returns `copy.copy(a)` for encrypted types.

### 2. NullSafeEvalAdd with Scalar Operands

**Problem**: `ct + imm` (cipher + scalar) generated `NullSafeEvalAdd(cc, ct, imm)`,
but `NullSafeEvalAdd` only accepts two `Ciphertext<DCRTPoly>` arguments.

**Fix**: Use `NullSafeEvalAdd` only when both operands are encrypted. When one is a
plain scalar, use `cc->EvalAdd(ct, scalar)` directly.

### 3. OpenFHE Requires CryptoContext Before Deserialization

**Problem**: `load_secret_key()` deserialized the sk without loading the CryptoContext
first, causing segfaults in OpenFHE.

**Fix**: `_gen_load_secret_key()` now auto-loads `cc.bin` from the same directory before
deserializing the secret key.

### 4. Directory Creation Ordering

**Problem**: `save_secret_key()` was called inside the function body before `main()`
created the output directory, so the write failed silently.

**Fix**: Both `_gen_save_secret_key()` and `_gen_save()` now call
`fs::create_directories()` before writing.

### 5. Array Literal vs Variable in encrypt()

**Problem**: `encrypt(pk, [value])` generated `std::vector<double>({value}.begin(), ...)`
which fails because you can't call `.begin()` on a brace-init-list. But
`encrypt(pk, row)` where `row` is a `vector<float>` needs iterator construction for
type conversion.

**Fix**: Check if the data argument is an `ast.ArrayLiteral`. If yes, use
`std::vector<double>{value}`. If no, use `.begin()/.end()` iterators.

### 6. Wire Type Field Name Mismatch

**Problem**: Hardcoded `er.result` in EncryptedResult loader, but the wire type might
have a `ciphertext` field instead.

**Fix**: Look up the actual field name from the wire definition at codegen time.

### 7. Tuple Return Type Interferes with Array Literals

**Problem**: In functions with tuple return types (e.g. `-> (vec<enc<T>>, vec<enc<T>>)`),
ALL 2-element array literals were converted to `std::make_pair(...)` — including ones
used as domain arguments in `chebyshev(..., domain: [SIGMOID_LO, SIGMOID_HI])`.

**Fix**: Only apply tuple conversion when `_in_return_expr` flag is set (i.e., in a
`return` statement's expression), not for arbitrary array literals.

### 8. Scheme `first_mod` Not Stripping `bits` Suffix

**Problem**: `first_mod: 57 bits` generated `parameters.SetFirstModSize(57 bits)` instead
of `parameters.SetFirstModSize(57)`. The `precision` field already handled this correctly.

**Fix**: Apply the same `str(raw).split()[0]` pattern used for `precision`.

### 9. ConstCiphertext from External Functions

**Problem**: External C++ functions (via `extern_call`) may return `ConstCiphertext<DCRTPoly>`
(a `shared_ptr<const ...>`), but wire type fields expect `Ciphertext<DCRTPoly>` (non-const).
Assigning the return value to a struct literal field caused a C++ type error.

**Fix**: When building a struct literal for a wire type, the codegen now auto-inserts
`std::const_pointer_cast<CiphertextImpl<DCRTPoly>>()` for `enc<T>` fields.

### 10. Static Library Cross-References in CMake

**Problem**: When external static libraries reference symbols from each other (e.g.,
`mlp_function_split_0` uses weight vectors from `vector_constants`), the linker fails
with undefined references because it processes libraries in order and discards symbols
not yet needed.

**Fix**: The generated CMakeLists.txt lists external libraries twice on the link line,
giving the linker a second pass to resolve cross-references.

## File Structure

```
dsl_fhe/
  CLAUDE.md                          # This file
  NB_LANGUAGE.md                     # Language reference
  GRAMMAR.md                         # Formal grammar (EBNF)
  README.md                          # User-facing docs
  Makefile                           # Build orchestration
  xcomp/                             # Cross-compiler
    nbc.py                           # CLI: parse, check, compile
    lexer.py, parser.py              # Frontend
    ast_nodes.py, nb_types.py        # Type system
    semantic.py                      # Analysis
    codegen.py                       # C++ codegen
    tests/                           # Unit tests (76 tests)
  examples/
    fetch-by-similarity/             # Full pipeline: DB search (compilable, runnable)
      shared.nb, client.nb, server.nb
      nb_out/                        # Generated C++ + build
    simple/                          # Basic cipher operations (compilable, runnable)
      shared.nb, client.nb, server.nb
      nb_out/                        # Generated C++ + build
    fhe-NetworkMonitor/              # KitNET anomaly detection (compilable, runnable)
      shared.nb, client.nb, server.nb
      nb_out/                        # Generated C++ + build
    ml-inference-fhe/                # MNIST MLP inference (compilable, runnable)
      shared.nb, client.nb, server.nb
      nb_out/                        # Generated C++ + build
      HOWTO.md                       # Example-specific implementation guide
    password-retrieval/              # FHE password retrieval via security questions
      shared.nb, client.nb, server.nb
      nb_out/                        # Generated C++ + build
      README.md                      # Design rationale and usage guide
    set-membership/                  # Private name matching (exact + Soundex fuzzy)
      shared.nb, client.nb, server.nb
      harness/encode_names.py        # Plaintext name encoding -> dataset.bin/query.bin
      nb_out/                        # Generated C++ + build
```

## Adding a New Example

See [HOWTO.md](HOWTO.md) for a comprehensive step-by-step guide.

Quick summary:
1. Create `examples/<name>/shared.nb`, `client.nb`, `server.nb`
2. Add Makefile targets (build + test)
3. Run `make <name>` to compile DSL -> C++ -> binaries
4. If the server uses external C++ code, use `extern ... from` and `extern_call()`
5. Shared functions with `enc<T>` params/return automatically get `cc` prepended
6. Wire types with `enc<T>` fields get automatic serialization (single or multi-field)
7. Test end-to-end: `./key_generation 0 && ./encrypt 0 && ./compute 0 && ./decrypt 0`

## Adding a New Built-in Function

1. Declare it once in `xcomp/builtins_registry.py` — return kind (`enc`,
   `plain`, a concrete scalar, or `unknown`), plus `vector_return` /
   `depth_opaque` flags. Both the semantic analyzer and codegen derive their
   classification tables from this single registry.
2. Add the codegen handler in `codegen.py:_gen_call_expr_impl()` under the
   `fname == "..."` checks.
3. If it's a shared function needing `cc` but has no `enc<T>` in its
   signature, add to `FHE_SHARED_FNS`.
4. Update `NB_LANGUAGE.md`'s built-in functions table.
