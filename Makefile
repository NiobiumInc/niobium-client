# ==============================================================================
# Niobium Client — Build System
# ==============================================================================
# Convention: dbuild/ for Debug, build/ for Release (matches niobium-compiler).
#
# Layout: the FHETCH library, compiler session API, and simulator live in
# vendor/niobium-fhetch (a submodule). OpenFHE is a nested submodule inside
# that (vendor/niobium-fhetch/vendor/openfhe). This Makefile builds OpenFHE,
# then the top-level CMake add_subdirectory()s niobium-fhetch and the client
# examples into a single build tree.
#
# Quick start:
#   make sync             # One-time: sync submodules (recursive)
#   make config           # Configure OpenFHE + client (Debug)
#   make build            # Build OpenFHE + client (Debug)
#
# Release:
#   make release          # Shortcut: config-release + build-release
#
# Full list:
#   make help
# ==============================================================================

SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c

# ==============================================================================
# Platform Detection & CPU Count
# ==============================================================================

UNAME_S := $(shell uname -s)

ifndef NUM_CPUS
  ifeq ($(UNAME_S), Darwin)
    NUM_CPUS := $(shell sysctl -n hw.ncpu)
    export DYLD_LIBRARY_PATH := $(CURDIR)/vendor/lib/openfhe/lib:$(DYLD_LIBRARY_PATH)
  else
    NUM_CPUS := $(shell nproc)
    export LD_LIBRARY_PATH := $(CURDIR)/vendor/lib/openfhe/lib:$(LD_LIBRARY_PATH)
  endif
endif

# ==============================================================================
# Build Configuration
# ==============================================================================

BUILD_CONFIG = Debug
BUILD_DIR = dbuild

define set-build-config
$(eval BUILD_CONFIG = $(1))
$(eval BUILD_DIR = $(2))
endef

# Directories
VENDOR_DIR       := $(CURDIR)/vendor
VENDOR_LIB_DIR   := $(VENDOR_DIR)/lib

# Paths overridable by a parent build (e.g. niobium-compiler) so the same
# source tree can be built either standalone (using vendored submodules) or
# with deps supplied from outside. Use ?= so command-line / env overrides win.
FHETCH_DIR          ?= $(VENDOR_DIR)/niobium-fhetch
OPENFHE_DIR         ?= $(FHETCH_DIR)/vendor/openfhe
OPENFHE_INSTALL_DIR ?= $(VENDOR_LIB_DIR)/openfhe
JSON_INCLUDE_DIR    ?=
CLIENT_INSTALL_DIR  := $(VENDOR_LIB_DIR)/niobium-client

# When EXTERNAL_OPENFHE=1, the parent has already built+installed OpenFHE and
# pointed OPENFHE_INSTALL_DIR at it; we skip our own openfhe config/build
# steps and the matching submodule sync.
EXTERNAL_OPENFHE ?= 0
ifeq ($(EXTERNAL_OPENFHE),1)
  OPENFHE_CONFIG_DEP_DEBUG   :=
  OPENFHE_CONFIG_DEP_RELEASE :=
  OPENFHE_BUILD_DEP_DEBUG    :=
  OPENFHE_BUILD_DEP_RELEASE  :=
else
  OPENFHE_CONFIG_DEP_DEBUG   := config-openfhe
  OPENFHE_CONFIG_DEP_RELEASE := config-openfhe-release
  OPENFHE_BUILD_DEP_DEBUG    := build-openfhe
  OPENFHE_BUILD_DEP_RELEASE  := build-openfhe-release
endif

# CMake -D flags that are only emitted when the corresponding override is set.
# Quote the path so cmake receives a single argument even if it contains spaces.
CMAKE_CLIENT_FHETCH_DIR_FLAG := $(if $(NIOBIUM_CLIENT_FHETCH_DIR),-DNIOBIUM_CLIENT_FHETCH_DIR="$(NIOBIUM_CLIENT_FHETCH_DIR)")
CMAKE_JSON_INCLUDE_DIR_FLAG  := $(if $(JSON_INCLUDE_DIR),-DJSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)")

# OpenMP toggle (OFF by default, override with: make config-openfhe OPENMP=ON)
OPENMP ?= OFF

# Native CPU optimizations (OFF by default for portability)
NATIVEOPT ?= OFF

# ==============================================================================
# Targets
# ==============================================================================

.PHONY: help sync sync-submodules update-openfhe update-niobium-fhetch \
        config config-release build build-release release \
        config-openfhe config-openfhe-release build-openfhe build-openfhe-release \
        config-client config-client-release \
        install install-release install-cli clean clean-all

##@ Primary Targets

help: ## Display this help message
	@echo "Niobium Client Build System"
	@echo ""
	@echo "Usage:"
	@echo "  make \033[36m<target>\033[0m"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf ""} \
		/^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-28s\033[0m %s\n", $$1, $$2 } \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Submodules

sync: sync-submodules ## Sync all submodules (recursive) — niobium-fhetch + its nested openfhe/json

sync-submodules: ## Sync niobium-fhetch and its nested submodules
	git submodule update --init --recursive

update-niobium-fhetch: ## Update niobium-fhetch to latest remote commit on main
	cd $(FHETCH_DIR) && git fetch origin && git checkout main && git pull origin main

update-openfhe: ## Update OpenFHE (inside niobium-fhetch) to latest remote commit
	cd $(OPENFHE_DIR) && git fetch origin && git checkout nb_main && git pull origin nb_main

##@ OpenFHE Build

config-openfhe: ## Configure OpenFHE (Debug)
	$(call set-build-config,Debug,dbuild)
	cmake -S $(OPENFHE_DIR) -B $(OPENFHE_DIR)/dbuild \
		-DCMAKE_BUILD_TYPE=Debug \
		-DBUILD_EXAMPLES=OFF \
		-DBUILD_UNITTESTS=OFF \
		-DBUILD_BENCHMARKS=OFF \
		-DBUILD_EXTRAS=OFF \
		-DWITH_CPROBES=ON \
		-DWITH_REDUCED_NOISE=ON \
		-DWITH_NATIVEOPT=$(NATIVEOPT) \
		-DWITH_OPENMP=$(OPENMP) \
		-DCMAKE_INSTALL_PREFIX=$(OPENFHE_INSTALL_DIR)

config-openfhe-release: ## Configure OpenFHE (Release)
	$(call set-build-config,Release,build)
	cmake -S $(OPENFHE_DIR) -B $(OPENFHE_DIR)/build \
		-DCMAKE_BUILD_TYPE=Release \
		-DBUILD_EXAMPLES=OFF \
		-DBUILD_UNITTESTS=OFF \
		-DBUILD_BENCHMARKS=OFF \
		-DBUILD_EXTRAS=OFF \
		-DWITH_CPROBES=ON \
		-DWITH_REDUCED_NOISE=ON \
		-DWITH_NATIVEOPT=$(NATIVEOPT) \
		-DWITH_OPENMP=$(OPENMP) \
		-DCMAKE_INSTALL_PREFIX=$(OPENFHE_INSTALL_DIR)

build-openfhe: ## Build and install OpenFHE (Debug)
	$(call set-build-config,Debug,dbuild)
	cd $(OPENFHE_DIR) && \
		cmake --build dbuild -j $(NUM_CPUS) --target install --config Debug

build-openfhe-release: ## Build and install OpenFHE (Release)
	$(call set-build-config,Release,build)
	cd $(OPENFHE_DIR) && \
		cmake --build build -j $(NUM_CPUS) --target install --config Release

##@ Client Build

# Records build-time deps for scripts/ that run after install (e.g. the
# transport test scripts on Linux, where there's no rpath fallback). Sourced
# by scripts/test_transport_mult.sh and scripts/fhetch_server.sh so they
# point LD_LIBRARY_PATH at whatever OpenFHE the build was actually linked
# against — the client's own vendored install in standalone builds, or a
# parent-supplied install when a parent (niobium-compiler) drove the build.
define write-build-env
	printf 'OPENFHE_LIB=%s/lib\n' "$(OPENFHE_INSTALL_DIR)" > $(CURDIR)/$(1)/niobium_client.env
endef

config-client: ## Configure the client + fhetch library + examples (Debug)
	$(call set-build-config,Debug,dbuild)
	cmake -S $(CURDIR) -B $(CURDIR)/dbuild \
		-DCMAKE_BUILD_TYPE=Debug \
		-DOPENFHE_INSTALL_DIR=$(OPENFHE_INSTALL_DIR) \
		$(CMAKE_CLIENT_FHETCH_DIR_FLAG) \
		$(CMAKE_JSON_INCLUDE_DIR_FLAG) \
		-DNIOBIUM_CLIENT_WITH_EXAMPLES=ON \
		-DCMAKE_INSTALL_PREFIX=$(CLIENT_INSTALL_DIR)
	@$(call write-build-env,dbuild)

config-client-release: ## Configure the client + fhetch library + examples (Release)
	$(call set-build-config,Release,build)
	cmake -S $(CURDIR) -B $(CURDIR)/build \
		-DCMAKE_BUILD_TYPE=Release \
		-DOPENFHE_INSTALL_DIR=$(OPENFHE_INSTALL_DIR) \
		$(CMAKE_CLIENT_FHETCH_DIR_FLAG) \
		$(CMAKE_JSON_INCLUDE_DIR_FLAG) \
		-DNIOBIUM_CLIENT_WITH_EXAMPLES=ON \
		-DCMAKE_INSTALL_PREFIX=$(CLIENT_INSTALL_DIR)
	@$(call write-build-env,build)

##@ Combined Targets

config: $(OPENFHE_CONFIG_DEP_DEBUG) config-client ## Configure everything (Debug)

config-release: $(OPENFHE_CONFIG_DEP_RELEASE) config-client-release ## Configure everything (Release)

build: $(OPENFHE_BUILD_DEP_DEBUG) ## Build everything (Debug)
	$(call set-build-config,Debug,dbuild)
	cmake --build dbuild -j $(NUM_CPUS) --config Debug

build-release: $(OPENFHE_BUILD_DEP_RELEASE) ## Build everything (Release)
	$(call set-build-config,Release,build)
	cmake --build build -j $(NUM_CPUS) --config Release

release: config-release build-release ## Shortcut: configure + build everything (Release)

##@ Installation

install: ## Install the client + fhetch library (Debug)
	$(call set-build-config,Debug,dbuild)
	cmake --install dbuild

install-release: ## Install the client + fhetch library (Release)
	$(call set-build-config,Release,build)
	cmake --install build

# The `fog` CLI is a self-contained Python tool (scripts/fog) — no build needed,
# so it installs independently of the client library. Onto PATH via CLI_PREFIX
# (default ~/.local/bin); override with `make install-cli CLI_PREFIX=/some/where`.
CLI_PREFIX ?= $(HOME)/.local
NBCC_FHETCH_REPLAY_BIN = build/src/fhetch_transport/nbcc_fhetch_replay

install-cli: ## Install the fog CLI + nbcc_fhetch_replay to $(CLI_PREFIX)/bin
	@mkdir -p "$(CLI_PREFIX)/bin"
	install -m 755 scripts/fog "$(CLI_PREFIX)/bin/fog"
	@echo "Installed fog -> $(CLI_PREFIX)/bin/fog"
	@if [ ! -x "$(NBCC_FHETCH_REPLAY_BIN)" ]; then \
		echo "ERROR: $(NBCC_FHETCH_REPLAY_BIN) not found — run 'make release' first"; \
		exit 2; \
	fi
	install -m 755 "$(NBCC_FHETCH_REPLAY_BIN)" "$(CLI_PREFIX)/bin/nbcc_fhetch_replay"
	@echo "Installed nbcc_fhetch_replay -> $(CLI_PREFIX)/bin/nbcc_fhetch_replay"

##@ Testing

test-bootstrap: build ## Run the bootstrap example: client → server → decrypt (Debug)
	$(call set-build-config,Debug,dbuild)
	@rm -rf bootstrap_keys bootstrap_server_*
	@echo "=== Running bootstrap client ==="
	$(BUILD_DIR)/examples/bootstrap_client bootstrap_keys
	@echo ""
	@echo "=== Running bootstrap server ==="
	$(BUILD_DIR)/examples/bootstrap_server bootstrap_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running bootstrap decrypt ==="
	$(BUILD_DIR)/examples/bootstrap_decrypt bootstrap_keys

test-bootstrap-release: build-release ## Run the bootstrap example: client → server → decrypt (Release)
	$(call set-build-config,Release,build)
	@rm -rf bootstrap_keys bootstrap_server_*
	@echo "=== Running bootstrap client ==="
	$(BUILD_DIR)/examples/bootstrap_client bootstrap_keys
	@echo ""
	@echo "=== Running bootstrap server ==="
	$(BUILD_DIR)/examples/bootstrap_server bootstrap_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running bootstrap decrypt ==="
	$(BUILD_DIR)/examples/bootstrap_decrypt bootstrap_keys

test-plaintext-add: build ## Run the plaintext-add example: client → server → decrypt (Debug)
	$(call set-build-config,Debug,dbuild)
	@rm -rf plaintext_add_keys plaintext_add_server_*
	@echo "=== Running plaintext_add client ==="
	$(BUILD_DIR)/examples/plaintext_add_client plaintext_add_keys
	@echo ""
	@echo "=== Running plaintext_add server ==="
	$(BUILD_DIR)/examples/plaintext_add_server plaintext_add_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running plaintext_add decrypt ==="
	$(BUILD_DIR)/examples/plaintext_add_decrypt plaintext_add_keys

test-plaintext-add-release: build-release ## Run the plaintext-add example: client → server → decrypt (Release)
	$(call set-build-config,Release,build)
	@rm -rf plaintext_add_keys plaintext_add_server_*
	@echo "=== Running plaintext_add client ==="
	$(BUILD_DIR)/examples/plaintext_add_client plaintext_add_keys
	@echo ""
	@echo "=== Running plaintext_add server ==="
	$(BUILD_DIR)/examples/plaintext_add_server plaintext_add_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running plaintext_add decrypt ==="
	$(BUILD_DIR)/examples/plaintext_add_decrypt plaintext_add_keys

# Default op exercised by the auto-facade test. ADD is the richest op
# that currently PASSES both the recording decrypt and the replay
# decrypt. MUL (and anything relin-heavy) records correctly but the
# sim-reconstructed ciphertext fails the CKKS decrypt tolerance on
# replay. Four distinct fixes were applied chasing MUL:
#
#   1. tag_keys() now fires after the user's DeserializeEvalMultKey has
#      loaded the key maps (during atexit on record, inside
#      ensure_replayed on replay).
#   2. NiobiumAutoScheme proxy is installed only in replay mode; the
#      recording path uses the real scheme + OPENFHE_CPROBES probes.
#   3. on_deserialize_ciphertext ignores facade-internal deserializes
#      (Compiler::result loading serialized_probes/<name>.ct) so the
#      template is not tagged as an input that overwrites sim memory.
#   4. captured_inputs + captured_outputs are rehydrated from disk on
#      cache-hit replay.
#
# With those four changes every live-in address loads cleanly
# ("Live-in: 40, loaded: 232 direct + 0 propagated, unloaded: 0") but
# the reconstructed ciphertext for MUL still decrypts past the 0.01
# tolerance. The simple_ops/MUL trace via fhetch_driver roundtrip
# passes the same arithmetic, so the gap is specific to the
# auto-facade's template+refill path for relin-based ops. Tracked as
# a follow-up simulator-precision task.
#
# Override AUTO_OP=MUL AUTO_EXPECTED=21 to reproduce the failure mode.
AUTO_OP        ?= ADD
AUTO_A         ?= 7
AUTO_B         ?= 3
AUTO_IMM       ?= 0
AUTO_EXPECTED  ?= 10

test-auto-ciphers-release: build-release ## Auto-facade ciphers_ops: keygen → record → replay (no niobium:: in user code). Op/values overridable: AUTO_OP=... AUTO_A=... AUTO_B=... AUTO_IMM=... AUTO_EXPECTED=...
	$(call set-build-config,Release,build)
	@rm -rf ciphers_auto
	@mkdir -p ciphers_auto
	@echo "=== keygen ==="
	@cd ciphers_auto && LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib \
		$(CURDIR)/$(BUILD_DIR)/examples/ciphers_ops_cache_keys 0
	@echo "=== encrypt ==="
	@cd ciphers_auto && LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib \
		$(CURDIR)/$(BUILD_DIR)/examples/ciphers_ops_client 0 $(AUTO_A) $(AUTO_B) output_a.bin output_b.bin
	@echo "=== record pass (auto-facade, op=$(AUTO_OP), imm=$(AUTO_IMM), expected=$(AUTO_EXPECTED)) ==="
	@cd ciphers_auto && LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib \
		python3 $(CURDIR)/tools/nbcc.py \
		--name auto_ops_$(AUTO_OP) --cache wl=TOY --cache op=$(AUTO_OP) \
		--keys-mult io/toy/keys/mk.bin --keys-auto io/toy/keys/rk.bin \
		--no-ring-dim-check \
		-- \
		$(CURDIR)/$(BUILD_DIR)/examples/ciphers_ops_server_auto 0 output_a.bin output_b.bin $(AUTO_EXPECTED) $(AUTO_OP) $(AUTO_IMM)
	@echo ""
	@echo "=== replay pass (cache hit) ==="
	@cd ciphers_auto && LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib \
		python3 $(CURDIR)/tools/nbcc.py \
		--name auto_ops_$(AUTO_OP) --cache wl=TOY --cache op=$(AUTO_OP) \
		--keys-mult io/toy/keys/mk.bin --keys-auto io/toy/keys/rk.bin \
		--no-ring-dim-check \
		-- \
		$(CURDIR)/$(BUILD_DIR)/examples/ciphers_ops_server_auto 0 output_a.bin output_b.bin $(AUTO_EXPECTED) $(AUTO_OP) $(AUTO_IMM)

test-mult: build ## Run the multiply example: client → server → decrypt (Debug)
	$(call set-build-config,Debug,dbuild)
	@rm -rf mult_keys mult_server_workload_*
	@echo "=== Running mult client ==="
	$(BUILD_DIR)/examples/mult_client mult_keys 7 13
	@echo ""
	@echo "=== Running mult server ==="
	$(BUILD_DIR)/examples/mult_server mult_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running mult decrypt ==="
	$(BUILD_DIR)/examples/mult_decrypt mult_keys

test-mult-release: build-release ## Run the multiply example: client → server → decrypt (Release)
	$(call set-build-config,Release,build)
	@rm -rf mult_keys mult_server_workload_*
	@echo "=== Running mult client ==="
	$(BUILD_DIR)/examples/mult_client mult_keys 7 13
	@echo ""
	@echo "=== Running mult server ==="
	$(BUILD_DIR)/examples/mult_server mult_keys --no-ring-dim-check
	@echo ""
	@echo "=== Running mult decrypt ==="
	$(BUILD_DIR)/examples/mult_decrypt mult_keys

# ==============================================================================
# test-mult-target — first client-server test exercising the --target= path.
#
# The client records the MUL trace in hollow mode (producing a fhetch project),
# then the server passes --target=<TARGET> to niobium::compiler().replay().
# The replay call skips the in-process FHETCH simulator and dispatches to the
# compiler-side nbcc_fhetch_replay executable instead, which re-drives the
# trace through the full Niobium optimization pipeline and writes ciphertext
# probes into <program_dir>/serialized_probes/ for the server's result() call.
#
# Requires: niobium-compiler must have been built with `make release` so that
# $(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay exists. Override the
# compiler root with NIOBIUM_COMPILER_ROOT=... when invoking make.
#
# Known limitation (2026-04-20): the compiler and client each ship an
# independent OpenFHE install under vendor/lib/openfhe/. They must be built
# from the same sources with the same flags (ideally sharing one install)
# for cereal-binary input/key files to deserialize across the boundary.
# Symptom if they don't match:
#   "Error loading cereal binary inputs: … serialized object version N is
#    from a later version of the library"
# ==============================================================================

NIOBIUM_COMPILER_ROOT ?= $(realpath $(CURDIR)/../..)
TARGET ?= FUNC_SIM

test-mult-target-release: build-release ## Run mult with --target=$(TARGET). Overrides: TARGET=FUNC_SIM|fpga5.2|…  NIOBIUM_COMPILER_ROOT=/path
	$(call set-build-config,Release,build)
	@if [ ! -x "$(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay" ]; then \
		echo "ERROR: nbcc_fhetch_replay not found at $(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay"; \
		echo "Build it with: (cd $(NIOBIUM_COMPILER_ROOT) && make release)"; \
		exit 2; \
	fi
	@rm -rf mult_keys mult_server_workload_* nbcc_fhetch_replay_source_*
	@echo "=== [1/3] mult_client: keygen + encrypt ==="
	$(BUILD_DIR)/examples/mult_client mult_keys 7 13
	@echo ""
	@echo "=== [2/3] mult_server --target=$(TARGET) (hollow record → dispatch to compiler) ==="
	NBCC_FHETCH_REPLAY=$(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay \
	LD_LIBRARY_PATH=$(OPENFHE_INSTALL_DIR)/lib:$(NIOBIUM_COMPILER_ROOT)/build:$(NIOBIUM_COMPILER_ROOT)/deps/photovoltaic/build/ntl/lib:$(LD_LIBRARY_PATH) \
		$(BUILD_DIR)/examples/mult_server mult_keys --target=$(TARGET) --no-ring-dim-check
	@echo ""
	@echo "=== [3/3] mult_decrypt ==="
	$(BUILD_DIR)/examples/mult_decrypt mult_keys

# ==============================================================================
# test-mult-transport-release — client → nbcc_fhetch_replay_server → compiler
#
# End-to-end round trip through the HTTP transport. Starts the server daemon
# in the background with --exec pointing at the compiler's nbcc_fhetch_replay,
# puts the client-side forwarder first on PATH so libnbfhetch's replay()
# dispatches to it, runs mult_client → mult_server --target=FUNC_SIM →
# mult_decrypt, and tears the server down.
#
# Same override knobs as test-mult-target-release (TARGET is pinned to
# FUNC_SIM here since any non-local target triggers the dispatch).
# ==============================================================================

test-mult-transport-release: build-release ## End-to-end transport round trip (server+client+compiler, FUNC_SIM)
	$(call set-build-config,Release,build)
	@if [ ! -x "$(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay" ]; then \
		echo "ERROR: nbcc_fhetch_replay not found at $(NIOBIUM_COMPILER_ROOT)/build/nbcc_fhetch_replay"; \
		echo "Build it with: (cd $(NIOBIUM_COMPILER_ROOT) && make release)"; \
		exit 2; \
	fi
	@NIOBIUM_COMPILER_ROOT="$(NIOBIUM_COMPILER_ROOT)" \
	 NIOBIUM_COMPILER_BUILD="$(NIOBIUM_COMPILER_ROOT)/build" \
	 scripts/test_transport_mult.sh

test-sim-mult: test-mult ## Record mult trace then simulate it (Debug)
	$(call set-build-config,Debug,dbuild)
	@echo ""
	@echo "=== Running FHETCH simulator on mult trace ==="
	$(BUILD_DIR)/vendor/niobium-fhetch/fhetch_sim mult_server_workload_bfv_mult/mult_server_workload_bfv_mult.fhetch --ring-dim 8192

test-sim-mult-release: test-mult-release ## Record mult trace then simulate it (Release)
	$(call set-build-config,Release,build)
	@echo ""
	@echo "=== Running FHETCH simulator on mult trace ==="
	$(BUILD_DIR)/vendor/niobium-fhetch/fhetch_sim mult_server_workload_bfv_mult/mult_server_workload_bfv_mult.fhetch --ring-dim 8192

test-sim-bootstrap-release: test-bootstrap-release ## Record bootstrap trace then simulate it (Release)
	$(call set-build-config,Release,build)
	@echo ""
	@echo "=== Running FHETCH simulator on bootstrap trace ==="
	$(BUILD_DIR)/vendor/niobium-fhetch/fhetch_sim bootstrap_server_workload_ckks_bootstrap/bootstrap_server_workload_ckks_bootstrap.fhetch --ring-dim 2048

# Helper: run a single simple_ops test
define run-simple-op
	@echo "=== Testing $(1): $(2) ==="
	@rm -rf simple_ops_keys simple_ops_server_*
	@$(BUILD_DIR)/examples/simple_ops_client simple_ops_keys $(2) $(3) 2>&1 | tail -1
	@$(BUILD_DIR)/examples/simple_ops_server simple_ops_keys $(1) --no-ring-dim-check 2>&1 | grep -E "Live-in|Complete|ERROR"
	@$(BUILD_DIR)/examples/simple_ops_decrypt simple_ops_keys $(1) 2>&1 | grep -E "PASS|FAIL"
	@echo ""
endef

test-simple-ops: build ## Run all simple_ops tests (Debug)
	$(call set-build-config,Debug,dbuild)
	$(call run-simple-op,ADD,5,6)
	$(call run-simple-op,SUB,5,6)
	$(call run-simple-op,NEG,5,6)
	$(call run-simple-op,ADD_ADD,5,6)
	$(call run-simple-op,ADD_SUB,5,6)
	$(call run-simple-op,MUL,5,6)
	$(call run-simple-op,MUL_ADD,5,6)
	$(call run-simple-op,ADD_MUL,5,6)
	$(call run-simple-op,MUL_MUL,5,6)
	$(call run-simple-op,MORPH,5,6)

test-simple-ops-release: build-release ## Run all simple_ops tests (Release)
	$(call set-build-config,Release,build)
	$(call run-simple-op,ADD,5,6)
	$(call run-simple-op,SUB,5,6)
	$(call run-simple-op,NEG,5,6)
	$(call run-simple-op,ADDI,5,6)
	$(call run-simple-op,SUBI,5,6)
	$(call run-simple-op,MULI,5,6)
	$(call run-simple-op,ADD_ADD,5,6)
	$(call run-simple-op,ADD_SUB,5,6)
	$(call run-simple-op,MUL,5,6)
	$(call run-simple-op,MUL_ADD,5,6)
	$(call run-simple-op,ADD_MUL,5,6)
	$(call run-simple-op,MUL_MUL,5,6)
	$(call run-simple-op,MORPH,5,6)

test-op-release: build-release ## Run a single simple_ops test: make test-op-release OP=ADD A=5 B=6
	$(call set-build-config,Release,build)
	$(call run-simple-op,$(OP),$(A),$(B))

# Negative test for the Niobium hardware parameter checks: the mult example
# uses ring dimension 2048, so running mult_server WITHOUT
# --no-ring-dim-check must abort with the compatibility error.
test-ring-dim-check-release: build-release ## Verify the ring-dimension hardware check rejects incompatible parameters
	$(call set-build-config,Release,build)
	@rm -rf mult_keys mult_server_workload_*
	@echo "=== ring-dim check: mult_server without --no-ring-dim-check must fail ==="
	$(BUILD_DIR)/examples/mult_client mult_keys 7 13
	@out=$$($(BUILD_DIR)/examples/mult_server mult_keys 2>&1); status=$$?; \
	 if [ $$status -eq 0 ]; then \
	     echo "FAIL: mult_server succeeded despite incompatible ring dimension"; exit 1; \
	 fi; \
	 if echo "$$out" | grep -q "Ring dimension 2048 is not compatible with Niobium Hardware."; then \
	     echo "PASS: ring-dim check rejected ring dimension 2048"; \
	 else \
	     echo "FAIL: mult_server failed but without the expected error message:"; \
	     echo "$$out" | tail -5; exit 1; \
	 fi

# ==============================================================================
# test-fhetch-release — delegate to the niobium-fhetch submodule's own
# test-release target. Forwards OPENFHE_INSTALL_DIR so the submodule reuses
# the OpenFHE install produced by this repo's build-openfhe-release rule
# (no second OpenFHE compile). Configures + builds the submodule's own
# build tree (separate from this repo's build/, since the submodule's
# test targets reference $(BUILD_DIR)/… paths relative to its own root).
# ==============================================================================

test-fhetch-release: $(OPENFHE_BUILD_DEP_RELEASE) ## Run the fhetch submodule's test-release + bootstrap roundtrip (simple_fhetch + fhetch_driver + simple_ops roundtrip + bootstrap roundtrip)
	$(MAKE) -C $(FHETCH_DIR) OPENFHE_INSTALL_DIR="$(OPENFHE_INSTALL_DIR)" $(if $(JSON_INCLUDE_DIR),JSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)") EXTERNAL_OPENFHE=$(EXTERNAL_OPENFHE) config-fhetch-release
	$(MAKE) -C $(FHETCH_DIR) OPENFHE_INSTALL_DIR="$(OPENFHE_INSTALL_DIR)" $(if $(JSON_INCLUDE_DIR),JSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)") EXTERNAL_OPENFHE=$(EXTERNAL_OPENFHE) test-release
	$(MAKE) -C $(FHETCH_DIR) OPENFHE_INSTALL_DIR="$(OPENFHE_INSTALL_DIR)" $(if $(JSON_INCLUDE_DIR),JSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)") EXTERNAL_OPENFHE=$(EXTERNAL_OPENFHE) test-roundtrip-bootstrap-release
	$(MAKE) -C $(FHETCH_DIR) OPENFHE_INSTALL_DIR="$(OPENFHE_INSTALL_DIR)" $(if $(JSON_INCLUDE_DIR),JSON_INCLUDE_DIR="$(JSON_INCLUDE_DIR)") EXTERNAL_OPENFHE=$(EXTERNAL_OPENFHE) test-roundtrip-plaintext-add-release

# ==============================================================================
# test-client-release / test-release — Release test aggregates
# ==============================================================================
# test-client-release — client-level tests only; safe for GitHub-hosted runners.
#   - test-simple-ops-release      (13 simple_ops, primary decrypt)
#   - test-mult-release            (CKKS EvalMult, primary decrypt)
#   - test-auto-ciphers-release    (auto-facade, AUTO_OP defaults to ADD)
#   - test-bootstrap-release       (client → server → decrypt)
#   - test-ring-dim-check-release  (hardware param check rejects ring dim 2048)
#
# test-release — full suite (client + fhetch submodule); run on internal server only.
#   Adds via test-fhetch-release:
#   - simple_fhetch                (FHETCH-only example, no OpenFHE)
#   - fhetch_driver                (re-drive a .fhetch through the API)
#   - roundtrip-simple-ops         (13 ops × primary + secondary decrypt)
#   - roundtrip-bootstrap          (CKKS bootstrap × primary + secondary decrypt)
#
# Override AUTO_OP=MUL AUTO_EXPECTED=21 to exercise the known-failing
# relin path inside the auto-facade test.

## Run all client-level Release tests (CI target — no fhetch submodule)
test-client-release: test-simple-ops-release test-mult-release test-auto-ciphers-release test-bootstrap-release test-plaintext-add-release test-ring-dim-check-release

## Run all currently-passing Release tests (client + fhetch submodule) — internal server only, do not run in CI
test-release: test-client-release test-fhetch-release

##@ Cleanup

clean: ## Remove all build artifacts
	-rm -rf build dbuild
	-rm -rf $(OPENFHE_DIR)/build $(OPENFHE_DIR)/dbuild
	-rm -rf bootstrap_keys mult_keys simple_ops_keys plaintext_add_keys
	-rm -rf bootstrap_server_* mult_server_* simple_ops_server_* plaintext_add_server_*

clean-all: clean ## Deep clean including vendor installations
	-rm -rf $(VENDOR_LIB_DIR)

# ==============================================================================
# Capability fragments (one per capability; keep this root file thin)
# ==============================================================================
include make/source-tarball.mk
