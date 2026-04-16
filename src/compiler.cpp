// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "niobium/compiler.h"
#include "trace_writer.h"

#include <cstring>
#include <filesystem>
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
    auto trace_path = impl_->trace_writer.write(dir, impl_->full_program_name());

    // Save FHETCH input/output metadata if in FHETCH mode
    if (impl_->fhetch_mode) {
        // These are called from fhetch_api.cpp's save functions
        // which the user or the compiler triggers.
    }

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
