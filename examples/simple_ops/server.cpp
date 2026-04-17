// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simple ops example — server side (CKKS)
//
// Loads crypto context + keys + ciphertexts, records a chosen operation,
// replays through the FHETCH simulator, and serializes the result.
//
// Supported operations:
//   ADD        a + b
//   SUB        a - b
//   MUL        a * b  (requires relinearization key)
//   NEG        -a
//   ADD_ADD    (a + b) + a  = 2a + b
//   ADD_SUB    (a + b) - a  = b
//   MUL_ADD    (a * b) + a
//   ADD_MUL    (a + b) * a
//
// Usage: ./simple_ops_server [key_dir [operation]]

#include "openfhe.h"
#include "niobium/compiler.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "simple_ops_keys";
    std::string operation = "ADD";

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) operation = argv[2];

    // ---- Niobium compiler setup ----
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info(
        "simple_ops_server", "1.0", "CKKS simple ops — FHETCH trace");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "simple_ops"});
    params.push_back({"op", operation});
    niobium::compiler().cache_parameters(params);

    std::cout << "=== Simple Ops — Server ===" << std::endl;
    std::cout << "Operation: " << operation << std::endl;

    // ---- Load crypto context ----
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    // ---- Load eval mult key (needed for MUL operations) ----
    bool has_mult_key = false;
    {
        std::ifstream mkStream(keyDir + "/mk.bin", std::ios::binary);
        if (mkStream.is_open()) {
            if (cc->DeserializeEvalMultKey(mkStream, SerType::BINARY))
                has_mult_key = true;
        }
    }

    // ---- Load ciphertexts ----
    Ciphertext<DCRTPoly> ct_a, ct_b;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext a");
    if (!Serial::DeserializeFromFile(keyDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext b");

    // ---- Capture crypto context and keys ----
    niobium::compiler().capture_crypto_context(cc);
    if (has_mult_key)
        niobium::compiler().tag_keys(cc);

    // ---- Tag inputs ----
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);

    if (!niobium::compiler().is_cache_valid()) {
        std::cout << "\n--- Recording " << operation << " ---" << std::endl;
        niobium::compiler().start();

        Ciphertext<DCRTPoly> result;

        if (operation == "ADD") {
            result = cc->EvalAdd(ct_a, ct_b);
        } else if (operation == "SUB") {
            result = cc->EvalSub(ct_a, ct_b);
        } else if (operation == "MUL") {
            result = cc->EvalMult(ct_a, ct_b);
        } else if (operation == "NEG") {
            result = cc->EvalNegate(ct_a);
        } else if (operation == "ADDI") {
            result = cc->EvalAdd(ct_a, 3.0);       // a + 3
        } else if (operation == "SUBI") {
            result = cc->EvalSub(ct_a, 2.0);       // a - 2
        } else if (operation == "MULI") {
            result = cc->EvalMult(ct_a, 4.0);      // a * 4
        } else if (operation == "ADD_ADD") {
            auto tmp = cc->EvalAdd(ct_a, ct_b);
            result = cc->EvalAdd(tmp, ct_a);
        } else if (operation == "ADD_SUB") {
            auto tmp = cc->EvalAdd(ct_a, ct_b);
            result = cc->EvalSub(tmp, ct_a);
        } else if (operation == "MUL_ADD") {
            auto tmp = cc->EvalMult(ct_a, ct_b);
            result = cc->EvalAdd(tmp, ct_a);
        } else if (operation == "ADD_MUL") {
            auto tmp = cc->EvalAdd(ct_a, ct_b);
            result = cc->EvalMult(tmp, ct_a);
        } else if (operation == "ALL_NO_MUL") {
            // Combines: add, sub, neg, addi, subi, muli
            // ((a + b) - a + 3 - 2) * 4 = (b + 1) * 4
            auto t1 = cc->EvalAdd(ct_a, ct_b);       // a + b
            auto t2 = cc->EvalSub(t1, ct_a);         // b
            auto t3 = cc->EvalAdd(t2, 3.0);          // b + 3
            auto t4 = cc->EvalSub(t3, 2.0);          // b + 1
            auto t5 = cc->EvalMult(t4, 4.0);         // (b + 1) * 4
            auto t6 = cc->EvalNegate(t5);             // -(b + 1) * 4
            result = cc->EvalNegate(t6);              // (b + 1) * 4
        } else {
            std::cerr << "Unknown operation: " << operation << std::endl;
            std::cerr << "Valid: ADD SUB MUL NEG ADDI SUBI MULI ADD_ADD ADD_SUB MUL_ADD ADD_MUL ALL_NO_MUL" << std::endl;
            return 1;
        }

        niobium::compiler().probe("result", result);
        niobium::compiler().stop();
    }

    // ---- Replay ----
    std::cout << "\n--- Replay ---" << std::endl;
    if (!niobium::compiler().replay()) {
        std::cerr << "[ERROR] Replay failed" << std::endl;
        return 1;
    }

    // ---- Retrieve and serialize result ----
    Ciphertext<DCRTPoly> ct_result;
    if (niobium::compiler().result(cc, "result", ct_result)) {
        Serial::SerializeToFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY);
        std::cout << "Result written to " << keyDir << "/ct_result.bin" << std::endl;
    } else {
        std::cerr << "[ERROR] Could not retrieve result" << std::endl;
        return 1;
    }

    return 0;
}
