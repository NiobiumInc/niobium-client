// Copyright (C) 2023-2026, All rights reserved by Niobium Microsystems.
// The contents of this file and all related materials provided herein (the
// "Product") may not be used except pursuant to a separate written
// agreement signed by a duly authorized officer of Niobium Microsystems,
// Inc. (a "License Agreement").
// Without limiting the foregoing, you may not, at any time or for any
// reason, directly or indirectly, in whole or in part: (i) copy, modify,
// or create derivative works of the Product; (ii) rent, lease, lend, sell,
// sublicense, assign, distribute, publish, transfer, or otherwise make
// available the Product; (iii) reverse engineer, disassemble, decompile,
// decode, or adapt the Product; or (iv) remove any proprietary notices
// from the Product.

#include "openfhe.h"

#include <iostream>
#include <iomanip>
#include <string>

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"

#include "utils.h"
#include "params.h"

using namespace lbcrypto;

int main(int argc, char** argv) {
  if (argc < 6 || !std::isdigit(argv[1][0])) {
    std::cout << "Usage: " << argv[0] << " instance-size <value_a> <value_b> <output_a.bin> <output_b.bin>\n";
    std::cout << "  Instance-size: 0-TOY, 1-SMALL, 2-MEDIUM, 3-LARGE\n";
    std::cout << "  value_a: First number to encrypt\n";
    std::cout << "  value_b: Second number to encrypt\n";
    std::cout << "  output_a.bin: Output file for encrypted value A\n";
    std::cout << "  output_b.bin: Output file for encrypted value B\n";
    return 0;
  }

  auto size = static_cast<InstanceSize>(std::stoi(argv[1]));
  InstanceParams prms(size);

  // Parse input values
  double num1 = std::stod(argv[2]);
  double num2 = std::stod(argv[3]);

  // Parse output file names
  std::string outputFileA = argv[4];
  std::string outputFileB = argv[5];

  std::string outputFileZero = "zero.bin";

  std::cout << "Input value A: " << std::fixed << std::setprecision(14) << num1 << std::endl;
  std::cout << "Input value B: " << std::fixed << std::setprecision(14) << num2 << std::endl;
  std::cout << "Output file A: " << outputFileA << std::endl;
  std::cout << "Output file B: " << outputFileB << std::endl;

  // Load crypto context
  CryptoContext<DCRTPoly> cc;
  if (!Serial::DeserializeFromFile(prms.keydir()/"cc.bin", cc, SerType::BINARY)) {
    throw std::runtime_error("Failed to get CryptoContext from "+prms.keydir().string());
  }

  // Print crypto context parameters
  std::cout << "Crypto Context Parameters:" << std::endl;
  std::cout << "  Ring Dimension: " << cc->GetRingDimension() << std::endl;
  auto ccParams = cc->GetCryptoParameters();
  std::cout << "  Modulus Chain Length: " << ccParams->GetElementParams()->GetParams().size() << std::endl;

  // Load public key for encryption
  PublicKey<DCRTPoly> pk;
  if (!Serial::DeserializeFromFile(prms.keydir()/"pk.bin", pk, SerType::BINARY)) {
    throw std::runtime_error("Failed to get public key from "+prms.keydir().string());
  }

  // Encode plaintexts with the default full slot set (ring_dim/2). The
  // compiler-side version of this example passes slots=1 to
  // MakeCKKSPackedPlaintext; the FHETCH simulator replay we use for
  // verification here is tuned for full-slot encodings, so we omit the
  // slot-1 override and keep the encoding consistent with the rest of
  // niobium-client's CKKS examples.
  std::vector<double> x1{num1};
  Plaintext pt1 = cc->MakeCKKSPackedPlaintext(x1);
  Ciphertext<DCRTPoly> ct1 = cc->Encrypt(pk, pt1);

  std::vector<double> x2{num2};
  Plaintext pt2 = cc->MakeCKKSPackedPlaintext(x2);
  Ciphertext<DCRTPoly> ct2 = cc->Encrypt(pk, pt2);

  std::vector<double> xZero{0};
  Plaintext ptZero = cc->MakeCKKSPackedPlaintext(xZero);
  Ciphertext<DCRTPoly> ctZero = cc->Encrypt(pk, ptZero);

  // Serialize ciphertexts to files
  if (!Serial::SerializeToFile(outputFileA, ct1, SerType::BINARY)) {
    throw std::runtime_error("Failed to serialize ciphertext to " + outputFileA);
  }
  std::cout << "✓ Encrypted value A written to " << outputFileA << std::endl;

  if (!Serial::SerializeToFile(outputFileB, ct2, SerType::BINARY)) {
    throw std::runtime_error("Failed to serialize ciphertext to " + outputFileB);
  }
  std::cout << "✓ Encrypted value B written to " << outputFileB << std::endl;

  if (!Serial::SerializeToFile(outputFileZero, ctZero, SerType::BINARY)) {
    throw std::runtime_error("Failed to serialize ciphertext to " + outputFileZero);
  }
  std::cout << "✓ Encrypted Zero written to " << outputFileZero << std::endl;

  std::cout << "✓ Client encryption completed successfully" << std::endl;
  return 0;
}