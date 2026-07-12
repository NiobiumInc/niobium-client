#!/usr/bin/env python3
"""bootstrap client — Python port of examples/bootstrap/client.cpp.

Generate a bootstrapping-capable CKKS context + keys + bootstrap precompute,
encrypt a test vector at the deepest level (so bootstrapping is required), and
serialize for the server. Usage: client.py [dir]   (default: bootstrap_keys)
"""
import os
import sys

from niobium_sdk import openfhe as o

BIN = o.BINARY
INPUT = [0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0]
LEVEL_BUDGET = [4, 4]
LEVELS_AFTER_BOOTSTRAP = 10


def main(argv):
    d = argv[1] if len(argv) > 1 else "bootstrap_keys"
    os.makedirs(d, exist_ok=True)

    depth = LEVELS_AFTER_BOOTSTRAP + o.FHECKKSRNS.GetBootstrapDepth(LEVEL_BUDGET, o.UNIFORM_TERNARY)
    p = o.CCParamsCKKSRNS()
    p.SetSecretKeyDist(o.UNIFORM_TERNARY)
    p.SetSecurityLevel(o.SecurityLevel.HEStd_NotSet)
    p.SetRingDim(2048)
    p.SetScalingModSize(59)
    p.SetScalingTechnique(o.FLEXIBLEAUTO)
    p.SetFirstModSize(60)
    p.SetMultiplicativeDepth(depth)
    cc = o.GenCryptoContext(p)
    for feat in (o.PKE, o.KEYSWITCH, o.LEVELEDSHE, o.ADVANCEDSHE, o.FHE):
        cc.Enable(feat)

    num_slots = cc.GetRingDimension() // 2
    cc.EvalBootstrapSetup(LEVEL_BUDGET)
    kp = cc.KeyGen()
    cc.EvalMultKeyGen(kp.secretKey)
    cc.EvalBootstrapKeyGen(kp.secretKey, num_slots)

    o.SerializeToFile(f"{d}/cc.bin", cc, BIN)
    o.SerializeToFile(f"{d}/pk.bin", kp.publicKey, BIN)
    o.SerializeToFile(f"{d}/sk.bin", kp.secretKey, BIN)
    cc.SerializeEvalMultKey(f"{d}/mk.bin", BIN, "")
    cc.SerializeEvalAutomorphismKey(f"{d}/rk.bin", BIN, "")

    pt = cc.MakeCKKSPackedPlaintext(INPUT, 1, depth - 1)   # deepest level
    o.SerializeToFile(f"{d}/ciphertext.bin", cc.Encrypt(kp.publicKey, pt), BIN)
    with open(f"{d}/depth.txt", "w") as fh:
        fh.write(f"{depth}\n")
    print(f"client complete (depth={depth}) -> {d}/")


if __name__ == "__main__":
    main(sys.argv)
