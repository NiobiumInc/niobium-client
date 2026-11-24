// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Matrix ops example — server side (CKKS)
//
// Loads matrix A (rows1 x cols1) and B (rows2 x cols2), records/replays
// C = A x B, one ciphertext per cell, via the public record/replay API.
//
// Usage: ./matrix_ops_server [key_dir [rows1 [cols1 [rows2 [cols2]]]]] [--target=N]
//   --target=N forwards to niobium::compiler().init(); default is local
//   (the client's own fhetch_sim).

#include "openfhe.h"
#include "niobium/compiler.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

using namespace lbcrypto;

static Ciphertext<DCRTPoly> load(const std::string& keyDir, const char* name, int i, int j) {
    Ciphertext<DCRTPoly> ct;
    std::string filename = keyDir + "/matrix_" + name + "_" +
                           std::to_string(i) + "_" + std::to_string(j) + ".bin";
    if (!Serial::DeserializeFromFile(filename, ct, SerType::BINARY))
        throw std::runtime_error("Failed to load " + filename);
    return ct;
}

int main(int argc, char* argv[]) {
    std::string keyDir = "matrix_ops_keys";
    int rows1 = 2, cols1 = 2, rows2 = 2, cols2 = 2;

    if (argc > 1) keyDir = argv[1];
    if (argc > 2) rows1 = std::stoi(argv[2]);
    if (argc > 3) cols1 = std::stoi(argv[3]);
    if (argc > 4) rows2 = std::stoi(argv[4]);
    if (argc > 5) cols2 = std::stoi(argv[5]);

    if (cols1 != rows2)
        throw std::runtime_error("cols1 must equal rows2 for A x B");

    niobium::compiler().init(argc, argv);
    niobium::compiler().set_program_info(
        "matrix_ops_server", "1.0", "CKKS matrix multiply — FHETCH trace");
    niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);

    niobium::Compiler::CacheParameters params;
    params.push_back({"workload", "matrix_ops"});
    params.push_back({"rows1", std::to_string(rows1)});
    params.push_back({"cols1", std::to_string(cols1)});
    params.push_back({"rows2", std::to_string(rows2)});
    params.push_back({"cols2", std::to_string(cols2)});
    niobium::compiler().cache_parameters(params);

    std::cout << "=== Matrix Ops — Server ===" << std::endl;
    std::cout << "C = A(" << rows1 << "x" << cols1 << ") x B(" << rows2 << "x" << cols2 << ")" << std::endl;

    CryptoContext<DCRTPoly> cc;
    if (!Serial::DeserializeFromFile(keyDir + "/cc.bin", cc, SerType::BINARY))
        throw std::runtime_error("Failed to load crypto context");

    std::vector<std::vector<Ciphertext<DCRTPoly>>> A(rows1, std::vector<Ciphertext<DCRTPoly>>(cols1));
    for (int i = 0; i < rows1; ++i)
        for (int j = 0; j < cols1; ++j)
            A[i][j] = load(keyDir, "a", i, j);

    std::vector<std::vector<Ciphertext<DCRTPoly>>> B(rows2, std::vector<Ciphertext<DCRTPoly>>(cols2));
    for (int i = 0; i < rows2; ++i)
        for (int j = 0; j < cols2; ++j)
            B[i][j] = load(keyDir, "b", i, j);

    std::ifstream mkStream(keyDir + "/mk.bin", std::ios::binary);
    if (!mkStream.is_open() || !cc->DeserializeEvalMultKey(mkStream, SerType::BINARY))
        throw std::runtime_error("Failed to load eval mult key");

    niobium::compiler().capture_crypto_context(cc);
    for (int i = 0; i < rows1; ++i)
        for (int j = 0; j < cols1; ++j)
            niobium::compiler().tag_input("A_" + std::to_string(i) + "_" + std::to_string(j), A[i][j]);
    for (int i = 0; i < rows2; ++i)
        for (int j = 0; j < cols2; ++j)
            niobium::compiler().tag_input("B_" + std::to_string(i) + "_" + std::to_string(j), B[i][j]);
    niobium::compiler().tag_keys(cc);

    const bool hollow = niobium::compiler().is_hollow_mode();
    std::vector<std::vector<Ciphertext<DCRTPoly>>> C(rows1, std::vector<Ciphertext<DCRTPoly>>(cols2));
    if (!niobium::compiler().is_cache_valid()) {
        std::cout << "--- Recording (" << (hollow ? "hollow" : "real") << " mode) ---" << std::endl;
        niobium::compiler().enable_hollow_mode(hollow);
        niobium::compiler().start();

        for (int i = 0; i < rows1; ++i) {
            for (int j = 0; j < cols2; ++j) {
                Ciphertext<DCRTPoly> acc;
                for (int k = 0; k < cols1; ++k) {
                    auto prod = cc->EvalMult(A[i][k], B[k][j]);
                    acc = acc ? cc->EvalAdd(acc, prod) : prod;
                }
                C[i][j] = acc;
                niobium::compiler().probe("C_" + std::to_string(i) + "_" + std::to_string(j), C[i][j]);
            }
        }

        niobium::compiler().stop();
        niobium::compiler().enable_hollow_mode(false);
    }

    std::cout << "--- Replay ---" << std::endl;
    if (!niobium::compiler().replay()) {
        std::cerr << "[ERROR] Replay failed" << std::endl;
        return 1;
    }

    for (int i = 0; i < rows1; ++i) {
        for (int j = 0; j < cols2; ++j) {
            Ciphertext<DCRTPoly> result;
            std::string tag = "C_" + std::to_string(i) + "_" + std::to_string(j);
            if (!niobium::compiler().result(cc, tag, result)) {
                std::cerr << "[ERROR] Could not retrieve " << tag << std::endl;
                return 1;
            }
            std::string filename = keyDir + "/matrix_c_" + std::to_string(i) + "_" + std::to_string(j) + ".bin";
            if (!Serial::SerializeToFile(filename, result, SerType::BINARY)) {
                std::cerr << "[ERROR] Failed to serialize " << filename << std::endl;
                return 1;
            }
        }
    }

    std::cout << "Result written to " << keyDir << "/matrix_c_*.bin" << std::endl;
    return 0;
}
