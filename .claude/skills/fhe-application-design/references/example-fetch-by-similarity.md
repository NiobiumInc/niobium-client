# Worked Example: CKKS Fetch-by-Cosine-Similarity Benchmark

This reference describes a more complex FHE application: a benchmark from
HomomorphicEncryption.org for cosine-similarity search over an encrypted
database. Given an encrypted query vector and an encrypted database of records,
find all records whose cosine similarity to the query exceeds a threshold, then
retrieve their associated payloads.

Source repository: https://github.com/fhe-benchmarking/fetch-by-similarity

This example demonstrates several advanced CKKS patterns not present in the
simpler set-membership example: Chebyshev polynomial approximation for
comparison, slot replication for matrix-vector products, running sums across
ciphertext slots, output compression for payload retrieval, and serialization
of keys and ciphertexts.

## Architecture Overview

The benchmark follows a client-server protocol with clear stage separation:

| Stage | Description |
|-------|-------------|
| Key generation | Client generates CKKS context, keys, rotation keys |
| DB preprocessing | Client preprocesses and encrypts the database |
| Query encryption | Client preprocesses and encrypts the query vector |
| Encrypted computation | Server performs similarity search + payload retrieval |
| Decryption | Client decrypts and postprocesses results |

The encrypted computation on the server has four phases:
1. Matrix-vector product (cosine similarity computation)
2. Compare to threshold (Chebyshev approximation of indicator function)
3. Running sums (to index matches within columns)
4. Output compression (extract and pack payload data for matches)

## CKKS Parameters

```cpp
CCParams<CryptoContextCKKSRNS> cParams;
cParams.SetSecretKeyDist(UNIFORM_TERNARY);
cParams.SetKeySwitchTechnique(HYBRID);
cParams.SetMultiplicativeDepth(23);
cParams.SetSecurityLevel(HEStd_128_classic);
cParams.SetScalingTechnique(FLEXIBLEAUTO);
cParams.SetScalingModSize(42);
cParams.SetFirstModSize(57);
```

Notable differences from the set-membership example:
- **42-bit scaling mod** (vs. 50-bit): tighter precision budget, trading
  precision for smaller ciphertexts and faster operations.
- **UNIFORM_TERNARY secret key**: slightly different security/performance
  tradeoff compared to the default.
- **HYBRID key switching**: more efficient for large key-switching operations.
- **23-level depth budget**: supports the full pipeline including Chebyshev
  evaluations.
- **ADVANCEDSHE enabled**: required for EvalChebyshevFunction and
  EvalSumRows operations.

With 42-bit scaling and 57-bit first modulus, usable precision is approximately
12–13 bits per slot value.

## Key Design Pattern: Chebyshev Polynomial Approximation

For threshold comparison (is similarity ≥ 0.8?), the application uses
OpenFHE's `EvalChebyshevFunction` to approximate a sigmoid-like indicator:

```cpp
// Sigmoid-like function, inscale constant determined by experiments
double sigmoid(double x, double outscale, double inscale = 69.0) {
    return outscale / (1.0 + std::exp(-(x * inscale)));
}

auto func = [threshold, outscale](double x) {
    return sigmoid(x - threshold, outscale);
};
size_t degree = 59;  // options: 59, 119, 247
ct = cc->EvalChebyshevFunction(func, ct, -1.0, 1.0, degree);
```

Key considerations:
- Input range must be normalized to [-1, 1] for Chebyshev to work correctly.
- Higher degree = better approximation but more depth consumed.
- The degree choice (59 vs. 119 vs. 247) depends on how much depth budget
  remains and how accurate the approximation needs to be.
- For count-only mode (just counting matches, not retrieving payloads), a
  higher degree (247) is used because accuracy matters for both matches and
  non-matches and there's more depth budget available.
- For payload retrieval mode, a lower degree (59) is used because there's
  less depth budget and non-match accuracy matters more than match accuracy.

For equality comparison (is running sum == i?), a Gaussian impulse function
is used instead:

```cpp
double impulse(double x, double sigma = 0.04) {
    double x_over_sigma = x / sigma;
    return std::exp(-x_over_sigma * x_over_sigma / 2);
}
```

## Key Design Pattern: Slot Replication

For the matrix-vector product, each element of the query vector must be
replicated to fill an entire ciphertext so it can be multiplied against a
row of the encrypted database matrix.

The application uses a tree-based DFS slot replicator that:
- Traverses a replication tree depth-first to minimize memory usage
- Uses "hoisting" for nodes with degree > 2 (precompute the expensive
  part of rotation once, reuse for multiple rotation amounts)
- Returns replicated ciphertexts one at a time via an iterator interface

```cpp
DFSSlotReplicator replicator(cc, degrees, n_reps);
for (auto ct_i = replicator.init(qry); ct_i != nullptr;
     ct_i = replicator.next_replica(), i++) {
    // ct_i has the i'th query element in all slots
    // multiply against database rows and accumulate
}
```

The tree shape (specified as a vector of degrees, e.g., {8, 4, 4} for
dimension 128) affects performance but not correctness. Optimal shapes
depend on hardware.

## Key Design Pattern: Deferred Relinearization

In the matrix-vector product, multiplications are performed without
relinearization (EvalMultNoRelin), and relinearization is done once
after accumulation:

```cpp
ct = cc->EvalMultNoRelin(ct, ct_i);  // cheaper multiply
// ... accumulate many such products ...
cc->RelinearizeInPlace(acc[j]);  // relinearize once at the end
```

This saves one relinearization per accumulated product, which is significant
when the dimension is large (128–512 products accumulated).

## Key Design Pattern: Running Sums

To index which match is the 1st, 2nd, 3rd, etc. in each column (needed for
payload extraction), the application computes running sums across SIMD slots.

The RunningSums class views ciphertext slots as a matrix with configurable
stride (number of columns), and computes column-wise prefix sums using a
shift-and-add algorithm with configurable depth budget.

After running sums, slots that were 1 (match) become their ordinal position
(1st match = 1, 2nd match = 2, etc.), enabling payload extraction by
comparing against each ordinal value.

## Key Design Pattern: Output Compression

Retrieving payloads for matches requires packing data from scattered positions
across multiple ciphertexts into a compact output format. The algorithm:

1. For each ordinal i (1st through 8th match):
   a. Compute an indicator ciphertext (1 where running sum == i, else 0)
   b. Multiply each payload dimension by the indicator to isolate that
      match's data
   c. Shift payload values to consecutive positions in their column
   d. Replicate across the column via total sums
   e. Mask to keep only the designated output positions
2. Accumulate all ordinals into a single output ciphertext

## Scale Factors at Instance Sizes

| Instance | Record dim | DB size | Ring dim | Batches |
|----------|-----------|---------|----------|---------|
| Toy | 128 | 1,000 | 1,024 | 2 |
| Small | 128 | 50,000 | 65,536 | 2 |
| Medium | 256 | 1,000,000 | 65,536 | 31 |
| Large | 512 | 20,000,000 | 65,536 | 611 |

## Performance (Toy instance, single machine)

- Key generation: ~0.18 s
- Database encryption: ~7.8 s
- Query encryption: ~0.05 s
- Encrypted computation: ~47–65 s (dominated by matrix-vector product)
- Decryption: ~0.03 s

## Project Structure

```
├── submission/
│   ├── CMakeLists.txt
│   ├── include/
│   │   ├── params.h              # Instance parameters, directory structure
│   │   ├── slot_replication.h    # DFS slot replicator
│   │   ├── running_sums.h        # Column-wise running sums
│   │   └── utils.h               # Timing, serialization helpers
│   └── src/
│       ├── client_key_generation.cpp
│       ├── client_encode_encrypt_db.cpp
│       ├── client_encode_encrypt_query.cpp
│       ├── server_encrypted_compute.cpp   # Core: mat-vec, threshold, compress
│       ├── client_decrypt_decode.cpp
│       └── client_postprocess.cpp
├── harness/                    # Python benchmark driver
│   ├── run_submission.py
│   ├── params.py
│   └── verify_result.py
└── measurements/               # Benchmark results (JSON)
```

The multi-executable structure (separate binaries for each stage) simulates
the client-server separation and enables independent timing measurement
of each phase.

## DSL Implementation

A complete implementation of this design exists in the `nb` FHE DSL:
`niobium-client/dsl_fhe/examples/fetch-by-similarity/` (~590 lines of `.niob`
replacing ~2,900 lines of C++). The Chebyshev threshold comparison, slot
replication, running sums, and payload extraction each map to dedicated
shared functions in `server.niob`; the harness (`harness/run.py`) generates the
dataset, orchestrates the pipeline, and verifies all extracted payloads
against a cleartext reference. Run with `make test-fetch`.
