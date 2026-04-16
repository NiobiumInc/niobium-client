// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "niobium/compiler.h"
#include "niobium/fhetch_sim/simulator.h"
#include "trace_writer.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>

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

    // Crypto context info
    uint64_t ring_dimension = 0;
    std::vector<uint64_t> modulus_chain;

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

    // Populate simulator memory from captured input data
    for (const auto& input : impl_->captured_inputs) {
        for (const auto& elem : input.elements) {
            impl_->simulator->store_polynomial(elem.addr_id, elem.values, elem.modulus);
        }
    }
    if (!impl_->captured_inputs.empty()) {
        std::cout << "[NIOBIUM] Loaded " << impl_->captured_inputs.size()
                  << " inputs into simulator memory" << std::endl;
    }

    auto result = impl_->simulator->run();

    if (result.errors > 0) {
        std::cerr << "[NIOBIUM] Replay failed: " << result.errors << " errors" << std::endl;
        return false;
    }

    std::cout << "[NIOBIUM] Replay complete: " << result.instructions_executed
              << " instructions, " << result.elapsed_seconds << "s" << std::endl;

    // Write output polynomial values for probe addresses
    write_replay_outputs();

    return true;
}

// ============================================================================
// write_replay_json — serialize inputs, outputs, and metadata for replay
// ============================================================================

void Compiler::write_replay_json() {
    using json = nlohmann::json;
    auto dir = get_program_directory();
    auto path = dir / "fhetch_replay.json";

    json replay;
    replay["program_name"] = impl_->full_program_name();
    replay["ring_dimension"] = impl_->ring_dimension;
    replay["trace_file"] = impl_->last_trace_path.filename().string();

    // Per-input files
    json inputs_arr = json::array();
    for (const auto& input : impl_->captured_inputs) {
        std::string input_file = impl_->full_program_name() + ".input_" + input.name + ".json";

        json idx;
        idx["name"] = input.name;
        idx["file"] = input_file;
        idx["element_count"] = input.elements.size();
        inputs_arr.push_back(idx);

        // Write per-input data file
        json input_data;
        input_data["name"] = input.name;
        json elems_arr = json::array();
        for (const auto& e : input.elements) {
            json elem;
            elem["addr_id"] = e.addr_id;
            elem["modulus"] = e.modulus;
            elem["values"] = e.values;
            elems_arr.push_back(elem);
        }
        input_data["elements"] = elems_arr;

        std::ofstream inp(dir / input_file);
        if (inp.is_open()) {
            inp << input_data.dump(2) << std::endl;
            inp.close();
        }
    }
    replay["inputs"] = inputs_arr;

    // Output probe definitions
    json outputs_arr = json::array();
    for (const auto& output : impl_->captured_outputs) {
        json out_entry;
        out_entry["name"] = output.name;
        out_entry["addr_ids"] = output.addr_ids;
        out_entry["moduli"] = output.moduli;
        outputs_arr.push_back(out_entry);
    }
    replay["outputs"] = outputs_arr;

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
