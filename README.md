# Niobium Client — OpenFHE Integration

Open-source thin client for the **Niobium Mistic** FHE accelerator. This repository is the OpenFHE integration layer: it wires the Niobium-instrumented OpenFHE branch to the FHETCH recording / replay library ([`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch)) and ships end-to-end OpenFHE examples you can run out of the box.

Customers link their OpenFHE C++ application against the Niobium-instrumented OpenFHE build plus `libnbfhetch`, run the application, and obtain an unoptimized `.fhetch` instruction trace that is sent to the Niobium compilation service for optimization and deployment to hardware. A local simulator can replay the trace end-to-end and round-trip reconstructed ciphertexts through the rest of the OpenFHE program — useful for validation without the real accelerator.

For the FHETCH Polynomial IR instruction set, session API, trace format, and simulator internals, see the companion repository: [`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch).

## How it fits together

```
 Customer C++ Application
 (standard OpenFHE API: EvalAdd, EvalMult, EvalRotate, ...)
         |
         | links against
         v
 +--------------------------------------------------+
 |  Niobium-instrumented OpenFHE                    |
 |  (probes.h — intercepts every polynomial op)     |
 +--------------------------------------------------+
         |
         | openfhe_cprobe_* fires on each
         | NTT, ADD, MUL, MORPH, ...
         v
 +--------------------------------------------------+
 |  niobium-fhetch (libnbfhetch)                    |
 |   fhetch_api.h — FHETCH Polynomial IR recording  |
 |   compiler.h   — session API + local replay      |
 +--------------------------------------------------+
         |
         | emits unoptimized .fhetch trace
         | + fhetch_replay.json manifest
         v
         +-------------------+-------------------+
         |                                       |
         v                                       v
 +---------------------+             +--------------------+
 | fhetch_sim (local)  |             | Niobium Server     |
 | — OpenFHE-based     |             | (niobium-compiler) |
 |   executor          |             | — proprietary      |
 | — replays trace and |             | — optimizes and    |
 |   reconstructs the  |             |   deploys to       |
 |   result ciphertext |             |   Mistic hardware  |
 +---------------------+             +--------------------+
```

### Step by step

1. **Compile & Link** — The customer builds their OpenFHE application and links against `libnbfhetch` and the Niobium-instrumented OpenFHE branch. The user code calls `niobium::compiler().init()`, `start()`, `stop()` to bracket the computation. No changes to FHE algorithm code are needed.

2. **Execute** — When the application runs, every OpenFHE polynomial operation (`NTT`, `INTT`, `ADD`, `SUB`, `MUL`, `MULI`, `ADDI`, `MORPH`, ...) triggers a C probe (`openfhe_cprobe_add`, `openfhe_cprobe_ntt`, etc.) inserted in the instrumented OpenFHE. Each probe calls the corresponding FHETCH API function (`sr_addp`, `sr_ntt`, ...) which records one or more hardware instructions in the trace.

3. **Capture** — On `compiler().stop()`, the client finalizes the instruction trace and writes it as a `.fhetch` text file alongside a `fhetch_replay.json` metadata file (crypto context, modulus chain, key ID ranges, input/output layout). This trace is a direct, unoptimized recording of every polynomial operation in execution order.

4. **Replay (local)** — Calling `compiler().replay()` executes the just-recorded trace through the bundled FHETCH simulator, which runs OpenFHE modular-arithmetic backends on the serialized input polynomials and writes their final values into the probed outputs. `compiler().result(cc, name, ct)` rehydrates a `Ciphertext<DCRTPoly>` from a probe so the rest of the application (decryption, verification) continues unchanged. Useful as a sanity check before submitting the trace.

5. **Submit** — The client transmits the `.fhetch` trace (plus serialized input data and metadata) to the Niobium compilation server. The server parses the FHETCH operations, lowers them to internal hardware instructions, and runs the full optimization pipeline to produce an optimized binary for the Mistic accelerator.

## Instrumenting an OpenFHE application

The customer writes standard OpenFHE code and adds only the `niobium::compiler()` calls to bracket the computation. The example below records `EvalMult`, replays it through the local simulator, and retrieves the result ciphertext for decryption:

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
    }

    // ---- REPLAY (optional, local) ----
    niobium::compiler().replay();
    Ciphertext<DCRTPoly> ct_result;
    niobium::compiler().result(cc, "result", ct_result);
    Serial::SerializeToFile("keys/ct_result.bin", ct_result, SerType::BINARY);

    return 0;
}
```

The user **never** calls `fhetch_api.h` functions directly. The instrumented OpenFHE branch intercepts `EvalMult`, `EvalAdd`, etc. at the polynomial level and calls the FHETCH API internally via probes.

### Tagging inputs, keys, and outputs

- `capture_crypto_context(cc)` — stamps the `fhetch_replay.json` manifest with ring dimension, modulus chain, and inverse chain. Also registers the auto-capture hook that walks the CryptoContext's bootstrap precompute map at `stop()` time.
- `tag_input(name, ct)` — pins a ciphertext's polynomials as named inputs so their FHETCH address space is stable and they get serialized into `<name>.bin` for replay.
- `tag_keys(cc)` — tags all evaluation keys (eval-mult + eval-automorphism).
- `probe(name, ct)` — marks a ciphertext as an observable output. After replay, `result(cc, name, ct)` reconstructs it.

Address layout follows `niobium-compiler` conventions: inputs occupy the low FHETCH address range (starting at 1; address 0 is the copy sentinel), evaluation keys follow, and bootstrap precompute plaintexts come after keys.

## Examples

Three end-to-end OpenFHE examples live in `examples/`. Each is a `client` / `server` / `decrypt` split:

| Example | What it does |
|---|---|
| `examples/bootstrap/` | CKKS bootstrap under hollow recording (large trace, full replay). |
| `examples/mult/` | CKKS `EvalMult` — client/server/decrypt split with replay + rehydrate. |
| `examples/simple_ops/` | 13 ops (ADD, SUB, MUL, NEG, ADDI/SUBI/MULI, compound chains, MORPH) driven by a single harness for systematic testing. |

Run the full sweep:

```bash
make test-simple-ops-release
make test-mult-release
make test-bootstrap-release
```

Run a specific op:

```bash
make test-op-release OP=MORPH A=5 B=6
```

## DSL for FHE (`dsl_fhe/`)

`dsl_fhe/` contains an optional domain-specific language and cross-compiler
(`nbc`) for writing FHE applications at a higher level. `.nb` source files
compile to OpenFHE C++ that links against this client (`libnbfhetch`), producing
the same record → replay → reconstruct pipeline the hand-written examples above
use — but with trust boundaries (`@client` / `@server`), key/serialization
plumbing, and Niobium record/replay instrumentation generated automatically.

```bash
cd dsl_fhe
make test-compiler          # compiler unit tests
make simple                 # compile a DSL example to C++ and build it
make test-simple            # build + run the simple example end-to-end
make examples               # build/run all self-contained examples
```

The DSL targets the open-source client (`niobium::compiler()` from
`libnbfhetch`) via cooperative auto-tagging — no dependency on the proprietary
compiler. See its own documentation:

| File | Purpose |
|---|---|
| [`dsl_fhe/README.md`](dsl_fhe/README.md) | User-facing overview, build instructions, example walkthroughs |
| [`dsl_fhe/CLAUDE.md`](dsl_fhe/CLAUDE.md) | Design rationale, codegen internals, client-API integration |
| [`dsl_fhe/NB_LANGUAGE.md`](dsl_fhe/NB_LANGUAGE.md) | Language reference — types, syntax, built-in functions |
| [`dsl_fhe/GRAMMAR.md`](dsl_fhe/GRAMMAR.md) | Formal EBNF grammar |
| [`dsl_fhe/HOWTO.md`](dsl_fhe/HOWTO.md) | Step-by-step guide for adding a new example |

## Building

```bash
git submodule update --init --recursive
make build-release       # or: make build  (Debug)
```

The top-level `Makefile` builds OpenFHE (vendored at `vendor/niobium-fhetch/vendor/openfhe`), installs it under `vendor/lib/openfhe`, then builds `libnbfhetch` (via `add_subdirectory(vendor/niobium-fhetch)`) and the example binaries in one tree.

### Prerequisites

- C++17 compiler
- CMake 3.16+
- OpenFHE (Niobium-instrumented branch, reached transitively through `vendor/niobium-fhetch/vendor/openfhe`)

## Project structure

```
niobium-client/
  examples/
    bootstrap/                # CKKS bootstrap (hollow recording)
    mult/                     # CKKS EvalMult (client / server / decrypt)
    simple_ops/               # 13 elementary ops for systematic testing
    CMakeLists.txt
  dsl_fhe/                    # DSL + cross-compiler (nbc) for FHE apps (see its README.md)
    xcomp/                    # the compiler: lexer, parser, semantic, codegen
    examples/                 # .nb examples (simple, fetch, ml-inference, ...)
  vendor/
    niobium-fhetch/           # submodule: libnbfhetch + simulator + API headers
      vendor/openfhe/         # nested submodule: Niobium-instrumented OpenFHE
      vendor/json/            # nested submodule: nlohmann/json
  CMakeLists.txt              # add_subdirectory(vendor/niobium-fhetch) + examples
  Makefile                    # Top-level driver (build, test-*, clean targets)
  README.md
  LICENSE                     # Apache 2.0
```

## Architecture decisions

- **Thin client by design** — All optimization logic lives server-side in `niobium-compiler`. The client only records and transmits the unoptimized instruction trace. This keeps the open-source surface minimal and the IP protected.

- **Probe-based recording** — The user writes standard OpenFHE code. The instrumented OpenFHE branch contains C probes (`probes.h`) that fire on every polynomial operation. The FHETCH library translates these probe calls into FHETCH trace instructions. No user code changes beyond the `compiler.h` session calls.

- **FHETCH-level trace format** — The trace uses FHETCH Polynomial IR operation names (`sr_addp`, `sr_ntt`, `mr_mulp`, etc.), not internal hardware instructions. The server-side compiler lowers these to internal instructions (`ntt1`+`ntt2`, `load`/`store` insertion, register allocation). See [`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch) for the full instruction set and trace format.

- **Cache support** — The client caches instruction traces by `CacheParameters`. If the computation structure hasn't changed, the same trace can be reused with different input data, avoiding re-recording.

- **Local simulator for trace validation** — `fhetch_sim` (from `niobium-fhetch`) replays a `.fhetch` file against its `fhetch_replay.json` metadata using OpenFHE's `NativeVector` / `NativeInteger` math, giving a deterministic reference for what the hardware would compute. The `Compiler::replay()` + `result()` pair makes this round-trip accessible from user code without leaving the OpenFHE object model.

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome. Please open an issue to discuss proposed changes before submitting a pull request.
