## **Step 1: Start with the Right Architecture**

**Manager:** We have a customer who wants centralized anomaly detection on their network traffic, but they’re worried about how attractive a central analysis server will be as a cyber-target. Can we get an industry-recognized anomaly detector running at reasonable speed in FHE?

**Dev:** Probably, yes. But let’s make sure the application has the right shape. FHE is strictly a client \- “blind server” model: the client encrypts their data and sends it to us. We run the anomaly detection model on the encrypted data and return an encrypted result. The client decrypts and reads anomaly scores. 

**Manager:** Well, the anomaly detector is strictly feed-forward arithmetic: packet features flow into the front, anomaly scores fall out the back. That seems a natural fit.

**Dev:** Good\! One more thing: who trains the normal traffic model and how does our server get it? That model has to be on the server before we start analytics. I think if the client trains the model and sends it to the server encrypted, we can’t learn anything, nor can any hacker hit us and learn anything.

**Manager:** That’s actually the architecture I was hoping for. The client trains the normal model, we store it and run detection on samples the client sends later.

**Dev:** Roger that.

## **Step 2: Get It Working in Plaintext First**

**Manager:** Great. What’s first?

**Dev:** We take the Kitsune anomaly detection package that’s well known for this job and get it running end-to-end in the clear, with our FHE client-server structure baked in. That means separate training and execution processes, the client-server split already present, and the data flowing through the same pipeline structure the FHE version will use.

**Manager:** Kitsune already works. Can’t we skip ahead?

**Dev:** No. The original Kitsune code moves directly from training to execution with everything in memory. We need to store model parameters after training so the client can send them to the server. We also need to separate the client-side feature extraction work from the server-side anomaly detection. And, we need to restructure the anomaly detector to operate on batches of data using the same order of operations the FHE version will require. All of that has to be right *in the clear* before we introduce FHE, or we won’t be able to tell a restructuring bug from an FHE bug.

**Manager:** So this plaintext version is our golden reference.

**Dev:** Exactly. Every step after this gets tested against it.

## **Step 3: Remove Data-Dependent Control Flow**

**Manager:** What changes does the Kitsune code need before we can encrypt it?

**Dev:** The fundamental change is that any branching that depends on data values has to go. This is a bit subtle. FHE forbids **data-dependent control flow**  (i.e. “if x \> 5”) but fully supports **data-dependent data flow** (i.e. “y \= x\[i\]”). The sequence of instructionsoperations the machine executes must be identical for every possible input. That's the part that's public and observable on the server. What's hidden is only the *data* those instructions operate on.

**Manager:** Wait one second. I thought there could be no data dependencies at all\!

**Dev:** Not quite right. The constraint isn't "no data dependencies", —  it's "no data dependencies *in the control path of the program*." 

**Manager:** Hmm…Kitsune’s ensemble of autoencoders is mostly multiply-add operations: no conditionals.

**Dev:** Right, and that’s a real advantage here. The core computation is just linear combinations through the autoencoder weights, followed by activation functions, followed by reconstruction error. No branching. The main thing we have to watch for is any control flow tied to learned architecture decisions, like dynamically choosing how many autoencoders to run. We’ll setfix the architecture to five autoencoders, each with a predetermined feature assignment of 10 features (for a total of 50 features) so the server always runs the same computation regardless of input. It’s going to be faster that way anyway.

## **Step 4: Understand Multiplicative Depth**

**Manager:** Okay, the architecture is setfixed. Now can we start encrypting?

**Dev:** Not yet. We need to count the multiplications first. Every time we multiply two ciphertexts, that multiplication consumes a level of what’s called the noise budget: —a finite resource embedded in the ciphertext at encryption time. Too many sequential multiplies means the result will beis garbage. But \- telling that it’s garbage isn’t something that FHE does. We’d discover it by accident once we tried to decrypt thein the output.

**Manager:** How deep is the anomaly detector computation, and how much can we afford?

**Dev:** The autoencoder pass has two activation function evaluations per unit: —one on the way through the hidden layer and, one on the reconstruction. The final anomaly detector adds more activations plus a squaring step to compute the mean squared error. Each Chebyshev polynomial approximation to mimic an activation function costs several levels of depth on its own. My estimate is around 22 levels total. We can likely tolerate more levels for this specific applicationlike 26, so we should be fine.

**Manager:** What if we need more depth than that allows?

**Dev:** There’s a technique called bootstrapping that refreshes the noise budget without decrypting. It’s expensive though, and we want to avoid it if at all possible. But we’re in luck with this application. No bootstrapping required, I’m betting.

## **Step 5: Approximate Your Non-Linear Functions**

**Manager:** You said earlier that non-linear functionss were bad for FHEju-ju. The autoencoders use sigmoids, and the anomaly detector uses tanh. How do we get around those?

**Dev:** Good catch. Since FHE can only add and multiply, we’ll replace both of those functions with Chebyshev polynomial approximations. So long as all inputs to those functions will stay strictly within range, Chebyshev will behave pretty well, and be much more uniform across the range than, say, a Taylor series approximation.

**Manager:** How accurate do we need the sigmoid and tanh approximations to be?

**Dev:** Accurate enough for the anomaly scores to remain meaningful, and no more. Analysis of Kitsune on example datasets shows that the inputs to the autoencoder sigmoids stay reliably within an input range (of plus or minus 5), so a fifth-order Chebyshev fit works well there. For the anomaly detector’s tanh functions, the inputs also stay reliably within an input range (of plus or minus 2 in this case), and fifth-order also works well. We’ll store the approximation coefficients in the model file so the server can use them directly.

**Manager:** What if inputs go outside those ranges?

**Dev:** Here there be monsters. The outputs of pPolynomial approximations diverge dramatically outside their valid range, and Kitsune’s default normalization doesn’t guarantee bounded inputs. We’re switching to a sigmoid-based normalization parameterized by the mean and variance of each feature measured during training. That bounds the normalized input values strictly between \-10 and 1, which keeps us well inside the approximation range.

**Manager:** Where did you learn all this stuff?

**Dev:** You know, the usual: late night math talk shows, reddit, playing Adventure.

## **Step 6: Constrain Data Types and Precision**

**Manager:** What about the feature values themselves? Do the packet lengths and timing data map cleanly into FHE? What about internal program variables that are meantwant to be floats inside the neural network?

**Dev:** With some care we’ll be fine. As for inputs, packet lengths are at most 14 bits, and timing differences can be represented in 12 bits, so 16-bit fixed-point inputs are a reasonable starting point there. After normalization to the \-10-to-1 input range, we’re working with fixed-point representations of the real values, but they still fit comfortably in the precision that the CKKS scheme I’m using provides.

**Manager:** And CKKS is the right scheme for this? Aren’t results from CKKS approximate?

**Dev:** Yes. But, we’re computing real-valued anomaly scores, not exact integer results, and we can tolerate small approximation errors in the output. CKKS is the natural fit.

## **Step 7: Select Scheme, Parameters, and SIMD Strategy Together**

**Manager:** There’s a lot of packets flowing by, according to WireShark. How many packets can we process at once?

**Dev:** This is where things get interesting. CKKS lets us pack multiple values into a single ciphertext and operate on all of them simultaneously,—a form of “Single Instruction Multiple Data” (SIMD) parallelism built into the scheme. We’re using a ring dimension of 64k, which lets usgives us process 32k packets simultaneously as ciphertextvalues that we can represent per ciphertext.

**Manager:** So we can process 32k packets in one shot?

**Dev:** Exactly, but the packing strategy matters. The natural layout for our application is one feature per ciphertext. Each of the 50 features gets its own ciphertext, and each slot in that ciphertext holds the value of that feature for one of the 32k packets in the batch. That’s about 20 minutes of a typical user's network activity.

**Manager:** So then how much data are we moving over the network to the central server for each batch?

**Dev:** The ciphertexts have to support 22 levels of multiplicative depth at the server, so each ciphertext is roughly 24 megabytes for 128-bit security. Fifty ciphertexts per batch comes to about 1.2 gigabytes per 20-minute batch of packets analyzed.

**Manager:** That’s seems like a lot. Is it manageable?

**Dev:** For a prototype, yes. For a production deployment, it’s a constraint we’d optimize around. On the memory side, keeping 50 feature ciphertexts plus working storage for a batch peaks around 3 gigabytes of RAM on the server, which is workable.

## **Step 8: Choose Your Library**

**Manager:** Which FHE library are you aiming to use?

**Dev:** OpenFHE. No question. It supports CKKS, it’s actively maintained, it’s open source, and it’s the most thoroughly tested option available. It uses CPUs to run computations. We can also get GPU support with a light lift. And, since our Niobium mistic compiler stack is integrated to OpenFHE, we get faster-than-GPU hardware acceleration for free.

## **Step 9: Build and Test the FHE Version**

**Manager:** Walk me through how this actually works.

**Dev:** The client starts by generating the cryptographic context: —the CKKS parameters, the key pair, and the relinearization key the server needs to handle ciphertext multiplications. The context and evaluation keys get serialized and sent to the server as part of the initialstartup handshake with the client. Bear in mind that the relinearization key doesn’t let the server decrypt anything. All it does is keep ciphertexts in a usable form after each multiply.

**Manager:** Then what?

**Dev:** The client samples network traffic in the usual way, runs feature extraction to produce 50 features per packet, normalizes them, batches 32k packets, and encrypts each feature batch into its own ciphertext using OpenFHE. The 50 ciphertexts go to the server. The server runs the full KitNet ensemble under encryption: linear combinations through the autoencoder weights, Chebyshev activations, reconstruction, residuals, then the final anomaly detector with its own activations and a squaring step for the mean squared error. The result is a single score ciphertext with one anomaly score per each of the 32K packetsslot. That comes back to the client, where it gets decrypted and evaluated plotted for interpretation.

**Manager:** How do we know it’s working?

**Dev:** We compare the decrypted scores against our plaintext reference. Discrepancies usually mean one of three things: either the noise budget is exhausted, the precision is insufficient, or a non-linear operation was missed somewhere in our code. 

**Manager:** Wait \- how do you debug it if the scores don’t match? All the data sent to the server programinside the program run is encrypted\! 

**Dev:** Debugging encrypted programs is genuinely hard. The most practical way is to output and decrypt intermediate values and compare themin the usual binary search debugging approach, comparing against the corresponding plaintext valueversion each time. One of these days I’ll use Claude to build an FHE program debugger that automates the process. But for now, I’m off the clock… xyzzy\!

*The steps above reflect the real development path of Niobium’s FHEsible Network Intrusion Detection benchmark, reproducing the Kitsune anomaly detection framework. Each step is necessary; none can be skipped. The good news is that Kitsune’s ensemble autoencoder structure—lightweight, mostly multiply-add, and structurally branchless—is one of the better matches to FHE’s capabilities that exists in practical machine learning.*

To learn more about FHE, hardware acceleration, and Niobium’s encrypted cloud platform, The Fog™, [contact us](https://niobium.co/contact) or [sign up](https://niobium.co/request-access) for early access.