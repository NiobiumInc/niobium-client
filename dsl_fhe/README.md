# Niobium DSL for FHE

A domain-specific language and cross-compiler for writing Fully Homomorphic Encryption
applications. The DSL compiles `.niob` source files to C++ with openFHE, producing
self-contained binaries for a client-server FHE pipeline.

## Motivation

### The Boilerplate Problem

Writing FHE applications in C++ with openFHE requires extensive boilerplate — parameter
setup, key management, serialization, trust boundary enforcement, and Niobium hardware
instrumentation — that obscures the actual computation. Analysis of the
`fetch-by-similarity` benchmark (13 .cpp files, ~2,900 lines) found dozens of
hardware-instrumentation blocks, 25+ manual serialization calls, and logic
duplicated across multiple server variants.

The DSL replaces all of that with ~590 lines of `.niob` source that compiles to equivalent
C++ (~1,650 generated lines) producing functionally identical results.

### Enabling Agentic AI for FHE Development

The DSL is designed to be written by both humans and AI coding agents (e.g. Claude Code,
Copilot, Cursor). Raw openFHE C++ is hostile to AI-assisted development for several
reasons:

1. **Context window cost.** A single FHE application in C++ can span 13+ files and
   thousands of lines, most of which is plumbing. An AI agent must read and hold all of
   this context before it can make a meaningful change — burning tokens on serialization
   boilerplate and `#ifdef` blocks instead of the actual algorithm.

2. **Implicit contracts.** The C++ code has critical invariants that are nowhere in the
   source: "the server binary must never link against the secret key", "rotation keys
   must be generated for exactly the indices used 500 lines later", "eval keys use
   stream-based serialization but ciphertexts use file-based". An AI agent that violates
   these produces code that compiles but silently fails at runtime.

3. **Copy-paste divergence.** When the same logic exists in multiple places (e.g.
   alternate server variants, multiple `read_keys()` implementations), an AI agent
   editing one copy has no reliable way to find and update the others.

4. **Error surface.** Generating correct openFHE C++ requires getting dozens of
   interacting details right simultaneously — parameter types, serialization API choice,
   Niobium instrumentation protocol ordering, null-safe accumulation. Each is a potential
   silent failure that only manifests after a multi-minute FHE computation.

The DSL eliminates these problems by making the intent explicit and the mechanism
generated:

| Problem | C++ | DSL |
|---|---|---|
| Trust boundaries | Convention (programmer discipline) | `@client` / `@server` (compiler-enforced) |
| Key generation | Manual, must match operations used | `requires { add, mul, rotate }` |
| Serialization | 25+ manual calls, two different APIs | `wire` types (auto-generated) |
| Hardware instrumentation | 38 `#ifdef` blocks | `@hardware(cache_key: [...])` |
| Shared logic | Copy-pasted across files | Single definition, used everywhere |
| Context for AI | ~2,900 lines across 13 files | ~590 lines across 3 files |

An AI agent working with the DSL can read the entire application in a single context
window, make changes at the algorithmic level, and rely on the compiler to generate
correct plumbing. The type system (`enc<f64>` vs `f64>`, `wire` types, domain
annotations) gives the agent — and its tool chain — the same guardrails that a human
expert would enforce during code review.

## Architecture

```
.niob source files
       |
       v
  +----------+     +--------+     +----------+     +---------+
  |  Lexer   | --> | Parser | --> | Semantic | --> | Codegen |
  | lexer.py |     |parser.py|    |semantic.py|   |codegen.py|
  +----------+     +--------+     +----------+     +---------+
                                                       |
                                                       v
                                               C++ with openFHE
                                               (nb_shared.h/.cpp
                                                + per-stage .cpp)
                                                       |
                                                       v
                                                CMake + g++/clang
                                                       |
                                                       v
                                              Standalone binaries
```

### Compiler Modules

| Module | Lines | Purpose |
|---|---|---|
| `xcomp/lexer.py` | 470 | Tokenizer for `.niob` syntax |
| `xcomp/parser.py` | 1,061 | Recursive-descent parser producing AST |
| `xcomp/ast_nodes.py` | 462 | AST node definitions (dataclasses) |
| `xcomp/nb_types.py` | 208 | Type system definitions |
| `xcomp/semantic.py` | 549 | Semantic analysis and validation |
| `xcomp/codegen.py` | 3,104 | C++ code generation targeting openFHE |
| `xcomp/nbc.py` | 229 | CLI driver (`parse`, `check`, `compile`) |

### Key Language Features

- **Trust domains**: `@client` and `@server` annotations compile to separate binaries.
  Server code cannot access `SecretKey` or call `decrypt()`.
- **Stage functions**: Each `@stage("name")` function becomes a standalone binary with
  CLI argument parsing, serialization, and (for server stages) Niobium hardware
  instrumentation.
- **Wire types**: `wire CryptoParams { ... }` defines serialization boundaries. The
  compiler generates all file I/O code — including openFHE's two different serialization
  APIs (stream-based for eval keys, file-based for everything else).
- **Scheme declarations**: `scheme CKKS { ... }` with `requires { add, mul, rotate, ... }`
  replaces manual `CCParams` setup and key generation.
- **FHE operations**: `+`, `*`, `*_norelin`, `rotate()`, `chebyshev()`, `slot_sum()`,
  `running_sums()` map directly to openFHE calls with automatic null-safe accumulation
  and relinearization.

## Directory Structure

```
dsl_fhe/
  README.md                            # This file
  CLAUDE.md                            # Design rationale and codegen internals
  HOWTO.md                             # Step-by-step guide for adding new examples
  NB_LANGUAGE.md                       # Language reference (types, syntax, built-ins)
  GRAMMAR.md                           # Formal EBNF grammar
  Makefile                             # Build orchestration
  xcomp/                               # Cross-compiler implementation
    nbc.py                             # CLI: parse, check, compile
    lexer.py                           # Tokenizer
    parser.py                          # Recursive-descent parser
    ast_nodes.py                       # AST node definitions
    nb_types.py                        # Type system
    semantic.py                        # Semantic analysis
    codegen.py                         # C++ code generator
    errors.py                          # Error reporting
    tests/                             # Unit tests per module
  examples/
    fetch-by-similarity/               # Full pipeline: DB similarity search (compilable)
      shared.niob                        # Types, constants, wire formats (129 lines)
      client.niob                        # Client operations: keygen, encrypt, decrypt (177 lines)
      server.niob                        # Server computation: mat-vec, threshold, extract (285 lines)
      harness/run.py                   # Pipeline orchestration script
      nb_out/                          # Generated output (+ manual build files)
    fhe-NetworkMonitor/                # KitNET anomaly detection (compilable, runnable)
      shared.niob                        # Model structs, constants, wire types (131 lines)
      client.niob                        # Keygen, feature-column encryption, decryption (89 lines)
      server.niob                        # Autoencoder ensemble + anomaly detector (188 lines)
      nb_out/                          # Generated C++ + build
    ml-inference-fhe/                  # MNIST MLP inference (compilable, runnable)
      shared.niob                        # Layer dims, instance sizes, wire types (96 lines)
      client.niob                        # Keygen, per-image encryption, decrypt+argmax (108 lines)
      server.niob                        # MLP inference via extern_call (78 lines)
      HOWTO.md                         # Example-specific implementation guide
      nb_out/                          # Generated C++ + build
    password-retrieval/                # FHE password retrieval via security questions
      shared.niob                        # Wire types, instance sizes (73 lines)
      client.niob                        # Keygen, setup record, submit query, decrypt (131 lines)
      server.niob                        # Per-question Chebyshev match + password gate (84 lines)
      README.md                        # Design rationale and usage guide
      nb_out/                          # Generated C++ + build
    set-membership/                    # Private name matching (compilable, runnable)
      shared.niob                        # Profiles (exact/soundex), wire types
      client.niob                        # Keygen, per-position query encryption, decrypt+threshold
      server.niob                        # Squared-distance + iterated-squaring indicator
      harness/encode_names.py          # Plaintext name encoding (exact + Soundex)
      nb_out/                          # Generated C++ + build
    fraud-flag/                        # Private card-number checking (skill-eval dogfood)
      shared.niob, client.niob, server.niob  # 8-stage design walkthrough in README.md
      harness/encode_cards.py          # Plaintext ground truth (5,000-card list)
      nb_out/                          # Generated C++ + build
    simple/                            # Basic cipher operations (compilable, runnable)
      shared.niob                        # Operation enum (25 ops), instance sizes
      client.niob                        # Encrypt two scalars, decrypt+verify
      server.niob                        # Operation dispatch via match
      nb_out/                          # Generated C++ + build
```

## Build and Run

### Prerequisites

- The niobium-client stack built (run `make build` at the repo root). This
  provides the Niobium-instrumented OpenFHE (`vendor/lib/openfhe`), the FHETCH
  client library `libnbfhetch`, `libniobium_client_autofacade`, the
  `fhetch_driver` helper, and the `fhetch_sim` simulator.
- Python 3 with numpy (for dataset generation and verification)

The generated binaries record an unoptimized `.fhetch` instruction trace and
replay it locally through the FHETCH simulator. The whole flow runs against the
open-source niobium-client (`libnbfhetch`) — no proprietary components needed.

### Build

```bash
cd dsl_fhe

# Run compiler tests (76 tests across lexer, parser, semantic, codegen)
make test-compiler

# Compile all examples and run end-to-end tests
make examples

# Or build/test individually
make simple                 # Build simple example (4 binaries)
make fetch-by-similarity    # Build fetch-by-similarity (8 binaries)
make fhe-network-monitor    # Build KitNET anomaly detection (4 binaries)
make ml-inference           # Build ML inference (4 binaries)
make password-retrieval     # Build password retrieval (5 binaries)
make set-membership         # Build private name matching (4 binaries)
make fraud-flag             # Build private card-number checking (4 + ref binaries)
make test-simple            # End-to-end: 8 FHE operations verified
make test-fetch             # End-to-end: toy fetch pipeline
make test-nid               # End-to-end: toy KitNET (keygen+encrypt+inference)
make test-ml                # End-to-end: keygen+encrypt+compute (single)
make test-password          # End-to-end: correct + wrong answer verification
make test-set-membership    # End-to-end: exact match, no-match, Soundex fuzzy
make test-fraud             # End-to-end: flagged/clean card + reference pipeline
make test-examples          # Run all end-to-end tests
```

Each example runs three phases:
1. **DSL compilation** (`nbc.py compile`): Parses `.niob` files and generates C++ in `nb_out/`.
2. **C++ compilation** (CMake + make): Builds binaries in `nb_out/build/`.
3. **End-to-end testing**: Runs the binaries to verify correctness.

### Build a standalone app (outside the repo)

The `make <example>` targets above are for examples that live inside
`dsl_fhe/examples/`. You can also build a DSL app in **your own project
directory**: `nbc.py compile` accepts arbitrary input paths and an `--outdir`,
and the generated `nb_out/` is a self-contained CMake project
(`project(nb_generated)`).

**Prerequisites:** a niobium-client checkout that has been **built** (`make
release` at the repo root, or `make build-release`) — the generated project
links against its `vendor/lib/openfhe` and `build/` artifacts (`libnbfhetch`,
`libniobium_client_autofacade`, `yaml-cpp`) — plus Python 3 and a C++17 compiler
with CMake ≥ 3.14.

```bash
# Run from your project dir, alongside your shared.niob / client.niob / server.niob.
NBROOT=/path/to/niobium-client          # a *built* checkout

# 1. Compile the DSL to a self-contained C++ project in ./nb_out
python3 "$NBROOT/dsl_fhe/xcomp/nbc.py" compile \
    shared.niob client.niob server.niob --outdir nb_out

# 2. Configure + build. -DNIOBIUM_CLIENT_ROOT is required outside the repo tree
#    (nbc's auto-detect only locates the root when nb_out sits inside it).
cmake -S nb_out -B nb_out/build -DNIOBIUM_CLIENT_ROOT="$NBROOT"
cmake --build nb_out/build

# 3. Run the stage binaries in order (which binaries exist depends on your
#    stages). For the `simple` design — key_generation, encrypt, compute, decrypt:
cd nb_out/build
./key_generation 0 3
./encrypt 0 3.0 5.0
./compute 0 0 0.0 --no-ring-dim-check   # ADD: 3 + 5
./decrypt_verify 0 8.0                   # asserts the decrypted result == 8
```

No extra `LD_LIBRARY_PATH` / `DYLD_LIBRARY_PATH` is needed at runtime — the build
embeds an rpath to the client's OpenFHE / FHETCH / yaml-cpp libraries.

For ground-truth checking, `nbc` also emits a `<stage>_ref` binary for each
*twinnable* stage that runs the same logic in plaintext (coverage is per-stage —
a server `@hardware` compute stage has no twin, so not every pipeline has a
complete `_ref` run).

To **contribute** an example back to this repo instead, add it under
`dsl_fhe/examples/<name>/` with Makefile build/test targets — see
[`HOWTO.md`](HOWTO.md).

### Run the Pipeline

```bash
cd examples/fetch-by-similarity

# Full pipeline (payload extraction mode)
python3 harness/run.py 0 --seed 42

# Count-only mode (faster, just counts matches)
python3 harness/run.py 0 --seed 42 --count_only

# Skip expensive steps when iterating (reuse existing keys/encrypted data)
python3 harness/run.py 0 --seed 42 --skip-data --skip-keys --skip-encrypt
```

Instance sizes: `0`=toy (2K ring, 1K records), `1`=small, `2`=medium, `3`=large, `4`=toy_large_ring.

### Pipeline Stages

The harness executes the pipeline stages in order:

| # | Binary | Domain | Purpose |
|---|---|---|---|
| 1 | `key_generation` | client | Generate CKKS context, keys, rotation indices |
| 2 | `encode_encrypt_db` | client | Read cleartext DB, transpose, batch, encrypt |
| 3 | `encode_encrypt_query` | client | Read query vector, tile to slots, encrypt |
| 4 | `encrypted_compute` | server | Mat-vec product, threshold, running sums, payload extraction (monolithic) |
| 5 | `decrypt_decode` | client | Decrypt result ciphertext to slot vector |
| 6 | `postprocess` | client | Decode payloads from slot layout (no crypto) |

Stages communicate via files in `io/<instance>/`:
- `keys/` — crypto context, public key, eval keys
- `encrypted/` — encrypted DB (batch directories), query, result
- `raw-result.bin` — decrypted slot vector
- `results.bin` — final decoded payloads

### Clean

```bash
make clean    # Removes generated C++ and build artifacts, keeps CMakeLists.txt and utils.h
```

## Example: DSL vs Generated C++

### DSL (server.niob)

```
@server @stage("encrypted_compute")
@hardware(cache_key: ["workload_size"])
fn encrypted_compute(inst: Instance, count_only: bool = false)
    -> reads(CryptoParams, EncryptedDB, EncryptedQuery),
       writes(EncryptedResult)
{
    let params = load(CryptoParams, from: keydir(inst))
    let db = load(EncryptedDB, from: encdir(inst))
    let eqry = load(EncryptedQuery, from: encdir(inst)).query

    let result = mat_vec_mult(inst, eqry, db)

    let (degree, outscale) = if count_only { (247, 1.0) } else { (59, 0.504) }
    result = for ct in result {
        chebyshev(|x| sigmoid(x - THRESHOLD, outscale), ct,
                  domain: [-1.0, 1.0], degree: degree)
    }

    if count_only {
        return EncryptedResult {
            result: reduce(+, result) |> slot_sum(n_slots(inst))
        }
    }

    return EncryptedResult {
        result: compact_and_extract(inst, db, result)
    }
}
```

### Generated C++ (encrypted_compute.cpp, excerpt)

```cpp
auto encrypted_compute(CryptoContext<DCRTPoly> cc, Instance inst, bool count_only) {
  auto params = CryptoParams{};
  auto db = [&]() { EncryptedDB edb; /* deserialization from batch dirs */ return edb; }();
  auto eqry = [&]() { /* deserialize eqry.bin */ }().query;
  auto result = mat_vec_mult(cc, inst, eqry, db);

  auto degree = (count_only) ? 247 : 59;
  auto outscale = (count_only) ? 1.0 : 0.504;
  result = [&]() { std::vector<Ciphertext<DCRTPoly>> _result;
    for (auto& ct : result) {
      _result.push_back(cc->EvalChebyshevFunction(
        [&](auto x) { return sigmoid((x - THRESHOLD), outscale); },
        ct, (-1.0), 1.0, degree));
    } return _result; }();

  if (count_only) {
    return EncryptedResult{.result = cc->EvalSum(/* reduce + sum */)};
  }
  return EncryptedResult{.result = compact_and_extract(cc, inst, db, result)};
}
```

The generated code also includes:
- A `main()` with CLI argument parsing and option flags
- For `@hardware` server stages: `niobium::compiler()` record/replay against the
  FHETCH client library — `init()` + `enable_auto_tagging()`, a `start()`
  bracket around the computation, `probe()`/`stop()`, then `replay()` +
  `result()`. Inputs, keys, and the crypto context are tagged automatically by
  the instrumented-OpenFHE deserialize hooks (cooperative auto-tagging).
- Result serialization (the result is rehydrated from the FHETCH simulator)

## Code Generation Details

The codegen (`xcomp/codegen.py`) handles several non-trivial translations:

### Wire Type Serialization

Each wire type has custom serialization logic:
- **CryptoParams**: Four files (`cc.bin`, `pk.bin`, `mk.bin`, `rk.bin`) using both
  `Serial::SerializeToFile` and stream-based `cc->SerializeEvalMultKey`
- **EncryptedDB**: Batch directories (`batch0000/`, `batch0001/`, ...) with individual
  `row_NNNN.bin` and `payload_NNNN.bin` files
- **Indexed wire `T[id]`**: per-index files in a subdirectory (`0.bin`, `1.bin`, ...)

### Null-Safe Accumulation

FHE accumulators start as null ciphertexts. The codegen generates a `NullSafeEvalAdd`
helper that returns `b` when `a` is null, avoiding crashes in accumulation loops.

### 2D Matrix Slicing

The expression `matrix[i..j, col]` (extract column `col` from rows `i` to `j`) generates
a lambda that iterates the row range and collects the column elements, since C++ has no
native 2D slice syntax.

### Scheme Override

`scheme.override(security: not_set, ring_dim: 2048)` for toy instances generates a
dynamic `_sec_level` variable that's conditionally set before crypto context creation.

### Scaling Precision

The `scale()` pipe operator always produces `double` output regardless of input type,
preventing integer truncation when scaling int16 payload values by fractional factors.

## Support Libraries

The fetch-by-similarity example uses three external C++ helpers
(`slot_replication`, `running_sums`, and `utils.h` timer utilities). These are
**vendored locally** under `examples/fetch-by-similarity/support/` (relicensed
under the client's Apache-2.0 header) and staged into `nb_out/` by the Makefile
at build time — the example has no dependency on any external/private repo.

`CMakeLists.txt` is auto-generated by the codegen. It auto-detects
`NIOBIUM_CLIENT_ROOT` by walking up the directory tree looking for
`vendor/niobium-fhetch/include/niobium/compiler.h`, then links the instrumented
OpenFHE, `libnbfhetch`, and (for `@hardware` server stages)
`libniobium_client_autofacade` + `yaml-cpp`.

## Verification

The pipeline is verified against a cleartext reference implementation:

```
$ python3 harness/run.py 0 --seed 42

=== Fetch-by-similarity: toy (size=0) ===
--- Step 1: Generate dataset ---            OK   (0.1s)
--- Step 2: Generate query ---              OK   (0.1s)
--- Step 3: Key generation ---              OK   (0.7s)
--- Step 4: Encrypt database ---            OK   (1.2s)
--- Step 5: Encrypt query ---               OK   (0.1s)
--- Step 6: Encrypted computation ---       OK   (12.9s)
--- Step 7: Decrypt ---                     OK   (0.0s)
--- Step 8: Postprocess ---                 OK   (0.0s)
--- Step 9: Verify ---
  [harness] PASS (All 8 payload vectors match)
```

Both payload-extraction mode and count-only mode produce correct results matching the
original hand-written C++ submission.

## Additional Examples

### `simple/` — Basic Cipher Operations

A minimal test harness of elementary CKKS cipher operations.
Encrypts two scalar values, applies one of 25 operations, decrypts and verifies.

Operations include cipher-cipher arithmetic (ADD, MUL, MUL_MUL, ADD_MUL, ...),
rotation-based (ROTATE_ADD, ROTATE_MUL, ...), immediate/cipher-plain (ADDI, MULI, ...),
mixed combinations, loops (LARGE_ADD_MUL), and low-level (MUL_MONOMIAL).

The server dispatches via a `match` expression on the `Operation` enum — replacing the
~350-line if-else chain and ~25 separate C++ functions in the original.

**DSL (server.niob excerpt):**
```
fn dispatch(op: Operation, ct1: enc<f64>, ct2: enc<f64>, imm: f64) -> enc<f64> {
    match op {
        ADD         => ct1 + ct2,
        MUL         => ct1 * ct2,
        MUL_MUL     => (ct1 * ct2) * ct1,
        ADDI        => ct1 + imm,
        ROTATE_ADD  => rotate(ct1, int(imm)) + ct2,
        MAT_PATTERN => (ct1 + ct2) * (ct1 - ct2),
        ...
    }
}
```

### `password-retrieval/` — FHE Password Retrieval

Encrypted password retrieval using security questions. A user's record stores 5
encrypted answer codes and an encrypted password. The server verifies the answers
homomorphically using Chebyshev-approximated Gaussian indicators — if all 5 match,
the password is revealed; otherwise the result is near-zero.

Uses **per-question comparison**: each answer difference gets its own Chebyshev
evaluation (`exp(-(x/0.3)²/2)`, degree 59), then all 5 indicators are multiplied
together for exponentially better discrimination than a single aggregate comparison.

- Single-slot packing: one value per ciphertext, 11 ciphertexts total
- Correct answers → password (41.85 ≈ 42), wrong answers → ~0 (-0.002)
- Demonstrates explicit `save()`/`load()` with different file paths for the same wire type

```bash
cd dsl_fhe && make test-password
# Runs: key_generation 0 && setup_record 0 && submit_query 0 && verify 0 && decrypt_result 0
# Then: submit_query 0 7 3 9 1 8 (wrong) && verify 0 && decrypt_result 0 0.0
```

### `set-membership/` — Private Name Matching

Privacy-preserving set membership (a port of the `openfhe-set-membership`
workload): given a client's private name and a server's private dataset of
names, determine whether the name appears in the dataset — without revealing
the query to the server or the dataset to the client.

Names are encoded outside the circuit (`harness/encode_names.py`) as
fixed-length integer vectors (a–z → 1..26, zero-padded) — raw characters in
**Exact** mode (L=20) or **Soundex** phonetic hashes in fuzzy mode (L=4, so
"Robbrt" matches "Robert"). The CKKS circuit computes, per SIMD slot (one
dataset name per slot):

1. squared Euclidean distance to the query, per character position — 1 level
2. normalize by C = 26²·L and complement: `t = 1 − S/C` — 1 level
3. **iterated squaring** `t^(2^K)`: matches stay ≈ 1, non-matches decay to ≈ 0 — K levels
4. slot-sum: the aggregate score ≈ the number of matching names

The client decrypts the score and thresholds at 0.5. Depth = 2 + K (K=16
exact, K=14 Soundex), chosen so the worst non-match indicator falls below 0.01.

- One ciphertext per character position, the query character replicated in all slots
- Dataset stays plaintext on the server (column-major packing), folded in via `ct − plaintext_vector`
- Demonstrates: per-instance `scheme.override(depth:)`, ct−vector ops, `slot_sum`, multi-batch accumulation

```bash
cd dsl_fhe && make test-set-membership
# exact:   'James Smith'   in dataset  → score ≈ 1
# exact:   'Zelda Quixote' not present → score ≈ 0 (7e-15)
# soundex: 'Robbrt Johnson' fuzzy      → score ≈ 10 (all 'Robert *' collide)
```

### `fhe-NetworkMonitor/` — KitNET Anomaly Detection

Encrypted network anomaly detection using the KitNET architecture: an ensemble of
autoencoders compute reconstruction error per feature group, then an anomaly detector
computes the final MSE score. Uses Chebyshev-approximated sigmoid and tanh activations.

- Each feature column is encrypted as a separate ciphertext (slots = packets)
- Autoencoder forward pass: `hidden = sigmoid(W^T * input + hbias)`, `recon = sigmoid(W * hidden + rbias)`
- Anomaly detector: same architecture with tanh, outputs `MSE = sum((residual - recon)^2) / vis_dim`
- Binary model loader auto-generated from `KitNETModel` struct definition
- Tuple return types (`-> (vec<enc<T>>, vec<enc<T>>)`) for autoencoder hidden+recon

> **Self-contained build (stub assets).** The trained KitNET model and Mirai
> dataset are not vendored in this open-source client. By default the example
> generates **stub** assets (zeros, minimal valid dims) so it builds and runs
> end-to-end (keygen → encrypt → encrypted inference) without any external repo,
> but the anomaly scores are **not** meaningful. For real detection, obtain the
> submission and build with
> `make fhe-network-monitor SUBMISSION_REPO=/path/to/submission-repo`. See
> `examples/fhe-NetworkMonitor/assets/README.md`.

**Pipeline stages:**

| # | Binary | Domain | Purpose |
|---|---|---|---|
| 1 | `keygen` | client | Generate CKKS context + keys |
| 2 | `encrypt` | client | Encrypt each feature column as a ciphertext |
| 3 | `inference` | server | Run KitNET ensemble + anomaly detector |
| 4 | `decrypt` | client | Decrypt anomaly scores |

```bash
cd examples/fhe-NetworkMonitor/nb_out/build
./keygen 0 && ./encrypt 0 && ./inference 0 && ./decrypt 0
# Profile: 0=Toy, 1=Mini, 2=Full
# Note: decrypt on Toy profile fails (noise budget too small for Chebyshev depth)
```

### `ml-inference-fhe/` — MNIST MLP Inference

Encrypted 2-layer MLP inference on MNIST digits (784 -> 512 -> 10) using
rotate-and-multiply matrix-vector products (HEIR v2 model). The model is linked
as an external library and called via `extern_call("mlp", ct)`, with weights
loaded at runtime through a local DSL bridge (`mlp_bridge.cpp`).

> **Self-contained build (stub model).** The real HEIR-generated model
> (`mlp_openfhe.cpp`, ~28K lines) and trained weights are not vendored in this
> open-source client. By default the example builds against a **stub** model +
> **stub zero-weights** so it compiles and runs end-to-end without any external
> repo — but the output is **not** real inference. For real results, obtain the
> ml-inference submission and build with
> `make ml-inference SUBMISSION_REPO=/path/to/submission-repo`. See
> `examples/ml-inference-fhe/data/README.md`.

- Client encrypts each image into one ciphertext, tiled to fill all 1024 slots
- Server runs the MLP forward pass on each encrypted image
- Client decrypts and takes argmax over the 10 output logits
- CKKS with ring_dim=2048, depth=8, rotation indices 1..1023

**New DSL features demonstrated**: `extern_call()`, `str()`,
indexed wire types (`EncryptedInput[*]`), `argmax()` with tuple destructuring,
`load_matrix<f32>()` with text format detection, `LOCAL_SRC_DIR` bridge pattern.

**Pipeline stages:**

| # | Binary | Domain | Purpose |
|---|---|---|---|
| 1 | `key_generation` | client | Generate CKKS context + keys + rotation keys (1..1023) |
| 2 | `encode_encrypt_input` | client | Load MNIST pixels, pad to 1024 slots, encrypt per image |
| 3 | `encrypted_compute` | server | Run MLP inference via external `mlp()` function |
| 4 | `decrypt_decode` | client | Decrypt, argmax over 10 logits, output predictions |

```bash
cd dsl_fhe && make test-ml
# Runs: key_generation 0 && encode_encrypt_input 0 && encrypted_compute 0 0
```
