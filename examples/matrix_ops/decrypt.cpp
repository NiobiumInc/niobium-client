// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Matrix ops example — decrypt and verify (CKKS)
//
// Usage: ./matrix_ops_decrypt [key_dir [rows1 [cols1 [rows2 [cols2]]]]]

#include "openfhe.h"

#include <cmath>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

static double decrypt_cell(const CryptoContext<DCRTPoly>& cc, const PrivateKey<DCRTPoly>& sk,
                           const std::string& keyDir, const char* name, int i, int j) {
    Ciphertext<DCRTPoly> ct;
    std::string filename = keyDir + "/matrix_" + name + "_" +
                           std::to_string(i) + "_" + std::to_string(j) + ".bin";
    if (!Serial::DeserializeFromFile(filename, ct, SerType::BINARY))
        throw std::runtime_error("Failed to load " + filename);
    Plaintext pt;
    cc->Decrypt(sk, ct, &pt);
    pt->SetLength(1);
    return pt->GetRealPackedValue()[0];
}

int main(int argc, char* argv[]) {
    std::string keyDir = "matrix_ops_keys";
    int rows1 = 2, cols1 = 2, rows2 = 2, cols2 = 2;

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) rows1 = std::stoi(argv[2]);
    if (argc > 3) cols1 = std::stoi(argv[3]);
    if (argc > 4) rows2 = std::stoi(argv[4]);
    if (argc > 5) cols2 = std::stoi(argv[5]);

    std::cout << "=== Matrix Ops — Decrypt ===" << std::endl;

    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");
    PrivateKey<DCRTPoly> sk;
    if (!Serial::DeserializeFromFile(keyDir + "/sk.bin", sk, SerType::BINARY))
        throw std::runtime_error("Failed to load secret key");

    std::vector<std::vector<double>> A(rows1, std::vector<double>(cols1));
    for (int i = 0; i < rows1; ++i)
        for (int j = 0; j < cols1; ++j)
            A[i][j] = decrypt_cell(cc, sk, keyDir, "a", i, j);

    std::vector<std::vector<double>> B(rows2, std::vector<double>(cols2));
    for (int i = 0; i < rows2; ++i)
        for (int j = 0; j < cols2; ++j)
            B[i][j] = decrypt_cell(cc, sk, keyDir, "b", i, j);

    double tolerance = 0.01;
    double max_error = 0.0;
    bool all_passed = true;
    for (int i = 0; i < rows1; ++i) {
        for (int j = 0; j < cols2; ++j) {
            double expected = 0.0;
            for (int k = 0; k < cols1; ++k) expected += A[i][k] * B[k][j];
            double got = decrypt_cell(cc, sk, keyDir, "c", i, j);
            double err = std::abs(got - expected);
            max_error = std::max(max_error, err);
            if (err > tolerance) {
                std::cerr << "[FAIL] C[" << i << "][" << j << "]: " << got
                          << " != " << expected << " (err " << err << ")" << std::endl;
                all_passed = false;
            }
        }
    }

    std::cout << "Max error: " << max_error << " (tolerance " << tolerance << ")" << std::endl;
    if (all_passed) {
        std::cout << "[PASS] matrix multiply: all " << (rows1 * cols2) << " cells correct" << std::endl;
        return 0;
    }
    std::cerr << "[FAIL] some result cells exceed tolerance" << std::endl;
    return 1;
}
