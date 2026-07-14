"""niobium_sdk.fog — pure-Python Fog cloud client (control plane + CLI).

The `fog` console script and this module give a pip-only user the full Fog path —
login -> provision a job -> submit the recorded project -> unpack -> decrypt — with
no C++ transport binary and no scripts/fog on disk.

Single source of truth: the vendored ``niobium_sdk._fog`` is a *verbatim* copy of
``scripts/fog`` (the same file the C++ stack ships). This module reuses it directly
for login/list/get/cancel/init and for its config/creds/HTTP primitives. The only
piece re-expressed here is the provision-and-submit orchestration, because
``scripts/fog`` finishes that path by ``exec()``-ing a transport binary the wheel
does not carry; the wheel instead does the data hop in-process via
``niobium_sdk.client.submit()``. The fog-api wire logic itself is not forked.

Two planes (see the Fog design): the *control plane* (POST /jobs/, long-poll, mint
the per-job ticket) lives in ``_fog``; the *data plane* (pack -> POST -> unpack) is
``niobium_sdk.client.submit()``. ``run()`` composes them.
"""
import argparse
import sys

from . import _fog, client


def provision(target, *, mode=None, wait=None, maxwait=None):
    """Provision a Fog job via fog-api; return ``(worker_url, ticket, job_id)``.

    Mirrors scripts/fog ``cmd_submit``'s control-plane hop on ``_fog``'s own
    primitives (``request``/``_api``/``_require_token``/``cfg``), so the endpoints,
    auth, and config precedence stay single-source. Exits via ``_fog.die`` on an
    API error, matching the CLI tool's behavior.
    """
    api = _fog._api()
    api_token = _fog._require_token()
    mode = mode or _fog.cfg("mode", "FOG_JOB_MODE", _fog.DEFAULTS["mode"])
    wait = int(wait if wait is not None
               else _fog.cfg("wait", "FOG_JOB_WAIT", _fog.DEFAULTS["wait"]))
    maxwait = int(maxwait if maxwait is not None
                  else _fog.cfg("maxwait", "FOG_JOB_MAXWAIT", _fog.DEFAULTS["maxwait"]))

    status, body = _fog.request("POST", f"{api}/jobs/", token=api_token,
                                json_body={"mode": mode, "target": target})
    if status == 202:  # queued -> long-poll for a worker
        jid = body.get("job_id") or _fog.die("queued but no job_id in response")
        waited = 0
        while not body.get("server_url"):
            if waited >= maxwait:
                _fog.die(f"job {jid} still queued after {maxwait}s")
            status, body = _fog.request("GET", f"{api}/jobs/{jid}?wait={wait}",
                                        token=api_token)
            if status != 200:
                _fog.die(f"GET /jobs/{jid} returned HTTP {status}")
            waited += wait
    elif status not in (200, 201):
        hint = {429: "over quota", 401: "unauthorized", 403: "forbidden"}.get(status, "")
        detail = _fog._detail(body)
        _fog.die(f"POST /jobs/ returned HTTP {status}{f' ({hint})' if hint else ''}"
                 f"{f': {detail}' if detail else ''}")

    server_url, ticket, jid = (body.get("server_url"), body.get("token"),
                               body.get("job_id"))
    if not (server_url and ticket and jid):
        _fog.die("assigned response missing server_url/token/job_id")
    worker_url = f"{server_url.rstrip('/')}/jobs/{jid}/run"
    return worker_url, ticket, jid


def run(project_dir, target, *, mode=None, opt_level=None, wait=None, maxwait=None):
    """Provision a Fog job, then submit ``project_dir`` to its worker.

    Returns the number of probe files unpacked into
    ``<project_dir>/serialized_probes`` (ready for ``session.result()``).
    """
    worker_url, ticket, jid = provision(target, mode=mode, wait=wait, maxwait=maxwait)
    print(f"[fog] assigned {jid} -> {worker_url}", file=sys.stderr)
    return client.submit(project_dir, target, endpoint=worker_url, token=ticket,
                         job_id=jid, opt_level=opt_level)


# ---- CLI --------------------------------------------------------------------
_USAGE = """usage: fog <command> [args]

  login [-u EMAIL] [-n NAME]      provision a named API key -> ~/.fog/credentials
  init [-f]                       write ~/.fog/config with default values
  submit <project_dir> --target=T [--opt O1] [--mode batch]
                                  provision a job and submit the recorded project
  list                            list all your jobs
  get ID [ID...]                  full JSON for specific job(s)
  cancel ID [ID...] | --pending   cancel job(s)

Config: ~/.fog/config + ~/.fog/credentials (shared with the `fog` CLI in scripts/).
"""


def _cmd_submit(rest):
    p = argparse.ArgumentParser(prog="fog submit", add_help=True)
    p.add_argument("project_dir", help="recorded fhetch project directory")
    p.add_argument("--target", required=True, help="device id (e.g. FOG, fpga5.2)")
    p.add_argument("--opt", dest="opt_level", default=None, help="O0..O3")
    p.add_argument("--mode", default=None, help="batch | persistent")
    args = p.parse_args(rest)
    n = run(args.project_dir, args.target, mode=args.mode, opt_level=args.opt_level)
    print(f"[fog] unpacked {n} probe file(s) into "
          f"{args.project_dir}/serialized_probes")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_USAGE, file=sys.stderr)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "submit":
        return _cmd_submit(rest)
    # login/init/list/get/cancel are pure control plane — reuse the vendored CLI
    # verbatim (no exec involved in any of them).
    delegates = {
        "login": _fog.cmd_login, "init": _fog.cmd_init, "list": _fog.cmd_list,
        "get": _fog.cmd_get, "cancel": _fog.cmd_cancel,
    }
    if cmd in delegates:
        return delegates[cmd](rest)
    _fog.die(f"unknown command {cmd!r}\n\n{_USAGE}")


if __name__ == "__main__":
    raise SystemExit(main())
