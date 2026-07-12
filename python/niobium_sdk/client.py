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

from . import _archive

# Wire contract — mirrors src/fhetch_transport/protocol.h.
_REPLAY_PATH = "/replay"
_CONTENT_TYPE = "application/x-niobium-archive"
_SERVER_ENV = "NBCC_FHETCH_SERVER"
_DEFAULT_SERVER = "http://127.0.0.1:9443"
_TOKEN_ENVS = ("NIOBIUM_TOKEN", "FOG_API_TOKEN")

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


def _resolve_token(token):
    if token or _config["token"]:
        return token or _config["token"]
    for env in _TOKEN_ENVS:
        if os.environ.get(env):
            return os.environ[env]
    return None


def submit(project_dir, target, *, endpoint=None, opt_level=None, token=None,
           timeout=7200):
    """Ship a recorded fhetch project dir to a replay server; unpack the probes.

    project_dir : the recorded project directory (contains the .fhetch + inputs).
    target      : device id forwarded as X-Target (e.g. "FUNC_SIM", "fpga5.2").
    endpoint    : server URL (else configure()/NBCC_FHETCH_SERVER/default).
    opt_level   : optional "O0".."O3" (X-Opt-Level); omit → server defaults O0.
    Returns the number of probe files unpacked into <project_dir>/serialized_probes.
    """
    project_dir = os.path.abspath(project_dir)
    server = _resolve_endpoint(endpoint).rstrip("/")

    body = _archive.pack_directory(project_dir)  # excludes serialized_probes/
    headers = {
        "X-Target": target,
        "X-Project-Name": os.path.basename(project_dir),
        "Content-Type": _CONTENT_TYPE,
    }
    if opt_level:
        headers["X-Opt-Level"] = opt_level
    tok = _resolve_token(token)
    if tok:
        headers["X-Api-Token"] = tok  # Fog /jobs auth; ignored by local /replay

    req = urllib.request.Request(server + _REPLAY_PATH, data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        archive = resp.read()

    probes_dir = os.path.join(project_dir, "serialized_probes")
    shutil.rmtree(probes_dir, ignore_errors=True)
    n = _archive.unpack_into(archive, probes_dir)
    if n == 0:
        raise RuntimeError("server returned an empty archive — nothing for result()")
    return n
