# **Alan Turing, Banburies, Narvik, and What FHE Can and Cannot Do**

In the winter of 1940, Alan Turing faced a problem. Bletchley Park had intercepted thousands of German naval Enigma messages. The data was right there but all encrypted, and the tools available to attack the encryptions could only perform certain kinds of operations. Turing’s breakthrough approach, Banburismus, didn’t “break” Enigma the way popular history suggests, cracking the cipher wide open and reading messages like postcards. Instead, Turing exploited a narrow statistical foothold that he had theorized, and verified when the Royal Navy captured the German trawler *Polares* off  Narvik, Norway. Because Kriegsmarine operators all started each day’s transmissions with their Enigma machine rotors in the same starting position, *every* *message* began with a short encrypted preamble of Enigma settings for that message, and all of these preambles were encrypted against that same key. This gave Turing just enough structure to work with: by counting letter coincidences where overlapping ciphertexts aligned and scoring the results probabilistically, his team could narrow 336 possible rotor configurations down to a handful worth testing on the electromechanical Bombe machines. Their primary tool for this critical job was simple: long sheets of strong paper with columns of letters printed vertically, onto which clerks punched holes representing ciphertext. Two sheets laid on a light box revealed coincidences wherever light shone through both. The sheets were printed by a stationer in Banbury, Oxfordshire, and so became known as “Banburies,” and the method that used them, Banburismus. From there, it was a matter of cribs, time, and Bombe machines to find the remaining Enigma settings in use for the day.

That’s it. No reading of plaintext. No flexible querying. All Turing’s team could do was counting, scoring, and ranking: structured arithmetic applied to encrypted data under rigid constraints. Banburismus worked because the *computation* it needed happened to fit the tools available. More than eighty years later, Fully Homomorphic Encryption presents us with a strikingly similar situation, but this time, the constraint isn’t a limitation we’re trying to work around. It’s the main feature.

## **The Arithmetic Box**

FHE lets you compute on encrypted data without ever decrypting it. That sentence gets repeated so often that it’s easy to miss what it actually promises, and what it doesn’t, much in the same way that popular history makes it easy to miss what Turing’s team could and couldn’t do.

What FHE *natively* supports is adding and multiplying encrypted integers. That’s it. There is no “if this, then that.” There is no comparison that FHE can make, no pattern matching, no way to do less computation based on some testable condition. Every FHE program must be, and can only be, a fixed arithmetic *circuit*: a graph of addition and multiplication gates with structure determined entirely before any data arrives.

Anything that a programmer would like to do beyond add and multiply must be reformulated into the terms above, often at enormous computational penalty. Some reformulations are practical: smooth mathematical functions can be approximated with truncated Chebyshev or Taylor series, turning transcendentals into polynomial chains that FHE handles natively. But each such approximation consumes more of the precious multiplication budget and requires careful attention to numerical stability. Getting it right is non-trivial, and getting it wrong is *silent*: the ciphertext will happily return garbage without complaint. Turing’s team knew this problem intimately: a flawed crib fed to the Bombes wouldn’t produce an error message, it would simply waste hours of irreplaceable machine time chasing phantom solutions.

## **The Rubric: Is your workload FHE-friendly?**

Just as Turing’s team had to assess each day’s intercepts against the capabilities of Banburismus and the Bombes, anyone considering FHE for a real workload needs to ask a structured set of questions. Here’s a practical rubric.

**1\. Can the computation be made data-oblivious?**

The single most important question. If you’re working with an existing algorithm that you want to turn into an FHE-friendly one, can you write out the complete sequence of operations *before you see any input data?* If the answer is yes, the circuit’s shape is fixed regardless of what the encrypted values turn out to be, then  you’re in FHE territory. If the operations you need to perform depend on what the data *is*, you’re fighting the model.

It’s critical to remember here that adapting existing algorithms is a short-term transition, however, and a natural consequence of how humans think. The “horseless carriage paradigm” appears repeatedly in human progress: automobiles, electrification, and even television, but most especially computing. Early software for mainframes mimicked batch-processing clerical workflows such as payroll. Time-sharing systems initially replicated the batch model interactively before anyone conceived of genuinely interactive paradigms like Engelbart's NLS or Xerox PARC's desktop metaphor. And even *that* metaphor follows the pattern: the "desktop" with "files" in "folders" was a skeuomorphic mapping of the office onto a fundamentally different medium. Decades later, truly new ideas like hypertext, wikis, and collaborative real-time editing emerged as native-to-the-medium concepts.

We should expect nothing different in the development of the secure computer, the FHE engine. Truly new thinking will lead with novel oblivious computing algorithms. But for now, we need to think in terms of “Can we adapt what we have to be data-oblivious?”

*FHE-friendly:* Matrix-vector multiplication, dot products, weighted sums, linear regression inference.

*FHE-hostile:* Graph traversal where edges are chosen at runtime, database joins on encrypted keys, any algorithm that follows pointers.

**2\. Does your computation stay in one lane, either arithmetic or logical, or does it mix the two?**

FHE schemes are broadly optimized for one of two worlds: bulk arithmetic over packed numerical data, or logical operations over individual bits. Each world has mature, efficient techniques. A major problem arises when a workload needs both, such as when an arithmetic pipeline must make a comparison, or when a logical operation must feed into a numerical calculation. Crossing between these domains is where FHE costs explode. A workload that stays cleanly in one lane is far more tractable than one that constantly switches between them.

*FHE-friendly:* Polynomial evaluation, statistical aggregation, neural network linear layers: workloads that stay arithmetic. Boolean circuit evaluation, encrypted database lookups: workloads that stay logical.

*FHE-hostile:* Regular expression matching over numerical data, the attention heads of large language models, cryptographic hashing: workloads that inherently interleave arithmetic and logical operations.

**3\. How deep is the multiplication chain?**

Every homomorphic multiplication adds noise to the ciphertext. After enough multiplications, the noise overwhelms the result. A trick called bootstrapping can reset this noise budget (at least in part), but that trick is extremely expensive. Workloads with shallow multiplicative depth (a handful of sequential multiplications) are vastly more practical than deep ones.

*FHE-hostile:* Iterative algorithms requiring hundreds of sequential multiply-dependent steps, the attention heads of large language models.

**4\. Do you expect to have a large number (thousands or more) of independent samples to process at the same time?**

Some FHE schemes pack thousands of independent values into a single ciphertext. A single homomorphic operation processes all of them simultaneously. Workloads that apply the same operation to many independent data points exploit this parallelism beautifully. Workloads that require complex data-dependent interaction between data points lose this advantage.

*FHE-friendly:* Scoring a fraud model across 20,000 transactions in parallel, evaluating a neural network on batched inputs, running an anomaly detection across 30,000 network packets in parallel.

*FHE-hostile:* Algorithms where each element’s next step depends on the values of other elements in unpredictable ways, or single-item computations that can’t be batched.

## **The Turing test for your workload**

Here’s the mental model that ties it all together. Imagine your computation as an assembly line in a factory. The raw materials (data) arrive from the customer in sealed, opaque containers that  you can never open. The only machines on the factory floor can add the contents of two containers together, or multiply them, producing a new sealed container with the result. You can set up the assembly line however you like, but you must design it *completely* before the containers arrive, and you can’t peek inside to decide what to do next. Only the customer can peek inside, after your trucks deliver the output packages to their receiving dock.

If your product can be manufactured under those constraints, FHE is your factory. If you need to open a container to decide which machine to route it to next, you need a different kind of factory entirely.

Turing and the Banburists of Hut 8 understood this instinctively: you work with the tools you have, on the problems those tools can reach. The power of FHE, like the power of Banburismus, isn’t that it can do everything you want. It’s that what it *can* do, it can do on data you were never allowed to see.

*To learn more about FHE, hardware acceleration, and Niobium’s encrypted cloud platform, The Fog™, [contact us](https://niobium.co/contact) or [sign up](https://niobium.co/request-access) for early access\!*