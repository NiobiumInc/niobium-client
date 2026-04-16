// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// FHETCH instruction representation and parser.

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace niobium::fhetch_sim {

enum class OpCode {
    SR_ADDP, SR_SUBP, SR_MULP,
    SR_ADDPS, SR_SUBPS, SR_MULPS,
    SR_ADDPS_COEFF, SR_SUBPS_COEFF,
    SR_NEGP,
    SR_NTT, SR_INTT,
    SR_PERMUTE,
    SR_AUTOMORPH_EVAL, SR_AUTOMORPH_COEFF, SR_ROT_AUTOMORPH_COEFF,
    // Non-integer variants
    SR_ADDP_NI, SR_SUBP_NI, SR_MULP_NI,
    SR_ADDPS_NI, SR_SUBPS_NI, SR_MULPS_NI,
    SR_ADDPS_COEFF_NI, SR_SUBPS_COEFF_NI,
    SR_NEGP_NI,
    SR_FT, SR_IFT,
    HALT,
    COMMENT,
    UNKNOWN
};

/// Parsed FHETCH instruction.
struct Instruction {
    OpCode opcode = OpCode::UNKNOWN;
    uint64_t dest = 0;              // Destination address (%N)
    uint64_t src1 = 0;              // First source address
    uint64_t src2 = 0;              // Second source (poly-poly ops)
    uint64_t immediate = 0;         // Scalar immediate (poly-scalar ops)
    uint32_t modulus_index = 0;     // Index into the modulus table (m=N)
    std::optional<uint64_t> k;      // Automorphism index
    std::optional<uint64_t> offset; // Rotation offset
    std::string comment;            // Comment text (for COMMENT opcode)
    std::string raw_line;           // Original text
    int line_number = 0;
};

/// Result of parsing a .fhetch trace.
struct ParsedTrace {
    std::vector<uint64_t> modulus_table;    // m[0], m[1], ...
    std::vector<Instruction> instructions;
};

/// Parse a .fhetch trace file.
/// Reads the modulus table and instruction section.
/// @return parsed trace, or empty on failure.
ParsedTrace parse_trace(const std::string& trace_text);

}  // namespace niobium::fhetch_sim
