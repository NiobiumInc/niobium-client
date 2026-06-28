# Choosing Your FHE Scheme: BGV, BFV, or CKKS

No single FHE scheme excels in every area, so different schemes have emerged, each optimizing for specific needs. This guide covers the three arithmetic FHE schemes supported by OpenFHE: BGV, BFV, and CKKS. (TFHE takes a fundamentally different approach based on logic circuits rather than arithmetic, and is out of scope here.)

## The Core Decision

The single most important question for scheme selection is:

**Does your computation need exact integer results, or can it tolerate small approximation errors on real-valued data?**

- If you need exact results on integer data → **BFV** (or BGV)
- If you're working with real-valued data and can absorb small errors → **CKKS**

That one question eliminates at least one scheme for most applications. The sections below provide the detail to make the final choice.

## BFV: The Balanced Workhorse

BFV, short for Brakerski-Fan-Vercauteren, is designed for exact arithmetic over integers. It's the most practical choice when your computation demands precise integer results.

**Key characteristics:**

- Supports exact modular integer computation — results are mathematically precise, not approximate.
- Uses leveled computation, with optional bootstrapping for deeper operations.
- SIMD support: packs N values into a single ciphertext (where N is the polynomial ring dimension), enabling parallel processing of thousands of data items simultaneously.
- Easier to work with in some scenarios due to its plaintext modulus design.

**When to use it:**

BFV is the right choice for applications that demand precise integer computations: financial analysis, private identity verification, secure voting, encrypted database lookups, or any setting where "close enough" isn't good enough. If you need exact integers, start with BFV.

## BGV: The Versatile Classic

BGV stands for Brakerski-Gentry-Vaikuntanathan, after the researchers who introduced it in 2011. BGV was one of the first practical FHE schemes and remains widely studied today, though it is seldom used for new applications now that BFV and CKKS have come along.

**Key characteristics:**

- Supports modular integer arithmetic on encrypted data, like BFV.
- Uses leveled FHE with modulus switching to control noise growth.
- SIMD support: packs N values per ciphertext, same as BFV.
- Performance characteristics are similar to BFV, with some differences in how noise is managed internally.

**When to use it:**

BGV covers the same use cases as BFV — encrypted statistics, private database queries, secure voting, and other exact-integer workloads. In practice, BFV has largely superseded BGV for new development. Choose BGV if you have a specific reason (existing codebase, particular research context, or a noise management strategy that benefits from BGV's modulus switching approach). Otherwise, default to BFV for exact integer work.

## CKKS: The Approximate Mathematician

The CKKS scheme (named after Cheon-Kim-Kim-Song) occupies different territory: approximate arithmetic on real numbers. It allows operations on decimal values, though with some loss in precision — similar to rounding in everyday math.

**Key characteristics:**

- Designed for approximate computations on real or complex numbers.
- Especially useful in machine learning, where small errors are tolerable.
- Efficient at vectorized operations, processing many numbers in parallel.
- Offers the highest throughput among FHE schemes for bulk numerical computation.
- SIMD support: packs N/2 values into a single ciphertext. Note that this is half the packing capacity of BFV/BGV for the same ring dimension — a practical consideration when planning data layout and throughput.

**When to use it:**

CKKS is the go-to scheme for privacy-preserving AI and ML: encrypted neural networks, homomorphic inference, and private data analytics. It's also the right choice in healthcare, finance, or scientific computing, where averages, trends, and predictions matter more than exact values. Any workload that naturally operates on real-valued data and can tolerate small approximation errors is a candidate for CKKS.

## Practical Tradeoffs

Beyond the exact-vs-approximate divide, there are practical differences that matter for real deployments:

**Memory footprint.** CKKS tends to use more memory than BFV or BGV at equivalent security levels and multiplicative depths. For memory-constrained environments (cloud VMs, containers), this is worth factoring into your choice. OpenFHE generally uses less memory than other libraries (such as Lattigo) across all schemes.

**Performance scaling with depth.** Execution time and memory usage increase roughly linearly with multiplicative depth for all three schemes. However, BGV and BFV tend to be faster than CKKS at equivalent depths when running on OpenFHE, with BGV holding a slight edge. Among all libraries, BGV in Lattigo has been measured as the fastest at all depths, though the library choice may be constrained by other factors.

**Precision budget.** In CKKS, every operation consumes some precision. The scaling modulus size (typically 40–50 bits per level) determines how many bits of precision you have to work with. If your application requires high precision across many sequential operations, you may need larger parameters, which increases memory and computation cost. BFV and BGV don't have this problem — their results are exact regardless of circuit depth (as long as you stay within the noise budget).

**Bootstrapping cost.** When the multiplicative depth of your circuit exceeds the leveled budget, bootstrapping is needed to refresh the noise. Bootstrapping is expensive in all three schemes, but the relative cost and maturity of bootstrapping implementations differs. For CKKS in OpenFHE, bootstrapping is well-supported and has been used in production applications. For BFV and BGV, bootstrapping support exists but is less commonly needed in practice, since many exact-integer workloads have shallow enough circuits to avoid it.

## Summary Decision Tree

1. **Are your inputs and outputs real-valued (floating point, decimals)?** → CKKS
2. **Do you need exact integer results?** → BFV (default) or BGV (if you have a specific reason)
3. **Is your workload heavy on vector inner products and batch processing?** → CKKS has the highest throughput here
4. **Are you memory-constrained?** → BFV or BGV will use less memory than CKKS
5. **Is your circuit very deep (many sequential multiplications)?** → Consider whether bootstrapping is needed and which scheme has the most mature bootstrapping support for your library
