// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "trace_writer.h"

#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>

namespace niobium {

TraceWriter::TraceWriter() = default;

void TraceWriter::set_program_info(const std::string& name,
                                   const std::string& version,
                                   const std::string& description) {
    program_name_ = name;
    program_version_ = version;
    program_description_ = description;
}

void TraceWriter::set_source_info(const std::string& file, int line,
                                  const std::string& timestamp) {
    source_file_ = file;
    source_line_ = line;
    build_timestamp_ = timestamp;
}

void TraceWriter::start_recording() {
    std::lock_guard<std::mutex> lock(mutex_);
    recording_ = true;
    paused_ = false;
}

void TraceWriter::stop_recording() {
    std::lock_guard<std::mutex> lock(mutex_);
    recording_ = false;
    paused_ = false;
}

void TraceWriter::pause_recording() {
    std::lock_guard<std::mutex> lock(mutex_);
    paused_ = true;
}

void TraceWriter::resume_recording() {
    std::lock_guard<std::mutex> lock(mutex_);
    paused_ = false;
}

uint32_t TraceWriter::register_modulus(uint64_t modulus) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = modulus_index_.find(modulus);
    if (it != modulus_index_.end())
        return it->second;
    uint32_t idx = static_cast<uint32_t>(modulus_table_.size());
    modulus_table_.push_back(modulus);
    modulus_index_[modulus] = idx;
    return idx;
}

void TraceWriter::emit(const std::string& instruction) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (recording_ && !paused_) {
        instructions_.push_back(instruction);
    }
}

void TraceWriter::emit_preamble(const std::string& instruction) {
    std::lock_guard<std::mutex> lock(mutex_);
    instructions_.push_back(instruction);
}

void TraceWriter::comment(const std::string& text) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (recording_ && !paused_) {
        instructions_.push_back("# " + text);
    }
}

std::filesystem::path TraceWriter::write(const std::filesystem::path& directory,
                                         const std::string& program_name) {
    std::filesystem::create_directories(directory);
    auto path = directory / (program_name + ".fhetch");

    std::ofstream out(path);
    if (!out.is_open()) {
        std::cerr << "[FHETCH] ERROR: Cannot write trace to " << path << std::endl;
        return {};
    }

    // Header
    out << "# =========================================\n";
    out << "# Niobium FHETCH Trace\n";
    out << "# =========================================\n";
    if (!program_name_.empty()) {
        out << "# Program: " << program_name_;
        if (!program_version_.empty()) out << " v" << program_version_;
        out << "\n";
    }
    if (!program_description_.empty())
        out << "# Description: " << program_description_ << "\n";
    if (!source_file_.empty())
        out << "# Source: " << source_file_ << ":" << source_line_ << "\n";
    if (!build_timestamp_.empty())
        out << "# Build: " << build_timestamp_ << "\n";
    out << "# Instruction Count: " << instructions_.size() << "\n";
    out << "# Modulus Count: " << modulus_table_.size() << "\n";

    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    out << "# Generated: " << now << "\n";
    out << "# =========================================\n";

    // Modulus table
    out << "\n# Modulus Table\n";
    out << "modulus_count " << modulus_table_.size() << "\n";
    for (size_t i = 0; i < modulus_table_.size(); ++i) {
        out << "m[" << i << "] 0x" << std::hex << std::uppercase
            << modulus_table_[i] << std::dec << "\n";
    }

    // Instructions
    out << "\n# Instructions\n";
    for (const auto& inst : instructions_) {
        out << inst << "\n";
    }

    out.close();
    std::cout << "[FHETCH] Trace written: " << path
              << " (" << instructions_.size() << " instructions, "
              << modulus_table_.size() << " moduli)" << std::endl;
    return path;
}

void TraceWriter::clear() {
    std::lock_guard<std::mutex> lock(mutex_);
    instructions_.clear();
    modulus_table_.clear();
    modulus_index_.clear();
}

}  // namespace niobium
