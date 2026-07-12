#!/usr/bin/env python3
"""Negative test for the Niobium ring-dimension hardware check.

Python analog of the C++ test-ring-dim-check-release. With the check enabled
(i.e. WITHOUT --no-ring-dim-check), capturing a crypto context whose ring
dimension is incompatible with Niobium hardware (2048) must raise. The guard
lives in libnbfhetch (Compiler::set_ring_dimension) and is reached from Python
via capture_crypto_context(), which derives N from the context.
"""
import sys

from niobium_sdk import openfhe as o, session as nb

RING_DIM = 2048  # small / HW-incompatible on purpose


def make_context(ring_dim):
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
    return cc


def main():
    cc = make_context(RING_DIM)

    # Check ENABLED: init with no flags (no --no-ring-dim-check).
    nb.init([])
    nb.set_program_info("ring_dim_check_smoke", "1.0", "ring-dim guard negative test")
    nb.cache_parameters([("workload", "ring_dim_check")])

    try:
        nb.capture_crypto_context(cc)
    except RuntimeError as e:
        if "not compatible with Niobium Hardware" in str(e):
            print(f"[PASS] ring-dim check rejected ring dimension {RING_DIM}")
            return 0
        print(f"[FAIL] rejected, but with an unexpected message: {e}")
        return 1
    print(f"[FAIL] ring dimension {RING_DIM} was accepted despite the check being on")
    return 1


if __name__ == "__main__":
    sys.exit(main())
