// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "niobium/compiler.h"
#include "niobium/fhetch_sim/simulator.h"
#include "compiler_internal.h"
#include "trace_writer.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <set>

// OpenFHE hollow mode global — defined in libOPENFHEcore.
// When true, polynomial operations skip expensive math but preserve structure.
namespace lbcrypto { extern bool g_hollow_mode; }

namespace niobium {

// ============================================================================
// Compiler::Impl — hidden implementation
// ============================================================================

struct Compiler::Impl {
    TraceWriter trace_writer;

    // Program metadata
    std::string program_name;
    std::string program_version;
    std::string program_description;
    std::string source_file;
    int source_line = 0;
    std::string build_timestamp;

    // Cache
    CacheParameters cache_params;
    std::string cache_suffix;

    // State
    bool running = false;
    bool stopped = false;
    bool hollow_mode = false;
    bool multithreaded = false;
    bool fhetch_mode = false;

    // Epochs
    uint32_t epoch_id = 0;

    // Crypto context info (populated by capture_crypto_context)
    uint64_t ring_dimension = 0;
    uint32_t multiplicative_depth = 0;
    uint32_t scaling_mod_size = 0;
    std::string scheme_name;
    std::string security_level;
    std::vector<uint64_t> modulus_chain;
    std::vector<uint64_t> inverse_modulus_chain;
    bool niobium_hw_mode = false;

    // Key start addr_ids (first addr_id recorded for each key type)
    uint64_t evalmult_start_addr_id = 0;
    uint64_t evalautomorphism_start_addr_id = 0;

    // Last written trace path (set by stop())
    std::filesystem::path last_trace_path;

    // Simulator instance (created by replay())
    std::unique_ptr<fhetch_sim::Simulator> simulator;

    // Input polynomial data captured by tag_input().
    // Each entry: {name, [{addr_id, modulus, values}]}
    struct PolyElement {
        uint64_t addr_id;
        uint64_t modulus;
        std::vector<uint64_t> values;
    };
    struct InputRecord {
        std::string name;
        std::vector<PolyElement> elements;
    };
    std::vector<InputRecord> captured_inputs;

    // Output probe addresses captured by probe().
    struct OutputRecord {
        std::string name;
        std::vector<uint64_t> addr_ids;
        std::vector<uint64_t> moduli;
    };
    std::vector<OutputRecord> captured_outputs;

    // Hook invoked by stop() right before fhetch_replay.json is written.
    // Set by capture_crypto_context<Ciphertext<DCRTPoly>> to auto-capture
    // CC-derived precomputed data (e.g. FHECKKSRNS bootstrap precompute)
    // without forcing the user to call a specific API for it.
    std::function<void()> auto_capture_at_stop;

    // Derived program directory
    std::filesystem::path program_dir;

    std::string full_program_name() const {
        std::string name = program_name;
        if (!cache_suffix.empty())
            name += "_" + cache_suffix;
        return name;
    }
};

// ============================================================================
// Singleton
// ============================================================================

static Compiler* g_compiler = nullptr;

Compiler& compiler() {
    if (!g_compiler) {
        g_compiler = new Compiler();
    }
    return *g_compiler;
}

// ============================================================================
// Constructor / Destructor
// ============================================================================

Compiler::Compiler() : impl_(std::make_unique<Impl>()) {}
Compiler::~Compiler() = default;

// ============================================================================
// Session lifecycle
// ============================================================================

void Compiler::init(int& argc, char** argv) {
    // Parse and consume Niobium-specific flags from argv.
    // Recognized flags: --hollow, --multithreaded, --ascii-json
    int write_pos = 1;
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--hollow") == 0) {
            impl_->hollow_mode = true;
        } else if (std::strcmp(argv[i], "--multithreaded") == 0) {
            impl_->multithreaded = true;
        } else {
            argv[write_pos++] = argv[i];
        }
    }
    argc = write_pos;
}

bool Compiler::start() {
    if (impl_->running) return false;
    impl_->running = true;
    impl_->stopped = false;
    impl_->trace_writer.start_recording();
    std::cout << "[NIOBIUM] Recording started" << std::endl;
    return true;
}

bool Compiler::stop() {
    if (!impl_->running) return false;
    impl_->trace_writer.emit("halt");
    impl_->trace_writer.stop_recording();
    impl_->running = false;
    impl_->stopped = true;

    // Write the FHETCH trace file
    auto dir = get_program_directory();
    impl_->last_trace_path = impl_->trace_writer.write(dir, impl_->full_program_name());

    // Auto-capture any CC-derived precomputed data (e.g. CKKS bootstrap
    // precompute) before writing the replay index, mirroring the compiler's
    // create_replay_index(): user code only calls start()/stop(), never a
    // "tag bootstrap precompute" API.
    if (impl_->auto_capture_at_stop) {
        impl_->auto_capture_at_stop();
    }

    // Write fhetch_replay.json with inputs, outputs, and modulus table
    write_replay_json();

    std::cout << "[NIOBIUM] Recording stopped ("
              << impl_->trace_writer.instruction_count()
              << " instructions)" << std::endl;
    return true;
}

bool Compiler::pause() {
    if (!impl_->running) return false;
    impl_->trace_writer.pause_recording();
    return true;
}

bool Compiler::resume() {
    if (!impl_->running) return false;
    impl_->trace_writer.resume_recording();
    return true;
}

// ============================================================================
// Program metadata
// ============================================================================

void Compiler::set_program_info(const std::string& name,
                                const std::string& version,
                                const std::string& description) {
    impl_->program_name = name;
    impl_->program_version = version;
    impl_->program_description = description;
    impl_->trace_writer.set_program_info(name, version, description);
}

void Compiler::set_build_info(const std::string& file, int line,
                              const std::string& timestamp) {
    impl_->source_file = file;
    impl_->source_line = line;
    impl_->build_timestamp = timestamp;
    impl_->trace_writer.set_source_info(file, line, timestamp);
}

// ============================================================================
// Cache management
// ============================================================================

void Compiler::cache_parameters(CacheParameters& params) {
    impl_->cache_params = params;

    // Build cache suffix from parameters
    std::string suffix;
    for (const auto& [key, value] : params) {
        if (!suffix.empty()) suffix += "_";
        suffix += key + "_" + value;
    }
    impl_->cache_suffix = suffix;
}

bool Compiler::is_cache_valid() {
    // Check if a trace file already exists for this program configuration
    auto dir = get_program_directory();
    auto trace_path = dir / (impl_->full_program_name() + ".fhetch");
    return std::filesystem::exists(trace_path);
}

// ============================================================================
// Recording modes
// ============================================================================

void Compiler::set_ring_dimension(uint64_t N) {
    impl_->ring_dimension = N;
    // Propagate to the probe layer so the scratch buffer returned from
    // openfhe_cprobe_address is sized correctly for OpenFHE's CopyValues.
    detail::set_precompute_ring_dim(static_cast<uintptr_t>(N));
}

void Compiler::set_crypto_context_info(const std::string& scheme_name,
                                       uint32_t multiplicative_depth,
                                       uint32_t scaling_mod_size,
                                       const std::string& security_level,
                                       const std::vector<uint64_t>& modulus_chain) {
    impl_->scheme_name = scheme_name;
    impl_->multiplicative_depth = multiplicative_depth;
    impl_->scaling_mod_size = scaling_mod_size;
    impl_->security_level = security_level;
    impl_->modulus_chain = modulus_chain;

    // Compute inverse modulus chain (Hensel lifting)
    impl_->inverse_modulus_chain.clear();
    for (uint64_t q : modulus_chain) {
        uint64_t ninv = 1;
        for (int i = 1; i < 64; i++) {
            if (((q * ninv) >> i) & 1)
                ninv |= (1ULL << i);
        }
        impl_->inverse_modulus_chain.push_back(ninv);
    }
}

void Compiler::set_key_start_addr_id(const std::string& key_type, uint64_t addr_id) {
    if (key_type == "evalmult")
        impl_->evalmult_start_addr_id = addr_id;
    else if (key_type == "evalautomorphism")
        impl_->evalautomorphism_start_addr_id = addr_id;
}

void Compiler::reserve_addresses(uint64_t next_addr) {
    detail::reserve_fhetch_addresses(next_addr);
}

void Compiler::clear_captured_inputs() {
    impl_->captured_inputs.clear();
}

void Compiler::set_auto_capture_at_stop(std::function<void()> fn) {
    impl_->auto_capture_at_stop = std::move(fn);
}

void Compiler::store_input_element(const std::string& input_name,
                                   uint64_t addr_id, uint64_t modulus,
                                   const std::vector<uint64_t>& values) {
    // Append to existing InputRecord or create a new one
    for (auto& rec : impl_->captured_inputs) {
        if (rec.name == input_name) {
            rec.elements.push_back({addr_id, modulus, values});
            return;
        }
    }
    Impl::InputRecord rec;
    rec.name = input_name;
    rec.elements.push_back({addr_id, modulus, values});
    impl_->captured_inputs.push_back(std::move(rec));
}

void Compiler::store_output_probe(const std::string& output_name,
                                  uint64_t addr_id, uint64_t modulus) {
    for (auto& rec : impl_->captured_outputs) {
        if (rec.name == output_name) {
            rec.addr_ids.push_back(addr_id);
            rec.moduli.push_back(modulus);
            return;
        }
    }
    Impl::OutputRecord rec;
    rec.name = output_name;
    rec.addr_ids.push_back(addr_id);
    rec.moduli.push_back(modulus);
    impl_->captured_outputs.push_back(std::move(rec));
}

void Compiler::enable_hollow_mode(bool enabled) {
    impl_->hollow_mode = enabled;
    lbcrypto::g_hollow_mode = enabled;
    if (enabled) {
        std::cout << "[NIOBIUM] Hollow mode ENABLED — skipping polynomial math" << std::endl;
    } else {
        std::cout << "[NIOBIUM] Hollow mode DISABLED — using real math" << std::endl;
    }
}

bool Compiler::is_hollow_mode() const {
    return impl_->hollow_mode;
}

void Compiler::enable_multithreaded_recording() {
    impl_->multithreaded = true;
}

bool Compiler::is_multithreaded() const {
    return impl_->multithreaded;
}

// ============================================================================
// FHETCH mode
// ============================================================================

bool Compiler::is_fhetch_mode() const {
    return impl_->fhetch_mode;
}

void Compiler::set_fhetch_mode() {
    impl_->fhetch_mode = true;
}

// ============================================================================
// Functional epochs
// ============================================================================

void Compiler::start_epoch() {
    // Nothing to memorize in the client — epochs are a recording-phase concept.
    // The trace writer handles the reset.
}

bool Compiler::stop_epoch() {
    if (!impl_->running) return false;

    // Finalize the current epoch's trace
    impl_->trace_writer.emit("halt");
    impl_->trace_writer.stop_recording();

    auto epoch_dir = get_program_directory() / ("epoch_" + std::to_string(impl_->epoch_id));
    std::string epoch_name = impl_->full_program_name() + "_epoch_" + std::to_string(impl_->epoch_id);
    impl_->trace_writer.write(epoch_dir, epoch_name);

    // Reset for next epoch
    impl_->trace_writer.clear();
    impl_->epoch_id++;
    impl_->trace_writer.start_recording();
    return true;
}

uint32_t Compiler::epoch_id() const {
    return impl_->epoch_id;
}

// ============================================================================
// Replay — run the FHETCH simulator
// ============================================================================

bool Compiler::replay() {
    if (impl_->last_trace_path.empty()) {
        // Look for an existing trace (cached)
        auto dir = get_program_directory();
        auto path = dir / (impl_->full_program_name() + ".fhetch");
        if (std::filesystem::exists(path)) {
            impl_->last_trace_path = path;
        } else {
            std::cerr << "[NIOBIUM] No trace file found for replay" << std::endl;
            return false;
        }
    }

    if (impl_->ring_dimension == 0) {
        std::cerr << "[NIOBIUM] Ring dimension not set — call capture_crypto_context() before replay()" << std::endl;
        return false;
    }

    std::cout << "[NIOBIUM] Replaying trace: " << impl_->last_trace_path << std::endl;

    impl_->simulator = std::make_unique<fhetch_sim::Simulator>();
    impl_->simulator->set_ring_dimension(impl_->ring_dimension);

    if (!impl_->simulator->load_trace(impl_->last_trace_path)) {
        std::cerr << "[NIOBIUM] Failed to load trace for replay" << std::endl;
        return false;
    }

    // Compute the "live-in" set: addresses read before being written in the trace.
    // Only these addresses need input data; addresses that are written first
    // get their values from the simulation itself.
    auto rbw_addrs = impl_->simulator->get_read_before_write_addresses();
    std::set<uint64_t> rbw_set(rbw_addrs.begin(), rbw_addrs.end());

    // Populate simulator memory from snapshots grabbed by OpenFHE's
    // CopyValues path (openfhe_cprobe_address → openfhe_cprobe_precompute).
    // Those cover every FHETCH address whose poly fired the precompute
    // probe — i.e. bootstrap DFT plaintexts AND their intermediate polys.
    {
        size_t snap_live = 0;
        const auto& snaps = detail::get_precompute_snapshots();
        for (const auto& [addr, values] : snaps) {
            if (impl_->simulator->is_initialized(addr)) continue;
            // modulus=0 → simulator uses the m=N from each instruction's
            // modulus_index for the actual arithmetic; the stored modulus
            // is only used for same-tower chaining, which matches values
            // written by OpenFHE in EVAL form.
            impl_->simulator->store_polynomial(addr, values, 0);
            if (rbw_set.count(addr)) ++snap_live;
        }
        if (!snaps.empty()) {
            std::cout << "[NIOBIUM]   precompute_snapshots: " << snaps.size()
                      << " polys, " << snap_live << " live-in" << std::endl;
        }
    }

    // Populate simulator memory from captured input data.
    //
    // Load captured polys unconditionally (not only live-in addresses).
    // Rationale: inputs like bootstrap-precompute plaintexts are typically
    // *cloned* before being consumed by the trace; the clone's address is
    // the one that ends up live-in, and the data-parent chain carries data
    // from the captured original to the clone. If we skip non-live-in
    // captures here, the parent is never materialized and propagation
    // cannot reach the clone.
    size_t direct_count = 0;
    for (const auto& input : impl_->captured_inputs) {
        size_t live = 0, aux = 0;
        for (const auto& elem : input.elements) {
            impl_->simulator->store_polynomial(elem.addr_id, elem.values, elem.modulus);
            direct_count++;
            if (rbw_set.count(elem.addr_id)) ++live;
            else ++aux;
        }
        std::cout << "[NIOBIUM]   " << input.name << ": "
                  << input.elements.size() << " elements, "
                  << live << " live-in, " << aux << " aux (for parent chain)"
                  << std::endl;
    }

    // Propagate data through the copy/move lineage. Both directions:
    //
    //  1. Forward  (parent -> child): after `child = copy(parent)` the
    //     child holds the parent's data, so if the parent is captured,
    //     the child inherits.
    //
    //  2. Reverse (child -> parent): if a captured poly is the end of a
    //     copy chain (e.g. an object stored in a map that was built by
    //     copying an earlier temporary), the earlier poly still holds
    //     the same data unless it was modified after the copy. For
    //     pre-start captures (inputs, keys, bootstrap precompute) the
    //     chain is stable, so reverse propagation is safe and fills in
    //     the addresses the trace actually reads from.
    //
    // We iterate to a fixed point so multi-hop chains get filled in.
    const auto& parent_map = detail::get_data_parent_map();
    size_t propagated = 0;
    bool changed = true;
    while (changed) {
        changed = false;
        for (const auto& [child, parent] : parent_map) {
            auto copy_one = [&](uint64_t dst, uint64_t src) {
                if (!impl_->simulator->is_initialized(dst) &&
                     impl_->simulator->is_initialized(src)) {
                    impl_->simulator->store_polynomial(
                        dst,
                        impl_->simulator->get_polynomial(src),
                        impl_->simulator->get_modulus(src));
                    propagated++;
                    changed = true;
                }
            };
            // forward
            if (rbw_set.count(child)) copy_one(child, parent);
            // reverse
            if (rbw_set.count(parent)) copy_one(parent, child);
        }
    }

    // Report unloaded live-in addresses
    std::vector<uint64_t> unloaded;
    for (uint64_t a : rbw_set) {
        if (!impl_->simulator->is_initialized(a))
            unloaded.push_back(a);
    }

    // Debug: dump a few sample values for every loaded input so we can
    // visually confirm (a) the data is non-zero, and (b) it lives at the
    // address the trace actually reads from. Enable with NIOBIUM_DEBUG_LIVEIN=1
    // or automatically if at least one input is all-zero.
    {
        const char* env = std::getenv("NIOBIUM_DEBUG_LIVEIN");
        bool force_dump = env && *env && std::string(env) != "0";
        size_t zero_addrs = 0;
        for (uint64_t a : rbw_set) {
            if (!impl_->simulator->is_initialized(a)) continue;
            const auto& v = impl_->simulator->get_polynomial(a);
            bool all_zero = true;
            for (uint64_t x : v) if (x != 0) { all_zero = false; break; }
            if (all_zero) ++zero_addrs;
        }
        if (force_dump || zero_addrs > 0) {
            std::cout << "[NIOBIUM-DBG] live-in data check ("
                      << zero_addrs << " all-zero of " << rbw_set.size() << "):" << std::endl;
            std::vector<uint64_t> sorted(rbw_set.begin(), rbw_set.end());
            std::sort(sorted.begin(), sorted.end());
            for (uint64_t a : sorted) {
                if (!impl_->simulator->is_initialized(a)) continue;
                const auto& v = impl_->simulator->get_polynomial(a);
                uint64_t q = impl_->simulator->get_modulus(a);
                bool all_zero = true;
                for (uint64_t x : v) if (x != 0) { all_zero = false; break; }
                std::cout << "[NIOBIUM-DBG]   %" << a
                          << " q=0x" << std::hex << q << std::dec
                          << " v[0..3]=" << (v.size()>0?v[0]:0)
                          << "," << (v.size()>1?v[1]:0)
                          << "," << (v.size()>2?v[2]:0)
                          << "," << (v.size()>3?v[3]:0)
                          << (all_zero ? "  [ALL-ZERO]" : "")
                          << std::endl;
            }
        }
    }

    // Debug: dump unloaded live-in addresses + a sample of captured non-live-in.
    if (std::getenv("NIOBIUM_DEBUG_ADDRS") && !unloaded.empty()) {
        std::sort(unloaded.begin(), unloaded.end());
        std::set<uint64_t> captured_addrs;
        for (const auto& input : impl_->captured_inputs)
            for (const auto& e : input.elements)
                captured_addrs.insert(e.addr_id);
        std::cout << "[NIOBIUM-DBG] unloaded live-in: min=" << unloaded.front()
                  << " max=" << unloaded.back() << " count=" << unloaded.size()
                  << std::endl;
        std::cout << "[NIOBIUM-DBG] captured addrs: ";
        std::vector<uint64_t> sorted_cap(captured_addrs.begin(), captured_addrs.end());
        std::sort(sorted_cap.begin(), sorted_cap.end());
        if (!sorted_cap.empty())
            std::cout << "min=" << sorted_cap.front() << " max=" << sorted_cap.back()
                      << " count=" << sorted_cap.size();
        std::cout << std::endl;
        size_t overlap = 0;
        for (uint64_t a : unloaded) if (captured_addrs.count(a)) ++overlap;
        std::cout << "[NIOBIUM-DBG] unloaded ∩ captured = " << overlap << std::endl;
    }

    std::cout << "[NIOBIUM] Live-in: " << rbw_set.size()
              << ", loaded: " << direct_count << " direct + "
              << propagated << " propagated, unloaded: " << unloaded.size();
    if (!unloaded.empty() && unloaded.size() <= 100) {
        std::cout << " [";
        for (size_t i = 0; i < unloaded.size(); ++i) {
            if (i > 0) std::cout << ", ";
            std::cout << "%" << unloaded[i];
        }
        std::cout << "]";
    }
    std::cout << std::endl;

    auto result = impl_->simulator->run();

    if (result.errors > 0) {
        std::cerr << "[NIOBIUM] Replay failed: " << result.errors << " errors" << std::endl;
        return false;
    }

    std::cout << "[NIOBIUM] Replay complete: " << result.instructions_executed
              << " instructions, " << result.elapsed_seconds << "s" << std::endl;

    // Write output polynomial values for probe addresses
    write_replay_outputs();

    // Reconstruct ciphertext probes from simulator output
    // (reads fhetch_replay_outputs.json written above)
    reconstruct_probes();

    return true;
}

// ============================================================================
// write_replay_json — serialize inputs, outputs, and metadata for replay
// ============================================================================

void Compiler::write_replay_json() {
    using json = nlohmann::json;
    auto dir = get_program_directory();
    auto path = dir / "fhetch_replay.json";
    std::string prog = impl_->full_program_name();

    json replay;

    // ---- program_name / program_info ----
    replay["program_name"] = prog + ".fhetch";
    replay["program_info"] = {
        {"name", impl_->program_name},
        {"version", impl_->program_version},
        {"description", impl_->program_description}
    };

    // ---- crypto_context (matches compiler's schema) ----
    json cc;
    cc["scheme_name"] = impl_->scheme_name;
    cc["ring_dimension"] = impl_->ring_dimension;
    cc["multiplicative_depth"] = impl_->multiplicative_depth;
    cc["scaling_modulus_size"] = impl_->scaling_mod_size;
    cc["security_level"] = impl_->security_level;
    // Use the trace writer's modulus table as the authoritative source —
    // it includes all moduli encountered during recording (base chain +
    // key-switching moduli), matching the .fhetch file's modulus_count.
    const auto& trace_moduli = impl_->trace_writer.modulus_table();
    if (!trace_moduli.empty()) {
        cc["modulus_chain"] = trace_moduli;
        cc["modulus_chain_length"] = trace_moduli.size();
        // Recompute inverse chain for the complete set
        std::vector<uint64_t> inv_chain;
        for (uint64_t q : trace_moduli) {
            uint64_t ninv = 1;
            for (int i = 1; i < 64; i++) {
                if (((q * ninv) >> i) & 1)
                    ninv |= (1ULL << i);
            }
            inv_chain.push_back(ninv);
        }
        cc["inverse_modulus_chain"] = inv_chain;
    } else {
        cc["modulus_chain"] = impl_->modulus_chain;
        cc["modulus_chain_length"] = impl_->modulus_chain.size();
        cc["inverse_modulus_chain"] = impl_->inverse_modulus_chain;
    }
    cc["is_valid"] = true;
    replay["crypto_context"] = cc;

    // ---- files ----
    json files;
    files["instructions"] = impl_->last_trace_path.filename().string();

    // Inputs (master index referencing per-input .bin + .ids)
    std::string inputs_index_file = prog + ".inputs.json";
    files["inputs"] = inputs_index_file;

    // Write the inputs index file (same format as compiler's inputs.cbor)
    {
        json inputs_index;
        inputs_index["program_name"] = prog + ".fhetch";
        inputs_index["input_count"] = impl_->captured_inputs.size();
        inputs_index["input_format"] = "cereal_binary";
        inputs_index["timestamp"] = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        json inputs_arr = json::array();
        for (const auto& input : impl_->captured_inputs) {
            json idx;
            idx["name"] = input.name;
            idx["ids_file"] = prog + ".input_" + input.name + ".ids";
            idx["bin_file"] = prog + ".input_" + input.name + ".bin";
            idx["instances_count"] = 1;
            inputs_arr.push_back(idx);
        }
        inputs_index["inputs"] = inputs_arr;
        std::ofstream inp_out(dir / inputs_index_file);
        if (inp_out.is_open()) {
            inp_out << inputs_index.dump(2) << std::endl;
            inp_out.close();
        }
    }

    // Outputs
    std::string outputs_file = prog + ".outputs.json";
    files["outputs"] = outputs_file;

    // Write the outputs file (same format as compiler's outputs.cbor)
    {
        json outputs_data;
        outputs_data["program_name"] = prog + ".fhetch";
        outputs_data["timestamp"] = std::chrono::duration_cast<std::chrono::seconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();
        json outputs_arr = json::array();
        for (const auto& output : impl_->captured_outputs) {
            json out_entry;
            out_entry["name"] = output.name;
            out_entry["payload_type"] = "ciphertext";
            json ct_data = json::array();
            for (size_t j = 0; j < output.addr_ids.size(); ++j) {
                json poly;
                poly["poly_index"] = j;
                poly["elements"] = json::array({output.addr_ids[j]});
                ct_data.push_back(poly);
            }
            out_entry["ciphertext_data"] = ct_data;
            outputs_arr.push_back(out_entry);
        }
        outputs_data["outputs"] = outputs_arr;
        std::ofstream out_out(dir / outputs_file);
        if (out_out.is_open()) {
            out_out << outputs_data.dump(2) << std::endl;
            out_out.close();
        }
    }

    // Key file references
    auto mk_bin = dir / (prog + ".mk.bin");
    if (std::filesystem::exists(mk_bin)) {
        files["evalmult_keys"] = (dir / (prog + ".mk.bin")).string();
        files["evalmult_ids"] = (dir / (prog + ".mk.ids")).string();
    }
    auto rk_bin = dir / (prog + ".rk.bin");
    if (std::filesystem::exists(rk_bin)) {
        files["evalautomorphism_keys"] = (dir / (prog + ".rk.bin")).string();
        files["evalautomorphism_ids"] = (dir / (prog + ".rk.ids")).string();
    }
    auto bp_bin = dir / (prog + ".bp.bin");
    if (std::filesystem::exists(bp_bin)) {
        files["bootstrap_precomp"] = (prog + ".bp.bin");
        files["bootstrap_precomp_ids"] = (prog + ".bp.ids");
    }

    replay["files"] = files;

    // ---- Top-level fields matching compiler's replay.json ----
    replay["input_format"] = "cereal_binary";
    replay["evalmult_format"] = "cereal_binary";
    replay["evalautomorphism_format"] = "cereal_binary";
    replay["niobium_hw"] = impl_->niobium_hw_mode;
    replay["num_registers"] = 16;
    replay["config_sectors"] = 1;
    replay["hbm_mode"] = "interleaved";
    replay["max_memory_id"] = 0;

    // Key start addr_ids
    json key_start;
    if (impl_->evalmult_start_addr_id > 0)
        key_start["evalmult"] = impl_->evalmult_start_addr_id;
    if (impl_->evalautomorphism_start_addr_id > 0)
        key_start["evalautomorphism"] = impl_->evalautomorphism_start_addr_id;
    if (!key_start.empty())
        replay["key_start_addr_ids"] = key_start;

    replay["generated_timestamp"] = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    std::ofstream out(path);
    if (out.is_open()) {
        out << replay.dump(2) << std::endl;
        out.close();
        std::cout << "[NIOBIUM] Replay JSON written: " << path << std::endl;
    }
}

// ============================================================================
// write_replay_outputs — after simulation, write computed probe values
// ============================================================================

void Compiler::write_replay_outputs() {
    using json = nlohmann::json;
    if (!impl_->simulator || impl_->captured_outputs.empty()) return;

    auto dir = get_program_directory();
    auto path = dir / "fhetch_replay_outputs.json";

    json root;
    json outputs_arr = json::array();
    for (const auto& output : impl_->captured_outputs) {
        json out_entry;
        out_entry["name"] = output.name;
        json elems_arr = json::array();
        for (size_t j = 0; j < output.addr_ids.size(); ++j) {
            uint64_t addr = output.addr_ids[j];
            auto values = impl_->simulator->get_polynomial(addr);
            json elem;
            elem["addr_id"] = addr;
            elem["modulus"] = output.moduli[j];
            elem["status"] = values.empty() ? "missing" : "computed";
            elem["values"] = values;
            elems_arr.push_back(elem);
        }
        out_entry["elements"] = elems_arr;
        outputs_arr.push_back(out_entry);
    }
    root["outputs"] = outputs_arr;

    std::ofstream out(path);
    if (out.is_open()) {
        out << root.dump(2) << std::endl;
        out.close();
        std::cout << "[NIOBIUM] Replay outputs written: " << path << std::endl;
    }
}

// ============================================================================
// State queries
// ============================================================================

bool Compiler::running_p() {
    return impl_->running;
}

bool Compiler::stopped_p() {
    return impl_->stopped;
}

std::string Compiler::program_name() {
    return impl_->full_program_name();
}

std::filesystem::path Compiler::get_program_directory() const {
    if (!impl_->program_dir.empty())
        return impl_->program_dir;

    // Default: create directory next to the executable or in cwd
    auto name = impl_->full_program_name();
    if (name.empty()) name = "niobium_trace";
    auto dir = std::filesystem::current_path() / name;
    return dir;
}

// Friend function declared in compiler.h — provides internal access to Impl.
Compiler::Impl& compiler_impl(Compiler& c) {
    return *c.impl_;
}

}  // namespace niobium

// ============================================================================
// Internal accessor for TraceWriter — used by fhetch_api.cpp and probes.cpp.
// ============================================================================

namespace niobium::detail {

TraceWriter& trace_writer() {
    auto& impl = niobium::compiler_impl(niobium::compiler());
    return impl.trace_writer;
}

}  // namespace niobium::detail
