#!/usr/bin/env python3
"""bootstrap server — Python port of examples/bootstrap/server.cpp.

Deserialize context/keys/ciphertext, re-run bootstrap precompute, record
EvalBootstrap (hollow mode by default), replay locally, serialize the result.

Usage: server.py [dir [niobium-init-flags...]]
  NIOBIUM_BOOTSTRAP_HOLLOW=0 forces real-math recording (default: hollow).
"""
import os
import sys

from niobium_client import openfhe as o, session as nb

BIN = o.BINARY
LEVEL_BUDGET = [4, 4]


def main(argv):
    d = argv[1] if len(argv) > 1 else "bootstrap_keys"
    flags = ["--no-ring-dim-check"] + list(argv[2:])

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    if not ok:
        sys.exit("failed to load crypto context")
    if not cc.DeserializeEvalMultKey(f"{d}/mk.bin", BIN):
        sys.exit("failed to load eval mult key")
    if not cc.DeserializeEvalAutomorphismKey(f"{d}/rk.bin", BIN):
        sys.exit("failed to load eval automorphism keys")
    ciph, ok = o.DeserializeCiphertext(f"{d}/ciphertext.bin", BIN)
    assert ok, "ciphertext"

    cc.EvalBootstrapSetup(LEVEL_BUDGET)   # re-run precompute (fires precompute probes)

    nb.init(flags)
    nb.set_program_info("bootstrap_server", "1.0", "CKKS bootstrapping")
    nb.set_build_info(__file__)
    nb.cache_parameters([("workload", "ckks_bootstrap")])
    nb.capture_crypto_context(cc)
    nb.tag_input("input_cipher", ciph)
    nb.tag_keys(cc)

    hollow = os.environ.get("NIOBIUM_BOOTSTRAP_HOLLOW", "1") != "0"
    if not nb.is_cache_valid():
        print(f"recording bootstrap ({'hollow' if hollow else 'real'} mode)...")
        nb.enable_hollow_mode(hollow)
        nb.start()
        nb.probe("output_cipher", cc.EvalBootstrap(ciph))
        nb.stop()
        nb.enable_hollow_mode(False)

    if not nb.replay():
        sys.exit("replay() failed")
    ok, ct_result = nb.result(cc, "output_cipher")
    if not ok:
        sys.exit("result() failed")
    o.SerializeToFile(f"{d}/ct_result.bin", ct_result, BIN)
    print(f"server complete -> {d}/ct_result.bin")


if __name__ == "__main__":
    main(sys.argv)
