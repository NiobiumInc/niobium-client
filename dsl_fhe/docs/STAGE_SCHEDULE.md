# Stage schedule: the stages an entry point runs, as data

A server stage can call other server stages. When that is all its body does,
the stage is a *schedule*: a fixed sequence of stage runs, possibly in loops,
whose bounds and arguments are known before the first one starts. `nbc
compile --schedule` writes that sequence to `<outdir>/nb_schedule.json`, so
that a runtime can run the stages itself (each one as a kernel, the state
between them kept where it is, the inputs bound by name) instead of the
entry point's binary.

The entry point still compiles to a binary of its own, which runs the stages
it calls in order, on the host, through the files they `load` and `save`:
the stages a stage calls are compiled into its binary as ordinary functions
(a called `@hardware` stage is not recorded on its own there).

The file is written only with `--schedule`: without it, `nbc compile` writes
what it wrote before. The schema (JSON Schema draft 2020-12) is
[`../schemas/stage_schedule.schema.json`](../schemas/stage_schedule.schema.json).

## What qualifies

An *entry point* is a `@server @stage` function whose body calls at least one
other stage. It has a schedule when its body has only:

- calls of `@server @stage` functions, as statements;
- `for v in lo..hi` (or `lo..=hi`) loops whose body qualifies in turn;

and when:

- every loop bound and every stage argument is a literal, a loop variable, a
  parameter of the entry point (or one of its fields, `inst.size`), a
  top-level `const` (a build parameter), or `+ - * / %` on those;
- every stage it calls is a step: a stage that calls no stage, returns
  nothing (its results are `save`d), and whose `load` and `save` calls sit in
  its own body, outside loops, branches and closures (the functions it calls
  load and save nothing), with paths made of its parameters, constants, path
  helpers (`statedir(inst, i)`), `root()`, `str()` and the values its
  top-level `let`s bind from those;
- the entry point itself declares no `writes(...)`.

An entry point that does not qualify has no schedule, and `nbc compile
--schedule` prints why, one line each:

```
nbc: no stage schedule for run_mixed: server.niob:67: arithmetic on encrypted values outside a stage
```

A stage that calls no stage is a step, never an entry point: it gets no line.
When no server stage calls another, `nbc` says so once.

## Example

```
const ROUNDS: u32 = 3
const PARTS: u32 = 2
fn statedir(inst: Instance, i: u32) -> path { encdir(inst) / ("step_" + str(i)) }

@server @stage("update")
@hardware(cache_key: ["workload_size", "i", "j"])
fn update(inst: Instance, i: u32 = 0, j: u32 = 0, scale: f64 = 0.5) -> reads(CryptoParams, State) {
    let params = load(CryptoParams, from: keydir(inst))
    let s = load(State, from: encdir(inst) / "state.bin").ciphertext
    save(Part { ciphertext: s * scale }, to: statedir(inst, i) / ("part_" + str(j)) / "part.bin")
    save(State { ciphertext: s + s }, to: encdir(inst) / "state.bin")
}

@server @stage("run")
fn run(inst: Instance, first: u32 = 0) -> reads(CryptoParams) {
    prepare(inst)
    for i in first..ROUNDS {
        for j in 0..PARTS {
            update(inst, i, j)
        }
        combine(inst, i)
    }
}
```

(`prepare` and `combine` are steps like `update`; the whole circuit is in
`xcomp/tests/test_schedule.py`.) `nbc compile --schedule --set ROUNDS=5`
writes, the `prepare` and `combine` steps elided and each path's `expr`
shortened:

```json
{
  "schema": "nb.stage_schedule",
  "version": 1,
  "params": {"PARTS": 2, "ROUNDS": 5},
  "entries": [
    {
      "entry": "run",
      "args": [{"name": "inst", "type": "Instance"}, {"name": "first", "type": "u32", "default": 0}],
      "steps": [
        {"stage": "prepare", "...": "..."},
        {"for": "i", "from": {"arg": "first"}, "to": {"param": "ROUNDS"}, "body": [
          {"for": "j", "from": 0, "to": {"param": "PARTS"}, "body": [
            {"stage": "update",
             "hardware": {"cache_key": ["workload_size", "i", "j"]},
             "args": {"inst": {"arg": "inst"}, "i": {"var": "i"}, "j": {"var": "j"}, "scale": 0.5},
             "reads": [
               {"wire": "CryptoParams", "path": {"text": "keydir(inst)", "expr": {"call": "keydir", "args": [{"arg": "inst"}]}, "vars": []}},
               {"wire": "State", "path": {"text": "encdir(inst) / \"state.bin\"", "expr": {"...": "..."}, "vars": []}}],
             "writes": [
               {"wire": "Part", "path": {"text": "statedir(inst, i) / (\"part_\" + str(j)) / \"part.bin\"", "expr": {"...": "..."}, "vars": ["i", "j"]}},
               {"wire": "State", "path": {"text": "encdir(inst) / \"state.bin\"", "expr": {"...": "..."}, "vars": []}}]}
          ]},
          {"stage": "combine", "...": "..."}
        ]}
      ]
    }
  ]
}
```

`update` reads and writes the same state, `State` at `encdir(inst) /
"state.bin"`: it is in both lists. Its `Part` depends on both loop
variables: one per iteration.

## `nb.stage_schedule`, version 1

| Field | Type | Meaning |
|---|---|---|
| `schema`, `version` | string, integer | `"nb.stage_schedule"`, `1`. Reject a document whose schema or version you do not know |
| `params` | object | Every build parameter (top-level `const`) an expression of the document names, with its value in this build: its default, or what `--set` gave it |
| `entries` | array | One per entry point that has a schedule, in declaration order |
| `entries[].entry` | string | The entry point's stage name, the name of its binary |
| `entries[].args` | array | Its parameters in order: `name`, `type` (as declared) and, when it has one, `default` (an expression) |
| `entries[].steps` | array | Its body: steps and loops, in program order |

A **loop** is `for v in from..to { body }`:

| Field | Type | Meaning |
|---|---|---|
| `for` | string | The loop variable |
| `from` | expression | Its first value |
| `to` | expression | One past its last: the body runs for `from`, `from + 1`, .. `to - 1`, not at all when `to <= from`. `lo..=hi` is written with `to` = `hi + 1` |
| `body` | array | Steps and loops |

A **step** is one run of a stage:

| Field | Type | Meaning |
|---|---|---|
| `stage` | string | The stage's name, the name of its binary |
| `hardware` | object or null | Its `@hardware` annotation: `{"cache_key": [...]}`; `null` when it has none |
| `args` | object | One expression per parameter of the stage, in its order: the call's argument, or the parameter's default |
| `reads` | array of state | Its `load` calls in program order, then the types of its `reads(...)` clause that no `load` names |
| `writes` | array of state | Its `save` calls in program order, then the types of its `writes(...)` clause that no `save` names |

A **state** is one wire the step reads or writes:

| Field | Type | Meaning |
|---|---|---|
| `wire` | string or null | The wire type; `null` for a clause entry that names only a path |
| `path` | path or null | Where: the `from:` of its `load`, the `to:` of its `save`; `null` for a clause entry the stage does not `load` or `save` (its `main()` places it) |
| `index` | expression | Only for an indexed wire, `load(W[k], ...)` or `reads(W[k])`: the index; `"*"` for `reads(W[*])` |

A **path** is the program's path with the step's arguments in place of the
stage's parameters, and the stage's own `let`s in place of their names:

| Field | Type | Meaning |
|---|---|---|
| `text` | string | In the language's spelling: `statedir(inst, i) / "part.bin"` |
| `expr` | expression | The same, as an expression |
| `vars` | array of string | The loop variables it depends on, in order of appearance: a path with none is the same in every iteration |

A path that names a directory is the wire's directory, one file per field
(`<field>.bin`), as `load` and `save` read and write it. Two states are the
same state when their wire and their `expr` are equal: that is how a runtime
sees which step's output is which step's input.

An **expression** is one of:

| Form | Meaning |
|---|---|
| a number, a string, `true`, `false` | A literal |
| `{"var": v}` | The value of the enclosing loop over `v` |
| `{"arg": a}` | The entry point's parameter `a`, as the run was given it |
| `{"param": p}` | The build parameter `p`; its value is `params[p]` |
| `{"field": f, "of": e}` | Field `f` of the struct `e`, `e.f`: `inst.size` |
| `{"op": o, "args": [e]}`, `{"op": o, "args": [l, r]}` | `-e`; `l o r` for `o` in `+ - * / %`, as in the language: integer arithmetic, `+` concatenating strings, `/` joining paths when `l` is a path |
| `{"call": f, "args": [...]}` | A call of the program's function `f` (a path helper such as `statedir`) or of the builtins `root()` and `str()` |

Names are the program's: a declaration of an imported module is
`module::name`, and a stage of an imported module is named `module_stage`,
as its binary is.

## Guarantees

- Within a version, fields are neither removed nor renamed and keep their
  meaning; a new field comes with a new version.
- The document describes the program as compiled, with the build parameters
  it was compiled with: compile again after a change to either.
- Steps are in program order. The entry point's binary runs the same stages,
  in the same order, with the same arguments.
