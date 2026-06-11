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
"""Verify that a cache-hit replay does NOT regenerate the recorded workload.

A replay run must execute zero FHE operations on the host and must not
re-record anything. This tool proves it at the filesystem level:

  snapshot  — record a manifest of the workload directory right after the
              RECORD run: per file, size, sha256, mtime_ns, and inode.
  verify    — after the REPLAY run, compare against the manifest:

    * The .fhetch instruction trace — and every other recording artifact —
      must be byte-identical AND must not have been rewritten: same content,
      same mtime_ns, same inode. A rewrite with identical bytes (new mtime
      or new inode) FAILS: the trace must not be re-generated at all.
    * Captured input copies (*.input_*.bin / *.ids) MAY be updated in place:
      that is refresh_stale_inputs delivering new input data by design.
      They must not be deleted.
    * serialized_probes/ MAY gain or update files: those are the
      reconstructed OUTPUTS the replay produces.
    * Captured inputs that point at serialized_probes/ are outputs
      mis-tagged as inputs — always a FAILure.
    * Any other new or deleted file FAILs.

Usage:
  verify_replay_integrity.py snapshot <workload_dir> [--manifest PATH]
  verify_replay_integrity.py verify   <workload_dir> [--manifest PATH]

Exit code 0 on PASS, 1 on FAIL (with a per-file report).
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PROBE_DIR = "serialized_probes"


def is_input_capture(rel: str) -> bool:
    """Captured-input artifacts that refresh_stale_inputs / the cooperative
    tagging path may rewrite in place between record and replay: input
    ciphertext captures AND eval-key/context captures (keys are inputs too —
    the documented record-once/replay-with-new-keys flow relies on
    refreshing them)."""
    name = Path(rel).name
    if not (name.endswith(".bin") or name.endswith(".ids")
            or name.endswith(".dat")):
        return False
    return (".input_" in name or ".mk." in name or ".rk." in name
            or ".cc." in name or name == "cryptocontext.dat")


def is_probe_output(rel: str) -> bool:
    return PROBE_DIR in Path(rel).parts


def is_junk_output_capture(rel: str) -> bool:
    """An input-capture whose source path was a serialized_probes file:
    a reconstructed OUTPUT captured as an input — never legitimate."""
    return is_input_capture(rel) and PROBE_DIR in Path(rel).name


def scan(workload_dir: Path) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    for p in sorted(workload_dir.rglob("*")):
        if not p.is_file():
            continue
        st = p.stat()
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        entries[str(p.relative_to(workload_dir))] = {
            "size": st.st_size,
            "sha256": h.hexdigest(),
            "mtime_ns": st.st_mtime_ns,
            "inode": st.st_ino,
        }
    return entries


def cmd_snapshot(workload_dir: Path, manifest: Path) -> int:
    if not workload_dir.is_dir():
        print(f"[replay-integrity] FAIL: no workload dir {workload_dir}")
        return 1
    entries = scan(workload_dir)
    if not any(rel.endswith(".fhetch") for rel in entries):
        print(f"[replay-integrity] FAIL: no .fhetch trace in {workload_dir}")
        return 1
    manifest.write_text(json.dumps(
        {"workload_dir": str(workload_dir), "files": entries}, indent=1))
    print(f"[replay-integrity] snapshot: {len(entries)} files "
          f"({sum(1 for r in entries if r.endswith('.fhetch'))} trace) "
          f"-> {manifest}")
    return 0


def cmd_verify(workload_dir: Path, manifest: Path) -> int:
    before = json.loads(manifest.read_text())["files"]
    after = scan(workload_dir)
    failures: list[str] = []
    refreshed = 0
    rewritten_same = 0
    new_outputs = 0

    for rel, old in before.items():
        now = after.get(rel)
        if now is None:
            failures.append(f"DELETED: {rel}")
            continue
        if is_input_capture(rel):
            if now["sha256"] != old["sha256"]:
                refreshed += 1
            elif now["mtime_ns"] != old["mtime_ns"]:
                rewritten_same += 1  # re-serialized identical bytes (waste,
                                     # not a correctness violation)
            continue  # in-place refresh is the feature
        if is_probe_output(rel):
            continue  # reconstructed outputs may be rewritten
        # Recording artifact (.fhetch trace, .ids maps, metadata): must be
        # untouched — identical bytes are NOT enough, it must be the same
        # file, never re-generated.
        if now["sha256"] != old["sha256"]:
            failures.append(f"REWRITTEN (content changed): {rel}")
        elif now["mtime_ns"] != old["mtime_ns"]:
            failures.append(f"RE-GENERATED (same bytes, new mtime): {rel}")
        elif now["inode"] != old["inode"]:
            failures.append(f"RE-CREATED (same bytes, new inode): {rel}")

    for rel in after:
        if rel in before:
            continue
        if is_junk_output_capture(rel):
            failures.append(f"OUTPUT CAPTURED AS INPUT: {rel}")
        elif is_probe_output(rel):
            new_outputs += 1  # the replay's reconstructed result — expected
        else:
            failures.append(f"NEW FILE (replay must not record): {rel}")

    if failures:
        print(f"[replay-integrity] FAIL ({len(failures)} violations):")
        for f in failures:
            print(f"  {f}")
        return 1
    n_trace = sum(1 for r in before if r.endswith(".fhetch"))
    print(f"[replay-integrity] PASS: {n_trace} .fhetch trace untouched "
          f"(content+mtime+inode), {refreshed} inputs refreshed in place, "
          f"{rewritten_same} input/key captures re-serialized identical "
          f"(waste, tolerated), {new_outputs} probe outputs written, "
          f"no junk captures")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["snapshot", "verify"])
    ap.add_argument("workload_dir", type=Path)
    ap.add_argument("--manifest", type=Path, default=None)
    args = ap.parse_args()
    manifest = args.manifest or (args.workload_dir.parent
                                 / f".{args.workload_dir.name}.manifest.json")
    if args.command == "snapshot":
        return cmd_snapshot(args.workload_dir, manifest)
    return cmd_verify(args.workload_dir, manifest)


if __name__ == "__main__":
    sys.exit(main())
