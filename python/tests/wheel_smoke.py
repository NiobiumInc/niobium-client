#!/usr/bin/env python3
"""Primary-only smoke for the INSTALLED niobium_client wheel.

Runs the real path end-to-end against the installed package — no build tree, no
PYTHONPATH hacks, no fhetch_driver secondary:

    serialize -> deserialize -> record -> replay() (bundled fhetch_sim) -> decrypt

Mirrors niobium-client's C++ `run-simple-op` (primary-only); the fhetch_driver
re-drive cross-check is the niobium-fhetch dev repo's job. Imports come straight
from the package, so this also exercises the __init__ RTLD_GLOBAL preload and the
namespaced surface (openfhe / session).

Run after `pip install niobium_client-*.whl`:  python wheel_smoke.py
"""
import os
import shutil
import sys
import tempfile

from niobium_client import openfhe as o, session as nb  # noqa: E402

BIN = o.BINARY


def build_inputs(d, a, b):
    """Client side: CKKS context + keys, encrypt (a,b), serialize everything."""
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

    o.SerializeToFile(f"{d}/cc.bin", cc, BIN)
    o.SerializeToFile(f"{d}/sk.bin", kp.secretKey, BIN)
    cc.SerializeEvalMultKey(f"{d}/mk.bin", BIN, "")
    o.SerializeToFile(f"{d}/ct_a.bin",
                      cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([a, b])), BIN)
    o.SerializeToFile(f"{d}/ct_b.bin",
                      cc.Encrypt(kp.publicKey, cc.MakeCKKSPackedPlaintext([b])), BIN)


def record_and_replay(d):
    """Server side: deserialize, record an EvalMult, replay via fhetch_sim."""
    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    assert ok, "cc"
    ct_a, ok = o.DeserializeCiphertext(f"{d}/ct_a.bin", BIN)
    assert ok, "ct_a"
    ct_b, ok = o.DeserializeCiphertext(f"{d}/ct_b.bin", BIN)
    assert ok, "ct_b"
    cc.DeserializeEvalMultKey(f"{d}/mk.bin", BIN)

    nb.init(["--no-ring-dim-check"])
    nb.set_program_info("wheel_smoke", "1.0", "primary-only wheel smoke (EvalMult)")
    nb.set_build_info(__file__)
    nb.cache_parameters([("workload", "wheel_smoke"), ("op", "MUL")])
    nb.capture_crypto_context(cc)
    nb.tag_input("ct_a", ct_a)
    nb.tag_input("ct_b", ct_b)
    nb.tag_keys(cc)

    if not nb.is_cache_valid():
        nb.start()
        nb.probe("result", cc.EvalMult(ct_a, ct_b))
        nb.stop()

    assert nb.replay(), "replay() failed"
    ok, ct = nb.result(cc, "result")
    assert ok, "result()"
    o.SerializeToFile(f"{d}/ct_result.bin", ct, BIN)


def _fresh_process():
    """Simulate a new process between phases.

    The real client/server/decrypt split runs in separate processes, each with an
    empty in-process crypto-context + eval-key registry. This single-process smoke
    must clear those registries between phases, else re-deserializing the context
    or its EvalMult key collides with the entries an earlier phase registered.
    """
    o.ClearEvalMultKeys()
    o.ReleaseAllContexts()


def decrypt_check(d, a, b):
    """Decrypt side: decrypt the replayed result and check slot 0 == a*b."""
    cc, ok = o.DeserializeCryptoContext(f"{d}/cc.bin", BIN)
    assert ok, "cc"
    sk, ok = o.DeserializePrivateKey(f"{d}/sk.bin", BIN)
    assert ok, "sk"
    ct, ok = o.DeserializeCiphertext(f"{d}/ct_result.bin", BIN)
    assert ok, "ct_result"
    pt = cc.Decrypt(ct, sk)
    pt.SetLength(1)
    return pt.GetRealPackedValue()[0]


def main():
    a, b = 5.0, 6.0
    work = tempfile.mkdtemp(prefix="nb_wheel_smoke_")
    cwd = os.getcwd()
    try:
        os.chdir(work)
        build_inputs(".", a, b)          # "client" process
        _fresh_process()
        record_and_replay(".")           # "server" process
        _fresh_process()
        got = decrypt_check(".", a, b)   # "decrypt" process
    finally:
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)

    exp = a * b
    ok = abs(got - exp) < 0.01
    print(f"niobium_client {getattr(__import__('niobium_client'), '__version__', '?')} "
          f"wheel smoke: [{'PASS' if ok else 'FAIL'}] MUL {got:.4f} ~= {exp:.4f}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
