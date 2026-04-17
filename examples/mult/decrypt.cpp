// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — decrypt and verify (CKKS)
//
// Loads the CKKS crypto context, secret key, and result ciphertext
// produced by the server. Decrypts and verifies a*b.
//
// Usage: ./mult_decrypt [key_dir]

#include "openfhe.h"

#include <cmath>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "mult_keys";
    if (argc > 1) keyDir = argv[1];

    std::cout << "=== CKKS Multiply — Decrypt & Verify ===" << std::endl;
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

    // ---- Load expected values ----
    double a = 0, b = 0;
    {
        std::ifstream valStream(keyDir + "/values.txt");
        if (!valStream.is_open())
            throw std::runtime_error("Failed to load values.txt");
        valStream >> a >> b;
    }

    // ---- Decrypt ----
    Plaintext pt_result;
    cc->Decrypt(secretKey, ct_result, &pt_result);
    pt_result->SetLength(1);

    double result = pt_result->GetRealPackedValue()[0];
    double expected = a * b;
    double tolerance = 0.01;
    double diff = std::abs(result - expected);

    std::cout << "Result: " << a << " * " << b << " = " << result
              << " (expected " << expected << ", diff " << diff << ")" << std::endl;

    if (diff < tolerance) {
        std::cout << "[PASS] " << result << " ~= " << expected << std::endl;
        return 0;
    } else {
        std::cerr << "[FAIL] " << result << " != " << expected
                  << " (diff " << diff << " > " << tolerance << ")" << std::endl;
        return 1;
    }
}
