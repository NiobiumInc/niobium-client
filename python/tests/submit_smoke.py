#!/usr/bin/env python3
"""Smoke test for niobium_client.client.submit() + the _archive binding.

- unit: pack_directory -> unpack_into round-trips a dir and excludes serialized_probes/.
- submit: against an in-process mock replay server, asserts the request contract
  (path, X-Target/X-Project-Name/content-type headers, a valid NBAR body) and that
  the returned probe archive unpacks into <project>/serialized_probes/.

No OpenFHE/libnbfhetch needed (the archive path is pure stdlib). Run with PYTHONPATH
pointing at the built niobium_client package (build/python).
"""
import http.server
import os
import tempfile
import threading

from niobium_client import client, _archive


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
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            got["path"] = self.path
            got["target"] = self.headers.get("X-Target")
            got["project"] = self.headers.get("X-Project-Name")
            got["ctype"] = self.headers.get("Content-Type")
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with tempfile.TemporaryDirectory() as td:
                got["unpacked"] = _archive.unpack_into(body, os.path.join(td, "in"))
                pdir = os.path.join(td, "probes")
                os.makedirs(pdir)
                open(os.path.join(pdir, "result.ct"), "wb").write(b"probe-bytes")
                resp = _archive.pack_directory(pdir, [])  # entry: "result.ct"
            self.send_response(200)
            self.send_header("Content-Type", "application/x-niobium-archive")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def main():
    ok = unit_pack_unpack()
    srv, got = start_mock_server()
    port = srv.server_address[1]
    try:
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "mult_server_workload_x")
            os.makedirs(proj)
            open(os.path.join(proj, "trace.fhetch"), "w").write("halt\n")
            n = client.submit(proj, target="FUNC_SIM",
                              endpoint=f"http://127.0.0.1:{port}", opt_level="O1")
            probe = os.path.join(proj, "serialized_probes", "result.ct")
            checks = [
                ("submit returned n>0", n > 0),
                ("probe unpacked into serialized_probes/",
                 os.path.exists(probe) and open(probe, "rb").read() == b"probe-bytes"),
                ("POST path == /replay", got.get("path") == "/replay"),
                ("X-Target forwarded", got.get("target") == "FUNC_SIM"),
                ("X-Project-Name == dir basename",
                 got.get("project") == "mult_server_workload_x"),
                ("content-type", got.get("ctype") == "application/x-niobium-archive"),
                ("server received a valid NBAR body", got.get("unpacked", 0) > 0),
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
