"""niobium_sdk.client — client surface.

Currently implements the cloud/remote target: submit(). Local replay (the default
target) is the bundled fhetch_sim, driven by the recorder's replay() — a separate
path. submit() mirrors the C++ nbcc_fhetch_replay forwarder (src/fhetch_transport/
client.cpp): pack the project dir (excluding serialized_probes/), POST it to the
replay server, unpack the returned archive into serialized_probes/.

Framing is the bound C++ archive (niobium_sdk._archive) — no Python reimpl of
the wire format. HTTP is stdlib urllib (no third-party dep).
"""
import os
import shutil
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from . import _archive

# Wire contract — mirrors src/fhetch_transport/protocol.h + client.cpp.
_REPLAY_PATH = "/replay"
_CONTENT_TYPE = "application/x-niobium-archive"
_SERVER_ENV = "NBCC_FHETCH_SERVER"
_DEFAULT_SERVER = "http://127.0.0.1:9443"
# The Fog per-job ticket (protocol.h kAuthTokenEnv) — sent as `Authorization:
# Bearer`. This is the *worker* auth, distinct from the fog-api control-plane key
# (X-Api-Token / FOG_API_TOKEN), which niobium_sdk.fog uses for POST /jobs/.
_TOKEN_ENV = "NBCC_FHETCH_TOKEN"

_config = {"endpoint": None, "token": None}


def configure(*, endpoint=None, token=None):
    """Set defaults for submit(): server URL and/or API token."""
    if endpoint is not None:
        _config["endpoint"] = endpoint
    if token is not None:
        _config["token"] = token


def _resolve_endpoint(endpoint):
    # server URL: explicit arg > configure() > NBCC_FHETCH_SERVER env > default.
    return (endpoint or _config["endpoint"]
            or os.environ.get(_SERVER_ENV) or _DEFAULT_SERVER)


def _target_url(server):
    """Resolve the POST URL — mirrors client.cpp origin_of().

    A bare origin (``http://host[:port]``) gets ``/replay`` appended (the direct /
    local-dev-server path). A URL that already carries a path — the Fog worker's
    ``/jobs/<id>/run``, exported as NBCC_FHETCH_SERVER by ``fog submit`` — is used
    verbatim, never re-suffixed.
    """
    s = server if "://" in server else "http://" + server
    parts = urlsplit(s)
    if parts.path in ("", "/"):
        return urlunsplit((parts.scheme, parts.netloc, _REPLAY_PATH, parts.query, ""))
    return s


def _resolve_token(token):
    # Fog worker ticket: explicit arg > configure() > NBCC_FHETCH_TOKEN env.
    return token or _config["token"] or os.environ.get(_TOKEN_ENV)


def submit(project_dir, target, *, endpoint=None, opt_level=None, token=None,
           job_id=None, timeout=7200):
    """Ship a recorded fhetch project dir to a replay server; unpack the probes.

    This is the data plane — the Python peer of the C++ nbcc_fhetch_replay client.
    It POSTs to whatever endpoint it is given; job provisioning (fog-api /jobs/)
    is the control plane, owned by niobium_sdk.fog, which calls this with the
    worker URL + per-job ticket it obtained.

    project_dir : the recorded project directory (contains the .fhetch + inputs).
    target      : device id forwarded as X-Target (e.g. "FUNC_SIM", "fpga5.2").
    endpoint    : server URL (else configure()/NBCC_FHETCH_SERVER/default). A bare
                  origin gets /replay; a Fog worker URL (/jobs/<id>/run) is used as-is.
    opt_level   : optional "O0".."O3" (X-Opt-Level); omit → server defaults O0.
    token       : Fog per-job ticket (else configure()/NBCC_FHETCH_TOKEN) → Bearer.
    job_id      : optional Fog job id, forwarded as X-Job-Id (server-side telemetry).
    Returns the number of probe files unpacked into <project_dir>/serialized_probes.
    """
    project_dir = os.path.abspath(project_dir)
    url = _target_url(_resolve_endpoint(endpoint))

    body = _archive.pack_directory(project_dir)  # excludes serialized_probes/
    headers = {
        "X-Target": target,
        "X-Project-Name": os.path.basename(project_dir),
        "Content-Type": _CONTENT_TYPE,
    }
    if opt_level:
        headers["X-Opt-Level"] = opt_level
    if job_id:
        headers["X-Job-Id"] = job_id
    tok = _resolve_token(token)
    if tok:
        headers["Authorization"] = "Bearer " + tok  # Fog per-job ticket; mirrors client.cpp

    req = urllib.request.Request(url, data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        archive = resp.read()

    probes_dir = os.path.join(project_dir, "serialized_probes")
    shutil.rmtree(probes_dir, ignore_errors=True)
    n = _archive.unpack_into(archive, probes_dir)
    if n == 0:
        raise RuntimeError("server returned an empty archive — nothing for result()")
    return n
