# ML-Inference-FHE: DSL Implementation Guide

## Overview

Encrypted MNIST 2-layer MLP inference using CKKS FHE (HEIR v2 model).
Architecture: 784 -> 512 -> 10 with Chebyshev ReLU approximation (layer 1), linear (layer 2).
Uses "rotate-and-multiply" SIMD technique: all 1024 slots carry the same image, rotations select different pixel positions.

## Self-contained stub vs. real model

The real HEIR-generated model (`mlp_openfhe.cpp`, ~28K lines) and the trained
weights are **not vendored** in this open-source client. To keep the example
buildable with no external/private repo:

- **Default (`make ml-inference`)**: builds against a **stub** model
  (`mlp_openfhe.{h,cpp}` in this directory — a no-op `mnist()` that does one
  trivial homomorphic op) plus **stub zero-weights** generated into `data/` by
  `data/make_stub_weights.py`. The pipeline builds and runs end-to-end
  (record → replay → decrypt) but the output is **not** real inference.
- **Real inference (opt-in)**: obtain the HEIR model + trained weights from the
  ml-inference submission package and build with
  `make ml-inference NIOBIUM_COMPILER_ROOT=/path/to/checkout`. The submission's
  `src/mlp_openfhe.cpp` and `data/*.bin` then take precedence over the stub.

See `data/README.md`. The rest of this guide describes the real submission.

## Pipeline Stages

| Stage | Binary | Domain | Description |
|-------|--------|--------|-------------|
| 1 | `key_generation` | client | CKKS depth=8, ring_dim=2048, rotation indices 1..1023 |
| 2 | `encode_encrypt_input` | client | Load MNIST pixels, pad to 1024 slots, encrypt per image |
| 3 | `encrypted_compute` | server | FHE MLP inference via `mlp()` bridge to HEIR v2 `mnist()` |
| 4 | `decrypt_decode` | client | Decrypt, argmax first 10 slots, output predicted digits |

## DSL Files

### shared.nb
- **Constants**: INPUT_DIM=784, NORMALIZED_DIM=1024, NUM_CLASSES=10, RING_DIM=2048, N_SLOTS=1024
- **Layer dims**: LAYER1_IN=784, LAYER1_OUT=512, LAYER2_IN=512, LAYER2_OUT=10
- **Instance enum**: Single(1), Small(100), Medium(1000), Large(10000) batches
- **Directories**: datadir, iodir, pubkeydir, seckeydir, ctxtupdir, ctxtdowndir
- **Wire types**: CryptoParams, EncryptedInput (enc<vec<f64>>), EncryptedResult (enc<vec<f64>>)
- **Model weights**: loaded at runtime from `submission/data/*.bin` via `mlp_bridge.cpp`

### client.nb
- **Scheme**: CKKS, security=not_set, ring_dim=2048, depth=8
- **Requires**: add, mul, rotate (indices 1..1023)
- Stage 1: `generate_keys(inst)` -> keygen + save to pubkeydir/seckeydir
- Stage 2: `encrypt_input(inst)` -> load_matrix, tile, encrypt, save per-batch
- Stage 3: `decrypt_decode(inst)` -> decrypt, argmax, write predictions file

### server.nb
- Stage: `encrypted_compute(inst, batch_id)` -> load params + input, call `mlp()`, save result
- Hardware annotation: `@hardware(cache_key: ["workload_size", "batch_id"])`
- `mlp()` uses `extern_call("mlp", ct)` — routed to `mlp_bridge.cpp` at link time

## Reference C++ Implementation

Location: `examples/ml-inference-fhe/submission/`

### Source Files
| File | Purpose |
|------|---------|
| `src/client_key_generation.cpp` | Creates CKKS context, generates key pair + rotation keys (1..1023) |
| `src/client_encode_encrypt_input.cpp` | Loads test_pixels.txt, encrypts per batch |
| `src/server_encrypted_compute.cpp` | FHE inference with Niobium record/replay, batch processing |
| `src/client_decrypt_decode.cpp` | Decrypts, argmax, writes predictions .txt |
| `src/mlp_openfhe.cpp` | HEIR v2 machine-generated model (~28K lines), exports `mnist()` |
| `src/weight_loader.cpp` | Loads float32 weights from binary files |
| `src/mlp_encryption_utils.cpp` | Crypto context setup, encrypt/decrypt helpers |
| `src/mlp_common.cpp` | Shared utilities (Score struct, argmax, key I/O) |

### DSL-specific bridge
| File | Purpose |
|------|---------|
| `dsl_fhe/examples/ml-inference-fhe/mlp_bridge.cpp` | Provides `mlp(cc, ct)` for the DSL: loads weights and calls `mnist()` |

### Key Headers
| File | Content |
|------|---------|
| `include/params.h` | InstanceSize enum, InstanceParams class, directory getters |
| `include/mlp_openfhe.h` | `mnist(cc, w1, b1, w2, b2, inputs) -> vector<CiphertextT>` |
| `include/weight_loader.h` | `load_weights(path, count) -> vector<float>` |
| `include/mlp_encryption_utils.h` | encrypt/decrypt helpers, load/write dataset |

### Key Function Signatures
```cpp
// HEIR v2 model (mlp_openfhe.h)
std::vector<CiphertextT> mnist(CryptoContextT cc,
    std::vector<float> fc1_weight, std::vector<float> fc1_bias,
    std::vector<float> fc2_weight, std::vector<float> fc2_bias,
    std::vector<CiphertextT> input);

// DSL bridge (mlp_bridge.cpp) — called by extern_call("mlp", ct)
ConstCiphertext<DCRTPoly> mlp(CryptoContext<DCRTPoly> cc, ConstCiphertext<DCRTPoly> ct);
```

## Data Format

### Input
- `datasets/{instance}/intermediate/test_pixels.txt` — 784 floats per line (normalized 0..1)
- Each line = one MNIST image (28x28 flattened)
- Padded to 1024 by tiling (repeating) the 784 values

### Weights
- `submission/data/fc1_weight.bin` — 512×784 float32 (401,408 values)
- `submission/data/fc1_bias.bin` — 512 float32
- `submission/data/fc2_weight.bin` — 10×512 float32 (5,120 values)
- `submission/data/fc2_bias.bin` — 10 float32

### Output
- `io/{instance}/encrypted_model_predictions.txt` — one digit 0-9 per line

## I/O Directory Structure
```
io/{single|small|medium|large}/
  public_keys/          cc.bin, pk.bin, mk.bin, rk.bin
  secret_key/           sk.bin
  ciphertexts_upload/   cipher_input_0.bin, cipher_input_1.bin, ...
  ciphertexts_download/ cipher_result_0.bin, cipher_result_1.bin, ...
  encrypted_model_predictions.txt
```

## Build Dependencies

The generated CMakeLists.txt links (auto-discovered by codegen):
- `mlp_openfhe` (from `submission/src/mlp_openfhe.cpp`) — defines `mnist()`
- `mlp_encryption_utils` (from `submission/src/mlp_encryption_utils.cpp`)
- `mlp_common` (from `submission/src/mlp_common.cpp`)
- `mlp_bridge` (from `dsl_fhe/examples/ml-inference-fhe/mlp_bridge.cpp`) — defines `mlp()`

`LOCAL_SRC_DIR` is passed via `dsl_fhe/Makefile` so codegen finds `mlp_bridge.cpp` outside `SUBMISSION_DIR`.

## Execution
```bash
cd dsl_fhe && make ml-inference
# Test (Single instance):
cd examples/ml-inference-fhe/nb_out/build
export ML_WEIGHT_DIR=<repo_root>/examples/ml-inference-fhe/submission/data
./key_generation 0
./encode_encrypt_input 0
./encrypted_compute 0
./decrypt_decode 0
```

## Harness
Full orchestration: `examples/ml-inference-fhe/harness/run_submission.py`
Supports `--niobium_hw`, `--target`, `--jobs`, `--preserve`, `--num_runs`
