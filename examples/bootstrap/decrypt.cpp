// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Bootstrap example — decrypt and verify
//
// Loads the CKKS crypto context, secret key, and result ciphertext
// produced by the server. Decrypts and verifies the bootstrapped output.
//
// Usage: ./bootstrap_decrypt [key_dir]

#include "openfhe.h"

#include <cmath>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "bootstrap_keys";
    if (argc > 1) keyDir = argv[1];

    std::cout << "=== CKKS Bootstrap — Decrypt & Verify ===" << std::endl;
    std::cout << "Loading from: " << keyDir << std::endl;

    // ---- Load crypto context ----
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    // ---- Load secret key ----
    PrivateKey<DCRTPoly> secretKey;
    if (!Serial::DeserializeFromFile(keyDir + "/sk.bin", secretKey, SerType::BINARY))
        throw std::runtime_error("Failed to load secret key");

    // ---- Load result ciphertext ----
    Ciphertext<DCRTPoly> ct_result;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY))
        throw std::runtime_error("Failed to load result ciphertext");

    // ---- Load depth ----
    usint depth = 0;
    {
        std::ifstream depthStream(keyDir + "/depth.txt");
        if (!depthStream.is_open())
            throw std::runtime_error("Failed to load depth");
        depthStream >> depth;
    }

    // ---- Decrypt ----
    size_t encodedLength = 8;
    Plaintext result;
    cc->Decrypt(secretKey, ct_result, &result);
    result->SetLength(encodedLength);

    std::cout << "Levels remaining after bootstrap: "
              << depth - ct_result->GetLevel()
                 - (ct_result->GetNoiseScaleDeg() - 1)
              << std::endl;
    std::cout << "Output after bootstrapping:\n\t" << result << std::endl;

    // ---- Verify ----
    std::vector<double> expected = {0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0};
    auto decoded = result->GetRealPackedValue();

    double logPrecision = result->GetLogPrecision() - 2;
    const double tolerance = std::max(0.01, std::pow(2.0, -logPrecision));
    std::cout << "Tolerance: " << tolerance
              << " (log precision: " << logPrecision << ")" << std::endl;

    bool success = true;
    for (size_t i = 0; i < expected.size(); i++) {
        double diff = std::abs(decoded[i] - expected[i]);
        if (std::abs(expected[i]) > 1.0)
            diff /= std::abs(expected[i]);
        if (diff > tolerance) {
            std::cerr << "[ERROR] Mismatch at index " << i
                      << ": expected " << expected[i]
                      << ", got " << decoded[i]
                      << " (diff = " << diff << ")" << std::endl;
            success = false;
        }
    }

    if (success) {
        std::cout << "[PASS] All values match expected within tolerance" << std::endl;
        return 0;
    } else {
        std::cerr << "[FAIL] Output does not match expected values" << std::endl;
        return 1;
    }
}
