---
name: fhe-application-design
description: >
  Design and build Fully Homomorphic Encryption (FHE) applications using OpenFHE.
  Guides developers from problem statement through privacy model, feasibility
  assessment, scheme selection, circuit design, SIMD data layout, parameter
  selection, and code generation. Use this skill whenever the user mentions FHE,
  homomorphic encryption, encrypted computation, computing on encrypted data,
  OpenFHE, CKKS, BFV, BGV, privacy-preserving computation, or wants to design
  any application that processes data without decrypting it. Also use when the
  user asks about FHE feasibility, whether a workload can run under encryption,
  or how to structure a client-server protocol for encrypted data. Use this skill
  even if the user doesn't explicitly say "FHE" — if they describe a scenario
  where computation must happen on data that the computing party cannot see,
  this skill applies.
---

# FHE Application Design

This skill guides developers through designing and building FHE applications
using OpenFHE. It targets developers who are new to FHE but know how to code.
The approach is top-down: start with the privacy problem, establish feasibility,
then progressively work down through scheme selection, circuit design, data
layout, parameter selection, and implementation.

## How to Use This Skill

Follow the stages below in order. Each stage has a brief description here in
the SKILL.md, with pointers to reference files that contain deeper guidance.
Read the relevant reference file when you need the detail for a given stage.

The stages mirror a real FHE design process. Do not skip stages — each one
produces inputs that the next stage depends on.

## Stage 1: Establish the Privacy Model

Before thinking about circuits, parameters, or code, work with the user to
answer four questions:

1. **Who are the parties, what do they hold, and who are the adversaries?**
   Map out every participant: what data they hold, what must stay private,
   who might try to learn it, what vantage point the adversary has (observing
   network traffic, server memory), and what the adversary can do (semi-honest
   vs. malicious).

2. **Who encrypts the data?** This question has major downstream consequences
   for SIMD packing (Stage 5). There are two fundamentally different patterns:
   - *Single encryptor*: one party holds all the input data and encrypts it
     (e.g., a company encrypting its own dataset for server processing). This
     party can freely pack records across SIMD slots within a ciphertext.
   - *Independent encryptors*: each data owner encrypts their own data
     independently (e.g., individual customers each submitting encrypted
     records to a bank). Different owners' data **cannot** share a ciphertext
     — doing so would require sharing encryption keys, letting one owner
     decrypt another's data.
   Identify which pattern applies. If independent encryptors, note that
   cross-record SIMD batching is not possible without violating privacy.

3. **Who should see the output?**
   Determine who holds the decryption key. Flag key proliferation as a risk.
   Consider threshold (multi-party) decryption if multiple parties need output
   access. Consider whether the same keys should be used across successive
   runs of the program.

4. **Is input privacy sufficient, or is there an output privacy problem?**
   FHE protects inputs during computation but says nothing about what the
   output reveals. Consider whether the decrypted result could leak information
   about private inputs through repeated queries, aggregate precision, model
   inversion, or protocol side channels. Consider mitigations: query rate
   limiting, output coarsening, differential privacy.

5. **Is there an output integrity problem?** FHE guarantees that the
   computation is correct, but the party who decrypts the result controls
   what gets reported. Ask: does the decryptor have an incentive to
   misrepresent the result to another party? If a customer decrypts their
   own credit score, they could claim any score they like — the bank cannot
   verify it. This is a fundamental protocol flaw, not a privacy problem.
   When the *consumer* of the result (the party who acts on it) is different
   from the *decryptor*, the protocol must ensure the consumer can verify or
   directly obtain the true result. The standard technique is
   **transciphering**: embed an FHE-friendly symmetric cipher (e.g., Rubato,
   HERA, Pasta) inside the homomorphic circuit so a copy of the result is
   encrypted under a key the consumer holds. The decryptor strips the FHE
   layer and obtains two things: the plaintext result (which they can read)
   and an opaque symmetric ciphertext of the same result (which they pass
   to the consumer). The consumer decrypts the symmetric layer with their
   key and obtains the verified result. This **dual-output** pattern gives
   the decryptor transparency (they see their own result) while giving the
   consumer integrity (they know the result is genuine). It adds modest
   depth (4–6 levels for Rubato) and avoids digital signatures under FHE
   (which would be prohibitively deep). See Stage 5 for circuit-level
   details.

After this stage, you should be able to state clearly: who the parties are,
who encrypts (single vs. independent encryptors), what the adversary model is,
who decrypts, whether FHE alone is sufficient or needs to be combined with
other techniques, and whether the protocol needs output integrity protection
(transciphering or another mechanism).

**For detailed guidance:** Read `references/fhe-privacy-model.md`

## Stage 2: Assess FHE Feasibility

Now determine whether the computation itself is tractable under FHE. Apply
four tests, in order of importance:

1. **Can the computation be made data-oblivious?** The complete sequence of
   operations must be determined before any input data arrives. If the
   algorithm branches based on encrypted values, it cannot run under FHE
   as-is. It must be restructured into a fixed arithmetic circuit.

2. **Does the computation stay in one lane — arithmetic or logical?** FHE
   schemes are optimized for either bulk arithmetic (add/multiply on packed
   numbers) or logical operations (Boolean circuits on bits). Workloads that
   cross between these domains pay enormous costs. A workload that stays
   cleanly in one lane is far more tractable.

3. **How deep is the multiplication chain?** Every homomorphic multiplication
   adds noise. The longest chain of dependent multiplications is the
   *multiplicative depth*, and it drives nearly every parameter choice.
   Shallow circuits (a handful of sequential multiplications) are practical.
   Deep circuits may require bootstrapping, which is very expensive.

4. **Is there natural SIMD parallelism?** FHE schemes pack thousands of
   independent values into a single ciphertext. A single operation processes
   all of them simultaneously. Workloads that apply the same operation to many
   independent data points (scoring a model across thousands of records,
   evaluating a function on batched inputs) exploit this beautifully.

   **Important:** SIMD parallelism depends on the encryption model from
   Stage 1 Question 2. Single-encryptor scenarios get full SIMD utilization
   — one party packs all records across slots. Independent-encryptor
   scenarios often have *poor* SIMD utilization — each party uses only a
   few slots (e.g., 15 features out of 32,768 available slots). The
   remaining slots are wasted. Do not confuse task-level concurrency
   (processing multiple independent requests on separate CPU threads) with
   SIMD parallelism (packing multiple data items into one ciphertext). The
   former is always available; the latter depends on the encryption model.
   Be honest about SIMD utilization in the assessment — poor utilization
   is a real cost of the independent-encryptor model, not a showstopper,
   but it should be acknowledged.

If the answer to #1 is "no, and it can't be restructured," FHE is not the
right tool. If #2 reveals constant lane-crossing, the cost may be prohibitive.
If #3 yields very deep circuits with no way to reduce depth, bootstrapping
costs may dominate. If #4 yields no parallelism, performance will suffer
(though task-level concurrency on the server can still provide throughput).

**For detailed guidance:** Read `references/fhe-what-fhe-can-and-cannot-do.md`

## Stage 3: Design the Plaintext Algorithm

Get the computation working in plaintext first, with the FHE client-server
structure already in place:

1. Write the entire program without encryption and run it against thorough
   test data. This becomes the ground truth that every subsequent version is
   tested against.

2. Separate client-side and server-side responsibilities cleanly. The server
   must complete its work in one pass — no round trips, no branching on
   intermediate values.

3. Remove all data-dependent control flow. Replace conditional branches with
   branchless arithmetic (evaluate both sides and use an arithmetic selector).
   Note: data-dependent *data flow* (e.g., y = x[i]) is fine — it's
   data-dependent *control flow* (e.g., if x > 5) that must go.

4. Count the multiplicative depth. Look for ways to reduce it: favor addition
   over multiplication, use tree-structured reductions, reorder operations to
   minimize the critical path.

5. Replace non-linear functions (division, comparison, square roots, sigmoid,
   tanh) with polynomial approximations — typically Chebyshev series. Each
   approximation adds depth, so there's a direct tradeoff between accuracy
   and cost. Ensure inputs are normalized to the approximation's valid range.

6. Constrain data types and precision. Move from floating-point to integer or
   fixed-point arithmetic. Aim for 32 bits or less of precision. Consider
   non-linear scaling of inputs to reduce dynamic range.

**For detailed guidance:** Read `references/building-your-first-fhe-application.md`
and `references/fhe-application-dialogue.md` (a worked example showing all
these steps applied to a real anomaly detection application).

## Stage 4: Select the FHE Scheme

Choose among the three arithmetic FHE schemes supported by OpenFHE:

- **CKKS** — Approximate arithmetic on real/complex numbers. Best for ML
  inference, signal processing, analytics on real-valued data. Highest
  throughput for bulk numerical computation. Packs N/2 SIMD slots per
  ciphertext.

- **BFV** — Exact modular integer arithmetic. Best for workloads requiring
  precise integer results: financial calculations, identity verification,
  encrypted database operations. Packs N SIMD slots per ciphertext.

- **BGV** — Also exact integer arithmetic, similar to BFV. Largely superseded
  by BFV for new development. Choose only if you have a specific reason.

The core decision: if your computation works on real-valued data and can
tolerate small approximation errors, use CKKS. If you need exact integer
results, use BFV.

**For detailed guidance:** Read `references/fhe-scheme-selection.md`

## Stage 5: Design the Homomorphic Circuit

This is the core of the FHE application design — translating the plaintext
algorithm into an arithmetic circuit that operates on encrypted data. Key
decisions at this stage:

**SIMD data layout.** How to pack data into ciphertext slots to maximize
parallelism. The packing strategy must be consistent with the privacy model
from Stage 1 — specifically, *who encrypts the data* determines what can
share a ciphertext:

- *Single-encryptor batching* (column-major): when one party holds all the
  data (e.g., a client encrypting its own dataset for server processing),
  pack one ciphertext per feature/field with each slot holding that feature
  for a different record. This enables massive SIMD parallelism. All three
  worked examples (set membership, fetch-by-similarity, NID) use this
  pattern because a single party legitimately holds all records.
- *Independent-encryptor (per-record)*: when each data owner encrypts
  independently (e.g., individual customers submitting to a bank), each
  owner produces their own ciphertexts. SIMD slots within a single
  ciphertext may still be used (e.g., packing multiple features of one
  record into that record's ciphertext), but most slots will be unused
  — e.g., 15 features out of 32,768 slots is 0.05% utilization. This is
  a real cost of the independent-encryptor model. The server can still
  achieve throughput via task-level concurrency (processing multiple
  customers' requests on separate threads), but each individual FHE
  computation underutilizes the SIMD capacity. You cannot pack different
  owners' data into the same
  ciphertext without violating the privacy model — one owner's key would
  decrypt another owner's data.
- *Multi-batch handling*: when the dataset exceeds the slot count (typically
  N/2 = 32,768 for CKKS at ring dimension 2^16), split into batches and
  aggregate results across batches.

**Check:** revisit Stage 1. If multiple independent parties each encrypt
their own data, do not use column-major packing across those parties.
Column-major packing is for single-encryptor scenarios. Misapplying it
creates a design where one party's key can decrypt another party's data.

**Comparison strategies in CKKS.** Since CKKS operates on approximate reals,
exact comparison is not directly possible. Common approaches include:
- *Squared Euclidean distance + iterated squaring*: compute the distance
  between encoded vectors, normalize, then amplify the match/non-match gap
  through repeated squaring. Low depth, good for set membership and matching.
- *Chebyshev polynomial approximation*: approximate indicator functions
  (sigmoid for thresholding, Gaussian impulse for equality) using Chebyshev
  series. Higher depth but more flexible, good for comparison against
  continuous thresholds.

**Depth budget.** Sum up the multiplicative depth of every operation in the
circuit. This number directly determines the CKKS/BFV/BGV parameters you'll
need. Strategies to reduce depth: tree-structured reductions, deferred
relinearization (EvalMultNoRelin followed by batch Relinearize), and
choosing lower-degree polynomial approximations where precision permits.

**Slot aggregation.** To reduce a SIMD vector to a scalar (e.g., summing
all match indicators), use rotate-and-sum: repeatedly rotate by powers of 2
and add, collapsing N slots into one in log(N) steps. This consumes no
multiplicative depth (rotations and additions are free in depth), but requires
rotation keys for each power-of-2 shift.

**Transciphering for output integrity.** If Stage 1 Question 5 identified an
output integrity problem (the decryptor might misrepresent the result to the
party who acts on it), add a transciphering layer to produce a **dual output**.
The computing server knows a symmetric key K. As the last operations in the
FHE circuit, produce two encrypted values: (a) the result itself, and (b) a
copy of the result encrypted under K using an FHE-friendly symmetric cipher.
Both are FHE-encrypted. After the decryptor strips the FHE layer, they obtain
the plaintext result (which they can read — it's their data) plus an opaque
symmetric ciphertext of the same result (which they cannot read without K).
The decryptor returns the symmetric ciphertext to the consumer (whoever holds
K), who decrypts it and obtains the verified result. This gives the decryptor
transparency about their own outcome while giving the consumer assured
integrity — the consumer knows the result came from the genuine computation,
not from the decryptor's claim.

FHE-friendly symmetric ciphers are designed to minimize multiplicative depth:

- *Rubato* — stream cipher, ~4–6 levels of depth, designed for CKKS
- *HERA* — block cipher, ~4–5 levels, supports both CKKS and BFV
- *Pasta* — ~5 levels, designed for hybrid HE frameworks
- *Elisabeth* — ~6 levels, designed for TFHE but adaptable

Budget the transciphering depth into the total circuit depth from the start.
For example, if the core computation needs 12 levels and Rubato adds 5, the
total depth budget is 17 — which may push the ring dimension from 2^15 to
2^16. Plan for this in Stage 6.

**For worked examples:** Read `references/example-set-membership.md` for a
complete CKKS application using squared distance + iterated squaring with
column-major packing. Read `references/example-fetch-by-similarity.md` for
a more complex application using Chebyshev approximation, slot replication,
running sums, and output compression. Read
`references/example-network-intrusion-detection.md` for ML inference under
encryption: an autoencoder ensemble with Chebyshev-approximated activation
functions, feature-major packing, and a streaming batch protocol.

## Stage 6: Select Parameters

Parameter selection is tightly coupled to the circuit design from Stage 5.
The key parameters for CKKS (and analogous choices for BFV/BGV):

- **Ring dimension (N).** Determines the number of SIMD slots (N/2 for CKKS,
  N for BFV/BGV) and the achievable security level for a given modulus size.
  Typical choices are 2^15 (32,768) or 2^16 (65,536). Larger N means more
  slots and support for deeper circuits, but larger ciphertexts and slower
  operations.

- **Multiplicative depth.** Set to match your circuit's depth budget from
  Stage 5. This is the most important parameter — it drives the modulus chain
  size, which in turn constrains the ring dimension needed for security.

- **Scaling modulus size** (CKKS). Bits per level in the RNS modulus chain.
  Typical values are 40–50 bits. Determines the precision available at each
  level. Larger values give more precision but require a larger overall
  modulus, which may force a larger ring dimension for security.

- **First modulus size** (CKKS). The first (largest) modulus in the chain.
  Typically 50–60 bits. Provides headroom for the initial encryption.

- **Security level.** Target HEStd_128_classic (128-bit classical security)
  unless you have a specific reason for a different level. OpenFHE enforces
  this — if your parameters don't achieve the target security, it will
  automatically promote the ring dimension.

- **Scaling technique.** Use FLEXIBLEAUTO in OpenFHE for automatic rescaling
  in CKKS. This inserts rescale operations as needed to keep ciphertext
  levels aligned, reducing the chance of subtle level-mismatch bugs.

- **Rotation keys.** Generate keys for every rotation amount your circuit
  uses (powers of 2 for rotate-and-sum, specific offsets for data
  rearrangement). Missing rotation keys cause runtime errors.

**Size estimation.** After choosing parameters, estimate the physical sizes
of ciphertexts and keys. These determine client memory requirements and
network bandwidth — designers are often surprised by how large FHE objects
are. Use these formulas (assuming 64-bit RNS limbs):

- *Ciphertext at level L*: 2 × N × (L + 1) × 8 bytes. A ciphertext is two
  polynomials, each with N coefficients represented in RNS with (L + 1)
  limbs. Example: N = 65536, L = 12 → 2 × 65536 × 13 × 8 ≈ 13.6 MB.
- *Public key*: same size as one ciphertext at the maximum level.
- *Relinearization key*: roughly dnum × 2 × N × (L + 1) × 8 bytes, where
  dnum is the number of key-switching digits (typically 2–3 in OpenFHE).
- *Each rotation key*: same size as a relinearization key. If the circuit
  needs K distinct rotation amounts, the total rotation key material is
  K × (size of one rotation key). This often dominates total key size.
- *Secret key*: N × 8 bytes (small, held only by the decryptor).

**Minimize ciphertext level.** Ciphertext size scales linearly with (L + 1).
Set multiplicative depth to the exact circuit depth needed — do not
over-provision "for safety." If the circuit requires 8 levels, setting depth
to 20 makes every ciphertext 2.4x larger than necessary. The depth budget
from Stage 5 should be tight.

**Estimate total transfer sizes.** For each direction of communication,
compute the total bytes:

- *Client → Server*: (number of input ciphertexts) × (ciphertext size at
  level L) + public key + relinearization key + rotation keys. In many
  protocols, keys are sent once during setup and reused across sessions.
- *Server → Client*: (number of output ciphertexts) × (ciphertext size at
  output level). Output ciphertexts are smaller than inputs because the
  computation consumes levels — an input at level 12 that uses all 12
  levels produces output at level 0, which is 13x smaller.

Present these estimates to the user so they can assess whether the
deployment is practical for their network and memory constraints.

**For reference on parameter choices in practice:** The example applications in
the references directory show concrete parameter selections with rationale.

## Stage 7: Implement, Test, and Iterate

There are two implementation tracks. Choose based on what is available and
what the design requires:

- **Track A — the `nb` FHE DSL (preferred).** If the
  [niobium-client](https://github.com/NiobiumInc/niobium-client) repository is
  available (look for a `dsl_fhe/` directory), implement in the `nb`
  domain-specific language. The DSL generates everything Track B builds by
  hand — the four-program architecture, all serialization, CMake, key
  generation matched to the operations used, and record/replay
  instrumentation — from ~3 short `.nb` files. Trust boundaries from Stage 1
  become compiler-enforced (`@client`/`@server`; server code referencing the
  secret key is a compile error). This track is dramatically less error-prone
  and keeps the whole application within one context window.
  **Read `references/implementing-with-nb-dsl.md`** for the stage-by-stage
  mapping from this skill's design outputs to DSL constructs, the workflow,
  and current limitations.

- **Track B — raw OpenFHE C++.** Use when the DSL is unavailable, or when the
  design needs features the DSL does not yet express: **BFV/BGV** (the DSL is
  CKKS-only today), **transciphering** (Stage 5 output integrity),
  **threshold/multi-party keys**, or **bootstrapping**. The rest of this
  stage describes Track B; it is also the reference model for understanding
  what Track A generates.

### Track B: the four-program architecture (raw OpenFHE)

Build the FHE version using OpenFHE as four separate executable programs.
This structure enforces the trust boundaries from the protocol — each program
runs in a distinct security context, communicates via serialized files, and
can be deployed independently:

1. **keygen** — Generates the crypto context, key pair, relinearization keys,
   and rotation keys. Serializes the public key, evaluation keys (relin +
   rotation), and secret key to separate files. The secret key file stays
   on the client; the public and evaluation keys are sent to the server
   (typically once, during setup). This program also serializes the crypto
   context parameters so all other programs can reconstruct the same context.

2. **encrypt** — Reads plaintext input data and the public key. Encodes the
   data into plaintexts (with appropriate SIMD packing from Stage 5),
   encrypts each plaintext, and serializes the resulting ciphertexts. These
   are the files sent from client to server for each request.

3. **server** — Reads the crypto context, public key, evaluation keys, and
   input ciphertexts. Executes the homomorphic circuit from Stage 5.
   Serializes the output ciphertexts. This program never sees the secret
   key. If transciphering is used (Stage 5), the server also holds the
   symmetric key and applies the transciphering layer before serializing
   output.

4. **decrypt** — Reads the secret key and output ciphertexts. Decrypts and
   decodes the results. Compares against the plaintext reference
   implementation from Stage 3 to validate correctness.

Use CMake with find_package(OpenFHE) for all four programs. Link against
OpenFHE's shared or static libraries. Enable PKE, KEYSWITCH, and LEVELEDSHE
features on the crypto context. Enable ADVANCEDSHE if using Chebyshev
evaluation or advanced rotation patterns. Use OpenFHE's serialization API
(Serial::SerializeToFile / Serial::DeserializeFromFile) for all inter-program
data exchange.

**Why four programs:** This structure makes the trust boundaries from Stage 1
concrete in the code. Each boundary between programs is a serialization point
where you can measure exactly what crosses the wire — validating the size
estimates from Stage 6. It also prevents accidental leakage of the secret key
into the server binary, which is easy to do in a monolithic implementation.

Also produce a fifth program:

5. **run_test** — A local test runner that orchestrates the full pipeline for
   development. It invokes keygen → encrypt → server → decrypt in sequence,
   passing serialized files between them, then automatically compares
   decrypted outputs against the plaintext reference from Stage 3. It should
   report: per-sample error (absolute and relative), mean and max error
   across the test set, serialized file sizes at each boundary (keys,
   input ciphertexts, output ciphertexts — for comparison against Stage 6
   estimates), and wall-clock time for each stage. This program is a
   development tool, not a production artifact — in deployment, the four
   core programs run on separate machines. But during the edit-test-iterate
   cycle, run_test makes it fast to validate changes without manually
   invoking each stage.

**Testing and debugging:**

1. **Run and compare.** Use run_test to execute the full pipeline and review
   the error report. Discrepancies against the plaintext reference usually
   mean: noise budget exhausted (depth too great for parameters),
   insufficient precision, or a missed non-linear operation.

2. **Debug.** Debugging encrypted programs is hard because all intermediate
   values are encrypted. The practical approach is to add temporary decrypt
   calls in the server program (using the secret key, for debugging only),
   inspect intermediate values, and compare against the plaintext
   implementation in a binary-search style.

3. **Profile and iterate.** Review the timing and file-size reports from
   run_test. Both memory and runtime will likely be large — gigabytes of
   memory and orders of magnitude slower than plaintext are normal. If
   unacceptable, loop back: reduce multiplicative depth, use smaller
   parameters, pack data more efficiently, or reduce precision.

**For a detailed walkthrough:** Read `references/building-your-first-fhe-application.md`

## Stage 8: Specify the Protocol and Threat Model

As a final design step, document the full protocol and its security properties:

1. The client-server message flow (what is sent, in what order).
2. What each party can and cannot learn, and under what assumptions.
3. Intentional information leakage (the protocol output).
4. Incidental leakage (dataset size from batch count, timing, raw scores).
5. Output integrity: if the decryptor differs from the result consumer, how
   does the consumer verify the result is genuine? Document whether
   transciphering is used, what symmetric cipher, and how the symmetric key
   is managed. If the decryptor *is* the consumer (e.g., a user checking
   their own data), output integrity is not a concern.
6. Threats not addressed (malicious adversaries, side channels, exhaustive
   query attacks, collusion).

This documentation serves both as a security specification and as a guide
for anyone reviewing or extending the application.

**For an example of thorough threat modeling:** Read
`references/example-set-membership.md`, which includes a complete threat model
and security analysis.

## Reference Files

Read these files as needed during the design process. Each file is
self-contained and can be read independently.

| Reference file | When to read it |
|---|---|
| `references/fhe-privacy-model.md` | Stage 1: establishing the privacy model (parties, adversaries, output privacy, differential privacy) |
| `references/fhe-what-fhe-can-and-cannot-do.md` | Stage 2: assessing whether a workload is FHE-feasible |
| `references/fhe-scheme-selection.md` | Stage 4: choosing between CKKS, BFV, and BGV |
| `references/building-your-first-fhe-application.md` | Stages 3, 6, 7: the development checklist from plaintext through implementation |
| `references/implementing-with-nb-dsl.md` | Stage 7 Track A: implementing the design in the `nb` FHE DSL (niobium-client) — stage-to-construct mapping, workflow, pitfalls, limitations |
| `references/fhe-application-dialogue.md` | Stages 3–7: a worked example showing all steps for a real anomaly detection application |
| `references/example-set-membership.md` | Stages 5–8: complete CKKS design spec and implementation (squared distance, iterated squaring, column-major packing, threat model) |
| `references/example-fetch-by-similarity.md` | Stage 5: advanced CKKS patterns (Chebyshev approximation, slot replication, running sums, output compression) |
| `references/example-network-intrusion-detection.md` | Stages 3–7: ML inference under encryption (autoencoder ensemble, Chebyshev activations, feature-major packing, streaming batches) |
| `references/openfhe-examples-catalog.md` | All stages: catalog of specific OpenFHE examples mapped to design patterns |

## Key Principles

Throughout the design process, keep these principles in mind:

- **Privacy model first.** Never jump to circuit design without establishing
  who holds what, who the adversaries are, and what the output reveals.

- **Plaintext correctness is ground truth.** Get the algorithm working
  correctly in the clear before introducing encryption. Every subsequent
  version is tested against this reference.

- **Depth is the critical resource.** Multiplicative depth drives parameter
  sizes, which drive memory and performance. Every design decision should
  be evaluated for its depth cost.

- **FHE protects inputs, not outputs.** The output of the computation is
  legitimately visible to the decryptor. If the output can be used to infer
  private inputs, that's a design problem FHE doesn't solve — you need
  additional protections.

- **Test against the plaintext reference at every stage.** When results
  diverge, the three most likely causes are: exhausted noise budget,
  insufficient precision, or a missed non-linear operation.

- **Iterate.** The first working encrypted version is a milestone, not the
  finish line. Real-world performance comes from iterating on the balance
  between depth, precision, parallelism, and parameter size.
