"""niobium_sdk.session — the niobium::compiler() record/replay API.

Thin re-export of the compiled ``niobium_session`` extension. Importing this
module (a submodule of ``niobium_sdk``) runs the package ``__init__`` first,
which RTLD_GLOBAL-preloads libnbfhetch so the extension's probe globals resolve.

    from niobium_sdk import session
    session.init(["--no-ring-dim-check"])
    session.start(); ...; session.stop()
    ok, ct = session.replay(...)   # local, via the bundled fhetch_sim
"""
from .niobium_session import *  # noqa: F401,F403
from . import niobium_session as _impl
from . import _sim_library_path


def replay(*args, **kwargs):
    """Replay the recorded trace via the bundled fhetch_sim (local target).

    Thin wrapper over the native ``replay`` that scopes the bundled-lib loader
    path to just the subprocess launch — see ``niobium_sdk._sim_library_path``.
    """
    with _sim_library_path():
        return _impl.replay(*args, **kwargs)


__all__ = [n for n in dir(_impl) if not n.startswith("_")]
