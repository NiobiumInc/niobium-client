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

// running-sums.cpp - Compute running sums acorss ciphertext slots.
//============================================================================
// Copyright (c) 2025, Amazon Web Services
// All rights reserved.
//
// This software is licensed under the terms of the Apache License v2.
// See the file LICENSE.md for details.
//============================================================================

#include "running_sums.h"
using namespace lbcrypto;

#ifdef NIOBIUM_COMPILER
#include "niobium/compiler.h"
#endif

// Some utility functions

inline int divc(int a, int b) {  // division with ceiling
  return (a + b - 1) / b;
}
// Encode a mask of the form {0 0 ... 0 1 1 ... 1}
static Plaintext mask4shift(const CryptoContext<DCRTPoly>& cc, int amt,
                            int level) {
  // Make sure that amt is in [0,n_slots-1]
  int n_slots = cc->GetRingDimension() / 2;
  amt %= n_slots;
  if (amt < 0) {
    amt += n_slots;
  }
  std::vector<double> mask(n_slots);
  for (int i = amt; i < n_slots; i++) {
    mask[i] = 1.0;
  }
#ifdef NIOBIUM_COMPILER
  niobium::compiler().pause();
#endif
  Plaintext result = cc->MakeCKKSPackedPlaintext(mask, 1, level);
#ifdef NIOBIUM_COMPILER
    niobium::compiler().tag_input("mask4shift", result);
    niobium::compiler().resume();
#endif
  return result;
}

// A helper function that returns all the shift amounts,
// they can be fed into CryptoContext->EvalAtIndexKeyGen(...)
std::vector<int> RunningSums::get_shift_amounts(int n_slots, int stride,
                                                int depth_budget) {
  // Currently we only support n_slots which is a power-of-two
  int logn = static_cast<int>(std::log2(n_slots));
  if (n_slots != (1 << logn)) {
    throw std::runtime_error("n_slots must be a power of two");
  }
  if (n_slots % stride != 0) {
    throw std::runtime_error("stride must divide n_slots");
  }
  // How many intervals of size stride fit in the slots of a ciphertext
  int n_intervals = n_slots / stride;
  int logn_intervals = static_cast<int>(std::log2(n_intervals));

  if (depth_budget <= 0 || depth_budget > logn_intervals) {  // fix depth
    depth_budget = logn_intervals;
  }

  int factor = 1 << divc(logn_intervals, depth_budget);
  // The shift amounts are decreasing by this factor for each phase
  // of the shift-and-add procedure

  // All phases but the last use factor-1 shift amounts
  std::vector<int> shift_amounts;
  while (n_intervals > factor) {
    n_intervals /= factor;
    for (int i = factor - 1; i > 0; i--) {
      shift_amounts.push_back(-stride * n_intervals * i);
      // Negative amount since OpenFHE rotates to the left
    }
  }
  // Last phase uses (whatever is left of n_strides) minus one
  if (n_intervals > 1) {
    for (int i = n_intervals - 1; i > 0; i--) {
      shift_amounts.push_back(-stride * i);
      // Negative amount since OpenFHE rotates to the left
    }
  }
  return shift_amounts;
}

/// Initializing a new running-sum structure (see header file)
RunningSums::RunningSums(const CryptoContext<DCRTPoly>& _cc, int stride,
                         int depth_budget, int level)
    : cc(_cc) {
  // Currently we only support n_slots which is a power-of-two
  int n_slots = cc->GetRingDimension() / 2;
  int logn = static_cast<int>(std::log2(n_slots));
  if (n_slots != (1 << logn)) {
    throw std::runtime_error("n_slots must be a power of two");
  }
  if (n_slots % stride != 0) {
    throw std::runtime_error("stride must divide n_slots");
  }

  // How many intervals of size stride fit in the slots of a ciphertext
  int n_intervals = n_slots / stride;
  int logn_intervals = static_cast<int>(std::log2(n_intervals));

  if (depth_budget <= 0 || depth_budget > logn_intervals) {  // fix depth
    depth_budget = logn_intervals;
  }

  int factor = 1 << divc(logn_intervals, depth_budget);
  // The shift amounts are decreasing by this factor for each phase
  // of the shift-and-add procedure

  // All phases but the last use factor-1 shift amounts
  while (n_intervals > factor) {
    n_intervals /= factor;
    std::map<int, Plaintext> phase_masks;  // masks for this phase
    for (int i = factor - 1; i > 0; i--) {
      int amt = stride * n_intervals * i;  // shift amount
      phase_masks.insert(std::make_pair(-amt, mask4shift(cc, amt, level)));
      // Negative amt since OpenFHE rotates to the left
    }
    this->masks.push_back(phase_masks);
    level++;  // The next phase masks should be encoded at a lower levels
  }
  // Last phase uses (whatever is left of n_strides) minus one
  if (n_intervals > 1) {
    std::map<int, Plaintext> phase_masks;  // masks for this phase
    for (int i = n_intervals - 1; i > 0; i--) {
      int amt = stride * i;  // shift amount
      phase_masks.insert(std::make_pair(-amt, mask4shift(cc, amt, level)));
      // Negative amt since OpenFHE rotates to the left
    }
    this->masks.push_back(phase_masks);
  }
}

/// Compute the running sums in-place, see details in the header file
void RunningSums::eval_in_place(
    std::vector<lbcrypto::Ciphertext<lbcrypto::DCRTPoly> >& ctxts) const {
  // Start by computing running-sums acorss the different ciphertexts
  for (size_t i = 1; i < ctxts.size(); i++) {
    ctxts[i] = cc->EvalAdd(ctxts[i - 1], ctxts[i]);
  }

  // Now perform the shift-and-add procedure on ctxts[n-1],
  // each time adding the shifted ciphertext to all the cipehrtexts
  for (auto& phase_masks : this->masks) {
    bool first = true;
    Ciphertext<DCRTPoly> acc;  // accumulator
    for (auto& [amt, mask] : phase_masks) {
      // Rotate the ctxt.back() by amt slots and multiply by the mask
      if (first) {
        acc = cc->EvalRotate(ctxts.back(), amt);
        acc = cc->EvalMult(acc, mask);
        first = false;
      } else {
        auto tmp = cc->EvalRotate(ctxts.back(), amt);
        tmp = cc->EvalMult(tmp, mask);
        cc->EvalAddInPlace(acc, tmp);
      }
    }
    // Add to all the ciphertexts
    for (auto& ct : ctxts) {
      ct = cc->EvalAdd(ct, acc);
    }
  }
}

// Rearrange the matrix entries in slots that can be encrypted
std::vector<std::vector<double> > RunningSums::from_matrix_form(
    const std::vector<std::vector<double> >& matrix, size_t n_slots) {
  if (matrix.empty() || matrix[0].empty()) {
    return {};
  }
  // Allocate space
  size_t n_rows = matrix.size();
  size_t n_cols = matrix[0].size();
  if (n_slots < n_cols || n_slots % n_cols != 0) {
    throw std::runtime_error("n_slots must be divisible by n_cols");
  }
  if ((n_rows * n_cols) % n_slots != 0) {
    throw std::runtime_error("n_entries must be divisible by n_slots");
  }
  std::vector<std::vector<double> > slots((n_cols * n_rows) / n_slots);
  for (auto& v : slots) {
    v.resize(n_slots);
  }

  // Go over the slots vector, filling them n_cols entries at a time
  for (size_t i = 0; i < n_rows; i++) {
    size_t slots_i = i % slots.size();
    size_t slots_j = n_cols * (i / slots.size());
    for (size_t j = 0; j < n_cols; j++, slots_j++) {
      slots[slots_i][slots_j] = matrix[i][j];
    }
  }
  return slots;
}

// Rearrange the slots according to the running-sum order
std::vector<std::vector<double> > RunningSums::to_matrix_form(
    const std::vector<std::vector<double> >& slots, size_t n_cols) {
  if (slots.empty() || slots[0].empty()) {
    return {};
  }
  if (slots[0].size() < n_cols || slots[0].size() % n_cols != 0) {
    throw std::runtime_error("n_slots must be divisible by n_cols");
  }
  int n_rows_per_vector = slots[0].size() / n_cols;
  // Allocate space
  std::vector<std::vector<double> > matrix(slots.size() * n_rows_per_vector);
  for (auto& row : matrix) {
    row.resize(n_cols);
  }

  // Fill the matrix rows one at a time
  for (size_t i = 0; i < matrix.size(); i++) {
    size_t slots_i = i % slots.size();
    size_t slots_j = n_cols * (i / slots.size());
    for (size_t j = 0; j < n_cols; j++, slots_j++) {
      matrix[i][j] = slots[slots_i][slots_j];
    }
  }
  return matrix;
}
