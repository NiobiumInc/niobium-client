# Worked Example: CKKS Private Set Membership for Names

This reference describes a complete FHE application: private name matching
using CKKS. A client holds a private name (the query) and a server holds a
private dataset of names. The system determines whether the query name appears
in the dataset, returning a Boolean result, without revealing the query to the
server or the dataset to the client.

Source repository: https://github.com/davearcher/openfhe-set-membership

## Architecture (Top-Down)

The system is organized in four layers:

```
┌──────────────────────────────────────────────────────┐
│  Layer 4: Protocol                                   │
│  Client encrypts query → Server evaluates circuit    │
│  → Client decrypts boolean result                    │
├──────────────────────────────────────────────────────┤
│  Layer 3: Homomorphic Circuit                        │
│  Squared-distance computation + iterated-squaring    │
│  indicator + SIMD aggregation                        │
├──────────────────────────────────────────────────────┤
│  Layer 2: SIMD Data Layout                           │
│  Column-major packing: one ciphertext per character  │
│  position, one SIMD slot per dataset name            │
├──────────────────────────────────────────────────────┤
│  Layer 1: Name Encoding                              │
│  Phonetic hashing (Soundex) or case-insensitive      │
│  letter encoding → fixed-length integer vectors      │
└──────────────────────────────────────────────────────┘
```

## Layer 1: Name Encoding

Names are reduced to fixed-length integer vectors. Letters a–z map to 1–26
(case-insensitive), zero-padded to fixed length. Two matching modes:

- **Exact mode**: encodes the name character-by-character, length L=20.
- **Soundex mode**: applies Soundex phonetic hashing first, producing a
  4-character code (e.g., "Robert" → R163), then encodes that, length L=4.

Soundex moves fuzzy matching into a plaintext preprocessing step, reducing
the encrypted computation to exact matching on short codes. This sidesteps
the need for edit distance under encryption, which would require TFHE-specific
features not available in CKKS.

## Layer 2: SIMD Data Layout

Uses column-major packing: for a dataset of names, each ciphertext holds one
character position across all names.

- `datasetColumns[pos][batch]` is a plaintext whose slot j holds the encoded
  character at position `pos` of name `(batch * batchSize + j)`.
- For L character positions and B batches, the server holds L × B plaintexts.
- The client encrypts L ciphertexts, each with the query's character value
  replicated across all slots.

At ring dimension N = 2^16, each ciphertext holds 32,768 SIMD slots. Datasets
larger than 32,768 names automatically split into multiple batches.

This layout means a single ciphertext-plaintext subtraction computes the
per-character difference for all names in a batch simultaneously.

## Layer 3: Homomorphic Circuit

### Why Not BFV Equality Polynomials

The standard BFV equality polynomial (Fermat-style or product polynomial)
relies on exact modular arithmetic. In CKKS, intermediate products reach
magnitudes of ~10^14, drowning the CKKS noise floor (~10^-7). This is a
fundamental incompatibility, not a tuning issue.

### Squared Euclidean Distance

Instead of per-character equality, compute:

```
S_j = Σ_{i=0}^{L-1} (query[i] − dataset[j][i])²
```

If the query matches name j, S_j = 0. If they differ in any position, S_j ≥ 1.
This requires only one level of multiplicative depth (one ct × ct multiply
for squaring, plus additions which are free).

### Iterated-Squaring Indicator

After normalizing s_j = S_j / C (where C = MAX_CHAR_VAL² × L):

```
t_j = (1 − s_j)^{2^K}
```

computed through K rounds of squaring (t ← t × t).

- Match: s_j = 0, so t_j = 1 (exactly preserved)
- Non-match: t_j decays exponentially toward 0

K is determined by: 2^K > C · ln(1/ε), with ε = 0.01.

| Mode    | L  | C      | K  | Total Depth (2+K) |
|---------|----|--------|----|--------------------|
| Soundex | 4  | 2,704  | 14 | 16                 |
| Exact   | 20 | 13,520 | 16 | 18                 |

### Full Circuit

| Step | Operation | Depth cost |
|------|-----------|------------|
| 1 | diff_i = query[i] − dataset[j][i] | 0 |
| 2 | S = Σ_i diff_i² | 1 |
| 3 | s = S / C | 1 |
| 4 | t = 1 − s | 0 |
| 5 | t = t^{2^K} via K squarings | K |
| 6 | sum = Σ_j t_j via rotate-and-sum | 0 |

Total multiplicative depth: **2 + K**.

## Layer 4: Protocol

**Client:** generateKeys() → encryptQuery(name) → send encrypted query + public key

**Server:** encodeDataset(names) → evaluate(encryptedQuery) → return result ciphertext

**Client:** decryptResult(encryptedResult) → threshold at 0.5 → MATCH or NO MATCH

## CKKS Parameters

- Ring dimension: N = 2^16 (32,768 SIMD slots)
- Security: HEStd_128_classic (128-bit classical)
- Scaling mod size: 50 bits per level
- First mod size: 60 bits
- Scaling technique: FLEXIBLEAUTO
- Total modulus chain: ~960 bits for exact mode

## OpenFHE API Patterns Used

**Key generation:**
```cpp
CCParams<CryptoContextCKKSRNS> params;
params.SetMultiplicativeDepth(multDepth);
params.SetScalingModSize(50);
params.SetFirstModSize(60);
params.SetSecurityLevel(HEStd_128_classic);
params.SetScalingTechnique(FLEXIBLEAUTO);
auto cc = GenCryptoContext(params);
cc->Enable(PKE); cc->Enable(KEYSWITCH); cc->Enable(LEVELEDSHE);
auto keyPair = cc->KeyGen();
cc->EvalMultKeyGen(keyPair.secretKey);
// Rotation keys for powers of 2 (rotate-and-sum)
cc->EvalRotateKeyGen(keyPair.secretKey, rotationIndices);
```

**Encrypt with slot replication:**
```cpp
std::vector<double> rep(batchSize, queryValue);
auto pt = cc->MakeCKKSPackedPlaintext(rep);
auto ct = cc->Encrypt(keyPair.publicKey, pt);
```

**Homomorphic evaluation core loop:**
```cpp
auto diff = cc->EvalSub(encQuery[pos], datasetPlaintext);
auto sq = cc->EvalMult(diff, diff);           // depth 1
auto s = cc->EvalMult(S, 1.0 / normConst);    // depth 2
auto t = cc->EvalAdd(cc->EvalNegate(s), 1.0);  // 1 - s
for (int round = 0; round < K; ++round)
    t = cc->EvalMult(t, t);                    // iterated squaring
```

**Rotate-and-sum aggregation:**
```cpp
auto acc = ct;
for (int r = 1; r < batchSize; r *= 2) {
    auto rotated = cc->EvalRotate(acc, r);
    acc = cc->EvalAdd(acc, rotated);
}
```

## Project Structure

```
├── CMakeLists.txt          # find_package(OpenFHE), link shared libs
├── src/
│   ├── phonetic.h          # Soundex hashing, character encoding
│   ├── fhe_string_matcher.h   # Class declaration, architecture docs
│   ├── fhe_string_matcher.cpp # CKKS key gen, encoding, evaluation, decryption
│   └── main.cpp            # CLI entry point with phase timing
├── tests/
│   └── test_matcher.cpp    # Integration tests (17 assertions)
├── data/                   # Datasets (sample, notable people, OFAC)
└── docs/
    └── DESIGN.md           # Full design specification
```

## Threat Model Summary

| Property | Guarantee | Assumption |
|----------|-----------|------------|
| Query confidentiality (vs. server) | Semantic security | RLWE hardness, 128-bit |
| Dataset confidentiality (vs. client) | Output-only leakage | Semi-honest client |
| Match result | Revealed to client | Intentional |
| Dataset size | Approximate leakage (batch count) | Mitigable with padding |

Threats NOT addressed: malicious adversaries, side-channel attacks, Soundex
preprocessing visibility on client machine, exhaustive query attacks (mitigable
with rate limiting), collusion.

## Performance (188 names, 1 batch, single-threaded)

| Phase | Exact mode | Soundex mode |
|-------|-----------|--------------|
| Key generation | ~1.3 s | ~1.2 s |
| Dataset encoding | ~0.16 s | ~0.03 s |
| Query encryption | ~1.3 s | ~0.23 s |
| Evaluation | ~2.0 s | ~0.8 s |
| Decryption | ~0.01 s | ~0.01 s |

Evaluation time scales linearly with the number of batches.

## DSL Implementation

A complete, tested implementation of this design exists in the `nb` FHE DSL:
`niobium-client/dsl_fhe/examples/set-membership/` (~180 lines of `.nb` plus a
small Python encoding harness). Design decision → code location:

| Design decision (this document) | DSL code |
|---|---|
| Name/Soundex encoding (Layer 1) | `harness/encode_names.py` — plaintext preprocessing, outside the circuit |
| Column-major packing (Layer 2) | `client.nb` `encrypt_query` — one ciphertext per character position via `qmat[0..n_slots, pos]` column slices |
| Squared distance + iterated squaring (Layer 3) | `server.nb` `compute` — `diff * diff` accumulation, `1 - acc/C`, K-round squaring loop |
| Slot aggregation (Layer 3) | `ct_t \|> slot_sum(inst.n_slots)` |
| Depth budget 2 + K | `Instance.depth` per profile + `scheme.override(depth: inst.depth)` |
| Exact vs. Soundex profiles | `Profile` enum: Exact (L=20, K=16, depth 18), Soundex (L=4, K=14, depth 16) |
| Four-program protocol (Layer 4) | `@stage` functions: `key_generation`, `encrypt_query`, `compute`, `decrypt_verify` |

Verified end-to-end (`make test-set-membership`): exact match → score 1.000,
no match → 7e-15, Soundex fuzzy "Robbrt Johnson" → 10.021 against 10 expected
collisions.
