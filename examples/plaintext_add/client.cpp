// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Plaintext-add example — client side
//
// Generates CKKS crypto context, keys, and an encrypted test vector [1..10].
// All artifacts are serialized to a directory for the server to consume.
//
// This file is pure OpenFHE — no Niobium compiler dependency.
//
// Usage: ./plaintext_add_client [output_dir]

#include "openfhe.h"

#include <filesystem>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "plaintext_add_keys";
    if (argc > 1) outputDir = argv[1];

    std::cout << "=== CKKS Plaintext-Add — Client (Key Generation) ===" << std::endl;
    std::cout << "Output directory: " << outputDir << std::endl;

    std::filesystem::create_directories(outputDir);

    // ---- CKKS parameters ----
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetSecretKeyDist(UNIFORM_TERNARY);
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(2048);
    parameters.SetScalingModSize(59);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);
    parameters.SetFirstModSize(60);
    parameters.SetMultiplicativeDepth(2);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);

    usint ringDim = cc->GetRingDimension();
    std::cout << "Ring dimension: " << ringDim << std::endl;

    // ---- Key generation ----
    auto keyPair = cc->KeyGen();

    // ---- Serialize crypto context + keys ----
    if (!Serial::SerializeToFile(outputDir + "/cc.bin", cc, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize crypto context" << std::endl;
        return 1;
    }
    if (!Serial::SerializeToFile(outputDir + "/pk.bin", keyPair.publicKey, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize public key" << std::endl;
        return 1;
    }
    if (!Serial::SerializeToFile(outputDir + "/sk.bin", keyPair.secretKey, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize secret key" << std::endl;
        return 1;
    }

    // ---- Encrypt the input vector [1..10] ----
    std::vector<double> input = {1.0, 2.0, 3.0, 4.0, 5.0,
                                 6.0, 7.0, 8.0, 9.0, 10.0};

    Plaintext ptxt = cc->MakeCKKSPackedPlaintext(input);
    ptxt->SetLength(input.size());
    std::cout << "Input: " << ptxt << std::endl;

    Ciphertext<DCRTPoly> ciph = cc->Encrypt(keyPair.publicKey, ptxt);

    if (!Serial::SerializeToFile(outputDir + "/ciphertext.bin", ciph, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize ciphertext" << std::endl;
        return 1;
    }

    std::cout << "\nClient key generation complete." << std::endl;
    std::cout << "Files written to " << outputDir << "/:" << std::endl;
    std::cout << "  cc.bin, pk.bin, sk.bin, ciphertext.bin" << std::endl;
    return 0;
}
