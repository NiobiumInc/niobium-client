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

    // Compiler-matching FHETCH address layout:
    //   1..24    — inputs / scratch (ct_a, ct_b, plus reserved slots)
    //   25..48   — evalmult keys
    //   49..     — evalautomorphism keys
    // OpenFHE polynomials get a FHETCH address the moment they are
    // constructed (during deserialization), so we must interleave
    // reserve_addresses() calls with the deserialization order.
    niobium::compiler().reserve_addresses(1);

    // ---- Load ciphertexts (consume low addresses 1..16) ----
    Ciphertext<DCRTPoly> ct_a, ct_b;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext a");
    if (!Serial::DeserializeFromFile(keyDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext b");

    // ---- Reserve slots 17..24 (compiler's VirtualZero range) and load keys ----
    niobium::compiler().reserve_addresses(25);
    {
        std::ifstream mkStream(keyDir + "/mk.bin", std::ios::in | std::ios::binary);
        if (mkStream.is_open()) {
            if (!cc->DeserializeEvalMultKey(mkStream, SerType::BINARY))
                throw std::runtime_error("Failed to load eval mult key");
            std::cout << "Loaded eval mult key" << std::endl;
        }
    }
    {
        std::ifstream rkStream(keyDir + "/rk.bin", std::ios::in | std::ios::binary);
        if (rkStream.is_open()) {
            if (!cc->DeserializeEvalAutomorphismKey(rkStream, SerType::BINARY))
                throw std::runtime_error("Failed to load eval automorphism key");
            std::cout << "Loaded eval automorphism key" << std::endl;
        }
    }

    // ---- Capture crypto context and tag polys for simulator ----
    niobium::compiler().capture_crypto_context(cc);
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);
    niobium::compiler().tag_keys(cc);

    // Saved copy of what OpenFHE actually computed — used by the differential
    // below to diff against whatever the simulator ends up producing.
    Ciphertext<DCRTPoly> ct_openfhe;

    if (!niobium::compiler().is_cache_valid()) {
        // ---- RECORDING PHASE ----
        std::cout << "\n--- Recording EvalMult ---" << std::endl;
        niobium::compiler().start();

        auto ct_result = cc->EvalMult(ct_a, ct_b);

        niobium::compiler().probe("result", ct_result);
        niobium::compiler().stop();

        ct_openfhe = ct_result;  // stash for diff after replay
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
        // ---- Differential: compare simulator output vs OpenFHE's own EvalMult result ----
        if (ct_openfhe) {
            std::cout << "\n--- Differential: simulator vs OpenFHE ---" << std::endl;
            const auto& oe_elems = ct_openfhe->GetElements();
            const auto& sm_elems = ct_result->GetElements();
            if (oe_elems.size() != sm_elems.size()) {
                std::cerr << "[DIFF] ciphertext size mismatch: openfhe=" << oe_elems.size()
                          << " simulator=" << sm_elems.size() << std::endl;
            } else {
                for (size_t comp = 0; comp < oe_elems.size(); ++comp) {
                    const auto& oe_towers = oe_elems[comp].GetAllElements();
                    const auto& sm_towers = sm_elems[comp].GetAllElements();
                    for (size_t t = 0; t < oe_towers.size(); ++t) {
                        const auto& ov = oe_towers[t].GetValues();
                        const auto& sv = sm_towers[t].GetValues();
                        size_t diffs = 0;
                        uint64_t first_bad = 0;
                        uint64_t oe0 = 0, sm0 = 0;
                        for (size_t i = 0; i < ov.GetLength(); ++i) {
                            if (ov[i] != sv[i]) {
                                if (diffs == 0) {
                                    first_bad = i;
                                    oe0 = ov[i].ConvertToInt();
                                    sm0 = sv[i].ConvertToInt();
                                }
                                ++diffs;
                            }
                        }
                        std::cout << "  comp=" << comp << " tower=" << t
                                  << " mod=0x" << std::hex
                                  << oe_towers[t].GetModulus().ConvertToInt() << std::dec
                                  << (diffs == 0 ? "  MATCH"
                                      : "  DIVERGE at [" + std::to_string(first_bad)
                                        + "]: openfhe=" + std::to_string(oe0)
                                        + " sim=" + std::to_string(sm0)
                                        + " (" + std::to_string(diffs) + "/"
                                        + std::to_string(ov.GetLength()) + " slots differ)")
                                  << std::endl;
                    }
                }
            }
            std::cout << std::endl;
        }

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
