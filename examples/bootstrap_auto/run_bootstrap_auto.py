#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
# Licensed under the Apache License, Version 2.0.
"""
run_bootstrap_auto.py — End-to-end driver for the auto-facade bootstrap example.

Exercises the "no user code changes" flow for Niobium instrumentation:

  1. bootstrap_client  — plain OpenFHE keygen + encrypts a test vector.
  2. bootstrap_auto    — plain OpenFHE bootstrapping. No niobium:: calls
                         anywhere; the auto-facade (enabled by the public
                         OPENFHE_CPROBES compile flag that libnbfhetch sets)
                         records the instruction trace on the first run.
  3. bootstrap_auto    — re-runs the same binary. On the second invocation the
                         cache is hit: recording is skipped and the replay
                         pipeline populates the result ciphertext.

The program itself returns [PASS] / [FAIL] based on a plaintext comparison
against the expected vector {0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0}.

Ported from niobium-compiler/run_scripts/bootstrap_auto.sh — expressed in
Python so the same script can run on Linux and macOS without shell-quoting
differences, and so it can set the platform-appropriate library search path
for the vendored OpenFHE shared libs.

Usage:
    python3 examples/bootstrap_auto/run_bootstrap_auto.py
        [--build-dir build]
        [--key-dir bootstrap_auto_keys]
        [--openfhe-lib vendor/lib/openfhe/lib]
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def setup_library_paths(env: dict[str, str], lib_dir: Path) -> None:
    """Point the dynamic linker at the OpenFHE shared libs bundled with the
    niobium-fhetch submodule install tree."""
    if platform.system() == "Darwin":
        var = "DYLD_LIBRARY_PATH"
    else:
        var = "LD_LIBRARY_PATH"
    existing = env.get(var, "")
    env[var] = f"{lib_dir}{os.pathsep}{existing}" if existing else str(lib_dir)


def run(cmd: Sequence[str | Path], env: dict[str, str], label: str) -> None:
    """Run a command with live output; raise with a clear message on failure."""
    str_cmd = [str(c) for c in cmd]
    print(f"\n=== {label} ===", flush=True)
    print("  $ " + " ".join(str_cmd), flush=True)
    result = subprocess.run(str_cmd, env=env)
    if result.returncode != 0:
        raise SystemExit(f"[run_bootstrap_auto] {label} failed "
                         f"(exit {result.returncode})")


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    repo_root  = script_dir.parent.parent  # niobium-client root

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", default=repo_root / "build",
                        type=Path,
                        help="CMake build directory (default: ./build)")
    parser.add_argument("--key-dir", default=repo_root / "bootstrap_auto_keys",
                        type=Path,
                        help="Directory for keys + intermediate artifacts "
                             "(default: ./bootstrap_auto_keys, wiped at start)")
    parser.add_argument("--openfhe-lib",
                        default=repo_root / "vendor/lib/openfhe/lib",
                        type=Path,
                        help="Directory containing libOPENFHE*.so/.dylib "
                             "(default: ./vendor/lib/openfhe/lib)")
    parser.add_argument("--keep-keys", action="store_true",
                        help="Do not wipe --key-dir at start (useful for "
                             "iterating on the auto-facade without re-keygen).")
    parser.add_argument("--skip-replay", action="store_true",
                        help="Only run the recording pass; skip the replay rerun.")
    args = parser.parse_args()

    build_dir     = args.build_dir.resolve()
    key_dir       = args.key_dir.resolve()
    openfhe_lib   = args.openfhe_lib.resolve()

    client_bin    = build_dir / "examples" / "bootstrap_client"
    auto_bin      = build_dir / "examples" / "bootstrap_auto"

    for needed in (client_bin, auto_bin):
        if not needed.exists():
            sys.exit(f"[run_bootstrap_auto] missing binary: {needed}\n"
                     f"  Build first, e.g.: make build-release")

    if not args.keep_keys and key_dir.exists():
        print(f"[run_bootstrap_auto] wiping {key_dir}")
        shutil.rmtree(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)

    # Also wipe any previous workload dir from the auto binary. The
    # program_name defaults to "bootstrap_auto" so the cache dir lives
    # alongside the key dir.
    for stale in repo_root.glob("bootstrap_auto_workload_*"):
        if stale.is_dir() and not args.keep_keys:
            print(f"[run_bootstrap_auto] wiping {stale}")
            shutil.rmtree(stale)

    env = os.environ.copy()
    setup_library_paths(env, openfhe_lib)

    # 1. Client / keygen
    run([client_bin, key_dir], env, "bootstrap_client (keygen)")

    # 2. First auto run — recording pass
    run([auto_bin, key_dir], env, "bootstrap_auto (recording)")

    # 3. Second auto run — replay pass (cache hit, no re-recording)
    if not args.skip_replay:
        run([auto_bin, key_dir], env, "bootstrap_auto (replay, cache hit)")

    print("\n[run_bootstrap_auto] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
