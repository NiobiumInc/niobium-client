# Python module & wheel (`niobium_client`)

How to build, test, and maintain the Python distribution of the Niobium client —
the `niobium_client` wheel. For the C++ build see [`README.md`](README.md).

## What ships in the wheel

`pip install niobium_client` gives four import surfaces, all self-contained (the
wheel bundles OpenFHE, `libnbfhetch`, and the `fhetch_sim` binary — no external
libraries or `LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH` needed at runtime):

| Import | What |
|---|---|
| `from niobium_client import openfhe`  | crypto — vendored openfhe-python, rebuilt against Niobium's instrumented OpenFHE |
| `from niobium_client import session`  | `niobium::compiler()` record/replay; local `replay()` via the bundled `fhetch_sim` |
| `from niobium_client import client`   | `submit()` / `configure()` — pure-Python transport (endpoint supplied by the caller) |
| `from niobium_client import nbc`      | the `.niob` DSL compiler (pure Python; `python -m niobium_client.nbc`) |

The crypto + session extensions are built by **niobium-fhetch** (its `WITH_PYTHON`
option); this repo assembles them + the `_archive` binding + `nbc` + `fhetch_sim`
into the wheel.

## Package naming & metadata

Naming/version live in `pyproject.toml` +
`python/niobium_client/VERSION`.

- **Distribution name:** `niobium_client` — `pip install niobium_client`,
  `import niobium_client`.
- **Namespaced imports — no top-level shadowing.** Everything is under
  `niobium_client.*`, *including* `niobium_client.openfhe`. This allows both this module and stock `import OpenFHE` to coexist in one environment. The
  compiled session module `niobium_session` is surfaced as `niobium_client.session`
- **Version:** plain semver, currently `0.1.0` (pre-1.0 while packaging/API settle). The
  bundled OpenFHE version goes in the wheel METADATA (informational)
- **Wheel tags:** one wheel per **CPython minor × platform** (the cibuildwheel matrix).
  Currently CPython **3.11–3.14** × {manylinux x86_64, manylinux aarch64,
  macOS arm64} = **12 wheels/release**
- **License / deps:** Apache-2.0 (`LICENSE` + classifier); **no runtime dependencies**
  — the crypto/session/archive natives are bundled, `submit()` and `nbc` are pure stdlib.

## Layout

```
pyproject.toml                     scikit-build-core backend + [tool.cibuildwheel] matrix
python/
  niobium_client/
    __init__.py                    RTLD_GLOBAL preload of libnbfhetch; sets NBCC_FHETCH_SIM; __version__
    session.py                     re-export shim over the compiled niobium_session
    client.py                      submit()/configure()
    VERSION                        single source of truth for the wheel version
  archive_binding.cpp              _archive pybind module (TLV pack/unpack; no OpenFHE)
  CMakeLists.txt                   dual-mode: standalone _archive OR full add_subdirectory assembly
  tests/{submit_smoke,wheel_smoke}.py
  examples/<scenario>/             client/server/decrypt ports (see examples/README.md)
scripts/repair_wheel.sh            Linux: self-bundle + $ORIGIN + auditwheel --exclude
scripts/repair_wheel_macos.sh      macOS: self-bundle + @loader_path + delocate --exclude
make/python.mk                     dev-convenience make targets (included by the root Makefile)
```

## Prerequisites (local dev)

All commands below are run from the **`niobium-client` root** and use the venv at
`.venv/` directly (no environment variables).

- **Python 3.11–3.14** with **pybind11 3.x** and **build** in a venv (pybind11 is a
  build-time dependency, discovered via `find_package`):
  ```bash
  python3 -m venv .venv
  .venv/bin/pip install pybind11 build
  ```
- **CMake ≥ 3.16** and a C++17 compiler (also `ninja` for speed).
- The **OpenFHE substrate** built + installed (one-time; the wheel links against it):
  ```bash
  make config-openfhe-release build-openfhe-release   # -> vendor/lib/openfhe/
  ```

## Build

```bash
# Fast, submit-only (just the _archive binding; no OpenFHE) — for iterating on submit():
make build-python-archive PYTHON=.venv/bin/python   # -> build/python/niobium_client/

# Full package assembly (openfhe + session + _archive + nbc + fhetch_sim + libs):
make build-wheel-release PYTHON=.venv/bin/python     # -> build-wheel/niobium_client/

# A real, distributable wheel via PEP 517 (needs `pip install build`):
make wheel PYTHON=.venv/bin/python                   # -> dist/niobium_client-*.whl
```

`make wheel` runs `python -m build`, which drives the top-level CMake
(`NIOBIUM_CLIENT_WITH_PYTHON=ON`, transport/auto-facade/examples off) and produces a
platform-tagged wheel. Note: a raw local wheel is **not yet relocatable** — the
OpenFHE/`libnbfhetch` deps are made self-contained by the repair scripts, which run
in CI (see [Release / CI](#release--ci)).

## Test

```bash
# submit() + _archive against an in-process mock server (no OpenFHE):
make test-submit-python-release PYTHON=.venv/bin/python

# Primary-only smoke (record -> replay() via bundled fhetch_sim -> decrypt).
# This is also the cibuildwheel test-command (runs against the installed wheel in CI):
make test-wheel-smoke-release PYTHON=.venv/bin/python
```

**Per-scenario tests** — the Python analogs of the C++ `test-<scenario>-release`
targets, run against the assembled `build-wheel/` package:

```bash
make test-mult-python-release           PYTHON=.venv/bin/python
make test-simple-ops-python-release     PYTHON=.venv/bin/python   # 13-op sweep
make test-op-python-release OP=ADD A=5 B=6 PYTHON=.venv/bin/python   # one simple_ops op
make test-plaintext-add-python-release  PYTHON=.venv/bin/python
make test-bootstrap-python-release      PYTHON=.venv/bin/python
make test-ring-dim-check-python-release PYTHON=.venv/bin/python   # negative: guard rejects ring dim 2048

# Delegate to the niobium-fhetch submodule's own Python roundtrip sweep
# (simple_ops + plaintext-add + bootstrap, each primary + secondary via fhetch_driver):
make test-fhetch-python-release         PYTHON=.venv/bin/python
```

Aggregates, mirroring the C++ `test-client-release` / `test-release`:

```bash
make test-client-python-release PYTHON=.venv/bin/python   # scenario tests + ring-dim guard
make test-python-release        PYTHON=.venv/bin/python   # client-level + fhetch (everything)
```

The scenario targets run the `python/examples/<scenario>` ports (`client.py` →
`server.py` → `decrypt.py`, each printing PASS/FAIL) against the assembled
`build-wheel/` tree — see `python/examples/README.md`. What's *not* ported:
`test-auto-ciphers-release` (auto-facade is off in the wheel); the `--target` C++ tests
are compiler-only (the client's `submit()` path is covered by
`test-submit-python-release`).

To exercise an **actually-installed** wheel in a clean venv (the real portability
check — the same thing CI's `test-wheel-smoke-release` does). From the root, into a
throwaway `.venv-demo/`:

```bash
python3 -m venv .venv-demo && .venv-demo/bin/pip install dist/niobium_client-*.whl
.venv-demo/bin/python python/examples/mult/client.py  mult_keys 7 13
.venv-demo/bin/python python/examples/mult/server.py  mult_keys
.venv-demo/bin/python python/examples/mult/decrypt.py mult_keys      # -> PASS 91.0
```

### Testing the manylinux wheel on a stock distro (Docker)

Run from the `niobium-client` root (the `$(pwd)` mounts resolve to this checkout):

```bash
# Build the manylinux wheel locally (needs Docker):
.venv/bin/pip install cibuildwheel
.venv/bin/cibuildwheel --only cp312-manylinux_$(uname -m)      # -> ./wheelhouse/*.whl

# Install + run it on a fresh Ubuntu (proves it's self-contained):
docker run --rm -it -v "$(pwd)/wheelhouse":/w -v "$(pwd)/python/examples":/ex ubuntu:24.04 bash
#   apt-get update && apt-get install -y python3-venv
#   python3 -m venv /work/v && . /work/v/bin/activate
#   pip install /w/niobium_client-*$(uname -m)*.whl
#   mkdir -p /work/run && cd /work/run
#   python /ex/mult/client.py mult_keys 7 13 && python /ex/mult/server.py mult_keys && python /ex/mult/decrypt.py mult_keys
```

## Release / CI

`.github/workflows/wheels.yml` builds the full matrix with cibuildwheel
(**manual / `workflow_dispatch`** for now):

- **Linux** manylinux_2_28, one native runner per arch (`ubuntu-latest` x86_64 +
  `ubuntu-24.04-arm` aarch64).
- **macOS** arm64 (`macos-14`).

Each job is `checkout` (recursive submodules) → `pypa/cibuildwheel` → upload. All
build/repair logic lives in `pyproject.toml [tool.cibuildwheel]` +
`scripts/repair_wheel*.sh`, so CI and local builds share one definition. The matrix
covers CPython **3.11–3.14**; musl/Alpine and Windows are not built.

Publishing to PyPI is a separate, manual, gated step (upload `wheelhouse/*.whl` after
review) — the workflow only builds + tests.

## How the wheel is made self-contained

The extensions depend on `libnbfhetch` + the OpenFHE dylibs, and the wheel also ships
`fhetch_sim` (an executable). auditwheel/delocate can't resolve an in-wheel companion
lib or repair a bundled executable, so the repair scripts instead **bundle every
native dep next to the extensions**, set `$ORIGIN` (Linux) / `@loader_path` (macOS)
rpaths, and run auditwheel/delocate only to *retag* (`--exclude` the bundled libs).
At import, `__init__.py` RTLD_GLOBAL-preloads `libnbfhetch` (so the crypto module's
inlined probe hooks resolve) and points the loader at the bundled libs for the
`fhetch_sim` subprocess.

## Updating / maintaining

- **Bump the wheel version:** edit `python/niobium_client/VERSION`. It is the single
  source of truth — both `niobium_client.__version__` and the wheel version read it.
- **Update the crypto binding (openfhe-python):** bump the nested submodule at
  `vendor/niobium-fhetch/vendor/openfhe-python`, then bump the `vendor/niobium-fhetch`
  pin here (`chore: bump niobium-fhetch to <sha>`). The build patches openfhe-python's
  over-broad `find_package(Python … Development)` → `Development.Module` from a build-tree
  copy (in niobium-fhetch's CMake), so re-vendoring needs no manual patch; CMake warns if
  the line to patch has changed upstream.
- **Change the pybind11 version:** it's an unpinned `find_package` build-time item —
  install the version you want in the build venv; CI resolves it from
  `[build-system] requires = ["pybind11>=3,<4"]`. (Aligned to upstream openfhe-python,
  which builds on pybind11 3.x. abi3 is not available with pybind11 at any version.)
- **Add a Python version to the matrix:** add it to `build` in
  `[tool.cibuildwheel]` and confirm the CI runners have it.
- **Add a new native dependency:** add it to the bundle + `--exclude` lists in both
  `scripts/repair_wheel.sh` and `scripts/repair_wheel_macos.sh`, and to the
  `__init__.py` preload glob if it must be RTLD_GLOBAL-loaded.
- **Rebuild after an OpenFHE bump:** the substrate must be rebuilt
  (`make config-openfhe-release build-openfhe-release`) before the wheel build.
