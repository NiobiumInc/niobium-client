# Niobium Client

Open-source client stack for the **Niobium Mistic** FHE accelerator. Whatever
front-end you enter through, the path is the same: your fully-homomorphic
computation is recorded as an unoptimized **FHETCH Polynomial IR** trace
(`.fhetch`), which you can replay through the bundled local simulator for
validation, or submit to the Niobium compilation service for optimization and
deployment to hardware. All optimization logic lives server-side — this client
stays thin, open (Apache 2.0), and self-contained.

## Choose your entry point

There are four ways in, by audience:

| You are… | Entry point | Start here |
|---|---|---|
| **An AI coding agent** (or pairing with one) | **nb DSL + design skill** — an 8-stage FHE design methodology that auto-loads for Claude Code, OpenAI Codex, and other agentskills.io-compatible agents, paired with a compact DSL whose compiler generates all the plumbing | [`dsl_fhe/`](dsl_fhe/README.md), [`.claude/skills/`](.claude/skills/fhe-application-design) & [`.agents/skills/`](.agents/skills/fhe-application-design) |
| **An application developer** with OpenFHE C++ | **Instrumented OpenFHE** — write standard `EvalMult`/`EvalAdd`/… code, bracket it with `niobium::compiler()` calls; probes record everything | [Instrumenting an OpenFHE application](#entry-point-2--openfhe-for-application-developers), [`examples/`](examples/) |
| **A compiler / code-generator author** | **FHETCH Polynomial IR** — emit the IR directly through the recording API (or the text trace format) and use the session, replay, and transport machinery as your backend | [`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch), [`src/fhetch_transport/`](src/fhetch_transport/) |
| **An FHE library integrator** (GPU/accelerator back-ends) | **HAZE** — a CUDA-shaped C API (`hazeMalloc`/`hazeMemcpy`/`hazeNTT`/…): each call records one polynomial-level IR op, so CUDA-targeting FHE libraries port with minimal effort | [`vendor/niobium-haze`](https://github.com/NiobiumInc/niobium-haze) |

All four converge on the same recorder and the same trace:

```
     AI agents               End users              FHE compilers          FHE libraries
  (Claude Code +          (OpenFHE C++              (emit Polynomial      (CUDA-shaped code,
   design skill)           applications)             IR directly)          e.g. FIDESlib)
        |                        |                        |                      |
        v                        |                        |                      v
 +---------------+               |                        |               +---------------+
 |  nb DSL       |               |                        |               |  HAZE         |
 |  dsl_fhe/     |--generates--->|                        |               |  libhaze      |
 |  (nbc)        |  OpenFHE C++  |                        |               |  hazeAdd,     |
 +---------------+               v                        |               |  hazeNTT, ... |
                    +---------------------+               |               +---------------+
                    |  Niobium-           |               |                      |
                    |  instrumented       |               |                      |
                    |  OpenFHE (probes.h) |               |                      |
                    +---------------------+               |                      |
                                 | openfhe_cprobe_*       | fhetch_api.h         | one IR op
                                 | fires on every         | (sr_addp,            | per haze
                                 | NTT, ADD, MUL, ...     |  sr_ntt, ...)        | call
                                 v                        v                      v
                    +----------------------------------------------------------------+
                    |        libnbfhetch  —  FHETCH Polynomial IR recorder           |
                    |  niobium::compiler() session API: init / start / probe / stop  |
                    |  cooperative auto-tagging  ·  cache  ·  replay  ·  result      |
                    +----------------------------------------------------------------+
                                 |
                                 |  unoptimized .fhetch trace
                                 |  + fhetch_replay.json manifest
                                 v
                 +---------------+--------------------+
                 |                                    |
                 v                                    v
     +-----------------------+          +--------------------------+
     |  fhetch_sim (local)   |          |  Niobium compilation     |
     |  replays the trace,   |          |  service (proprietary)   |
     |  reconstructs result  |          |  optimizes and deploys   |
     |  ciphertexts — for    |          |  to Mistic hardware      |
     |  validation           |          |                          |
     +-----------------------+          +--------------------------+
```

For the FHETCH instruction set, session API, trace format, and simulator
internals, see the companion repository:
[`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch).

---

## Entry point 1 — DSL + design skill, for AI agents

The combination is designed so an AI coding agent can take an application from
*privacy model* to *verified encrypted pipeline* in one session:

- **The design skill** (vendored from the
  [`niobium-skills`](https://github.com/NiobiumInc/niobium-skills)
  catalog into both [`.claude/skills/fhe-application-design`](.claude/skills/fhe-application-design)
  and [`.agents/skills/fhe-application-design`](.agents/skills/fhe-application-design))
  auto-loads in this repository for Claude Code (from `.claude/skills/`), OpenAI
  Codex, and other agentskills.io-compatible agents (from `.agents/skills/`). It
  walks the 8-stage methodology — privacy model, feasibility, plaintext ground truth, scheme
  selection, circuit design, parameter selection, implementation, protocol
  spec — and its Stage 7 "Track A" targets the DSL below.

- **The nb DSL** ([`dsl_fhe/`](dsl_fhe/README.md)) compiles `.niob` source to
  OpenFHE C++ that links this client. Trust boundaries (`@client`/`@server`,
  `@encryptors(independent)`) are compiler-enforced; serialization, key
  generation, and record/replay instrumentation are generated; encrypted-ness
  is fully structural in the type flow; every stage gets a generated
  **cleartext reference twin** (ground truth); and compile-time advisories
  cover Chebyshev degree selection (`max_error:`), depth budgets, and the
  security/parameter frontier (logQ vs. ring dimension, with fixed-N headroom).

```bash
cd dsl_fhe
make test-compiler          # compiler unit tests
make examples               # build + run all self-contained examples
```

Seven worked examples (`simple`, `fetch-by-similarity`, `password-retrieval`,
`set-membership`, `fraud-flag`, `ml-inference-fhe`, `fhe-NetworkMonitor`) pair
with the skill's design references — three of them are the skill's own worked
designs, implemented.

| File | Purpose |
|---|---|
| [`dsl_fhe/README.md`](dsl_fhe/README.md) | Overview, build instructions, example walkthroughs |
| [`dsl_fhe/CLAUDE.md`](dsl_fhe/CLAUDE.md) | Design rationale, codegen internals |
| [`dsl_fhe/NB_LANGUAGE.md`](dsl_fhe/NB_LANGUAGE.md) | Language reference |
| [`dsl_fhe/GRAMMAR.md`](dsl_fhe/GRAMMAR.md) | Formal EBNF grammar |
| [`dsl_fhe/HOWTO.md`](dsl_fhe/HOWTO.md) | Adding a new example, step by step |

## Entry point 2 — OpenFHE, for application developers

You write standard OpenFHE code and add only the `niobium::compiler()` calls
to bracket the computation. The instrumented OpenFHE branch intercepts every
polynomial operation at the probe level — you **never** call the FHETCH API
directly.

### Step by step

1. **Compile & Link** — Build your OpenFHE application against `libnbfhetch`
   and the Niobium-instrumented OpenFHE branch. Add `niobium::compiler().init()`,
   `start()`, `stop()` around the computation. No changes to FHE algorithm code.

2. **Execute** — Every OpenFHE polynomial operation (`NTT`, `INTT`, `ADD`,
   `SUB`, `MUL`, `MULI`, `ADDI`, `MORPH`, …) triggers a C probe
   (`openfhe_cprobe_add`, `openfhe_cprobe_ntt`, …) which records one or more
   FHETCH instructions in the trace.

3. **Capture** — On `compiler().stop()`, the trace is finalized as a `.fhetch`
   text file plus a `fhetch_replay.json` manifest (crypto context, modulus
   chain, key ID ranges, input/output layout).

4. **Replay (local)** — `compiler().replay()` executes the recorded trace
   through the bundled FHETCH simulator; `compiler().result(cc, name, ct)`
   rehydrates a `Ciphertext<DCRTPoly>` from a probe so the rest of the
   application (decryption, verification) continues unchanged. On a
   cache-valid run the host executes **zero** FHE operations — and a recorded
   trace can be replayed with **regenerated keys/inputs** (changed input files
   are refreshed automatically).

5. **Submit** — Ship the trace (plus serialized inputs and metadata) to the
   Niobium compilation service, which lowers and optimizes it for the Mistic
   accelerator.

### Minimal example

```cpp
#include "openfhe.h"
#include "niobium/compiler.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info("my_app", "1.0", "CKKS multiply example");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "ckks_mul"});
    niobium::compiler().cache_parameters(params);

    // Load previously-generated crypto context, keys, and ciphertexts.
    CryptoContext<DCRTPoly> cc;
    Serial::DeserializeFromFile("keys/cc.bin", cc, SerType::BINARY);
    Ciphertext<DCRTPoly> ct_a, ct_b;
    Serial::DeserializeFromFile("keys/ct_a.bin", ct_a, SerType::BINARY);
    Serial::DeserializeFromFile("keys/ct_b.bin", ct_b, SerType::BINARY);
    // ... load mk.bin, rk.bin ...

    niobium::compiler().capture_crypto_context(cc);
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);
    niobium::compiler().tag_keys(cc);

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING ----
        // Probes fire automatically during this OpenFHE call.
        niobium::compiler().start();

        auto result = cc->EvalMult(ct_a, ct_b);

        niobium::compiler().probe("result", result);
        niobium::compiler().stop();
        // .fhetch + fhetch_replay.json are now written to disk.
    } else {
        // ---- REPLAY (cache hit: zero FHE ops on the host) ----
        niobium::compiler().replay();
    }
    Ciphertext<DCRTPoly> ct_result;
    niobium::compiler().result(cc, "result", ct_result);
    Serial::SerializeToFile("keys/ct_result.bin", ct_result, SerType::BINARY);

    return 0;
}
```

Prefer not to tag by hand? `niobium::compiler().enable_auto_tagging()` switches
to **cooperative auto-tagging**: the instrumented deserialize hooks capture the
crypto context, tag the eval keys, and tag each input ciphertext as your code
loads them (this is what the DSL generates). See
[`docs/AUTO_FACADE.md`](docs/AUTO_FACADE.md).

### Tagging inputs, keys, and outputs (manual mode)

- `capture_crypto_context(cc)` — stamps the manifest with ring dimension,
  modulus chain, and inverse chain; registers the bootstrap-precompute hook.
- `tag_input(name, ct)` — pins a ciphertext's polynomials as named inputs with
  a stable FHETCH address range, serialized for replay.
- `tag_keys(cc)` — tags all evaluation keys (eval-mult + eval-automorphism).
- `probe(name, ct)` — marks an observable output; after replay,
  `result(cc, name, ct)` reconstructs it.

Address layout: inputs occupy the low FHETCH address range (starting at 1;
address 0 is the copy sentinel), evaluation keys follow, bootstrap precompute
plaintexts come after keys.

### Hand-written examples

| Example | What it does |
|---|---|
| `examples/bootstrap/` | CKKS bootstrap under hollow recording (large trace, full replay) |
| `examples/mult/` | CKKS `EvalMult` — client/server/decrypt split with replay + rehydrate |
| `examples/simple_ops/` | 13 ops (ADD, SUB, MUL, NEG, ADDI/SUBI/MULI, compound chains, MORPH) driven by one harness |

```bash
make test-simple-ops-release
make test-mult-release
make test-bootstrap-release
make test-op-release OP=MORPH A=5 B=6   # one specific op
```

## Entry point 3 — FHETCH, for compiler writers

If you are building an FHE compiler, transpiler, or code generator, target the
**FHETCH Polynomial IR** directly and let this stack be your backend:

- **Recording API** — `fhetch_api.h` in
  [`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch): one call
  per IR operation (`sr_addp`, `sr_mulp`, `sr_ntt`, `mr_mulp`, …), wrapped by
  the `niobium::compiler()` session (init / start / probe / stop / replay /
  result / cache).
- **Trace format** — `.fhetch` is a text format; you can also emit it
  directly. The `fhetch_replay.json` manifest carries crypto context, modulus
  chain, and I/O layout.
- **Validation** — the bundled `fhetch_sim` replays any trace with
  deterministic OpenFHE native math; `fhetch_driver` re-drives a trace through
  the API as a round-trip check.
- **Transport** — [`src/fhetch_transport/`](src/fhetch_transport/) ships a
  client/server pair + archive format for delivering traces (with inputs and
  metadata) to a compilation target. Pass `--target FOG` to run on Niobium's
  stable FPGA device: the server resolves the alias to its currently pinned
  hardware id, so clients never depend on internal device names. Any other
  target id is forwarded verbatim.

The trace records FHETCH operation names, not hardware instructions — the
server-side compiler does the lowering (NTT splitting, load/store insertion,
register allocation).

## Entry point 4 — HAZE, for FHE library integrators

[`niobium-haze`](https://github.com/NiobiumInc/niobium-haze) (vendored at
`vendor/niobium-haze`) exposes a **CUDA-shaped C API** one level below
OpenFHE: `hazeMalloc` / `hazeMemcpy` / `hazeAdd` / `hazeNTT` / … — each public
entry point is a single polynomial-level IR op, recorded through the same
`libnbfhetch` core. The shape is deliberately CUDA's so GPU FHE libraries
written against CUDA — for example
[FIDESlib](https://github.com/CKKS-Community/FIDESlib) — can be retargeted to
Niobium hardware with minimal porting effort: swap the `cuda*` calls for
`haze*` calls, then `hazeFlush()` finalizes the trace and dispatches replay
(local or remote) and `hazeMemcpy` D2H reads back reconstructed results.

---

## Building

### Fresh Build

```bash
make sync                 # git submodule update --init --recursive
make release              # configure + build everything (Release)
make config && make build # same, Debug (config once, then build)
```

### Incremental Build

```bash
make sync
make build-release        # build OpenFHE + libnbfhetch + examples (Release)
make build                # same, Debug
```

### Build Pipeline

The top-level `Makefile` builds OpenFHE (vendored at
`vendor/niobium-fhetch/vendor/openfhe`), installs it under
`vendor/lib/openfhe`, then builds `libnbfhetch` and the example binaries in
one tree.

### Prerequisites

- C++17 compiler
- CMake 3.16+
- OpenFHE (Niobium-instrumented branch, reached transitively through
  `vendor/niobium-fhetch/vendor/openfhe`)
- Python 3 (DSL compiler + example harnesses)

### Updating the vendored design skill

The FHE design skill is **vendored** (its files are committed into
`.claude/skills/fhe-application-design/` and `.agents/skills/fhe-application-design/`),
not mounted as a submodule — the skill lives in a `skills/<name>/` subdirectory
of the upstream catalog, which a submodule can't mount, and committing the files
keeps a plain `git clone` working on every platform. Each copy records the
upstream commit it came from in a `.vendored-from` file.

To bump the skill to a newer upstream commit, run the refresh script with a ref
and commit the result:

```bash
scripts/update-fhe-skill.sh <git-ref>   # re-vendors both copies, updates .vendored-from
git add .claude/skills/fhe-application-design .agents/skills/fhe-application-design
git commit -m "chore: bump fhe-application-design skill to <git-ref>"
```

It is a manual step — nothing fetches the skill at build or clone time.

## Project structure

```
niobium-client/
  .claude/skills/
    fhe-application-design/   # vendored: the 8-stage FHE design skill (AI agents)
  .agents/skills/
    fhe-application-design/   # same skill, .agents/ convention (Codex / agentskills.io)
  dsl_fhe/                    # nb DSL + cross-compiler (nbc) — entry point 1
    xcomp/                    # the compiler: lexer, parser, semantic, codegen
    tools/                    # replay-integrity verifier, ...
    examples/                 # simple, fetch-by-similarity, password-retrieval,
                              # set-membership, fraud-flag, ml-inference-fhe,
                              # fhe-NetworkMonitor
  examples/                   # hand-written OpenFHE examples — entry point 2
    bootstrap/                #   CKKS bootstrap (hollow recording)
    mult/                     #   CKKS EvalMult (client / server / decrypt)
    simple_ops/               #   13 elementary ops, one harness
  include/niobium/            # public client headers (Utils/ScopedPause.h, ...)
  src/
    auto_facade/              # cooperative auto-tagging (deserialize hooks)
    fhetch_transport/         # trace transport client/server + archive — entry point 3
  docs/
    AUTO_FACADE.md            # transparent/cooperative record-replay design
  vendor/
    niobium-fhetch/           # submodule: libnbfhetch + fhetch_sim + API headers
      vendor/openfhe/         #   nested submodule: Niobium-instrumented OpenFHE
    niobium-haze/             # submodule: CUDA-shaped C API — entry point 4
    lib/openfhe/              # installed OpenFHE (built by the Makefile)
  CMakeLists.txt  Makefile  README.md  CLAUDE.md  LICENSE (Apache 2.0)
```

## Architecture decisions

- **Many front-ends, one IR** — the DSL, instrumented OpenFHE, direct FHETCH
  emission, and HAZE all converge on the same FHETCH Polynomial IR and the
  same `niobium::compiler()` session machinery. Anything that records a valid
  trace gets the simulator, the cache, replay-with-new-inputs, and the
  compilation service for free.

- **Thin client by design** — All optimization logic lives server-side in the
  proprietary compiler. The client only records and transmits the unoptimized
  instruction trace, keeping the open-source surface minimal.

- **Probe-based recording** — Application code stays standard OpenFHE; C
  probes (`probes.h`) in the instrumented branch fire on every polynomial
  operation and the FHETCH library translates them into trace instructions.

- **FHETCH-level trace format** — The trace uses Polynomial IR operation names
  (`sr_addp`, `sr_ntt`, `mr_mulp`, …), not hardware instructions; the
  server-side compiler lowers them (NTT splitting, load/store insertion,
  register allocation).

- **Cache + replay-with-new-data** — Traces are cached by `CacheParameters`.
  A cache-valid run executes zero FHE operations on the host: the trace is
  replayed with the current input files (changed inputs and keys are
  refreshed automatically), and the recorded trace itself is never
  regenerated (test-gated, including timestamps).

- **Local simulator for validation** — `fhetch_sim` replays a `.fhetch` file
  with deterministic OpenFHE native math, giving a reference for what the
  hardware computes, reachable from user code via `replay()` + `result()`
  without leaving the OpenFHE object model.

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Contributing

We are actively working on a contribution policy and Contributor License
Agreement (CLA). Until that process is in place we are not yet able to accept
external contributions. If you have a bug report, a feature request, or a
question, please [contact us](https://niobium.co/contact) directly.
Watch this repository to be notified when the contribution policy launches.
