// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Internal header — provides access to Compiler internals for
// fhetch_api.cpp and probes.cpp within the library.
// NOT part of the public API.

#pragma once

#include "trace_writer.h"

namespace niobium::detail {

/// Get the global TraceWriter instance (owned by the Compiler singleton).
TraceWriter& trace_writer();

}  // namespace niobium::detail
