#!/usr/bin/env python3
"""bootstrap decrypt — Python port of examples/bootstrap/decrypt.cpp.

Verify the bootstrapped output matches the original input within tolerance.
Usage: decrypt.py [dir [ct_file]].
"""
import sys

from niobium_client import openfhe as o

BIN = o.BINARY
EXPECTED = [0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0]


def main(argv):
    d = argv[1] if len(argv) > 1 else "bootstrap_keys"
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
    print(f"[{'PASS' if ok_all else 'FAIL'}] bootstrap: "
          f"{[round(x, 3) for x in got]} (tol {tol:.4g})")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main(sys.argv)
