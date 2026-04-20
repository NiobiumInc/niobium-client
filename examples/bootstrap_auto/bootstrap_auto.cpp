// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// bootstrap_auto.cpp — Auto-facade CKKS bootstrapping example.
//
// Loads a pre-generated CryptoContext, keys, and encrypted ciphertext from a
// keys directory (produced by bootstrap_client), then bootstraps the
// ciphertext and verifies the result.
//
// This file contains ZERO Niobium-specific code — everything is standard
// OpenFHE. The auto-facade (enabled by OPENFHE_CPROBES, which libnbfhetch
// already defines publicly) silently intercepts:
//
//   Serial::DeserializeFromFile<CryptoContext> → swaps in the recording /
//                                                replay scheme proxy and
//                                                fires lazy_init.
//   Serial::DeserializeFromFile<Ciphertext>    → registers the ciphertext as
//                                                a tagged input for replay.
//   Serial::SerializeToFile<Ciphertext>        → probes the output into the
//                                                trace (recording mode) or
//                                                writes the reconstructed
//                                                ciphertext to file (replay).
//
// First run (no cache): records the bootstrapping instruction trace, runs
// the simulator, writes the reconstructed ciphertext to RESULT_BIN.
// Second run (cache hit): skips re-recording; replay populates RESULT_BIN
// from the stored trace + captured inputs.
//
// Ported from niobium-compiler/examples/auto/bootstrap_auto.cpp. Adjusted
// to consume the keys produced by niobium-client's bootstrap_client (same
// expected plaintext vector, same ring dimension, same level budget).
//
// Usage: ./bootstrap_auto [key_dir]

#include "openfhe.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "bootstrap_auto_keys";
    if (argc > 1) keyDir = argv[1];

    const std::string prefix     = keyDir + "/";
    const std::string resultBin  = keyDir + "/ct_result.bin";

    std::cout << "=== CKKS Bootstrap — Auto-Facade ===" << std::endl;
    std::cout << "Key directory: " << keyDir << std::endl;

    // -------------------------------------------------------------------------
    // Load CryptoContext — triggers the auto-facade hook: the FHE scheme is
    // replaced by the Niobium proxy so that every subsequent Eval* call either
    // records (first run) or short-circuits to the captured HW/sim output
    // (second run).
    // -------------------------------------------------------------------------
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(prefix + "cc.bin", cc, SerType::BINARY)) {
        std::cerr << "Failed to load cc from " << prefix << "cc.bin\n"
                  << "Run bootstrap_client first.\n";
        return 1;
    }

    // -------------------------------------------------------------------------
    // Evaluation keys — loaded via direct stream so they populate the CC
    // without going through the auto-facade's DeserializeFromFile hook (which
    // only intercepts whole-object deserialization).
    // -------------------------------------------------------------------------
    {
        std::ifstream f(prefix + "mk.bin", std::ios::binary);
        if (!f || !cc->DeserializeEvalMultKey(f, SerType::BINARY)) {
            std::cerr << "Failed to load mk\n";
            return 1;
        }
    }
    {
        std::ifstream f(prefix + "rk.bin", std::ios::binary);
        if (!f || !cc->DeserializeEvalAutomorphismKey(f, SerType::BINARY)) {
            std::cerr << "Failed to load rk\n";
            return 1;
        }
    }

    // Regenerate bootstrap precompute plaintexts (not stored in the CC
    // serialization). The auto-facade routes EvalBootstrapPrecompute through
    // a ScopedPause + direct forward to the real FHECKKSRNS implementation,
    // so the polynomial ops it emits are excluded from the trace.
    std::vector<uint32_t> levelBudget = {4, 4};
    cc->EvalBootstrapSetup(levelBudget);

    // -------------------------------------------------------------------------
    // Load input ciphertext — the auto-facade tags this as a named input so
    // the simulator can populate its memory during replay.
    // -------------------------------------------------------------------------
    Ciphertext<DCRTPoly> ct;
    if (!Serial::DeserializeFromFile(prefix + "ciphertext.bin", ct, SerType::BINARY)) {
        std::cerr << "Failed to load input from " << prefix << "ciphertext.bin\n";
        return 1;
    }

    // -------------------------------------------------------------------------
    // Bootstrap. In recording mode this runs the full software pass and emits
    // the instruction trace; in replay mode the proxy returns a placeholder
    // and the serialized result below is filled from the simulator output.
    // -------------------------------------------------------------------------
    std::cout << "Running EvalBootstrap..." << std::endl;
    auto result = cc->EvalBootstrap(ct);

    // -------------------------------------------------------------------------
    // Serialize result. In recording mode this fires the output probe AND
    // writes the software-computed ciphertext; in replay mode it writes the
    // HW/sim-computed ciphertext directly.
    // -------------------------------------------------------------------------
    if (!Serial::SerializeToFile(resultBin, result, SerType::BINARY)) {
        std::cerr << "Failed to serialise result to " << resultBin << "\n";
        return 1;
    }

    // -------------------------------------------------------------------------
    // Read the result back from file and decrypt. In replay mode the
    // in-memory `result` holds the placeholder; the file contains the real
    // reconstructed ciphertext.
    // -------------------------------------------------------------------------
    Ciphertext<DCRTPoly> final_result;
    if (!Serial::DeserializeFromFile(resultBin, final_result, SerType::BINARY)) {
        std::cerr << "Failed to reload result\n";
        return 1;
    }

    PrivateKey<DCRTPoly> sk;
    if (!Serial::DeserializeFromFile(prefix + "sk.bin", sk, SerType::BINARY)) {
        std::cerr << "Failed to load sk\n";
        return 1;
    }

    Plaintext pt;
    cc->Decrypt(sk, final_result, &pt);

    // Same plaintext the client encodes in examples/bootstrap/client.cpp.
    const std::vector<double> expected = {0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0};
    pt->SetLength(expected.size());
    std::cout << "Output after bootstrapping:\n\t" << pt << std::endl;

    const double logPrecision = pt->GetLogPrecision() - 2;
    const double tolerance    = std::max(0.01, std::pow(2.0, -logPrecision));
    std::cout << std::defaultfloat
              << "Tolerance: " << tolerance
              << " (log precision: " << logPrecision << ")" << std::endl;

    auto decoded = pt->GetCKKSPackedValue();
    bool ok = true;
    for (size_t i = 0; i < expected.size(); ++i) {
        double diff = std::abs(decoded[i].real() - expected[i]);
        if (std::abs(expected[i]) > 1.0)
            diff /= std::abs(expected[i]);
        if (diff > tolerance) {
            std::cerr << "[ERROR] index " << i << ": expected " << expected[i]
                      << ", got " << decoded[i].real()
                      << " (diff=" << diff << ")\n";
            ok = false;
        }
    }

    if (ok) {
        std::cout << "[PASS] All values match within tolerance "
                  << tolerance << std::endl;
        return 0;
    }
    std::cerr << "[FAIL] Output does not match expected values" << std::endl;
    return 1;
}
