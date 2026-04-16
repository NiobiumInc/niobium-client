// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — decrypt and verify
//
// Loads the BFV crypto context, secret key, and result ciphertext
// produced by the server. Decrypts and verifies a*b.
//
// Usage: ./mult_decrypt [key_dir]

#include "openfhe.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/bfvrns/bfvrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "mult_keys";
    if (argc > 1) keyDir = argv[1];

    std::cout << "=== Integer Multiply — Decrypt & Verify ===" << std::endl;
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
    int64_t a = 0, b = 0;
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

    int64_t result = pt_result->GetPackedValue()[0];
    int64_t expected = a * b;

    std::cout << "Result: " << a << " * " << b << " = " << result << std::endl;

    if (result == expected) {
        std::cout << "[PASS] " << result << " == " << expected << std::endl;
        return 0;
    } else {
        std::cerr << "[FAIL] " << result << " != " << expected << std::endl;
        return 1;
    }
}
