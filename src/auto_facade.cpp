// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Stub implementations for niobium_auto::* hook functions and globals
// required by the Niobium-instrumented OpenFHE branch.
//
// In the full niobium-compiler, AutoFacade.cpp provides config-driven
// record/replay orchestration. Here in the client we provide minimal
// stubs so that the instrumented OpenFHE links and the probes fire.

#include "niobium/compiler.h"

#include "openfhe.h"
#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "scheme/ckksrns/ckksrns-ser.h"
#include "scheme/ckksrns/ckksrns-fhe.h"

#include "cereal_io.h"
#include <cereal/archives/portable_binary.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

using DCRTPoly = lbcrypto::DCRTPoly;
// Format is a top-level enum in OpenFHE (utils/inttypes.h), not namespaced.

// ============================================================================
// Stored references to OpenFHE objects for re-extraction at replay() time.
// The fhetch_driver approach: capture ALL polynomial data right before
// simulation, including derived addresses from OpenFHE's pre-processing.
// ============================================================================

static std::vector<lbcrypto::Ciphertext<DCRTPoly>> g_stored_ciphertexts;
static lbcrypto::CryptoContext<DCRTPoly> g_stored_cc;

// ============================================================================
// Global flags read by instrumented OpenFHE headers
// (declared extern in niobium_auto_hooks.h)
// ============================================================================

bool g_replay_mode = false;
std::atomic<uint64_t> g_replay_noop_count{0};

// ============================================================================
// niobium_auto::* hooks — signatures must match niobium_auto_hooks.h exactly
// ============================================================================

namespace niobium_auto {

void on_deserialize_crypto_context(
    lbcrypto::CryptoContext<DCRTPoly>& /*cc*/) {
    // No-op in client — crypto context capture is explicit via compiler API.
}

void on_deserialize_ciphertext(
    const std::string& /*filepath*/,
    lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // No-op in client.
}

void lazy_init(const lbcrypto::CryptoContext<DCRTPoly>& /*cc*/) {
    // No-op in client — init is explicit via compiler().init().
}

void lazy_init() {
    // No-arg overload — no-op in client.
}

bool on_serialize_ciphertext(
    const std::string& /*filepath*/,
    const lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // Return false: caller proceeds with normal file write.
    return false;
}

bool is_recording() {
    return niobium::compiler().running_p();
}

bool on_decrypt(lbcrypto::Ciphertext<DCRTPoly>& /*ct*/) {
    // Return false: caller proceeds with normal Decrypt.
    return false;
}

bool is_replaying() {
    return g_replay_mode;
}

std::shared_ptr<lbcrypto::SchemeBase<DCRTPoly>> unwrap_scheme(
    const std::shared_ptr<lbcrypto::SchemeBase<DCRTPoly>>& scheme) {
    // Client doesn't use the NiobiumAutoScheme proxy — return as-is.
    return scheme;
}

}  // namespace niobium_auto

// ============================================================================
// Additional probe functions referenced by the instrumented OpenFHE
// ============================================================================

extern "C" {

void openfhe_cporbe_with_openmp(bool /*with_openmp*/) {
    // Signals OpenFHE's OpenMP state. No-op in client.
}

void openfhe_cprobe_save_dcrt_poly(const void* /*dcrt_poly_ptr*/) {
    // DATA_TRACKING feature only — no-op in client.
}

}  // extern "C"

// ============================================================================
// Explicit template instantiations for Compiler methods with OpenFHE types
// ============================================================================

#include "compiler_internal.h"

namespace niobium {

// Forward-declare the tag_bootstrap_precompute<CryptoContext> specialization
// so capture_crypto_context() below can reference it inside the auto-capture
// lambda without implicitly instantiating it ahead of the explicit definition.
template<>
void Compiler::tag_bootstrap_precompute<lbcrypto::CryptoContext<DCRTPoly>>(
    const lbcrypto::CryptoContext<DCRTPoly>& cc);

template<>
void Compiler::capture_crypto_context<lbcrypto::CryptoContext<DCRTPoly>>(
    const lbcrypto::CryptoContext<DCRTPoly>& cc) {
    uint64_t rd = cc->GetRingDimension();
    set_ring_dimension(rd);

    // Extract crypto parameters
    auto cryptoParams = cc->GetCryptoParameters();
    auto elemParams = cryptoParams->GetElementParams();

    // Scheme name
    std::string scheme;
    auto sid = cc->getSchemeId();
    if (sid == lbcrypto::SCHEME::CKKSRNS_SCHEME) scheme = "CKKS";
    else if (sid == lbcrypto::SCHEME::BFVRNS_SCHEME) scheme = "BFV";
    else if (sid == lbcrypto::SCHEME::BGVRNS_SCHEME) scheme = "BGV";
    else scheme = "UNKNOWN";

    // Modulus chain
    std::vector<uint64_t> modulus_chain;
    for (const auto& p : elemParams->GetParams())
        modulus_chain.push_back(p->GetModulus().ConvertToInt());

    uint32_t depth = static_cast<uint32_t>(modulus_chain.size() > 0 ? modulus_chain.size() - 1 : 0);

    set_crypto_context_info(scheme, depth, 0, "HEStd_NotSet", modulus_chain);

    // Store for re-extraction at replay() time
    g_stored_cc = cc;

    // Register the auto-capture hook so stop() walks any CC-derived
    // precomputed data (e.g. CKKS bootstrap precompute). Must run at
    // stop() — not here — because EvalBootstrapSetup fires its
    // precompute probes AFTER capture_crypto_context returns.
    set_auto_capture_at_stop([this, cc]() {
        this->tag_bootstrap_precompute(cc);
    });

    std::cout << "[NIOBIUM] Captured crypto context: scheme=" << scheme
              << " ring_dim=" << rd
              << " depth=" << depth
              << " moduli=" << modulus_chain.size() << std::endl;
}

// Helper: collect addr_ids and coefficient data from a ciphertext
static void capture_ciphertext_polys(niobium::Compiler& compiler,
                                     const std::string& name,
                                     const lbcrypto::Ciphertext<DCRTPoly>& ct,
                                     std::vector<uint64_t>& addr_ids) {
    const auto& elements = ct->GetElements();
    for (const auto& dcrt : elements) {
        for (const auto& poly : dcrt.GetAllElements()) {
            uintptr_t poly_id = poly.GetId();
            // Pin + allocate a FHETCH address lazily (matches the
            // compiler's compact_address at tag time).
            niobium::detail::pin_openfhe_id(poly_id);
            uint64_t fhetch_addr = niobium::detail::ensure_fhetch_address(poly_id);
            if (fhetch_addr == static_cast<uint64_t>(-1)) continue;

            addr_ids.push_back(fhetch_addr);

            uint64_t modulus = poly.GetModulus().ConvertToInt();
            size_t n = poly.GetLength();
            std::vector<uint64_t> vals(n);
            const auto& vec = poly.GetValues();
            for (size_t i = 0; i < n; ++i)
                vals[i] = vec[i].ConvertToInt();

            compiler.store_input_element(name, fhetch_addr, modulus, vals);
        }
    }
}

template<>
void Compiler::tag_input<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& input_name,
    lbcrypto::Ciphertext<DCRTPoly>& ct,
    std::optional<std::filesystem::path> /*file*/) {
    auto dir = get_program_directory();
    std::filesystem::create_directories(dir);
    std::string prog = program_name();

    // Collect addr_ids and in-memory data
    std::vector<uint64_t> addr_ids;
    capture_ciphertext_polys(*this, input_name, ct, addr_ids);

    // Write .ids file
    auto ids_path = dir / (prog + ".input_" + input_name + ".ids");
    cereal_io::write_addr_ids(ids_path, addr_ids);

    // Write .bin file (cereal binary — same format as niobium-compiler)
    auto bin_path = dir / (prog + ".input_" + input_name + ".bin");
    {
        std::ofstream bin_stream(bin_path, std::ios::binary);
        if (bin_stream.is_open()) {
            cereal::PortableBinaryOutputArchive ar(bin_stream);
            ar(static_cast<uint32_t>(1));  // instances_count
            ar(static_cast<uint8_t>(1));   // payload_type: ciphertext
            auto& elements = ct->GetElements();
            ar(static_cast<uint32_t>(elements.size()));
            for (auto& dcrt : elements) {
                ar(dcrt);
            }
        }
    }

    // Store reference for re-extraction at replay() time
    g_stored_ciphertexts.push_back(ct);
}

template<>
void Compiler::tag_input<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& input_name,
    const lbcrypto::Ciphertext<DCRTPoly>& ct,
    std::optional<std::filesystem::path> file) {
    auto& mutable_ct = const_cast<lbcrypto::Ciphertext<DCRTPoly>&>(ct);
    tag_input(input_name, mutable_ct, file);
}

template<>
void Compiler::probe<lbcrypto::Ciphertext<DCRTPoly>>(
    const std::string& var_name,
    const lbcrypto::Ciphertext<DCRTPoly>& ct) {
    // Record the FHETCH addresses of the output polynomials.
    const auto& elements = ct->GetElements();
    for (const auto& dcrt : elements) {
        for (const auto& poly : dcrt.GetAllElements()) {
            uintptr_t poly_id = poly.GetId();
            uint64_t fhetch_addr = detail::lookup_fhetch_address(poly_id);
            if (fhetch_addr == static_cast<uint64_t>(-1)) continue;
            uint64_t modulus = poly.GetModulus().ConvertToInt();
            store_output_probe(var_name, fhetch_addr, modulus);
        }
    }

    // Save ciphertext template for result reconstruction after replay.
    // The template preserves the ciphertext structure (params, format, etc.)
    // so we can fill in simulator-computed polynomial values later.
    auto dir = get_program_directory();
    auto templates_dir = dir / "ciphertext_templates";
    std::filesystem::create_directories(templates_dir);
    auto template_path = templates_dir / (var_name + ".template");
    if (!lbcrypto::Serial::SerializeToFile(template_path.string(), ct, lbcrypto::SerType::BINARY)) {
        std::cerr << "[NIOBIUM] WARNING: Failed to save ciphertext template for " << var_name << std::endl;
    }
}

// Debug verbosity: set NIOBIUM_DEBUG_CAPTURE=1 to print every captured poly.
static bool capture_debug_enabled() {
    static int cached = -1;
    if (cached == -1) {
        const char* e = std::getenv("NIOBIUM_DEBUG_CAPTURE");
        cached = (e && *e && std::string(e) != "0") ? 1 : 0;
    }
    return cached == 1;
}

// Helper: capture DCRTPoly polynomials — collects addr_ids and stores values.
// Forces EVALUATION (NTT) form before reading values so the simulator sees
// the same representation the compiler's reference captures.
static size_t capture_dcrt_polys(Compiler& compiler,
                                 const std::string& key_name,
                                 const std::vector<DCRTPoly>& polys,
                                 std::vector<uint64_t>& addr_ids) {
    size_t count = 0;
    size_t coeff_count = 0;   // how many polys were in COEFFICIENT form on entry
    size_t zero_count = 0;    // captured polys whose values are all-zero
    bool dbg = capture_debug_enabled();

    for (size_t di = 0; di < polys.size(); ++di) {
        // Cast away const so we can normalize the format — captures should
        // always be in EVALUATION form (matches OpenFHE's post-encrypt state
        // and the compiler's capture convention).
        auto& dcrt = const_cast<DCRTPoly&>(polys[di]);
        if (dcrt.GetFormat() != Format::EVALUATION) {
            dcrt.SetFormat(Format::EVALUATION);
        }

        const auto& towers = dcrt.GetAllElements();
        for (size_t ti = 0; ti < towers.size(); ++ti) {
            const auto& poly = towers[ti];
            uintptr_t poly_id = poly.GetId();
            // Pin the OpenFHE id so its compact FHETCH address stays live
            // through any reassignments (inputs/keys/precompute must survive
            // the address-recycling layer).
            detail::pin_openfhe_id(poly_id);
            // Allocate a FHETCH address if the poly didn't already get one.
            // Setup-time probes no longer allocate addresses — tag_* is the
            // intended mapping point, giving inputs/keys/precompute low
            // compact ids before the trace starts consuming the space.
            uint64_t fhetch_addr = detail::ensure_fhetch_address(poly_id);
            if (fhetch_addr == static_cast<uint64_t>(-1)) continue;

            if (poly.GetFormat() != Format::EVALUATION)
                ++coeff_count;  // unexpected — SetFormat above should have normalized

            addr_ids.push_back(fhetch_addr);

            uint64_t modulus = poly.GetModulus().ConvertToInt();
            size_t n = poly.GetLength();
            std::vector<uint64_t> vals(n);
            const auto& vec = poly.GetValues();
            bool any_nonzero = false;
            for (size_t i = 0; i < n; ++i) {
                vals[i] = vec[i].ConvertToInt();
                if (vals[i] != 0) any_nonzero = true;
            }
            if (!any_nonzero) ++zero_count;

            if (dbg) {
                std::cout << "[NIOBIUM-DBG] " << key_name
                          << " dcrt[" << di << "].tower[" << ti << "]"
                          << " poly_id=0x" << std::hex << poly_id << std::dec
                          << " addr=%" << fhetch_addr
                          << " fmt=" << (poly.GetFormat()==Format::EVALUATION?"EVAL":"COEFF")
                          << " mod=0x" << std::hex << modulus << std::dec
                          << " N=" << n
                          << " vals[0..3]=" << vals[0]
                          << "," << (n>1?vals[1]:0)
                          << "," << (n>2?vals[2]:0)
                          << "," << (n>3?vals[3]:0)
                          << (any_nonzero ? "" : "  [ALL-ZERO]")
                          << std::endl;
            }

            compiler.store_input_element(key_name, fhetch_addr, modulus, vals);
            count++;
        }
    }

    if (count > 0) {
        std::cout << "[NIOBIUM] captured " << key_name << ": " << count
                  << " polys";
        if (coeff_count) std::cout << "  (" << coeff_count << " still in COEFF form!)";
        if (zero_count)  std::cout << "  (" << zero_count << " all-zero)";
        std::cout << std::endl;
    }
    return count;
}

// Helper: serialize eval keys to .bin via cereal + write .ids
static void serialize_eval_keys(
    const std::vector<lbcrypto::EvalKey<DCRTPoly>>& keys,
    const std::filesystem::path& bin_path,
    const std::filesystem::path& ids_path,
    const std::vector<uint64_t>& addr_ids) {
    // .ids
    cereal_io::write_addr_ids(ids_path, addr_ids);
    // .bin — serialize each key's A/B vectors
    std::ofstream bin(bin_path, std::ios::binary);
    if (!bin.is_open()) return;
    cereal::PortableBinaryOutputArchive ar(bin);
    ar(static_cast<uint32_t>(keys.size()));
    for (const auto& key : keys) {
        const auto& av = key->GetAVector();
        const auto& bv = key->GetBVector();
        ar(static_cast<uint32_t>(av.size()));
        for (const auto& dcrt : av) ar(dcrt);
        ar(static_cast<uint32_t>(bv.size()));
        for (const auto& dcrt : bv) ar(dcrt);
    }
}

template<>
void Compiler::tag_keys<lbcrypto::CryptoContext<DCRTPoly>>(
    const lbcrypto::CryptoContext<DCRTPoly>& /*cc*/) {
    auto dir = get_program_directory();
    std::filesystem::create_directories(dir);
    std::string prog = program_name();
    size_t total = 0;

    // EvalMult keys
    try {
        const auto& allMultKeys = lbcrypto::CryptoContextImpl<DCRTPoly>::GetAllEvalMultKeys();
        std::vector<uint64_t> mk_addr_ids;
        std::vector<lbcrypto::EvalKey<DCRTPoly>> all_mk;
        for (const auto& [keyTag, keyVec] : allMultKeys) {
            for (const auto& evalKey : keyVec) {
                total += capture_dcrt_polys(*this, "evalmult_key", evalKey->GetAVector(), mk_addr_ids);
                total += capture_dcrt_polys(*this, "evalmult_key", evalKey->GetBVector(), mk_addr_ids);
                all_mk.push_back(evalKey);
            }
        }
        if (!all_mk.empty()) {
            serialize_eval_keys(all_mk,
                dir / (prog + ".mk.bin"), dir / (prog + ".mk.ids"), mk_addr_ids);
            if (!mk_addr_ids.empty())
                set_key_start_addr_id("evalmult", mk_addr_ids[0]);
        }
    } catch (...) {}

    // EvalAutomorphism keys
    try {
        const auto& allAutoKeys = lbcrypto::CryptoContextImpl<DCRTPoly>::GetAllEvalAutomorphismKeys();
        std::vector<uint64_t> rk_addr_ids;
        std::vector<lbcrypto::EvalKey<DCRTPoly>> all_rk;
        for (const auto& [keyTag, keyMap] : allAutoKeys) {
            if (!keyMap) continue;
            for (const auto& [idx, evalKey] : *keyMap) {
                total += capture_dcrt_polys(*this, "automorphism_key", evalKey->GetAVector(), rk_addr_ids);
                total += capture_dcrt_polys(*this, "automorphism_key", evalKey->GetBVector(), rk_addr_ids);
                all_rk.push_back(evalKey);
            }
        }
        if (!all_rk.empty()) {
            serialize_eval_keys(all_rk,
                dir / (prog + ".rk.bin"), dir / (prog + ".rk.ids"), rk_addr_ids);
            if (!rk_addr_ids.empty())
                set_key_start_addr_id("evalautomorphism", rk_addr_ids[0]);
        }
    } catch (...) {}

    std::cout << "[NIOBIUM] Tagged " << total << " key polynomials for replay" << std::endl;
}

// Capture the CKKS bootstrap precomputation plaintexts so the simulator
// has their polynomial values as live-in. Walks the FHECKKSRNS private
// m_bootPrecomMap via its probe-exposed accessor (GetBootPrecomMap), and
// captures every DCRTPoly inside m_U0Pre / m_U0hatTPre / m_U0PreFFT /
// m_U0hatTPreFFT as an input under the "bootstrap_precompute" name.
//
// Also serializes the captured data to disk as `.bp.bin` + `.bp.ids`,
// matching niobium-compiler's CerealIO::write_bootstrap_precomp format.
template<>
void Compiler::tag_bootstrap_precompute<lbcrypto::CryptoContext<DCRTPoly>>(
    const lbcrypto::CryptoContext<DCRTPoly>& cc) {
    if (!cc) return;
    auto scheme = cc->GetScheme();
    if (!scheme) return;
    auto fheBase = scheme->GetFHE();
    if (!fheBase) return;
    auto fheCkks = std::dynamic_pointer_cast<lbcrypto::FHECKKSRNS>(fheBase);
    if (!fheCkks) return;

    const auto& bootMap = fheCkks->GetBootPrecomMap();
    if (bootMap.empty()) return;

    // Same traversal order as the compiler's write_bootstrap_precomp:
    // per-slot entry, then m_U0hatTPreFFT, m_U0PreFFT, m_U0Pre, m_U0hatTPre.
    std::vector<uint64_t> addr_ids;
    auto capture_pt = [&](const auto& pt) {
        if (!pt) return;
        const auto& el = pt->template GetElement<lbcrypto::DCRTPoly>();
        std::vector<DCRTPoly> one;
        one.push_back(el);
        capture_dcrt_polys(*this, "bootstrap_precompute", one, addr_ids);
    };

    auto range = [&](size_t from) {
        if (addr_ids.size() <= from) return std::string("empty");
        uint64_t mn = addr_ids[from], mx = addr_ids[from];
        for (size_t i = from + 1; i < addr_ids.size(); ++i) {
            mn = std::min(mn, addr_ids[i]);
            mx = std::max(mx, addr_ids[i]);
        }
        return std::to_string(mn) + ".." + std::to_string(mx)
               + " (" + std::to_string(addr_ids.size() - from) + ")";
    };
    for (const auto& [slots, precom] : bootMap) {
        if (!precom) continue;
        size_t s0 = addr_ids.size();
        for (const auto& inner : precom->m_U0hatTPreFFT)
            for (const auto& pt : inner) capture_pt(pt);
        std::cout << "[NIOBIUM-DBG]   slots=" << slots
                  << " m_U0hatTPreFFT addrs=" << range(s0) << std::endl;
        size_t s1 = addr_ids.size();
        for (const auto& inner : precom->m_U0PreFFT)
            for (const auto& pt : inner) capture_pt(pt);
        std::cout << "[NIOBIUM-DBG]   m_U0PreFFT    addrs=" << range(s1) << std::endl;
        size_t s2 = addr_ids.size();
        for (const auto& pt : precom->m_U0Pre) capture_pt(pt);
        std::cout << "[NIOBIUM-DBG]   m_U0Pre       addrs=" << range(s2) << std::endl;
        size_t s3 = addr_ids.size();
        for (const auto& pt : precom->m_U0hatTPre) capture_pt(pt);
        std::cout << "[NIOBIUM-DBG]   m_U0hatTPre   addrs=" << range(s3) << std::endl;
    }

    std::cout << "[NIOBIUM] Tagged " << addr_ids.size()
              << " bootstrap precompute polynomials"
              << " (precompute probe fired " << niobium::detail::niobium_precompute_probe_count()
              << " times, " << niobium::detail::niobium_precompute_probe_already_mapped_count()
              << " already mapped)"
              << std::endl;

    // Write .bp.bin and .bp.ids to disk (compiler-parity format).
    auto dir = get_program_directory();
    std::filesystem::create_directories(dir);
    std::string prog = program_name();

    auto ids_path = dir / (prog + ".bp.ids");
    cereal_io::write_addr_ids(ids_path, addr_ids);

    auto bin_path = dir / (prog + ".bp.bin");
    std::ofstream bin(bin_path, std::ios::binary);
    if (!bin.is_open()) return;
    cereal::PortableBinaryOutputArchive ar(bin);

    ar(static_cast<uint32_t>(bootMap.size()));
    for (const auto& [slots, precom] : bootMap) {
        if (!precom) continue;
        ar(precom->m_slots);
        ar(static_cast<uint32_t>(precom->m_paramsEnc.g));  // m_dim1

        auto write_params = [&](const auto& p) {
            ar(p.lvlb, p.layersCollapse, p.remCollapse, p.numRotations,
               p.b, p.g, p.numRotationsRem, p.bRem, p.gRem);
        };
        write_params(precom->m_paramsEnc);
        write_params(precom->m_paramsDec);

        auto write_2d = [&](const auto& outer) {
            ar(static_cast<uint32_t>(outer.size()));
            for (const auto& inner : outer) {
                ar(static_cast<uint32_t>(inner.size()));
                for (const auto& pt : inner)
                    ar(pt->template GetElement<lbcrypto::DCRTPoly>());
            }
        };
        auto write_1d = [&](const auto& vec) {
            ar(static_cast<uint32_t>(vec.size()));
            for (const auto& pt : vec)
                ar(pt->template GetElement<lbcrypto::DCRTPoly>());
        };

        write_2d(precom->m_U0hatTPreFFT);
        write_2d(precom->m_U0PreFFT);
        write_1d(precom->m_U0Pre);
        write_1d(precom->m_U0hatTPre);
    }
    bin.close();
    std::cout << "[NIOBIUM] Wrote bootstrap precompute: "
              << bin_path.filename().string() << " + "
              << ids_path.filename().string() << std::endl;
}

// ============================================================================
// refresh_all_inputs() — re-extract ALL polynomial data at replay() time
// ============================================================================
// This is the fhetch_driver approach: walk all stored OpenFHE objects
// (ciphertexts + keys) and capture their polynomial data at whatever
// FHETCH addresses their current poly IDs map to. This captures
// derived addresses created by OpenFHE's pre-processing.

static size_t extract_all_polys_from_dcrt(niobium::Compiler& compiler,
                                          const std::string& name,
                                          const std::vector<DCRTPoly>& dcrts) {
    size_t count = 0;
    for (const auto& dcrt : dcrts) {
        for (const auto& poly : dcrt.GetAllElements()) {
            uintptr_t poly_id = poly.GetId();
            uint64_t fhetch_addr = niobium::detail::lookup_fhetch_address(poly_id);
            if (fhetch_addr == static_cast<uint64_t>(-1)) continue;

            uint64_t modulus = poly.GetModulus().ConvertToInt();
            size_t n = poly.GetLength();
            std::vector<uint64_t> vals(n);
            const auto& vec = poly.GetValues();
            for (size_t i = 0; i < n; ++i)
                vals[i] = vec[i].ConvertToInt();

            compiler.store_input_element(name, fhetch_addr, modulus, vals);
            count++;
        }
    }
    return count;
}

void niobium::Compiler::refresh_all_inputs() {
    // Clear previously captured inputs — we're re-capturing everything
    // from the live OpenFHE objects at their current state.
    clear_captured_inputs();

    size_t total = 0;

    // Re-extract from stored ciphertexts
    for (size_t i = 0; i < g_stored_ciphertexts.size(); ++i) {
        const auto& ct = g_stored_ciphertexts[i];
        if (!ct) continue;
        total += extract_all_polys_from_dcrt(*this, "ct_" + std::to_string(i),
                                             ct->GetElements());
    }

    // Re-extract from ALL EvalMult keys
    try {
        const auto& allMultKeys = lbcrypto::CryptoContextImpl<DCRTPoly>::GetAllEvalMultKeys();
        for (const auto& [keyTag, keyVec] : allMultKeys) {
            for (const auto& evalKey : keyVec) {
                total += extract_all_polys_from_dcrt(*this, "evalmult_key",
                             evalKey->GetAVector());
                total += extract_all_polys_from_dcrt(*this, "evalmult_key",
                             evalKey->GetBVector());
            }
        }
    } catch (...) {}

    // Re-extract from ALL EvalAutomorphism keys
    try {
        const auto& allAutoKeys = lbcrypto::CryptoContextImpl<DCRTPoly>::GetAllEvalAutomorphismKeys();
        for (const auto& [keyTag, keyMap] : allAutoKeys) {
            if (!keyMap) continue;
            for (const auto& [idx, evalKey] : *keyMap) {
                total += extract_all_polys_from_dcrt(*this, "automorphism_key",
                             evalKey->GetAVector());
                total += extract_all_polys_from_dcrt(*this, "automorphism_key",
                             evalKey->GetBVector());
            }
        }
    } catch (...) {}

    std::cout << "[NIOBIUM] Refreshed " << total
              << " polynomials from live OpenFHE objects" << std::endl;
}

// ============================================================================
// reconstruct_probes() — fill ciphertext templates with simulator output
// ============================================================================

// reconstruct_probes() needs both Compiler::Impl (from compiler.cpp) and
// OpenFHE types. We implement it here using the public API + fhetch_replay_outputs.json
// rather than accessing Impl directly.

void Compiler::reconstruct_probes() {
    auto dir = get_program_directory();
    auto templates_dir = dir / "ciphertext_templates";
    auto serialized_dir = dir / "serialized_probes";
    auto outputs_path = dir / "fhetch_replay_outputs.json";

    if (!std::filesystem::exists(outputs_path)) return;
    std::filesystem::create_directories(serialized_dir);

    // Load the simulator's computed output values
    std::ifstream ifs(outputs_path);
    if (!ifs.is_open()) return;
    nlohmann::json outputs_json = nlohmann::json::parse(ifs, nullptr, false);
    if (outputs_json.is_discarded() || !outputs_json.contains("outputs")) return;

    for (const auto& output_entry : outputs_json["outputs"]) {
        std::string name = output_entry.value("name", "");
        auto template_path = templates_dir / (name + ".template");
        if (!std::filesystem::exists(template_path)) continue;

        lbcrypto::Ciphertext<DCRTPoly> ct;
        if (!lbcrypto::Serial::DeserializeFromFile(template_path.string(), ct, lbcrypto::SerType::BINARY)) {
            std::cerr << "[NIOBIUM] Failed to load template for " << name << std::endl;
            continue;
        }

        // Fill the polynomial values from the simulator output
        const auto& sim_elements = output_entry["elements"];
        auto& ct_elements = ct->GetElements();
        size_t elem_idx = 0;
        for (auto& dcrt : ct_elements) {
            auto& polys = dcrt.GetAllElements();
            for (auto& native_poly : polys) {
                if (elem_idx >= sim_elements.size()) break;
                const auto& sim_elem = sim_elements[elem_idx];
                if (sim_elem.value("status", "") == "computed" && sim_elem.contains("values")) {
                    auto values = sim_elem["values"].get<std::vector<uint64_t>>();
                    size_t ring_dim = native_poly.GetLength();
                    auto modulus = native_poly.GetModulus();
                    lbcrypto::NativeVector nv(ring_dim, modulus);
                    for (size_t i = 0; i < ring_dim && i < values.size(); i++)
                        nv[i] = lbcrypto::NativeInteger(values[i]);
                    native_poly.SetValues(std::move(nv), native_poly.GetFormat());
                }
                elem_idx++;
            }
        }

        auto ct_path = serialized_dir / (name + ".ct");
        if (lbcrypto::Serial::SerializeToFile(ct_path.string(), ct, lbcrypto::SerType::BINARY)) {
            std::cout << "[NIOBIUM] Serialized probe '" << name << "' to " << ct_path << std::endl;
        }
    }
}

// ============================================================================
// result() — load simulator-computed ciphertext from serialized_probes/
// ============================================================================

template<>
bool Compiler::result<lbcrypto::CryptoContext<DCRTPoly>, lbcrypto::Ciphertext<DCRTPoly>>(
    lbcrypto::CryptoContext<DCRTPoly>& /*cc*/,
    const std::string& var_name,
    lbcrypto::Ciphertext<DCRTPoly>& ct_result) {
    auto dir = get_program_directory();
    auto serialized_path = dir / "serialized_probes" / (var_name + ".ct");
    if (std::filesystem::exists(serialized_path)) {
        if (lbcrypto::Serial::DeserializeFromFile(serialized_path.string(), ct_result, lbcrypto::SerType::BINARY)) {
            std::cout << "[NIOBIUM] Loaded result '" << var_name << "' from serialized probe" << std::endl;
            return true;
        }
    }
    std::cerr << "[NIOBIUM] Result '" << var_name << "' not found" << std::endl;
    return false;
}

}  // namespace niobium
