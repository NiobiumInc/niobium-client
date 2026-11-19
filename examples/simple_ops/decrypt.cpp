// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simple ops example — decrypt and verify (CKKS)
//
// Usage: ./simple_ops_decrypt [key_dir [operation]]

#include "openfhe.h"

#include <cmath>
#include <optional>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

constexpr uint32_t kDefaultSlotPeriod = 64;

// C++'s % keeps the sign of its left operand; slot indices need the
// mathematical, always-non-negative result (e.g. mod_floor(-5, 16) == 11).
static int mod_floor(int value, int period) {
    int r = value % period;
    return r < 0 ? r + period : r;
}

int main(int argc, char* argv[]) {
    std::string keyDir = "simple_ops_keys";
    std::string operation = "ADD";
    std::optional<double> immediate;
    uint32_t slot_period = kDefaultSlotPeriod;

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) operation = argv[2];
    for (int i = 3; i < argc; ++i) {
        const std::string arg = argv[i];
        const std::string imm_flag = "--immediate=";
        const std::string period_flag = "--slot-period=";
        if (arg.rfind(imm_flag, 0) == 0)
            immediate = std::stod(arg.substr(imm_flag.size()));
        else if (arg.rfind(period_flag, 0) == 0)
            slot_period = static_cast<uint32_t>(std::stoul(arg.substr(period_flag.size())));
    }

    std::cout << "=== Simple Ops — Decrypt (" << operation << ") ===" << std::endl;

    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    PrivateKey<DCRTPoly> secretKey;
    if (!Serial::DeserializeFromFile(keyDir + "/sk.bin", secretKey, SerType::BINARY))
        throw std::runtime_error("Failed to load secret key");

    Ciphertext<DCRTPoly> ct_result;
    if (!Serial::DeserializeFromFile(keyDir + "/ct_result.bin", ct_result, SerType::BINARY))
        throw std::runtime_error("Failed to load result ciphertext");

    double a = 0, b = 0;
    {
        std::ifstream valStream(keyDir + "/values.txt");
        valStream >> a >> b;
    }

    Plaintext pt_result;
    cc->Decrypt(secretKey, ct_result, &pt_result);
    pt_result->SetLength(1);

    double result = pt_result->GetRealPackedValue()[0];

    // Compute expected value based on operation
    double expected = 0;
    if (operation == "ADD")          expected = a + b;
    else if (operation == "SUB")     expected = a - b;
    else if (operation == "MUL")     expected = a * b;
    else if (operation == "NEG")     expected = -a;
    else if (operation == "ADDI")    expected = a + immediate.value_or(3.0);
    else if (operation == "SUBI")    expected = a - immediate.value_or(2.0);
    else if (operation == "MULI")    expected = a * immediate.value_or(4.0);
    else if (operation == "ADD_ADD") expected = 2 * a + b;
    else if (operation == "ADD_SUB") expected = b;
    else if (operation == "ADD_NEG") expected = b;
    else if (operation == "MUL_ADD") expected = a * b + a;
    else if (operation == "ADD_MUL") expected = (a + b) * a;
    else if (operation == "MUL_MUL") expected = a * b * a;
    else if (operation == "NEG_MUL_ADD") expected = (-a) * b + a + b;
    else if (operation == "ADD_NEG_MUL_ADD") expected = (a + b) * (-a) + b;
    else if (operation == "MAT_PATTERN") expected = (a + b) * (a - b);
    else if (operation == "ALL_NO_MUL") expected = (b + 1.0) * 4.0;
    else if (operation == "MULI_ADDI") {
        double imm = immediate.value_or(4.0);
        expected = a * imm + imm;
    } else if (operation == "ADDI_MULI") {
        double imm = immediate.value_or(4.0);
        expected = (a + imm) * imm;
    } else if (operation == "MULI_ADD") {
        expected = a * immediate.value_or(4.0) + b;
    } else if (operation == "ROTATE_ADD") {
        int rot = static_cast<int>(immediate.value_or(1.0));
        expected = (a + mod_floor(rot, static_cast<int>(slot_period))) + b;
    } else if (operation == "ADD_ROTATE") {
        int rot = static_cast<int>(immediate.value_or(1.0));
        expected = a + b + mod_floor(rot, static_cast<int>(slot_period));
    } else if (operation == "ROTATE_MUL") {
        int rot = static_cast<int>(immediate.value_or(1.0));
        expected = (a + mod_floor(rot, static_cast<int>(slot_period))) * b;
    } else if (operation == "ROTATE_ROTATE") {
        int rot = static_cast<int>(immediate.value_or(1.0));
        int period = static_cast<int>(slot_period);
        expected = (a + mod_floor(rot, period)) + (b + mod_floor(-rot, period));
    } else if (operation == "MULI_ROT_ADD_MUL_SUBI") {
        double imm = immediate.value_or(1.0);
        int rot = static_cast<int>(imm);
        double rotated = a * imm + mod_floor(rot, static_cast<int>(slot_period));
        expected = (rotated + b) * b - imm;
    } else if (operation == "MORPH") {
        // ct_a holds the slot ramp (client.cpp): slot i = a + (i % slot_period).
        expected = a + mod_floor(1, static_cast<int>(slot_period));
    } else {
        std::cerr << "Unknown operation: " << operation << std::endl;
        return 1;
    }

    double diff = std::abs(result - expected);
    double tolerance = 0.01;

    std::cout << "Result: " << result << " (expected " << expected
              << ", diff " << diff << ")" << std::endl;

    if (diff < tolerance) {
        std::cout << "[PASS] " << operation << ": " << result << " ~= " << expected << std::endl;
        return 0;
    } else {
        std::cerr << "[FAIL] " << operation << ": " << result << " != " << expected
                  << " (diff " << diff << " > " << tolerance << ")" << std::endl;
        return 1;
    }
}
