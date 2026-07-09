# Python distribution channel — thin dev-convenience dispatch.
#
# The real build system is pyproject.toml + scikit-build-core + cibuildwheel; these
# targets just wrap the dev loop and `python -m build`. Included by the root Makefile
# (shares its variable namespace: OPENFHE_INSTALL_DIR, NUM_CPUS, CURDIR come from there).

.PHONY: config-python-release build-python-release test-submit-python-release \
        config-wheel-release build-wheel-release test-wheel-smoke-release wheel

PYTHON       ?= python3
PYBIND11_DIR := $(shell $(PYTHON) -m pybind11 --cmakedir 2>/dev/null)
PY_EXE       := $(shell command -v $(PYTHON))

##@ Python package (submit client + _archive binding; needs pybind11)

# The _archive binding is pure C++ stdlib (no OpenFHE), so it builds standalone via
# python/CMakeLists.txt (dual-mode) — fast submit-only iteration without OpenFHE.
config-python-release: ## Configure the Python package (_archive binding). Needs pybind11.
	@if [ -z "$(PYBIND11_DIR)" ]; then \
		echo "pybind11 CMake dir not found for '$(PYTHON)'. Install: $(PYTHON) -m pip install pybind11"; \
		exit 1; \
	fi
	cmake -S python -B build/python -Dpybind11_DIR=$(PYBIND11_DIR) -DPython_EXECUTABLE=$(PY_EXE)

build-python-release: config-python-release ## Build the Python package (_archive)
	cmake --build build/python -j $(NUM_CPUS)

test-submit-python-release: build-python-release ## submit() + _archive smoke (mock server; no OpenFHE)
	PYTHONPATH=$(CURDIR)/build/python $(PY_EXE) python/tests/submit_smoke.py

# --- Full wheel assembly -------------------------------------------------------
# Drives the top-level CMake with NIOBIUM_CLIENT_WITH_PYTHON: builds openfhe +
# niobium_session (via niobium-fhetch WITH_PYTHON) + _archive and assembles the
# importable package at build-wheel/niobium_client/. Separate build dir from the
# client build (build/) and the standalone submit build (build/python). Needs the
# OpenFHE substrate installed (make install-release) + pybind11.
config-wheel-release: ## Configure the full niobium_client package assembly
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

build-wheel-release: config-wheel-release ## Build + assemble build-wheel/niobium_client/
	cmake --build build-wheel -j $(NUM_CPUS)

test-wheel-smoke-release: build-wheel-release ## Primary-only smoke against the assembled package
	PYTHONPATH=$(CURDIR)/build-wheel \
	DYLD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib:$(CURDIR)/build-wheel/niobium_client \
	LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib:$(CURDIR)/build-wheel/niobium_client \
	$(PY_EXE) python/tests/wheel_smoke.py

# Build the distributable wheel via PEP 517 (scikit-build-core). Uses build
# isolation, so it fetches scikit-build-core + pybind11 itself; needs `build`
# (pip install build) and the OpenFHE substrate installed. CI (cibuildwheel) runs
# the per-version × platform matrix + delocate/auditwheel; this is the local path.
wheel: ## Build the niobium_client wheel into dist/ (python -m build)
	$(PY_EXE) -m build --wheel
