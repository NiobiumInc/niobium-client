# Niobium Client

Open-source thin client for the **Niobium Mistic** FHE accelerator. Customers link this library against their C++ application built on the [Niobium-instrumented OpenFHE branch](https://github.com/openfheorg/openfhe-development), run their application, and obtain an unoptimized FHETCH instruction trace that is sent to the Niobium compilation service for optimization and deployment to hardware.

A local **FHETCH simulator** (`fhetch_sim`) can also replay the trace through OpenFHE modular arithmetic, reconstruct ciphertexts at probed outputs, and round-trip them back through the application — useful for validating traces end-to-end without the real accelerator.

## How It Works

```
 Customer C++ Application
 (standard OpenFHE API: EvalAdd, EvalMult, EvalRotate, ...)
         |
         | links against
         v
 +--------------------------------------------------+
 |  Niobium-instrumented OpenFHE                     |
 |  (probes.h — intercepts every polynomial op)      |
 +--------------------------------------------------+
         |
         | probes fire on each NTT, ADD, MUL, ...
         v
 +--------------------------------------------------+
 |  niobium-client                                   |
 |                                                   |
 |  fhetch_api.h  — complete FHETCH instruction set  |
 |                   (called by probes, not by user)  |
 |                                                   |
 |  compiler.h    — user-facing session API           |
 |                   (init, start, stop, tag_input,   |
 |                    probe, is_cache_valid)           |
 +--------------------------------------------------+
         |
         | emits unoptimized instruction trace
         v
 +--------------------------------------------------+
 |  .fhetch trace file (text format)                 |
 |  modulus_count 3                                   |
 |  m[0] 0xFFFFFFFFFFFFFFFF   # copy sentinel         |
 |  m[1] 0x3FFFFE80001                                |
 |  m[2] 0x40000560001                                |
 |  sr_addp %2, %0, %1, m=1                           |
 |  sr_mulp %3, %2, %1, m=2                           |
 |  sr_ntt %4, %3, m=1, omega=...                     |
 |  halt                                              |
 +--------------------------------------------------+
         |
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

1. **Compile & Link** — The customer builds their OpenFHE application and links against `niobium-client` and the Niobium-instrumented OpenFHE branch. The user code calls `niobium::compiler().init()`, `start()`, `stop()` to bracket the computation. No changes to FHE algorithm code are needed.

2. **Execute** — When the application runs, every OpenFHE polynomial operation (`NTT`, `INTT`, `ADD`, `SUB`, `MUL`, `MULI`, `ADDI`, `MORPH`, ...) triggers a C probe (`openfhe_cprobe_add`, `openfhe_cprobe_ntt`, etc.) inserted in the instrumented OpenFHE. Each probe calls the corresponding FHETCH API function (`sr_addp`, `sr_ntt`, ...) which records one or more hardware instructions in the trace.

3. **Capture** — On `compiler().stop()`, the client finalizes the instruction trace and writes it as a `.fhetch` text file using FHETCH Polynomial IR operation names (`sr_addp`, `sr_ntt`, `mr_mulp`, etc.) alongside a `fhetch_replay.json` metadata file (crypto context, modulus chain, key ID ranges, input/output layout). This trace is a direct, unoptimized recording of every polynomial operation in execution order.

4. **Replay (local)** — Calling `compiler().replay()` executes the just-recorded trace through the bundled FHETCH simulator, which runs OpenFHE modular-arithmetic backends on the serialized input polynomials and writes their final values into the probed outputs. `compiler().result(cc, name, ct)` rehydrates a `Ciphertext<DCRTPoly>` from a probe so the rest of the application (decryption, verification) continues unchanged. Useful as a sanity check before submitting the trace.

5. **Submit** — The client transmits the `.fhetch` trace (plus serialized input data and metadata) to the Niobium compilation server. The server parses the FHETCH operations, lowers them to internal hardware instructions (e.g., `sr_ntt` → `ntt1`+`ntt2`, multi-residue gadgets expand per-residue), and runs the full optimization pipeline (register allocation, dead code elimination, SSA compaction, load/store elimination, prefetch optimization, etc.) to produce an optimized binary for the Mistic accelerator.

## Key Components

### `fhetch_api.h` — Complete FHETCH Instruction Set

Defines every FHETCH Polynomial IR instruction as a C++ function. These are **not called directly by the user** — they are called by the probe mechanism inside the instrumented OpenFHE branch. Each function records one hardware instruction in the trace.

**Baseline instructions** (map to hardware ISA):

| Function | Opcode | Description |
|---|---|---|
| `sr_addp(a, b, q)` | ADD | Component-wise polynomial addition mod q |
| `sr_subp(a, b, q)` | SUB | Component-wise polynomial subtraction mod q |
| `sr_mulp(a, b, q)` | MUL | Component-wise polynomial multiplication mod q |
| `sr_addps(a, s, q)` | ADDI | Polynomial + scalar mod q |
| `sr_subps(a, s, q)` | SUBI | Polynomial - scalar mod q |
| `sr_mulps(a, s, q)` | MULI | Polynomial * scalar mod q |
| `sr_ntt(a, q)` | NTT1+NTT2 | Forward negacyclic NTT |
| `sr_intt(a, q)` | INTT1+INTT2 | Inverse negacyclic NTT |
| `sr_permute(a, ...)` | MORPH1+MORPH2 | General permutation with sign flips |
| `halt()` | STOP | End of trace |

**Multi-residue gadgets** (expand into per-residue baseline instructions):
`mr_addp`, `mr_subp`, `mr_mulp`, `mr_ntt`, `mr_intt`, `mr_zeros`, `mr_append_srp`, `mr_union`, `mr_subset`, `fast_base_convert`, `rescale_fbc`, `mrpa_dotproduct`, `dig_decomp`

**Data types** (opaque, pimpl pattern):
`Polynomial` (single-residue), `Scalar`, `MRP` (multi-residue polynomial), `MRS` (multi-residue scalar), `SRPArray`, `MRPArray`

**Optional operations**: Non-integer arithmetic (`_ni` suffix), Fourier transforms (`sr_ft`/`sr_ift`), TFHE-specific (`gadget_decomp`, `gsw_rlwe_ext_prod`), automorphisms, CKKS bootstrapping.

### `compiler.h` — Minimal User-Facing API

The strict-minimum API the customer uses to control the recording session:

```cpp
namespace niobium {
  class Compiler {
  public:
    // Session lifecycle
    void init(int& argc, char** argv);       // Parse CLI options
    bool start();                            // Begin recording
    bool stop();                             // Finalize trace, write .fhetch

    // Program metadata
    void set_program_info(name, version, description);
    void set_build_info(file, line, timestamp);

    // Cache management
    typedef std::vector<std::pair<std::string, std::string>> CacheParameters;
    void cache_parameters(CacheParameters& params);
    bool is_cache_valid();

    // Crypto context and key tagging
    template<typename CryptoContextType>
    void capture_crypto_context(const CryptoContextType& cc);
    template<typename CryptoContextType>
    void tag_keys(const CryptoContextType& cc);   // evalmult + evalautomorphism
    void reserve_addresses(uint64_t next_addr);    // lay out inputs vs keys

    // Input/output tagging
    template<typename T>
    void tag_input(const std::string& name, const T& ct, ...);
    template<typename T>
    void probe(const std::string& name, const T& ct);
    template<typename CC, typename T>
    bool result(const CC& cc, const std::string& name, T& ct);

    // Replay (local simulator)
    bool replay();                           // Run trace through FHETCH sim

    // Recording modes
    void enable_hollow_mode(bool enabled);    // Skip expensive math
    void enable_multithreaded_recording();

    // Functional epochs (split large computations)
    void start_epoch();
    bool stop_epoch();
  };

  Compiler& compiler();  // Global singleton
}
```

### FHETCH Instruction Trace Format

The output is a human-readable text file recording every FHETCH Polynomial IR operation as it was executed:

```
# Niobium FHETCH Trace
# Program: my_program v1.0
# Instruction Count: 24
# Modulus Count: 2

# Modulus Table
modulus_count 2
m[0] 0xFFFFFFFF00000001
m[1] 0xFFFFFFFE00000001

# Instructions
sr_addp %2, %0, %1, m=0
sr_mulp %3, %2, %1, m=0
sr_mulps %4, %3, 42, m=0
sr_negp %5, %4, m=0
sr_intt %6, %5, m=0
sr_ntt %7, %6, m=0
sr_addps_coeff %8, %7, 1, m=1
halt
```

The file starts with a **modulus table** that maps indices to prime modulus values. Instructions reference moduli by index (`m=0`, `m=1`, ...) rather than repeating the full value on every line — this keeps the trace compact and makes parsing efficient.

By convention (matching `niobium-compiler`'s `ModulusTable`):

- `m[0]` is reserved for the sentinel value `0xFFFFFFFFFFFFFFFF` used by register-copy and zero-init instructions (e.g. `sr_addps %d, %s, 0, m=0` is a raw copy, since no modular reduction is meaningful against the sentinel).
- `m[1..N]` hold the real Q and P moduli, sorted in ascending value order for deterministic ordering regardless of encounter order during recording.

Each instruction line is one FHETCH operation using the Polynomial IR function names. The server-side `niobium-compiler` parses this trace and lowers each FHETCH operation to internal hardware instructions (e.g., `sr_ntt` becomes `ntt1`+`ntt2`, multi-residue gadgets expand into per-residue operations, register allocation and load/store insertion is performed). This trace is **intentionally unoptimized** — all optimization happens server-side.

### Replay Metadata (`fhetch_replay.json`)

Alongside the `.fhetch` file, the client emits a JSON replay manifest consumed by the local simulator and the compilation server. It mirrors the schema of `niobium-compiler`'s `replay.json`:

- `crypto_context` — scheme, ring dimension, multiplicative depth, full modulus chain (with sentinel at index 0) and its Hensel-lifted inverse chain.
- `key_start_addr_ids` — FHETCH address where each key type begins; by convention `evalmult` = 25 and `evalautomorphism` starts right after the last evalmult id. This lets inputs occupy the low address range (1..24) while keys sit at 25+.
- `files` — pointers to per-input `.bin` + `.ids` pairs, key files (`mk.bin` / `mk.ids`, `rk.bin` / `rk.ids`), the instruction trace, and the output index.

## Project Structure

```
niobium-client/
  include/
    niobium/
      fhetch_api.h            # Complete FHETCH instruction set (used by probes)
      compiler.h              # Minimal user-facing session API
      fhetch_sim/             # Simulator API (load_trace, run, result lookup)
      openfhe/                # Probe ABI seen by the instrumented OpenFHE
  src/
    fhetch_api.cpp            # Instruction recording into .fhetch trace
    compiler.cpp              # Session lifecycle, replay orchestration, JSON
    trace_writer.{h,cpp}      # .fhetch text emitter + modulus-table normalizer
    probes.cpp                # C-linkage openfhe_cprobe_* implementations
    auto_facade.cpp           # capture_crypto_context / tag_keys / tag_input
    fhetch_sim/               # Trace parser + OpenFHE-backed executor
    cereal_io.h               # Binary I/O helpers (matches compiler layout)
  examples/
    bootstrap/                # CKKS bootstrap (hollow recording — big trace)
    mult/                     # CKKS EvalMult (client / server / decrypt)
    simple_ops/               # 13 elementary ops for systematic testing
  vendor/
    json/                     # nlohmann/json (submodule)
    openfhe/                  # Niobium-instrumented OpenFHE (submodule)
  CMakeLists.txt
  Makefile                    # Top-level driver (build, test-*, clean targets)
  README.md
  LICENSE                     # Apache 2.0
```

## Usage

The customer writes standard OpenFHE code, adding only the `compiler.h` calls to bracket the computation. The example below records `EvalMult`, replays it through the local simulator, and retrieves the result ciphertext for decryption:

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

    // Load previously-generated crypto context, keys, and ciphertexts
    CryptoContext<DCRTPoly> cc;
    Serial::DeserializeFromFile("keys/cc.bin", cc, SerType::BINARY);
    // ... deserialize mk.bin, rk.bin, ct_a.bin, ct_b.bin ...

    // Compiler-matching FHETCH layout: inputs in 1..24, keys in 25+.
    // OpenFHE assigns poly IDs the moment polys are constructed (during
    // deserialization), so reserve_addresses() is interleaved with loads.
    niobium::compiler().reserve_addresses(1);
    // ... load ct_a, ct_b ...
    niobium::compiler().reserve_addresses(25);
    // ... load mk, rk ...

    niobium::compiler().capture_crypto_context(cc);
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);
    niobium::compiler().tag_keys(cc);

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING ----
        // Probes fire automatically during these OpenFHE calls
        niobium::compiler().start();

        auto result = cc->EvalMult(ct_a, ct_b);

        niobium::compiler().probe("result", result);
        niobium::compiler().stop();
        // .fhetch + fhetch_replay.json are now written to disk
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

### Examples

| Example | What it does |
|---|---|
| `examples/bootstrap/` | CKKS bootstrap under hollow recording (large trace, no replay). |
| `examples/mult/` | CKKS `EvalMult` — client/server/decrypt split with replay + rehydrate. |
| `examples/simple_ops/` | 13 ops (ADD, SUB, MUL, NEG, ADDI/SUBI/MULI, compound chains, MORPH) driven by a single harness for systematic testing. |

Run a specific op with:

```bash
make test-op-release OP=MORPH A=5 B=6
```

or the full sweep with `make test-simple-ops-release`.

## Building

The top-level `Makefile` drives an out-of-source CMake build and pulls in the vendored submodules (`vendor/json`, `vendor/openfhe`) on first build:

```bash
git submodule update --init --recursive
make build-release       # or: make build  (Debug)
```

To run the bundled examples end-to-end:

```bash
make test-mult-release           # client → server → decrypt
make test-simple-ops-release     # ADD, SUB, MUL, NEG, MORPH, ...
make test-bootstrap-release      # CKKS bootstrap (hollow recording)
```

### Prerequisites

- C++17 compiler
- CMake 3.16+
- OpenFHE (Niobium-instrumented branch, vendored under `vendor/openfhe`)

## Architecture Decisions

- **Thin client by design** — All optimization logic lives server-side in `niobium-compiler`. The client only records and transmits the unoptimized instruction trace. This keeps the open-source surface minimal and the IP protected.

- **Probe-based recording** — The user writes standard OpenFHE code. The instrumented OpenFHE branch contains C probes (`probes.h`) that fire on every polynomial operation. The client's FHETCH API translates these probe calls into FHETCH trace instructions. No user code changes beyond the `compiler.h` session calls.

- **FHETCH-level trace format** — The trace uses FHETCH Polynomial IR operation names (`sr_addp`, `sr_ntt`, `mr_mulp`, etc.), not internal hardware instructions. The server-side compiler is responsible for lowering these to internal instructions (`ntt1`+`ntt2`, `load`/`store` insertion, register allocation). This keeps the client simple and decoupled from hardware-specific details.

- **Cache support** — The client caches instruction traces by `CacheParameters`. If the computation structure hasn't changed, the same trace can be reused with different input data, avoiding re-recording.

- **Compiler-parity conventions** — The trace and `fhetch_replay.json` follow `niobium-compiler`'s conventions so the two can be diffed artifact-for-artifact:
  - Modulus table reserves index 0 for the copy sentinel, with the real moduli sorted ascending at indices 1..N.
  - FHETCH address space is laid out with inputs at ids 1..24 and keys at 25+ (`evalmult` at 25, `evalautomorphism` following).
  - `capture_crypto_context` captures both Q and P (aux) moduli; `inverse_modulus_chain` is Hensel-lifted inline.

- **Local simulator for trace validation** — `fhetch_sim` replays a `.fhetch` file against its `fhetch_replay.json` metadata using OpenFHE's `NativeVector` / `NativeInteger` math, giving a deterministic reference for what the hardware would compute. The `Compiler::replay()` + `result()` pair makes this round-trip accessible from user code without leaving the OpenFHE object model.

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome. Please open an issue to discuss proposed changes before submitting a pull request.
