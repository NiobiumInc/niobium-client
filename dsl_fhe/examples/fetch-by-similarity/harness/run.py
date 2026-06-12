#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
run.py — Harness for the DSL-generated fetch-by-similarity pipeline (monolithic).

Self-contained: dataset/query generation, the cleartext reference, the verifier,
and instance parameters all live in this directory (see params.py,
generate_dataset.py, generate_query.py, cleartext_impl.py, verify_result.py).

Usage:
    python3 run.py <size> [--seed SEED] [--count_only]
                   [--skip-data] [--skip-keys] [--skip-encrypt]

Instance sizes: 0=toy, 1=small, 2=medium, 3=large, 4=toy_large_ring
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths — everything is local to this example (no external repo dependency).
# ---------------------------------------------------------------------------
HARNESS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS_DIR))

from params import InstanceParams, instance_name  # noqa: E402

EXAMPLE_ROOT = HARNESS_DIR.parent          # examples/fetch-by-similarity
BUILD_DIR = EXAMPLE_ROOT / "nb_out" / "build"
# The root() function in nb_shared.h returns cwd, so we run from EXAMPLE_ROOT
RUN_CWD = EXAMPLE_ROOT


def run_stage(name: str, cmd: list, cwd=None):
    """Run a pipeline stage, print timing, abort on failure."""
    cwd = cwd or RUN_CWD
    print(f"  [{name}]  {' '.join(str(c) for c in cmd)}")
    t0 = time.time()
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    elapsed = time.time() - t0
    if result.returncode != 0:
        print(f"  FAIL ({elapsed:.1f}s)")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)
        sys.exit(1)
    print(f"  OK   ({elapsed:.1f}s)")
    if result.stdout.strip():
        for line in result.stdout.strip().split("\n"):
            print(f"         {line}")
    return elapsed


def main():
    parser = argparse.ArgumentParser(
        description="Run the DSL-generated fetch-by-similarity pipeline (monolithic)")
    parser.add_argument("size", type=int, choices=range(5),
                        help="Instance size (0=toy .. 4=toy_large_ring)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for dataset/query generation")
    parser.add_argument("--count_only", action="store_true",
                        help="Only count matches (skip payload extraction)")
    parser.add_argument("--skip-data", action="store_true",
                        help="Skip dataset/query generation (reuse existing)")
    parser.add_argument("--skip-keys", action="store_true",
                        help="Skip key generation (reuse existing)")
    parser.add_argument("--skip-encrypt", action="store_true",
                        help="Skip encryption stages (reuse existing)")
    parser.add_argument("--no-ring-dim-check", action="store_true",
                        help="Skip the Niobium hardware ring-dimension check "
                             "(forwarded to encrypted_compute)")
    args = parser.parse_args()

    params = InstanceParams(args.size, rootdir=RUN_CWD)
    size_name = instance_name(args.size)
    count_flag = ["--count_only"] if args.count_only else []
    compute_flags = ["--no-ring-dim-check"] if args.no_ring_dim_check else []

    print(f"=== Fetch-by-similarity: {size_name} (size={args.size}) ===")
    print(f"    Build dir: {BUILD_DIR}")
    print(f"    Run CWD:   {RUN_CWD}")
    print()

    # Verify binaries exist
    required_bins = ["key_generation", "encode_encrypt_db",
                     "encode_encrypt_query", "encrypted_compute",
                     "decrypt_decode", "postprocess"]
    for b in required_bins:
        if not (BUILD_DIR / b).exists():
            print(f"ERROR: Binary not found: {BUILD_DIR / b}")
            print("       Run 'make fetch-by-similarity' first.")
            sys.exit(1)

    timings = {}
    total_t0 = time.time()

    # ------------------------------------------------------------------
    # Step 0: Create directories
    # ------------------------------------------------------------------
    print("--- Step 0: Initialize directories ---")
    (RUN_CWD / "datasets" / size_name).mkdir(parents=True, exist_ok=True)
    (RUN_CWD / "io" / size_name / "keys").mkdir(parents=True, exist_ok=True)
    (RUN_CWD / "io" / size_name / "encrypted").mkdir(parents=True, exist_ok=True)
    print("  OK")
    print()

    # ------------------------------------------------------------------
    # Step 1: Generate dataset
    # ------------------------------------------------------------------
    if not args.skip_data:
        print("--- Step 1: Generate dataset ---")
        timings["generate_dataset"] = run_stage(
            "generate_dataset",
            [sys.executable, str(HARNESS_DIR / "generate_dataset.py"),
             str(args.size), "--seed", str(args.seed)],
            cwd=str(RUN_CWD))
        print()

    # ------------------------------------------------------------------
    # Step 2: Generate query
    # ------------------------------------------------------------------
    if not args.skip_data:
        print("--- Step 2: Generate query ---")
        timings["generate_query"] = run_stage(
            "generate_query",
            [sys.executable, str(HARNESS_DIR / "generate_query.py"),
             str(args.size), "--seed", str(args.seed)],
            cwd=str(RUN_CWD))
        print()

    # ------------------------------------------------------------------
    # Step 3: Key generation
    # ------------------------------------------------------------------
    if not args.skip_keys:
        print("--- Step 3: Key generation ---")
        timings["key_generation"] = run_stage(
            "key_generation",
            [str(BUILD_DIR / "key_generation"), str(args.size)] + count_flag)
        print()

    # ------------------------------------------------------------------
    # Step 4: Encrypt database
    # ------------------------------------------------------------------
    if not args.skip_encrypt:
        print("--- Step 4: Encrypt database ---")
        timings["encode_encrypt_db"] = run_stage(
            "encode_encrypt_db",
            [str(BUILD_DIR / "encode_encrypt_db"), str(args.size)])
        print()

    # ------------------------------------------------------------------
    # Step 5: Encrypt query
    # ------------------------------------------------------------------
    if not args.skip_encrypt:
        print("--- Step 5: Encrypt query ---")
        timings["encode_encrypt_query"] = run_stage(
            "encode_encrypt_query",
            [str(BUILD_DIR / "encode_encrypt_query"), str(args.size)])
        print()

    # ------------------------------------------------------------------
    # Step 6: Server encrypted computation (monolithic)
    # ------------------------------------------------------------------
    print("--- Step 6: Encrypted computation (monolithic) ---")
    timings["encrypted_compute"] = run_stage(
        "encrypted_compute",
        [str(BUILD_DIR / "encrypted_compute"), str(args.size)]
        + count_flag + compute_flags)
    print()

    # ------------------------------------------------------------------
    # Step 7: Decrypt
    # ------------------------------------------------------------------
    print("--- Step 7: Decrypt ---")
    timings["decrypt_decode"] = run_stage(
        "decrypt_decode",
        [str(BUILD_DIR / "decrypt_decode"), str(args.size)])
    print()

    # ------------------------------------------------------------------
    # Step 8: Postprocess
    # ------------------------------------------------------------------
    print("--- Step 8: Postprocess ---")
    timings["postprocess"] = run_stage(
        "postprocess",
        [str(BUILD_DIR / "postprocess"), str(args.size)] + count_flag)
    print()

    # ------------------------------------------------------------------
    # Step 9: Cleartext reference + verification
    # ------------------------------------------------------------------
    print("--- Step 9: Verify ---")
    run_stage(
        "cleartext_impl",
        [sys.executable, str(HARNESS_DIR / "cleartext_impl.py"),
         str(args.size)] + count_flag,
        cwd=str(RUN_CWD))

    dataset_dir = RUN_CWD / "datasets" / size_name
    io_dir = RUN_CWD / "io" / size_name
    if args.count_only:
        # DSL writes int16_t count; original verifier expects int64.
        # Do the comparison inline with the correct dtype.
        import numpy as np
        expected = np.fromfile(str(dataset_dir / "expected.bin"), dtype=np.int_)
        result_data = np.fromfile(str(io_dir / "results.bin"), dtype=np.int16)
        if np.array_equal(expected, result_data.astype(np.int_)):
            print(f"  [harness] PASS (result={result_data})")
        else:
            print(f"  [harness] FAIL (expected {expected} but found {result_data})")
            sys.exit(1)
    else:
        result = subprocess.run(
            [sys.executable, str(HARNESS_DIR / "verify_result.py"),
             str(dataset_dir / "expected.bin"),
             str(io_dir / "results.bin")],
            cwd=str(RUN_CWD),
            capture_output=True, text=True)
        if result.stdout.strip():
            print(f"  {result.stdout.strip()}")
        if result.returncode != 0:
            print("  VERIFICATION FAILED")
            if result.stderr.strip():
                print(result.stderr.strip())
            sys.exit(1)
    print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_elapsed = time.time() - total_t0
    print("=== Timing Summary ===")
    for stage, t in timings.items():
        print(f"  {stage:30s}  {t:8.2f}s")
    print(f"  {'TOTAL':30s}  {total_elapsed:8.2f}s")

    # Report file sizes
    print()
    print("=== File Sizes ===")
    key_dir = io_dir / "keys"
    enc_dir = io_dir / "encrypted"
    if key_dir.exists():
        key_size = sum(f.stat().st_size for f in key_dir.rglob("*") if f.is_file())
        print(f"  Keys:            {key_size / 1e6:8.1f} MB")
    if enc_dir.exists():
        enc_size = sum(f.stat().st_size for f in enc_dir.rglob("*") if f.is_file())
        print(f"  Encrypted data:  {enc_size / 1e6:8.1f} MB")


if __name__ == "__main__":
    main()
