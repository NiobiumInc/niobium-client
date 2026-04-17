// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// FHETCH instruction-set simulator — executes .fhetch traces using
// OpenFHE modular arithmetic.

#include "niobium/fhetch_sim/simulator.h"
#include "instruction.h"
#include "memory.h"

#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <sstream>

// OpenFHE math
#include "math/math-hal.h"
#include "math/nbtheory.h"
#include "core/lattice/hal/lat-backend.h"
#include "lattice/lat-hal.h"

using namespace lbcrypto;

namespace niobium::fhetch_sim {

// ============================================================================
// Impl
// ============================================================================

struct Simulator::Impl {
    uint64_t ring_dim = 0;
    ParsedTrace trace;
    Memory memory;
    size_t error_count = 0;

    // Resolve a modulus index to the actual value
    uint64_t resolve_modulus(uint32_t idx) const {
        if (idx < trace.modulus_table.size())
            return trace.modulus_table[idx];
        return 0;
    }

    // Get polynomial from memory, returning zero-initialized if missing
    const std::vector<uint64_t>& get_or_zero(uint64_t addr,
                                              std::vector<uint64_t>& scratch,
                                              const Instruction& inst) {
        if (memory.is_initialized(addr))
            return memory.get(addr).values;
        std::cerr << "[FHETCH_SIM] WARNING: read from uninitialized address %"
                  << addr << " (line " << inst.line_number << ": "
                  << inst.raw_line << ")" << std::endl;
        scratch.assign(ring_dim, 0);
        return scratch;
    }

    void error(const Instruction& inst, const std::string& msg) {
        std::cerr << "[FHETCH_SIM] ERROR line " << inst.line_number
                  << ": " << msg << "\n  " << inst.raw_line << std::endl;
        error_count++;
    }

    // --- Arithmetic dispatch ---

    bool exec_addp(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch1, scratch2;
        const auto& a = get_or_zero(inst.src1, scratch1, inst);
        const auto& b = get_or_zero(inst.src2, scratch2, inst);
        if (a.size() != ring_dim || b.size() != ring_dim) {
            error(inst, "ring dimension mismatch"); return false;
        }

        NativeInteger mod(q);
        NativeVector va(ring_dim, mod), vb(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) {
            va[i] = NativeInteger(a[i]);
            vb[i] = NativeInteger(b[i]);
        }
        NativeVector vr = va.ModAdd(vb);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++)
            result[i] = vr[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_subp(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch1, scratch2;
        const auto& a = get_or_zero(inst.src1, scratch1, inst);
        const auto& b = get_or_zero(inst.src2, scratch2, inst);
        if (a.size() != ring_dim || b.size() != ring_dim) {
            error(inst, "ring dimension mismatch"); return false;
        }

        NativeInteger mod(q);
        NativeVector va(ring_dim, mod), vb(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) {
            va[i] = NativeInteger(a[i]);
            vb[i] = NativeInteger(b[i]);
        }
        NativeVector vr = va.ModSub(vb);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++)
            result[i] = vr[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_mulp(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch1, scratch2;
        const auto& a = get_or_zero(inst.src1, scratch1, inst);
        const auto& b = get_or_zero(inst.src2, scratch2, inst);
        if (a.size() != ring_dim || b.size() != ring_dim) {
            error(inst, "ring dimension mismatch"); return false;
        }

        NativeInteger mod(q);
        NativeVector va(ring_dim, mod), vb(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) {
            va[i] = NativeInteger(a[i]);
            vb[i] = NativeInteger(b[i]);
        }
        NativeVector vr = va.ModMul(vb);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++)
            result[i] = vr[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_addps(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        // Special case: immediate 0 = copy (no modular reduction).
        // Used by copy probes where the modulus may not match the data.
        if (inst.immediate == 0) {
            memory.set(inst.dest, std::vector<uint64_t>(a), q);
            return true;
        }

        NativeInteger mod(q), imm(inst.immediate);
        NativeVector va(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) va[i] = NativeInteger(a[i]);
        va.ModAddEq(imm);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++) result[i] = va[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_subps(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        NativeInteger mod(q), imm(inst.immediate);
        NativeVector va(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) va[i] = NativeInteger(a[i]);
        va.ModSubEq(imm);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++) result[i] = va[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_mulps(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);

        // Special case: multiply by 0 always produces zero
        if (inst.immediate == 0) {
            memory.set(inst.dest, std::vector<uint64_t>(ring_dim, 0), q);
            return true;
        }

        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        NativeInteger mod(q), imm(inst.immediate);
        NativeVector va(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) va[i] = NativeInteger(a[i]);
        va.ModMulEq(imm);
        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++) result[i] = va[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_negp(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++)
            result[i] = (a[i] == 0) ? 0 : q - a[i];
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_ntt(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        NativeInteger mod(q);
        NativeVector va(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) va[i] = NativeInteger(a[i]);

        NativeInteger root = RootOfUnity<NativeInteger>(2 * ring_dim, mod);
        ChineseRemainderTransformFTT<NativeVector> transformer;
        transformer.ForwardTransformToBitReverse(va, root, 2 * ring_dim, &va);

        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++) result[i] = va[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    bool exec_intt(const Instruction& inst) {
        uint64_t q = resolve_modulus(inst.modulus_index);
        std::vector<uint64_t> scratch;
        const auto& a = get_or_zero(inst.src1, scratch, inst);
        if (a.size() != ring_dim) { error(inst, "ring dimension mismatch"); return false; }

        NativeInteger mod(q);
        NativeVector va(ring_dim, mod);
        for (size_t i = 0; i < ring_dim; i++) va[i] = NativeInteger(a[i]);

        NativeInteger root = RootOfUnity<NativeInteger>(2 * ring_dim, mod);
        ChineseRemainderTransformFTT<NativeVector> transformer;
        transformer.InverseTransformFromBitReverse(va, root, 2 * ring_dim, &va);

        std::vector<uint64_t> result(ring_dim);
        for (size_t i = 0; i < ring_dim; i++) result[i] = va[i].ConvertToInt();
        memory.set(inst.dest, std::move(result), q);
        return true;
    }

    // --- Main execution loop ---

    SimResult execute() {
        auto start = std::chrono::steady_clock::now();
        size_t executed = 0;
        error_count = 0;
        size_t total = trace.instructions.size();

        std::cout << "[FHETCH_SIM] Executing " << total << " instructions, "
                  << trace.modulus_table.size() << " moduli, N=" << ring_dim
                  << std::endl;

        auto last_report = start;
        for (size_t i = 0; i < total; i++) {
            const auto& inst = trace.instructions[i];
            bool ok = true;

            switch (inst.opcode) {
            case OpCode::SR_ADDP:        ok = exec_addp(inst);  break;
            case OpCode::SR_SUBP:        ok = exec_subp(inst);  break;
            case OpCode::SR_MULP:        ok = exec_mulp(inst);  break;
            case OpCode::SR_ADDPS:
            case OpCode::SR_ADDPS_COEFF: ok = exec_addps(inst); break;
            case OpCode::SR_SUBPS:
            case OpCode::SR_SUBPS_COEFF: ok = exec_subps(inst); break;
            case OpCode::SR_MULPS:       ok = exec_mulps(inst); break;
            case OpCode::SR_NEGP:        ok = exec_negp(inst);  break;
            case OpCode::SR_NTT:         ok = exec_ntt(inst);   break;
            case OpCode::SR_INTT:        ok = exec_intt(inst);  break;

            case OpCode::SR_PERMUTE:
            case OpCode::SR_AUTOMORPH_EVAL:
            case OpCode::SR_AUTOMORPH_COEFF:
            case OpCode::SR_ROT_AUTOMORPH_COEFF:
                // TODO: permutation/automorphism simulation
                ok = true;
                break;

            // Non-integer ops: pass through (no modular reduction)
            case OpCode::SR_ADDP_NI:
            case OpCode::SR_SUBP_NI:
            case OpCode::SR_MULP_NI:
            case OpCode::SR_ADDPS_NI:
            case OpCode::SR_SUBPS_NI:
            case OpCode::SR_MULPS_NI:
            case OpCode::SR_ADDPS_COEFF_NI:
            case OpCode::SR_SUBPS_COEFF_NI:
            case OpCode::SR_NEGP_NI:
            case OpCode::SR_FT:
            case OpCode::SR_IFT:
                ok = true;  // TODO: non-integer arithmetic
                break;

            case OpCode::HALT:
                ok = true;
                break;

            case OpCode::COMMENT:
            case OpCode::UNKNOWN:
                ok = true;
                break;
            }

            if (ok) executed++;

            // Progress reporting every 2 seconds
            auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration_cast<std::chrono::seconds>(now - last_report).count() >= 2) {
                int pct = static_cast<int>(100 * (i + 1) / total);
                std::cout << "\r[FHETCH_SIM] Progress: " << pct << "% ("
                          << (i + 1) << "/" << total << ")" << std::flush;
                last_report = now;
            }
        }

        auto end = std::chrono::steady_clock::now();
        double elapsed = std::chrono::duration<double>(end - start).count();

        std::cout << "\r[FHETCH_SIM] Complete: " << executed << " executed, "
                  << error_count << " errors, "
                  << std::fixed << std::setprecision(2) << elapsed << "s"
                  << std::endl;

        return {executed, error_count, elapsed};
    }
};

// ============================================================================
// Public API
// ============================================================================

Simulator::Simulator() : impl_(std::make_unique<Impl>()) {}
Simulator::~Simulator() = default;

void Simulator::set_ring_dimension(uint64_t N) {
    impl_->ring_dim = N;
}

bool Simulator::load_trace(const std::filesystem::path& trace_file) {
    std::ifstream in(trace_file);
    if (!in.is_open()) {
        std::cerr << "[FHETCH_SIM] Cannot open: " << trace_file << std::endl;
        return false;
    }
    std::string content((std::istreambuf_iterator<char>(in)),
                        std::istreambuf_iterator<char>());
    impl_->trace = parse_trace(content);

    std::cout << "[FHETCH_SIM] Loaded: " << trace_file << "\n"
              << "  Modulus table: " << impl_->trace.modulus_table.size() << " entries\n"
              << "  Instructions:  " << impl_->trace.instructions.size() << std::endl;
    return !impl_->trace.instructions.empty();
}

void Simulator::store_polynomial(uint64_t address,
                                 const std::vector<uint64_t>& values,
                                 uint64_t modulus) {
    impl_->memory.set(address, values, modulus);
}

SimResult Simulator::run() {
    if (impl_->ring_dim == 0) {
        std::cerr << "[FHETCH_SIM] ring dimension not set" << std::endl;
        return {0, 1, 0.0};
    }
    return impl_->execute();
}

std::vector<uint64_t> Simulator::get_polynomial(uint64_t address) const {
    if (impl_->memory.is_initialized(address))
        return impl_->memory.get(address).values;
    return {};
}

uint64_t Simulator::get_modulus(uint64_t address) const {
    return impl_->memory.get(address).modulus;
}

bool Simulator::is_initialized(uint64_t address) const {
    return impl_->memory.is_initialized(address);
}

std::vector<uint64_t> Simulator::get_read_before_write_addresses() const {
    std::set<uint64_t> written;
    std::vector<uint64_t> rbw;

    for (const auto& inst : impl_->trace.instructions) {
        if (inst.opcode == OpCode::HALT || inst.opcode == OpCode::COMMENT ||
            inst.opcode == OpCode::UNKNOWN)
            continue;

        // Source addresses are read
        // For poly-poly ops: src1 and src2 are sources
        // For poly-scalar/unary ops: src1 is the source
        // The dest is also a source for in-place ops (dest == src1)
        uint64_t sources[2] = {inst.src1, inst.src2};
        int nsrc = 2;

        // Unary ops only have src1
        switch (inst.opcode) {
        case OpCode::SR_NEGP:
        case OpCode::SR_NTT: case OpCode::SR_INTT:
        case OpCode::SR_NEGP_NI:
        case OpCode::SR_FT: case OpCode::SR_IFT:
        case OpCode::SR_PERMUTE:
        case OpCode::SR_AUTOMORPH_EVAL:
        case OpCode::SR_AUTOMORPH_COEFF:
        case OpCode::SR_ROT_AUTOMORPH_COEFF:
            nsrc = 1;
            break;
        case OpCode::SR_ADDPS: case OpCode::SR_SUBPS: case OpCode::SR_MULPS:
        case OpCode::SR_ADDPS_COEFF: case OpCode::SR_SUBPS_COEFF:
        case OpCode::SR_ADDPS_NI: case OpCode::SR_SUBPS_NI: case OpCode::SR_MULPS_NI:
        case OpCode::SR_ADDPS_COEFF_NI: case OpCode::SR_SUBPS_COEFF_NI:
            nsrc = 1;  // scalar ops: only src1 is a poly address
            break;
        default:
            break;
        }

        for (int i = 0; i < nsrc; i++) {
            if (written.find(sources[i]) == written.end()) {
                // First time seeing this address as a source, and it hasn't
                // been written yet — it's a read-before-write.
                rbw.push_back(sources[i]);
                written.insert(sources[i]);  // prevent duplicates in result
            }
        }

        // Dest is written
        written.insert(inst.dest);
    }

    return rbw;
}

}  // namespace niobium::fhetch_sim
