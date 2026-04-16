// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "instruction.h"

#include <algorithm>
#include <cctype>
#include <iostream>
#include <sstream>
#include <unordered_map>

namespace niobium::fhetch_sim {

// ============================================================================
// Opcode string → enum mapping
// ============================================================================

static const std::unordered_map<std::string, OpCode> opcode_map = {
    {"sr_addp",               OpCode::SR_ADDP},
    {"sr_subp",               OpCode::SR_SUBP},
    {"sr_mulp",               OpCode::SR_MULP},
    {"sr_addps",              OpCode::SR_ADDPS},
    {"sr_subps",              OpCode::SR_SUBPS},
    {"sr_mulps",              OpCode::SR_MULPS},
    {"sr_addps_coeff",        OpCode::SR_ADDPS_COEFF},
    {"sr_subps_coeff",        OpCode::SR_SUBPS_COEFF},
    {"sr_negp",               OpCode::SR_NEGP},
    {"sr_ntt",                OpCode::SR_NTT},
    {"sr_intt",               OpCode::SR_INTT},
    {"sr_permute",            OpCode::SR_PERMUTE},
    {"sr_automorph_eval",     OpCode::SR_AUTOMORPH_EVAL},
    {"sr_automorph_coeff",    OpCode::SR_AUTOMORPH_COEFF},
    {"sr_rot_automorph_coeff",OpCode::SR_ROT_AUTOMORPH_COEFF},
    {"sr_addp_ni",            OpCode::SR_ADDP_NI},
    {"sr_subp_ni",            OpCode::SR_SUBP_NI},
    {"sr_mulp_ni",            OpCode::SR_MULP_NI},
    {"sr_addps_ni",           OpCode::SR_ADDPS_NI},
    {"sr_subps_ni",           OpCode::SR_SUBPS_NI},
    {"sr_mulps_ni",           OpCode::SR_MULPS_NI},
    {"sr_addps_coeff_ni",     OpCode::SR_ADDPS_COEFF_NI},
    {"sr_subps_coeff_ni",     OpCode::SR_SUBPS_COEFF_NI},
    {"sr_negp_ni",            OpCode::SR_NEGP_NI},
    {"sr_ft",                 OpCode::SR_FT},
    {"sr_ift",                OpCode::SR_IFT},
    {"halt",                  OpCode::HALT},
};

// ============================================================================
// Helpers
// ============================================================================

// Strip leading/trailing whitespace
static std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    size_t end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

// Parse %N → N
static bool parse_addr(const std::string& tok, uint64_t& out) {
    std::string s = trim(tok);
    // Remove trailing comma
    if (!s.empty() && s.back() == ',') s.pop_back();
    if (s.empty() || s[0] != '%') return false;
    out = std::stoull(s.substr(1));
    return true;
}

// Parse m=N → N
static bool parse_modulus_ref(const std::string& tok, uint32_t& out) {
    std::string s = trim(tok);
    if (s.substr(0, 2) != "m=") return false;
    out = static_cast<uint32_t>(std::stoul(s.substr(2)));
    return true;
}

// Parse a plain integer (immediate value)
static bool parse_uint(const std::string& tok, uint64_t& out) {
    std::string s = trim(tok);
    if (!s.empty() && s.back() == ',') s.pop_back();
    if (s.empty()) return false;
    out = std::stoull(s);
    return true;
}

// Parse k=N
static bool parse_named_uint(const std::string& tok, const std::string& prefix, uint64_t& out) {
    std::string s = trim(tok);
    if (!s.empty() && s.back() == ',') s.pop_back();
    if (s.substr(0, prefix.size()) != prefix) return false;
    out = std::stoull(s.substr(prefix.size()));
    return true;
}

// ============================================================================
// Parser
// ============================================================================

ParsedTrace parse_trace(const std::string& trace_text) {
    ParsedTrace result;
    std::istringstream stream(trace_text);
    std::string line;
    int line_num = 0;

    while (std::getline(stream, line)) {
        line_num++;
        std::string trimmed = trim(line);
        if (trimmed.empty()) continue;

        // ---------- Comment lines ----------
        if (trimmed[0] == '#') continue;

        // ---------- Modulus table: modulus_count N ----------
        if (trimmed.substr(0, 13) == "modulus_count") {
            size_t count = std::stoull(trim(trimmed.substr(13)));
            result.modulus_table.reserve(count);
            continue;
        }

        // ---------- Modulus table: m[N] 0xHEX ----------
        if (trimmed.size() > 2 && trimmed[0] == 'm' && trimmed[1] == '[') {
            // Find the hex value after the space
            size_t space = trimmed.find(' ');
            if (space == std::string::npos) {
                std::cerr << "[FHETCH_SIM] Line " << line_num
                          << ": malformed modulus entry: " << trimmed << std::endl;
                continue;
            }
            std::string hex_str = trim(trimmed.substr(space + 1));
            uint64_t modulus = std::stoull(hex_str, nullptr, 16);
            result.modulus_table.push_back(modulus);
            continue;
        }

        // ---------- Strip inline comment ----------
        std::string inst_text = trimmed;
        size_t comment_pos = trimmed.find('#');
        if (comment_pos != std::string::npos) {
            inst_text = trim(trimmed.substr(0, comment_pos));
            if (inst_text.empty()) continue;
        }

        // ---------- Tokenize ----------
        std::istringstream tokens(inst_text);
        std::string opcode_str;
        tokens >> opcode_str;

        // Lowercase the opcode for matching
        std::transform(opcode_str.begin(), opcode_str.end(), opcode_str.begin(),
                       [](unsigned char c) { return std::tolower(c); });

        auto it = opcode_map.find(opcode_str);
        if (it == opcode_map.end()) {
            // Skip unknown lines silently (annotations, etc.)
            continue;
        }

        Instruction inst;
        inst.opcode = it->second;
        inst.raw_line = trimmed;
        inst.line_number = line_num;

        // Collect remaining tokens
        std::vector<std::string> args;
        std::string tok;
        while (tokens >> tok) args.push_back(tok);

        // ---------- Dispatch by opcode category ----------
        switch (inst.opcode) {
        case OpCode::HALT:
            break;

        // poly-poly: dest, src1, src2, m=N
        case OpCode::SR_ADDP:
        case OpCode::SR_SUBP:
        case OpCode::SR_MULP:
            if (args.size() >= 4) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                parse_addr(args[2], inst.src2);
                parse_modulus_ref(args[3], inst.modulus_index);
            }
            break;

        // poly-poly non-integer: dest, src1, src2
        case OpCode::SR_ADDP_NI:
        case OpCode::SR_SUBP_NI:
        case OpCode::SR_MULP_NI:
            if (args.size() >= 3) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                parse_addr(args[2], inst.src2);
            }
            break;

        // poly-scalar: dest, src1, immediate, m=N
        case OpCode::SR_ADDPS:
        case OpCode::SR_SUBPS:
        case OpCode::SR_MULPS:
        case OpCode::SR_ADDPS_COEFF:
        case OpCode::SR_SUBPS_COEFF:
            if (args.size() >= 4) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                parse_uint(args[2], inst.immediate);
                parse_modulus_ref(args[3], inst.modulus_index);
            }
            break;

        // poly-scalar non-integer: dest, src1, immediate
        case OpCode::SR_ADDPS_NI:
        case OpCode::SR_SUBPS_NI:
        case OpCode::SR_MULPS_NI:
        case OpCode::SR_ADDPS_COEFF_NI:
        case OpCode::SR_SUBPS_COEFF_NI:
            if (args.size() >= 3) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                parse_uint(args[2], inst.immediate);
            }
            break;

        // unary with modulus: dest, src1, m=N
        case OpCode::SR_NEGP:
        case OpCode::SR_NTT:
        case OpCode::SR_INTT:
        case OpCode::SR_PERMUTE:
            if (args.size() >= 3) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                parse_modulus_ref(args[2], inst.modulus_index);
            }
            break;

        // unary without modulus
        case OpCode::SR_NEGP_NI:
        case OpCode::SR_FT:
        case OpCode::SR_IFT:
            if (args.size() >= 2) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
            }
            break;

        // automorphism: dest, src1, k=N, m=N
        case OpCode::SR_AUTOMORPH_EVAL:
            if (args.size() >= 3) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                uint64_t kval;
                if (parse_named_uint(args[2], "k=", kval)) inst.k = kval;
                if (args.size() >= 4)
                    parse_modulus_ref(args[3], inst.modulus_index);
            }
            break;

        case OpCode::SR_AUTOMORPH_COEFF:
            if (args.size() >= 4) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                uint64_t kval;
                if (parse_named_uint(args[2], "k=", kval)) inst.k = kval;
                parse_modulus_ref(args[3], inst.modulus_index);
            }
            break;

        case OpCode::SR_ROT_AUTOMORPH_COEFF:
            if (args.size() >= 4) {
                parse_addr(args[0], inst.dest);
                parse_addr(args[1], inst.src1);
                uint64_t off;
                if (parse_named_uint(args[2], "offset=", off)) inst.offset = off;
                parse_modulus_ref(args[3], inst.modulus_index);
            }
            break;

        default:
            break;
        }

        result.instructions.push_back(inst);
    }

    return result;
}

}  // namespace niobium::fhetch_sim
