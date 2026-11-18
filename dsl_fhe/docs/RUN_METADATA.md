# Run metadata: what a build and a run write about themselves

A generated program can describe its own build and run, so that a runtime can
rebuild the plaintexts of its data transforms, bind its inputs again and run
its recorded stages without the program. This page describes every such file:

| File | Written by | When | Section |
|---|---|---|---|
| `nb_params.json` | `nbc compile` | a build with `--set` or `--scheme` | [Build parameters](#build-parameters-nb_paramsjson) |
| `NB_DT_RECIPES` file | the generated program | the variable is set | [`nb.data_transform_recipe`](#nbdata_transform_recipe-version-1) |
| `vectors.jsonl`, `pv_<n>.f64` | the generated program | `NB_PLAINTEXT_VECTORS` is set | [`nb.plaintext_vector`](#nbplaintext_vector-version-1) |
| tags on ciphertexts | the generated program | the FIDESlib backend | [Input and output tags](#input-and-output-tags) |
| `cc.bin.dev` | FIDESlib, beside the serialized context | the FIDESlib backend, always | [The context's sidecar](#the-contexts-sidecar-ccbindev) |
| `fideslib.input_roles.json`, `fideslib.input_<name>.bin` | FIDESlib's haze engine, in the program directory | the haze engine records a stage | [The recording's roles](#the-recordings-roles-fideslibinput_rolesjson) |

The run-time files are off by default: a run that sets none of the
environment variables below writes none of them and behaves as before.

| Environment variable | What the run writes |
|---|---|
| `NB_DT_RECIPES=<file>` | One line per product of a ciphertext by a diagonal set (`linear_transform`, each set of `linear_transforms`), appended to `<file>`: schema `nb.data_transform_recipe` |
| `NB_LT_RECIPES=<file>` | The earlier name of `NB_DT_RECIPES`, read only when `NB_DT_RECIPES` is unset or empty |
| `NB_PLAINTEXT_VECTORS=<dir>` | For every plaintext a ciphertext-vector operation encodes (`ct + v`, `ct - v`, `ct * v`): its values in `<dir>/pv_<n>.f64` and one line appended to `<dir>/vectors.jsonl`: schema `nb.plaintext_vector`. `<dir>` is created if missing |

Both files are [JSON Lines](https://jsonlines.org/): one JSON object per
line, each ended by `\n`. The schemas (JSON Schema draft 2020-12) are in
[`../schemas/`](../schemas/). What nbc itself can write about a program's
stages, at compile time, is in [STAGE_SCHEDULE.md](STAGE_SCHEDULE.md).

## `bsgs_diagonals`

```
bsgs_diagonals(w, row0, col0, width, count, bstep, region, length, r_lo, r_hi, s) -> vec<vec<f64>>
```

The diagonal set `linear_transform(ct, d, bstep)` takes for the `width` x
`width` submatrix of the matrix `w` at (`row0`, `col0`), scaled by `s` and
repeated in the regions `r_lo .. r_hi` (inclusive) of the slot vector,
regions `region` slots apart. It returns `count` vectors of `length` slots.
For diagonal `k` (`0 <= k < count`), with `shift = bstep * (k / bstep)` (the
counter-rotation of `k`'s giant step, integer division):

```
d[k][r * region + shift + i] = w[row0 + (i + k) % width][col0 + i] * s    for r in r_lo .. r_hi, 0 <= i < width
d[k][j] = 0 * s                                                           for every other slot j
```

`bstep` must be positive; a row or column outside `w`, or a slot at or past
`length`, is a run-time error. The result is a plain `vec<vec<f64>>`, the
same on every back end. Example: a 2 x 2 product of the lower-right submatrix of
a 4 x 4 matrix, in the first two regions of 8 slots:

```
let w = load_matrix<f64>(root() / "w.bin", 4)
let d = bsgs_diagonals(w, 2, 2, 2, 2, 2, 8, 16, 0, 1, 1.0)
let y = linear_transform(x, d, 2)
```

## `nb.data_transform_recipe`, version 1

Schema: [`data_transform_recipe.schema.json`](../schemas/data_transform_recipe.schema.json).
One line per product, written when the product runs, before its diagonals
are encoded; for the example above, with `w.bin` holding the values 0, 1, ..., 15:

```
{"schema": "nb.data_transform_recipe", "version": 1, "product": 0, "diagonals": 2, "level": 3, "slots": 16, "noise_scale_deg": 1, "recipe": {"kind": "bsgs_diagonals", "set": 0, "file": "w.bin", "rows": 4, "cols": 4, "digest": "ad4d7d3a52923a60", "row0": 2, "col0": 2, "width": 2, "count": 2, "bstep": 2, "region": 8, "length": 16, "r_lo": 0, "r_hi": 1, "scale": 1}}
```

| Field | Type | Meaning |
|---|---|---|
| `schema`, `version` | string, integer | `"nb.data_transform_recipe"`, `1`. Reject a record whose schema or version you do not know |
| `product` | integer >= 0 | The product's number in the process, from 0, in the order the products run |
| `diagonals` | integer >= 1 | The number of diagonals in the set |
| `level` | integer | The level every diagonal is encoded at |
| `slots` | integer | The slot count every diagonal is encoded with |
| `noise_scale_deg` | integer | Always `1` |
| `recipe` | object or null | How the set was built; `null` when it was not built by `bsgs_diagonals`, or was changed after it was |
| `recipe.kind` | string | `"bsgs_diagonals"` |
| `recipe.set` | integer >= 0 | The set's number in the process, from 0, in the order `bsgs_diagonals` returned the sets; products of one set share it |
| `recipe.file` | string or null | The path `load_matrix` read `w` from, as the program spelled it (relative to the run's working directory when relative); `null` when `w` is not a loaded matrix, or was changed after it was loaded |
| `recipe.rows`, `recipe.cols` | integer | The shape of `w` (`cols`: the record length `load_matrix` was given) |
| `recipe.digest` | string | The matrix digest of `w`, below: 16 lowercase hexadecimal digits |
| `recipe.row0` .. `recipe.r_hi` | integer | The arguments of `bsgs_diagonals`, unsigned 32-bit |
| `recipe.scale` | number | Its `s`, printed with 17 significant digits (it reads back to the same float64). A set with a non-finite `s` has no recipe |

**Matrix digest.** A 64-bit hash of the matrix's values (not of the file's
bytes), all arithmetic modulo 2^64, with `P = 0x100000001b3`:

```
h = 0xcbf29ce484222325 XOR rows
for each row, in order:
    h = (h XOR length(row)) * P
    for each value v of the row, in order:
        h = (h XOR bits(v)) * P        # bits(v): v's IEEE-754 binary64 bit pattern, as an unsigned 64-bit integer
        h = h XOR (h >> 31)
```

A set is matched to its recipe by its storage and a digest of its values, so
a copy of a set built by `bsgs_diagonals`, or a set changed after it was
built, has a `null` recipe: its values are then the only description of it.

## `nb.plaintext_vector`, version 1

Schema: [`plaintext_vector.schema.json`](../schemas/plaintext_vector.schema.json).
One line per plaintext, written when it is encoded:

```
{"schema": "nb.plaintext_vector", "version": 1, "tag": "pv:0", "length": 2, "level": 3, "slots": 16, "noise_scale_deg": 1}
```

| Field | Type | Meaning |
|---|---|---|
| `schema`, `version` | string, integer | `"nb.plaintext_vector"`, `1` |
| `tag` | string | `pv:<n>`: the n-th such plaintext of the process, from 0, in the order they are encoded |
| `length` | integer >= 0 | The number of values |
| `level` | integer | The level it is encoded at |
| `slots` | integer | The slot count it is encoded with |
| `noise_scale_deg` | integer | Always `1` |

`pv_<n>.f64`, next to `vectors.jsonl`: the `length` values the plaintext was
encoded from, as IEEE-754 binary64, little-endian, with no header: exactly
`8 * length` bytes. Encoding them with `MakeCKKSPackedPlaintext(values,
noise_scale_deg, level, nullptr, slots)` gives the program's plaintext.

## Input and output tags

With a FIDESlib that has input tags (`FIDESLIB_HAS_INPUT_TAGS`), every
ciphertext and plaintext carries `tag`, an opaque name the caller gives it as
a program input; empty means none. No engine interprets it: a recording
engine writes it next to the input or output it records
([the recording's roles](#the-recordings-roles-fideslibinput_rolesjson)), so
that whoever runs the recorded program can bind that value again.

The generated glue names a ciphertext by its file: one read from a file is
tagged with the path it was read from, and one written to a file carries the
path it is written to while it is marked as an output (its own tag is put
back afterwards, so a ciphertext saved to two files, or read and saved
unchanged, keeps its input name). The path is the one the program passed,
unchanged.

The glue tags no plaintext. A caller that rebuilds plaintexts itself sets
their tags from a hook: `nb_recipe::plain_hook()` sees every plaintext vector
as it is encoded (its tag is its `pv:<n>`), and the recipe hook every
diagonal set of a data transform (its line in the recipes file says what it
is). A plaintext left untagged (a mask, a table the context computes) is a
constant of the program.

## Guarantees of the JSON Lines files

- Within a version, fields are neither removed nor renamed and keep their
  meaning; a new field comes with a new version. Lines are written with the
  fields in the order above.
- Lines are appended, never rewritten: a file named by two runs holds both
  runs' lines, each run's numbers starting from 0. Start from an empty file or
  directory for one run.
- Lines are in the order of the events they describe, and each is flushed
  when written: a run that stops early leaves every line before the stop.
- The numbers (`product`, `set`, `pv:<n>`) count events in one process in
  program order: they are the same in two runs of the same program on the
  same inputs, and change when the control flow does.

## Build parameters: `nb_params.json`

`nbc compile --set NAME=VALUE` and `--scheme FIELD=VALUE` write
`nb_params.json` in the output directory: one JSON object, each parameter
given on the command line and its value. A build with neither writes no file.

```json
{
  "ROUNDS": 5,
  "SCALE": 0.25,
  "TRACE": true,
  "scheme::linear_transform_memory": "20 GB"
}
```

| Key | Value |
|---|---|
| `NAME` (a `--set`) | the value parsed as the constant's declared type: a JSON integer (`u32`, `i64`, ...), number (`f64`), boolean (`bool`) or string (`string`). A name declared in several modules is `<module>::NAME` |
| `scheme::FIELD` (a `--scheme`) | the value as given on the command line, a string |

The same parameters are the `// nbc --set` and `// nbc --scheme` lines at the
top of `nb_shared.h`, and the `nbc:NAME` cache parameters of every `@hardware`
stage, so that a recording made with one value is never replayed for another
([NB_LANGUAGE.md](../NB_LANGUAGE.md), compile-time parameters).

## The context's sidecar: `cc.bin.dev`

With `--backend fideslib`, the client's key generation serializes the
context through FIDESlib, which writes the OpenFHE context to `cc.bin` and,
beside it, `cc.bin.dev`: what OpenFHE's serialization does not keep and the
facade needs to rebuild the same context for a server stage. A server stage
reads both (`nb_fideslib::import_context`) and re-engines the context for the
backend `FIDESLIB_BACKEND` names; the sidecar's own backend is the client's
(`cpu`).

The file is text, eight lines in this order, each `Label: value`:

```
Backend: 0
0 { }
AutoLoadCiphertexts: 1
AutoLoadPlaintexts: 1
RotationIndexes: { 1 2 4 -1 }
KeyDist: 2
BootstrapSlots: { 32768 }
ReducedNoise: 0
```

| Line | Meaning |
|---|---|
| `Backend: <n>` | the engine the context was serialized from: `0` cpu, `1` cuda, `2` haze. Stored, never inferred |
| `<count> { <id> ... }` | the CUDA devices of that engine (empty for the others) |
| `AutoLoadCiphertexts: 0|1` | whether the engine loads a ciphertext onto the device when an operation first uses it |
| `AutoLoadPlaintexts: 0|1` | the same for plaintexts |
| `RotationIndexes: { <i> ... }` | the rotation indices the context's keys were generated for, in generation order |
| `KeyDist: <n>` | the secret key distribution: `0` Gaussian, `1` uniform ternary, `2` sparse ternary, `3` sparse encapsulated |
| `BootstrapSlots: { <s> ... }` | the slot counts a bootstrap key was generated for: the server's engine installs one bootstrap precomputation per entry |
| `ReducedNoise: 0|1` | the bootstrap variant the engine was built with. Required: a sidecar without this line is refused, not defaulted |

A reader fails on a missing or unreadable sidecar, an early end of file, or a
malformed `ReducedNoise` line.

## The recording's roles: `fideslib.input_roles.json`

When the haze engine (`FIDESLIB_BACKEND=haze`) records a stage, it writes the
recorded program into a program directory and, beside it, what each input and
output of the program is, so that a runtime can run the program with values
it binds itself: the keys it holds, the ciphertexts other stages wrote, the
plaintexts it encodes from recipes, the scalars of each launch.

The program directory is
`<FIDESLIB_HAZE_RUNS_DIR, or /tmp/fideslib-haze-runs>/<key>/ctx<N>`: `<key>`
is the process id, or `FIDESLIB_HAZE_PROGRAM_KEY` when set (the same program
run again then reuses the directory and what was lowered there); `ctx<N>`
counts the contexts the process loaded, from 0. The directory is removed when
the context is released unless `FIDESLIB_HAZE_KEEP_RUNS` or
`FIDESLIB_HAZE_PROGRAM_KEY` is set, or the recording is trace-only.

| Environment variable | Effect on the roles |
|---|---|
| `FIDESLIB_HAZE_TRACE_ONLY=1` | record the program and the roles without the data: keys, ciphertexts, tagged plaintexts and scalar inputs are declared, not uploaded (marked `"omitted"`, with empty word files); untagged plaintexts keep their words. The program is written, not run |
| `FIDESLIB_HAZE_SCALAR_INPUTS=1` | each scalar operand of a multiplication or addition becomes an input of the program (role `scalar`), so one recording serves every value |
| `FIDESLIB_HAZE_SCALAR_INPUTS=patch` | each scalar operand stays an immediate of its instruction, listed under `scalars` so that a runtime can patch its words |
| `FIDESLIB_HAZE_OMIT_INPUTS=<role>,...` | empty the recorded words of the inputs with these roles (`key`, `ciphertext`, ...) and mark them `"omitted"`: the runtime supplies them. An untagged plaintext, a constant of the program, is never omitted |

Each input's words are in the program directory as
`fideslib.input_<name>.bin`, `<name>` being the input's name below.

### Shape

```json
{
  "schema": "fideslib.input_roles",
  "version": 1,
  "format": 1,
  "inputs": [
    {"name": "<name>", "role": "key", "id": "auto:5", "digit": 0, "component": "a", "towers": 31, "omitted": true},
    {"name": "<name>", "role": "ciphertext", "component": 0, "tag": "io/enc/state.bin", "noise_scale_deg": 1, "scaling_factor": 1.2089258196146292e+24, "slots": 32768, "towers": 12},
    {"name": "<name>", "role": "plaintext", "tag": "pv:0", "noise_scale_deg": 1, "scaling_factor": 1.2089258196146292e+24, "slots": 32768, "towers": 12},
    {"name": "<name>", "role": "scalar", "tag": "scalar:0", "kind": "mul", "value": 0.5, "noise_scale_deg": 1, "factors": [0, 0], "towers": 2}
  ],
  "scalars": [
    {"tag": "scalar:1", "kind": "add", "value": 1, "noise_scale_deg": 2, "towers": 2, "factors": [0, 0], "sentinels": [0, 0]}
  ],
  "outputs": [
    {"name": "<name>", "role": "ciphertext", "component": 0, "tag": "io/enc/state.bin", "towers": 11}
  ]
}
```

(`<name>` is the input's or output's name in the recorded program; the
numbers are illustrative.)

### Top level

| Field | Meaning |
|---|---|
| `schema`, `version` | `"fideslib.input_roles"`, `1`. Reject a file whose schema or version you do not know |
| `format` | `1`: the layout of the input word files |
| `inputs` | every input of the program, in the order it was uploaded or declared |
| `scalars` | the scalar operands recorded as patchable immediates (`FIDESLIB_HAZE_SCALAR_INPUTS=patch`), in program order |
| `outputs` | every output of the program, in the order it was marked |

### `inputs`

Every input has `name` (its name in the program and in its word file),
`role` and `towers` (the number of RNS limbs of its words). `"omitted": true`
marks an input whose words are not in the recording, for the runtime to
supply: declared without words under trace-only, or emptied by
`FIDESLIB_HAZE_OMIT_INPUTS`; its word file is empty. The other fields depend
on the role:

| Role | Fields | What the runtime binds |
|---|---|---|
| `key` | `id`: `relin`, `auto:<k>` (the rotation key of automorphism index k), or `sparse` (the bootstrap's sparse switching key); `digit`: the key-switching digit; `component`: `"a"` or `"b"` | the key it holds, by identity |
| `ciphertext` | `component` (0 or 1), `tag` (the caller's name, for the generated glue the file it was read from), `noise_scale_deg`, `scaling_factor`, `slots`, and `"extended": true` for a value over the extended basis | the ciphertext another stage wrote under that tag |
| `plaintext` | `tag`, `noise_scale_deg`, `scaling_factor`, `slots`, `extended` as above. An empty `tag` is a constant of the program (a mask, a refresh table, a monomial): its words are always recorded | a tagged plaintext: the one it encodes again from its recipe (`pv:<n>`, a data transform's line) |
| `scalar` | `tag` (`scalar:<n>`, the n-th scalar operand of the process), `kind` (`mul` or `add`), `value` (the operand as recorded), `noise_scale_deg` of the operand it applies to, `factors` (its encoding, one word per limb) | the launch's value, encoded as `factors` are |

### `scalars`

With `FIDESLIB_HAZE_SCALAR_INPUTS=patch`, a scalar operand is not an input:
its words are immediates of its instruction. Each entry has `tag`
(`scalar:<n>`), `kind`, `value`, `noise_scale_deg`, `towers`, `factors` (the
words for the recorded value, one per limb) and `sentinels`. Under trace-only
the immediates recorded are the sentinels: one word per limb, from a
splitmix64 of the operand's number and the limb, in `[2, q_i - 1)` so that
none is 0 or 1. A runtime finds them in the program and replaces them by the
launch's value's words.

### `outputs`

Every output has `name` (its name in the program's outputs), `role`
(`ciphertext`), `component`, `tag` (the caller's name for it: for the
generated glue, the file the stage saves it to) and `towers`. A runtime hands
an output to the next program whose input has the same tag.

### Guarantees

- Within a version, fields keep their name and meaning; a new field may be
  added, a removed or renamed one comes with a new version.
- The file is rewritten by each recording in its program directory.
- Names are unique within `inputs` and within `outputs`; tags are not
  (a ciphertext's two components share its tag, and one value can be read or
  saved twice).
