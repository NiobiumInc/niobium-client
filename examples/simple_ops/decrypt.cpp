// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simple ops example — decrypt and verify (CKKS)
//
// Usage: ./simple_ops_decrypt [key_dir [operation]]

#include "openfhe.h"

#include <cmath>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "simple_ops_keys";
    std::string operation = "ADD";

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) operation = argv[2];

    std::cout << "=== Simple Ops — Decrypt (" << operation << ") ===" << std::endl;

    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    PrivateKey<DCRTPoly> secretKey;
    if (!Serial::DeserializeFromFile(keyDir + "/sk.bin", secretKey, SerType::BINARY))
        throw std::runtime_error("Failed to load secret key");

    Ciphertext<DCRTPoly> ct_result;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY))
        throw std::runtime_error("Failed to load result ciphertext");

    double a = 0, b = 0;
    {
        std::ifstream valStream(keyDir + "/values.txt");
        valStream >> a >> b;
    }

    Plaintext pt_result;
    cc->Decrypt(secretKey, ct_result, &pt_result);
    pt_result->SetLength(1);

    double result = pt_result->GetRealPackedValue()[0];

    // Compute expected value based on operation
    double expected = 0;
    if (operation == "ADD")          expected = a + b;
    else if (operation == "SUB")     expected = a - b;
    else if (operation == "MUL")     expected = a * b;
    else if (operation == "NEG")     expected = -a;
    else if (operation == "ADDI")    expected = a + 3.0;
    else if (operation == "SUBI")    expected = a - 2.0;
    else if (operation == "MULI")    expected = a * 4.0;
    else if (operation == "ADD_ADD") expected = 2 * a + b;
    else if (operation == "ADD_SUB") expected = b;
    else if (operation == "MUL_ADD") expected = a * b + a;
    else if (operation == "ADD_MUL") expected = (a + b) * a;
    else if (operation == "ALL_NO_MUL") expected = (b + 1.0) * 4.0;
    else if (operation == "MORPH")   expected = b;  // EvalRotate(ct_a={a,b}, 1)[0]
    else {
        std::cerr << "Unknown operation: " << operation << std::endl;
        return 1;
    }

    double diff = std::abs(result - expected);
    double tolerance = 0.01;

    std::cout << "Result: " << result << " (expected " << expected
              << ", diff " << diff << ")" << std::endl;

    if (diff < tolerance) {
        std::cout << "[PASS] " << operation << ": " << result << " ~= " << expected << std::endl;
        return 0;
    } else {
        std::cerr << "[FAIL] " << operation << ": " << result << " != " << expected
                  << " (diff " << diff << " > " << tolerance << ")" << std::endl;
        return 1;
    }
}
