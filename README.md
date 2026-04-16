# Niobium Client

Open-source thin client for the **Niobium Mistic** FHE accelerator. Customers link this library against their C++ application built on the [Niobium-instrumented OpenFHE branch](https://github.com/openfheorg/openfhe-development), run their application, and obtain an unoptimized FHETCH instruction trace that is sent to the Niobium compilation service for optimization and deployment to hardware.

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
 |  sr_addp %2, %0, %1, q=...                        |
 |  sr_mulp %3, %2, %1, q=...                        |
 |  sr_ntt %4, %3, q=...                              |
 |  mr_addp %6, %5, %7                                |
 |  halt                                              |
 +--------------------------------------------------+
         |
         | gRPC
         v
 +--------------------------------------------------+
 |  Niobium Server (niobium-compiler)                |
 |  — proprietary, not open-source                   |
 |  — 17-pass optimization pipeline                  |
 |  — targets Mistic / BASALISC hardware             |
 +--------------------------------------------------+
```

### Step by step

1. **Compile & Link** — The customer builds their OpenFHE application and links against `niobium-client` and the Niobium-instrumented OpenFHE branch. The user code calls `niobium::compiler().init()`, `start()`, `stop()` to bracket the computation. No changes to FHE algorithm code are needed.

2. **Execute** — When the application runs, every OpenFHE polynomial operation (`NTT`, `INTT`, `ADD`, `SUB`, `MUL`, `MULI`, `ADDI`, `MORPH`, ...) triggers a C probe (`openfhe_cprobe_add`, `openfhe_cprobe_ntt`, etc.) inserted in the instrumented OpenFHE. Each probe calls the corresponding FHETCH API function (`sr_addp`, `sr_ntt`, ...) which records one or more hardware instructions in the trace.

3. **Capture** — On `compiler().stop()`, the client finalizes the instruction trace and writes it as a `.fhetch` text file using FHETCH Polynomial IR operation names (`sr_addp`, `sr_ntt`, `mr_mulp`, etc.). This trace is a direct, unoptimized recording of every polynomial operation in execution order.

4. **Submit** — The client transmits the `.fhetch` trace (plus serialized input data and metadata) to the Niobium compilation server via gRPC. The server parses the FHETCH operations, lowers them to internal hardware instructions (e.g., `sr_ntt` → `ntt1`+`ntt2`, multi-residue gadgets expand per-residue), and runs the full optimization pipeline (register allocation, dead code elimination, SSA compaction, load/store elimination, prefetch optimization, etc.) to produce an optimized binary for the Mistic accelerator.

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
    bool stop();                             // Finalize trace, write .seq

    // Program metadata
    void set_program_info(name, version, description);
    void set_build_info(file, line, timestamp);

    // Cache management
    typedef std::vector<std::pair<std::string, std::string>> CacheParameters;
    void cache_parameters(CacheParameters& params);
    bool is_cache_valid();

    // Input/output tagging
    template<typename T>
    void tag_input(const std::string& name, const T& ct, ...);
    template<typename T>
    void probe(const std::string& name, const T& ct);

    // Crypto context capture
    void capture_crypto_context(const CryptoContext<DCRTPoly>& cc);

    // Hollow mode (skip expensive math, keep structure for fast recording)
    void enable_hollow_mode(bool enabled);

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
sr_addp %2, %0, %1, q=0xFFFFFFFF00000001
sr_mulp %3, %2, %1, q=0xFFFFFFFF00000001
sr_mulps %4, %3, 42, q=0xFFFFFFFF00000001
sr_negp %5, %4, q=0xFFFFFFFF00000001
sr_intt %6, %5, q=0xFFFFFFFF00000001
sr_ntt %7, %6, q=0xFFFFFFFF00000001
sr_addps_coeff %8, %7, 1, q=0xFFFFFFFF00000001
mr_addp %10, %9, %11          # multi-residue (expands per-residue on server)
mr_ntt %12, %10
mrpa_dotproduct %13, %14, %15
halt
```

Each line is one FHETCH operation using the Polynomial IR function names. The server-side `niobium-compiler` parses this trace and lowers each FHETCH operation to internal hardware instructions (e.g., `sr_ntt` becomes `ntt1`+`ntt2`, multi-residue gadgets expand into per-residue operations, register allocation and load/store insertion is performed). This trace is **intentionally unoptimized** — all optimization happens server-side.

## Project Structure

```
niobium-client/
  include/
    niobium/
      fhetch_api.h            # Complete FHETCH instruction set (used by probes)
      compiler.h              # Minimal user-facing session API
  src/
    fhetch_api.cpp            # Instruction recording into .seq trace
    compiler.cpp              # Session lifecycle, cache, metadata
    trace_writer.cpp          # FHETCH text format serializer
    grpc_client.cpp           # Trace submission to Niobium server
  proto/
    niobium_service.proto     # gRPC service definition
  examples/
    simple_ckks.cpp           # BFV/CKKS example using OpenFHE + compiler.h
  CMakeLists.txt
  README.md
  LICENSE                     # Apache 2.0
```

## Usage

The customer writes standard OpenFHE code, adding only the `compiler.h` calls to bracket the computation:

```cpp
#include "openfhe.h"
#include "niobium/compiler.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    // Initialize the Niobium compiler (parses --target, -O flags, etc.)
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info("my_app", "1.0", "CKKS multiply example");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "ckks_mul"});
    niobium::compiler().cache_parameters(params);

    // Standard OpenFHE setup
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetMultiplicativeDepth(2);
    parameters.SetScalingModSize(50);
    auto cc = GenCryptoContext(parameters);
    cc->Enable(PKE); cc->Enable(KEYSWITCH); cc->Enable(LEVELEDSHE);
    auto keys = cc->KeyGen();
    cc->EvalMultKeyGen(keys.secretKey);

    // Capture crypto context for serialization
    niobium::compiler().capture_crypto_context(cc);

    // Encrypt inputs
    auto ct1 = cc->Encrypt(keys.publicKey, cc->MakeCKKSPackedPlaintext({3.14}));
    auto ct2 = cc->Encrypt(keys.publicKey, cc->MakeCKKSPackedPlaintext({2.71}));

    // Tag inputs
    niobium::compiler().tag_input("ct1", ct1);
    niobium::compiler().tag_input("ct2", ct2);

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING ----
        // Probes fire automatically during these OpenFHE calls
        niobium::compiler().start();

        auto result = cc->EvalMult(ct1, ct2);

        niobium::compiler().probe("result", result);
        niobium::compiler().stop();
        // FHETCH trace file is now written to disk
    }

    return 0;
}
```

The user **never** calls `fhetch_api.h` functions directly. The instrumented OpenFHE branch intercepts `EvalMult`, `EvalAdd`, etc. at the polynomial level and calls the FHETCH API internally via probes.

## Building

```bash
mkdir build && cd build
cmake ..
make
```

### Prerequisites

- C++17 compiler
- CMake 3.16+
- gRPC and Protobuf
- OpenFHE (Niobium instrumented branch)

## Architecture Decisions

- **Thin client by design** — All optimization logic lives server-side in `niobium-compiler`. The client only records and transmits the unoptimized instruction trace. This keeps the open-source surface minimal and the IP protected.

- **Probe-based recording** — The user writes standard OpenFHE code. The instrumented OpenFHE branch contains C probes (`probes.h`) that fire on every polynomial operation. The client's FHETCH API translates these probe calls into FHETCH trace instructions. No user code changes beyond the `compiler.h` session calls.

- **FHETCH-level trace format** — The trace uses FHETCH Polynomial IR operation names (`sr_addp`, `sr_ntt`, `mr_mulp`, etc.), not internal hardware instructions. The server-side compiler is responsible for lowering these to internal instructions (`ntt1`+`ntt2`, `load`/`store` insertion, register allocation). This keeps the client simple and decoupled from hardware-specific details.

- **Cache support** — The client caches instruction traces by `CacheParameters`. If the computation structure hasn't changed, the same trace can be reused with different input data, avoiding re-recording.

- **gRPC transport** — Chosen for efficient streaming of large traces and bidirectional communication for compilation status.

## License

Apache 2.0 — see [LICENSE](LICENSE).

## Contributing

Contributions are welcome. Please open an issue to discuss proposed changes before submitting a pull request.
