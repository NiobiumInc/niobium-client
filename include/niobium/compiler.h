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
// Niobium Client — Minimal Compiler API
//
// User-facing API for controlling FHETCH instruction trace recording.
// This is the strict-minimum subset of the Niobium compiler interface
// needed to record instruction traces on the client side.
//
// Usage:
//   #include "niobium/compiler.h"
//   niobium::compiler().init(argc, argv);
//   niobium::compiler().start();
//   // ... OpenFHE operations (probes fire automatically) ...
//   niobium::compiler().stop();

#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace niobium {

class Compiler {
public:
    Compiler();
    ~Compiler();

    // Non-copyable, non-movable (singleton)
    Compiler(const Compiler&) = delete;
    Compiler& operator=(const Compiler&) = delete;

    // ====================================================================
    // SESSION LIFECYCLE
    // ====================================================================

    /// Initialize compiler with command-line arguments.
    /// Parses and consumes Niobium-specific flags from argv.
    void init(int& argc, char** argv);

    /// Begin instruction recording.
    /// Must be called before performing any FHE operations to record.
    /// @return true if recording started successfully.
    bool start();

    /// Stop instruction recording and finalize the FHETCH trace.
    /// Writes the trace file and serializes input/output metadata.
    /// @return true if recording stopped successfully.
    bool stop();

    /// Temporarily pause recording.
    /// Use to exclude deserialization or plaintext operations from the trace.
    bool pause();

    /// Resume recording after pause().
    bool resume();

    // ====================================================================
    // PROGRAM METADATA
    // ====================================================================

    /// Set program information for debugging and identification.
    void set_program_info(const std::string& name,
                          const std::string& version,
                          const std::string& description);

    /// Set build information for traceability.
    /// @param file      Source file path (use __FILE__).
    /// @param line      Source line number (use __LINE__).
    /// @param timestamp Build timestamp (use __TIMESTAMP__).
    void set_build_info(const std::string& file, int line,
                        const std::string& timestamp);

    // ====================================================================
    // CACHE MANAGEMENT
    // ====================================================================

    /// Vector of key-value pairs used to determine cache validity.
    typedef std::vector<std::pair<std::string, std::string>> CacheParameters;

    /// Set cache parameters for instruction trace validation.
    /// Cache parameters uniquely identify the computation configuration.
    void cache_parameters(CacheParameters& params);

    /// Check if the cached instruction trace is valid for reuse.
    /// @return true if cache is valid (skip recording), false if recording needed.
    bool is_cache_valid();

    // ====================================================================
    // INPUT / OUTPUT TAGGING (OpenFHE types)
    // ====================================================================
    // These templates accept OpenFHE Ciphertext, Plaintext, and vector
    // thereof. The user includes "openfhe.h" in their own code; this
    // header does not depend on OpenFHE.

    /// Tag an input ciphertext/plaintext for recording.
    /// @param input_name  Unique name for the input.
    /// @param value       OpenFHE Ciphertext or Plaintext to tag.
    /// @param file        Optional file path for data loading during replay.
    template<typename T>
    void tag_input(const std::string& input_name,
                   const T& value,
                   std::optional<std::filesystem::path> file = std::nullopt);

    /// Tag an input (non-const overload, for in-place ID capture).
    template<typename T>
    void tag_input(const std::string& input_name,
                   T& value,
                   std::optional<std::filesystem::path> file = std::nullopt);

    /// Tag an output ciphertext/plaintext for recording.
    /// Call for all computation outputs before stop().
    /// @param var_name     Unique name for the output variable.
    /// @param value        OpenFHE Ciphertext or Plaintext to tag as output.
    template<typename T>
    void probe(const std::string& var_name, const T& value);

    /// Tag a vector of output ciphertexts for recording.
    template<typename T>
    void probe(const std::string& var_name, const std::vector<T>& values);

    // ====================================================================
    // CRYPTO CONTEXT
    // ====================================================================

    /// Capture the cryptographic context for serialization.
    /// Must be called after all keys are loaded and before recording starts.
    /// @param cc  OpenFHE CryptoContext (lbcrypto::CryptoContext<DCRTPoly>).
    template<typename CryptoContextType>
    void capture_crypto_context(const CryptoContextType& cc);

    // ====================================================================
    // RECORDING MODES
    // ====================================================================

    /// Enable or disable hollow recording mode.
    /// When enabled, OpenFHE operations skip expensive polynomial math but
    /// preserve structure and fire probes. Reduces recording time from hours
    /// to seconds for large workloads.
    void enable_hollow_mode(bool enabled = true);

    /// Check if hollow recording mode is active.
    bool is_hollow_mode() const;

    /// Enable multi-threaded recording mode.
    /// Call before start() when using multithreading in user code.
    void enable_multithreaded_recording();

    /// Check if multi-threaded recording is enabled.
    bool is_multithreaded() const;

    // ====================================================================
    // FHETCH MODE
    // ====================================================================

    /// Check if operating in FHETCH mode (set automatically when any
    /// FHETCH API function is called, vs. OpenFHE probe path).
    bool is_fhetch_mode() const;

    /// Activate FHETCH mode (called internally by FHETCH API functions).
    void set_fhetch_mode();

    // ====================================================================
    // FUNCTIONAL EPOCHS
    // ====================================================================

    /// Mark the start of a new epoch's computation.
    /// On first call, memorizes the polynomial ID base. On subsequent calls,
    /// resets the counter back to keep each epoch's address space predictable.
    void start_epoch();

    /// Trigger a functional epoch boundary.
    /// Performs: stop → write trace → reset state → resume recording.
    /// Each epoch is cached independently in program_dir/epoch_N/.
    bool stop_epoch();

    /// Get the current epoch ID (0 before first stop_epoch() call).
    uint32_t epoch_id() const;

    // ====================================================================
    // STATE QUERIES
    // ====================================================================

    /// Check if the compiler is currently recording instructions.
    bool running_p();

    /// Check if stop() has been called (recording is complete).
    bool stopped_p();

    /// Get the current program name (including cache parameters).
    std::string program_name();

    /// Get or create the program directory for output files.
    std::filesystem::path get_program_directory() const;

    // ====================================================================
    // CONVENIENCE
    // ====================================================================

    /// Run a lambda with automatic start/stop bracketing.
    template<typename Lambda, typename... Args>
    void run(Lambda&& work, Args&&... args) {
        start();
        std::forward<Lambda>(work)(std::forward<Args>(args)...);
        stop();
    }

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;

    // Internal library access (fhetch_api.cpp, probes.cpp).
    friend Impl& compiler_impl(Compiler& c);
};

/// Get the global Compiler singleton instance.
Compiler& compiler();

}  // namespace niobium
