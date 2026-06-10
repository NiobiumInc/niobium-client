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

// slot-replication.cpp - a mechanism to replicate slots across ciphertexts
//============================================================================
// Copyright (c) 2025, Amazon Web Services
// All rights reserved.
//
// This software is licensed under the terms of the Apache License v2.
// See the file LICENSE.md for details.
//============================================================================
#include <algorithm>
#include <cassert>
#include <functional>
#include <memory>

#include "utils.h"
#include "slot_replication.h"

#ifdef NIOBIUM_COMPILER
#include "niobium/compiler.h"
#endif

using namespace lbcrypto;

// Controls print statements, undef for production
#undef VERBOSE

// The main replicator node implementation
class ReplicatorNode {
 private:
  std::shared_ptr<ReplicatorNode> parent;
  const int num_replicas;  // number of replicas that can be returned for
                           // each source-ctxt that we get from the parent
  int current;             // how may replicas already returned for this source

  std::vector<Ciphertext<DCRTPoly>> shifts;  // shifted versions of the source
  std::vector<Plaintext> masks;  // masks to apply to the shifted versions

  const int rot_amt;  // by how much to rotate each of the shifted CtxtPtr

  void generate_masks(CryptoContext<DCRTPoly>& cc);
  void install_source(const Ciphertext<DCRTPoly>& ct);

 public:
  ReplicatorNode(CryptoContext<DCRTPoly>& cc,
                 std::shared_ptr<ReplicatorNode> _parent, int _nreps, int _amt)
      : parent(_parent),       // set the parent so we can get sources from it
        num_replicas(_nreps),  // how many replicas to return per source
        current(_nreps),       // current==num_replicas signals missing source
        rot_amt(_amt) {
    if (_nreps < 2) {
      throw std::invalid_argument("degrees in the tree must all be >= 2");
    }
    shifts.resize(_nreps);
    generate_masks(cc);  // pre-compute the masks
  }

  // Override the default get_parent() method of the base class
  std::shared_ptr<ReplicatorNode> get_parent() { return parent; }
  int get_num_replicas() { return num_replicas; }

  // The main entry points, returns either the next replicated ciphertext
  // or a nullptr if no more replications can be returned.
  Ciphertext<DCRTPoly> init(const Ciphertext<DCRTPoly>& ct);
  Ciphertext<DCRTPoly> next_replica();
};

// Prepare the node with a new source ciphertext.
// This must never be called with ct == nullptr.
void ReplicatorNode::install_source(const Ciphertext<DCRTPoly>& ct) {
  auto cc = ct->GetCryptoContext();
  shifts[0] = ct;

  // shifts[0] now holds the new source, we compute all its rotations by
  // rot_amt, rot_amt*2,... If we need to compute more than one rotation
  // (i.e. num_replicas>2) then we use the "hoisting" technique from
  // https://ia.cr/2018/244, section 5.
  if (num_replicas == 2) {  // degree-2 node
    shifts[1] = cc->EvalRotate(ct, -rot_amt);
#ifdef VERBOSE
    std::cout << ">>" << rot_amt << ' ';
#endif
  } else {  // degree > 2
    // Break the ciphertext into digits (in NTT form) so we can
    // apply the NTT to them as needed for the hoisted automorphisms
    auto digits = cc->EvalFastRotationPrecompute(ct);

    // We use "fast" rotation in each amount, applying the
    // corresponding automorphism to each digit then key-switching
    // and adding them
    for (int i = 1; i < num_replicas; i++) {
      shifts[i] = cc->EvalFastRotation(ct, -i * rot_amt,
                                       cc->GetCyclotomicOrder(), digits);
#ifdef VERBOSE
      std::cout << ">>" << (i * rot_amt) << ' ';
#endif
    }
  }
  current = 0;  // we are ready to compute replicas of the new source
}

/// "Install" a ciphertext and return the 1st replicated ciphertext
Ciphertext<DCRTPoly> ReplicatorNode::init(const Ciphertext<DCRTPoly>& ct) {
  if (ct == nullptr) {
    return nullptr;
  }
  if (get_parent() == nullptr) {  // the root
    install_source(ct);
  } else {  // non-root
    install_source(get_parent()->init(ct));
  }
  return next_replica();
}

/// @brief next_replica - the main interface
/// @return the next replicated ciphertext in the tree
Ciphertext<DCRTPoly> ReplicatorNode::next_replica() {
  // If we need a new source then ask for one from your parent,
  // and then pre-process it to compute all the rotation amounts
  if (current == num_replicas) {  // need a new source
    if (get_parent() == nullptr) {
      return nullptr;
    }
    auto ct = get_parent()->next_replica();
    if (ct == nullptr) {
      return nullptr;  // out of replicas
    }
    install_source(ct);
  }
  auto cc = shifts[0]->GetCryptoContext();

  // Return the next replicated ciphertext, multiplying each shifted
  // ciphertext by the correspondin gmask and addin gthem up. Which mask
  // corresponds to what ciphertext depnds on the value of current.

  Ciphertext<DCRTPoly> acc = cc->EvalMult(shifts[0], masks[current]);
  for (int i = 1; i < num_replicas; i++) {
    Ciphertext<DCRTPoly> tmp =
        cc->EvalMult(shifts[i], masks[(i + current) % num_replicas]);
    cc->EvalAddInPlace(acc, tmp);
  }
  current++;  // ready to return the next replica (if any)
  return acc;
}

// A private method, generates num_replicas masks, each one zeros out all
// but 1/num_replicas of the slots, arranged in runs of length rot_amt.
// For example, if rot_amt=2 and num_replicas=4 then we have the following
// four masks:
//     (1 1 0 0 0 0 0 0 1 1 0 0 ... )
//     (0 0 1 1 0 0 0 0 0 0 1 1 ... )
//     (0 0 0 0 1 1 0 0 0 0 0 0 ... )
//     (0 0 0 0 0 0 1 1 0 0 0 0 ... )
void ReplicatorNode::generate_masks(CryptoContext<DCRTPoly>& cc) {
  int nslots = cc->GetRingDimension() / 2;
  int block_size = rot_amt * num_replicas;
  assert(nslots % block_size ==
         0);  // pattern-size must divide evenly the # of slots
  int nblocks = nslots / block_size;

  masks.resize(num_replicas);                  // allocate space
  std::vector<std::complex<double>> tmp_mask;  // A scratch working space

  // Compute the masks and encode them as Plaintext elements
  for (int i = 0; i < num_replicas; i++) {               // compute the ith mask
    tmp_mask.assign(nslots, std::complex<double>(0.0));  // rest to zero
    for (int b = 0; b < nblocks; b++) {  // set rot_amt slots to 1 in each block
      int run_start = b * block_size + i * rot_amt;
      for (int j = 0; j < rot_amt; j++) {
        tmp_mask[run_start + j] = std::complex<double>(1.0);
      }
    }
#ifdef NIOBIUM_COMPILER
    niobium::compiler().pause();
#endif
    // encode mask as Plaintext element and add to the list
    masks[i] = cc->MakeCKKSPackedPlaintext(tmp_mask);
#ifdef NIOBIUM_COMPILER
    niobium::compiler().tag_input("generate_masks", masks[i]);
    niobium::compiler().resume();
#endif
  }
}

inline bool isPowerOfTwo(int n) { return (n > 0) && ((n & (n - 1)) == 0); }

// Builds a replication tree to replicate the slots of a ciphertext.
// See the header file for detailed dsescription.
DFSSlotReplicator::DFSSlotReplicator(
    CryptoContext<DCRTPoly>& cc,  // the cryptocontext
    const std::vector<int>
        tree_degrees,      // the degrees of different levels in the tree
    int input_replication  // is the input already party replicated?
) {
  int num_slots = cc->GetRingDimension() / 2;
  if (input_replication <= 0) {
    throw std::runtime_error("input_replication must be at least 1");
  }
  if (num_slots % input_replication != 0) {
    throw std::runtime_error(
        "input_replication must divides the number of slots");
  }
  int pattern_len = num_slots / input_replication;

  // Verifies that all the degrees are >1, and that input_replication
  // times the product of the tree_degrees equals the number of slots.
  auto max_element =
      *(std::max_element(tree_degrees.begin(), tree_degrees.end()));
  if (max_element < 2) {
    throw std::runtime_error("Tree degrees must be at least 2");
  }
  if (num_slots != input_replication *
                       std::accumulate(tree_degrees.begin(), tree_degrees.end(),
                                       1, std::multiplies<int>())) {
    throw std::runtime_error(
        "Tree degrees must multiply to the number of slots");
  }

  // Construct a tree of replicator nodes

  std::shared_ptr<ReplicatorNode> current = nullptr;
  auto rot_amt = pattern_len;
  for (auto deg : tree_degrees) {
    rot_amt /= deg;
    current = std::make_shared<ReplicatorNode>(cc, current, deg, rot_amt);
  }
  this->handle = current;
}

// "Install" a ciphertext and return the 1st replicated ciphertext
Ciphertext<DCRTPoly> DFSSlotReplicator::init(Ciphertext<DCRTPoly>& ct) {
  return this->handle->init(ct);
}
// Returns the next replica in the replication tree
Ciphertext<DCRTPoly> DFSSlotReplicator::next_replica() {
  return this->handle->next_replica();
}

// Replicates each slot into a full ciphertext.
// This is a static function, see the header file for full description.
std::vector<Ciphertext<DCRTPoly>> DFSSlotReplicator::batch_replicate(
    Ciphertext<DCRTPoly> ct, std::vector<int> tree_degrees,
    int input_replication) {
  auto cc = ct->GetCryptoContext();
  DFSSlotReplicator replicator(cc, tree_degrees, input_replication);
  int num_results = cc->GetRingDimension() / (2 * input_replication);
  std::vector<Ciphertext<DCRTPoly>> result;
  result.reserve(num_results);
  for (auto ct_i = replicator.init(ct); ct_i != nullptr;
       ct_i = replicator.next_replica()) {
    result.push_back(ct_i);
  }
  if (int(result.size()) < num_results) {
    // The tree ran out of replicas, this is an error
    throw std::runtime_error("Not enough replicas in the tree");
  }
  return result;
}

// A helper function that returns rotation amounts for a tree.
// This is a static function, see the header file for full description.
std::vector<int> DFSSlotReplicator::get_rotation_amounts(
    std::vector<int> tree_degrees) {
  std::vector<int> result;

  // Start from the product of all the tree-degrees
  auto rot_amt = std::accumulate(tree_degrees.begin(), tree_degrees.end(), 1,
                                 std::multiplies<int>());

  // Each successive level has smaller rotation amounts
  for (auto deg : tree_degrees) {
    rot_amt /= deg;
    for (auto i = 1; i < deg; i++) {
      result.push_back(-i * rot_amt);
      // Negative since openfhe rotates to the left
    }
  }
  // sort(result.begin(), result.end()); // is this needed?
  return result;
}

// Traversing an existing tree and returning the degrees
std::vector<int> DFSSlotReplicator::get_degrees() {
  std::vector<int> result;

  // As long as we have a "real node" in the tree
  auto current = std::dynamic_pointer_cast<ReplicatorNode>(this->handle);
  while (current) {
    result.push_back(current->get_num_replicas());
    current = std::dynamic_pointer_cast<ReplicatorNode>(current->get_parent());
  }
  std::reverse(result.begin(), result.end());
  return result;
}

// A placeholder for a tool that suggest the tree shape to use
// This is a static function.
std::vector<int> DFSSlotReplicator::suggest_degrees(int num_outputs) {
  // Return a vector with the first entry at most 16, the second
  // at most 4, and all the others 2. This assumes that the number
  // of outputs is a power of two.
  assert(isPowerOfTwo(num_outputs));

  if (num_outputs <= 8) {  // very small trees are kept flat
    return (std::vector<int>({num_outputs}));
  }
  // The root has degree 8
  std::vector<int> degrees;
  degrees.push_back(8);
  num_outputs /= 8;

  // The second node is 4 (if needed)
  if (num_outputs >= 4) {
    degrees.push_back(4);
    num_outputs /= 4;
  }

  // All other levels have degree 2
  while (num_outputs > 1) {
    degrees.push_back(2);
    num_outputs /= 2;
  }
  return degrees;
}
