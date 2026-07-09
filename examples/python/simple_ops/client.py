#!/usr/bin/env python3
"""simple_ops client — Python port of examples/simple_ops/client.cpp.

Generate a CKKS context + keys, encrypt two values, serialize for the server.
ct_a packs (a, b) so EvalRotate(ct_a, 1)[0] == b (for the MORPH op); ct_b = (b).

Usage: client.py [dir [a [b]]]   (defaults: simple_ops_keys, 5.0, 6.0)
"""
import os
import sys

from niobium_client import openfhe as o

BIN = o.BINARY


def main(argv):
    d = argv[1] if len(argv) > 1 else "simple_ops_keys"
    a = float(argv[2]) if len(argv) > 2 else 5.0
    b = float(argv[3]) if len(argv) > 3 else 6.0
    os.makedirs(d, exist_ok=True)

    p = o.CCParamsCKKSRNS()
    p.SetSecurityLevel(o.SecurityLevel.HEStd_NotSet)
    p.SetRingDim(2048)
    p.SetMultiplicativeDepth(3)
    p.SetScalingModSize(42)
    p.SetFirstModSize(57)
    p.SetScalingTechnique(o.FLEXIBLEAUTO)
    cc = o.GenCryptoContext(p)
    for feat in (o.PKE, o.KEYSWITCH, o.LEVELEDSHE, o.ADVANCEDSHE):
        cc.Enable(feat)

    kp = cc.KeyGen()
    cc.EvalMultKeyGen(kp.secretKey)
    cc.EvalRotateKeyGen(kp.secretKey, [1, -1])   # for MORPH (EvalRotate ±1)

    o.SerializeToFile(f"{d}/cc.bin", cc, BIN)
    o.SerializeToFile(f"{d}/pk.bin", kp.publicKey, BIN)
    o.SerializeToFile(f"{d}/sk.bin", kp.secretKey, BIN)
    cc.SerializeEvalMultKey(f"{d}/mk.bin", BIN, "")
    cc.SerializeEvalAutomorphismKey(f"{d}/rk.bin", BIN, "")

    o.SerializeToFile(f"{d}/ct_a.bin", cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([a, b])), BIN)
    o.SerializeToFile(f"{d}/ct_b.bin", cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([b])), BIN)
    with open(f"{d}/values.txt", "w") as fh:
        fh.write(f"{a} {b}\n")
    print(f"client complete -> {d}/")


if __name__ == "__main__":
    main(sys.argv)
