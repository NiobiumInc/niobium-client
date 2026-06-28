# **Building Your First B-17 Flying Fortress:** **A Practical Roadmap to FHE Applications**

In 1935, Boeing’s Model 299—the prototype of the B-17, later known as the Flying Fortress—crashed on its demonstration flight, killing the test pilot. The Army Air Corps concluded that the plane was “too much airplane for one man to fly.” But the winning response wasn’t to simplify the aircraft and dilute its capabilities. Instead, a group of test pilots invented the *pre-flight checklist*: a methodical, step-by-step procedure that made a machine of overwhelming complexity manageable. The B-17 went on to fly safely for decades, and the checklist became standard practice across all of aviation.

Today, fully homomorphic encryption (FHE) is too much for most programmers to approach without a checklist. FHE lets you compute on data without ever decrypting it—a remarkable capability—but the path from idea to working application crosses unfamiliar terrain: noise budgets, polynomial approximations, ciphertext packing, and parameter tradeoffs. Taken all at once, it’s overwhelming. Taken step by step, it’s tractable. Here’s a programmer’s checklist for FHE drawn from real experience developing FHE applications at Niobium.

## **Start with the Right Architecture**

Before you think about encryption, think about structure. FHE applications follow a natural client-server pattern: the client holds plaintext data, encrypts it, and sends the ciphertext to the server. The server performs computation on the encrypted data, then sends the encrypted result back. The client decrypts and reads the answer.

This means your program needs a clean separation. The server must be able to do its work without ever needing to peek at the data or ask the client for help mid-computation. No round trips, no branching based on intermediate values the server can’t see. Just one transmission in, one transmission out. If your application doesn’t fit this shape, you’ll need to rethink the design before going further.

In some cases—federated learning being a prominent example—there may be a multiple-client, single-server pattern to implement. FHE is amenable to this as well, but it introduces a question you need to think carefully about: which client or clients will be allowed to decrypt the program’s result? FHE protects data during computation, but it does not guarantee anything about what can be learned from the *output* of that computation. The cryptographic design of who holds decryption keys in such cases, and what the results reveal, requires significant thought beyond the mechanics of encryption itself.

## **Get It Working in Plaintext First**

Write the entire program without encryption and run it against a thorough set of test data. This might sound obvious, but it’s essential: your plaintext implementation becomes the ground truth that every subsequent version is tested against. Don’t skip this. You’re going to transform this program through several rounds of structural changes, and at each stage you need to confirm that you haven’t broken anything. And without this step, it will be much harder to debug intermediate outputs.

## **Remove Data-Dependent Control Flow**

Here’s where FHE starts to impose constraints. Because the server operates on encrypted data, it can never inspect a value to decide which branch to take. Every *if* statement that depends on data, every loop whose iteration count is determined at runtime by data has to be removed or replaced by an alternative that translates our standard notion of conditional branching into something more akin to linear algebra.

The standard approach is branchless computation: evaluate both sides of a conditional and use an arithmetic selector to pick the right result. It’s counterintuitive if you’re used to conventional programming, but it’s a fundamental requirement. Once you’ve restructured, test again against your reference results.

## **Understand Multiplicative Depth**

This is the single most important concept in practical FHE, and it should shape your thinking from this point forward. Every multiplication on encrypted data consumes noise budget, a finite resource baked into the ciphertext at encryption time. Chain enough multiplications together and the noise overwhelms the signal, corrupting your result.

The longest chain of dependent multiplications in your program is its *multiplicative depth*, and this number drives nearly every parameter choice you’ll make later. Start counting multiplications now. Look for ways to reduce depth: favor addition over multiplication where possible, use tree-structured reductions instead of sequential accumulation, and reorder operations to minimize the critical path.

What happens when multiplicative depth is too much for the noise budget? We remove part of the noise using a technique called *bootstrapping*, allowing for more computation. What happens when the noise keeps growing as we compute more? Bootstrap again. And again. Bootstrapping doesn’t require decryption on the server, which would lead to loss of privacy. However, it’s a very costly operation, which you’ll want to avoid if at all possible. Sometimes, though, for example in deep neural networks or AI models, you won’t have a choice.

## **Approximate Your Non-Linear Functions**

Division, comparison, square roots, sigmoid—none of these have direct FHE equivalents. You’ll need to replace them with polynomial approximations, typically Chebyshev or Taylor series. Or, you may find a way to remove these functions entirely. For example, some algorithms can be modified to work on data in squared form that would normally be processed through a square root operator, removing the need for a complex and non FHE-friendly sqrt() operation. Each approximation adds multiplications, which means more depth, so there’s a direct tradeoff between the accuracy of your approximation (higher-degree polynomial) on one hand, and both the multiplicative depth and computational cost on the other.

Keep in mind that polynomial approximations are typically valid only over a bounded input range—for example, \[−1, 1\]—and can diverge wildly outside it. Lower order approximations diverge less severely than higher order, so do not automatically assume higher order is better. You’ll need to ensure your inputs are normalized to the expected range, or your approximation will produce nonsensical results.

After substituting your approximations, don’t just check that the answers are close. Verify that the accumulated approximation error stays within bounds your application can tolerate. This matters especially if you later choose the CKKS scheme, which introduces its own approximation noise on top of what the polynomials contribute.

## **Constrain Your Data Types and Precision**

Examine every variable: inputs, intermediate values, results. FHE *only operates on integer data*, because the only known hard math problems that give us homomorphic encryption are integer/lattice problems, so encoding into that domain is a prerequisite for security. It follows that all data must move from floating-point to integer or fixed-point arithmetic.

Precision of the computation must be accordingly reduced as well—aim for 32 bits or less. One approach that may help is to limit the dynamic range of your input data. Non-linear scaling of inputs *a priori* can reduce the need to spend bits of precision on representing outlier data points.

This step also begins to inform a key architectural choice. If your computation naturally lives in the integers and you need exact results, you’re headed toward the BFV or BGV encryption schemes. If you’re working with real-valued data and can absorb small errors, CKKS is the better fit. Keep this in mind as you move to parameter selection.

## **Select Scheme, Parameters, and SIMD Strategy Together**

These decisions are deeply coupled and should be made as a unit. Your choice of scheme determines the kind of arithmetic you can do. BFV and BGV are for integer-only arithmetic of the kind typically found in image processing applications. On the other hand, CKKS is *approximate*, and is therefore better suited for signal processing applications where data takes on real values, such as those found in medical applications. Your choice of the degree of polynomials used in ciphertexts—typically 2¹⁵ or 2¹⁶—determines both how many data items you can encode into a single ciphertext (the *slot count*), the multiplicative depth available at a given security level, and the size of ciphertexts, which dominates in determining your memory footprint. Your choice of how many “limbs” to use in data representation also controls multiplicative depth.

Next, think through how your data can be organized for processing in FHE. Vector processing is a built-in advantage of modern FHE schemes, giving the option to process tens of thousands of data items in parallel at no additional cost by *packing* them into the same ciphertext. That means you may want to re-organize the way you think about data. For example, if you’re aiming to compute machine learning inference over many *features* of data, and you naturally have many data samples to process, you’ll want to pack the same feature of thousands of those samples into each ciphertext. This will stripe your features across several ciphertexts, but you will gain huge efficiency in processing.

When you select parameters, remember: 128-bit security is the practical ceiling for most FHE applications, though 192-bit may be feasible for shallower circuits.

## **Choose Your Library**

Now you can pick a library. For the BFV/BGV/CKKS schemes discussed here, actively maintained open-source options include OpenFHE (C++ with Python bindings) and Lattigo (Go). There are also closed-source commercial libraries, such as those from DeSilo and CryptoLab. Other libraries such as SEAL and HElib exist but are no longer actively supported. There are also libraries built around the TFHE scheme, such as Concrete and TFHE-rs, which take a fundamentally different approach. They’re worth knowing about, but follow a different development path than the one outlined in this post. My recommendation: go with OpenFHE. It’s well-supported, open-source, and has seen significantly more testing by the user community than the others. Your earlier decisions about scheme and language will narrow the field further if you explore alternatives.

## **Build and Test the FHE Version**

Now you can build and test the FHE version of your program. The first step here is to replace each addition and multiplication with the corresponding FHE library call, and then compile again to make sure you’ve got the syntax right. 

Next, set up your first test data. To do that, you’ll need to generate keys, with the help of the FHE library you’re using. As with typical *public key* cryptography, there’s the secret (decryption) key and the public (encryption) key. For FHE, there’s more to do: you’ll need the keys that allow the compute server to perform homomorphic computations. Remember that the keys you’ll hand to that server do NOT allow it to decrypt anything. Instead, they’re needed to keep the computation under control. Relinearization keys are a great example here. When you multiply two encrypted values together, the result is encrypted in a way that's "tangled up" with itself. Relinearization keys are special public data that let the system untangle the result back into a normal-functioning ciphertext without ever decrypting it. 

With your keys ready, encrypt a representative test data set matching your plaintext test cases. Finally, run the program, decrypt the outputs, and compare them against your plaintext reference. Discrepancies at this stage usually mean one of three things: your noise budget is exhausted (the depth is too great for your parameters), precision is insufficient, or you missed a non-linear operation somewhere.

Debugging a program where all the variables are encrypted is a challenging problem. One good approach if you hit a discrepancy is to output and decrypt intermediate variables one step at a time, comparing those intermediate values against your matching plaintext implementation.

## **Profile and Iterate**

Finally, measure. Check peak memory consumption and wall-clock runtime. Both will likely be startling—memory footprints measured in gigabytes and runtimes orders of magnitude slower than plaintext are normal in FHE. If the numbers are unacceptable, loop back. Can you shave a level of multiplicative depth? Use smaller parameters? Pack data more efficiently? Reduce precision further?

This isn’t a failure condition. It’s the normal development cycle for FHE applications. The first working encrypted version is a milestone, not the finish line. Real-world performance comes from iterating on the balance between depth, precision, parallelism, and parameter size until you find the sweet spot your application needs. FHE hardware accelerators built by Niobium boost that performance into the realm where the inherent overhead of FHE falls away, and your application is ready for prime time performance. 

Your flying privacy fortress awaits. Aim high\!

*To learn more about FHE, hardware acceleration, and Niobium’s encrypted cloud platform, The Fog™, [contact us](https://niobium.co/contact) or sign up for early access\!*