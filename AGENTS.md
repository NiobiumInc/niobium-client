# AGENTS.md

Guidance for Claude Code (and other agents) working in this repository.

## Project Overview

**Niobium Client** is the open-source client stack for the Niobium Mistic FHE
accelerator, with four entry points converging on one FHETCH Polynomial IR
trace: the **nb DSL + design skill** (AI-agent coding, `dsl_fhe/` +
`.claude/skills/fhe-application-design`), **instrumented OpenFHE**
(application developers, `examples/`), **direct FHETCH IR emission** (compiler
writers, via `libnbfhetch` + `src/fhetch_transport/`), and **HAZE** (FHE
library integrators, CUDA-shaped C API, `vendor/niobium-haze`). It wires the
Niobium-instrumented OpenFHE branch to the FHETCH recording/replay library
(`libnbfhetch`, vendored from
[`niobium-fhetch`](https://github.com/NiobiumInc/niobium-fhetch)) and ships
end-to-end OpenFHE examples.

A customer links their OpenFHE C++ app against the instrumented OpenFHE +
`libnbfhetch`, brackets the computation with `niobium::compiler()` calls, runs
it to capture an unoptimized `.fhetch` instruction trace, and either replays the
trace locally through the bundled simulator (`fhetch_sim`) to reconstruct the
result ciphertext, or submits it to the Niobium compilation service. This repo
does **not** contain the proprietary compiler.

See [`README.md`](README.md) for the full narrative and diagrams.

## Development Commands

```bash
git submodule update --init --recursive   # or: make sync-submodules

make build-release        # build OpenFHE + libnbfhetch + examples (Release)
make build                # same, Debug

make test-release         # run the example/replay test sweep (Release)
make test-mult-release    # CKKS EvalMult: record → replay → decrypt
make test-simple-ops-release
make test-bootstrap-release
make test-op-release OP=MORPH A=5 B=6   # one simple_ops operation

make clean / make clean-all
```

Build options (CMake): `NIOBIUM_CLIENT_WITH_AUTO_FACADE` (default ON),
`NIOBIUM_CLIENT_WITH_FHETCH_TRANSPORT`, `NIOBIUM_CLIENT_WITH_EXAMPLES`.

## Architecture Overview

- **Instrumented OpenFHE** (`vendor/lib/openfhe`, built from
  `vendor/niobium-fhetch/vendor/openfhe`) — every polynomial op fires a C probe.
- **`libnbfhetch`** (`vendor/niobium-fhetch`) — the FHETCH IR recorder, the
  `niobium::compiler()` session/replay API (`niobium/compiler.h`), and the
  `fhetch_sim` local simulator.
- **Auto-facade** (`src/auto_facade/`, `libniobium_client_autofacade`) —
  transparent/cooperative record-replay via instrumented-OpenFHE deserialize
  hooks, so input/key/context tagging happens without explicit API calls. See
  [`docs/AUTO_FACADE.md`](docs/AUTO_FACADE.md).
- **FHETCH transport** (`src/fhetch_transport/`) — client/server + archive for
  shipping traces to a compilation target.
- **Examples** (`examples/`) — hand-written `client`/`server`/`decrypt` splits.
- **DSL** (`dsl_fhe/`) — a higher-level language + cross-compiler that generates
  client-linked OpenFHE C++ (see below).

## DSL for FHE (`dsl_fhe/`)

`dsl_fhe/` is an optional domain-specific language and cross-compiler (`nbc`):
`.niob` files compile to OpenFHE C++ that links this client, with trust
boundaries (`@client`/`@server`), serialization, and Niobium record/replay
instrumentation generated automatically. It targets the open-source client
(`libnbfhetch`) via cooperative auto-tagging — no proprietary-compiler
dependency. Its examples build self-contained (`make -C dsl_fhe examples`);
ml-inference and fhe-NetworkMonitor build against stub models (real
model/weights are an opt-in via `NIOBIUM_COMPILER_ROOT`).

| File | Purpose |
|---|---|
| [`dsl_fhe/README.md`](dsl_fhe/README.md) | User-facing overview, build instructions, example walkthroughs |
| [`dsl_fhe/CLAUDE.md`](dsl_fhe/CLAUDE.md) | Design rationale, codegen internals, client-API integration |
| [`dsl_fhe/NB_LANGUAGE.md`](dsl_fhe/NB_LANGUAGE.md) | Language reference — types, syntax, built-in functions |
| [`dsl_fhe/GRAMMAR.md`](dsl_fhe/GRAMMAR.md) | Formal EBNF grammar |
| [`dsl_fhe/HOWTO.md`](dsl_fhe/HOWTO.md) | Step-by-step guide for adding a new example |

## Directory Structure

```
niobium-client/
  include/niobium/          # public client headers (Utils/ScopedPause.h, ...)
  src/
    auto_facade/            # libniobium_client_autofacade (transparent record/replay)
    fhetch_transport/       # trace transport client/server + archive
  examples/                 # hand-written OpenFHE examples (bootstrap, mult, simple_ops, ...)
  dsl_fhe/                  # DSL + cross-compiler (nbc); see dsl_fhe/README.md
  .claude/skills/
    fhe-application-design/ # submodule: 8-stage FHE design skill (AI agents)
  vendor/
    niobium-fhetch/         # submodule: libnbfhetch + fhetch_sim + API headers
      vendor/openfhe/       # nested submodule: Niobium-instrumented OpenFHE
    niobium-haze/           # submodule: CUDA-shaped C API (library integrations)
    lib/openfhe/            # installed OpenFHE (built by the Makefile)
  docs/AUTO_FACADE.md       # transparent record/replay design
  CMakeLists.txt  Makefile  README.md  CLAUDE.md  LICENSE (Apache 2.0)
```

## Notes

- The public record/replay API is `niobium::compiler()` from
  `niobium/compiler.h`; `replay()` takes no target argument in the client (local
  FHETCH simulator). Compiler-only features (`Target`, `cached_key`,
  `global_key_cache`) do not exist here.
- Everything is Apache-2.0 and self-contained: no build or test target depends
  on the proprietary `niobium-compiler` repo.
