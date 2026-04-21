// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "archive.h"

#include <cstring>
#include <fstream>
#include <stdexcept>

namespace niobium::fhetch_transport {

namespace {

constexpr const char  kMagic[4] = {'N', 'B', 'A', 'R'};

// Append n bytes of `value` (little-endian) to `out`.
template <typename T>
void append_le(std::string& out, T value) {
    static_assert(std::is_unsigned_v<T>, "LE writer wants unsigned ints");
    for (std::size_t i = 0; i < sizeof(T); ++i) {
        out.push_back(static_cast<char>((value >> (i * 8)) & 0xff));
    }
}

// Read n bytes starting at `pos` as little-endian T; advances `pos`.
template <typename T>
T read_le(const std::string& buf, std::size_t& pos) {
    static_assert(std::is_unsigned_v<T>, "LE reader wants unsigned ints");
    if (pos + sizeof(T) > buf.size()) {
        throw std::runtime_error("archive truncated in fixed-width field");
    }
    T value = 0;
    for (std::size_t i = 0; i < sizeof(T); ++i) {
        value |= static_cast<T>(static_cast<unsigned char>(buf[pos + i]))
                 << (i * 8);
    }
    pos += sizeof(T);
    return value;
}

// Reject paths that would allow the archive to escape its extraction root.
// Keep this narrow: anything with a leading slash, an embedded ".." component,
// or a drive letter (Windows-style) trips the check. No symlink traversal
// because archive.h documents that only regular files are packed.
void validate_relative_path(const std::filesystem::path& rel) {
    if (rel.empty() || rel.is_absolute()) {
        throw std::runtime_error("archive entry must be a non-empty "
                                 "relative path: '" + rel.string() + "'");
    }
    for (const auto& part : rel) {
        const auto s = part.string();
        if (s == ".." || s == "") {
            throw std::runtime_error("archive entry contains invalid component: '"
                                     + rel.string() + "'");
        }
    }
}

}  // namespace

std::string
pack_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>& filter) {
    namespace fs = std::filesystem;
    if (!fs::exists(root) || !fs::is_directory(root)) {
        throw std::runtime_error("pack_directory: not a directory: "
                                 + root.string());
    }

    // First pass: collect entries and their sizes so the header count is
    // correct before we write bytes.
    struct Entry { fs::path abs; std::string rel; std::uintmax_t size; };
    std::vector<Entry> entries;
    std::uintmax_t total_bytes = 0;

    for (auto it = fs::recursive_directory_iterator(root); it != fs::recursive_directory_iterator(); ++it) {
        if (!it->is_regular_file()) continue;
        auto rel = fs::relative(it->path(), root);
        if (filter && !filter(rel)) continue;
        validate_relative_path(rel);
        const auto sz = fs::file_size(it->path());
        total_bytes += sz;
        entries.push_back({it->path(), rel.generic_string(), sz});
    }

    std::string out;
    out.reserve(static_cast<std::size_t>(total_bytes) + entries.size() * 32 + 64);

    out.append(kMagic, sizeof(kMagic));
    append_le<uint32_t>(out, static_cast<uint32_t>(entries.size()));

    for (const auto& e : entries) {
        append_le<uint32_t>(out, static_cast<uint32_t>(e.rel.size()));
        out.append(e.rel);
        append_le<uint64_t>(out, static_cast<uint64_t>(e.size));

        std::ifstream f(e.abs, std::ios::binary);
        if (!f.is_open()) {
            throw std::runtime_error("pack_directory: cannot open "
                                     + e.abs.string());
        }
        // Stream directly into the buffer — resize then read in-place
        // so we don't pay for an intermediate copy.
        const auto prev = out.size();
        out.resize(prev + static_cast<std::size_t>(e.size));
        f.read(out.data() + prev, static_cast<std::streamsize>(e.size));
        if (!f) {
            throw std::runtime_error("pack_directory: short read on "
                                     + e.abs.string());
        }
    }

    return out;
}

std::size_t
unpack_into(const std::string& archive,
            const std::filesystem::path& dest) {
    namespace fs = std::filesystem;
    std::size_t pos = 0;

    if (archive.size() < sizeof(kMagic) + sizeof(uint32_t)) {
        throw std::runtime_error("archive too small for header");
    }
    if (std::memcmp(archive.data(), kMagic, sizeof(kMagic)) != 0) {
        throw std::runtime_error("archive magic mismatch (expected NBAR)");
    }
    pos += sizeof(kMagic);

    const auto count = read_le<uint32_t>(archive, pos);
    fs::create_directories(dest);

    for (uint32_t i = 0; i < count; ++i) {
        const auto name_len = read_le<uint32_t>(archive, pos);
        if (pos + name_len > archive.size()) {
            throw std::runtime_error("archive truncated in name field");
        }
        std::string name(archive.data() + pos, name_len);
        pos += name_len;

        const auto data_len = read_le<uint64_t>(archive, pos);
        if (pos + data_len > archive.size()) {
            throw std::runtime_error("archive truncated in data field for '" + name + "'");
        }

        fs::path rel(name);
        validate_relative_path(rel);
        fs::path out_path = dest / rel;
        fs::create_directories(out_path.parent_path());

        std::ofstream f(out_path, std::ios::binary | std::ios::trunc);
        if (!f.is_open()) {
            throw std::runtime_error("cannot open output file: " + out_path.string());
        }
        if (data_len > 0) {
            f.write(archive.data() + pos,
                    static_cast<std::streamsize>(data_len));
            if (!f) {
                throw std::runtime_error("short write on " + out_path.string());
            }
        }
        pos += data_len;
    }

    return count;
}

}  // namespace niobium::fhetch_transport
