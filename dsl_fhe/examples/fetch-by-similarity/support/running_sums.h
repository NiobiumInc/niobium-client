#ifndef RUNNING_SUMS_H_
#define RUNNING_SUMS_H_
/// running-sums.h - Computing the running sums acorss ciphertext slots
//============================================================================
// Copyright (c) 2025, Amazon Web Services
// All rights reserved.
//
// This software is licensed under the terms of the Apache License v2.
// See the file LICENSE.md for details.
//============================================================================
/// This module implements the shift-and-add running sum algorithm, extended
/// in a few ways:
///   - It can work in strides, viewing the ciphertext as a matrix,
///     similar to EvalSumCols;
///   - It can operate on a vector of ciphertexts rather than just one
///   - It can get a bound on the mult-by-constant depth.
///
/// Example: suppose we have three ciphertexts with n_slots=8 and stride=4.
/// This means that we consider each ciphertext as a 2-by-4 matrix and these
/// matrices are interleaved to form a 6-by-4 matrix:
/// Input: [ a1 b1 c1 d1  a4 b5 c4 d4 ]
///        [ a2 b2 c2 d2  a5 b6 c5 d5 ],
///        [ a3 b3 c3 d3  a6 b6 c6 d6 ]
/// represents the matrix: [ a1 b1 c1 d1 ]
///                        [ a2 b2 c2 d2 ]
///                        [ a3 b3 c3 d3 ]
///                        [ a4 b4 c4 d4 ]
///                        [ a5 b5 c5 d5 ]
///                        [ a6 b6 c6 d6 ]
/// We compute running sums in each column, so the expected output is:
/// [a1       b1       c1       d1       a1+..+a4 b1+..+b4 c1+..+c4 d1+..+d4],
/// [a1+a2    b1+b2    c1+c2    d1+d2    a1+..+a5 b1+..+b5 c1+..+c5 d1+..+d5],
/// [a1+a2+a3 b1+b2+b3 c1+c2+c3 d1+d2+d3 a1+..+a6 b1+..+b6 c1+..+c6 d1+..+d6]
///
/// The reason for the interleacing is to keep the complexity low, essentially
/// independent of the number of ciphertexts.
/// Complexity is dominated by the number of automorphisms. With the default
/// depth D=log2(n_slots/stride), the number of automorphisms is also D.
/// For a smaller depth bound B<D, the number of automorphisms is roughly
/// B * (2^{ceil(D/B)} -1).

#include <vector>
#include "openfhe.h"

class RunningSums {
private:
  lbcrypto::CryptoContext<lbcrypto::DCRTPoly> cc;
  std::vector<std::map<int, lbcrypto::Plaintext>> masks;

public:
  /// @brief Initializing a new running-sum structure
  /// @param cc The CryptoContext, determines the number of slots
  /// @param stride Number of columns when viweing the input as a matrix
  /// @param depth_budget Mult-by-constant depth, defaults to log(n_slots/dtride)
  /// @param top_level Top level of input ciphertexts that would be input
  //                   to the eval method of this object (default=0)
  explicit RunningSums(const lbcrypto::CryptoContext<lbcrypto::DCRTPoly>& cc,
      int stride=1, int depth_budget=0, int top_level=0);

  /// @brief Compute the running sums in-place
  /// @param ctxts The input/output ciphertexts
  void eval_in_place(std::vector<lbcrypto::Ciphertext<lbcrypto::DCRTPoly> >& ctxts) const;

  /// A helper function that returns all the shift amounts,
  /// they can be fed into CryptoContext->EvalAtIndexKeyGen(...)
  std::vector<int> get_shift_amounts() const {
    std::vector<int> keys;
    for (auto& phase_masks: masks) {
      for (auto& kv : phase_masks) {
        keys.push_back(kv.first);
      }
    }
    return keys;
  }

  // A similar helper function that does not require an initialized object
  static std::vector<int> get_shift_amounts(int n_slots, int stride=1, int depth_budget=0);


  // Helper function to convert from slots to matrix representation and back

  /// Rearrange the matrix entries in slots that can be encrypted
  static std::vector< std::vector<double> > from_matrix_form(
        const std::vector<std::vector<double> >& matriv, size_t n_slots);

  /// Rearrange the slots according to the running-sum order
  static std::vector< std::vector<double> > to_matrix_form(
        const std::vector<std::vector<double> >& slots, size_t n_cols);
};
#endif // #ifndef RUNNING_SUMS_H_