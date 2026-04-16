// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Binary serialization helpers for replay data.
// Matches the niobium-compiler's CerealIO format for .ids and .bin files.

#pragma once

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace niobium::cereal_io {

// ============================================================================
// .ids files — raw uint64_t arrays: [count][addr_id_0][addr_id_1]...
// ============================================================================

inline bool write_addr_ids(const std::filesystem::path& path,
                           const std::vector<uint64_t>& ids) {
    std::ofstream f(path, std::ios::binary);
    if (!f.is_open()) return false;
    uint64_t count = ids.size();
    f.write(reinterpret_cast<const char*>(&count), sizeof(count));
    f.write(reinterpret_cast<const char*>(ids.data()), count * sizeof(uint64_t));
    return f.good();
}

inline std::vector<uint64_t> read_addr_ids(const std::filesystem::path& path) {
    std::vector<uint64_t> ids;
    std::ifstream f(path, std::ios::binary);
    if (!f.is_open()) return ids;
    uint64_t count = 0;
    f.read(reinterpret_cast<char*>(&count), sizeof(count));
    ids.resize(count);
    f.read(reinterpret_cast<char*>(ids.data()), count * sizeof(uint64_t));
    return ids;
}

}  // namespace niobium::cereal_io
