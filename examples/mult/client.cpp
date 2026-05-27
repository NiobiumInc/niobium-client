// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — client side (CKKS)
//
// Generates a CKKS crypto context, keys, and encrypts two values.
// All artifacts are serialized to a directory for the server to consume.
//
// Usage: ./mult_client [output_dir [a [b [ring_dim]]]]
//   Defaults: output_dir=mult_keys, a=7.0, b=13.0, ring_dim=2048

#include "openfhe.h"

#include <filesystem>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "mult_keys";
    double a = 7.0, b = 13.0;
    uint32_t ring_dim = 2048;

    if (argc > 1) outputDir = argv[1];
    if (argc > 2) a = std::stod(argv[2]);
    if (argc > 3) b = std::stod(argv[3]);
    if (argc > 4) ring_dim = static_cast<uint32_t>(std::stoul(argv[4]));

    std::cout << "=== CKKS Multiply — Client (Key Generation) ===" << std::endl;
    std::cout << "Output directory: " << outputDir << std::endl;
    std::cout << "a = " << a << ", b = " << b << std::endl;

    std::filesystem::create_directories(outputDir);

    // ---- CKKS parameters (matching compiler's TOY defaults) ----
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(ring_dim);
    parameters.SetMultiplicativeDepth(3);
    parameters.SetScalingModSize(42);
    parameters.SetFirstModSize(57);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);
    cc->Enable(ADVANCEDSHE);

    std::cout << "Ring dimension: " << cc->GetRingDimension() << std::endl;

    // ---- Key generation ----
    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);
    // Generate a minimal rotation key set so the server can exercise the
    // evalautomorphism-key loading path (matches the compiler reference,
    // which always ships with rotation keys regardless of operation).
    cc->EvalRotateKeyGen(keyPair.secretKey, {1, -1});

    // ---- Serialize crypto context + keys ----
    if (!Serial::SerializeToFile(outputDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to serialize crypto context");
    if (!Serial::SerializeToFile(outputDir + "/pk.bin", keyPair.publicKey, SerType::BINARY))
        throw std::runtime_error("Failed to serialize public key");
    if (!Serial::SerializeToFile(outputDir + "/sk.bin", keyPair.secretKey, SerType::BINARY))
        throw std::runtime_error("Failed to serialize secret key");

    std::ofstream mkStream(outputDir + "/mk.bin", std::ios::out | std::ios::binary);
    if (!mkStream.is_open() || !cc->SerializeEvalMultKey(mkStream, SerType::BINARY))
        throw std::runtime_error("Failed to serialize eval mult key");
    mkStream.close();

    std::ofstream rkStream(outputDir + "/rk.bin", std::ios::out | std::ios::binary);
    if (!rkStream.is_open() || !cc->SerializeEvalAutomorphismKey(rkStream, SerType::BINARY))
        throw std::runtime_error("Failed to serialize eval automorphism key");
    rkStream.close();

    // ---- Encrypt inputs ----
    std::vector<double> va = {a};
    std::vector<double> vb = {b};
    auto pt_a = cc->MakeCKKSPackedPlaintext(va);
    auto pt_b = cc->MakeCKKSPackedPlaintext(vb);

    auto ct_a = cc->Encrypt(keyPair.publicKey, pt_a);
    auto ct_b = cc->Encrypt(keyPair.publicKey, pt_b);

    if (!Serial::SerializeToFile(outputDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to serialize ciphertext a");
    if (!Serial::SerializeToFile(outputDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to serialize ciphertext b");

    // Save plaintext values for verification
    std::ofstream valStream(outputDir + "/values.txt");
    valStream << a << " " << b << std::endl;
    valStream.close();

    std::cout << "\nClient complete. Files written to " << outputDir << "/" << std::endl;
    return 0;
}
