// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Internal header — provides access to Compiler internals for
// fhetch_api.cpp and probes.cpp within the library.
// NOT part of the public API.

#pragma once

#include "trace_writer.h"
#include <cstdint>

namespace niobium::detail {

/// Get the global TraceWriter instance (owned by the Compiler singleton).
TraceWriter& trace_writer();

/// Look up the FHETCH address for an OpenFHE polynomial ID.
/// Returns (uint64_t)-1 if not found.
uint64_t lookup_fhetch_address(uintptr_t openfhe_poly_id);

/// Get the data parent map: derived_addr → source_addr.
/// Used to propagate input data to addresses created by copy/move probes.
const std::unordered_map<uint64_t, uint64_t>& get_data_parent_map();

/// Bump the FHETCH address allocator so the next allocation returns
/// an address >= `next_addr`. No-op if the allocator is already past
/// that point. Used to carve out fixed ID ranges for inputs vs. keys.
void reserve_fhetch_addresses(uint64_t next_addr);

}  // namespace niobium::detail
