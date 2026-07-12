#!/usr/bin/env python3
"""plaintext_add decrypt — Python port of examples/plaintext_add/decrypt.cpp.

Verify each of the 10 slots equals 2*(i+1). Usage: decrypt.py [dir [ct_file]].
"""
import sys

from niobium_sdk import openfhe as o

BIN = o.BINARY
EXPECTED = [2.0 * (i + 1) for i in range(10)]


def main(argv):
    d = argv[1] if len(argv) > 1 else "plaintext_add_keys"
    ct_file = argv[2] if len(argv) > 2 else "ct_result.bin"

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    assert ok, "cc"
    sk, ok = o.DeserializePrivateKey(f"{d}/sk.bin", BIN)
    assert ok, "sk"
    ct, ok = o.DeserializeCiphertext(f"{d}/{ct_file}", BIN)
    assert ok, ct_file

    pt = cc.Decrypt(ct, sk)
    pt.SetLength(len(EXPECTED))
    got = pt.GetRealPackedValue()
    tol = max(0.01, 2.0 ** -(pt.GetLogPrecision() - 2))
    ok_all = all(
        (abs(got[i] - exp) / (abs(exp) if abs(exp) > 1.0 else 1.0)) <= tol
        for i, exp in enumerate(EXPECTED)
    )
    print(f"[{'PASS' if ok_all else 'FAIL'}] plaintext_add: "
          f"{[round(x, 3) for x in got]} (tol {tol:.4g})")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main(sys.argv)
