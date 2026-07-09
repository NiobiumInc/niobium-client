"""niobium_client — Niobium FHE client (Python).

Public surface:
- ``from niobium_client import openfhe``  — crypto (vendored openfhe-python,
  rebuilt against Niobium's instrumented OpenFHE); recording fires from the C++
  core.
- ``from niobium_client import session`` — the ``niobium::compiler()``
  record/replay session API; local ``replay()`` via the bundled ``fhetch_sim``.
- ``from niobium_client import client``  — ``submit()`` / ``configure()`` cloud
  transport (pure Python).
- ``from niobium_client import nbc``     — the ``.niob`` DSL compiler (pure Python;
  CLI: ``python -m niobium_client.nbc``).

The crypto/session extensions (``openfhe``/``niobium_session``) are imported on
demand, so DSL-only (``nbc``) and submit-only (``client``) use does not require
the native stack to load.
"""
import ctypes
import glob
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _preload_libnbfhetch():
    """Load libnbfhetch into the global symbol namespace before ``openfhe``.

    The instrumented-OpenFHE probe hooks compiled into ``openfhe`` (under
    ``OPENFHE_CPROBES``) resolve ``g_replay_mode`` from libnbfhetch, which is not
    a declared dependency of the ``openfhe`` extension — so it must already be
    RTLD_GLOBAL-loaded when ``openfhe`` is imported. This coupling is invisible
    to delocate/auditwheel, hence the explicit preload. No-op in a source tree
    where the bundled lib isn't present (e.g. the standalone _archive build).
    """
    for sub in ("", ".dylibs", os.path.join(os.pardir, "niobium_client.libs")):
        for lib in sorted(glob.glob(os.path.join(_HERE, sub, "libnbfhetch*"))):
            return ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
    return None


_preload_libnbfhetch()

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
