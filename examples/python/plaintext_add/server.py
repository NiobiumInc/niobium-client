#!/usr/bin/env python3
"""plaintext_add server — Python port of examples/plaintext_add/server.cpp.

Deserialize the context + ciphertext, encode the server-side plaintext [1..10],
record EvalAdd(ciphertext, plaintext), replay locally, serialize the result.

Usage: server.py [dir [niobium-init-flags...]]
"""
import sys

from niobium_client import openfhe as o, session as nb

BIN = o.BINARY
INPUT = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def main(argv):
    d = argv[1] if len(argv) > 1 else "plaintext_add_keys"
    flags = ["--no-ring-dim-check"] + list(argv[2:])

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    if not ok:
        sys.exit("failed to load crypto context")
    ciph, ok = o.DeserializeCiphertext(f"{d}/ciphertext.bin", BIN)
    assert ok, "ciphertext"
    pt = cc.MakeCKKSPackedPlaintext(INPUT)

    nb.init(flags)
    nb.set_program_info("plaintext_add_server", "1.0", "CKKS plaintext+ciphertext add")
    nb.set_build_info(__file__)
    nb.cache_parameters([("workload", "ckks_plaintext_add")])
    nb.capture_crypto_context(cc)
    nb.tag_input("input_cipher", ciph)
    nb.tag_input("input_plaintext", pt)

    if not nb.is_cache_valid():
        nb.start()
        nb.probe("output_cipher", cc.EvalAdd(ciph, pt))
        nb.stop()

    if not nb.replay():
        sys.exit("replay() failed")
    ok, ct_result = nb.result(cc, "output_cipher")
    if not ok:
        sys.exit("result() failed")
    o.SerializeToFile(f"{d}/ct_result.bin", ct_result, BIN)
    print(f"server complete -> {d}/ct_result.bin")


if __name__ == "__main__":
    main(sys.argv)
