#!/usr/bin/env python3
"""mult decrypt — Python port of examples/mult/decrypt.cpp (pip niobium_client).

Decrypt the result ciphertext and verify a * b. Usage: decrypt.py [dir [ct_file]].
"""
import sys

from niobium_client import openfhe as o

BIN = o.BINARY


def main(argv):
    d = argv[1] if len(argv) > 1 else "mult_keys"
    ct_file = argv[2] if len(argv) > 2 else "ct_result.bin"

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    assert ok, "cc"
    sk, ok = o.DeserializePrivateKey(f"{d}/sk.bin", BIN)
    assert ok, "sk"
    ct, ok = o.DeserializeCiphertext(f"{d}/{ct_file}", BIN)
    assert ok, ct_file
    with open(f"{d}/values.txt") as fh:
        a, b = (float(x) for x in fh.read().split())

    pt = cc.Decrypt(ct, sk)
    pt.SetLength(1)
    got = pt.GetRealPackedValue()[0]
    exp = a * b
    ok = abs(got - exp) < 0.01
    print(f"[{'PASS' if ok else 'FAIL'}] {a} * {b} = {got:.4f} (expected {exp:.4f})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
