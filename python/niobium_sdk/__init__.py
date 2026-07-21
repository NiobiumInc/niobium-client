"""niobium_sdk — Niobium FHE client (Python).

Public surface:
- ``from niobium_sdk import openfhe``  — crypto (vendored openfhe-python,
  rebuilt against Niobium's instrumented OpenFHE); recording fires from the C++
  core.
- ``from niobium_sdk import session`` — the ``niobium::compiler()``
  record/replay session API; local ``replay()`` via the bundled ``fhetch_sim``.
- ``from niobium_sdk import client``  — ``submit()`` / ``configure()`` cloud
  transport (pure Python).
- ``from niobium_sdk import nbc``     — the ``.niob`` DSL compiler (pure Python;
  CLI: ``python -m niobium_sdk.nbc``).

The crypto/session extensions (``openfhe``/``niobium_session``) are imported on
demand, so DSL-only (``nbc``) and submit-only (``client``) use does not require
the native stack to load.
"""
import contextlib
import ctypes
import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _lib_dirs():
    """Dirs that hold the bundled native libs: the package itself + the vendored-lib
    dir that auditwheel (``niobium_sdk.libs``, a sibling) / delocate (``.dylibs``)
    create during wheel repair. Only existing dirs, package dir first."""
    dirs = [_HERE]
    for sub in (".dylibs", os.path.join(os.pardir, "niobium_sdk.libs")):
        d = os.path.normpath(os.path.join(_HERE, sub))
        if os.path.isdir(d):
            dirs.append(d)
    return dirs


_LIBDIRS = _lib_dirs()


def _preload_libnbfhetch():
    """Load libnbfhetch into the global symbol namespace before ``openfhe``.

    The instrumented-OpenFHE probe hooks compiled into ``openfhe`` (under
    ``OPENFHE_CPROBES``) resolve ``g_replay_mode`` from libnbfhetch, which is not
    a declared dependency of the ``openfhe`` extension — so it must already be
    RTLD_GLOBAL-loaded when ``openfhe`` is imported. This coupling is invisible
    to delocate/auditwheel, hence the explicit preload. No-op in a source tree
    where the bundled lib isn't present (e.g. the standalone _archive build).
    """
    for d in _LIBDIRS:
        for lib in sorted(glob.glob(os.path.join(d, "libnbfhetch*"))):
            return ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
    return None


_preload_libnbfhetch()


@contextlib.contextmanager
def _sim_library_path():
    """Temporarily prepend the bundled native-lib dirs to the loader search path.

    fhetch_sim runs as a *subprocess* (posix_spawn'd by the C++ ``replay()``,
    inheriting this process's environment), so it needs DYLD/LD_LIBRARY_PATH to
    point at our bundled libnbfhetch + OpenFHE. Scoped to the ``replay()`` call
    (see ``niobium_sdk.session.replay``) and restored afterward, rather than set
    globally at import — so the bundled libs never leak into an unrelated child
    process the host may spawn. (The extensions themselves resolve their deps via
    their $ORIGIN/@loader_path RPATHs + the RTLD_GLOBAL preload above, not this.)
    No-op when nothing is bundled (source tree). Not reentrant across threads.
    """
    if not _LIBDIRS:
        yield
        return
    var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    prev = os.environ.get(var)
    os.environ[var] = os.pathsep.join(_LIBDIRS + ([prev] if prev else []))
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = prev


# Point replay() at the bundled fhetch_sim unless the caller overrode it.
_SIM = os.path.join(_HERE, "fhetch_sim")
if os.path.exists(_SIM):
    os.environ.setdefault("NBCC_FHETCH_SIM", _SIM)


def _read_version():
    try:
        with open(os.path.join(_HERE, "VERSION")) as fh:
            return fh.read().strip()
    except OSError:
        return "0+unknown"


__version__ = _read_version()

from . import client  # noqa: E402  (pure Python; safe without the native stack)

__all__ = ["client", "openfhe", "session", "nbc", "__version__"]
