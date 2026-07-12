#!/usr/bin/env python3
"""mult client — Python port of examples/mult/client.cpp (pip niobium_sdk).

Generate a CKKS context + keys, encrypt two values, serialize everything for the
server. Pure OpenFHE (no Niobium session).

Usage: client.py [dir [a [b [ring_dim]]]]   (defaults: mult_keys, 7.0, 13.0, 2048)
"""
import os
import sys

from niobium_sdk import openfhe as o

BIN = o.BINARY


def main(argv):
    d = argv[1] if len(argv) > 1 else "mult_keys"
    a = float(argv[2]) if len(argv) > 2 else 7.0
    b = float(argv[3]) if len(argv) > 3 else 13.0
    ring_dim = int(argv[4]) if len(argv) > 4 else 2048
    os.makedirs(d, exist_ok=True)

    # CKKS params (compiler TOY defaults).
    p = o.CCParamsCKKSRNS()
    p.SetSecurityLevel(o.SecurityLevel.HEStd_NotSet)
    p.SetRingDim(ring_dim)
    p.SetMultiplicativeDepth(3)
    p.SetScalingModSize(42)
    p.SetFirstModSize(57)
    p.SetScalingTechnique(o.FLEXIBLEAUTO)
    cc = o.GenCryptoContext(p)
    for feat in (o.PKE, o.KEYSWITCH, o.LEVELEDSHE, o.ADVANCEDSHE):
        cc.Enable(feat)

    kp = cc.KeyGen()
    cc.EvalMultKeyGen(kp.secretKey)
    cc.EvalRotateKeyGen(kp.secretKey, [1, -1])   # so the server exercises rot-key loading

    o.SerializeToFile(f"{d}/cc.bin", cc, BIN)
    o.SerializeToFile(f"{d}/pk.bin", kp.publicKey, BIN)
    o.SerializeToFile(f"{d}/sk.bin", kp.secretKey, BIN)
    cc.SerializeEvalMultKey(f"{d}/mk.bin", BIN, "")
    cc.SerializeEvalAutomorphismKey(f"{d}/rk.bin", BIN, "")

    o.SerializeToFile(f"{d}/ct_a.bin", cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([a])), BIN)
    o.SerializeToFile(f"{d}/ct_b.bin", cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([b])), BIN)
    with open(f"{d}/values.txt", "w") as fh:
        fh.write(f"{a} {b}\n")
    print(f"client complete (a={a}, b={b}) -> {d}/")


if __name__ == "__main__":
    main(sys.argv)
