#!/usr/bin/env python3
"""mult server — Python port of examples/mult/server.cpp (pip niobium_sdk).

Deserialize the context/keys/ciphertexts, tag them, record a * b as a FHETCH
trace, replay it locally through the bundled fhetch_sim, serialize the result.

Usage: server.py [dir [niobium-init-flags...]]
"""
import sys

from niobium_sdk import openfhe as o, session as nb

BIN = o.BINARY


def main(argv):
    d = argv[1] if len(argv) > 1 else "mult_keys"
    flags = ["--no-ring-dim-check"] + list(argv[2:])

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    if not ok:
        sys.exit("failed to load crypto context")
    ct_a, ok = o.DeserializeCiphertext(f"{d}/ct_a.bin", BIN)
    assert ok, "ct_a"
    ct_b, ok = o.DeserializeCiphertext(f"{d}/ct_b.bin", BIN)
    assert ok, "ct_b"
    cc.DeserializeEvalMultKey(f"{d}/mk.bin", BIN)
    cc.DeserializeEvalAutomorphismKey(f"{d}/rk.bin", BIN)

    nb.init(flags)
    nb.set_program_info("mult_server", "1.0", "CKKS multiplication — FHETCH trace")
    nb.set_build_info(__file__)
    nb.cache_parameters([("workload", "ckks_mult")])
    nb.capture_crypto_context(cc)
    nb.tag_input("ct_a", ct_a)
    nb.tag_input("ct_b", ct_b)
    nb.tag_keys(cc)

    if not nb.is_cache_valid():
        nb.start()
        nb.probe("result", cc.EvalMult(ct_a, ct_b))
        nb.stop()

    if not nb.replay():
        sys.exit("replay() failed")
    ok, ct_result = nb.result(cc, "result")
    if not ok:
        sys.exit("result() failed")
    o.SerializeToFile(f"{d}/ct_result.bin", ct_result, BIN)
    print(f"server complete -> {d}/ct_result.bin")


if __name__ == "__main__":
    main(sys.argv)
