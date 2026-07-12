#!/usr/bin/env python3
"""simple_ops server — Python port of examples/simple_ops/server.cpp.

Deserialize context/keys/ciphertexts, tag them, record the chosen op as a FHETCH
trace, replay locally through the bundled fhetch_sim, serialize the result.

Usage: server.py [dir [op [niobium-init-flags...]]]   (op default: MUL)
"""
import sys

from niobium_sdk import openfhe as o, session as nb

BIN = o.BINARY


def apply_op(cc, op, ct_a, ct_b):
    if op == "ADD":
        return cc.EvalAdd(ct_a, ct_b)
    if op == "SUB":
        return cc.EvalSub(ct_a, ct_b)
    if op == "MUL":
        return cc.EvalMult(ct_a, ct_b)
    if op == "NEG":
        return cc.EvalNegate(ct_a)
    if op == "ADDI":
        return cc.EvalAdd(ct_a, 3.0)
    if op == "SUBI":
        return cc.EvalSub(ct_a, 2.0)
    if op == "MULI":
        return cc.EvalMult(ct_a, 4.0)
    if op == "ADD_ADD":
        return cc.EvalAdd(cc.EvalAdd(ct_a, ct_b), ct_a)
    if op == "ADD_SUB":
        return cc.EvalSub(cc.EvalAdd(ct_a, ct_b), ct_a)
    if op == "MUL_ADD":
        return cc.EvalAdd(cc.EvalMult(ct_a, ct_b), ct_a)
    if op == "ADD_MUL":
        return cc.EvalMult(cc.EvalAdd(ct_a, ct_b), ct_a)
    if op == "MUL_MUL":
        return cc.EvalMult(cc.EvalMult(ct_a, ct_b), ct_a)
    if op == "ALL_NO_MUL":
        t1 = cc.EvalAdd(ct_a, ct_b)     # a + b
        t2 = cc.EvalSub(t1, ct_a)       # b
        t3 = cc.EvalAdd(t2, 3.0)        # b + 3
        t4 = cc.EvalSub(t3, 2.0)        # b + 1
        t5 = cc.EvalMult(t4, 4.0)       # (b + 1) * 4
        return cc.EvalNegate(cc.EvalNegate(t5))
    if op == "MORPH":
        return cc.EvalRotate(ct_a, 1)   # slot 0 -> b
    sys.exit(f"Unknown operation: {op}")


def main(argv):
    d = argv[1] if len(argv) > 1 else "simple_ops_keys"
    op = argv[2] if len(argv) > 2 else "MUL"
    flags = ["--no-ring-dim-check"] + list(argv[3:])

    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    if not ok:
        sys.exit("failed to load crypto context")
    ct_a, ok = o.DeserializeCiphertext(f"{d}/ct_a.bin", BIN)
    assert ok, "ct_a"
    ct_b, ok = o.DeserializeCiphertext(f"{d}/ct_b.bin", BIN)
    assert ok, "ct_b"
    has_mult_key = cc.DeserializeEvalMultKey(f"{d}/mk.bin", BIN)
    cc.DeserializeEvalAutomorphismKey(f"{d}/rk.bin", BIN)

    nb.init(flags)
    nb.set_program_info("simple_ops_server", "1.0", "CKKS simple ops — FHETCH trace")
    nb.set_build_info(__file__)
    nb.cache_parameters([("workload", "simple_ops"), ("op", op)])
    nb.capture_crypto_context(cc)
    nb.tag_input("ct_a", ct_a)
    nb.tag_input("ct_b", ct_b)
    if has_mult_key:
        nb.tag_keys(cc)

    if not nb.is_cache_valid():
        nb.start()
        nb.probe("result", apply_op(cc, op, ct_a, ct_b))
        nb.stop()

    if not nb.replay():
        sys.exit("replay() failed")
    ok, ct_result = nb.result(cc, "result")
    if not ok:
        sys.exit("result() failed")
    o.SerializeToFile(f"{d}/ct_result.bin", ct_result, BIN)
    print(f"server complete ({op}) -> {d}/ct_result.bin")


if __name__ == "__main__":
    main(sys.argv)
