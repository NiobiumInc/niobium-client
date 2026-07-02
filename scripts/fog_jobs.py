#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
# Licensed under the Apache License, Version 2.0.
#
# fog_jobs.py — list / inspect / cancel Fog jobs. Companion to fog_replay.py;
# reuses its fog()/die() helpers (same X-Api-Token auth + TLS trust).
#
#   fog_jobs.py list                    # all your jobs (table)
#   fog_jobs.py get <id> [<id>...]      # full details for specific jobs
#   fog_jobs.py cancel <id> [<id>...]   # cancel/release specific jobs
#   fog_jobs.py cancel --pending        # cancel every in-flight job (frees workers)
#
# Env: FOG_API_URL [default https://api.niobium.co], FOG_API_TOKEN [required].
import argparse
import json
import os
import sys

from fog_replay import die, fog  # same dir on sys.path when run as a script

# In-flight = fog-api's IN_FLIGHT_STATUSES; these are what DELETE can act on.
CANCELLABLE = ("queued", "assigned", "running", "reserved")


def _api_token():
    api = os.environ.get("FOG_API_URL", "https://api.niobium.co").rstrip("/")
    token = os.environ.get("FOG_API_TOKEN", "")
    if not token:
        die("set FOG_API_TOKEN")
    return api, token


def _get_all(api, token):
    status, body = fog("GET", f"{api}/jobs/", token)  # trailing slash: avoid 307
    if status != 200 or not isinstance(body, list):
        die(f"GET /jobs/ returned HTTP {status}: {body}")
    return body


def _row(j):
    return (f"{j['job_id']}  {j.get('status',''):<10} {j.get('mode',''):<10} "
            f"{j.get('target',''):<14} {(j.get('worker_hostname') or '-'):<20} "
            f"{j.get('enqueued_at') or ''}")


def cmd_list(api, token, _args):
    jobs = _get_all(api, token)
    if not jobs:
        print("no jobs")
        return
    print(f"{'JOB_ID':<38}{'STATUS':<11}{'MODE':<11}{'TARGET':<15}{'WORKER':<21}ENQUEUED")
    for j in jobs:
        print(_row(j))
    print(f"\n{len(jobs)} job(s); "
          f"{sum(j.get('status') in CANCELLABLE for j in jobs)} in-flight")


def cmd_get(api, token, args):
    for jid in args.ids:
        status, body = fog("GET", f"{api}/jobs/{jid}", token)
        if status != 200:
            print(f"[{jid}] HTTP {status}: {body.get('detail') if isinstance(body, dict) else body}",
                  file=sys.stderr)
            continue
        print(json.dumps(body, indent=2))


def cmd_cancel(api, token, args):
    if args.pending:
        ids = [j["job_id"] for j in _get_all(api, token) if j.get("status") in CANCELLABLE]
        if not ids:
            print("no in-flight jobs to cancel")
            return
    elif args.ids:
        ids = args.ids
    else:
        die("cancel: give job ids or --pending")

    for jid in ids:
        status, body = fog("DELETE", f"{api}/jobs/{jid}", token)
        if status == 200:
            print(f"[{jid}] -> {body.get('status')}")
        else:  # 409 already terminal, 403 not owner, 404 unknown
            detail = body.get("detail") if isinstance(body, dict) else body
            print(f"[{jid}] HTTP {status}: {detail}", file=sys.stderr)


def selftest():
    sample = [{"status": s} for s in
              ("queued", "running", "completed", "reserved", "cancelled", "assigned")]
    got = [s["status"] for s in sample if s["status"] in CANCELLABLE]
    assert got == ["queued", "running", "reserved", "assigned"], got
    print("[fog_jobs] selftest OK")


def main(argv):
    p = argparse.ArgumentParser(description="List / inspect / cancel Fog jobs.")
    p.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("list", help="list all your jobs")
    g = sub.add_parser("get", help="full details for specific job(s)")
    g.add_argument("ids", nargs="+")
    c = sub.add_parser("cancel", help="cancel/release job(s)")
    c.add_argument("ids", nargs="*")
    c.add_argument("--pending", action="store_true", help="cancel every in-flight job")

    args = p.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.cmd:
        p.print_help()
        return

    api, token = _api_token()
    {"list": cmd_list, "get": cmd_get, "cancel": cmd_cancel}[args.cmd](api, token, args)


if __name__ == "__main__":
    main(sys.argv[1:])
