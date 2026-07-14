#!/usr/bin/env python3
"""Smoke test for niobium_sdk.client.submit() + the _archive binding.

- unit: pack_directory -> unpack_into round-trips a dir and excludes serialized_probes/.
- submit: against an in-process mock replay server, asserts the request contract
  (path, X-Target/X-Project-Name/content-type headers, a valid NBAR body) and that
  the returned probe archive unpacks into <project>/serialized_probes/.

No OpenFHE/libnbfhetch needed (the archive path is pure stdlib). Run with PYTHONPATH
pointing at the built niobium_sdk package (build/python).
"""
import http.server
import os
import tempfile
import threading

from niobium_sdk import client, _archive


def unit_pack_unpack():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "proj")
        os.makedirs(os.path.join(src, "sub"))
        open(os.path.join(src, "a.bin"), "wb").write(b"hello")
        open(os.path.join(src, "sub", "b.txt"), "w").write("world")
        os.makedirs(os.path.join(src, "serialized_probes"))
        open(os.path.join(src, "serialized_probes", "old.ct"), "wb").write(b"stale")

        arc = _archive.pack_directory(src)  # excludes serialized_probes/
        dest = os.path.join(d, "out")
        n = _archive.unpack_into(arc, dest)
        ok = (open(os.path.join(dest, "a.bin"), "rb").read() == b"hello"
              and open(os.path.join(dest, "sub", "b.txt")).read() == "world"
              and not os.path.exists(os.path.join(dest, "serialized_probes")))
        print(f"  [{'PASS' if ok else 'FAIL'}] pack/unpack round-trip, excl "
              f"serialized_probes (n={n})")
        return ok


def start_mock_server():
    reqs = []  # one dict per received POST, in order

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with tempfile.TemporaryDirectory() as td:
                unpacked = _archive.unpack_into(body, os.path.join(td, "in"))
                pdir = os.path.join(td, "probes")
                os.makedirs(pdir)
                open(os.path.join(pdir, "result.ct"), "wb").write(b"probe-bytes")
                resp = _archive.pack_directory(pdir, [])  # entry: "result.ct"
            reqs.append({
                "path": self.path,
                "target": self.headers.get("X-Target"),
                "project": self.headers.get("X-Project-Name"),
                "ctype": self.headers.get("Content-Type"),
                "opt": self.headers.get("X-Opt-Level"),
                "auth": self.headers.get("Authorization"),
                "jobid": self.headers.get("X-Job-Id"),
                "unpacked": unpacked,
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/x-niobium-archive")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, reqs


def main():
    ok = unit_pack_unpack()
    srv, reqs = start_mock_server()
    port = srv.server_address[1]
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "mult_server_workload_x")
            os.makedirs(proj)
            open(os.path.join(proj, "trace.fhetch"), "w").write("halt\n")

            # (1) direct / local-dev path: bare origin -> /replay, no auth.
            n = client.submit(proj, target="FUNC_SIM",
                              endpoint=f"http://127.0.0.1:{port}", opt_level="O1")
            probe = os.path.join(proj, "serialized_probes", "result.ct")
            r0 = reqs[0]
            checks = [
                ("submit returned n>0", n > 0),
                ("probe unpacked into serialized_probes/",
                 os.path.exists(probe) and open(probe, "rb").read() == b"probe-bytes"),
                ("bare origin -> POST /replay", r0["path"] == "/replay"),
                ("X-Target forwarded", r0["target"] == "FUNC_SIM"),
                ("X-Project-Name == dir basename",
                 r0["project"] == "mult_server_workload_x"),
                ("X-Opt-Level forwarded", r0["opt"] == "O1"),
                ("content-type", r0["ctype"] == "application/x-niobium-archive"),
                ("server received a valid NBAR body", r0["unpacked"] > 0),
                ("no Authorization when no token", r0["auth"] is None),
            ]

            # (2) Fog worker path: a full /jobs/<id>/run URL is used verbatim, and
            # the per-job ticket -> Authorization: Bearer, job id -> X-Job-Id.
            client.submit(proj, target="fpga5.2",
                          endpoint=f"http://127.0.0.1:{port}/jobs/j123/run",
                          token="tok123", job_id="j123")
            r1 = reqs[1]
            checks += [
                ("Fog worker URL used verbatim (no /replay suffix)",
                 r1["path"] == "/jobs/j123/run"),
                ("per-job ticket -> Bearer", r1["auth"] == "Bearer tok123"),
                ("job id -> X-Job-Id", r1["jobid"] == "j123"),
            ]

            for name, passed in checks:
                print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
                ok = ok and passed
    finally:
        srv.shutdown()
    print("submit smoke:", "ALL PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
