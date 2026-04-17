// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// FHETCH Simulator — instruction-set simulator for .fhetch trace files.
//
// Reads a FHETCH instruction trace and executes each operation using
// OpenFHE modular arithmetic, producing computed polynomial values
// in memory. This is the functional equivalent of the niobium-compiler's
// FHE_SIM replay target, but operating on the FHETCH text format
// instead of the internal .seq format.

#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

namespace niobium::fhetch_sim {

/// Result of running the simulator.
struct SimResult {
    size_t instructions_executed = 0;
    size_t errors = 0;
    double elapsed_seconds = 0.0;
};

/// FHETCH instruction-set simulator.
///
/// Usage:
///   Simulator sim;
///   sim.set_ring_dimension(4096);
///   sim.load_trace("program.fhetch");
///   sim.load_input("a", {1, 2, 3, ...}, modulus);
///   auto result = sim.run();
///   auto values = sim.get_polynomial(address);
///
class Simulator {
public:
    Simulator();
    ~Simulator();

    Simulator(const Simulator&) = delete;
    Simulator& operator=(const Simulator&) = delete;

    /// Set the ring dimension (N) for all polynomials.
    void set_ring_dimension(uint64_t N);

    /// Load a .fhetch trace file.
    /// @return true if parsing succeeded.
    bool load_trace(const std::filesystem::path& trace_file);

    /// Store a polynomial in simulator memory at the given address.
    /// Used to populate inputs before running.
    void store_polynomial(uint64_t address, const std::vector<uint64_t>& values,
                          uint64_t modulus);

    /// Run the loaded trace.
    SimResult run();

    /// Retrieve a polynomial from simulator memory after execution.
    /// @return empty vector if address is uninitialized.
    std::vector<uint64_t> get_polynomial(uint64_t address) const;

    /// Get the modulus associated with a memory address.
    uint64_t get_modulus(uint64_t address) const;

    /// Check if a memory address is initialized.
    bool is_initialized(uint64_t address) const;

    /// Get the set of addresses that are read before being written in the trace.
    /// These are the "live-in" addresses that need input data.
    std::vector<uint64_t> get_read_before_write_addresses() const;

    /// Scan the trace for instructions that initialize an address with a
    /// known constant value of zero (e.g. `sr_mulps %x, %x, 0, m=...`)
    /// BEFORE it appears as a read anywhere, and materialize the zero
    /// vector in simulator memory at that address. Called by replay()
    /// as part of the load-time population so the data-parent chain can
    /// propagate from zero-initialized sources.
    void prematerialize_zero_inits();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace niobium::fhetch_sim
