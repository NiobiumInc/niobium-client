#!/usr/bin/env python3
"""Smoke test for niobium_sdk.fog — the pure-Python Fog control plane.

Runs entirely in-process against a mock fog-api + mock worker; no network, no
OpenFHE (the archive path is pure stdlib). Covers:
  - the vendored _fog module is intact (its own selftest);
  - fog.run(): provision (POST /jobs/) -> submit to the worker (/jobs/<id>/run)
    with the per-job ticket as Bearer + X-Job-Id -> probes unpack;
  - a control-plane error (429) surfaces as an exit, not a silent pass.

Run with PYTHONPATH pointing at the assembled niobium_sdk package (build/python),
which carries _archive + the vendored _fog.py.
"""
import http.server
import os
import tempfile
import threading

from niobium_sdk import fog, _fog, _archive

TICKET = "ticket-xyz"
JOB_ID = "jABC123"


def start_mock_fog():
    """A mock fog-api + worker. state['jobs_status'] controls POST /jobs/."""
    state = {"jobs_status": 200, "worker": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, status, obj):
            import json
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path == "/jobs/":                      # control plane: provision
                if state["jobs_status"] != 200:
                    return self._json(state["jobs_status"], {"detail": "quota exceeded"})
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                return self._json(200, {"server_url": base, "token": TICKET,
                                        "job_id": JOB_ID})
            if self.path == f"/jobs/{JOB_ID}/run":         # data plane: the worker
                with tempfile.TemporaryDirectory() as td:
                    unpacked = _archive.unpack_into(body, os.path.join(td, "in"))
                    pdir = os.path.join(td, "probes")
                    os.makedirs(pdir)
                    open(os.path.join(pdir, "result.ct"), "wb").write(b"probe-bytes")
                    resp = _archive.pack_directory(pdir, [])
                state["worker"] = {
                    "auth": self.headers.get("Authorization"),
                    "jobid": self.headers.get("X-Job-Id"),
                    "target": self.headers.get("X-Target"),
                    "unpacked": unpacked,
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/x-niobium-archive")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
                return
            self._json(404, {"detail": "not found"})

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


def main():
    ok = True

    # (A) the vendored control-plane CLI is intact.
    try:
        _fog.selftest()
        print("  [PASS] vendored _fog selftest")
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] vendored _fog selftest: {e}")
        ok = False

    srv, state = start_mock_fog()
    port = srv.server_address[1]
    os.environ["FOG_API_URL"] = f"http://127.0.0.1:{port}"
    os.environ["FOG_API_TOKEN"] = "fake-api-key"          # skips login
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "job_workload")
            os.makedirs(proj)
            open(os.path.join(proj, "trace.fhetch"), "w").write("halt\n")

            # (B) happy path: provision -> submit to worker -> unpack.
            n = fog.run(proj, target="FOG", opt_level="O2")
            w = state["worker"] or {}
            probe = os.path.join(proj, "serialized_probes", "result.ct")
            checks = [
                ("run() unpacked probes (n>0)", n > 0),
                ("probe written to serialized_probes/",
                 os.path.exists(probe) and open(probe, "rb").read() == b"probe-bytes"),
                ("worker got the archive", w.get("unpacked", 0) > 0),
                ("per-job ticket -> Bearer at worker",
                 w.get("auth") == f"Bearer {TICKET}"),
                ("job id -> X-Job-Id at worker", w.get("jobid") == JOB_ID),
                ("X-Target forwarded to worker", w.get("target") == "FOG"),
            ]

            # (C) control-plane error is not swallowed: 429 -> exit.
            state["jobs_status"] = 429
            raised = False
            try:
                fog.provision("FOG")
            except SystemExit:
                raised = True
            checks.append(("provision() exits on 429 (over quota)", raised))

            for name, passed in checks:
                print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
                ok = ok and passed
    finally:
        srv.shutdown()
    print("fog smoke:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
