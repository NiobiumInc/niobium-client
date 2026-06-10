# OpenFHE Examples Catalog

This catalog maps common FHE design patterns to specific examples in the
OpenFHE library and documentation. All example source files are located under
`src/pke/examples/` in the OpenFHE repository at
https://github.com/openfheorg/openfhe-development.

## CKKS Basic Encryption and Arithmetic

**Example:** `simple-real-numbers.cpp`

Demonstrates CKKS encryption of real-valued vectors, addition, multiplication,
and rotation. This is the starting point for any CKKS application.

Key API calls shown:
- `CCParams<CryptoContextCKKSRNS>` — parameter object for CKKS
- `GenCryptoContext(params)` — create crypto context from parameters
- `cc->Enable(PKE)`, `cc->Enable(KEYSWITCH)`, `cc->Enable(LEVELEDSHE)`
- `cc->KeyGen()` — generate key pair
- `cc->EvalMultKeyGen(secretKey)` — generate relinearization keys
- `cc->MakeCKKSPackedPlaintext(vector)` — encode real vector into plaintext
- `cc->Encrypt(publicKey, plaintext)` — encrypt
- `cc->EvalAdd`, `cc->EvalMult`, `cc->EvalSub` — homomorphic operations
- `cc->Decrypt(secretKey, ciphertext, &result)` — decrypt

## CKKS Rotation (EvalRotate)

**Example:** `simple-real-numbers.cpp` (includes rotation), also demonstrated
in the set-membership and fetch-by-similarity worked examples.

Key API calls:
- `cc->EvalRotateKeyGen(secretKey, {1, 2, -1, -2})` — generate rotation keys
  for specific shift amounts
- `cc->EvalRotate(ciphertext, shiftAmount)` — rotate SIMD slots

**Rotate-and-sum pattern** (from worked examples):
```cpp
// Collapse N slots into slot 0 by rotating and adding
auto acc = ct;
for (int r = 1; r < batchSize; r *= 2) {
    auto rotated = cc->EvalRotate(acc, r);
    acc = cc->EvalAdd(acc, rotated);
}
```
Requires rotation keys for each power of 2 up to batchSize/2.

## CKKS Chebyshev Polynomial Evaluation

**Example:** `function-evaluation.cpp`
**Documentation:** `FUNCTION_EVALUATION.md` (in same directory)

Demonstrates `EvalChebyshevFunction` for evaluating arbitrary smooth functions
on encrypted data. Shows the logistic function and square root as examples.

Key API calls:
- `cc->Enable(ADVANCEDSHE)` — required for Chebyshev evaluation
- `cc->EvalChebyshevFunction(func, ct, lowerBound, upperBound, degree)` —
  evaluate a function via Chebyshev approximation
- `cc->EvalLogistic(ct, a, b, degree)` — built-in logistic function
- `EvalChebyshevCoefficients(func, a, b, degree)` — compute coefficients
  (useful for pre-computing and reusing across multiple evaluations)
- `cc->EvalChebyshevSeries(ct, coeffs, a, b)` — evaluate using pre-computed
  coefficients

**Chebyshev Degree to Multiplicative Depth Table:**

| Degree | Multiplicative Depth |
|--------|---------------------|
| 3–5 | 4 |
| 6–13 | 5 |
| 14–27 | 6 |
| 28–59 | 7 |
| 60–119 | 8 |
| 120–247 | 9 |
| 248–495 | 10 |
| 496–1007 | 11 |
| 1008–2031 | 12 |

Note: if the input range is (-1, 1), depth is 1 less than shown. This table
is critical for depth budgeting — choosing the Chebyshev degree directly
determines the multiplicative depth cost.

**Patterns from worked examples:**
- Set-membership: uses iterated squaring (not Chebyshev) for comparison
- Fetch-by-similarity: uses degree 59/119/247 Chebyshev for sigmoid threshold
  and Gaussian impulse (7–9 levels of depth per evaluation)
- Network intrusion detection: uses degree 5 Chebyshev for sigmoid and tanh
  activation functions (4 levels per evaluation, applied multiple times)

## CKKS Bootstrapping

**Example:** `simple-ckks-bootstrapping.cpp`
**Example:** `iterative-ckks-bootstrapping.cpp`

Bootstrapping refreshes the noise budget of a ciphertext, enabling computation
beyond the initial multiplicative depth. It is expensive (both in computation
time and in the depth it consumes) and should be used only when the circuit
depth genuinely exceeds what can be supported without it.

Key API calls:
- `cc->Enable(FHE)` — required for bootstrapping
- `cc->EvalBootstrapSetup(levelBudget, dim1, slots, correctionFactor)`
- `cc->EvalBootstrapKeyGen(secretKey, slots)`
- `cc->EvalBootstrap(ciphertext)` — refresh noise budget

## BFV Basic Encryption and Arithmetic

**Example:** `simple-integers.cpp`

Demonstrates BFV encryption of integer vectors, addition, multiplication, and
rotation. BFV provides exact modular arithmetic (no approximation error).

Key differences from CKKS setup:
- `CCParams<CryptoContextBFVRNS>` — parameter object for BFV
- `params.SetPlaintextModulus(65537)` — sets the plaintext modulus (integers
  are computed modulo this value)
- `cc->MakePackedPlaintext(vector)` — encode integer vector
- BFV packs N SIMD slots per ciphertext (vs. N/2 for CKKS)

## BGV Basic Operations

**Example:** `simple-integers-bgvrns.cpp`

BGV is similar to BFV with modular integer arithmetic. The API is analogous:
- `CCParams<CryptoContextBGVRNS>` — parameter object for BGV
- Otherwise same Enable/KeyGen/Encrypt/Eval pattern as BFV

BGV is largely superseded by BFV for new development in OpenFHE.

## Key Generation Patterns

**Key generation** (from all examples):
```cpp
auto cc = GenCryptoContext(params);
cc->Enable(PKE);
cc->Enable(KEYSWITCH);
cc->Enable(LEVELEDSHE);

auto keyPair = cc->KeyGen();
cc->EvalMultKeyGen(keyPair.secretKey);  // relinearization keys

// Rotation keys — specify every rotation amount your circuit needs
std::vector<int> rotIndices = {1, 2, 4, 8, 16, ...};
cc->EvalRotateKeyGen(keyPair.secretKey, rotIndices);
```

Important: missing rotation keys cause runtime errors. Generate keys for
every rotation amount used in the circuit.

For Chebyshev evaluation, also enable:
```cpp
cc->Enable(ADVANCEDSHE);
```

## Serialization and Deserialization

**Example:** `simple-integers-serial.cpp`

Demonstrates serializing and deserializing the crypto context, keys, and
ciphertexts to/from files. Essential for client-server architectures where
keys and ciphertexts are exchanged via files or network.

Key API calls:
```cpp
#include "cryptocontext-ser.h"
#include "ciphertext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

// Serialize crypto context
Serial::SerializeToFile(path, cc, SerType::BINARY);

// Deserialize crypto context
CryptoContext<DCRTPoly> cc;
Serial::DeserializeFromFile(path, cc, SerType::BINARY);

// Serialize/deserialize keys
Serial::SerializeToFile(path, keyPair.publicKey, SerType::BINARY);
Serial::SerializeToFile(path, keyPair.secretKey, SerType::BINARY);

// Eval mult keys (relinearization) use stream-based serialization
std::ofstream emkeyfile(path, std::ios::out | std::ios::binary);
cc->SerializeEvalMultKey(emkeyfile, SerType::BINARY);

std::ifstream emkeyfile(path, std::ios::in | std::ios::binary);
cc->DeserializeEvalMultKey(emkeyfile, SerType::BINARY);

// Serialize/deserialize ciphertexts
Serial::SerializeToFile(path, ciphertext, SerType::BINARY);
Serial::DeserializeFromFile(path, ciphertext, SerType::BINARY);
```

After deserializing a crypto context, re-enable required features:
```cpp
cc->Enable(PKE);
cc->Enable(KEYSWITCH);
cc->Enable(LEVELEDSHE);
cc->Enable(ADVANCEDSHE);  // if using Chebyshev
```

## Multi-Party (Threshold) FHE

**Example:** `threshold-fhe.cpp`
**Example:** `tckks-interactive-mp-bootstrapping-Chebyshev.cpp`

Threshold FHE allows the secret key to be split across multiple parties so
that no single party can decrypt alone. OpenFHE supports two modes:

- `NOISE_FLOODING_MULTIPARTY` — enhanced security with additional noise
  flooding (recommended)
- `FIXED_NOISE_MULTIPARTY` — fixed noise, faster but less secure

The threshold examples demonstrate multi-party key generation, collective
decryption, and (for TCKKS) interactive bootstrapping.

## Parameter Selection Quick Reference

**CKKS typical parameters:**

| Use case | Ring dim | Mult depth | Scaling mod | Security |
|----------|---------|------------|-------------|----------|
| Simple arithmetic (few mults) | 2^15 | 3–5 | 50 | HEStd_128_classic |
| Moderate (polynomial eval) | 2^16 | 10–15 | 50 | HEStd_128_classic |
| Deep (ML inference, Chebyshev) | 2^16 | 18–25 | 50–54 | HEStd_128_classic |
| With bootstrapping | 2^16+ | varies | 50 | HEStd_128_classic |

**Scaling technique:** Use `FLEXIBLEAUTO` for automatic rescaling. This
handles level alignment automatically and prevents subtle level-mismatch bugs.

**Security levels:**
- `HEStd_128_classic` — 128-bit classical security (standard choice)
- `HEStd_192_classic` — 192-bit classical security
- `HEStd_256_classic` — 256-bit classical security
- `HEStd_NotSet` — no security enforcement (development/testing only)

If your parameters don't achieve the target security level, OpenFHE will
automatically promote the ring dimension to the smallest value that does.

## Resources

- OpenFHE documentation: https://openfhe.org/documentation/
- OpenFHE GitHub: https://github.com/openfheorg/openfhe-development
- OpenFHE ReadTheDocs: https://openfhe-development.readthedocs.io/
- Awesome OpenFHE: https://github.com/openfheorg/awesome-openfhe
- OpenFHE webinars: https://openfhe.org/webinars/
- OpenFHE discourse forum: https://openfhe.discourse.group/
- OpenFHE examples directory: https://github.com/openfheorg/openfhe-development/tree/main/src/pke/examples
