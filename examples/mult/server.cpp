// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — server side (CKKS)
//
// Loads CKKS crypto context, keys, and two encrypted values from client.
// Records a*b via the Niobium compiler, producing a FHETCH instruction trace.
// Then replays the trace through the FHETCH simulator and serializes the
// result ciphertext for the decrypt step.
//
// Usage: ./mult_server [key_dir]

#include "openfhe.h"
#include "niobium/compiler.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    // ---- Niobium compiler setup ----
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info(
        "mult_server", "1.0", "CKKS multiplication — FHETCH trace");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "ckks_mult"});
    niobium::compiler().cache_parameters(params);

    std::string keyDir = "mult_keys";
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg.find("--") != 0) { keyDir = arg; break; }
    }

    std::cout << "=== CKKS Multiply — Server ===" << std::endl;
    std::cout << "Loading from: " << keyDir << std::endl;

    // ---- Load crypto context ----
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    std::cout << "Ring dimension: " << cc->GetRingDimension() << std::endl;

    // ---- Load keys ----
    {
        std::ifstream mkStream(keyDir + "/mk.bin", std::ios::in | std::ios::binary);
        if (mkStream.is_open()) {
            if (!cc->DeserializeEvalMultKey(mkStream, SerType::BINARY))
                throw std::runtime_error("Failed to load eval mult key");
            std::cout << "Loaded eval mult key" << std::endl;
        }
    }

    // ---- Load ciphertexts ----
    Ciphertext<DCRTPoly> ct_a, ct_b;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext a");
    if (!Serial::DeserializeFromFile(keyDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext b");

    // ---- Capture crypto context and keys for simulator ----
    niobium::compiler().capture_crypto_context(cc);
    niobium::compiler().tag_keys(cc);

    // ---- Tag inputs ----
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING PHASE ----
        std::cout << "\n--- Recording EvalMult ---" << std::endl;
        niobium::compiler().start();

        auto ct_result = cc->EvalMult(ct_a, ct_b);

        niobium::compiler().probe("result", ct_result);
        niobium::compiler().stop();
    }

    // ---- REPLAY: execute trace through the FHETCH simulator ----
    std::cout << "\n--- Replay ---" << std::endl;
    if (!niobium::compiler().replay()) {
        std::cerr << "[ERROR] Replay failed" << std::endl;
        return 1;
    }

    // ---- Retrieve result and serialize for decrypt step ----
    Ciphertext<DCRTPoly> ct_result;
    if (niobium::compiler().result(cc, "result", ct_result)) {
        if (!Serial::SerializeToFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY)) {
            std::cerr << "Error: Failed to serialize result ciphertext" << std::endl;
            return 1;
        }
        std::cout << "Result ciphertext written to " << keyDir << "/ct_result.bin" << std::endl;
    } else {
        std::cerr << "[ERROR] Could not retrieve result" << std::endl;
        return 1;
    }

    return 0;
}
