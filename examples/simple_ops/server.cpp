// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simple ops example — server side (CKKS)
//
// Loads crypto context + keys + ciphertexts, records a chosen operation,
// replays through the FHETCH simulator, and serializes the result.
//
// Supported operations:
//   ADD                    a + b
//   SUB                    a - b
//   MUL                    a * b  (requires relinearization key)
//   NEG                    -a
//   ADD_ADD                (a + b) + a                             = 2a + b
//   ADD_SUB                (a + b) - a                             = b
//   ADD_NEG                (a + b) + (-a)                          = b
//   MUL_ADD                (a * b) + a
//   ADD_MUL                (a + b) * a
//   MUL_MUL                (a * b) * a
//   NEG_MUL_ADD            ((-a) * b) + a + b
//   ADD_NEG_MUL_ADD        ((a + b) * (-a)) + b
//   MAT_PATTERN            (a + b) * (a - b)
//   ALL_NO_MUL             (a + b - a + 3 - 2) * 4  = (b + 1) * 4
//
//   immediate ops, default immediate shown, overridable via --immediate=N:
//   ADDI                   a + immediate                       (default 3)
//   SUBI                   a - immediate                       (default 2)
//   MULI                   a * immediate                       (default 4)
//   MULI_ADDI              (a * immediate) + immediate         (default 4)
//   ADDI_MULI              (a + immediate) * immediate         (default 4)
//   MULI_ADD               (a * immediate) + b                 (default 4)
//
//   rotation ops, default rotation shown, overridable via --immediate=N:
//   ROTATE_ADD             rotate(a, immediate) + b                                  (default 1)
//   ADD_ROTATE             rotate(a + b, immediate)                                  (default 1)
//   ROTATE_MUL             rotate(a, immediate) * b                                  (default 1)
//   ROTATE_ROTATE          rotate(a, immediate) + rotate(b, -immediate)              (default 1)
//   MULI_ROT_ADD_MUL_SUBI  ((rotate(a * immediate, immediate) + b) * b) - immediate  (default 1)
//   MORPH                  rotate(a, 1)  -- slot i of a holds a + (i % slot_period)
//
// Usage: ./simple_ops_server [key_dir [operation]] [--immediate=N]

#include "openfhe.h"
#include "niobium/compiler.h"

#include <optional>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

int main(int argc, char* argv[]) {
    std::string keyDir = "simple_ops_keys";
    std::string operation = "ADD";
    std::optional<double> immediate;

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) operation = argv[2];
    for (int i = 3; i < argc; ++i) {
        const std::string arg = argv[i];
        const std::string flag = "--immediate=";
        if (arg.rfind(flag, 0) == 0)
            immediate = std::stod(arg.substr(flag.size()));
    }

    // ---- Niobium compiler setup ----
    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info(
        "simple_ops_server", "1.0", "CKKS simple ops — FHETCH trace");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "simple_ops"});
    params.push_back({"op", operation});
    if (immediate)
        params.push_back({"immediate", std::to_string(*immediate)});
    niobium::compiler().cache_parameters(params);

    std::cout << "=== Simple Ops — Server ===" << std::endl;
    std::cout << "Operation: " << operation << std::endl;

    // ---- Load crypto context ----
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    // ---- Load ciphertexts and keys (addresses assigned later at tag time) ----
    Ciphertext<DCRTPoly> ct_a, ct_b;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_a.bin", ct_a, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext a");
    if (!Serial::DeserializeFromFile(keyDir + "/ct_b.bin", ct_b, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext b");

    // Load only the keys this operation uses — tag_keys() ships whatever the
    // context holds, so loading nothing extra uploads nothing extra.
    // Only ct*ct multiplies need the relinearization key; scalar EvalMult
    // (MULI, ALL_NO_MUL) does not.
    const bool needs_mult_key = operation == "MUL" || operation == "MUL_ADD" ||
                                operation == "ADD_MUL" || operation == "MUL_MUL" ||
                                operation == "NEG_MUL_ADD" || operation == "ADD_NEG_MUL_ADD" ||
                                operation == "MAT_PATTERN" || operation == "ROTATE_MUL" ||
                                operation == "MULI_ROT_ADD_MUL_SUBI";
    const bool needs_rotation_key = operation == "MORPH" || operation == "ROTATE_ADD" ||
                                    operation == "ADD_ROTATE" || operation == "ROTATE_MUL" ||
                                    operation == "ROTATE_ROTATE" || operation == "MULI_ROT_ADD_MUL_SUBI";
    if (needs_mult_key) {
        std::ifstream mkStream(keyDir + "/mk.bin", std::ios::binary);
        if (mkStream.is_open())
            cc->DeserializeEvalMultKey(mkStream, SerType::BINARY);
    }
    if (needs_rotation_key) {
        std::ifstream rkStream(keyDir + "/rk.bin", std::ios::binary);
        if (rkStream.is_open())
            cc->DeserializeEvalAutomorphismKey(rkStream, SerType::BINARY);
    }

    // ---- Capture crypto context and tag polys ----
    niobium::compiler().capture_crypto_context(cc);
    niobium::compiler().tag_input("ct_a", ct_a);
    niobium::compiler().tag_input("ct_b", ct_b);
    // Keyless sessions are valid: tag_keys only serializes eval keys; context
    // metadata comes from capture_crypto_context.
    if (needs_mult_key || needs_rotation_key)
        niobium::compiler().tag_keys(cc);

    // Saved copy of OpenFHE's own computed result — diffed against the
    // simulator's output after replay.
    Ciphertext<DCRTPoly> ct_openfhe;

    const bool hollow = niobium::compiler().is_hollow_mode();  // set by --hollow
    if (!niobium::compiler().is_cache_valid()) {
        std::cout << "\n--- Recording " << operation << " ("
                  << (hollow ? "hollow" : "real") << " mode) ---" << std::endl;
        niobium::compiler().enable_hollow_mode(hollow);
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
            result = cc->EvalAdd(ct_a, immediate.value_or(3.0));
        } else if (operation == "SUBI") {
            result = cc->EvalSub(ct_a, immediate.value_or(2.0));
        } else if (operation == "MULI") {
            result = cc->EvalMult(ct_a, immediate.value_or(4.0));
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
        } else if (operation == "MUL_MUL") {
            auto tmp = cc->EvalMult(ct_a, ct_b);
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
        } else if (operation == "ADD_NEG") {
            auto tmp = cc->EvalAdd(ct_a, ct_b);
            result = cc->EvalAdd(tmp, cc->EvalNegate(ct_a));  // (a + b) + (-a) = b
        } else if (operation == "NEG_MUL_ADD") {
            auto tmp = cc->EvalMult(cc->EvalNegate(ct_a), ct_b);  // (-a) * b
            tmp = cc->EvalAdd(tmp, ct_a);
            result = cc->EvalAdd(tmp, ct_b);
        } else if (operation == "ADD_NEG_MUL_ADD") {
            auto tmp = cc->EvalAdd(ct_a, ct_b);                       // a + b
            tmp = cc->EvalMult(tmp, cc->EvalNegate(ct_a));            // (a + b) * (-a)
            result = cc->EvalAdd(tmp, ct_b);
        } else if (operation == "MAT_PATTERN") {
            auto sum = cc->EvalAdd(ct_a, ct_b);
            auto diff = cc->EvalSub(ct_a, ct_b);
            result = cc->EvalMult(sum, diff);        // (a + b) * (a - b)
        } else if (operation == "MULI_ADDI") {
            double imm = immediate.value_or(4.0);
            result = cc->EvalMult(ct_a, imm);
            result = cc->EvalAdd(result, imm);       // (a * imm) + imm
        } else if (operation == "ADDI_MULI") {
            double imm = immediate.value_or(4.0);
            result = cc->EvalAdd(ct_a, imm);
            result = cc->EvalMult(result, imm);      // (a + imm) * imm
        } else if (operation == "MULI_ADD") {
            double imm = immediate.value_or(4.0);
            result = cc->EvalMult(ct_a, imm);
            result = cc->EvalAdd(result, ct_b);      // (a * imm) + b
        } else if (operation == "ROTATE_ADD") {
            int rot = static_cast<int>(immediate.value_or(1.0));
            auto rotated = cc->EvalRotate(ct_a, rot);
            result = cc->EvalAdd(rotated, ct_b);     // rotate(a, rot) + b
        } else if (operation == "ADD_ROTATE") {
            int rot = static_cast<int>(immediate.value_or(1.0));
            result = cc->EvalAdd(ct_a, ct_b);
            result = cc->EvalRotate(result, rot);    // rotate(a + b, rot)
        } else if (operation == "ROTATE_MUL") {
            int rot = static_cast<int>(immediate.value_or(1.0));
            auto rotated = cc->EvalRotate(ct_a, rot);
            result = cc->EvalMult(rotated, ct_b);    // rotate(a, rot) * b
        } else if (operation == "ROTATE_ROTATE") {
            int rot = static_cast<int>(immediate.value_or(1.0));
            auto rot1 = cc->EvalRotate(ct_a, rot);
            auto rot2 = cc->EvalRotate(ct_b, -rot);
            result = cc->EvalAdd(rot1, rot2);        // rotate(a, rot) + rotate(b, -rot)
        } else if (operation == "MULI_ROT_ADD_MUL_SUBI") {
            double imm = immediate.value_or(1.0);
            result = cc->EvalMult(ct_a, imm);
            result = cc->EvalRotate(result, static_cast<int>(imm));
            result = cc->EvalAdd(result, ct_b);
            result = cc->EvalMult(result, ct_b);
            result = cc->EvalSub(result, imm);       // ((rotate(a*imm, imm) + b) * b) - imm
        } else if (operation == "MORPH") {
            // ct_a holds the slot ramp (see client.cpp): slot i = a + (i %
            // slot_period). Rotating left by 1 brings a + 1 into slot 0.
            result = cc->EvalRotate(ct_a, 1);
        } else {
            std::cerr << "Unknown operation: " << operation << std::endl;
            std::cerr << "Valid: ADD SUB MUL NEG ADDI SUBI MULI ADD_ADD ADD_SUB ADD_NEG MUL_ADD "
                      << "ADD_MUL MUL_MUL NEG_MUL_ADD ADD_NEG_MUL_ADD MAT_PATTERN ALL_NO_MUL "
                      << "MULI_ADDI ADDI_MULI MULI_ADD ROTATE_ADD ADD_ROTATE "
                      << "ROTATE_MUL ROTATE_ROTATE MULI_ROT_ADD_MUL_SUBI MORPH" << std::endl;
            return 1;
        }

        // Compress to one tower: decrypt needs only the last tower, and the
        // compressed ciphertext is what travels back from a remote worker.
        result = cc->Compress(result, 1);

        niobium::compiler().probe("result", result);
        niobium::compiler().stop();
        niobium::compiler().enable_hollow_mode(false);  // replay does real math
        // A hollow record holds placeholder values, so there is no reference to diff.
        if (!hollow)
            ct_openfhe = result;
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
        if (ct_openfhe) {
            // Both sides are the Compress()ed single-tower result.
            std::cout << "\n--- Differential: simulator vs OpenFHE ---" << std::endl;
            const auto& oe = ct_openfhe->GetElements();
            const auto& sm = ct_result->GetElements();
            if (oe.size() != sm.size()) {
                std::cerr << "[DIFF] ciphertext size mismatch: "
                          << oe.size() << " vs " << sm.size() << std::endl;
            } else {
                for (size_t c = 0; c < oe.size(); ++c) {
                    const auto& oet = oe[c].GetAllElements();
                    const auto& smt = sm[c].GetAllElements();
                    for (size_t t = 0; t < oet.size(); ++t) {
                        const auto& ov = oet[t].GetValues();
                        const auto& sv = smt[t].GetValues();
                        size_t diffs = 0; size_t first = 0;
                        uint64_t oe0 = 0, sm0 = 0;
                        for (size_t i = 0; i < ov.GetLength(); ++i) {
                            if (ov[i] != sv[i]) {
                                if (diffs == 0) { first = i;
                                    oe0 = ov[i].ConvertToInt();
                                    sm0 = sv[i].ConvertToInt(); }
                                ++diffs;
                            }
                        }
                        std::cout << "  comp=" << c << " tower=" << t
                                  << " mod=0x" << std::hex << oet[t].GetModulus().ConvertToInt() << std::dec
                                  << (diffs == 0 ? "  MATCH"
                                      : "  DIVERGE at [" + std::to_string(first)
                                        + "]: openfhe=" + std::to_string(oe0)
                                        + " sim=" + std::to_string(sm0)
                                        + " (" + std::to_string(diffs) + "/"
                                        + std::to_string(ov.GetLength()) + ")")
                                  << std::endl;
                    }
                }
            }
            std::cout << std::endl;
        }
        if (!Serial::SerializeToFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY)) {
            std::cerr << "[ERROR] Failed to serialize result ciphertext" << std::endl;
            return 1;
        }
        std::cout << "Result written to " << keyDir << "/ct_result.bin" << std::endl;
    } else {
        std::cerr << "[ERROR] Could not retrieve result" << std::endl;
        return 1;
    }

    return 0;
}
