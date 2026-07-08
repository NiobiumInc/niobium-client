"""niobium_client — Niobium FHE client (Python).

Public surface (per the distribution plan): `from niobium_client import client`
(record + crypto + replay + submit) and `from niobium_client import nbc` (the
pure-Python DSL). This early package ships the submit() path + the _archive
binding; the crypto/session modules + nbc are assembled during wheel packaging.
"""
from . import client

__all__ = ["client"]
