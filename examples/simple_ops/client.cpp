// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simple ops example — client side (CKKS)
//
// Generates CKKS crypto context + keys, encrypts two values.
// Matching compiler's TOY defaults: qi=42, firstMod=57, N=2048, depth=2.
//
// Usage: ./simple_ops_client [output_dir [a b]]

#include "openfhe.h"

#include <filesystem>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string outputDir = "simple_ops_keys";
    double a = 5.0, b = 6.0;

    if (argc > 1) outputDir = argv[1];
    if (argc > 2) a = std::stod(argv[2]);
    if (argc > 3) b = std::stod(argv[3]);

    std::cout << "=== Simple Ops — Client ===" << std::endl;
    std::cout << "a = " << a << ", b = " << b << std::endl;

    std::filesystem::create_directories(outputDir);

    CCParams<CryptoContextCKKSRNS> parameters;
    parameters.SetSecurityLevel(HEStd_NotSet);
    parameters.SetRingDim(2048);
    parameters.SetMultiplicativeDepth(2);
    parameters.SetScalingModSize(42);
    parameters.SetFirstModSize(57);
    parameters.SetScalingTechnique(FLEXIBLEAUTO);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(parameters);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);

    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);

    // Serialize
    Serial::SerializeToFile(outputDir + "/cc.bin", cc, SerType::BINARY);
    Serial::SerializeToFile(outputDir + "/pk.bin", keyPair.publicKey, SerType::BINARY);
    Serial::SerializeToFile(outputDir + "/sk.bin", keyPair.secretKey, SerType::BINARY);
    std::ofstream mkStream(outputDir + "/mk.bin", std::ios::binary);
    cc->SerializeEvalMultKey(mkStream, SerType::BINARY);
    mkStream.close();

    std::vector<double> va = {a};
    std::vector<double> vb = {b};
    auto ct_a = cc->Encrypt(keyPair.publicKey, cc->MakeCKKSPackedPlaintext(va));
    auto ct_b = cc->Encrypt(keyPair.publicKey, cc->MakeCKKSPackedPlaintext(vb));

    Serial::SerializeToFile(outputDir + "/ct_a.bin", ct_a, SerType::BINARY);
    Serial::SerializeToFile(outputDir + "/ct_b.bin", ct_b, SerType::BINARY);

    std::ofstream valStream(outputDir + "/values.txt");
    valStream << a << " " << b << std::endl;
    valStream.close();

    std::cout << "Client complete. Files in " << outputDir << "/" << std::endl;
    return 0;
}
