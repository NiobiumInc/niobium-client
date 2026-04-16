// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Internal trace writer — records FHETCH operations and writes .fhetch files.

#pragma once

#include <cstdint>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

namespace niobium {

class TraceWriter {
public:
    TraceWriter();

    void set_program_info(const std::string& name, const std::string& version,
                          const std::string& description);
    void set_source_info(const std::string& file, int line,
                         const std::string& timestamp);

    bool is_recording() const { return recording_; }
    void start_recording();
    void stop_recording();
    void pause_recording();
    void resume_recording();

    // Emit a FHETCH instruction line into the trace.
    void emit(const std::string& instruction);

    // Emit a comment line (prefixed with #).
    void comment(const std::string& text);

    // Write the accumulated trace to a .fhetch file.
    // Returns the path written.
    std::filesystem::path write(const std::filesystem::path& directory,
                                const std::string& program_name);

    // Clear all recorded instructions (for epoch reset).
    void clear();

    size_t instruction_count() const { return instructions_.size(); }

private:
    bool recording_ = false;
    bool paused_ = false;
    std::string program_name_;
    std::string program_version_;
    std::string program_description_;
    std::string source_file_;
    int source_line_ = 0;
    std::string build_timestamp_;
    std::vector<std::string> instructions_;
    mutable std::mutex mutex_;
};

}  // namespace niobium
