#!/usr/bin/env python3
"""Security regression tests for nbcc_fhetch_replay_server.

The transport's end-to-end test (test_transport_mult.sh) proves the happy path
works. It cannot prove that anything gets *rejected*, which is what the server's
input validation exists for. This suite drives the daemon over HTTP with hostile
input and asserts the refusals actually fire:

  1. X-Target path traversal          — the target becomes a path component
                                        (devices/<target>/spec.yaml) on the
                                        compiler side, so ".." must not survive
  2. Legitimate device ids            — the guard must not reject real targets
                                        (fpga6.1.0 and func_sim_hw are the ones
                                        a naive charset rule breaks)
  3. Hostile archive entry names      — the archive format stores names
                                        verbatim; shell metacharacters and
                                        traversal must never reach the filesystem
  4. Implausible name lengths         — rejected before being buffered
  5. Error bodies withhold paths      — no server-side path disclosure unless
                                        the operator passes --return-logs
  6. Per-job timing dir (optional)    — NB_TIMING_SUMMARY_DIR still reaches the
                                        compiler; needs a real compiler binary

Cases 1-5 need no compiler: every one of them is refused before the server
spawns anything, so a stub --exec is enough and this runs in the client's own
CI. Case 6 needs the real (proprietary) nbcc_fhetch_replay plus a recorded
project, so it only runs when --project and --exec point at them.

Usage:
  test_transport_hardening.py [--server-bin PATH] [--exec PATH] [--project DIR]
"""
import argparse
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

CLIENT_ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_SERVER = CLIENT_ROOT / "build/src/fhetch_transport/nbcc_fhetch_replay_server"


class Results:
    def __init__(self):
        self.failures = []

    def check(self, name, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {name}", flush=True)
        if not ok:
            self.failures.append(f"{name}: {detail}")
            if detail:
                print(f"        {detail}", flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def archive(entries):
    """Build an NBAR archive: magic, u32 count, then per entry
    u32 name_len + name + u64 data_len + data."""
    out = b"NBAR" + struct.pack("<I", len(entries))
    for name, data in entries:
        raw = name.encode()
        out += struct.pack("<I", len(raw)) + raw
        out += struct.pack("<Q", len(data)) + data
    return out


def pack_dir(root):
    root = pathlib.Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file())
    return archive([(p.relative_to(root).as_posix(), p.read_bytes()) for p in files])


def post(base, body, target="FUNC_SIM", job_id=None, timeout=60):
    headers = {"X-Target": target, "Content-Type": "application/x-niobium-archive"}
    if job_id:
        headers["X-Job-Id"] = job_id
    req = urllib.request.Request(f"{base}/replay", data=body, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def wait_healthy(base, proc, attempts=30):
    for _ in range(attempts):
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(1)  # sleep between polls: never spin on a socket/file
    return False


def run_checks(base, res, project, real_compiler, timing_root):
    good = archive([("fhetch_replay.json", b"{}")])

    print("[1] X-Target traversal rejected")
    for bad in ["../../../etc", "..", "a/../../b", "func_sim/../../x",
                ".hidden", "fpga5.2/", "a b", "x;y", "$(id)"]:
        status, body = post(base, good, target=bad)
        text = body.decode(errors="replace")
        res.check(f"target {bad!r} -> 400",
                  status == 400 and "X-Target" in text,
                  f"got {status}: {text[:120]!r}")

    print("[2] Legitimate device ids pass target validation")
    for ok in ["FUNC_SIM", "func_sim", "func_sim_hw", "fpga5.2", "fpga6.1.0",
               "fpga8.0.1", "mistic1.0", "qemu_sim", "FOG"]:
        status, body = post(base, good, target=ok)
        text = body.decode(errors="replace")
        # Must get PAST the target check; failing later (unpack/compile) is fine.
        res.check(f"target {ok!r} accepted", "X-Target" not in text,
                  f"rejected by target check: {status}: {text[:120]!r}")

    print("[3] Hostile archive entry names rejected")
    hostile = {
        "shell metachar":   "out;rm -rf ~.ct",
        "cmd substitution": "$(whoami).ct",
        "backtick":         "`id`.ct",
        "space":            "two words.ct",
        "leading dash":     "-rf.ct",
        "traversal":        "../escaped.ct",
        "nested traversal": "a/../../escaped.ct",
        "absolute path":    "/etc/passwd",
        "newline":          "probe\n.ct",
        "backslash":        "a\\b.ct",
        "quote":            'a"b.ct',
    }
    for label, name in hostile.items():
        status, body = post(base, archive([(name, b"x")]))
        res.check(f"{label} ({name!r}) -> 400", status == 400,
                  f"got {status}: {body.decode(errors='replace')[:120]!r}")

    print("[4] Implausible entry name length rejected")
    status, body = post(base, b"NBAR" + struct.pack("<I", 1)
                        + struct.pack("<I", 0xFFFFFFFF))
    res.check("name_len=4G -> 400", status == 400,
              f"got {status}: {body.decode(errors='replace')[:120]!r}")

    print("[5] Error bodies withhold server-side paths")
    leaks = []
    for name in ["../escaped.ct", "out;rm.ct"]:
        _, body = post(base, archive([(name, b"x")]))
        text = body.decode(errors="replace")
        for marker in ("/var/folders", "/tmp/", "nbcc_fhetch_server_"):
            if marker in text:
                leaks.append(f"{name!r} leaked {marker!r}")
    res.check("no temp path in error bodies", not leaks, "; ".join(leaks))

    if not (project and real_compiler):
        print("[6] Per-job timing dir: SKIPPED (needs --project and a real --exec)")
        return

    print("[6] Per-job timing dir reaches the compiler")
    job = "job-hardening-test"
    status, body = post(base, pack_dir(project), job_id=job, timeout=900)
    res.check("replay with X-Job-Id -> 200", status == 200,
              f"got {status}: {body.decode(errors='replace')[:200]!r}")
    if status == 200:
        job_dir = pathlib.Path(timing_root) / job
        produced = sorted(p.name for p in job_dir.rglob("*")) if job_dir.is_dir() else []
        # Telemetry can only land here if the child actually got the env var.
        res.check("NB_TIMING_SUMMARY_DIR honored", bool(produced),
                  f"{job_dir} empty or missing")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server-bin", default=str(DEFAULT_SERVER))
    ap.add_argument("--exec", dest="compiler",
                    help="compiler binary; omit to use a stub (cases 1-5 only)")
    ap.add_argument("--project", help="recorded fhetch project dir (enables case 6)")
    args = ap.parse_args()

    if not os.access(args.server_bin, os.X_OK):
        sys.exit(f"error: server binary not executable: {args.server_bin}\n"
                 f"build it with: make build-release")

    # Cases 1-5 never reach the compiler, so any executable satisfies the
    # server's startup pre-flight. Case 6 needs the real thing.
    real_compiler = bool(args.compiler)
    compiler = args.compiler or "/bin/echo"

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    res = Results()

    with tempfile.TemporaryDirectory(prefix="nbcc_hardening_") as timing_root:
        env = dict(os.environ, NBCC_FHETCH_TIMING_ROOT=timing_root)
        proc = subprocess.Popen(
            [args.server_bin, "--port", str(port), "--bind", "127.0.0.1",
             "--exec", compiler],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            if not wait_healthy(base, proc):
                out = proc.stdout.read() if proc.stdout else ""
                sys.exit(f"error: server did not become healthy on {base}\n{out}")
            print(f"server up on {base} (exec={compiler})\n")
            run_checks(base, res, args.project, real_compiler, timing_root)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()

    print()
    if res.failures:
        print(f"FAILED ({len(res.failures)}):")
        for f in res.failures:
            print(f"  - {f}")
        return 1
    print("all transport hardening checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
