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

// Upper bound on a single entry's name. Real fhetch projects nest a handful of
// levels (epoch_N/serialized_probes/<probe>.ct); 4 KiB is far past anything
// legitimate and keeps a bogus name_len from being buffered before we can
// reject it.
constexpr std::uint32_t kMaxEntryNameLen   = 4096;
constexpr std::size_t   kMaxEntryNameParts = 64;

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

// Ingress-only name check, layered on top of validate_relative_path().
//
// Traversal is not the whole risk for a name we did not produce. Because the
// format stores names verbatim, an archive arriving over the network can create
// files whose names contain spaces, quotes, newlines, `;`, `$(...)`, or a
// leading `-` inside the extraction root. None of that escapes the root, but it
// turns into an injection or an argument-smuggle the moment anything downstream
// globs or shells over the extracted tree. So allowlist the characters real
// fhetch projects actually use and reject everything else.
//
// Deliberately NOT applied to pack_directory(): those names come from probe
// tags that the compiler has already produced, and aborting a completed run
// over a probe name would trade a live-path regression for no security gain.
// The trust asymmetry is the point — this guards bytes off the wire.
void validate_untrusted_entry_name(const std::filesystem::path& rel,
                                   const std::string& raw) {
    auto bad = [&raw](const char* why) {
        return std::runtime_error(std::string("archive entry name rejected (")
                                  + why + "): '" + raw + "'");
    };

    if (raw.size() > kMaxEntryNameLen) {
        throw bad("too long");
    }

    std::size_t parts = 0;
    for (const auto& part : rel) {
        const auto component = part.string();
        if (component == ".") {
            continue;  // harmless "./" noise; not a component
        }
        if (++parts > kMaxEntryNameParts) {
            throw bad("too deeply nested");
        }
        // A leading '-' would be read as an option flag by any tool that later
        // receives this name as an argument.
        if (component[0] == '-') {
            throw bad("component starts with '-'");
        }
        for (unsigned char c : component) {
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                (c >= '0' && c <= '9') ||
                 c == '_' || c == '.' || c == '-' || c == '+' ||
                 c == '=' || c == ',' || c == '@')
                continue;
            throw bad("illegal character");
        }
    }
    if (parts == 0) throw bad("no usable path component");
}

}  // namespace

// ---- SHA-1 (OpenSSL EVP) ---------------------------------------------------

namespace {

// EVP_sha1() returns a static descriptor from the default provider — no fetch,
// no free, no per-call cost. It can still come back null on a build configured
// with a provider that withholds SHA-1, which must be a hard failure here: a
// missing digest would silently disarm the integrity gate.
const EVP_MD* sha1_md() {
    const EVP_MD* md = EVP_sha1();
    if (md == nullptr) {
        throw std::runtime_error("OpenSSL: SHA-1 digest unavailable "
                                 "(provider configuration?)");
    }
    return md;
}

}  // namespace

Sha1::Sha1() : ctx_(EVP_MD_CTX_new()) {
    if (ctx_ == nullptr) {
        throw std::runtime_error("OpenSSL: EVP_MD_CTX_new failed");
    }
    if (EVP_DigestInit_ex(ctx_, sha1_md(), nullptr) != 1) {
        EVP_MD_CTX_free(ctx_);
        throw std::runtime_error("OpenSSL: SHA-1 init failed");
    }
}

Sha1::~Sha1() { EVP_MD_CTX_free(ctx_); }

void Sha1::reset() {
    if (EVP_DigestInit_ex(ctx_, sha1_md(), nullptr) != 1) {
        throw std::runtime_error("OpenSSL: SHA-1 re-init failed");
    }
}

void Sha1::update(const void* data, std::size_t len) {
    if (EVP_DigestUpdate(ctx_, data, len) != 1) {
        throw std::runtime_error("OpenSSL: SHA-1 update failed");
    }
}

std::string Sha1::hex() {
    unsigned char md[EVP_MAX_MD_SIZE];
    unsigned int  n = 0;
    if (EVP_DigestFinal_ex(ctx_, md, &n) != 1 || n * 2 != kSha1HexLen) {
        throw std::runtime_error("OpenSSL: SHA-1 finalize failed");
    }
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(kSha1HexLen);
    for (unsigned int i = 0; i < n; ++i) {
        out.push_back(kHex[md[i] >> 4]);
        out.push_back(kHex[md[i] & 0xF]);
    }
    return out;
}

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

void
add_sha1_manifest(std::vector<ArchiveEntry>& entries) {
    // "<40 hex><2 spaces><name><newline>" per artifact — fixed width but for
    // the name, which we already know, so the total is exact before any file
    // is read. An empty `abs` marks the entry as synthesized by stream_archive.
    std::uint64_t size = 0;
    for (const auto& e : entries) {
        size += kSha1HexLen + 2 + e.rel.size() + 1;
    }
    entries.push_back({{}, kSha1ManifestName, size});
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
               const std::function<bool(const char* data, std::size_t len)>& emit,
               std::string* manifest_out) {
    // Header: magic + entry count.
    std::string hdr;
    hdr.append(kMagic, sizeof(kMagic));
    append_le<uint32_t>(hdr, static_cast<uint32_t>(entries.size()));
    if (!emit(hdr.data(), hdr.size())) return false;

    constexpr std::size_t kChunk = 1u << 20;  // 1 MiB read/emit granularity
    std::vector<char> buf(kChunk);
    std::string manifest;  // "<sha1>  <name>\n" per artifact, in emit order

    for (const auto& e : entries) {
        // Per-entry framing: name_len + name + data_len.
        std::string frame;
        append_le<uint32_t>(frame, static_cast<uint32_t>(e.rel.size()));
        frame.append(e.rel);
        append_le<uint64_t>(frame, e.size);
        if (!emit(frame.data(), frame.size())) return false;

        // The trailing manifest (add_sha1_manifest) has no file behind it: its
        // bytes are the digests accumulated from every entry emitted above.
        if (e.abs.empty()) {
            if (manifest.size() != e.size) {
                throw std::runtime_error(
                    "stream_archive: manifest is " + std::to_string(manifest.size())
                    + " bytes but " + std::to_string(e.size) + " were declared");
            }
            if (!emit(manifest.data(), manifest.size())) return false;
            continue;
        }

        std::ifstream f(e.abs, std::ios::binary);
        if (!f.is_open()) {
            throw std::runtime_error("stream_archive: cannot open "
                                     + e.abs.string());
        }
        Sha1 sha;
        std::uint64_t remaining = e.size;
        while (remaining > 0) {
            const std::size_t n = static_cast<std::size_t>(
                std::min<std::uint64_t>(remaining, buf.size()));
            f.read(buf.data(), static_cast<std::streamsize>(n));
            if (!f) {
                throw std::runtime_error("stream_archive: short read on "
                                         + e.abs.string());
            }
            // Fingerprint on the way to the socket — the bytes are already in
            // hand, so this costs no extra read of the artifact.
            sha.update(buf.data(), n);
            if (!emit(buf.data(), n)) return false;
            remaining -= n;
        }
        manifest += sha.hex();
        manifest += "  ";
        manifest += e.rel;
        manifest += '\n';
    }
    if (manifest_out) *manifest_out = manifest;
    return true;
}

std::string
pack_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>& filter,
               bool sha1_manifest) {
    // Buffered pack: reuse the streaming path, appending every span into one
    // contiguous buffer. Kept for callers (e.g. the small probe-response path)
    // that want the archive materialized.
    auto entries = scan_directory(root, filter);
    if (sha1_manifest) add_sha1_manifest(entries);
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
            cur_sha_.update(data + off, n);
            if (cur_name_ == kSha1ManifestName) manifest_.append(data + off, n);
            off += n;
            data_remaining_ -= n;
            if (data_remaining_ == 0) {
                cur_file_.close();
                if (!cur_file_) {
                    throw std::runtime_error("ArchiveUnpacker: flush failed on '"
                                             + cur_name_ + "'");
                }
                finish_entry();
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
                // Reject before `need_` commits us to buffering that many bytes.
                if (name_len == 0 || name_len > kMaxEntryNameLen) {
                    throw std::runtime_error(
                        "ArchiveUnpacker: implausible entry name length: "
                        + std::to_string(name_len));
                }
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
                validate_untrusted_entry_name(rel, cur_name_);
                fs::path out_path = dest_ / rel;
                fs::create_directories(out_path.parent_path());
                cur_file_.open(out_path, std::ios::binary | std::ios::trunc);
                if (!cur_file_.is_open()) {
                    throw std::runtime_error("ArchiveUnpacker: cannot open output "
                                             "file: " + out_path.string());
                }
                cur_sha_.reset();
                if (data_remaining_ == 0) {  // empty file: nothing to stream
                    cur_file_.close();
                    finish_entry();
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

void
ArchiveUnpacker::finish_entry() {
    if (cur_name_ == kSha1ManifestName) {
        manifest_seen_ = true;
    } else {
        observed_[cur_name_] = cur_sha_.hex();
    }
    ++done_count_;
    state_ = (done_count_ == count_) ? State::Done : State::NameLen;
    need_ = 4;
    pending_.clear();
}

// The integrity gate (§9c of FOG_ARCHITECTURE_CLIENT_PROPOSED): every artifact
// the producer fingerprinted is re-hashed here from the bytes that actually
// arrived, so a swapped or truncated artifact is rejected before anything
// downstream reads the extracted tree. Archives with no manifest (the legacy
// producers) skip the gate entirely — this is additive, never a new refusal
// for a stream that used to work.
void
ArchiveUnpacker::verify_manifest() const {
    std::size_t checked = 0;
    for (std::size_t pos = 0; pos < manifest_.size();) {
        auto eol = manifest_.find('\n', pos);
        if (eol == std::string::npos) eol = manifest_.size();
        const std::string line = manifest_.substr(pos, eol - pos);
        pos = eol + 1;
        if (line.empty()) continue;

        if (line.size() < kSha1HexLen + 3 ||
            line.compare(kSha1HexLen, 2, "  ") != 0) {
            throw IntegrityError("SHA-1 manifest: malformed line");
        }
        const std::string want = line.substr(0, kSha1HexLen);
        const std::string name = line.substr(kSha1HexLen + 2);

        const auto it = observed_.find(name);
        if (it == observed_.end()) {
            throw IntegrityError("SHA-1 manifest lists '" + name
                                     + "' but the archive did not carry it");
        }
        if (it->second != want) {
            throw IntegrityError("SHA-1 mismatch on '" + name + "': manifest "
                                     "claims " + want + ", received bytes hash to "
                                     + it->second);
        }
        ++checked;
    }
    if (checked != observed_.size()) {
        throw IntegrityError("SHA-1 manifest covers " + std::to_string(checked)
                                 + " of " + std::to_string(observed_.size())
                                 + " archived artifacts");
    }
}

std::size_t
ArchiveUnpacker::finish() {
    if (state_ != State::Done) {
        throw std::runtime_error("ArchiveUnpacker: archive truncated "
                                 "(incomplete stream)");
    }
    if (manifest_seen_) verify_manifest();
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
        validate_untrusted_entry_name(rel, name);
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
