#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
# Licensed under the Apache License, Version 2.0.
#
# fog_replay.py — Fog "jobs-as-a-service" wrapper around nbcc_fhetch_replay.
#
# Drop-in for the replay step (§10 of FOG_ARCHITECTURE_CLIENT_PROPOSED.md):
#   1. POST /jobs {mode,target} to fog-api  -> {job_id, server_url, token}
#      (202 queued -> long-poll GET /jobs/{id}?wait= until assigned)
#   2. export NBCC_FHETCH_SERVER=<server_url>/jobs/<id>/run + NBCC_FHETCH_TOKEN
#   3. exec nbcc_fhetch_replay (it adds Authorization: Bearer and POSTs there)
#
# Same CLI as nbcc_fhetch_replay (--project/--target/--opt-level pass through).
# stdlib only — no requests, no curl.
#
# Env:
#   FOG_API_URL      base URL of fog-api                  [default https://api.niobium.co]
#   FOG_API_TOKEN    user's API token (sent as X-Api-Token)               [required]
#   FOG_JOB_MODE     batch | persistent                                   [default batch]
#   FOG_JOB_WAIT     per-request long-poll seconds                        [default 20]
#   FOG_JOB_MAXWAIT  total seconds to keep polling a queued job           [default 600]
#   NBCC_FHETCH_REPLAY_BIN  path to nbcc_fhetch_replay   [default: PATH / build dir]
import json
import os
import shutil
import ssl
import sys
import urllib.error
import urllib.request

# TLS trust: prefer certifi's maintained CA bundle when installed (python.org
# builds ship with no CA store — cafile=None — so the default context can't
# verify anything). Falls back to the default context, which still honors a
# working OS store and $SSL_CERT_FILE. `pip install certifi` fixes a bare host.
try:
    import certifi
    _TLS = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _TLS = None


def die(msg):
    sys.exit(f"[fog_replay] {msg}")


def fog(method, url, token, body=None):
    """Return (http_status, parsed_json|{}). Never raises on HTTP >=400."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"X-Api-Token": token})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=60, context=_TLS)
    except urllib.error.HTTPError as e:
        resp = e  # HTTPError is itself a readable response
    except urllib.error.URLError as e:
        die(f"cannot reach {url}: {e.reason}")
    raw = resp.read()
    try:
        return resp.status, (json.loads(raw) if raw else {})
    except json.JSONDecodeError:
        return resp.status, {}


def selftest():
    # Smallest check that fails if the assigned-response contract breaks.
    j = json.loads('{"job_id":"j1","server_url":"https://h/w3","token":"tok"}')
    assert j["server_url"] == "https://h/w3"
    assert json.loads('{"job_id":"j1"}').get("token") is None
    print("[fog_replay] selftest OK")


def main(argv):
    if argv[:1] == ["--selftest"]:
        return selftest()

    api = os.environ.get("FOG_API_URL", "https://api.niobium.co").rstrip("/")
    api_token = os.environ.get("FOG_API_TOKEN", "")
    if not api_token:
        die("set FOG_API_TOKEN")
    mode = os.environ.get("FOG_JOB_MODE", "batch")
    wait = int(os.environ.get("FOG_JOB_WAIT", "20"))
    maxwait = int(os.environ.get("FOG_JOB_MAXWAIT", "600"))

    # target is the one arg we must read (fog-api needs it); rest pass through.
    target = ""
    for i, a in enumerate(argv):
        if a.startswith("--target="):
            target = a[len("--target="):]
        elif a == "--target" and i + 1 < len(argv):
            target = argv[i + 1]
    if not target:
        die("--target is required")

    binary = os.environ.get("NBCC_FHETCH_REPLAY_BIN") or shutil.which("nbcc_fhetch_replay")
    if not binary:
        local = os.path.join(os.path.dirname(__file__),
                             "../build/src/fhetch_transport/nbcc_fhetch_replay")
        if os.access(local, os.X_OK):
            binary = local
    if not binary or not os.access(binary, os.X_OK):
        die("nbcc_fhetch_replay not found (set NBCC_FHETCH_REPLAY_BIN)")

    # ---- 1. submit --------------------------------------------------------
    # Trailing slash required: fog-api's route is POST /jobs/ and its default
    # redirect_slashes would 307 /jobs -> /jobs/, which urllib won't re-POST.
    print(f"[fog_replay] POST {api}/jobs/ {{mode:{mode}, target:{target}}}", file=sys.stderr)
    status, body = fog("POST", f"{api}/jobs/", api_token, {"mode": mode, "target": target})

    if status == 202:  # queued -> long-poll
        jid = body.get("job_id") or die("queued but no job_id in response")
        print(f"[fog_replay] queued as {jid}; long-polling for a worker…", file=sys.stderr)
        waited = 0
        while not body.get("server_url"):
            if waited >= maxwait:
                die(f"job {jid} still queued after {maxwait}s")
            status, body = fog("GET", f"{api}/jobs/{jid}?wait={wait}", api_token)
            if status != 200:
                die(f"GET /jobs/{jid} returned HTTP {status}")
            waited += wait
    elif status not in (200, 201):
        # FastAPI puts the reason in {"detail": ...} — surface it (which quota,
        # which grant) instead of a bare status.
        detail = body.get("detail") if isinstance(body, dict) else None
        hint = {429: "over quota", 401: "unauthorized", 403: "forbidden"}.get(status, "")
        die(f"POST /jobs/ returned HTTP {status}{f' ({hint})' if hint else ''}"
            f"{f': {detail}' if detail else ''}")

    server_url, token, jid = body.get("server_url"), body.get("token"), body.get("job_id")
    if not (server_url and token and jid):
        die("assigned response missing server_url/token/job_id")

    # ---- 2 & 3. point the binary at the worker run endpoint and exec ------
    os.environ["NBCC_FHETCH_SERVER"] = f"{server_url.rstrip('/')}/jobs/{jid}/run"
    os.environ["NBCC_FHETCH_TOKEN"] = token
    print(f"[fog_replay] assigned {jid} -> {os.environ['NBCC_FHETCH_SERVER']}", file=sys.stderr)
    os.execv(binary, [binary] + argv)


if __name__ == "__main__":
    main(sys.argv[1:])
