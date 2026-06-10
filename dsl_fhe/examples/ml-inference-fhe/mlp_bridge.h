#ifndef MLP_BRIDGE_H_
#define MLP_BRIDGE_H_

#include "openfhe.h"

using namespace lbcrypto;

ConstCiphertext<DCRTPoly> mlp(CryptoContext<DCRTPoly> cc,
                               ConstCiphertext<DCRTPoly> ct);

#endif  // MLP_BRIDGE_H_
