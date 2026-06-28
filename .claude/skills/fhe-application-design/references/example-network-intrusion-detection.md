# Worked Example: CKKS Privacy-Preserving Network Intrusion Detection

This reference describes a production-scale FHE application: privacy-preserving
network intrusion detection (NID) using the Kitsune anomaly detection framework.
A client organization monitors its own network traffic for intrusions by sending
encrypted network features to a security service, which runs an anomaly detection
neural network entirely under encryption and returns encrypted anomaly scores.

Source: Niobium Microsystems FHEsible NID application.

This example demonstrates several patterns relevant to FHE application design:
ML inference under encryption (autoencoder ensemble), Chebyshev polynomial
approximation of activation functions (sigmoid and tanh), feature-major SIMD
packing, plaintext model weights used as constants, and a streaming batch
protocol for processing arbitrarily large datasets.

## Architecture Overview

The system has three tiers and five stages:

| Tier | Language | Role |
|------|----------|------|
| Training (offline) | Python | Train KitNET model on normal traffic |
| Client (CPU) | Python + OpenFHE-Python | Extract features, encrypt, decrypt results |
| Server (FHE) | C++ + OpenFHE | Run neural network on encrypted data |

| Stage | Description |
|-------|-------------|
| Model training | Client trains Kitsune autoencoder ensemble on plaintext traffic (offline) |
| Key generation | Client generates CKKS context, keys, serializes to files |
| Feature encryption | Client extracts features from pcap, normalizes, batches 32K packets, encrypts |
| Encrypted inference | Server loads model + encrypted features, runs KitNET under FHE |
| Decryption & analysis | Client decrypts anomaly scores, applies threshold, visualizes |

The client and server communicate via file exchange (serialized ciphertexts on
disk), coordinated by a pipe-based protocol. This decouples the Python client
from the C++ server and allows the server to be replaced with a GPU or hardware
accelerator implementation.

## Privacy Model

**Parties:** A client organization that holds private network traffic data, and
a centralized security service (server) that performs anomaly detection.

**Threat model:** Semi-honest server. The server performs the computation
correctly but might try to learn about the client's network behavior. The
client's network traffic patterns, IP addresses, connection metadata, and
anomaly scores must remain private from the server.

**Key insight:** The trained model (autoencoder weights and biases) is *not*
private — it encapsulates average traffic behavior patterns, not specific
connections or addresses. The model is provided to the server in plaintext.
This is a critical design decision: keeping the model in plaintext avoids
the enormous cost of encrypted model parameters and enables efficient
ciphertext-plaintext operations.

**Output:** The client receives encrypted anomaly scores (one MSE value per
packet). The client decrypts and applies a threshold to classify packets as
normal or anomalous. The server never sees the scores.

## KitNET Neural Network Architecture

KitNET is an ensemble-based anomaly detector with two stages:

```
Input features (50 per packet)
    │
    ├─→ Autoencoder 0: features[map[0]] → hidden → reconstruct → residual
    ├─→ Autoencoder 1: features[map[1]] → hidden → reconstruct → residual
    ├─→ ...
    └─→ Autoencoder 9: features[map[9]] → hidden → reconstruct → residual
         │
         └─→ Concatenate all residuals (50 values)
              │
              └─→ Anomaly Detector: residuals → hidden → reconstruct → MSE score
```

Each autoencoder is a 3-layer perceptron (encoder-decoder) with:
- Visible dimension: 5 (number of features per autoencoder, for MINI) or 10
- Hidden dimension: 3 (compressed representation)
- Activation: sigmoid (input range [0, 1])

The anomaly detector is also a 3-layer perceptron:
- Visible dimension: 50 (concatenated residuals from all autoencoders)
- Hidden dimension: variable
- Activation: tanh (input range [-1, 1])

The MSE of the anomaly detector's reconstruction error is the anomaly score.
Higher MSE indicates the packet deviates from learned normal behavior.

## Key Design Pattern: Feature-Major SIMD Packing

Each ciphertext holds a single feature across all 32,768 packets in a batch:

```
Ciphertext for feature f:
  slot 0: feature f of packet 0
  slot 1: feature f of packet 1
  ...
  slot 32767: feature f of packet 32767
```

With 50 features, one batch requires 50 ciphertexts. This layout means:
- A single ciphertext-plaintext multiply applies one model weight to all
  packets simultaneously
- Linear combinations (matrix-vector products) operate across feature
  ciphertexts, accumulating into a single result ciphertext
- The final MSE score ciphertext has one anomaly score per slot per packet

This is the same column-major packing pattern used in the set-membership
example, but applied to ML inference rather than matching.

## Key Design Pattern: Linear Combination Without Rotations

The autoencoder's matrix-vector product is implemented without rotations.
Each weight column is applied as a scalar multiply against the corresponding
feature ciphertext, then accumulated by addition:

```cpp
static Ciphertext<DCRTPoly> LinComb_NoRotate(
    const CryptoContext<DCRTPoly>& cc,
    const std::vector<Ciphertext<DCRTPoly>>& X,  // feature ciphertexts
    const std::vector<double>& w,                  // weight column (plaintext)
    double b) {                                    // bias (plaintext)

    Ciphertext<DCRTPoly> acc = cc->EvalMult(X[0], w[0]);
    for (size_t i = 1; i < w.size(); ++i)
        acc = cc->EvalAdd(acc, cc->EvalMult(X[i], w[i]));
    if (b != 0.0)
        acc = cc->EvalAdd(acc, b);
    return acc;
}
```

This avoids rotation keys entirely for the linear algebra. The ciphertext-
plaintext multiplies (EvalMult with a scalar) are much cheaper than
ciphertext-ciphertext multiplies and consume no multiplicative depth in
CKKS with FLEXIBLEAUTO scaling.

## Key Design Pattern: Chebyshev Activation Functions

Sigmoid and tanh activations cannot be computed exactly on encrypted data.
The application approximates them using Chebyshev polynomial series via
OpenFHE's `EvalChebyshevSeries`:

```cpp
// Compute Chebyshev coefficients at initialization (once)
auto sigCoeffs = EvalChebyshevCoefficients(sigmoid, -5.0, 5.0, 5);
auto tanhCoeffs = EvalChebyshevCoefficients(tanh, -2.0, 2.0, 5);

// Apply during encrypted inference
auto activated = cc->EvalChebyshevSeries(linearOutput, sigCoeffs, -5.0, 5.0);
```

Key considerations:
- **Degree 5 polynomials** are used for both sigmoid (range [-5, 5]) and
  tanh (range [-2, 2]). This is a low degree chosen to minimize depth cost.
- Chebyshev coefficients are computed using OpenFHE's
  `EvalChebyshevCoefficients` function rather than external tools, ensuring
  coefficient format compatibility.
- Sigmoid is used for autoencoders (inputs normalized to [0, 1] range).
  Tanh is used for the anomaly detector (inputs in [-1, 1] range).
- The degree-5 approximation introduces ~3% error in the full model output,
  which is acceptable for anomaly detection (the threshold is coarse).

## Homomorphic Circuit

Each autoencoder performs:
1. **Hidden layer:** For each hidden unit j: linear combination of input
   features weighted by column j of W, plus bias → sigmoid activation.
   Cost: 1 level (Chebyshev on degree-5 polynomial).
2. **Reconstruction layer:** For each visible unit i: linear combination of
   hidden units weighted by row i of W, plus bias → sigmoid activation.
   Cost: 1 level (Chebyshev).
3. **Residual:** input - reconstruction. Cost: 0 levels (subtraction is free).

The anomaly detector performs the same structure with tanh activations.

**Final MSE computation:**
```cpp
auto diff = cc->EvalSub(R_ct[i], ZN_ct[i]);      // residual
sumsq = cc->EvalAdd(sumsq, cc->EvalMult(diff, diff));  // squared error
auto mse = cc->EvalMult(sumsq, 1.0 / Vn);         // mean
```

Total multiplicative depth: ~22 levels (dominated by Chebyshev evaluations
across the autoencoder ensemble and anomaly detector layers).

## CKKS Parameters

```cpp
CCParams<CryptoContextCKKSRNS> parameters;
parameters.SetSecretKeyDist(UNIFORM_TERNARY);
parameters.SetSecurityLevel(HEStd_128_classic);
parameters.SetMultiplicativeDepth(22);
parameters.SetScalingModSize(54);
parameters.SetScalingTechnique(FLEXIBLEAUTO);
parameters.SetRingDim(65536);       // N = 2^16
parameters.SetBatchSize(32768);     // N/2 = 2^15 SIMD slots
```

Notable choices:
- **54-bit scaling modulus**: slightly higher than the 50-bit used in the
  set-membership example, providing more precision per level.
- **22-level depth budget**: driven by the Chebyshev evaluations across
  multiple autoencoder layers plus the anomaly detector.
- **ADVANCEDSHE enabled**: required for `EvalChebyshevSeries`.
- **No rotation keys needed**: the linear combination pattern uses
  ciphertext-plaintext operations, avoiding rotations entirely.

With 54-bit scaling and 22 levels, each ciphertext is approximately:
64K (ring dimension) × 2 × 22 (levels) × 8 bytes ≈ 22 MB.
50 feature ciphertexts per batch ≈ 1.1 GB total ciphertext load.

## Streaming Batch Protocol

The application processes data in batches of 32,768 packets. Each batch
follows a complete encrypt → compute → decrypt cycle:

1. Client reads next 32K packets from pcap, extracts features, normalizes
2. Client encrypts 50 feature ciphertexts, writes to shared directory
3. Client signals server via pipe ("data ready")
4. Server deserializes ciphertexts, runs KitNET FHE, writes encrypted scores
5. Server signals client via pipe ("scores ready")
6. Client reads and decrypts scores, stores results
7. Repeat for next batch (same keys reused across batches)

Partial batches at end-of-file are discarded (not processed). The client
paces batch delivery so the server never needs to buffer multiple batches.

Keys are generated once and reused across all batches in a session. This is
the streaming pipeline pattern — distinct from benchmark patterns where keys
are regenerated per run.

## Performance

| Phase | MINI (5 features) | FULL (50 features) |
|-------|-------------------|---------------------|
| Key generation | ~1-2 s | ~1-2 s |
| Feature encryption | seconds | seconds |
| FHE inference (CPU) | ~25 min | ~5 hours |
| Decryption | ~15 min | ~2.5 hours |
| FHE output error | ~25% relative | ~3% relative |

The MINI profile uses only 5 of 50 features with a model trained on 50,
causing architectural mismatch and higher error. Both error rates are
acceptable for demonstrating FHE correctness.

## Project Structure

```
├── Server/
│   ├── CMakeLists.txt           # OpenFHE linkage, build targets
│   ├── Kitnet.h / Kitnet.cpp    # KitNET ensemble: plaintext + FHE paths
│   ├── dA.h / dA.cpp            # Autoencoder: weights, Chebyshev activation
│   ├── Packetdata.h / .cpp      # Batch feature array (plaintext helper)
│   ├── server_process.cpp       # GPU-accelerated client-server FHE server
│   └── server_standalone.cpp    # Standalone server (compiler instrumentation)
├── src/
│   ├── keygen.cpp               # Profile-aware CKKS key generation
│   ├── encrypt_mirai.cpp        # Batch feature encryption (TOY/MINI/FULL)
│   ├── decrypt_probe.cpp        # Result decryption and validation
│   └── verify_replay_output.cpp # 3-step verification workflow
├── KitNET/                      # Python KitNET training package
├── Features/                    # Python feature extraction (from Kitsune)
├── training/                    # Model training scripts
├── Datasets/                    # Curated network traces (Mirai, Fuzzing, etc.)
├── run_workload.sh              # End-to-end workflow driver
└── WORKFLOWS.md                 # Execution guide for MINI/FULL profiles
```

The dual-path architecture (Python client + C++ server) reflects the real
deployment model: the client runs wherever the network data lives, the server
runs on FHE-capable hardware. The `execute_ckks` method in `Kitnet.cpp`
mirrors the plaintext `execute` method, enabling direct comparison.

## Comparison with Other Examples

| Aspect | Set Membership | Fetch by Similarity | Network Intrusion |
|--------|---------------|--------------------|--------------------|
| Domain | Name matching | Database search | ML inference |
| Model | None (direct comparison) | None (cosine similarity) | Neural network (KitNET) |
| Comparison method | Squared distance + iterated squaring | Chebyshev sigmoid | Chebyshev sigmoid + tanh |
| SIMD packing | Column-major (names) | Column-major (records) | Feature-major (packets) |
| Rotation keys | Powers of 2 (rotate-and-sum) | Full replication tree | None needed |
| Depth | 16-18 | 23 | 22 |
| Data volume | Small (hundreds of names) | Large (millions of records) | Streaming (32K packet batches) |
| Protocol | Single request-response | Single computation | Streaming batches |
| Model weights | N/A | N/A | Plaintext (not private) |

## DSL Implementation

A complete implementation of this design exists in the `nb` FHE DSL:
`niobium-client/dsl_fhe/examples/fhe-NetworkMonitor/`. The autoencoder
ensemble and anomaly detector are shared functions in `server.niob` with
Chebyshev-approximated sigmoid/tanh activations; the binary KitNET model
loader is generated from the `KitNETModel` struct declaration; feature-major
packing is one ciphertext per feature column. The default build runs against
generated stub model/assets (`make test-nid`); real detection uses the trained
model from the submission package.
