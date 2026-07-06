// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.

#include "archive.h"

#include <algorithm>
#include <cstring>
#include <fstream>
#include <stdexcept>
#include <vector>

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

std::vector<ArchiveEntry>
scan_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>& filter) {
    namespace fs = std::filesystem;
    if (!fs::exists(root) || !fs::is_directory(root)) {
        throw std::runtime_error("scan_directory: not a directory: "
                                 + root.string());
    }

    // Collect entries and their sizes so the header count is correct before we
    // write bytes. (Shared first pass for both buffered and streaming packing.)
    std::vector<ArchiveEntry> entries;
    for (auto it = fs::recursive_directory_iterator(root); it != fs::recursive_directory_iterator(); ++it) {
        if (!it->is_regular_file()) continue;
        auto rel = fs::relative(it->path(), root);
        if (filter && !filter(rel)) continue;
        validate_relative_path(rel);
        entries.push_back({it->path(), rel.generic_string(),
                           static_cast<std::uint64_t>(fs::file_size(it->path()))});
    }
    return entries;
}

std::uint64_t
archive_content_length(const std::vector<ArchiveEntry>& entries) {
    std::uint64_t total = sizeof(kMagic) + sizeof(uint32_t);  // magic + count
    for (const auto& e : entries) {
        total += sizeof(uint32_t) + e.rel.size() + sizeof(uint64_t) + e.size;
    }
    return total;
}

bool
stream_archive(const std::vector<ArchiveEntry>& entries,
               const std::function<bool(const char* data, std::size_t len)>& emit) {
    // Header: magic + entry count.
    std::string hdr;
    hdr.append(kMagic, sizeof(kMagic));
    append_le<uint32_t>(hdr, static_cast<uint32_t>(entries.size()));
    if (!emit(hdr.data(), hdr.size())) return false;

    constexpr std::size_t kChunk = 1u << 20;  // 1 MiB read/emit granularity
    std::vector<char> buf(kChunk);

    for (const auto& e : entries) {
        // Per-entry framing: name_len + name + data_len.
        std::string frame;
        append_le<uint32_t>(frame, static_cast<uint32_t>(e.rel.size()));
        frame.append(e.rel);
        append_le<uint64_t>(frame, e.size);
        if (!emit(frame.data(), frame.size())) return false;

        std::ifstream f(e.abs, std::ios::binary);
        if (!f.is_open()) {
            throw std::runtime_error("stream_archive: cannot open "
                                     + e.abs.string());
        }
        std::uint64_t remaining = e.size;
        while (remaining > 0) {
            const std::size_t n = static_cast<std::size_t>(
                std::min<std::uint64_t>(remaining, buf.size()));
            f.read(buf.data(), static_cast<std::streamsize>(n));
            if (!f) {
                throw std::runtime_error("stream_archive: short read on "
                                         + e.abs.string());
            }
            if (!emit(buf.data(), n)) return false;
            remaining -= n;
        }
    }
    return true;
}

std::string
pack_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>& filter) {
    // Buffered pack: reuse the streaming path, appending every span into one
    // contiguous buffer. Kept for callers (e.g. the small probe-response path)
    // that want the archive materialized.
    const auto entries = scan_directory(root, filter);
    std::string out;
    out.reserve(static_cast<std::size_t>(archive_content_length(entries)));
    stream_archive(entries, [&out](const char* data, std::size_t len) {
        out.append(data, len);
        return true;
    });
    return out;
}

ArchiveUnpacker::ArchiveUnpacker(std::filesystem::path dest)
    : dest_(std::move(dest)) {}

void
ArchiveUnpacker::feed(const char* data, std::size_t len) {
    namespace fs = std::filesystem;
    std::size_t off = 0;

    while (off < len) {
        // --- Streaming file data straight to disk (never buffered) ---------
        if (state_ == State::Data) {
            const std::size_t n = static_cast<std::size_t>(
                std::min<std::uint64_t>(data_remaining_, len - off));
            cur_file_.write(data + off, static_cast<std::streamsize>(n));
            if (!cur_file_) {
                throw std::runtime_error("ArchiveUnpacker: short write on '"
                                         + cur_name_ + "'");
            }
            off += n;
            data_remaining_ -= n;
            if (data_remaining_ == 0) {
                cur_file_.close();
                if (!cur_file_) {
                    throw std::runtime_error("ArchiveUnpacker: flush failed on '"
                                             + cur_name_ + "'");
                }
                ++done_count_;
                state_ = (done_count_ == count_) ? State::Done : State::NameLen;
                need_ = 4;
                pending_.clear();
            }
            continue;
        }
        if (state_ == State::Done) {
            throw std::runtime_error("ArchiveUnpacker: trailing bytes after "
                                     "final entry");
        }

        // --- Accumulate a fixed-width field or the name ---------------------
        const std::size_t want = need_ - pending_.size();
        const std::size_t n = std::min(want, len - off);
        pending_.append(data + off, n);
        off += n;
        if (pending_.size() < need_) return;  // need more bytes (chunk exhausted)

        switch (state_) {
            case State::Magic: {
                if (std::memcmp(pending_.data(), kMagic, sizeof(kMagic)) != 0) {
                    throw std::runtime_error("archive magic mismatch (expected NBAR)");
                }
                state_ = State::Count; need_ = sizeof(uint32_t); pending_.clear();
                break;
            }
            case State::Count: {
                std::size_t pos = 0;
                count_ = read_le<uint32_t>(pending_, pos);
                fs::create_directories(dest_);
                state_ = (count_ == 0) ? State::Done : State::NameLen;
                need_ = 4; pending_.clear();
                break;
            }
            case State::NameLen: {
                std::size_t pos = 0;
                const auto name_len = read_le<uint32_t>(pending_, pos);
                state_ = State::Name; need_ = name_len; pending_.clear();
                break;
            }
            case State::Name: {
                cur_name_.assign(pending_.data(), pending_.size());
                state_ = State::DataLen; need_ = sizeof(uint64_t); pending_.clear();
                break;
            }
            case State::DataLen: {
                std::size_t pos = 0;
                data_remaining_ = read_le<uint64_t>(pending_, pos);
                pending_.clear();

                fs::path rel(cur_name_);
                validate_relative_path(rel);
                fs::path out_path = dest_ / rel;
                fs::create_directories(out_path.parent_path());
                cur_file_.open(out_path, std::ios::binary | std::ios::trunc);
                if (!cur_file_.is_open()) {
                    throw std::runtime_error("ArchiveUnpacker: cannot open output "
                                             "file: " + out_path.string());
                }
                if (data_remaining_ == 0) {  // empty file: nothing to stream
                    cur_file_.close();
                    ++done_count_;
                    state_ = (done_count_ == count_) ? State::Done : State::NameLen;
                    need_ = 4;
                } else {
                    state_ = State::Data;
                }
                break;
            }
            default:
                break;
        }
    }
}

std::size_t
ArchiveUnpacker::finish() {
    if (state_ != State::Done) {
        throw std::runtime_error("ArchiveUnpacker: archive truncated "
                                 "(incomplete stream)");
    }
    return done_count_;
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
