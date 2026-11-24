// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Matrix ops example — client side (CKKS)
//
// Generates CKKS crypto context + keys, encrypts matrix A (rows1 x cols1)
// and matrix B (rows2 x cols2), one ciphertext per cell.
//
// Usage: ./matrix_ops_client [output_dir [rows1 [cols1 [rows2 [cols2 [ring_dim]]]]]]
//   Defaults: output_dir=matrix_ops_keys, 2x2 x 2x2, ring_dim=65536 (2^16)

#include "openfhe.h"

#include <filesystem>
#include <random>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "matrix_ops_keys";
    int rows1 = 2, cols1 = 2, rows2 = 2, cols2 = 2;
    uint32_t ring_dim = 65536;

    if (argc > 1) outputDir = argv[1];
    if (argc > 2) rows1 = std::stoi(argv[2]);
    if (argc > 3) cols1 = std::stoi(argv[3]);
    if (argc > 4) rows2 = std::stoi(argv[4]);
    if (argc > 5) cols2 = std::stoi(argv[5]);
    if (argc > 6) ring_dim = static_cast<uint32_t>(std::stoul(argv[6]));

    if (cols1 != rows2)
        throw std::runtime_error("cols1 must equal rows2 for A x B");

    std::cout << "=== Matrix Ops — Client ===" << std::endl;
    std::cout << "A: " << rows1 << "x" << cols1 << ", B: " << rows2 << "x" << cols2 << std::endl;

    std::filesystem::create_directories(outputDir);

    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(ring_dim);
    parameters.SetMultiplicativeDepth(1);  // one EvalMult per term; EvalAdd is free
    parameters.SetScalingModSize(42);
    parameters.SetFirstModSize(57);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);
    cc->Enable(ADVANCEDSHE);

    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);

    if (!Serial::SerializeToFile(outputDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to serialize crypto context");
    if (!Serial::SerializeToFile(outputDir + "/pk.bin", keyPair.publicKey, SerType::BINARY))
        throw std::runtime_error("Failed to serialize public key");
    if (!Serial::SerializeToFile(outputDir + "/sk.bin", keyPair.secretKey, SerType::BINARY))
        throw std::runtime_error("Failed to serialize secret key");
    std::ofstream mkStream(outputDir + "/mk.bin", std::ios::binary);
    if (!mkStream.is_open() || !cc->SerializeEvalMultKey(mkStream, SerType::BINARY))
        throw std::runtime_error("Failed to serialize eval mult key");
    mkStream.close();

    std::mt19937_64 rng(std::random_device{}());
    std::uniform_real_distribution<double> dist(0.0, 1.0);

    auto encrypt_matrix = [&](const char* name, int rows, int cols) {
        for (int i = 0; i < rows; ++i) {
            for (int j = 0; j < cols; ++j) {
                double value = dist(rng);
                Plaintext pt = cc->MakeCKKSPackedPlaintext(std::vector<double>{value}, 1, 0, nullptr, 1);
                auto ct = cc->Encrypt(keyPair.publicKey, pt);
                std::string filename = outputDir + "/matrix_" + name + "_" +
                                       std::to_string(i) + "_" + std::to_string(j) + ".bin";
                if (!Serial::SerializeToFile(filename, ct, SerType::BINARY))
                    throw std::runtime_error("Failed to serialize " + filename);
            }
        }
    };
    encrypt_matrix("a", rows1, cols1);
    encrypt_matrix("b", rows2, cols2);

    std::cout << "Client complete. Files in " << outputDir << "/" << std::endl;
    return 0;
}
