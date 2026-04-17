# ==============================================================================
# Niobium Client — Build System
# ==============================================================================
# Convention: dbuild/ for Debug, build/ for Release (matches niobium-compiler)
#
# Quick start:
#   make sync             # One-time: sync OpenFHE submodule
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

OPENFHE_DIR          := $(VENDOR_DIR)/openfhe
OPENFHE_INSTALL_DIR  := $(VENDOR_LIB_DIR)/openfhe
CLIENT_INSTALL_DIR   := $(VENDOR_LIB_DIR)/niobium-client

# OpenMP toggle (OFF by default, override with: make config-openfhe OPENMP=ON)
OPENMP ?= OFF

# Native CPU optimizations (OFF by default for portability)
NATIVEOPT ?= OFF

# ==============================================================================
# Targets
# ==============================================================================

.PHONY: help sync config config-release build build-release release \
        config-openfhe config-openfhe-release build-openfhe build-openfhe-release \
        config-client config-client-release \
        install install-release clean clean-all

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

sync: sync-openfhe sync-json ## Sync all submodules to pinned commits

sync-openfhe: ## Sync OpenFHE submodule to pinned commit
	git submodule update --init --recursive vendor/openfhe

sync-json: ## Sync nlohmann/json submodule to pinned commit
	git submodule update --init vendor/json

update-openfhe: ## Update OpenFHE submodule to latest remote commit
	cd $(OPENFHE_DIR) && git fetch origin && git checkout nb_main && git pull origin nb_main

##@ OpenFHE Build

config-openfhe: ## Configure OpenFHE (Debug mode)
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

config-openfhe-release: ## Configure OpenFHE (Release mode)
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

build-openfhe: ## Build and install OpenFHE (Debug mode)
	$(call set-build-config,Debug,dbuild)
	cd $(OPENFHE_DIR) && \
		cmake --build dbuild -j $(NUM_CPUS) --target install --config Debug

build-openfhe-release: ## Build and install OpenFHE (Release mode)
	$(call set-build-config,Release,build)
	cd $(OPENFHE_DIR) && \
		cmake --build build -j $(NUM_CPUS) --target install --config Release

##@ Client Build

config-client: ## Configure the client library (Debug mode, requires OpenFHE built)
	$(call set-build-config,Debug,dbuild)
	cmake -S $(CURDIR) -B $(CURDIR)/dbuild \
		-DCMAKE_BUILD_TYPE=Debug \
		-DOPENFHE_INSTALL_DIR=$(OPENFHE_INSTALL_DIR) \
		-DNIOBIUM_CLIENT_WITH_EXAMPLES=ON \
		-DCMAKE_INSTALL_PREFIX=$(CLIENT_INSTALL_DIR)

config-client-release: ## Configure the client library (Release mode, requires OpenFHE built)
	$(call set-build-config,Release,build)
	cmake -S $(CURDIR) -B $(CURDIR)/build \
		-DCMAKE_BUILD_TYPE=Release \
		-DOPENFHE_INSTALL_DIR=$(OPENFHE_INSTALL_DIR) \
		-DNIOBIUM_CLIENT_WITH_EXAMPLES=ON \
		-DCMAKE_INSTALL_PREFIX=$(CLIENT_INSTALL_DIR)

##@ Combined Targets

config: config-openfhe config-client ## Configure everything (Debug mode)

config-release: config-openfhe-release config-client-release ## Configure everything (Release mode)

build: build-openfhe ## Build everything (Debug mode)
	$(call set-build-config,Debug,dbuild)
	cmake --build dbuild -j $(NUM_CPUS) --config Debug

build-release: build-openfhe-release ## Build everything (Release mode)
	$(call set-build-config,Release,build)
	cmake --build build -j $(NUM_CPUS) --config Release

release: config-release build-release ## Shortcut: configure + build everything (Release)

##@ Installation

install: ## Install the client library (Debug mode)
	$(call set-build-config,Debug,dbuild)
	cmake --install dbuild

install-release: ## Install the client library (Release mode)
	$(call set-build-config,Release,build)
	cmake --install build

##@ Testing

test-bootstrap: build ## Run the bootstrap example: client → server → decrypt (Debug)
	$(call set-build-config,Debug,dbuild)
	@rm -rf bootstrap_keys bootstrap_server_*
	@echo "=== Running bootstrap client ==="
	$(BUILD_DIR)/examples/bootstrap_client bootstrap_keys
	@echo ""
	@echo "=== Running bootstrap server ==="
	$(BUILD_DIR)/examples/bootstrap_server bootstrap_keys
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
	$(BUILD_DIR)/examples/bootstrap_server bootstrap_keys
	@echo ""
	@echo "=== Running bootstrap decrypt ==="
	$(BUILD_DIR)/examples/bootstrap_decrypt bootstrap_keys

test-mult: build ## Run the multiply example: client → server → decrypt (Debug)
	$(call set-build-config,Debug,dbuild)
	@rm -rf mult_keys mult_server_workload_*
	@echo "=== Running mult client ==="
	$(BUILD_DIR)/examples/mult_client mult_keys 7 13
	@echo ""
	@echo "=== Running mult server ==="
	$(BUILD_DIR)/examples/mult_server mult_keys
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
	$(BUILD_DIR)/examples/mult_server mult_keys
	@echo ""
	@echo "=== Running mult decrypt ==="
	$(BUILD_DIR)/examples/mult_decrypt mult_keys

test-sim-mult: test-mult ## Record mult trace then simulate it (Debug)
	$(call set-build-config,Debug,dbuild)
	@echo ""
	@echo "=== Running FHETCH simulator on mult trace ==="
	$(BUILD_DIR)/fhetch_sim mult_server_workload_bfv_mult/mult_server_workload_bfv_mult.fhetch --ring-dim 8192

test-sim-mult-release: test-mult-release ## Record mult trace then simulate it (Release)
	$(call set-build-config,Release,build)
	@echo ""
	@echo "=== Running FHETCH simulator on mult trace ==="
	$(BUILD_DIR)/fhetch_sim mult_server_workload_bfv_mult/mult_server_workload_bfv_mult.fhetch --ring-dim 8192

test-sim-bootstrap-release: test-bootstrap-release ## Record bootstrap trace then simulate it (Release)
	$(call set-build-config,Release,build)
	@echo ""
	@echo "=== Running FHETCH simulator on bootstrap trace ==="
	$(BUILD_DIR)/fhetch_sim bootstrap_server_workload_ckks_bootstrap/bootstrap_server_workload_ckks_bootstrap.fhetch --ring-dim 2048

# Helper: run a single simple_ops test
define run-simple-op
	@echo "=== Testing $(1): $(2) ==="
	@rm -rf simple_ops_keys simple_ops_server_*
	@$(BUILD_DIR)/examples/simple_ops_client simple_ops_keys $(2) $(3) 2>&1 | tail -1
	@$(BUILD_DIR)/examples/simple_ops_server simple_ops_keys $(1) 2>&1 | grep -E "Live-in|Complete|ERROR"
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

##@ Cleanup

clean: ## Remove all build artifacts
	-rm -rf build dbuild
	-rm -rf $(OPENFHE_DIR)/build $(OPENFHE_DIR)/dbuild
	-rm -rf bootstrap_keys mult_keys simple_ops_keys
	-rm -rf bootstrap_server_* mult_server_* simple_ops_server_*

clean-all: clean ## Deep clean including vendor installations
	-rm -rf $(VENDOR_LIB_DIR)
