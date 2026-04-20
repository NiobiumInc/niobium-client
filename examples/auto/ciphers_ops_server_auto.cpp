// Copyright (C) 2023-2026, All rights reserved by Niobium Microsystems.
//
// ciphers_ops_server_auto.cpp — Auto-facade example
//
// Identical FHE logic to ciphers_ops_server.cpp but with zero Niobium
// instrumentation boilerplate.  Build with -DOPENFHE_CPROBES=ON and
// place ciphers_ops_server_auto.niobium_compiler.yml alongside the binary
// (or in CWD) to get automatic record/replay.
//
// Usage:
//   ciphers_ops_server_auto <instance-size> <input_a.bin> <input_b.bin> <expected|expected.txt> [operation] [immediate_value]
//
//   instance-size: 0=TOY, 1=SMALL, 2=MEDIUM, 3=LARGE
//   operation:     ADD (default), MUL, MUL_MUL, ADD_MUL, MUL_ADD,
//                  MUL_ADD_NEG, NEG_MUL_ADD, ADD_NEG_MUL_ADD, ADD_ADD,
//                  ADD_NEG, ROTATE_ADD, ADD_ROTATE, ROTATE_MUL,
//                  ROTATE_ROTATE, MAT_PATTERN, ADDI, SUBI, MULI,
//                  MULI_ADDI, ADDI_MULI, MULI_ADD,
//                  ADD_ADDI_MULI_ADDI_MULI_NEG, MULI_ROT_ADD_MUL_SUBI,
//                  LARGE_ADD_MUL, MUL_MONOMIAL
//   immediate_value: rotation amount / scalar / loop count (where needed)

#include "openfhe.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

#include "utils.h"
#include "params.h"

#include <fstream>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

#include "niobium_auto_hooks.h"


using namespace lbcrypto;

// ---------------------------------------------------------------------------
// FHE operation functions (identical to ciphers_ops_server.cpp)
// ---------------------------------------------------------------------------

static Ciphertext<DCRTPoly> op_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalAdd(ct1, ct2);
}

static Ciphertext<DCRTPoly> op_mul(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalMult(ct1, ct2);
}

static Ciphertext<DCRTPoly> op_mul_mul(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalMult(cc->EvalMult(ct1, ct2), ct1);
}

static Ciphertext<DCRTPoly> op_add_mul(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalMult(cc->EvalAdd(ct1, ct2), ct1);
}

static Ciphertext<DCRTPoly> op_add_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalAdd(cc->EvalAdd(ct1, ct2), ct1);
}

static Ciphertext<DCRTPoly> op_add_neg(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    auto sum = cc->EvalAdd(ct1, ct2);
    return cc->EvalAdd(sum, cc->EvalNegate(ct1));
}

static Ciphertext<DCRTPoly> op_mul_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalAdd(cc->EvalMult(ct1, ct2), ct1);
}

static Ciphertext<DCRTPoly> op_mul_add_neg(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    auto r = cc->EvalMult(ct1, ct2);
    r = cc->EvalAdd(r, ct1);
    return cc->EvalAdd(r, cc->EvalNegate(ct2));
}

static Ciphertext<DCRTPoly> op_neg_mul_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    auto r = cc->EvalMult(cc->EvalNegate(ct1), ct2);
    r = cc->EvalAdd(r, ct1);
    return cc->EvalAdd(r, ct2);
}

static Ciphertext<DCRTPoly> op_add_neg_mul_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    auto r = cc->EvalMult(cc->EvalAdd(ct1, ct2), cc->EvalNegate(ct1));
    return cc->EvalAdd(r, ct2);
}

static Ciphertext<DCRTPoly> op_rotate_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, int rot) {
    return cc->EvalAdd(cc->EvalRotate(ct1, rot), ct2);
}

static Ciphertext<DCRTPoly> op_add_rotate(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, int rot) {
    return cc->EvalRotate(cc->EvalAdd(ct1, ct2), rot);
}

static Ciphertext<DCRTPoly> op_rotate_mul(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, int rot) {
    return cc->EvalMult(cc->EvalRotate(ct1, rot), ct2);
}

static Ciphertext<DCRTPoly> op_rotate_rotate(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, int rot) {
    return cc->EvalAdd(cc->EvalRotate(ct1, rot), cc->EvalRotate(ct2, -rot));
}

static Ciphertext<DCRTPoly> op_mat_pattern(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2) {
    return cc->EvalMult(cc->EvalAdd(ct1, ct2), cc->EvalSub(ct1, ct2));
}

static Ciphertext<DCRTPoly> op_addi(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, double v) {
    return cc->EvalAdd(ct1, v);
}

static Ciphertext<DCRTPoly> op_subi(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, double v) {
    return cc->EvalSub(ct1, v);
}

static Ciphertext<DCRTPoly> op_muli(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, double v) {
    return cc->EvalMult(ct1, v);
}

static Ciphertext<DCRTPoly> op_muli_addi(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, double v) {
    return cc->EvalAdd(cc->EvalMult(ct1, v), v);
}

static Ciphertext<DCRTPoly> op_addi_muli(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, double v) {
    return cc->EvalMult(cc->EvalAdd(ct1, v), v);
}

static Ciphertext<DCRTPoly> op_muli_add(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, double v) {
    return cc->EvalAdd(cc->EvalMult(ct1, v), ct2);
}

static Ciphertext<DCRTPoly> op_add_addi_muli_addi_muli_neg(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, double v) {
    auto r = cc->EvalAdd(ct1, ct2);
    r = cc->EvalAdd(r, v);
    r = cc->EvalMult(r, v);
    r = cc->EvalAdd(r, v);
    r = cc->EvalMult(r, v);
    return cc->EvalNegate(r);
}

static Ciphertext<DCRTPoly> op_muli_rot_add_mul_subi(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, double v) {
    auto r = cc->EvalMult(ct1, v);
    r = cc->EvalRotate(r, static_cast<int>(v));
    r = cc->EvalAdd(r, ct2);
    r = cc->EvalMult(r, ct2);
    return cc->EvalSub(r, v);
}

static Ciphertext<DCRTPoly> op_large_add_mul(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, Ciphertext<DCRTPoly> ct2, int n) {
    auto r = ct1;
    for (int i = 0; i < n; ++i) {
        r = cc->EvalAdd(r, ct2);
        r = cc->EvalMult(r, ct2);
    }
    return r;
}

static Ciphertext<DCRTPoly> op_mul_monomial(CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> ct1, int power) {
    return cc->GetScheme()->MultByMonomial(ct1, power);
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

static Ciphertext<DCRTPoly> dispatch(
    const std::string& op,
    CryptoContext<DCRTPoly> cc,
    Ciphertext<DCRTPoly> ct1,
    Ciphertext<DCRTPoly> ct2,
    double imm) {
    if (op == "ADD")                        return op_add(cc, ct1, ct2);
    if (op == "MUL")                        return op_mul(cc, ct1, ct2);
    if (op == "MUL_MUL")                    return op_mul_mul(cc, ct1, ct2);
    if (op == "ADD_MUL")                    return op_add_mul(cc, ct1, ct2);
    if (op == "ADD_ADD")                    return op_add_add(cc, ct1, ct2);
    if (op == "ADD_NEG")                    return op_add_neg(cc, ct1, ct2);
    if (op == "MUL_ADD")                    return op_mul_add(cc, ct1, ct2);
    if (op == "MUL_ADD_NEG")                return op_mul_add_neg(cc, ct1, ct2);
    if (op == "NEG_MUL_ADD")                return op_neg_mul_add(cc, ct1, ct2);
    if (op == "ADD_NEG_MUL_ADD")            return op_add_neg_mul_add(cc, ct1, ct2);
    if (op == "ROTATE_ADD")                 return op_rotate_add(cc, ct1, ct2, static_cast<int>(imm));
    if (op == "ADD_ROTATE")                 return op_add_rotate(cc, ct1, ct2, static_cast<int>(imm));
    if (op == "ROTATE_MUL")                 return op_rotate_mul(cc, ct1, ct2, static_cast<int>(imm));
    if (op == "ROTATE_ROTATE")              return op_rotate_rotate(cc, ct1, ct2, static_cast<int>(imm));
    if (op == "MAT_PATTERN")                return op_mat_pattern(cc, ct1, ct2);
    if (op == "ADDI")                       return op_addi(cc, ct1, imm);
    if (op == "SUBI")                       return op_subi(cc, ct1, imm);
    if (op == "MULI")                       return op_muli(cc, ct1, imm);
    if (op == "MULI_ADDI")                  return op_muli_addi(cc, ct1, imm);
    if (op == "ADDI_MULI")                  return op_addi_muli(cc, ct1, imm);
    if (op == "MULI_ADD")                   return op_muli_add(cc, ct1, ct2, imm);
    if (op == "ADD_ADDI_MULI_ADDI_MULI_NEG") return op_add_addi_muli_addi_muli_neg(cc, ct1, ct2, imm);
    if (op == "MULI_ROT_ADD_MUL_SUBI")      return op_muli_rot_add_mul_subi(cc, ct1, ct2, imm);
    if (op == "LARGE_ADD_MUL")              return op_large_add_mul(cc, ct1, ct2, static_cast<int>(imm));
    if (op == "MUL_MONOMIAL")               return op_mul_monomial(cc, ct1, static_cast<int>(imm));
    throw std::invalid_argument("Unknown operation: " + op);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char** argv) {
    if (argc < 5) {
        std::cout << "Usage: " << argv[0]
                  << " <instance-size> <input_a.bin> <input_b.bin>"
                     " <expected> [operation] [immediate_value]\n"
                  << "  instance-size: 0=TOY 1=SMALL 2=MEDIUM 3=LARGE\n"
                  << "  expected: a single number or a path to a file with one value per line\n";
        return 1;
    }

    const auto size      = static_cast<InstanceSize>(std::stoi(argv[1]));
    const std::string inputFileA = argv[2];
    const std::string inputFileB = argv[3];
    // Parse expected value(s): if argv[4] can be parsed as a double use it
    // directly; otherwise treat it as a file path and read one value per line.
    std::vector<double> expected_values;
    try {
        expected_values.push_back(std::stod(argv[4]));
    } catch (const std::invalid_argument&) {
        std::ifstream ef(argv[4]);
        if (!ef) {
            std::cerr << "ERROR: expected value is not a number and could not open file: " << argv[4] << std::endl;
            return 1;
        }
        double v;
        while (ef >> v) expected_values.push_back(v);
        if (expected_values.empty()) {
            std::cerr << "ERROR: expected values file is empty: " << argv[4] << std::endl;
            return 1;
        }
        std::cout << "Reading " << expected_values.size() << " expected values from " << argv[4] << std::endl;
    }
    const double expected = expected_values[0];
    const std::string operation  = argc >= 6 ? argv[5] : "ADD";
    const double imm             = argc >= 7 ? std::stod(argv[6]) : 0.0;

    InstanceParams prms(size);

    // -----------------------------------------------------------------------
    // Load crypto context
    // -----------------------------------------------------------------------
    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(prms.keydir() / "cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load CryptoContext from " + prms.keydir().string());

    // -----------------------------------------------------------------------
    // Load evaluation keys
    // -----------------------------------------------------------------------
    {
        std::ifstream f(prms.keydir() / "mk.bin", std::ios::binary);
        if (!f.is_open() || !cc->DeserializeEvalMultKey(f, SerType::BINARY))
            throw std::runtime_error("Failed to load EvalMult key");
    }
    {
        std::ifstream f(prms.keydir() / "rk.bin", std::ios::binary);
        if (!f.is_open() || !cc->DeserializeEvalAutomorphismKey(f, SerType::BINARY))
            throw std::runtime_error("Failed to load EvalAutomorphism key");
    }

    // -----------------------------------------------------------------------
    // Load input ciphertexts
    // -----------------------------------------------------------------------
    // Mirror ciphers_ops_server.cpp: skip loading ct2 for pure-immediate ops
    // that don't use a second ciphertext (e.g. MUL_MONOMIAL, ADDI, MULI, ...).
    const bool is_immediate_op = (operation == "ADDI" || operation == "SUBI" ||
        operation == "MULI" || operation == "MULI_ADDI" || operation == "ADDI_MULI" ||
        operation == "MULI_ADD" || operation == "ADD_ADDI_MULI_ADDI_MULI_NEG" ||
        operation == "ROTATE_ADD" || operation == "ADD_ROTATE" ||
        operation == "ROTATE_MUL" || operation == "ROTATE_ROTATE" ||
        operation == "MULI_ROT_ADD_MUL_SUBI" || operation == "LARGE_ADD_MUL" ||
        operation == "MUL_MONOMIAL");
    const bool needs_ct2 = (operation == "MULI_ADD" ||
        operation == "ADD_ADDI_MULI_ADDI_MULI_NEG" ||
        operation == "ROTATE_ADD" || operation == "ADD_ROTATE" ||
        operation == "ROTATE_MUL" || operation == "ROTATE_ROTATE" ||
        operation == "MULI_ROT_ADD_MUL_SUBI" || operation == "LARGE_ADD_MUL");

    Ciphertext<DCRTPoly> ct1, ct2;
    if (!Serial::DeserializeFromFile(inputFileA, ct1, SerType::BINARY))
        throw std::runtime_error("Failed to load ciphertext A from " + inputFileA);
    if (!is_immediate_op || needs_ct2) {
        if (!Serial::DeserializeFromFile(inputFileB, ct2, SerType::BINARY))
            throw std::runtime_error("Failed to load ciphertext B from " + inputFileB);
    }

    // -----------------------------------------------------------------------
    // Compute (first FHE op triggers auto-facade lazy_init)
    // -----------------------------------------------------------------------
    std::cout << "Operation: " << operation << std::endl;

    if (operation == "MUL_MONOMIAL") {
        if (setenv("NB_NO_DECODE_NOISE", "1", 1) != 0)
            std::cerr << "Failed to set NB_NO_DECODE_NOISE" << std::endl;
        if (expected_values.size() == 1)
            std::cout << "[WARNING]: Comparing MUL_MONOMIAL with only one expected value is extremely unreliable.\n"
                      << "mul_monomial_client produces a file with expected values for all slots. Pass the path to this as the expected argument." << std::endl;
    }

    auto result = dispatch(operation, cc, ct1, ct2, imm);

    // -----------------------------------------------------------------------
    // Decrypt — the auto-facade Decrypt hook handles:
    //   recording: probes the ciphertext as output before decrypting
    //   replay:    substitutes the dummy with the HW-computed result
    // -----------------------------------------------------------------------
    PrivateKey<DCRTPoly> sk;
    if (!Serial::DeserializeFromFile(prms.keydir() / "sk.bin", sk, SerType::BINARY))
        throw std::runtime_error("Failed to load secret key");

    Plaintext pt;
    cc->Decrypt(sk, result, &pt);
    pt->SetLength(expected_values.size());
    auto decoded = pt->GetCKKSPackedValue();
    std::vector<double> computed_values;
    for (size_t i = 0; i < expected_values.size(); i++)
        computed_values.push_back(i < decoded.size() ? decoded[i].real() : 0.0);
    const double computed = computed_values[0];

    // -----------------------------------------------------------------------
    // Verify
    // -----------------------------------------------------------------------
    constexpr double tolerance = 0.01;
    bool is_correct = true;

    std::cout << "The answer is " << std::defaultfloat << computed << "." << std::endl;
    if (expected_values.size() == 1) {
        double rounded_result   = std::round(computed * 1000.0) / 1000.0;
        double rounded_expected = std::round(expected * 1000.0) / 1000.0;
        is_correct = std::abs(rounded_result - rounded_expected) < tolerance;
        std::cout << "Expected: " << std::fixed << std::setprecision(3) << expected  << "\n";
        std::cout << "Computed: " << std::fixed << std::setprecision(3) << computed << "\n";
    } else {
        for (size_t i = 0; i < expected_values.size(); i++) {
            double diff = std::abs(computed_values[i] - expected_values[i]);
            if (diff >= tolerance) {
                is_correct = false;
                std::cout << "Slot " << i
                          << ": expected=" << std::fixed << std::setprecision(3) << expected_values[i]
                          << " computed=" << std::setprecision(3) << computed_values[i]
                          << " [diff=" << std::setprecision(5) << diff << "]\n";
            }
        }
        std::cout << "Expected: " << std::fixed << std::setprecision(3) << expected_values[0] << "\n";
        std::cout << "Computed: " << std::fixed << std::setprecision(3) << computed_values[0] << "\n";
    }

    if (is_correct) {
        std::cout << "✓ PASS: Result is correct (within tolerance of " << tolerance << ")\n";
    } else {
        std::cout << "✗ FAIL: Result is incorrect\n";
        return 1;
    }

    return 0;
}
