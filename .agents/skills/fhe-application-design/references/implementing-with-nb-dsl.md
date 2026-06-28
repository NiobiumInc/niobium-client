# Implementing the Design in the `nb` FHE DSL

This reference covers Stage 7 **Track A**: turning the design produced by
Stages 1–6 into a working application using the `nb` domain-specific language
and its `nbc` cross-compiler, which live in the
[niobium-client](https://github.com/NiobiumInc/niobium-client) repository under
`dsl_fhe/`. The DSL compiles `.niob` source to OpenFHE C++ and generates
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
| Who encrypts / packing legality (1, 5) | Expressed in *which stage does the encrypting*, and **compiler-checked**: annotate independent-encryptor stages `@encryptors(independent)` and `encrypt()` of a column slice spanning records is a compile error (cross-owner SIMD packing). Single encryptor (default) → one `@client` stage packs records across slots, column-major |
| Scheme selection (4) | `scheme CKKS { ... }` block. **The DSL is CKKS-only today** — a BFV/BGV design must use Track B |
| Depth budget (5, 6) | `depth:` in the scheme block; per-instance via `scheme.override(depth: inst.depth)`. The compiler **errors** when the statically tracked multiplication chain exceeds the budget (a sound lower bound) and warns on heavy over-provisioning. `chebyshev` with a literal/const `degree:` is a **modeled subcircuit**: it charges `ceil(log2(degree+1)) + 1` levels into the tracker (59 → 7, 119 → 8, …); non-literal degrees stay depth-opaque |
| Security ↔ parameters frontier (6) | Compile-time advisor: `nbc check` emits a `note:` computing `logQ ≈ first_mod + depth × q_i` and the minimum ring dimension for the declared security level (HE-standard tables), flagging declared `ring_dim`s below target (warning if no `scheme.override(security:)` dev profile covers them). Use it to trade q_i / depth / approximation degree against N |
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
   The compiler reinforces it: for every stage free of non-twinnable
   constructs (`extern_call`, `replicate`, `running_sums`, `load_model`), it
   ALSO generates a `<stage>_ref` **cleartext reference twin** — the same
   `.niob` circuit with plaintext semantics (`enc<T>` → slot vectors, FHE ops →
   elementwise arithmetic, `chebyshev` → the true function). Run the `_ref`
   pipeline alongside the encrypted one; the difference between the two
   outputs is exactly the approximation + noise error.

2. **Write three files** in `dsl_fhe/examples/<name>/`:
   - `shared.niob` — `Instance` struct (per-profile `ring_dim`, depth, sizes),
     directory-layout helpers, `wire` types.
   - `client.niob` — `scheme` block, `requires`, then `@client` stages:
     key generation, input encryption, decrypt-and-verify.
   - `server.niob` — `@server @stage(...) @hardware(cache_key: [...])` compute
     stage(s) plus shared circuit functions.

3. **Add Makefile targets** (`<name>` and `test-<name>`) following
   `HOWTO.md` — compile (`nbc.py compile`), build (CMake), then run the stage
   binaries end-to-end with verification. Wire into `examples`,
   `test-examples`, `test-compiler`, and `clean`.

4. **Iterate with the compiler's feedback**: `make test-compiler` runs
   parse/semantic checks over all examples; the end-to-end target verifies
   numerics. A new example should reach `0 warnings, 0 errors` on
   `nbc.py check`.

5. **ML workloads: ground truth first, then sweep.** Require a full plaintext
   model implementation and a representative test set (Stage 3 — firm, not
   advisory). Then run the security-vs-accuracy sweep: vary (Chebyshev
   `degree:`, `precision:` (q_i), `depth:`, `ring_dim`) and measure
   end-to-end model accuracy at each point. The compiler's params note shows
   where each configuration sits on the security frontier; the generated
   `_ref` reference twins give each sweep point's accuracy (vs. the true
   nonlinearities) without running encryption; the encrypted pipeline
   confirms the final choice.

## Worked design↔implementation pairs

All three of this skill's worked examples have complete, tested DSL
implementations — read the design reference and the DSL code side by side:

| Design reference | DSL implementation (`dsl_fhe/examples/`) |
|---|---|
| `example-set-membership.md` | `set-membership/` — exact + Soundex profiles, squared distance + iterated squaring, ~180 lines of `.niob` |
| `example-fetch-by-similarity.md` | `fetch-by-similarity/` — Chebyshev threshold, slot replication, running sums, payload extraction |
| `example-network-intrusion-detection.md` | `fhe-NetworkMonitor/` — autoencoder ensemble, Chebyshev sigmoid/tanh, feature-major packing |

## Pitfalls specific to the DSL (current state)

1. **Encrypted-ness is fully structural — names carry no meaning.** The
   compiler classifies ciphertexts from annotations, builtin/user-fn return
   types, let-binding flow, loop elements, combinator-closure params, wire
   fields, and destructured tuples. There is no name heuristic: a variable
   the flow cannot resolve is treated as plaintext, and if it was actually a
   ciphertext the generated C++ fails to compile (never silently wrong). The
   fix is always an annotation (`let x: enc<vec<f64>> = ...`).

2. **Wire layouts are field-type-driven — names carry no special meaning.**
   Every wire serializes the same way: `enc<T>` → `{field}.bin`,
   `vec<enc<T>>` → `{field}_<i>.bin`, `vec<vec<enc<T>>>` →
   `batchNNNN/{field}_NNNN.bin`; a wire carrying a `CryptoContext` field (any
   name) gets the canonical `cc/pk/mk/rk` key layout; a `from:`/`to:` path
   naming a `.bin` file addresses a single-enc-field wire directly. Name your
   wires whatever fits the domain.

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
