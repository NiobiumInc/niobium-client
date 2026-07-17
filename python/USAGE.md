# Niobium client wheel — end-to-end build & demo walkthrough

A linear, copy-pasteable path from a fresh checkout to a **pip-installed
`niobium_sdk` wheel** running the example FHE programs and printing `PASS`. Use
this to verify a build or to demo that the wheel is self-contained (no external
OpenFHE / library path needed at runtime).

**Run every command from the `niobium-client` root.** All paths are relative to it;
the virtualenvs (`.venv`, `.venv-demo`) and example output dirs are created there and
are gitignored.

> Scope: the open-source client wheel — crypto (`openfhe`), record/replay
> (`session`, local `fhetch_sim`), transport (`client.submit()`), Fog cloud submit
> (`fog`), and the `nbc` DSL.
> For maintainer details (updating pins, the relocatability mechanism, CI) see
> [`README.md`](README.md).

---

## 0. Prerequisites

- **Python 3.11–3.14** (any of them — `python3` below is whatever your default is),
  **CMake ≥ 3.16**, a C++17 compiler (and `ninja` for speed).
- Submodules checked out:
  ```bash
  git submodule update --init --recursive     # or: make sync-submodules
  ```
- A build virtualenv with **pybind11** + **build**:
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install pybind11 build
  ```

---

## 1. Build the OpenFHE substrate (one-time)

The wheel links a Niobium-instrumented OpenFHE. Build + install it once:

```bash
make config-openfhe-release build-openfhe-release      # -> vendor/lib/openfhe/
```

(Slow — it compiles OpenFHE. Subsequent wheel builds reuse this install.)

> **Shortcut:** to skip the OpenFHE compile entirely, build the wheel against a prebuilt
> `niobium-runtime` distribution instead of building from source — see the runtime build note
> in [`README.md`](README.md#building-against-a-prebuilt-runtime-optional). This walkthrough
> takes the from-source path.

---

## 2. Build the wheel

Two options depending on what you want:

### 2a. A local wheel (fast, this machine only)

```bash
make wheel PYTHON=.venv/bin/python                     # -> dist/niobium_sdk-*.whl
```

`make wheel` runs `python -m build`, driving the top-level CMake with
`NIOBIUM_CLIENT_WITH_PYTHON=ON`. Good enough to install + demo **on this machine**.

### 2b. A relocatable, distributable wheel

This is the wheel you would ship: unlike 2a it **carries its own native deps**, so it
runs with nothing from this source tree on the path (the §3 fresh-venv install is the
proof). Two ways to produce one — the **manual repair** path (fast, this machine's
platform, proven locally) and the **cibuildwheel** path (the full CI matrix). Both end
in `./wheelhouse/*.whl` and both self-bundle via the same scripts; they differ only in
how the wheel is first built and which min-OS / platform tag it carries.

What "relocate" does, on either OS: take a built wheel (whose extensions still expect
the OpenFHE dylibs at `vendor/lib/openfhe`), copy `libOPENFHE*` + `libnbfhetch` in next
to the extensions and `fhetch_sim`, fix the rpaths so `@rpath/libX` / `$ORIGIN/libX`
resolves to the co-located copy, then let delocate/auditwheel verify + retag.

#### macOS — you are here (native, no Docker)

**Recommended for local testing** — build a local wheel, then relocate it with the
macOS repair script (this is the locally-proven path):

```bash
make wheel PYTHON=.venv/bin/python                              # -> dist/niobium_sdk-*.whl (2a)
mkdir -p wheelhouse
bash scripts/repair_wheel_macos.sh dist/niobium_sdk-*.whl wheelhouse   # -> wheelhouse/*.whl
```

`repair_wheel_macos.sh` unpacks the wheel, copies the `libOPENFHE*.1.dylib` set from
`vendor/lib/openfhe/lib` in beside the extensions, adds `@loader_path` to each
artifact's rpath (extensions, `libnbfhetch`, and the `fhetch_sim` binary), repacks,
and runs `delocate` in verify-and-tag mode (the bundled libs are `--exclude`d because
they're already placed). Override the substrate location with
`OPENFHE_INSTALL_DIR=...` if it isn't the default `vendor/lib/openfhe`.

The resulting `wheelhouse/*.whl` is relocatable **on this machine/architecture** —
exactly what the §3 clean-venv install then §4 examples verify. Its min-OS tag reflects
the substrate you built in step 1 (a locally-built substrate is fine for local testing;
for a portable `macosx_11_0` tag you want the cibuildwheel path below, which pins
`MACOSX_DEPLOYMENT_TARGET=11.0`).

**Full-matrix (cibuildwheel):**

```bash
.venv/bin/pip install cibuildwheel
.venv/bin/cibuildwheel --only cp312-macosx_$(uname -m)         # -> ./wheelhouse/*.whl
```

- Runs **natively** — no container. `$(uname -m)` = `arm64` on Apple Silicon,
  `x86_64` on Intel. `cp312` picks CPython 3.12; swap cp311/cp313/cp314 for another
  target (cibuildwheel provisions its own interpreter for the tag, independent of
  `.venv`'s Python).
- cibuildwheel runs its own configured steps from `pyproject.toml`
  (`[tool.cibuildwheel.macos]`) — you don't invoke them. Its `before-all` hook
  **wipes and rebuilds the substrate from scratch** (`rm -rf vendor/lib …` then
  `make …-openfhe-release`) with `MACOSX_DEPLOYMENT_TARGET=11.0`, so step 1 is not a
  prerequisite here — but note it discards the step-1 install and re-pays the slow
  OpenFHE compile.
- Repair uses the same `scripts/repair_wheel_macos.sh`; then cibuildwheel runs the
  `test-command` smoke test (`python/tests/wheel_smoke.py`) against the freshly
  installed wheel automatically.
- Caveat: this flow downloads an official python.org framework CPython for the target
  and is primarily **CI-validated**; if it can't provision one locally, use the manual
  repair path above.

#### Linux (needs Docker)

```bash
.venv/bin/pip install cibuildwheel
.venv/bin/cibuildwheel --only cp312-manylinux_$(uname -m)      # -> ./wheelhouse/*.whl
```

- Requires **Docker**; the build runs inside the `manylinux_2_28` container
  (x86_64 or aarch64). cibuildwheel's `before-all` hook (`[tool.cibuildwheel.linux]`
  in `pyproject.toml`) builds the substrate **in-container** from source, so the
  host's `vendor/lib` is not used (it's `rm -rf`'d first).
- Repair uses `scripts/repair_wheel.sh` — same self-bundle, but with `$ORIGIN` rpaths
  and `auditwheel` retagging manylinux instead of delocate.
- You can cross-build the Linux wheel **from your Mac** with Docker Desktop running
  (the non-native arch runs emulated, slowly). But since you're testing on macOS, the
  macOS wheel above is the one you install in §3.

### How the wheel is wired (`make wheel` flow)

The whole build runs through `pyproject.toml` + CMake — no bespoke build script.
Source inputs feeding each step are shown after `←`:

```
make wheel                                            (make/python.mk)
   │
   ▼
python -m build --wheel                               PEP 517 frontend
   │   reads pyproject.toml → build-backend = scikit_build_core.build
   ▼
scikit-build-core                                     cmake.args:
   │                                                    -DNIOBIUM_CLIENT_WITH_PYTHON=ON
   │                                                    (AUTO_FACADE / TRANSPORT / EXAMPLES = OFF)
   ▼
CMakeLists.txt  (top level)
   │   NIOBIUM_CLIENT_WITH_PYTHON=ON  ⇒  WITH_PYTHON=ON (forced)
   ├───────────────────────────────────────────────┬───────────────────────────────┐
   ▼                                                 ▼                                 │
add_subdirectory(vendor/niobium-fhetch)      add_subdirectory(python)  (client)       │
   │  vendor/niobium-fhetch/CMakeLists.txt       │  python/CMakeLists.txt              │
   │   • niobium_fhetch → libnbfhetch.so.1       ▼                                     │
   │   • fhetch_sim                           pybind11_add_module(_archive)            │
   │   • if(WITH_PYTHON) add_subdirectory(python) ← archive_binding.cpp               │
   │        python/CMakeLists.txt                 + src/fhetch_transport/archive.cpp  │
   │          copy vendor/openfhe-python → build/openfhe-python-src                   │
   │            (patch find_package Development → Development.Module)                 │
   │          ExternalProject openfhe_python                                          │
   │            ← build/openfhe-python-src  (the patched copy, built)                 │
   │            ← vendor/lib/openfhe        (installed OpenFHE, linked)               │
   │            ⇒ openfhe*.so                                                         │
   │          pybind11_add_module(niobium_session)                                   │
   │            ← python/niobium_session.cpp                                          │
   │            ← links niobium_fhetch                                                │
   │          ⇒ build/python/{openfhe,niobium_session}*.so                           │
   └───────────────────────────────┬─────────────────────────────────────────────────┘
                                    ▼
     niobium_sdk_pkg  — assemble → build/niobium_sdk/
        ├─ niobium_sdk/*.py     ← python/niobium_sdk/ (__init__, session, client, fog, VERSION)
        │                          + _fog.py ← scripts/fog (verbatim build-time copy)
        ├─ openfhe*.so             ← build/python/
        ├─ niobium_session*.so     ← build/python/
        ├─ _archive*.so            ← (built above)
        ├─ libnbfhetch.so.1        ← niobium_fhetch SONAME (RTLD_GLOBAL-preloaded by __init__)
        ├─ fhetch_sim             ← local replay()
        └─ nbc/                    ← vendored dsl_fhe/xcomp
                                    │   install(COMPONENT python_wheel)
                                    ▼
                        dist/niobium_sdk-*.whl
                                    │
                                    ▼   (CI only — cibuildwheel, §2b)
     scripts/repair_wheel*.sh: graft OpenFHE dylibs next to the extensions +
     $ORIGIN / @loader_path rpaths + auditwheel/delocate  ⇒  relocatable wheel
```

Linkage notes:
- **`openfhe.so` + `niobium_session.so` + `libnbfhetch` are a coupled set** — the
  crypto module's inlined probes resolve globals in `libnbfhetch`, so `__init__.py`
  RTLD_GLOBAL-preloads it before importing `openfhe`.
- **`make wheel` stops at `dist/*.whl`** (not relocatable — it relies on the
  `vendor/lib/openfhe` dylibs at runtime). The repair step that makes the wheel
  self-contained runs automatically under cibuildwheel (§2b / CI), or you can run
  `scripts/repair_wheel*.sh` by hand on a `make wheel` output (§2b, macOS path).
- **OpenFHE is built once** (step 1) and merely *linked* here; the wheel build never
  recompiles it.
- **openfhe-python is never edited in place** — at CMake configure time
  (`vendor/niobium-fhetch/python/CMakeLists.txt`) the submodule is copied to
  `build/openfhe-python-src` and the *copy's* `find_package(Python … Development)` is
  rewritten to `Development.Module`, so manylinux's module-only Python can configure it
  while the vendored submodule stays pristine.

---

## 3. Install into a clean virtualenv

Installing into a **fresh** virtualenv is the real proof the wheel is self-contained —
nothing from this source tree is on the path.

```bash
python3 -m venv .venv-demo
.venv-demo/bin/pip install dist/niobium_sdk-*.whl        # or wheelhouse/*.whl
```

> **The demo venv's Python minor must match the wheel's `cpXXX` tag** — these are
> per-version wheels (no abi3), so a `cp312-…` wheel installs only into a 3.12 venv
> (else pip says *"not a supported wheel on this platform"*). The local `make wheel`
> path (§2a) builds against your `.venv`, so `python3` here lines up automatically. But
> if you install a wheel built for a specific version — e.g. the cibuildwheel
> `cp312-macosx_11_0` wheel — build the demo venv with **that** interpreter:
> ```bash
> /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv-demo
> ```

Quick import check (no OpenFHE / library-path setup of any kind should be needed):

```bash
.venv-demo/bin/python -c "import niobium_sdk; from niobium_sdk import openfhe, session, client, fog, nbc; print('import OK', niobium_sdk.__version__)"
```

---

## 4. Run the examples (the demo)

The examples live in [`examples/`](examples/) as `client` → `server` →
`decrypt` splits. Each writes its artifacts to a directory you pass and the `decrypt`
step prints `PASS`/`FAIL`. The directory names below are gitignored.

### mult — CKKS `a * b`

```bash
.venv-demo/bin/python python/examples/mult/client.py  mult_keys 7 13
.venv-demo/bin/python python/examples/mult/server.py  mult_keys
.venv-demo/bin/python python/examples/mult/decrypt.py mult_keys
```
Expected:
```
[PASS] 7.0 * 13.0 = 91.0000 (expected 91.0000)
```

### simple_ops — pick an operation

Ops: `ADD SUB NEG ADDI SUBI MULI ADD_ADD ADD_SUB MUL MUL_ADD ADD_MUL MUL_MUL MORPH`.

```bash
.venv-demo/bin/python python/examples/simple_ops/client.py  simple_ops_keys 5 6
.venv-demo/bin/python python/examples/simple_ops/server.py  simple_ops_keys MUL
.venv-demo/bin/python python/examples/simple_ops/decrypt.py simple_ops_keys MUL
```
Expected:
```
[PASS] MUL: 30.0000 ~= 30.0000
```

### plaintext_add — `EvalAdd(ciphertext, server-side plaintext)`

```bash
.venv-demo/bin/python python/examples/plaintext_add/client.py  plaintext_add_keys
.venv-demo/bin/python python/examples/plaintext_add/server.py  plaintext_add_keys
.venv-demo/bin/python python/examples/plaintext_add/decrypt.py plaintext_add_keys
```
Expected:
```
[PASS] plaintext_add: [2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0] (tol 0.01)
```

### bootstrap — CKKS `EvalBootstrap`

```bash
.venv-demo/bin/python python/examples/bootstrap/client.py  bootstrap_keys
.venv-demo/bin/python python/examples/bootstrap/server.py  bootstrap_keys
.venv-demo/bin/python python/examples/bootstrap/decrypt.py bootstrap_keys
```
Expected:
```
[PASS] bootstrap: [0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0] (tol 0.01)
```

Four `PASS` lines = the installed wheel records, replays through the bundled
`fhetch_sim`, and decrypts correctly, with no external OpenFHE.

---

## 5. Cross-distro demo (Linux, optional)

Prove the manylinux wheel runs on a stock distro it wasn't built on. Run from the
`niobium-client` root; the mounts use `$(pwd)` so they resolve to this checkout:

```bash
docker run --rm -it \
  -v "$(pwd)/wheelhouse":/w \
  -v "$(pwd)/python/examples":/ex \
  ubuntu:24.04 bash
# inside the container:
apt-get update && apt-get install -y python3-venv
python3 -m venv /work/v && . /work/v/bin/activate
pip install /w/niobium_sdk-*$(uname -m)*.whl
mkdir -p /work/run && cd /work/run
python /ex/mult/client.py mult_keys 7 13 && python /ex/mult/server.py mult_keys && python /ex/mult/decrypt.py mult_keys
#   -> [PASS] 7.0 * 13.0 = 91.0000 (expected 91.0000)
```

---

## 6. Shortcut: the make test targets

If you just want the automated equivalent (against the assembled `build-wheel/` tree,
not a pip install), skip the manual steps:

```bash
make test-client-python-release PYTHON=.venv/bin/python   # scenario tests + ring-dim guard
make test-python-release        PYTHON=.venv/bin/python   # everything: client-level + fhetch roundtrips
```

The manual walkthrough above is the stronger check because it exercises the actual
**installed, relocated** wheel end-to-end.

---

## 7. Cleanup

Remove everything this walkthrough created (run from the root):

```bash
make clean-python     # wheel outputs (dist/ wheelhouse/ build-wheel/) + every venv + bytecode
make clean            # C++ / OpenFHE build trees + example run dirs (mult_keys, …)
```

`make clean-python` also deletes the virtualenvs (`.venv`, `.venv-demo`, or any
custom-named env — it finds them by their `pyvenv.cfg`). Both leave the **OpenFHE
substrate install** (`vendor/lib/`) in place, so a re-run skips the slow substrate
recompile. To go fully fresh — forcing step 1 to rebuild the substrate — also remove it:

```bash
rm -rf vendor/lib     # installed OpenFHE + libnbfhetch (step 1 rebuilds these)
```

Everything above is gitignored, so `git status` is unchanged by the cleanup.

