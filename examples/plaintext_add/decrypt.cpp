// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Plaintext-add example — decrypt and verify
//
// Loads the CKKS crypto context, secret key, and result ciphertext produced
// by the server. Decrypts and verifies that each of the 10 meaningful slots
// equals 2 * (i+1), i.e. [2, 4, 6, 8, 10, 12, 14, 16, 18, 20].
//
// Usage: ./plaintext_add_decrypt [key_dir [ct_file]]

#include "openfhe.h"

#include <cmath>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "plaintext_add_keys";
    std::string ct_file = "ct_result.bin";
    if (argc > 1) keyDir = argv[1];
    if (argc > 2) ct_file = argv[2];

    std::cout << "=== CKKS Plaintext-Add — Decrypt & Verify (ct=" << ct_file << ") ===" << std::endl;
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
    if (!Serial::DeserializeFromFile(keyDir + "/" + ct_file, ct_result, SerType::BINARY))
        throw std::runtime_error("Failed to load result ciphertext: " + ct_file);

    // ---- Decrypt ----
    constexpr size_t encodedLength = 10;
    Plaintext result;
    cc->Decrypt(secretKey, ct_result, &result);
    result->SetLength(encodedLength);

    std::cout << "Output after plaintext-add:\n\t" << result << std::endl;

    // ---- Verify each of the 10 meaningful slots ----
    std::vector<double> expected = {2.0, 4.0, 6.0, 8.0, 10.0,
                                    12.0, 14.0, 16.0, 18.0, 20.0};
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
    }
    std::cerr << "[FAIL] Output does not match expected values" << std::endl;
    return 1;
}
