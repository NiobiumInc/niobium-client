# Niobium `.nb` Language Reference

A practical guide to writing FHE applications in the Niobium DSL.
For the formal grammar, see [GRAMMAR.md](GRAMMAR.md).

---

## Program Structure

An `.nb` program is split across three files by convention:

| File | Contains | Domain |
|---|---|---|
| `shared.nb` | Constants, enums, structs, wire types, helper functions | Both |
| `client.nb` | Key generation, encryption, decryption stages | Client |
| `server.nb` | Encrypted computation stages | Server |

Files reference each other with `use shared::*` at the top.

```nb
use shared::*
```

### External Module Declarations

Declare external C++ modules that should be included and linked:

```nb
extern weights from "vector_constants"
```

This generates `#include "vector_constants.h"` in the shared header and adds the
module's source files to the CMakeLists.txt build. Vendor the C++ sources in the
example directory and pass `-DLOCAL_SRC_DIR=...` so the example builds from this
repo alone (no external repo required).

---

## Declarations

### Constants

```nb
const THRESHOLD: f64 = 0.8
const PAYLOAD_DIM: u32 = 8
const MAX_VAL: u32 = 256
```

Constants are evaluated at compile time and emitted as `constexpr` in C++.

### Enums

```nb
enum InstanceSize { Toy, Small, Medium, Large }
enum Operation { ADD, MUL, ROTATE_ADD, ADDI, MULI }
```

Variants are auto-numbered starting from 0. Enums compile to C++ `enum` types and
can be used in `match` expressions and as CLI arguments (passed as integers).

### Structs

```nb
struct Instance {
    size: InstanceSize,
    record_dim: u32,
    ring_dim: u32,
    degrees: vec<u32>,
}
```

Structs are plain data containers. Fields use the same types as function parameters.
Struct literals use `Name { field: value }` syntax with shorthand for same-name bindings:

```nb
Instance { size, record_dim: 128, ring_dim: 2048, degrees: [8, 4, 4] }
// 'size' is shorthand for 'size: size'
```

### Wire Types

Wire types define what crosses the client-server boundary. The compiler generates all
serialization code automatically.

```nb
wire CryptoParams {
    context: CryptoContext,
    public_key: PublicKey,
    eval_mult_key: EvalMultKey,
    eval_rot_keys: EvalAutomorphismKeys,
}

wire EncryptedInput {
    ciphertext: enc<f64>,
}

wire EncryptedResult {
    ciphertext: enc<f64>,
}
```

Wire types may NOT contain `SecretKey` (enforced by the semantic analyzer).
Wire types with a single `enc<T>` field get optimized serialization.

### Scheme Configuration

Declares the FHE scheme parameters. Placed in `client.nb`:

```nb
scheme CKKS {
    security: 128-classic    // or: not_set
    ring_dim: 2048           // optional: literal, inst.ring_dim, or omit entirely
    key_dist: uniform_ternary
    key_switch: hybrid
    scaling: flexible_auto
    precision: 42 bits
    first_mod: 57 bits
    depth: 23
}
```

The scheme can be overridden at runtime (e.g., for toy instances):

```nb
if inst.size == Toy {
    scheme.override(security: not_set, ring_dim: 2048)
}
```

### Requires Declaration

Declares which FHE operations the application uses. The compiler generates the
appropriate key generation calls:

```nb
requires { add, mul, rotate }
// Also available: chebyshev, running_sums, replicate
```

---

## Type System

### Primitive Types

| Type | C++ equivalent | Description |
|---|---|---|
| `bool` | `bool` | Boolean |
| `u8`, `u16`, `u32`, `u64` | `uint8_t`, ..., `uint64_t` | Unsigned integers |
| `i8`, `i16`, `i32`, `i64` | `int8_t`, ..., `int64_t` | Signed integers |
| `f32`, `f64` | `float`, `double` | Floating point |
| `string` | `std::string` | String |
| `path` | `std::filesystem::path` | File path |

### Composite Types

| Type | C++ equivalent | Description |
|---|---|---|
| `vec<T>` | `std::vector<T>` | Dynamic vector |
| `mat<T>` | `std::vector<std::vector<T>>` | 2D matrix |
| `enc<T>` | `Ciphertext<DCRTPoly>` | Encrypted value |
| `enc<vec<T>>` | `Ciphertext<DCRTPoly>` | Encrypted vector (packed slots) |
| `(T1, T2)` | `std::pair<T1, T2>` | Tuple (2 elements) |
| `(T1, T2, T3)` | `std::tuple<T1, T2, T3>` | Tuple (3+ elements) |

### Encryption Type Rules

The `enc<T>` type participates in arithmetic:

```
enc<T> + enc<T>  ->  enc<T>     (EvalAdd, both ciphertext)
enc<T> + T       ->  enc<T>     (EvalAdd, cipher-plain)
enc<T> * enc<T>  ->  enc<T>     (EvalMult, increases depth)
enc<T> * T       ->  enc<T>     (EvalMult, cipher-plain, no depth increase)
enc<T> - enc<T>  ->  enc<T>     (EvalSub)
```

The compiler tracks multiplicative depth through expressions.

---

## Functions

### Basic Functions

```nb
fn helper(x: f64, y: f64) -> f64 {
    return x + y
}
```

Functions in `shared.nb` (no domain annotation) are available to both client and server.

### Default Parameters

```nb
fn compute(inst: Instance, op: Operation = 0, immediate: f64 = 0.0) { ... }
```

Parameters with defaults become optional CLI arguments when the function is a stage.

### Functions Taking Encrypted Values

Functions that operate on encrypted data need `CryptoContext`. The compiler auto-detects
functions with `enc<T>` parameters or return type and injects `cc` as the first parameter:

```nb
// In .nb:
fn dispatch(op: Operation, ct1: enc<f64>, ct2: enc<f64>, imm: f64) -> enc<f64> { ... }

// Generated C++:
Ciphertext<DCRTPoly> dispatch(CryptoContext<DCRTPoly> cc, Operation op,
                               Ciphertext<DCRTPoly> ct1, ...) { ... }
```

---

## Annotations

### Domain Annotations

```nb
@client   // Function runs in client domain (has secret key)
@server   // Function runs in server domain (no secret key)
```

**Enforcement**: Server functions cannot call `decrypt()`, `load_secret_key()`, or
reference `SecretKey`. Violations are compile errors.

### Stage Annotation

Each `@stage("name")` function compiles to a separate binary:

```nb
@client @stage("key_generation")
fn generate_keys(inst: Instance, mult_depth: u32 = 3) -> writes(CryptoParams) { ... }
```

The generated binary accepts CLI arguments:
- First positional: instance size (integer)
- Additional positional: non-bool params in declaration order
- `--flag_name`: boolean parameters

### Hardware Annotation

Generates Niobium record/replay instrumentation for server stages:

```nb
@server @stage("compute")
@hardware(cache_key: ["workload_size", "operation"])
fn compute(inst: Instance, op: Operation = 0) -> reads(CryptoParams), writes(EncryptedResult) { ... }
```

This generates Niobium *client* record/replay instrumentation (`libnbfhetch`):
`init()` + `enable_auto_tagging()`, a recording path (`start()`/`probe()`/
`stop()`), and an always-run replay path (`replay()`/`result()`). Inputs, eval
keys, and the crypto context are tagged automatically by the
instrumented-OpenFHE deserialize hooks (cooperative auto-tagging) — no explicit
`tag_input`/`tag_keys` calls are emitted.

### Return Specifications

Stage functions use `reads`/`writes` instead of simple return types:

```nb
fn compute(inst: Instance)
    -> reads(CryptoParams, EncryptedInput),
       writes(EncryptedResult)
{ ... }
```

- `reads(T)` — deserialize wire type T at function entry
- `writes(T)` — serialize wire type T at function exit
- `reads_plaintext(path)` — read raw binary from path
- `writes_plaintext(path)` — write raw binary to path
- Indexed: `reads(EncryptedDB[batch_id])` — load single batch

---

## Statements

### Let Bindings

```nb
let x = 42
let name: string = "hello"
let (degree, outscale) = if count_only { (247, 1.0) } else { (59, 0.504) }
```

Let bindings support:
- Type inference (`let x = ...`)
- Explicit types (`let x: f64 = ...`)
- Destructuring (`let (a, b) = ...`)
- Rebinding (same name in same scope becomes assignment, not re-declaration)

### Assignment

```nb
result = (result + ct2) * ct2
matrix[i][j] = value
acc[j] = acc[j] + term
```

### Return

```nb
return EncryptedResult { ciphertext: result }
return value
return   // void return
```

### Assert

```nb
assert rows(db) == inst.db_size
assert max_val > threshold, "marker not found"
```

---

## Control Flow

### If Statement / Expression

```nb
// Statement (no else required)
if inst.size == Toy {
    scheme.override(security: not_set, ring_dim: 2048)
}

// Expression (else required, produces a value)
let (degree, outscale) = if count_only { (247, 1.0) } else { (59, 0.504) }
```

### For Loop / Expression

```nb
// Loop statement
for i in 0..n {
    result = result + rotate(result, 1 << i)
}

// Destructured loop
for (i, row) in enumerate(batch) {
    encrypt(params.public_key, row, level: enc_level)
}

// For-expression (produces a vector of results)
let encrypted = for row in batch {
    encrypt(pk, row, level: 5)
}

// Inclusive range
for i in 1..=max_n_match(inst) { ... }

// Reversed range
for i in (0..s).rev() { ... }
```

### Match Statement / Expression

```nb
// Expression (each arm produces a value)
fn instance(size: InstanceSize) -> Instance {
    match size {
        Toy    => Instance { size, ring_dim: 2048 },
        Small  => Instance { size, ring_dim: 65536 },
        Medium => Instance { size, ring_dim: 65536 },
        Large  => Instance { size, ring_dim: 65536 },
    }
}

// Multi-statement arms use blocks
match op {
    LARGE_ADD_MUL => {
        let result = ct1
        for i in 0..n { result = (result + ct2) * ct2 }
        return result
    },
    ADD => ct1 + ct2,
}
```

---

## Expressions

### Arithmetic Operators

| Operator | On encrypted | Compiles to |
|---|---|---|
| `a + b` | enc + enc | `NullSafeEvalAdd(cc, a, b)` |
| `a + b` | enc + plain | `cc->EvalAdd(a, b)` |
| `a - b` | enc - enc/plain | `cc->EvalSub(a, b)` |
| `a * b` | enc * enc | `cc->EvalMult(a, b)` |
| `a * b` | enc * plain | `cc->EvalMult(a, b)` |
| `a *_norelin b` | enc * enc | `cc->EvalMultNoRelin(a, b)` |
| `a ^ b` | plain only | `std::pow(a, b)` |
| `a / b` | plain only | `a / b` |

### Comparison and Logical

```nb
a == b    a != b    a < b    a > b    a <= b    a >= b
a && b    a || b    !a
a ~= b    // approximate equality (CKKS)
```

### Pipe Operator

Left-to-right function composition:

```nb
data |> transpose |> batch(n_slots(inst)) |> scale(0.5)
// Desugars to: scale(batch(transpose(data), n_slots(inst)), 0.5)

reduce(+, result) |> slot_sum(n_slots(inst))
// Desugars to: slot_sum(reduce(+, result), n_slots(inst))
```

### Type Cast

```nb
round(value) |> as i16
count |> as i16
int(imm)     // cast to integer (in function-call style)
```

### Closures

```nb
|x| x + 1                              // single expression
|x| sigmoid(x - THRESHOLD, outscale)   // expression with captures
|r, m| r * m                           // two parameters
|x| { let y = x + 1; return y * 2 }   // block body
```

### Array Literals

```nb
[8, 4, 4]
[value_a]
[]                // empty
[[count]]         // nested
```

### Struct Literals

```nb
Instance { size, record_dim: 128, ring_dim: 2048, degrees: [8, 4, 4] }
EncryptedResult { ciphertext: result }
CryptoParams { context: keys.context, public_key: keys.public }
```

### Field Access and Indexing

```nb
inst.size
inst.ring_dim
keys.public
result[0]
matrix[i][j]
matrix[i..i+PAYLOAD_DIM, col]   // 2D slice
db.rows[j][i]
```

### Method Calls

```nb
replicator.replicate(eqry)
results.push(rec)
scheme.override(security: not_set, ring_dim: 2048)
```

---

## Built-in Functions

### FHE Operations

| Function | Description | C++ output |
|---|---|---|
| `encrypt(pk, data, level: n)` | Encrypt data with public key | `MakeCKKSPackedPlaintext` + `Encrypt` |
| `decrypt(sk, ct)` | Decrypt ciphertext | `Decrypt` + `GetRealPackedValue` |
| `keygen()` | Generate keys from scheme config | Full key generation IIFE |
| `rotate(ct, n)` | Rotate ciphertext slots | `cc->EvalRotate(ct, n)` |
| `negate(ct)` | Negate ciphertext | `cc->EvalNegate(ct)` |
| `relin(ct)` | Relinearize after *_norelin | `cc->Relinearize(ct)` |
| `chebyshev(fn, ct, domain:, degree:)` | Chebyshev function evaluation | `cc->EvalChebyshevFunction(...)` |
| `slot_sum(ct, n)` | Sum all slots | `cc->EvalSum(ct, n)` |
| `running_sums(cts, stride:, depth:)` | In-place running sums | `RunningSums(cc, ...).eval_in_place(cts)` |
| `reduce(+, vec)` | Reduce vector with add | `EvalAddInPlace` accumulation |
| `clone(vec)` | Deep-copy ciphertext vector | `.Clone()` each element |
| `zero()` | Null ciphertext (for accumulation) | `Ciphertext<DCRTPoly>()` |
| `mul_monomial(ct, n)` | Low-level monomial multiply | `cc->GetScheme()->MultByMonomial(ct, n)` |

### Data I/O

| Function | Description |
|---|---|
| `load(WireType, from: path)` | Deserialize a wire type from disk |
| `load_all(WireType, from: path)` | Load all indexed intermediate results |
| `save(WireType{...}, to: path)` | Serialize a wire type to disk |
| `save_secret_key(sk, path)` | Save secret key (client only) |
| `load_secret_key(path)` | Load secret key (client only) |
| `load_matrix<T>(path, dim)` | Read matrix from disk (text for .txt/.csv, binary otherwise) |
| `load_vec<T>(path, dim)` | Read binary vector from disk |
| `load_model(path)` | Load binary model file (auto-generates loader from struct) |
| `extern_call("func", args...)` | Call external C++ function with `cc` auto-prepended |
| `str(value)` | Convert value to string (`std::to_string`) |
| `print(value)` | Print value to stdout |

### Collection Operations

| Function | Description |
|---|---|
| `map(collection, fn)` | Apply function to each element |
| `zip_map(a, b, fn)` | Apply binary function element-wise |
| `len(v)` | Vector length |
| `rows(m)` | Matrix row count |
| `sort(v)` | Sort a vector |
| `argmax(v)` | Index and value of maximum element (returns pair) |
| `enumerate(v)` | Iterate with (index, element) pairs |
| `stride(start, end, step)` | Generate integer sequence with step |
| `vec_zeros<T>(n)` | Zero-initialized vector |
| `mat_zeros<T>(r, c)` | Zero-initialized matrix |

### Data Transformation

| Function | Description |
|---|---|
| `transpose(matrix)` | Transpose 2D matrix |
| `batch(matrix, size)` | Split into batches of `size` rows |
| `tile(vec, n)` | Repeat vector to fill `n` elements |
| `prepend_column(matrix, val)` | Add column with constant value |
| `scale(batches, factor)` | Scale all values by factor |
| `slot_mask(n_slots, n_cols, row_range: a..b)` | Create a binary slot mask |

### Path and Instance Helpers

| Function | Description |
|---|---|
| `root()` | Project root directory |
| `instance(size)` | Create Instance from InstanceSize |
| `instance_name(size)` | String name for an instance size |
| `iodir(inst)` | I/O directory for instance |
| `keydir(inst)` | Key directory for instance |
| `encdir(inst)` | Encrypted data directory for instance |
| `datadir(inst)` | Dataset directory for instance |

### Math

| Function | Description |
|---|---|
| `abs(x)` | Absolute value |
| `round(x)` | Round to nearest integer |
| `ceil_div(a, b)` | Ceiling division |
| `log2(x)` | Log base 2 (integer) |
| `exp(x)` | Exponential |

---

## Codegen Patterns

### How `+` compiles for encrypted values

- Both operands encrypted: `NullSafeEvalAdd(cc, a, b)` — null-safe for accumulation
- One operand is a plain scalar: `cc->EvalAdd(a, scalar)` — direct
- Both plain: `(a + b)` — normal C++

### How wire types serialize

Each wire type generates custom serialization:

- **CryptoParams**: 4 files (`cc.bin`, `pk.bin`, `mk.bin`, `rk.bin`) using both
  `Serial::SerializeToFile` and stream-based eval key serialization
- **Single-ciphertext wires** (e.g., `EncryptedInput`, `EncryptedResult`):
  Direct `SerializeToFile`/`DeserializeFromFile` of the ciphertext field
- **EncryptedDB**: Batch directories with individual row/payload files

### How stages become binaries

A `@stage("name")` function generates a `.cpp` file with:
1. The function implementation
2. A `main()` with CLI argument parsing
3. Key loading (for server stages)
4. Wire type serialization at entry/exit
5. Niobium client record/replay instrumentation (if `@hardware`) — always
   compiled in; cooperative auto-tagging via `libniobium_client_autofacade`

---

## Common Patterns

### Encrypt two values and compute

```nb
// client.nb
@client @stage("encrypt")
fn encrypt_inputs(inst: Instance, a: f64 = 3.0, b: f64 = 5.0)
    -> reads(CryptoParams), writes(EncryptedInput)
{
    let params = load(CryptoParams, from: keydir(inst))
    let ct_a = encrypt(params.public_key, [a], slots: 1)
    let ct_b = encrypt(params.public_key, [b], slots: 1)
    save(EncryptedInput { ciphertext: ct_a }, to: iodir(inst) / "a.bin")
    save(EncryptedInput { ciphertext: ct_b }, to: iodir(inst) / "b.bin")
}

// server.nb
@server @stage("compute")
@hardware(cache_key: ["workload_size"])
fn compute(inst: Instance)
    -> reads(CryptoParams, EncryptedInput), writes(EncryptedResult)
{
    let params = load(CryptoParams, from: keydir(inst))
    let a = load(EncryptedInput, from: iodir(inst) / "a.bin").ciphertext
    let b = load(EncryptedInput, from: iodir(inst) / "b.bin").ciphertext
    let result = a + b
    return EncryptedResult { ciphertext: result }
}
```

### Dispatch via match expression

```nb
fn dispatch(op: Operation, ct1: enc<f64>, ct2: enc<f64>, imm: f64) -> enc<f64> {
    match op {
        ADD  => ct1 + ct2,
        MUL  => ct1 * ct2,
        ADDI => ct1 + imm,
        MULI => ct1 * imm,
    }
}
```

### Batch encrypt with for-expression

```nb
return EncryptedDB {
    rows: for (i, batch) in enumerate(db_batched) {
        for (j, row) in enumerate(batch) {
            encrypt(params.public_key, row, level: enc_level)
        }
    },
}
```

### Pipeline with pipes

```nb
let processed = payloads
    |> prepend_column(2 * MAX_VAL * PRECISION)
    |> transpose
    |> batch(n_slots(inst))
    |> scale(1.0 / PRECISION)
```

### Chebyshev approximation with closure

```nb
result = for ct in result {
    chebyshev(|x| sigmoid(x - THRESHOLD, outscale), ct,
              domain: [-1.0, 1.0], degree: degree)
}
```

### Tuple return types

Functions can return tuples, which compile to `std::pair` (2 elements) or
`std::tuple` (3+ elements):

```nb
fn autoencoder_forward(inst: Instance, ae: Autoencoder,
                       inputs: vec<enc<vec<f64>>>, sig_coeffs: vec<f64>)
    -> (vec<enc<vec<f64>>>, vec<enc<vec<f64>>>)
{
    let hidden: vec<enc<vec<f64>>> = for j in 0..n_hid { ... }
    let recon: vec<enc<vec<f64>>> = for i in 0..n_vis { ... }
    return (hidden, recon)
}

// Destructure at call site:
let (hidden, recon) = autoencoder_forward(inst, ae, inputs, coeffs)
```

### Chebyshev with named constants

```nb
chebyshev(base_sigmoid, acc, domain: [SIGMOID_LO, SIGMOID_HI],
          degree: CHEB_ORDER)
```

### Loading a binary model file

```nb
let model = load_model(model_file(inst))
```

When the program defines a `KitNETModel` struct, the codegen auto-generates a
`load_kitnet_model()` function that reads the binary model format (header,
Chebyshev coefficients, feature maps, autoencoder weights, anomaly detector weights).

### Calling external C++ functions

Use `extern_call` to invoke machine-generated or hand-written C++ functions.
The crypto context `cc` is automatically prepended as the first argument.

```nb
// In shared.nb: declare the external module
extern weights from "vector_constants"

// In server.nb: wrapper function that delegates to external C++
fn mlp(ct: enc<vec<f64>>) -> enc<vec<f64>> {
    extern_call("mlp", ct)
}

// Usage in a stage:
let result = mlp(input.ciphertext)
```

The codegen detects wrapper functions (body = single `extern_call`) and generates
direct calls to the external function with `cc` prepended. The CMakeLists.txt
auto-discovers source files matching the function name pattern (e.g., `mlp_openfhe.cpp`,
`mlp_function_split_0.cpp`, `mlp_common.cpp`, `mlp_bridge.cpp`) in `LOCAL_SRC_DIR/`
— vendor those sources in the example directory so it builds from this repo alone.
(An optional `SUBMISSION_DIR/src/` is also searched, and takes precedence, for
swapping in a real/large model from a separate package; see ml-inference.)

**Required files for `extern_call("X", ...)`** (by convention — missing headers cause build errors):
- `X_openfhe.h` — model/library header declaring the underlying function; reachable via `LOCAL_SRC_DIR` (or, if used, `SUBMISSION_DIR/include`)
- `X_bridge.h` — DSL bridge header declaring the `X(cc, ...)` wrapper; reachable via `LOCAL_SRC_DIR`

See `dsl_fhe/examples/ml-inference-fhe/mlp_bridge.{h,cpp}` for a reference implementation.

### Indexed wire types for batch processing

```nb
// Batch save (multiple files):
fn encrypt_input(inst: Instance) -> writes(EncryptedInput[*]) {
    for i in 0..inst.batch_count {
        save(EncryptedInput { ciphertext: ct },
             to: dir / ("cipher_input_" + str(i) + ".bin"))
    }
}

// Batch load (per-index):
fn compute(inst: Instance, batch_id: u32)
    -> reads(EncryptedInput[batch_id]) { ... }

// Batch load (all at once):
fn decrypt(inst: Instance) -> reads(EncryptedResult[*]) {
    for i in 0..inst.batch_count {
        let encrypted = load(EncryptedResult,
                             from: dir / ("result_" + str(i) + ".bin"))
    }
}
```

### Vector-of-ciphertext wire types

Wire types with `vec<enc<T>>` fields serialize per-element:

```nb
wire EncryptedFeatures {
    features: vec<enc<vec<f64>>>,   // features_0.bin, features_1.bin, ...
}
```
