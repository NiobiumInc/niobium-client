# Implementing the Design in the `nb` FHE DSL

This reference covers Stage 7 **Track A**: turning the design produced by
Stages 1–6 into a working application using the `nb` domain-specific language
and its `nbc` cross-compiler, which live in the
[niobium-client](https://github.com/NiobiumInc/niobium-client) repository under
`dsl_fhe/`. The DSL compiles `.nb` source to OpenFHE C++ and generates
everything the raw-C++ track builds by hand: the four-program architecture,
serialization, CMake, key generation matched to the operations used, and
Niobium record/replay instrumentation.

Authoritative language docs (read as needed; paths relative to `dsl_fhe/`):

| File | Content |
|---|---|
| `NB_LANGUAGE.md` | Language reference — types, syntax, built-in functions |
| `GRAMMAR.md` | Formal EBNF grammar |
| `HOWTO.md` | Step-by-step guide for adding a new example (Makefile targets, testing) |
| `CLAUDE.md` | Codegen internals and known pitfalls |

## How design outputs map to DSL constructs

Every artifact Stages 1–6 produce has a direct DSL counterpart. Use this table
to transcribe the design:

| Design output (stage) | DSL construct |
|---|---|
| Parties and trust boundaries (1) | `@client` / `@server` function annotations — compiled to separate binaries; server code referencing `SecretKey` or calling `decrypt()` is a **compile error** |
| What crosses the wire (1, 8) | `wire` type declarations — the compiler generates all serialization; a stage's `reads(...)`/`writes(...)` clauses are a machine-checked message-flow spec |
| Who encrypts / packing legality (1, 5) | Expressed in *which stage does the encrypting*. Single encryptor → one `@client` stage packs records across slots. Independent encryptors → each owner's data is its own ciphertext; never pack across owners (the DSL does not yet check this — enforce it by design review) |
| Scheme selection (4) | `scheme CKKS { ... }` block. **The DSL is CKKS-only today** — a BFV/BGV design must use Track B |
| Depth budget (5, 6) | `depth:` in the scheme block; per-instance via `scheme.override(depth: inst.depth)`. The compiler **errors** when the statically tracked multiplication chain exceeds the budget (a sound lower bound), and warns when the budget is heavily over-provisioned — the warning stays silent when depth is statically opaque (loops over encrypted ops, `chebyshev`, `*_norelin`, combinators, `extern_call`) |
| Ring dimension / slots (6) | `ring_dim` field on the `Instance` struct (applied automatically); `n_slots = ring_dim / 2` for CKKS |
| Security level (6) | `security:` in the scheme block; `scheme.override(security: not_set)` for toy/dev profiles |
| Scaling / first modulus (6) | `precision:` (scaling mod size) and `first_mod:` in the scheme block |
| Rotation keys (6) | `requires { add, mul, rotate, ... }` — keygen is generated to match; `slot_sum` uses EvalSum keys which are always generated |
| Column-major packing (5) | Load a row-major matrix with `load_matrix<f64>(path, cols)`, slice columns with `m[a..b, col]`, and `encrypt(pk, column)` — one ciphertext per feature/position |
| Comparison circuits (5) | Squared distance + iterated squaring: `diff * diff` accumulation + a squaring loop. Chebyshev: `chebyshev(|x| f(x), ct, domain: [lo, hi], degree: d)` |
| Slot aggregation (5) | `ct \|> slot_sum(n_slots)` (EvalSum, no depth cost) |
| Deferred relinearization (5) | `a *_norelin b` accumulation followed by `relin(acc)` |
| Four-program architecture (7) | One `@stage("name")` function per program — keygen / encrypt / server-compute / decrypt each becomes a standalone binary with CLI parsing, serialization, and (for `@server @hardware` stages) record/replay instrumentation |
| run_test (7) | A `test-<example>` target in `dsl_fhe/Makefile` that runs the stage binaries in order and verifies against the plaintext reference |
| Protocol spec (8) | Largely self-documenting: domains + wire types + `reads`/`writes` state the message flow; write the threat-model prose in the example's README |

## Workflow

1. **Plaintext preprocessing stays outside the DSL.** Anything string-shaped or
   data-wrangling (name encoding, Soundex, dataset generation, padding,
   computing the expected reference output) belongs in a small Python harness
   (`examples/<name>/harness/`). The DSL sees only fixed-length numeric
   matrices/vectors, loaded with `load_matrix`/`load_vec`. The harness should
   also print/compute the **expected plaintext result** so the decrypt stage
   can verify against it — this is the Stage 3 "ground truth" discipline.

2. **Write three files** in `dsl_fhe/examples/<name>/`:
   - `shared.nb` — `Instance` struct (per-profile `ring_dim`, depth, sizes),
     directory-layout helpers, `wire` types.
   - `client.nb` — `scheme` block, `requires`, then `@client` stages:
     key generation, input encryption, decrypt-and-verify.
   - `server.nb` — `@server @stage(...) @hardware(cache_key: [...])` compute
     stage(s) plus shared circuit functions.

3. **Add Makefile targets** (`<name>` and `test-<name>`) following
   `HOWTO.md` — compile (`nbc.py compile`), build (CMake), then run the stage
   binaries end-to-end with verification. Wire into `examples`,
   `test-examples`, `test-compiler`, and `clean`.

4. **Iterate with the compiler's feedback**: `make test-compiler` runs
   parse/semantic checks over all examples; the end-to-end target verifies
   numerics. A new example should reach `0 warnings, 0 errors` on
   `nbc.py check`.

## Worked design↔implementation pairs

All three of this skill's worked examples have complete, tested DSL
implementations — read the design reference and the DSL code side by side:

| Design reference | DSL implementation (`dsl_fhe/examples/`) |
|---|---|
| `example-set-membership.md` | `set-membership/` — exact + Soundex profiles, squared distance + iterated squaring, ~180 lines of `.nb` |
| `example-fetch-by-similarity.md` | `fetch-by-similarity/` — Chebyshev threshold, slot replication, running sums, payload extraction |
| `example-network-intrusion-detection.md` | `fhe-NetworkMonitor/` — autoencoder ensemble, Chebyshev sigmoid/tanh, feature-major packing |

## Pitfalls specific to the DSL (current state)

1. **Heed the heuristic-fallback warning.** Encrypted-ness is tracked
   structurally (annotations, builtin/user-fn return types, let-binding flow,
   loop elements, combinator-closure params, wire fields, destructured
   tuples) — all shipped examples compile with zero name-heuristic fallbacks.
   If `nbc compile` warns that a variable's "encrypted-ness ... was decided by
   the variable-name heuristic", don't ignore it: add an annotation
   (`let x: enc<vec<f64>> = ...`) or rename the variable if it is plaintext.

2. **Some wire-type names are special-cased.** `CryptoParams`,
   `EncryptedResult`, `EncryptedQuery`, and `EncryptedDB` get bespoke
   serialization layouts. Reuse `CryptoParams` and `EncryptedResult` (they fit
   most designs); for anything else prefer a **fresh name** (e.g.
   `EncryptedName`) to get the predictable generic layout (single-ct field →
   one file; `vec<enc<T>>` field → `field_<i>.bin` per element).

3. **One static `scheme` block per application.** Per-instance variation goes
   through `Instance` fields + `scheme.override(depth: ...)` /
   `scheme.override(security: not_set)` inside the keygen stage.

4. **Loops with runtime bounds defeat static depth tracking.** An iterated
   squaring loop `for r in 0..inst.k { t = t * t }` has data-dependent depth;
   the compiler's depth warnings are best-effort. Keep the Stage 5 depth
   budget arithmetic in a comment next to the `Instance` definition.

5. **`@hardware` cache keys gate record/replay.** A recorded trace is reused
   when the `cache_key` values match; if inputs change under the same key,
   delete the recorded program directory (`*_workload_*`,
   `fhetch_driver_source_*`) to force a fresh record.

## What still requires Track B (raw OpenFHE)

- **BFV / BGV** — the codegen targets CKKS only.
- **Transciphering** (Stage 5 output-integrity dual output) — no DSL
  construct yet; implementable via `extern_call` to hand-written C++ if the
  rest of the app fits the DSL.
- **Threshold / multi-party keys**, **bootstrapping**, **interactive
  multi-round protocols** — not expressible; the DSL's model is
  one-pass client → server → client.
- **Encryption-model enforcement** — the single-vs-independent-encryptor rule
  (Stage 1/5) is not yet compiler-checked; a planned `@encryptors(...)`
  annotation will lint cross-owner packing. Until then it is a design-review
  obligation.
