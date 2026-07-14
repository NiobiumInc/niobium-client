# Python distribution channel — thin dev-convenience dispatch.
#
# The real build system is pyproject.toml + scikit-build-core + cibuildwheel; these
# targets just wrap the dev loop and `python -m build`. Included by the root Makefile
# (shares its variable namespace: OPENFHE_INSTALL_DIR, NUM_CPUS, CURDIR come from there).

.PHONY: config-python-archive build-python-archive test-submit-python-release \
        config-wheel-release build-wheel-release test-wheel-smoke-release wheel \
        test-mult-python-release test-simple-ops-python-release test-op-python-release \
        test-plaintext-add-python-release test-bootstrap-python-release \
        test-ring-dim-check-python-release test-fhetch-python-release \
        test-submit-python-release test-fog-python-release \
        test-client-python-release test-python-release clean-python

PYTHON       ?= python3
PYBIND11_DIR := $(shell $(PYTHON) -m pybind11 --cmakedir 2>/dev/null)
PY_EXE       := $(shell command -v $(PYTHON))

##@ Python package (submit client + _archive binding; needs pybind11)

# The _archive binding is pure C++ stdlib (no OpenFHE), so it builds standalone via
# python/CMakeLists.txt (dual-mode) — fast submit-only iteration without OpenFHE.
config-python-archive: ## Configure the standalone _archive binding. Needs pybind11.
	@if [ -z "$(PYBIND11_DIR)" ]; then \
		echo "pybind11 CMake dir not found for '$(PYTHON)'. Install: $(PYTHON) -m pip install pybind11"; \
		exit 1; \
	fi
	cmake -S python -B build/python -Dpybind11_DIR=$(PYBIND11_DIR) -DPython_EXECUTABLE=$(PY_EXE)

build-python-archive: config-python-archive ## Build the standalone _archive binding (submit-only; no OpenFHE)
	cmake --build build/python -j $(NUM_CPUS)

test-submit-python-release: build-python-archive ## submit() + _archive smoke (mock server; no OpenFHE)
	PYTHONPATH=$(CURDIR)/build/python $(PY_EXE) python/tests/submit_smoke.py

test-fog-python-release: build-python-archive ## niobium_sdk.fog control-plane smoke (mock fog-api + worker; no OpenFHE)
	PYTHONPATH=$(CURDIR)/build/python $(PY_EXE) python/tests/fog_smoke.py

# --- Full wheel assembly -------------------------------------------------------
# Drives the top-level CMake with NIOBIUM_CLIENT_WITH_PYTHON: builds openfhe +
# niobium_session (via niobium-fhetch WITH_PYTHON) + _archive and assembles the
# importable package at build-wheel/niobium_sdk/. Separate build dir from the
# client build (build/) and the standalone submit build (build/python). Needs the
# OpenFHE substrate installed (make install-release) + pybind11.
config-wheel-release: ## Configure the full niobium_sdk package assembly
	@if [ -z "$(PYBIND11_DIR)" ]; then \
		echo "pybind11 CMake dir not found for '$(PYTHON)'. Install: $(PYTHON) -m pip install pybind11"; \
		exit 1; \
	fi
	cmake -S . -B build-wheel -DCMAKE_BUILD_TYPE=Release \
		-DNIOBIUM_CLIENT_WITH_PYTHON=ON \
		-DNIOBIUM_CLIENT_WITH_AUTO_FACADE=OFF \
		-DNIOBIUM_CLIENT_WITH_FHETCH_TRANSPORT=OFF \
		-DNIOBIUM_CLIENT_WITH_EXAMPLES=OFF \
		-DOPENFHE_INSTALL_DIR=$(OPENFHE_INSTALL_DIR) \
		-Dpybind11_DIR=$(PYBIND11_DIR) -DPython_EXECUTABLE=$(PY_EXE)

build-wheel-release: config-wheel-release ## Build + assemble build-wheel/niobium_sdk/
	cmake --build build-wheel -j $(NUM_CPUS)

# Env for importing + running the assembled package tree (build-wheel/niobium_sdk):
# import from build-wheel, resolve the OpenFHE dylibs + bundled natives at runtime.
# Shared by the wheel smoke and the example-scenario tests below.
WHEEL_RUN_ENV = PYTHONPATH=$(CURDIR)/build-wheel \
	DYLD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib:$(CURDIR)/build-wheel/niobium_sdk \
	LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib:$(CURDIR)/build-wheel/niobium_sdk

test-wheel-smoke-release: build-wheel-release ## Primary-only smoke against the assembled package
	$(WHEEL_RUN_ENV) $(PY_EXE) python/tests/wheel_smoke.py

# --- Example-scenario tests against the assembled package ----------------------
# The Python analogs of the C++ test-<scenario>-release targets: run the
# python/examples/<scenario> client → server → decrypt ports against the assembled
# niobium_sdk tree, each printing the example's own PASS/FAIL line. (The example
# servers auto-add --no-ring-dim-check.) auto-facade and the ring-dim-check negative
# test have no analog here — the wheel is built WITH_AUTO_FACADE=OFF and there is no
# Python ring-dim scenario; the compiler/transport C++ targets are out of scope for
# the open-source client (submit() is covered by test-submit-python-release).
NB_PY_EX := python/examples

test-mult-python-release: build-wheel-release ## Python mult example: client → server → decrypt (assembled wheel)
	@rm -rf mult_keys mult_server_workload_*
	@echo "=== mult client (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/mult/client.py mult_keys 7 13
	@echo "=== mult server (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/mult/server.py mult_keys
	@echo "=== mult decrypt (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/mult/decrypt.py mult_keys

# simple_ops op sweep — Python analog of run-simple-op (client/server quiet; only
# the per-op decrypt PASS/FAIL is shown, Python tracebacks still surface on stderr).
define run-simple-op-python
	@echo "=== $(1) ($(2) $(3)) (python) ==="
	@rm -rf simple_ops_keys simple_ops_server_workload_*
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/simple_ops/client.py simple_ops_keys $(2) $(3) >/dev/null
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/simple_ops/server.py simple_ops_keys $(1) >/dev/null
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/simple_ops/decrypt.py simple_ops_keys $(1) 2>&1 | grep -E "PASS|FAIL"
endef

test-simple-ops-python-release: build-wheel-release ## Python simple_ops sweep: all ops (assembled wheel)
	$(call run-simple-op-python,ADD,5,6)
	$(call run-simple-op-python,SUB,5,6)
	$(call run-simple-op-python,NEG,5,6)
	$(call run-simple-op-python,ADDI,5,6)
	$(call run-simple-op-python,SUBI,5,6)
	$(call run-simple-op-python,MULI,5,6)
	$(call run-simple-op-python,ADD_ADD,5,6)
	$(call run-simple-op-python,ADD_SUB,5,6)
	$(call run-simple-op-python,MUL,5,6)
	$(call run-simple-op-python,MUL_ADD,5,6)
	$(call run-simple-op-python,ADD_MUL,5,6)
	$(call run-simple-op-python,MUL_MUL,5,6)
	$(call run-simple-op-python,MORPH,5,6)

test-op-python-release: build-wheel-release ## Single python simple_ops op: make test-op-python-release OP=ADD A=5 B=6
	$(call run-simple-op-python,$(OP),$(A),$(B))

test-plaintext-add-python-release: build-wheel-release ## Python plaintext-add example: client → server → decrypt (assembled wheel)
	@rm -rf plaintext_add_keys plaintext_add_server_workload_*
	@echo "=== plaintext_add client (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/plaintext_add/client.py plaintext_add_keys
	@echo "=== plaintext_add server (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/plaintext_add/server.py plaintext_add_keys
	@echo "=== plaintext_add decrypt (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/plaintext_add/decrypt.py plaintext_add_keys

test-bootstrap-python-release: build-wheel-release ## Python bootstrap example: client → server → decrypt (assembled wheel)
	@rm -rf bootstrap_keys bootstrap_server_workload_*
	@echo "=== bootstrap client (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/bootstrap/client.py bootstrap_keys
	@echo "=== bootstrap server (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/bootstrap/server.py bootstrap_keys
	@echo "=== bootstrap decrypt (python) ==="
	@$(WHEEL_RUN_ENV) $(PY_EXE) $(NB_PY_EX)/bootstrap/decrypt.py bootstrap_keys

# Ring-dimension hardware-guard negative test — Python analog of
# test-ring-dim-check-release. Asserts capture_crypto_context() rejects an
# HW-incompatible ring dim (2048) when the check is on (no --no-ring-dim-check).
test-ring-dim-check-python-release: build-wheel-release ## Python ring-dim guard negative test (assembled wheel)
	@rm -rf ring_dim_check_smoke_workload_*
	@$(WHEEL_RUN_ENV) $(PY_EXE) python/tests/ring_dim_check_smoke.py

# Python analog of test-fhetch-release: delegate to the niobium-fhetch submodule's
# own Python roundtrip sweep (simple_ops + plaintext-add + bootstrap, each primary +
# secondary via fhetch_driver). Forwards the same OpenFHE/JSON flags plus PYTHON, so
# the submodule builds its bindings against this repo's OpenFHE install with the same
# interpreter (needs pybind11 in that PYTHON). No analog for the C++-only simple_fhetch
# / fhetch_driver mechanics in fhetch's test-release.
# Forward the interpreter as an ABSOLUTE path ($(abspath $(PY_EXE))): this recipe cd's
# into the submodule, so a relative PYTHON (e.g. .venv/bin/python) would not resolve
# there. PY_EXE is `command -v $(PYTHON)`, so a bare `python3` is PATH-resolved first.
test-fhetch-python-release: $(OPENFHE_BUILD_DEP_RELEASE) ## Run the fhetch submodule's Python roundtrip sweep (simple_ops + plaintext-add + bootstrap)
	$(MAKE) -C $(FHETCH_DIR) OPENFHE_INSTALL_DIR="$(OPENFHE_INSTALL_DIR)" $(if $(JSON_INCLUDE_DIR),JSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)") EXTERNAL_OPENFHE=$(EXTERNAL_OPENFHE) PYTHON="$(abspath $(PY_EXE))" test-roundtrip-python-release

# --- Aggregates (mirror the C++ test-client-release / test-release) ------------
# All client-level Python tests: the scenario ports + the ring-dim guard. No
# auto-facade analog — the wheel is built WITH_AUTO_FACADE=OFF.
test-client-python-release: test-mult-python-release test-simple-ops-python-release test-plaintext-add-python-release test-bootstrap-python-release test-ring-dim-check-python-release ## All client-level Python tests (analog of test-client-release)

# Full Python sweep: client-level + the fhetch submodule's own Python roundtrips.
test-python-release: test-client-python-release test-fhetch-python-release ## Full Python test sweep (analog of test-release)

# Build the distributable wheel via PEP 517 (scikit-build-core). Uses build
# isolation, so it fetches scikit-build-core + pybind11 itself; needs `build`
# (pip install build) and the OpenFHE substrate installed. CI (cibuildwheel) runs
# the per-version × platform matrix + delocate/auditwheel; this is the local path.
wheel: ## Build the niobium_sdk wheel into dist/ (python -m build)
	$(PY_EXE) -m build --wheel

##@ Python cleanup

# Remove all Python build artifacts + any virtualenv + bytecode/egg-info. Virtualenvs
# are found generically by their `pyvenv.cfg` marker (so custom-named envs are caught,
# not just `.venv`). Leaves the C++ `build/` and the OpenFHE substrate (`vendor/lib`) —
# use `make clean` / `make clean-all` for those.
clean-python: ## Remove Python build artifacts, virtualenvs, and bytecode
	-rm -rf build-wheel build/python dist wheelhouse
	@# virtualenvs: any dir containing pyvenv.cfg, name-agnostic; skip submodules
	@find . -name pyvenv.cfg -not -path './vendor/*' 2>/dev/null | while read -r cfg; do \
		d=$$(dirname "$$cfg"); echo "  rm venv $$d"; rm -rf "$$d"; done
	@find . -type d -name __pycache__     -not -path './vendor/*' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name '*.egg-info'    -not -path './vendor/*' -exec rm -rf {} + 2>/dev/null || true
