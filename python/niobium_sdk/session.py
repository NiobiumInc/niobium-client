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

__all__ = [n for n in dir(_impl) if not n.startswith("_")]
