// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — client side
//
// Generates a BFV crypto context, keys, and encrypts two integers.
// All artifacts are serialized to a directory for the server to consume.
//
// Usage: ./mult_client [output_dir [a b]]
//   Defaults: output_dir=mult_keys, a=7, b=13

#include "openfhe.h"

#include <filesystem>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/bfvrns/bfvrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "mult_keys";
    int64_t a = 7, b = 13;

    if (argc > 1) outputDir = argv[1];
    if (argc > 2) a = std::stoll(argv[2]);
    if (argc > 3) b = std::stoll(argv[3]);

    std::cout << "=== Integer Multiply — Client (Key Generation) ===" << std::endl;
    std::cout << "Output directory: " << outputDir << std::endl;
    std::cout << "a = " << a << ", b = " << b << std::endl;

    std::filesystem::create_directories(outputDir);

    // ---- BFV parameters ----
    CCParams<CryptoContextBFVRNS> parameters;
    parameters.SetPlaintextModulus(65537);
    parameters.SetMultiplicativeDepth(1);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);

    std::cout << "Ring dimension: " << cc->GetRingDimension() << std::endl;

    // ---- Key generation ----
    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);

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

    std::ofstream mkStream(outputDir + "/mk.bin", std::ios::out | std::ios::binary);
    if (!mkStream.is_open() || !cc->SerializeEvalMultKey(mkStream, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize eval mult key" << std::endl;
        return 1;
    }
    mkStream.close();

    // ---- Encrypt inputs ----
    Plaintext pt_a = cc->MakePackedPlaintext({a});
    Plaintext pt_b = cc->MakePackedPlaintext({b});

    auto ct_a = cc->Encrypt(keyPair.publicKey, pt_a);
    auto ct_b = cc->Encrypt(keyPair.publicKey, pt_b);

    if (!Serial::SerializeToFile(outputDir + "/ct_a.bin", ct_a, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize ciphertext a" << std::endl;
        return 1;
    }
    if (!Serial::SerializeToFile(outputDir + "/ct_b.bin", ct_b, SerType::BINARY)) {
        std::cerr << "Error: Failed to serialize ciphertext b" << std::endl;
        return 1;
    }

    // Save plaintext values for verification
    std::ofstream valStream(outputDir + "/values.txt");
    valStream << a << " " << b << std::endl;
    valStream.close();

    std::cout << "\nClient complete. Files written to " << outputDir << "/" << std::endl;
    return 0;
}
