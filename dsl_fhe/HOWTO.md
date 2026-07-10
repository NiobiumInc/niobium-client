# HOWTO: Add a New Example to the Niobium DSL

Step-by-step guide for porting an existing FHE C++ application to the `.niob` DSL.

---

## Overview

The Niobium DSL compiles `.niob` source files into standalone C++ binaries using openFHE.
Each example follows a client-server architecture:

```
shared.niob   →  types, constants, wire types (both domains)
client.niob   →  keygen, encrypt, decrypt stages
server.niob   →  encrypted computation stages
```

The compiler generates: `nb_shared.h`, `nb_shared.cpp`, one `.cpp` per stage, and
`CMakeLists.txt`.

---

## Step 0: Choose a Packing Strategy

Before writing any code, decide how plaintext values map into ciphertext slots.
This choice drives everything — encryption layout, rotation patterns, and depth
budget — and is expensive to change later.

CKKS provides N/2 slots per ciphertext (where N is the ring dimension).
A homomorphic operation (add, multiply) applies to **all slots in parallel**.
The packing strategy determines what those parallel operations compute.

### Single-Slot Packing

One value per ciphertext. Wastes most of the ring capacity but is the simplest
to reason about. Useful for testing or when values need independent noise budgets.

```
Ciphertext 0:  [ x₀ | 0 | 0 | 0 | ... | 0 ]
Ciphertext 1:  [ x₁ | 0 | 0 | 0 | ... | 0 ]
```

**DSL example**: `simple/` — encrypts one scalar per ciphertext for basic
arithmetic tests.

### Full SIMD Packing

Fill all slots. A single `EvalAdd` or `EvalMult` processes N/2 values at once.
Use `rotate()` to shift data across slots for reductions or access patterns.

```
Ciphertext 0:  [ x₀ | x₁ | x₂ | ... | x_{N/2-1} ]
```

**DSL example**: `ml-inference-fhe/` — packs a 784-pixel image across 1024 slots.
The MLP forward pass uses rotate-and-multiply to select different pixel positions
from the same ciphertext.

### Column-Based Packing

Transpose the data: instead of one record per ciphertext, pack the k-th element
of every record into the k-th ciphertext. A single ciphertext-ciphertext multiply
computes the k-th term of all inner products at once.

```
Record layout (3 records, 4 features):
  record₀ = [a₀, a₁, a₂, a₃]
  record₁ = [b₀, b₁, b₂, b₃]
  record₂ = [c₀, c₁, c₂, c₃]

Column-packed (4 ciphertexts):
  ct_col0:  [ a₀ | b₀ | c₀ | 0 | ... ]    ← feature 0, all records
  ct_col1:  [ a₁ | b₁ | c₁ | 0 | ... ]    ← feature 1, all records
  ct_col2:  [ a₂ | b₂ | c₂ | 0 | ... ]    ← feature 2, all records
  ct_col3:  [ a₃ | b₃ | c₃ | 0 | ... ]    ← feature 3, all records
```

**DSL examples**:
- `fhe-NetworkMonitor/` — each network feature column is a separate ciphertext.
  The mat-vec product `W^T · x` becomes element-wise multiply + accumulate across
  feature ciphertexts.
- `fetch-by-similarity/` — the database is column-packed so one `EvalMult`
  computes one dimension of similarity across all records simultaneously.

### Sparse Packing

Leave slots intentionally empty (zero-padded) to simplify rotations or prevent
cross-talk between values during rotate-and-accumulate patterns.

```
Ciphertext:  [ x₀ | 0 | 0 | 0 | x₁ | 0 | 0 | 0 | x₂ | 0 | 0 | 0 | ... ]
              ←── stride 4 ──→
```

**DSL example**: `fetch-by-similarity/` — payloads are packed with gaps between
records so that rotation-based extraction can isolate individual results without
cross-talk from adjacent slots.

### Replication Packing

Copy the same value into every slot (or a contiguous block). Multiplying a
replicated ciphertext by a column-packed ciphertext broadcasts one constant
across all records in a single operation.

```
Ciphertext:  [ v | v | v | v | ... | v ]    ← same value in every slot
```

Building replication requires `log₂(N)` rotations and additions. The DSL
`replicate` capability auto-generates the necessary rotation keys.

**DSL example**: `fetch-by-similarity/` — the query vector is replicated across
all slots via `slot_replicator`, so a single `EvalMult` with the column-packed
database computes the dot product contribution for every record simultaneously.

### Choosing the Right Strategy

| Strategy | Ciphertexts | Parallelism | Rotations | Best for |
|----------|-------------|-------------|-----------|----------|
| Single-slot | N (one per value) | None | None | Scalar ops, testing |
| Full SIMD | 1 per vector | All slots | Many (access patterns) | Dense vector ops |
| Column-based | d (one per dim) | All records | Few (accumulate) | Matrix-vector products |
| Sparse | Varies | Partial | Simplified | Alignment-sensitive ops |
| Replication | 1 per broadcast | All slots | log₂(N) to build | Broadcast multiply |

Most real applications **combine** strategies: column-based for the database with
replication for the query, or full SIMD with sparse padding for alignment. Document
your packing choice in a comment at the top of `shared.niob` — future readers will
thank you.

---

## Step 1: Study the Reference C++ Implementation

Before writing any `.niob` code, read the reference C++ to identify:

1. **Pipeline stages**: What binaries exist? What does each one do?
2. **Scheme parameters**: Ring dimension, multiplicative depth, scaling mod size, security level
3. **Key types needed**: EvalMult, EvalRotate/EvalAtIndex, EvalSum
4. **Wire types**: What data crosses the client-server boundary? (crypto params, encrypted inputs, encrypted results)
5. **Directory layout**: Where are keys, ciphertexts, datasets stored?
6. **External dependencies**: Are there large machine-generated source files or external libraries?

### Checklist

```
[ ] Chosen a packing strategy (see Step 0) and documented it
[ ] Identified all pipeline stages and their domain (client/server)
[ ] Noted scheme parameters (ring_dim, depth, precision, security)
[ ] Listed all key types (add, mul, rotate, chebyshev, etc.)
[ ] Defined wire types (what gets serialized between stages)
[ ] Mapped the directory layout (key dirs, ciphertext dirs, data dirs)
[ ] Found any external C++ source files that need linking
```

---

## Step 2: Write `shared.niob`

This file contains everything shared between client and server.

### Constants

```nb
const INPUT_DIM: u32 = 784
const NUM_CLASSES: u32 = 10
const RING_DIM: u32 = 2048
```

### Instance Sizes

Define an enum and struct for workload sizes:

```nb
enum InstanceSize { Single, Small, Medium, Large }

struct Instance {
    size: InstanceSize,
    batch_count: u32,
}

fn instance(size: InstanceSize) -> Instance {
    match size {
        Single => Instance { size, batch_count: 1 },
        Small  => Instance { size, batch_count: 100 },
    }
}
```

### Directory Layout

Define functions that map instances to filesystem paths:

```nb
fn iodir(inst: Instance) -> path    { root() / "io" / instance_name(inst.size) }
fn keydir(inst: Instance) -> path   { iodir(inst) / "keys" }
fn encdir(inst: Instance) -> path   { iodir(inst) / "encrypted" }
fn datadir(inst: Instance) -> path  { root() / "datasets" / instance_name(inst.size) }
```

For separate public/secret key directories:

```nb
fn pubkeydir(inst: Instance) -> path { iodir(inst) / "public_keys" }
fn seckeydir(inst: Instance) -> path { iodir(inst) / "secret_key" }
```

### Wire Types

Define serialization boundaries:

```nb
wire CryptoParams {
    context: CryptoContext,
    public_key: PublicKey,
    eval_mult_key: EvalMultKey,
    eval_rot_keys: EvalAutomorphismKeys,
}

wire EncryptedInput {
    ciphertext: enc<vec<f64>>,
}

wire EncryptedResult {
    ciphertext: enc<vec<f64>>,
}
```

### External Modules (if needed)

If the server links against external C++ source files:

```nb
extern weights from "vector_constants"
```

This generates `#include "vector_constants.h"` in the shared header and adds the
source to CMakeLists.txt. Vendor the C++ source/header in the example directory
and point `-DLOCAL_SRC_DIR=...` at it so the example stays self-contained.

---

## Step 3: Write `client.niob`

### Scheme Configuration

```nb
use shared::*

scheme CKKS {
    security: not_set          // or: 128-classic
    ring_dim: 2048             // omit to use inst.ring_dim or let openFHE auto-select
    depth: 9
    precision: 42 bits         // scaling mod size
    first_mod: 57 bits         // optional
}

requires { add, mul, rotate }  // generates appropriate key types
```

**Available capabilities**: `add`, `mul`, `rotate`, `chebyshev`, `running_sums`, `replicate`

- `rotate` generates `EvalRotateKeyGen` with indices 1..ring_dim/2-1
- `replicate` uses `DFSSlotReplicator::get_rotation_amounts(inst.degrees)`
- `running_sums` uses `RunningSums::get_shift_amounts(n_slots, n_cols, ...)`

### Key Generation Stage

```nb
@client @stage("key_generation")
fn generate_keys(inst: Instance) -> writes(CryptoParams)
{
    let keys = keygen()
    save_secret_key(keys.secret, seckeydir(inst) / "sk.bin")
    return CryptoParams {
        context: keys.context,
        public_key: keys.public,
        eval_mult_key: keys.eval_mult,
        eval_rot_keys: keys.eval_rot,
    }
}
```

The compiler auto-generates serialization of `CryptoParams` to the directory returned
by the first available key directory function (`pubkeydir`, `keydir`, etc.).

### Encrypt Stage

```nb
@client @stage("encode_encrypt_input")
fn encrypt_input(inst: Instance)
    -> reads(CryptoParams), writes(EncryptedInput[*])
{
    let params = load(CryptoParams, from: pubkeydir(inst))
    let dataset = load_matrix<f32>(datadir(inst) / "test_pixels.txt", INPUT_DIM)

    for i in 0..inst.batch_count {
        let ct = encrypt(params.public_key, dataset[i])
        save(EncryptedInput { ciphertext: ct },
             to: ctxtupdir(inst) / ("cipher_input_" + str(i) + ".bin"))
    }
}
```

**Key patterns**:
- `writes(EncryptedInput[*])` — declares batch output (indexed wire type)
- `load_matrix<f32>(path, cols)` — auto-detects text format for `.txt` files, binary for `.bin`
- `str(i)` — converts integer to string for filename construction
- `encrypt(pk, data)` — handles both array literals and variables

### Decrypt Stage

```nb
@client @stage("decrypt_decode")
fn decrypt_decode(inst: Instance)
    -> reads(EncryptedResult[*]),
       writes_plaintext(iodir(inst) / "predictions.txt")
{
    let sk = load_secret_key(seckeydir(inst) / "sk.bin")
    let predictions: vec<u32> = []

    for i in 0..inst.batch_count {
        let encrypted = load(EncryptedResult,
                             from: ctxtdowndir(inst) / ("result_" + str(i) + ".bin"))
        let logits = decrypt(sk, encrypted.ciphertext)
        let (predicted, _) = argmax(logits[0..NUM_CLASSES])
        predictions.push(predicted)
    }

    return predictions
}
```

**Key patterns**:
- `reads(EncryptedResult[*])` — declares batch input
- `load_secret_key` auto-loads `cc.bin` from the key directory (required by openFHE)
- `argmax(vec)` returns a pair `(index, value)` — destructure to get just the index
- `writes_plaintext(path)` — writes the return value as binary data

---

## Step 4: Write `server.niob`

### Computation Stage

```nb
use shared::*

// Wrapper for the external MLP function
fn mlp(ct: enc<vec<f64>>) -> enc<vec<f64>> {
    extern_call("mlp", ct)
}

@server @stage("encrypted_compute")
@hardware(cache_key: ["workload_size", "batch_id"])
fn encrypted_compute(inst: Instance, batch_id: u32)
    -> reads(CryptoParams, EncryptedInput[batch_id]),
       writes(EncryptedResult[batch_id])
{
    let params = load(CryptoParams, from: pubkeydir(inst))
    let input = load(EncryptedInput,
                     from: ctxtupdir(inst) / ("cipher_input_" + str(batch_id) + ".bin"))
    let result = mlp(input.ciphertext)
    return EncryptedResult { ciphertext: result }
}
```

**Key patterns**:
- `extern_call("func", args...)` — calls external C++ function with `cc` auto-prepended
- `@hardware(cache_key: [...])` — generates Niobium record/replay instrumentation
- Indexed wire types `EncryptedInput[batch_id]` for per-batch I/O
- The function wrapper `fn mlp(ct) { extern_call("mlp", ct) }` is auto-detected as an
  extern wrapper — the codegen skips generating its body and calls the external function directly

---

## Step 5: Add Makefile Targets

Add to `dsl_fhe/Makefile`:

```makefile
# Variables
MY_NB_OUT     := $(EXAMPLES)/my-example/nb_out
# Directory holding any vendored C++ helpers for this example (an extern_call
# bridge, support libs, etc.). Keep them in the example directory so the example
# stays self-contained — don't reference an external repo.
MY_LOCAL_SRCS := $(realpath $(CURDIR)/$(EXAMPLES)/my-example)

# Build target
my-example:
	@echo "=== Compiling my-example (DSL → C++) ==="
	@mkdir -p $(MY_NB_OUT)
	@cd $(XCOMP) && PYTHONPATH=.. python3 -m xcomp.nbc compile \
		../$(EXAMPLES)/my-example/shared.niob \
		../$(EXAMPLES)/my-example/client.niob \
		../$(EXAMPLES)/my-example/server.niob \
		--outdir ../$(MY_NB_OUT)
	@echo ""
	@echo "=== Building my-example (C++ → binaries) ==="
	@mkdir -p $(MY_NB_OUT)/build
	@cd $(MY_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) \
		         -DLOCAL_SRC_DIR=$(MY_LOCAL_SRCS) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(MY_NB_OUT)/build/"

# Test target
test-my-example: my-example
	@echo ""
	@echo "=== Testing my-example (end-to-end) ==="
	@cd $(MY_NB_OUT)/build && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		echo "  keygen..." && ./key_generation 0 && \
		echo "  encrypt..." && ./encode_encrypt_input 0 && \
		echo "  compute..." && ./encrypted_compute 0 0 && \
		echo "  PASS"
	@echo ""
	@echo "All my-example tests passed."
```

Add to `.PHONY`, `examples`, `test-examples`, `clean`, `help`, and `test-compiler`
(for parse/check validation).

Pass `-DLOCAL_SRC_DIR=...` only if the example has vendored C++ helpers (e.g. an
`extern_call` bridge). Vendor those sources inside the example directory so the
example builds from this repo alone, with no dependency on an external repo.

---

## Step 6: Create `nb_out/.gitignore`

```gitignore
# Auto-generated by nbc — do not check in
*.cpp
*.h
CMakeLists.txt
build/
```

---

## Step 7: Build and Test

```bash
cd dsl_fhe

# Run compiler tests (parse + semantic check)
make test-compiler

# Build the example (DSL -> C++ -> binaries)
make my-example

# Run end-to-end test
make test-my-example
```

---

## Common Issues and Solutions

### `inst.ring_dim` — struct has no member

If the scheme declaration uses a literal `ring_dim: 2048`, the codegen uses that value
directly. If `ring_dim` is omitted from the scheme, the codegen checks whether the
`Instance` struct has a `ring_dim` field. If it does, it emits `parameters.SetRingDim(inst.ring_dim)`.
If neither the scheme nor the Instance struct has `ring_dim`, the call is omitted entirely
and openFHE auto-selects based on security level and depth.

**Options**: Add `ring_dim` to the scheme declaration, add the field to `Instance`, or omit
it entirely to let openFHE choose.

### `ConstCiphertext` vs `Ciphertext` type mismatch

External C++ functions may return `ConstCiphertext<DCRTPoly>` (a `shared_ptr<const ...>`).
When assigning to a wire type's `enc<T>` field, the codegen auto-inserts
`std::const_pointer_cast` to handle the conversion.

### Separate `pubkeydir` / `seckeydir`

The codegen auto-detects whether to use unified `keydir(inst)` or split
`pubkeydir(inst)` / `seckeydir(inst)`. It inspects shared functions to find the
appropriate directory getter. When `load_secret_key` needs to load `cc.bin`, it uses
`pubkeydir(inst)` if both exist.

### Static library cross-references

When external libraries reference each other (e.g., `mlp_function_split_0` uses symbols
from `vector_constants`), the generated CMakeLists.txt lists each library twice on the
link line to resolve circular dependencies.

### Text vs binary dataset files

`load_matrix<T>(path, cols)` auto-detects file format by extension:
- `.txt` / `.csv` → reads as space-separated text (`read_text_matrix`)
- `.bin` / other → reads as raw binary (`read2vecs`)

### `argmax` returns a pair

`argmax(vec)` returns `(index, value)` as a `std::pair<uint32_t, T>`. If you only need
the index, use tuple destructuring: `let (idx, _) = argmax(v)`.

---

## Feature Reference

### Language Features Used Across Examples

| Feature | simple | fetch | NID | ml-inference | password |
|---------|--------|-------|-----|-------------|----------|
| `scheme CKKS { ... }` | yes | yes | yes | yes | yes |
| `requires { ... }` | yes | yes | yes | yes | yes |
| `@hardware` | yes | yes | yes | yes | — |
| `wire` types | yes | yes | yes | yes | yes |
| `match` expressions | yes | yes | — | — | — |
| `chebyshev()` | — | yes | yes | — | yes |
| `extern_call()` | — | — | — | yes | — |
| `extern ... from` | — | — | — | — | — |
| Indexed wire types `[*]` | — | — | — | yes | — |
| Tuple return types | — | yes | yes | — | — |
| `load_model()` | — | — | yes | — | — |
| `load_matrix<T>()` | — | yes | yes | yes | — |
| `str()` | — | — | — | yes | — |
| `argmax()` | — | yes | — | yes | — |
| `tile()` | — | — | — | yes | — |
| `slot_mask()` | — | yes | — | — | — |
| `replicate` capability | — | yes | — | — | — |
| `running_sums` capability | — | yes | — | — | — |
| `rotate` capability | — | — | — | yes | — |
| Explicit `save()`/`load()` | yes | — | — | — | yes |

### Per-Example Summary

| Example | DSL Lines | C++ Generated | Binaries | External Libs |
|---------|-----------|---------------|----------|---------------|
| simple | ~220 | ~6 files | 4 | — |
| fetch-by-similarity | ~590 | ~9 files | 8 | slot_replication, running_sums |
| fhe-NetworkMonitor | ~410 | ~6 files | 4 | — |
| ml-inference-fhe | ~270 | ~7 files | 4 | mlp_openfhe, mlp_encryption_utils, mlp_common, mlp_bridge |
| password-retrieval | ~290 | ~8 files | 5 | — |
