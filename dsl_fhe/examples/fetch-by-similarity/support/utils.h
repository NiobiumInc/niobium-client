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

#ifndef FHEBENCH_UTILS_H_
#define FHEBENCH_UTILS_H_
// utils.h - Utility declerations for fetch-by-similarity
//============================================================================
// Copyright (c) 2025, Amazon Web Services
// All rights reserved.
//
// This software is licensed under the terms of the Apache License v2.
// See the file LICENSE.md for details.
//============================================================================
#include <string>
#include <filesystem>
#include <iostream>
#include <fstream>
#include <vector>
#include <set>

template<typename T>
std::vector<T> vector_union(std::vector<std::vector<T> >& vecs)
{
  std::set<T> combinedSet;

  // Insert elements from both vectors into the set
  for (const auto& v : vecs) {
    combinedSet.insert(v.begin(), v.end());
  }

  // Create a new vector from the set
  std::vector<T> result(combinedSet.begin(), combinedSet.end());
  return result;
}

/// Read a binary file into a vector of vectors, all of dimension record_dim
template<typename T> std::vector<std::vector<T>> read2vecs(
    std::filesystem::path fname, int record_dim)
{
  std::ifstream file(fname, std::ios::binary);
  if (!file.is_open()) {
    throw std::runtime_error("Cannot open " + fname.string() + " for read");
  }
  // Calculate size of the matrix
  file.seekg(0, std::ios::end);
  std::streampos nbytes = file.tellg();
  file.seekg(0, std::ios::beg);
  auto nrecords = nbytes / (record_dim * sizeof(T));

  std::vector<std::vector<T>> a(nrecords);
  for (auto& r : a) {
    r.resize(record_dim);
    file.read(reinterpret_cast<char*>(&r[0]), record_dim * sizeof(T));
  }
  file.close();
  return a;
}

// Write a binary file containing the matrix in vecs
template<typename T> void write2disk(
    std::filesystem::path fname,const std::vector<std::vector<T>>& vecs)
{
  std::ofstream file(fname, std::ios::binary);
  if (!file.is_open()) {
    throw std::runtime_error("Cannot open " + fname.string() + " for write");
  }
  for (auto& v : vecs) {
    file.write(reinterpret_cast<const char*>(&v[0]), v.size() * sizeof(T));
  }
  file.close();
}

/// Encode the dataset in column order: The input is an n-by-m matrix that
/// we want to transpose, but the rows of the output cannot have dimension
/// above n_slots. To accomodate input matrices with more than n_slots rows,
/// the output is split into ceil(n/n_slots) matrices, each of dimension
/// m-by-n_slots, where the rows of the last one may be padded with zeros.
template<typename T>
std::vector<std::vector<std::vector<double> > > transpose_matrix(
    std::vector<std::vector<T> > &mat, size_t n_slots)
{
  // ceil( mat.size()/n_slots )
  auto n_ctxt_per_row = (mat.size() + n_slots - 1) / n_slots;
  auto record_dim = mat[0].size();

  //  std::cout << "n_ctxt_per_row=" << n_ctxt_per_row
  //            << ", record_dim=" << record_dim << std::endl;

  // Allocate space
  std::vector<std::vector<std::vector<double>>> transposed(n_ctxt_per_row);
  for (auto& batch : transposed) {
    batch.resize(record_dim);
    for (auto& record : batch) {
      record.assign(n_slots, 0.0);
    }
  }

  // encode in batches of n_slots records at a time
  for (size_t i = 0; i < n_ctxt_per_row; i++) {  // go over the batches
    // transpose the next n_slots rows in db
    for (size_t j = 0; j < record_dim; j++) {
      for (size_t k = 0; k < n_slots; k++) {
        auto idx = (i * n_slots) + k;
        if (idx < mat.size()) {
          transposed[i][j][k] = mat[idx][j];
        } else {
          break;
        }
      }
    }
  }
  return transposed;  // return the encoded matrix
}

#include <chrono>
#include <iomanip>
#include <sstream>
/// Returns the current time in the format H:M:S, and also duration
/// since last call in seconds (or 0 if this is the first call).
inline std::tuple<std::string,int64_t> getCurrentTimeFormatted() {
    using namespace std::chrono;
    static std::chrono::system_clock::time_point previous;
    auto now = system_clock::now();
    auto now_c = system_clock::to_time_t(now);

    // Format hours, minutes, seconds
    std::stringstream ss;
    ss << std::put_time(std::localtime(&now_c), "%H:%M:%S");

    // If not the 1st call, also print duration
    int64_t n_seconds = 0;
    if (previous != system_clock::time_point{}) {
      // Compute the duration between now and previous and report it
      n_seconds = duration_cast<seconds>(now - previous).count();
    }
    previous = now;
    return std::make_pair(ss.str(), n_seconds);
}

/// StopWatch — collects named timing entries with millisecond precision
/// and prints a formatted summary table.
///
/// Usage:
///   StopWatch sw("server_batch");
///   sw.lap("load cc.bin");
///   // ... do work ...
///   sw.lap("load pk.bin");
///   // ... do work ...
///   sw.print_summary();  // prints [TIMING] tagged lines for parsing
class StopWatch {
public:
    explicit StopWatch(std::string context)
        : context_(std::move(context)),
          start_(std::chrono::steady_clock::now()),
          last_(start_) {}

    /// Record a lap. Call AFTER the operation completes.
    void lap(const std::string& name) {
        auto now = std::chrono::steady_clock::now();
        double ms = std::chrono::duration<double, std::milli>(now - last_).count();
        entries_.push_back({name, ms});
        last_ = now;
    }

    /// Get elapsed ms since construction (total wall time).
    double elapsed_ms() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration<double, std::milli>(now - start_).count();
    }

    /// Print a formatted summary table to stdout.
    /// All lines are tagged with [TIMING] for easy grep/parsing.
    void print_summary() const {
        double total = 0;
        for (auto& e : entries_) total += e.ms;

        std::cout << "\n[TIMING] ========== " << context_ << " ==========\n";
        std::cout << "[TIMING] " << std::left << std::setw(45) << "Operation"
                  << std::right << std::setw(12) << "Time (s)"
                  << std::setw(10) << "%" << "\n";
        std::cout << "[TIMING] " << std::string(67, '-') << "\n";

        for (auto& e : entries_) {
            double pct = (total > 0) ? (e.ms / total * 100.0) : 0.0;
            std::cout << "[TIMING] " << std::left << std::setw(45) << e.name
                      << std::right << std::fixed << std::setprecision(3)
                      << std::setw(12) << (e.ms / 1000.0)
                      << std::setprecision(1) << std::setw(9) << pct << "%"
                      << "\n";
        }

        std::cout << "[TIMING] " << std::string(67, '-') << "\n";
        std::cout << "[TIMING] " << std::left << std::setw(45) << "TOTAL"
                  << std::right << std::fixed << std::setprecision(3)
                  << std::setw(12) << (total / 1000.0) << "\n";
        std::cout << "[TIMING] " << std::string(67, '=') << "\n";
        std::cout << std::flush;
    }

private:
    struct Entry { std::string name; double ms; };
    std::string context_;
    std::chrono::steady_clock::time_point start_;
    std::chrono::steady_clock::time_point last_;
    std::vector<Entry> entries_;
};

#endif  // ifdef FHEBENCH_UTILS_H_
