#!/usr/bin/env python3
"""simple_ops decrypt — Python port of examples/simple_ops/decrypt.cpp.

Decrypt the result and check slot 0 against the expected value for the op.
Usage: decrypt.py [dir [op [ct_file]]]   (op default: MUL)
"""
import sys

from niobium_sdk import openfhe as o

BIN = o.BINARY

EXPECTED = {
    "ADD": lambda a, b: a + b,
    "SUB": lambda a, b: a - b,
    "MUL": lambda a, b: a * b,
    "NEG": lambda a, b: -a,
    "ADDI": lambda a, b: a + 3.0,
    "SUBI": lambda a, b: a - 2.0,
    "MULI": lambda a, b: a * 4.0,
    "ADD_ADD": lambda a, b: 2 * a + b,
    "ADD_SUB": lambda a, b: b,
    "MUL_ADD": lambda a, b: a * b + a,
    "ADD_MUL": lambda a, b: (a + b) * a,
    "MUL_MUL": lambda a, b: a * b * a,
    "ALL_NO_MUL": lambda a, b: (b + 1.0) * 4.0,
    "MORPH": lambda a, b: b,
}


def main(argv):
    d = argv[1] if len(argv) > 1 else "simple_ops_keys"
    op = argv[2] if len(argv) > 2 else "MUL"
    ct_file = argv[3] if len(argv) > 3 else "ct_result.bin"

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
    exp = EXPECTED[op](a, b)
    ok = abs(got - exp) < 0.01
    print(f"[{'PASS' if ok else 'FAIL'}] {op}: {got:.4f} ~= {exp:.4f}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main(sys.argv)
