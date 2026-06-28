# The Privacy Model: What to Establish Before You Design Anything

Before you think about circuits, parameters, or code, you need to answer three questions about privacy. These questions determine whether FHE is the right tool, how to structure the protocol, and what additional protections you might need. Skip this step and you risk building an application that's cryptographically secure but doesn't actually protect what matters.

## Question 1: Who are the parties involved in providing data and computation, what do they hold, and who are the adversaries?

Start by mapping out the participants and their assets. In the simplest case, there are two parties: a client who owns an input to the computation and expects it to stay private, and a server that will perform the computation. The server may also own data — a model, a database, a reference set — that it considers proprietary and doesn't want revealed to the client.

But two parties isn't the only pattern. In federated learning, for example, multiple clients each contribute private data to a computation performed by a separate server. In that setting, each client may want its input protected not only from the server but from every other client as well. The number of parties and the relationships between them shape the entire design.

For each party, establish:

- **What data do they hold?** Be specific. Name each client, and describe the kind of data they hold.
- **What must remain private to them?** Not all of a party's data necessarily needs protection. A client might be willing to reveal that they're making a query, as long as the content of the query stays hidden.
- **Who are the adversaries?** An adversary is anyone who might try to learn private data. The server is typically an adversary with respect to the client's input. The client may be an adversary with respect to the server's dataset. Other clients may also be adversaries with respect to a client's private data. External attackers who can observe network traffic are another category of adversary.
- **What is each adversary's vantage point?** Can they observe the encrypted data in transit? Can they observe the memory of the computation server as it runs?
- **What can the adversary do?** This is where formal models become relevant. An *honest-but-curious* (semi-honest) adversary follows the protocol correctly but tries to learn as much as possible from what it observes. A *malicious* adversary may deviate from the protocol — sending crafted inputs, altering the computation, or colluding with other parties. FHE natively provides protection against honest-but-curious adversaries. Defending against malicious adversaries requires additional techniques (verifiable computation, zero-knowledge proofs) layered on top.

Most FHE applications assume the semi-honest model. That's a reasonable starting point, but it should be a conscious choice, not an unexamined default. If your threat model includes adversaries who might tamper with the computation, you need to acknowledge that FHE alone is not sufficient and plan accordingly.

Also, FHE always assumes that the computation server honestly performs the computation as expected. That is, FHE does not provide an *integrity* guarantee. Addressing the integrity concern would also require verifiable computation proofs.

## Question 2: Who should see the output?

FHE protects data *during* computation, but the result of that computation must eventually be decrypted by someone. The question of who holds the decryption key is a design decision with significant consequences.

In the common two-party case, the answer is usually straightforward: the client holds the secret key and decrypts the result. The server never has access to the key and therefore never sees the plaintext — not the input, not the intermediate values, and not the output.

But consider multi-party scenarios. If five hospitals contribute patient data to train a shared model, who should be able to decrypt the trained model? All five? A designated trusted party? This is where things get subtle:

- **Key proliferation is a red flag.** A central tenet of good cryptography is that secret keys should have minimal distribution. If your design requires many parties to hold the same decryption key, you've created a system where any single compromise breaks everyone's privacy. Question whether the design can be restructured to avoid this.
- **Multi-party decryption** (threshold decryption) is a technique where the secret key is split into shares held by different parties. No single party can decrypt alone — a quorum must cooperate. This lets you implement policies like "three of five hospitals must agree before the model is released" without ever assembling the full key in one place. It adds protocol complexity but solves the key distribution problem cleanly.
- **Asymmetric output visibility** is also possible. Perhaps the client should see a yes/no answer but not the underlying match scores. Perhaps one party should see aggregate statistics but not individual records. The protocol design — specifically, what computation is performed before the result is returned for decryption — determines what the decrypted output reveals.

The key question to resolve: for each party in your system, should they be able to decrypt the output? If yes, how do they get the key (or key share) securely? If no, how do you ensure they never obtain it?

A related question comes up when the same FHE program may be run on successive input data sets: should the same parties be able to see the output of all runs of the program? If not, you may need to consider how often to re-generate keys that control encryption and decryption in the program.

## Question 3: Is input privacy sufficient, or do you also have an output privacy problem?

This is the question people most often overlook, and it's the one that can quietly undermine an otherwise well-designed system.

FHE guarantees *input privacy*: the party performing the computation learns nothing about the encrypted inputs. But FHE says nothing about what the *output* reveals. The output is the legitimate result of the computation — it's supposed to be seen by whoever holds the decryption key. The problem is that the output may reveal more about the inputs than the input providers intended.

Consider a simple example: a private set membership test that returns "match" or "no match." That Boolean result is the intended output, and it reveals very little. But what if the protocol returned the number of matching records instead? An adversary who can submit many queries could probe the dataset systematically — testing names one at a time — and eventually reconstruct significant portions of it. The computation is correct, the encryption is unbroken, and the privacy guarantee is still violated.

This is the *output privacy* problem. It arises whenever the legitimate output of the computation contains enough information to allow inference about private inputs. Common manifestations include:

- **Repeated queries.** Even a boolean output becomes dangerous if the adversary can query freely. Rate limiting, query budgets, or audit logging may be needed at the application level.
- **Aggregate leakage.** Returning an exact count, a precise average, or a raw score may leak more than a thresholded or rounded result would. Think carefully about the precision of what you return.
- **Model inversion.** In machine learning settings, a party who sees prediction outputs may be able to infer properties of the training data. This is a well-studied problem in ML privacy, and FHE does not make it go away.
- **Side channels in the protocol structure.** The number of ciphertext batches processed, the timing of the computation, or the size of the encrypted result may leak information about private inputs (such as dataset size) even though the data itself is encrypted.

### Mitigations

The resolution depends on the application. Some mitigations are straightforward and can be implemented at the application level:

**Query rate limiting and budgets.** If a party can submit unlimited queries to your system, even a single-bit boolean response per query can eventually leak significant information about private inputs. Application-level controls — rate limiting, per-user query budgets, audit logging — are the first line of defense. These aren't cryptographic protections; they're operational controls that limit the adversary's ability to accumulate information over time.

**Output coarsening.** Returning a precise numerical result when a coarser answer would suffice is a common and avoidable source of leakage. Threshold a score to a boolean. Round a count to the nearest ten. Quantize a probability to a few discrete buckets. Every bit of precision you remove from the output is information an adversary can't exploit.

**Differential privacy.** For applications where some precision in the output is needed but individual-level privacy must be preserved, differential privacy (DP) provides a principled framework. The core idea is to add carefully calibrated random noise to the computation's output, providing a mathematically rigorous guarantee that no single input record can significantly influence the result. DP is particularly relevant in aggregate analytics and machine learning settings — for example, if your FHE application computes an average salary across encrypted records, DP noise ensures that the output doesn't reveal whether any particular individual's salary was included. Differential privacy composes well with FHE: FHE protects the inputs during computation, and DP protects the inputs from inference through the output. The noise can be added either inside the FHE computation (by the server, before returning the result) or outside it (by the client, after decryption). Each approach has tradeoffs — adding noise under encryption avoids trusting the client but is more complex to implement.

**Structural padding.** Protocol-level side channels — batch counts that reveal dataset size, timing that reveals computation complexity — can be mitigated by padding to fixed sizes and adding dummy computation. These are straightforward but easy to overlook.

### A Fundamental Limit

Even with all these mitigations, there is a theoretical boundary worth understanding. Rice's Theorem from computability theory tells us that for any nontrivial semantic property of a program's output, it is undecidable in general whether the program has that property. Applied to our context: there is no general procedure that can examine an FHE program and determine whether its output leaks a particular piece of information from its input. You can analyze specific leakage channels and mitigate the ones you find, but you cannot prove that you've found them all.

This is not a reason to despair — it's a reason to be humble. Careful analysis will catch the obvious and many of the subtle leakage paths. Differential privacy provides a mathematical backstop for aggregate outputs. But no analysis of a nontrivial program's output can be complete, and the claim "this system leaks nothing beyond the intended result" should always be understood as "we've found no leakage in our analysis" rather than as a proof.

### The Bottom Line

The important thing is to *ask the question*. Establishing that FHE provides sufficient input privacy is not the same as establishing that the overall system provides sufficient privacy. If the answer to "can someone who sees the output infer things about the inputs?" is yes, you need to decide whether that's acceptable and, if not, what additional protections to layer on.

## Putting It Together

After working through these three questions, you should be able to state clearly:

1. Who the parties are, what each party's private data is, and what adversary model you're designing against.
2. Who will hold the decryption key (or key shares), and why that's the right choice.
3. Whether the computation's output might leak information about private inputs beyond what's intended, and what mitigations are needed if so.

With these answers in hand, you can make an informed decision about whether FHE's native client-server structure fits your problem, whether FHE alone is sufficient or needs to be combined with other techniques, and how to structure the protocol. Only then does it make sense to move on to the question of whether FHE can *computationally* do what you need.
