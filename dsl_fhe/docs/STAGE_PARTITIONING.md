# Partitioning a computation into stages

A `@stage` function is the unit the DSL builds, runs and records: it becomes
one binary, it reads and writes wire files, and, on a server marked
`@hardware`, its body is recorded once as a FHETCH program and replayed from
then on. How the server's work is cut into stages decides how long a recording
takes, how many recordings there are, how much state moves between them and
how much of it a runtime can keep on the device. This page says what each
choice costs and how to express it.

It applies to both backends. With the default (OpenFHE), an `@hardware` stage
is recorded through the client's instrumentation; with `--backend fideslib`,
each server stage runs on the engine `FIDESLIB_BACKEND` names, and the `haze`
engine records the same stage boundaries.

## What a stage is

- **A binary.** `nbc compile` writes one `.cpp` per stage, with a `main()` that
  parses the stage's arguments, loads the keys and runs the body.
- **A boundary of files.** Everything that crosses from one stage to the next
  is a wire file: `save(...)` in one stage, `load(...)` in the next. The
  `reads(...)` / `writes(...)` clause declares which wire types a stage takes
  and makes; the files are OpenFHE BINARY whatever the backend.
- **A recording** (server stages with `@hardware(cache_key: [...])`). The first
  run with a given cache key executes the body and records it; a later run
  with the same key executes no FHE operation: it replays the recording on the
  new inputs and writes the same files the body would have written.

## The cache key: one recording per circuit, not per input

`cache_key` names the stage's command-line arguments, in order: with

```nb
@server @stage("update")
@hardware(cache_key: ["workload_size", "block"])
fn update(inst: Instance, block: u32 = 0, part: u32 = 0) -> reads(CryptoParams, State) { ... }
```

the first argument (the instance) and `block` select the recording; `part`
does not. A compile-time parameter (`nbc --set NAME=VALUE`) is always part of
the key.

Put in the key exactly the arguments that change the circuit:

- **In the key**: anything that changes which operations run, their shapes,
  or a value computed or encoded inside the stage (a constant, a plaintext the
  stage encodes from data, a polynomial's coefficients). A recording keeps
  those values; replaying it for another value would be wrong.
- **Out of the key**: an argument that only chooses which wire files the stage
  loads and where it saves. The loaded ciphertexts are the recording's inputs,
  bound again on every run, and the save paths are evaluated again from the
  run's arguments, so one recording serves every value of that argument.

The fewer arguments in the key, the fewer recordings: a computation that
repeats one shape over many data sets wants a stage whose key does not name
the data set.

## Where to cut

Each cut costs a write and a read of every ciphertext that crosses it (a host
round trip on a device runtime, unless the runtime keeps it where it is), and
each stage costs a recording per cache key. Within those costs:

- **Cut at narrow places.** Prefer a boundary that few ciphertexts cross: after
  a reduction, before a fan-out, where the state is one ciphertext.
- **Give a repeated structure one stage.** A computation that applies the same
  sub-circuit several times (rounds, blocks, parts) is written as one stage
  with an index argument. Whether the index belongs in the cache key is the
  rule above.
- **Cut a stage that records too slowly or too large.** A recording is made by
  executing the stage, so its time and size grow with the number of
  operations. Two halves of a large block, each a stage, record in parallel and
  can be re-recorded alone.
- **Keep a stage within one device.** A stage runs as one program: its live
  ciphertexts, the keys it uses and its constants must fit the device it runs
  on at once. A stage that does not fit is cut further, at a narrow place.
- **Keep the level budget readable.** A ciphertext crosses a boundary at the
  level it has; the next stage continues from there. Refreshing (`bootstrap`)
  inside the stage that runs out keeps each stage's level use visible in one
  place.

## State between stages

State lives in wire files under directories the program computes from the
stage's arguments (`encdir(inst)`, `statedir(inst, i)`). Three patterns cover
most circuits:

- **Updated in place.** A stage loads `state.bin`, computes and saves
  `state.bin` again; the next stage reads the new one.
- **Indexed.** A stage writes under a directory computed from its arguments
  (`blockdir(inst, block) / "out.bin"`); a later stage reads the one it
  needs.
- **Prepared slots.** A stage writes several files that later stages fill:
  one `save()` per slot, in a `for` loop over a range.

The same file name may be saved by one stage into several directories, and a
`save()` may sit in a `for i in a..b` loop with `i` in its path. In an
`@hardware` stage each save is a separate output of the recording: a file name
saved once keeps its stem as the output's name (`state`; a name computed in the
path, `"part_" + str(i) + ".bin"`, is named by its literal pieces, `part`), a
repeated one takes
`<stem>__2`, `<stem>__3`, ..., and a save in loops whose path uses a loop
variable one output per iteration (`<name>__i0`, `<name>__i1`, ...); a save in
a loop whose path ignores the loop writes the same file each time and keeps
one output. On a replayed run `main()` repeats those
loops to write every file again, so:

- a save path may use the stage's arguments, top-level `const`s, helper
  functions and the variables of the range loops around it, but not a value
  the body computes;
- the bounds of those loops follow the same rule;
- a `save()` inside a loop that is not over a range (`for x in values`,
  `enumerate(...)`) is rejected: return such values through the stage's
  result wire instead.

## Driving the stages

The stages run in the order a harness or a runtime calls them. A server stage
whose body only calls other stages, in loops over ranges, is a schedule:
`nbc compile --schedule` writes that order to `nb_schedule.json`, so a runtime
can run the stages itself, keeping state on the device between them
([STAGE_SCHEDULE.md](STAGE_SCHEDULE.md)). A schedule's steps load and save in
their own bodies, outside loops and branches: a stage that prepares slots in a
loop runs before the schedule, from the harness, as in the example below.

## Example

A computation that refines a state over `BLOCKS` blocks, each with its own
scale, and keeps `SLOTS` running results for a later pass:

```nb
const BLOCKS: u32 = 4
const SLOTS: u32 = 3

fn blockdir(inst: Instance, b: u32) -> path { encdir(inst) / ("block_" + str(b)) }
fn slotdir(inst: Instance, s: u32) -> path { encdir(inst) / ("slot_" + str(s)) }

// The slots, prepared once before the blocks run: the first holds the
// starting state, the others start at zero. One save() per slot, the later
// ones in a loop over a range.
@server @stage("open_slots")
@hardware(cache_key: ["workload_size"])
fn open_slots(inst: Instance) -> reads(CryptoParams, State) {
    let params = load(CryptoParams, from: keydir(inst))
    let x = load(State, from: encdir(inst) / "state.bin").ciphertext
    save(State { ciphertext: x }, to: slotdir(inst, 0) / "acc.bin")
    let zero = x * 0.0
    for s in 1..SLOTS {
        save(State { ciphertext: zero }, to: slotdir(inst, s) / "acc.bin")
    }
}

// One stage for every block. `block` is in the cache key because the scale
// is computed from it inside the stage: one recording per block.
@server @stage("refine")
@hardware(cache_key: ["workload_size", "block"])
fn refine(inst: Instance, block: u32 = 0) -> reads(CryptoParams, State) {
    let params = load(CryptoParams, from: keydir(inst))
    let x = load(State, from: encdir(inst) / "state.bin").ciphertext
    let y = x * x + x * block_scale(block)
    save(State { ciphertext: y }, to: encdir(inst) / "state.bin")
    save(State { ciphertext: y }, to: blockdir(inst, block) / "state.bin")
}

// The order of the blocks, as data: `nbc compile --schedule` describes it.
@server @stage("run")
fn run(inst: Instance) -> reads(CryptoParams) {
    for b in 0..BLOCKS {
        refine(inst, b)
    }
}
```

`open_slots`'s recording has outputs `acc` (slot 0) and `acc__2__s1`,
`acc__2__s2` (the loop). `refine`'s has `state` (the running state) and
`state__2` (the block's copy). A replayed run of `refine(inst, 2)` writes
`state.bin` and `block_2/state.bin` from the recording, without executing a
ciphertext operation. The harness runs `open_slots`, then `run`.
