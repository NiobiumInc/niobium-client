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
scripts/repair_wheel.sh            Linux: self-bundle + $ORIGIN + auditwheel --exclude
scripts/repair_wheel_macos.sh      macOS: self-bundle + @loader_path + delocate --exclude
make/python.mk                     dev-convenience make targets (included by the root Makefile)
examples/python/<scenario>/        client/server/decrypt examples (run against the installed wheel)
```

## Prerequisites (local dev)

- **Python 3.11–3.14** with **pybind11 3.x** and **build** in a venv (pybind11 is a
  build-time dependency, discovered via `find_package`):
  ```bash
  python3.12 -m venv .venv
  .venv/bin/pip install pybind11 build
  ```
- **CMake ≥ 3.16** and a C++17 compiler (also `ninja` for speed).
- The **OpenFHE substrate** built + installed (one-time; the wheel links against it):
  ```bash
  make config-openfhe-release build-openfhe-release   # -> vendor/lib/openfhe/
  ```

Point the make targets at your venv with `PYTHON=`:
```bash
export PY=$PWD/.venv/bin/python
```

## Build

```bash
# Fast, submit-only (just the _archive binding; no OpenFHE) — for iterating on submit():
make build-python-release PYTHON=$PY          # -> build/python/niobium_client/

# Full package assembly (openfhe + session + _archive + nbc + fhetch_sim + libs):
make build-wheel-release PYTHON=$PY           # -> build-wheel/niobium_client/

# A real, distributable wheel via PEP 517 (needs `pip install build`):
make wheel PYTHON=$PY                          # -> dist/niobium_client-*.whl
```

`make wheel` runs `python -m build`, which drives the top-level CMake
(`NIOBIUM_CLIENT_WITH_PYTHON=ON`, transport/auto-facade/examples off) and produces a
platform-tagged wheel. Note: a raw local wheel is **not yet relocatable** — the
OpenFHE/`libnbfhetch` deps are made self-contained by the repair scripts, which run
in CI (see [Release / CI](#release--ci)).

## Test

```bash
# submit() + _archive against an in-process mock server (no OpenFHE):
make test-submit-python-release PYTHON=$PY

# Full primary-only smoke: record -> replay() (bundled fhetch_sim) -> decrypt:
make test-wheel-smoke-release PYTHON=$PY

# Against an actually-installed wheel, in a clean venv (the real portability check):
python3.12 -m venv /tmp/t && /tmp/t/bin/pip install dist/niobium_client-*.whl
cd /tmp && /tmp/t/bin/python /path/to/examples/python/mult/client.py out \
  && /tmp/t/bin/python .../mult/server.py out && /tmp/t/bin/python .../mult/decrypt.py out
```

The example scenarios (`mult`, `simple_ops`, `plaintext_add`, `bootstrap`) under
`examples/python/` each run `client.py` → `server.py` → `decrypt.py` against the
installed wheel and print a PASS/FAIL — see `examples/python/README.md`.

### Testing the manylinux wheel on a stock distro (Docker)

```bash
# Build the manylinux wheel locally (needs Docker):
pip install cibuildwheel
cibuildwheel --only cp312-manylinux_$(uname -m)      # -> ./wheelhouse/*.whl

# Install + run it on a fresh Ubuntu (proves it's self-contained):
docker run --rm -it -v "$PWD/wheelhouse":/w -v "$PWD/examples/python":/ex ubuntu:24.04 bash
#   apt-get update && apt-get install -y python3-venv
#   python3 -m venv /v && . /v/bin/activate
#   pip install /w/niobium_client-*$(uname -m)*.whl
#   cd /tmp && python /ex/mult/client.py o && python /ex/mult/server.py o && python /ex/mult/decrypt.py o
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
