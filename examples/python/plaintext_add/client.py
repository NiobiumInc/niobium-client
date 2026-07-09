#!/usr/bin/env python3
"""plaintext_add client — Python port of examples/plaintext_add/client.cpp.

Generate a CKKS context + keys, encrypt [1..10], serialize for the server.
Usage: client.py [dir]   (default: plaintext_add_keys)
"""
import os
import sys

from niobium_client import openfhe as o

BIN = o.BINARY
INPUT = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]


def main(argv):
    d = argv[1] if len(argv) > 1 else "plaintext_add_keys"
    os.makedirs(d, exist_ok=True)

    p = o.CCParamsCKKSRNS()
    p.SetSecretKeyDist(o.UNIFORM_TERNARY)
    p.SetSecurityLevel(o.SecurityLevel.HEStd_NotSet)
    p.SetRingDim(2048)
    p.SetScalingModSize(59)
    p.SetScalingTechnique(o.FLEXIBLEAUTO)
    p.SetFirstModSize(60)
    p.SetMultiplicativeDepth(2)
    cc = o.GenCryptoContext(p)
    for feat in (o.PKE, o.KEYSWITCH, o.LEVELEDSHE):
        cc.Enable(feat)

    kp = cc.KeyGen()
    o.SerializeToFile(f"{d}/cc.bin", cc, BIN)
    o.SerializeToFile(f"{d}/pk.bin", kp.publicKey, BIN)
    o.SerializeToFile(f"{d}/sk.bin", kp.secretKey, BIN)
    o.SerializeToFile(f"{d}/ciphertext.bin",
                      cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext(INPUT)), BIN)
    print(f"client complete -> {d}/")


if __name__ == "__main__":
    main(sys.argv)
