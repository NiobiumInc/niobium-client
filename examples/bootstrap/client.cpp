// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Bootstrap example — client side
//
// Generates CKKS crypto context, keys, and an encrypted test vector.
// All artifacts are serialized to a directory for the server to consume.
//
// This file is pure OpenFHE — no Niobium compiler dependency.
//
// Usage: ./bootstrap_client [output_dir]

#include "openfhe.h"

#include <filesystem>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "bootstrap_keys";
    if (argc > 1) outputDir = argv[1];

    std::cout << "=== CKKS Bootstrap — Client (Key Generation) ===" << std::endl;
    std::cout << "Output directory: " << outputDir << std::endl;

    // Create output directory
    std::filesystem::create_directories(outputDir);

    // ---- CKKS parameters (bootstrap defaults: qi=59, firstMod=60) ----
    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetSecretKeyDist(UNIFORM_TERNARY);
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(2048);
    parameters.SetScalingModSize(59);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);
    parameters.SetFirstModSize(60);

    std::vector<uint32_t> levelBudget = {4, 4};
    uint32_t levelsAfterBootstrap = 10;
    usint depth = levelsAfterBootstrap +
                  FHECKKSRNS::GetBootstrapDepth(levelBudget, UNIFORM_TERNARY);
    parameters.SetMultiplicativeDepth(depth);

    std::cout << "Multiplicative depth: " << depth << std::endl;

    // ---- Create crypto context ----
    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);
    cc->Enable(ADVANCEDSHE);
    cc->Enable(FHE);

    usint ringDim = cc->GetRingDimension();
    usint numSlots = ringDim / 2;
    std::cout << "Ring dimension: " << ringDim << std::endl;
    std::cout << "Number of slots: " << numSlots << std::endl;

    // ---- Bootstrap precomputation ----
    std::cout << "Setting up bootstrap precomputations..." << std::endl;
    cc->EvalBootstrapSetup(levelBudget);

    // ---- Key generation ----
    std::cout << "Generating keys..." << std::endl;
    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);
    cc->EvalBootstrapKeyGen(keyPair.secretKey, numSlots);

    // ---- Serialize crypto context ----
    std::cout << "Serializing crypto context..." << std::endl;
    if (!Serial::SerializeToFile(outputDir + "/cc.bin", cc, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize crypto context" << std::endl;
        return 1;
    }

    // ---- Serialize keys ----
    if (!Serial::SerializeToFile(outputDir + "/pk.bin", keyPair.publicKey, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize public key" << std::endl;
        return 1;
    }
    if (!Serial::SerializeToFile(outputDir + "/sk.bin", keyPair.secretKey, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize secret key" << std::endl;
        return 1;
    }

    std::ofstream mkStream(outputDir + "/mk.bin", std::ios::out | std::ios::binary);
    if (!mkStream.is_open() || !cc->SerializeEvalMultKey(mkStream, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize eval mult key" << std::endl;
        return 1;
    }
    mkStream.close();

    std::ofstream rkStream(outputDir + "/rk.bin", std::ios::out | std::ios::binary);
    if (!rkStream.is_open() || !cc->SerializeEvalAutomorphismKey(rkStream, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize eval automorphism keys" << std::endl;
        return 1;
    }
    rkStream.close();

    // ---- Create and serialize test ciphertext ----
    std::vector<double> input = {0.25, 0.5, 0.75, 1.0, 2.0, 3.0, 4.0, 5.0};

    // Encrypt at the deepest level so bootstrapping is needed to continue computing
    Plaintext ptxt = cc->MakeCKKSPackedPlaintext(input, 1, depth - 1);
    ptxt->SetLength(input.size());
    std::cout << "Input: " << ptxt << std::endl;

    Ciphertext<DCRTPoly> ciph = cc->Encrypt(keyPair.publicKey, ptxt);
    std::cout << "Levels remaining before bootstrap: "
              << depth - ciph->GetLevel() << std::endl;

    if (!Serial::SerializeToFile(outputDir + "/ciphertext.bin", ciph, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize ciphertext" << std::endl;
        return 1;
    }

    // Save depth for the server
    std::ofstream depthStream(outputDir + "/depth.txt");
    depthStream << depth << std::endl;
    depthStream.close();

    std::cout << "\nClient key generation complete." << std::endl;
    std::cout << "Files written to " << outputDir << "/:" << std::endl;
    std::cout << "  cc.bin, pk.bin, sk.bin, mk.bin, rk.bin, ciphertext.bin, depth.txt"
              << std::endl;
    return 0;
}
