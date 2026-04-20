// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// ciphers_ops_cache_keys.cpp — minimal CKKS keygen for the auto-facade demo.
//
// Produces the cc.bin / pk.bin / sk.bin / mk.bin / rk.bin set that
// ciphers_ops_client (encrypts inputs) and ciphers_ops_server_auto
// (runs the recorded computation) expect under <rootdir>/io/<size>/keys/.
//
// Scaled-down version of niobium-compiler's
// examples/fetch/ciphers_ops_cache_keys.cpp. The compiler variant wires up
// a lot of matrix / replication rotation indices that the basic
// arithmetic ops in this example don't need. Here we keep just enough to
// cover ADD / SUB / MUL / ADDI / SUBI / MULI / ADD_ADD / ADD_SUB /
// ROTATE_ADD plus the immediate-rotation operations the server accepts.
//
// Usage: ciphers_ops_cache_keys <instance-size> [multiplicative_depth]

#include "openfhe.h"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

#include "params.h"

using namespace lbcrypto;

namespace fs = std::filesystem;

int main(int argc, char* argv[]) {
    if (argc < 2 || !std::isdigit(argv[1][0])) {
        std::cout << "Usage: " << argv[0]
                  << " <instance-size> [multiplicative_depth]\n"
                  << "  instance-size: 0=TOY, 1=SMALL, 2=MEDIUM, 3=LARGE\n";
        return 0;
    }
    const auto size = static_cast<InstanceSize>(std::stoi(argv[1]));
    InstanceParams prms(size);

    int multiplicative_depth = 3;
    if (argc > 2 && std::isdigit(argv[2][0]))
        multiplicative_depth = std::stoi(argv[2]);

    std::cout << "=== ciphers_ops_cache_keys ===" << std::endl;
    std::cout << "Instance: " << instance_name(size) << std::endl;
    std::cout << "Keydir:   " << prms.keydir() << std::endl;
    std::cout << "Depth:    " << multiplicative_depth << std::endl;

    CCParams<CryptoContextCKKSRNS> cParams;
    cParams.SetSecretKeyDist(UNIFORM_TERNARY);
    cParams.SetKeySwitchTechnique(HYBRID);
    cParams.SetMultiplicativeDepth(multiplicative_depth);
    cParams.SetSecurityLevel(HEStd_NotSet);
    cParams.SetRingDim(prms.getRingDim());
    cParams.SetBatchSize(prms.getRingDim() / 2);
    cParams.SetScalingTechnique(FLEXIBLEAUTO);
    // Match niobium-client/examples/simple_ops params for ring_dim 2048 —
    // those values (42/57) are what the FHETCH simulator has been
    // tuned against for this repo's roundtrip tests; using the larger
    // 50/60 pair drifts past the 0.01 tolerance on replay.
    cParams.SetScalingModSize(42);
    cParams.SetFirstModSize(57);

    CryptoContext<DCRTPoly> cc = GenCryptoContext(cParams);
    cc->Enable(PKE);
    cc->Enable(KEYSWITCH);
    cc->Enable(LEVELEDSHE);
    cc->Enable(ADVANCEDSHE);

    auto keyPair = cc->KeyGen();
    cc->EvalMultKeyGen(keyPair.secretKey);
    // Small rotation set covering the ROTATE_* operations the server
    // accepts with single-digit rotation amounts.
    std::vector<int> rotations = {1, -1, 2, -2, 3, -3, 5, -5};
    cc->EvalAtIndexKeyGen(keyPair.secretKey, rotations);

    fs::create_directories(prms.keydir());

    if (!Serial::SerializeToFile(prms.keydir() / "cc.bin", cc, SerType::BINARY) ||
        !Serial::SerializeToFile(prms.keydir() / "pk.bin",
                                 keyPair.publicKey, SerType::BINARY) ||
        !Serial::SerializeToFile(prms.keydir() / "sk.bin",
                                 keyPair.secretKey, SerType::BINARY)) {
        throw std::runtime_error("Failed to serialize cc/pk/sk to "
                                  + prms.keydir().string());
    }

    {
        std::ofstream mk_file(prms.keydir() / "mk.bin",
                              std::ios::out | std::ios::binary);
        std::ofstream rk_file(prms.keydir() / "rk.bin",
                              std::ios::out | std::ios::binary);
        if (!mk_file.is_open() || !rk_file.is_open() ||
            !cc->SerializeEvalMultKey(mk_file, SerType::BINARY) ||
            !cc->SerializeEvalAutomorphismKey(rk_file, SerType::BINARY)) {
            throw std::runtime_error("Failed to write eval keys to "
                                      + prms.keydir().string());
        }
    }

    std::cout << "Wrote cc.bin, pk.bin, sk.bin, mk.bin, rk.bin to "
              << prms.keydir() << std::endl;
    return 0;
}
