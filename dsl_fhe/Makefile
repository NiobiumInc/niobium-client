XCOMP     := xcomp
NBC       := python3 $(XCOMP)/nbc.py
EXAMPLES  := examples
NPROC     := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)

# Root of the niobium-client checkout (holds vendor/lib/openfhe and the built
# libnbfhetch). dsl_fhe lives directly under it.
NIOBIUM_CLIENT_ROOT ?= $(realpath $(CURDIR)/..)

# Self-contained examples (simple, fetch-by-similarity, password-retrieval) have
# no external dependencies — they build and run from this repo alone.
#
# By default the optional ml-inference and fhe-NetworkMonitor examples build
# against vendored STUBS, so they too need no external sources. To build them
# with the REAL model/weights — which ship as a separate submission package, not
# vendored here — point SUBMISSION_REPO at a checkout that provides it:
#   make ml-inference SUBMISSION_REPO=/path/to/submission-repo
SUBMISSION_REPO ?=

.PHONY: test-compiler examples fetch-by-similarity simple fhe-network-monitor ml-inference \
       set-membership test-set-membership \
       password-retrieval test-simple test-fetch test-nid test-ml test-password test-examples \
       clean help

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test-compiler:
	@echo "=== Lexer ==="
	@python3 $(XCOMP)/tests/test_lexer.py
	@echo ""
	@echo "=== Parser ==="
	@python3 $(XCOMP)/tests/test_parser.py
	@echo ""
	@echo "=== Semantic ==="
	@python3 $(XCOMP)/tests/test_semantic.py
	@echo ""
	@echo "=== Codegen ==="
	@python3 $(XCOMP)/tests/test_codegen.py
	@echo ""
	@echo "=== End-to-end: parse examples ==="
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fetch-by-similarity/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fetch-by-similarity/client.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fetch-by-similarity/server.nb  > /dev/null
	@echo "  PASS  parse fetch-by-similarity/*.nb"
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/simple/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/simple/server.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/simple/client.nb  > /dev/null
	@echo "  PASS  parse simple/*.nb"
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fhe-NetworkMonitor/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fhe-NetworkMonitor/client.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/fhe-NetworkMonitor/server.nb  > /dev/null
	@echo "  PASS  parse fhe-NetworkMonitor/*.nb"
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/ml-inference-fhe/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/ml-inference-fhe/client.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/ml-inference-fhe/server.nb  > /dev/null
	@echo "  PASS  parse ml-inference-fhe/*.nb"
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/password-retrieval/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/password-retrieval/client.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/password-retrieval/server.nb  > /dev/null
	@echo "  PASS  parse password-retrieval/*.nb"
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/set-membership/shared.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/set-membership/client.nb  > /dev/null
	@cd $(XCOMP) && python3 nbc.py parse ../$(EXAMPLES)/set-membership/server.nb  > /dev/null
	@echo "  PASS  parse set-membership/*.nb"
	@echo ""
	@echo "=== End-to-end: semantic check ==="
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/fetch-by-similarity/shared.nb \
		../$(EXAMPLES)/fetch-by-similarity/client.nb \
		../$(EXAMPLES)/fetch-by-similarity/server.nb
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/simple/shared.nb \
		../$(EXAMPLES)/simple/client.nb \
		../$(EXAMPLES)/simple/server.nb
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/fhe-NetworkMonitor/shared.nb \
		../$(EXAMPLES)/fhe-NetworkMonitor/client.nb \
		../$(EXAMPLES)/fhe-NetworkMonitor/server.nb
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/ml-inference-fhe/shared.nb \
		../$(EXAMPLES)/ml-inference-fhe/client.nb \
		../$(EXAMPLES)/ml-inference-fhe/server.nb
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/password-retrieval/shared.nb \
		../$(EXAMPLES)/password-retrieval/client.nb \
		../$(EXAMPLES)/password-retrieval/server.nb
	@cd $(XCOMP) && python3 nbc.py check \
		../$(EXAMPLES)/set-membership/shared.nb \
		../$(EXAMPLES)/set-membership/client.nb \
		../$(EXAMPLES)/set-membership/server.nb
	@echo ""
	@echo "All tests passed."

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------

# Default build: all examples are self-contained (no external-repo dependency).
# ml-inference and fhe-NetworkMonitor build against stub model/assets — they run
# but don't produce real results (real model/weights need the submission; see
# each example's data/README.md). test-examples runs only the examples that
# produce meaningful, verifiable output.
examples: simple fetch-by-similarity ml-inference fhe-network-monitor password-retrieval set-membership test-examples

# --- fetch-by-similarity ---

# Support libraries are vendored locally under examples/fetch-by-similarity/support
# (relicensed under the client's Apache-2.0 header) — no external repo needed.
FETCH_SUPPORT := $(EXAMPLES)/fetch-by-similarity/support
FETCH_NB_OUT  := $(EXAMPLES)/fetch-by-similarity/nb_out

fetch-by-similarity:
	@echo "=== Compiling fetch-by-similarity (DSL → C++) ==="
	@mkdir -p $(FETCH_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/fetch-by-similarity/shared.nb \
		../$(EXAMPLES)/fetch-by-similarity/client.nb \
		../$(EXAMPLES)/fetch-by-similarity/server.nb \
		--outdir ../$(FETCH_NB_OUT)
	@echo ""
	@echo "=== Staging local support libraries ==="
	@cp $(FETCH_SUPPORT)/slot_replication.h   $(FETCH_NB_OUT)/
	@cp $(FETCH_SUPPORT)/slot_replication.cpp $(FETCH_NB_OUT)/
	@cp $(FETCH_SUPPORT)/running_sums.h       $(FETCH_NB_OUT)/
	@cp $(FETCH_SUPPORT)/running_sums.cpp     $(FETCH_NB_OUT)/
	@cp $(FETCH_SUPPORT)/utils.h              $(FETCH_NB_OUT)/
	@echo ""
	@echo "=== Building fetch-by-similarity (C++ → binaries) ==="
	@mkdir -p $(FETCH_NB_OUT)/build
	@cd $(FETCH_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(FETCH_NB_OUT)/build/"

# --- simple ---

SIMPLE_NB_OUT := $(EXAMPLES)/simple/nb_out

simple:
	@echo "=== Compiling simple (DSL → C++) ==="
	@mkdir -p $(SIMPLE_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/simple/shared.nb \
		../$(EXAMPLES)/simple/client.nb \
		../$(EXAMPLES)/simple/server.nb \
		--outdir ../$(SIMPLE_NB_OUT)
	@echo ""
	@echo "=== Building simple (C++ → binaries) ==="
	@mkdir -p $(SIMPLE_NB_OUT)/build
	@cd $(SIMPLE_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(SIMPLE_NB_OUT)/build/"

# --- fhe-NetworkMonitor (KitNET anomaly detection) ---
# Self-contained by default: the build needs no external sources, and STUB
# model/dataset assets are generated so it runs end-to-end (not real detection;
# see examples/fhe-NetworkMonitor/assets/README.md). For real detection, set
# SUBMISSION_REPO to a checkout shipping the trained KitNET model + Mirai
# dataset; its assets are then used instead of the stubs.

NID_SUBMISSION := $(SUBMISSION_REPO)/examples/fhe-NetworkMonitor
NID_NB_OUT     := $(EXAMPLES)/fhe-NetworkMonitor/nb_out
NID_ASSETS     := $(CURDIR)/$(EXAMPLES)/fhe-NetworkMonitor/assets

fhe-network-monitor:
	@echo "=== Compiling fhe-NetworkMonitor (DSL → C++) ==="
	@mkdir -p $(NID_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/fhe-NetworkMonitor/shared.nb \
		../$(EXAMPLES)/fhe-NetworkMonitor/client.nb \
		../$(EXAMPLES)/fhe-NetworkMonitor/server.nb \
		--outdir ../$(NID_NB_OUT)
	@echo ""
ifeq ($(SUBMISSION_REPO),)
	@echo "=== Generating stub KitNET model + dataset (self-contained build) ==="
	@python3 $(EXAMPLES)/fhe-NetworkMonitor/assets/make_stub_assets.py TOY
else
	@echo "=== Staging real KitNET assets from submission ==="
	@mkdir -p $(NID_ASSETS)/models $(NID_ASSETS)/datasets
	@cp -r $(NID_SUBMISSION)/assets/models/.    $(NID_ASSETS)/models/    2>/dev/null || true
	@cp -r $(NID_SUBMISSION)/assets/datasets/.  $(NID_ASSETS)/datasets/  2>/dev/null || true
endif
	@echo ""
	@echo "=== Building fhe-NetworkMonitor (C++ → binaries) ==="
	@mkdir -p $(NID_NB_OUT)/build
	@cd $(NID_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(NID_NB_OUT)/build/"

# --- ml-inference-fhe ---
# Self-contained by default: builds against a STUB MLP model (mlp_openfhe.{h,cpp})
# and stub zero-weights, so no external/private repo is needed. The output is not
# real inference (see examples/ml-inference-fhe/data/README.md). For real results,
# set SUBMISSION_REPO to a checkout that ships the HEIR model + trained
# weights; its submission sources/weights then take precedence over the stub.

ML_SUBMISSION := $(SUBMISSION_REPO)/examples/ml-inference-fhe/submission
ML_NB_OUT     := $(EXAMPLES)/ml-inference-fhe/nb_out
ML_LOCAL_SRCS := $(realpath $(CURDIR)/$(EXAMPLES)/ml-inference-fhe)
ML_DATA_DIR   := $(CURDIR)/$(EXAMPLES)/ml-inference-fhe/data

ml-inference:
	@echo "=== Compiling ml-inference-fhe (DSL → C++) ==="
	@mkdir -p $(ML_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/ml-inference-fhe/shared.nb \
		../$(EXAMPLES)/ml-inference-fhe/client.nb \
		../$(EXAMPLES)/ml-inference-fhe/server.nb \
		--outdir ../$(ML_NB_OUT)
	@echo ""
ifeq ($(SUBMISSION_REPO),)
	@echo "=== Generating stub MLP weights (self-contained build) ==="
	@python3 $(EXAMPLES)/ml-inference-fhe/data/make_stub_weights.py $(ML_DATA_DIR)
endif
	@echo ""
	@echo "=== Building ml-inference-fhe (C++ → binaries) ==="
	@mkdir -p $(ML_NB_OUT)/build
	@cd $(ML_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) \
		         -DLOCAL_SRC_DIR=$(ML_LOCAL_SRCS) \
		         $(if $(SUBMISSION_REPO),-DSUBMISSION_DIR=$(ML_SUBMISSION),) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(ML_NB_OUT)/build/"

# --- password-retrieval ---

PWD_NB_OUT := $(EXAMPLES)/password-retrieval/nb_out

# --- set-membership (private name matching) ---

SETM_NB_OUT := $(EXAMPLES)/set-membership/nb_out

set-membership:
	@echo "=== Compiling set-membership (DSL → C++) ==="
	@mkdir -p $(SETM_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/set-membership/shared.nb \
		../$(EXAMPLES)/set-membership/client.nb \
		../$(EXAMPLES)/set-membership/server.nb \
		--outdir ../$(SETM_NB_OUT)
	@echo ""
	@echo "=== Building set-membership (C++ → binaries) ==="
	@mkdir -p $(SETM_NB_OUT)/build
	@cd $(SETM_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(SETM_NB_OUT)/build/"

password-retrieval:
	@echo "=== Compiling password-retrieval (DSL → C++) ==="
	@mkdir -p $(PWD_NB_OUT)
	@cd $(XCOMP) && python3 nbc.py compile \
		../$(EXAMPLES)/password-retrieval/shared.nb \
		../$(EXAMPLES)/password-retrieval/client.nb \
		../$(EXAMPLES)/password-retrieval/server.nb \
		--outdir ../$(PWD_NB_OUT)
	@echo ""
	@echo "=== Building password-retrieval (C++ → binaries) ==="
	@mkdir -p $(PWD_NB_OUT)/build
	@cd $(PWD_NB_OUT)/build && \
		cmake .. -DNIOBIUM_CLIENT_ROOT=$(NIOBIUM_CLIENT_ROOT) && \
		$(MAKE) -j$(NPROC) 2>&1
	@echo ""
	@echo "Built binaries in $(PWD_NB_OUT)/build/"

# ---------------------------------------------------------------------------
# End-to-end tests (run the built binaries)
# ---------------------------------------------------------------------------

LD_LIB_PATH := $(NIOBIUM_CLIENT_ROOT)/vendor/lib/openfhe/lib:$(NIOBIUM_CLIENT_ROOT)/build/vendor/niobium-fhetch:$(NIOBIUM_CLIENT_ROOT)/vendor/niobium-fhetch/build:$(NIOBIUM_CLIENT_ROOT)/build/_deps/yaml_cpp-build

# @hardware server stages replay via the cooperative path, which spawns the
# fhetch_driver helper. It's built alongside niobium-fhetch's tests. Export
# NBCC_FHETCH_DRIVER so the client library finds it without relying on PATH.
NBCC_FHETCH_DRIVER ?= $(NIOBIUM_CLIENT_ROOT)/vendor/niobium-fhetch/build/tests/fhetch_driver/fhetch_driver
export NBCC_FHETCH_DRIVER

test-simple: simple
	@echo ""
	@echo "=== Testing simple (end-to-end) ==="
	@cd $(SIMPLE_NB_OUT)/build && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		rm -rf io && \
		echo "  key_generation..." && ./key_generation 0 3 && \
		echo "  encrypt (a=3, b=5)..." && ./encrypt 0 3.0 5.0 && \
		echo "  ADD (3+5=8)..." && ./compute 0 0 0.0 && ./decrypt_verify 0 8.0 && \
		echo "  PASS  ADD" && \
		echo "  MUL (3*5=15)..." && ./compute 0 1 0.0 && ./decrypt_verify 0 15.0 && \
		echo "  PASS  MUL" && \
		echo "  ADDI (3+2=5)..." && ./compute 0 15 2.0 && ./decrypt_verify 0 5.0 && \
		echo "  PASS  ADDI" && \
		echo "  SUBI (3-2=1)..." && ./compute 0 16 2.0 && ./decrypt_verify 0 1.0 && \
		echo "  PASS  SUBI" && \
		echo "  MULI (3*2=6)..." && ./compute 0 17 2.0 && ./decrypt_verify 0 6.0 && \
		echo "  PASS  MULI" && \
		echo "  MUL_MUL ((3*5)*3=45)..." && ./compute 0 2 0.0 && ./decrypt_verify 0 45.0 && \
		echo "  PASS  MUL_MUL" && \
		echo "  ADD_MUL ((3+5)*3=24)..." && ./compute 0 3 0.0 && ./decrypt_verify 0 24.0 && \
		echo "  PASS  ADD_MUL" && \
		echo "  ADD_ADD ((3+5)+3=11)..." && ./compute 0 5 0.0 && ./decrypt_verify 0 11.0 && \
		echo "  PASS  ADD_ADD" && \
		rm -rf io && \
		echo "" && echo "All simple tests passed."

test-fetch: fetch-by-similarity
	@echo ""
	@echo "=== Testing fetch-by-similarity (end-to-end, toy instance) ==="
	@cd $(EXAMPLES)/fetch-by-similarity && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		rm -rf *_workload_* fhetch_driver_source_* && \
		python3 harness/run.py 0 --seed 42 && \
		echo "" && \
		echo "=== Replay run (cache hit — zero FHE ops, FHETCH simulator) ===" && \
		nb_out/build/encrypted_compute 0 && \
		nb_out/build/decrypt_decode 0 && \
		nb_out/build/postprocess 0 && \
		python3 harness/verify_result.py datasets/toy/expected.bin io/toy/results.bin && \
		echo "  PASS  replay run verified against expected payloads"
	@echo ""
	@echo "All fetch-by-similarity tests passed."

test-nid: fhe-network-monitor
	@echo ""
	@echo "=== Testing fhe-NetworkMonitor (build + keygen + encrypt + inference, toy) ==="
	@cd $(NID_NB_OUT)/build && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		test -L assets || ln -sf ../../assets assets && \
		echo "  keygen..." && ./keygen 0 && \
		echo "  encrypt..." && ./encrypt 0 && \
		echo "  inference..." && ./inference 0 && \
		echo "  PASS  keygen+encrypt+inference (Toy)"
	@echo ""
	@echo "All fhe-NetworkMonitor tests passed."

test-ml: ml-inference
	@echo ""
	@echo "=== Testing ml-inference-fhe (build + keygen + encrypt + compute, single) ==="
	@cd $(ML_NB_OUT)/build && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		export ML_WEIGHT_DIR=$(if $(SUBMISSION_REPO),$(ML_SUBMISSION)/data,$(ML_DATA_DIR)) && \
		mkdir -p datasets/single/intermediate && \
		test -s datasets/single/intermediate/test_pixels.txt || \
			python3 -c "print(' '.join(['0.000000']*784))" > datasets/single/intermediate/test_pixels.txt && \
		echo "  keygen..." && ./key_generation 0 && \
		echo "  encrypt..." && ./encode_encrypt_input 0 && \
		echo "  compute..." && ./encrypted_compute 0 0 && \
		echo "  PASS  keygen+encrypt+compute (Single)"
	@echo ""
	@echo "All ml-inference-fhe tests passed."

test-password: password-retrieval
	@echo ""
	@echo "=== Testing password-retrieval (end-to-end) ==="
	@cd $(PWD_NB_OUT)/build && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		rm -rf io && \
		echo "  keygen..." && ./key_generation 0 && \
		echo "  setup record (answers: 7,3,9,1,5 pw: 42)..." && ./setup_record 0 && \
		echo "  submit correct answers (7,3,9,1,5)..." && ./submit_query 0 && \
		echo "  verify..." && ./verify 0 && \
		echo "  decrypt (expect ≈ 42)..." && ./decrypt_result 0 42.0 && \
		echo "  PASS  correct answers → password retrieved" && \
		echo "  submit wrong answers (7,3,9,1,8)..." && ./submit_query 0 7.0 3.0 9.0 1.0 8.0 && \
		echo "  verify..." && ./verify 0 && \
		echo "  decrypt (expect ≈ 0)..." && ./decrypt_result 0 0.0 && \
		echo "  PASS  wrong answers → password hidden" && \
		echo "  cleartext reference pipeline (generated ground truth)..." && \
		rm -rf io && \
		./setup_record_ref 0 && ./submit_query_ref 0 && ./verify_ref 0 && \
		./decrypt_result_ref 0 42.0 && \
		echo "  PASS  reference pipeline → exact 42" && \
		rm -rf io && \
		echo "" && echo "All password-retrieval tests passed."

test-set-membership: set-membership
	@echo ""
	@echo "=== Testing set-membership (end-to-end) ==="
	@cd $(EXAMPLES)/set-membership && \
		export LD_LIBRARY_PATH=$(LD_LIB_PATH) && \
		rm -rf io *_workload_* fhetch_driver_source_* && \
		B=nb_out/build && \
		echo "  exact: query 'James Smith' (in dataset)..." && \
		python3 harness/encode_names.py 0 --query "James Smith" && \
		$$B/key_generation 0 && $$B/encrypt_query 0 && $$B/compute 0 && \
		$$B/decrypt_verify 0 1.0 && \
		echo "  PASS  exact match → score ≈ 1" && \
		echo "  exact: query 'Zelda Quixote' (not in dataset)..." && \
		rm -rf *_workload_* fhetch_driver_source_* && \
		python3 harness/encode_names.py 0 --query "Zelda Quixote" && \
		$$B/encrypt_query 0 && $$B/compute 0 && \
		$$B/decrypt_verify 0 0.0 && \
		echo "  PASS  no match → score ≈ 0" && \
		echo "  soundex: fuzzy query 'Robbrt Johnson' (10 'Robert *' collisions)..." && \
		rm -rf *_workload_* fhetch_driver_source_* && \
		python3 harness/encode_names.py 1 --query "Robbrt Johnson" && \
		$$B/key_generation 1 && $$B/encrypt_query 1 && $$B/compute 1 && \
		$$B/decrypt_verify 1 10.0 && \
		echo "  PASS  soundex fuzzy match → score ≈ 10" && \
		echo "  cleartext reference pipeline (generated ground truth)..." && \
		$$B/encrypt_query_ref 1 && $$B/compute_ref 1 && $$B/decrypt_verify_ref 1 10.0 && \
		echo "  PASS  reference pipeline agrees (score ≈ 10)" && \
		rm -rf io *_workload_* fhetch_driver_source_* && \
		echo "" && echo "All set-membership tests passed."

test-examples: test-simple test-fetch test-password test-set-membership
	@echo ""
	@echo "All example tests passed."

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

clean:
	rm -rf $(XCOMP)/nb_out $(XCOMP)/__pycache__ $(XCOMP)/tests/__pycache__
	rm -rf $(FETCH_NB_OUT)/build
	cd $(FETCH_NB_OUT) && \
		find . -maxdepth 1 -type f ! -name utils.h ! -name .gitignore -delete 2>/dev/null; true
	rm -rf $(SIMPLE_NB_OUT)
	rm -rf $(SETM_NB_OUT) $(EXAMPLES)/set-membership/io
	rm -rf $(NID_NB_OUT)/build
	cd $(NID_NB_OUT) && \
		find . -maxdepth 1 -type f ! -name .gitignore -delete 2>/dev/null; true
	rm -rf $(ML_NB_OUT)/build
	cd $(ML_NB_OUT) && \
		find . -maxdepth 1 -type f ! -name .gitignore -delete 2>/dev/null; true
	rm -rf $(PWD_NB_OUT)/build
	cd $(PWD_NB_OUT) && \
		find . -maxdepth 1 -type f ! -name .gitignore -delete 2>/dev/null; true

help:
	@echo "Targets:"
	@echo "  test-compiler        Run all unit tests + end-to-end checks"
	@echo "  examples             Build all examples and run tests"
	@echo "  simple               Build the simple cipher-ops example"
	@echo "  fetch-by-similarity  Build the fetch-by-similarity example"
	@echo "  fhe-network-monitor  Build the KitNET anomaly detection example"
	@echo "  ml-inference         Build the ML inference example"
	@echo "  password-retrieval   Build the password retrieval example"
	@echo "  test-simple          Run simple example end-to-end tests"
	@echo "  test-fetch           Run fetch-by-similarity end-to-end (toy)"
	@echo "  test-nid             Run fhe-NetworkMonitor end-to-end (toy)"
	@echo "  test-ml              Run ml-inference-fhe end-to-end (single)"
	@echo "  test-password        Run password-retrieval end-to-end (toy)"
	@echo "  test-examples        Run all example end-to-end tests"
	@echo "  clean                Remove generated files"
	@echo "  help                 Show this message"
