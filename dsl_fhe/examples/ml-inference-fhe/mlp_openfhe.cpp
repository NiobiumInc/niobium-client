// Copyright 2024-present Niobium Microsystems, Inc.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// ---------------------------------------------------------------------------
// STUB implementation of the HEIR v2 MLP `mnist()` entry point.
//
// This is a placeholder so the ml-inference example BUILDS self-contained,
// without the proprietary/large HEIR-generated model. It does NOT compute a
// real MLP forward pass — it simply echoes the input ciphertext through, so
// the pipeline links and runs end-to-end without producing meaningful logits.
//
// To run real inference, build against the actual ml-inference submission
// (the HEIR-generated mlp_openfhe.cpp + trained weights). See
// examples/ml-inference-fhe/HOWTO.md and the Makefile's SUBMISSION_DIR path.
// ---------------------------------------------------------------------------
#include "mlp_openfhe.h"

#include <iostream>
#include <vector>

std::vector<CiphertextT> mnist(CryptoContextT cc,
    std::vector<float> /*fc1_weight*/, std::vector<float> /*fc1_bias*/,
    std::vector<float> /*fc2_weight*/, std::vector<float> /*fc2_bias*/,
    std::vector<CiphertextT> input) {
    static bool warned = false;
    if (!warned) {
        std::cerr << "[mlp stub] WARNING: using the stub MLP model — output is "
                     "NOT real inference. Build against the ml-inference "
                     "submission for correct results.\n";
        warned = true;
    }
    if (input.empty()) return { CiphertextT() };
    // Perform one trivial homomorphic op so the pipeline records a real trace
    // and round-trips through record/replay. This is NOT an MLP forward pass —
    // it just keeps the example buildable and runnable without the real model.
    return { cc->EvalAdd(input[0], input[0]) };
}
