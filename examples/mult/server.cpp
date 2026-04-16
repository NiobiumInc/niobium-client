// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Multiply example — server side
//
// Loads BFV crypto context, keys, and two encrypted integers from client.
// Records a*b via the Niobium compiler, producing a FHETCH instruction trace.
// Serializes the result ciphertext for the decrypt step.
//
// Usage: ./mult_server [key_dir]

#include "openfhe.h"
#include "niobium/compiler.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/bfvrns/bfvrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    // ---- Niobium compiler setup ----
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info(
        "mult_server", "1.0", "BFV integer multiplication — FHETCH trace");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "bfv_mult"});
    niobium::compiler().cache_parameters(params);

    std::string keyDir = "mult_keys";
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg.find("--") != 0) { keyDir = arg; break; }
    }

    std::cout << "=== Integer Multiply — Server (Trace Recording) ===" << std::endl;
    std::cout << "Loading from: " << keyDir << std::endl;

    // ---- Load crypto context ----
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    std::cout << "Ring dimension: " << cc->GetRingDimension() << std::endl;

    // ---- Load eval mult key ----
    {
        std::ifstream mkStream(keyDir + "/mk.bin", std::ios::in | std::ios::binary);
        if (!mkStream.is_open() || !cc->DeserializeEvalMultKey(mkStream, SerType::BINARY))
            throw std::runtime_error("Failed to load eval mult key");
    }

    // ---- Load ciphertexts ----
    Ciphertext<DCRTPoly> ct_a, ct_b;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext a");
    if (!Serial::DeserializeFromFile(keyDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext b");

    // ---- Capture crypto context ----
    niobium::compiler().capture_crypto_context(cc);

    // ---- Tag inputs ----
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);

    Ciphertext<DCRTPoly> ct_result;

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING PHASE (hollow mode) ----
        // Hollow mode skips expensive polynomial math during recording
        // while preserving structure and firing probes correctly.
        // Real input data is still captured via tag_input().
        // The result ciphertext is NOT valid after hollow recording —
        // real results come from the Niobium hardware after compilation.
        std::cout << "\n--- Recording EvalMult (hollow mode) ---" << std::endl;
        niobium::compiler().enable_hollow_mode(true);
        niobium::compiler().start();

        ct_result = cc->EvalMult(ct_a, ct_b);

        niobium::compiler().probe("result", ct_result);
        niobium::compiler().stop();
        niobium::compiler().enable_hollow_mode(false);
        std::cout << "Recording complete. FHETCH trace written." << std::endl;
        std::cout << "NOTE: Hollow mode was used — result ciphertext is not valid." << std::endl;
        std::cout << "      In production, the Niobium server returns the real result" << std::endl;
        std::cout << "      after compiling and executing the trace on hardware." << std::endl;
    } else {
        std::cout << "\n--- Using cached trace ---" << std::endl;
    }

    // In a real deployment, the FHETCH trace + serialized inputs would be
    // sent to the Niobium compilation service. The server would return the
    // hardware-computed result ciphertext, which the client then decrypts.
    //
    // For this example, no ct_result.bin is produced since hollow mode
    // does not compute real polynomial values.

    return 0;
}
