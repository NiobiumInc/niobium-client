// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Simulator memory model — maps FHETCH addresses to polynomial data.

#pragma once

#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

namespace niobium::fhetch_sim {

struct MemoryElement {
    std::vector<uint64_t> values;
    uint64_t modulus = 0;
    bool initialized = false;
};

class Memory {
public:
    void set(uint64_t address, const std::vector<uint64_t>& values, uint64_t modulus) {
        auto& elem = mem_[address];
        elem.values = values;
        elem.modulus = modulus;
        elem.initialized = true;
    }

    void set(uint64_t address, std::vector<uint64_t>&& values, uint64_t modulus) {
        auto& elem = mem_[address];
        elem.values = std::move(values);
        elem.modulus = modulus;
        elem.initialized = true;
    }

    const MemoryElement& get(uint64_t address) const {
        static const MemoryElement empty;
        auto it = mem_.find(address);
        return (it != mem_.end()) ? it->second : empty;
    }

    bool is_initialized(uint64_t address) const {
        auto it = mem_.find(address);
        return it != mem_.end() && it->second.initialized;
    }

    size_t size() const { return mem_.size(); }

private:
    std::unordered_map<uint64_t, MemoryElement> mem_;
};

}  // namespace niobium::fhetch_sim
