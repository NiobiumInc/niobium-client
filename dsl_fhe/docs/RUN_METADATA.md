# Run metadata: data transform recipes, plaintext vectors, ciphertext tags

A generated program can describe its own run, so that a runtime can rebuild
the plaintexts of its data transforms and bind its inputs again without the
program. Everything here is off by default: a run that sets none of the
environment variables writes nothing and behaves as before.

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

## Ciphertext tags

With a FIDESlib that has input tags (`FIDESLIB_HAS_INPUT_TAGS`), the
generated glue names a ciphertext by its file: one read from a file is tagged
with the path it was read from, and one written to a file carries the path
it is written to while it is marked as an output (its own tag is put back
afterwards). A recording back end writes the tags next to the program's
inputs and outputs. The path is the one the program passed, unchanged.

## Guarantees

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
