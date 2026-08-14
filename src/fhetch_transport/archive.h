// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Dead-simple TLV archive format used by the FHETCH transport.
//
// One archive is one contiguous byte buffer:
//
//   magic(4)   = "NBAR"
//   file_count(u32, little-endian)
//   repeat file_count times:
//       name_len(u32)  name(bytes)
//       data_len(u64)  data(bytes)
//
// Paths are stored verbatim relative to a base directory chosen by the
// writer. The reader is responsible for refusing any path that contains
// "..", starts with "/", or otherwise escapes its own extraction root.
//
// This format exists because tar/zip would drag in a dependency and we
// only need to round-trip a flat tree of small-to-medium files between
// two trusted hosts on the same deployment.

#pragma once

#include <openssl/evp.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace niobium::fhetch_transport {

// Optional trailing entry carrying one SHA-1 per archived artifact, in
// `sha1sum` format ("<40 hex>  <name>\n") so `sha1sum -c` verifies an
// extracted tree. Name is charset-safe for the ingress check on unpack.
constexpr const char* kSha1ManifestName = "nbar_manifest.sha1";
constexpr std::size_t kSha1HexLen       = 40;

// Thrown when the trailing manifest disagrees with the bytes that arrived.
// Distinct from a malformed-archive error because it is *actionable by the
// caller* (re-ship the artifacts), and because its message names only
// client-supplied artifact names and digests — never a server-side path — so a
// daemon may return it verbatim without disclosing anything.
struct IntegrityError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// Incremental SHA-1 over an artifact's bytes, via OpenSSL's EVP interface.
// libcrypto is a hard dependency of this library for exactly this reason: its
// SHA-1 runs on the CPU's crypto extensions (ARMv8 SHA1C/SHA1P, x86 SHA-NI),
// which is several times what portable C reaches, and it is the audited
// implementation rather than one we maintain.
//
// Owns an EVP context, so it is non-copyable; reuse one across artifacts with
// reset() rather than reconstructing. Every EVP call is checked — a digest
// that silently failed would defeat the integrity gate it feeds.
class Sha1 {
  public:
    Sha1();
    ~Sha1();
    Sha1(const Sha1&) = delete;
    Sha1& operator=(const Sha1&) = delete;

    void reset();                                  // begin a fresh digest
    void update(const void* data, std::size_t len);
    std::string hex();  // finalize + hex-encode; one call per reset()

  private:
    EVP_MD_CTX* ctx_;
};

// Pack every regular file under `root` whose relative path passes `filter`
// (default: accept all) into a single in-memory archive. The buffer is
// returned by value; caller is free to hand it straight to cpp-httplib.
//
// `sha1_manifest` appends the trailing per-artifact fingerprint entry (see
// add_sha1_manifest). Off by default: it belongs on a job's *request*, not on
// the probe archive a server sends back.
//
// Throws std::runtime_error on unreadable files or malformed input.
std::string
pack_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>&
                   filter = {},
               bool sha1_manifest = false);

// Reverse of pack_directory(). Writes each archived entry to
// `dest / entry_name`, creating parent directories as needed. Overwrites
// existing files without asking. Throws on malformed archives or on any
// path that would escape `dest`.
//
// Note: unlike ArchiveUnpacker this does NOT check a SHA-1 manifest — it only
// ever consumes the probe archive a server sends back, which carries none. Use
// ArchiveUnpacker for anything arriving as a job request.
//
// Returns the number of files extracted.
std::size_t
unpack_into(const std::string& archive,
            const std::filesystem::path& dest);

// --- Streaming pack API (memory-bounded; used by the transport forwarder) ---
// The buffered pack_directory() above materializes the whole archive in RAM,
// which is the dominant memory cost for large trace archives. The forwarder
// instead scans the tree once, then streams the archive straight to the socket
// without ever holding it whole. The wire bytes are identical to
// pack_directory(), so a streaming producer is fully compatible with a buffered
// (unpack_into) consumer.

struct ArchiveEntry {
    std::filesystem::path abs;   // absolute path to the source file
    std::string           rel;   // archived (relative) name, generic form
    std::uint64_t         size;  // file size in bytes at scan time
};

// First pass: enumerate the regular files under `root` that pass `filter`,
// validating each relative path. Mirrors pack_directory()'s scan.
std::vector<ArchiveEntry>
scan_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>&
                   filter = {});

// Append the trailing SHA-1 manifest entry (kSha1ManifestName) to `entries`.
// Call once, after scan_directory(). Digests are fixed width, so the entry's
// byte size is exact and known *now* — archive_content_length() stays correct
// while stream_archive() fills the digests in as it reads. That is what keeps
// this to a single pass over the (multi-GB) key material.
void
add_sha1_manifest(std::vector<ArchiveEntry>& entries);

// Exact byte length of the archive stream_archive()/pack_directory() would
// produce for `entries` (magic + count + per-entry framing + file data).
std::uint64_t
archive_content_length(const std::vector<ArchiveEntry>& entries);

// Emit the archive for `entries` incrementally: `emit(ptr, len)` is called with
// successive byte spans (header, then per entry its framing followed by the
// file's bytes read in bounded chunks). Returns false as soon as `emit` returns
// false (e.g. the socket sink closed). Throws on unreadable/short files.
//
// `manifest_out`, when given, receives the same "<sha1>  <name>\n" lines that
// the trailing manifest entry carries — the caller's copy of the provenance.
bool
stream_archive(const std::vector<ArchiveEntry>& entries,
               const std::function<bool(const char* data, std::size_t len)>&
                   emit,
               std::string* manifest_out = nullptr);

// --- Streaming unpack (memory-bounded; used by the transport server) --------
// Incremental, chunk-boundary-agnostic counterpart to unpack_into(). feed() the
// archive bytes as they arrive (e.g. from an HTTP ContentReader); each file is
// written to disk on the fly, so the whole archive never sits in RAM. Any TLV
// field may be split across feed() calls. Consumes the same wire format as
// pack_directory()/stream_archive(), so it interoperates with either producer.
class ArchiveUnpacker {
  public:
    explicit ArchiveUnpacker(std::filesystem::path dest);

    // Consume one span of archive bytes. Throws on malformed input, an unsafe
    // path, or an I/O error (writing each file to `dest / name`).
    void feed(const char* data, std::size_t len);

    // Verify the stream ended exactly on an entry boundary, and — when the
    // archive carried a kSha1ManifestName entry — that every artifact's bytes
    // hash to what the manifest claimed. Returns the number of files written.
    // Throws if the archive was truncated mid-entry or any digest disagrees.
    std::size_t finish();

  private:
    enum class State { Magic, Count, NameLen, Name, DataLen, Data, Done };
    void finish_entry();
    void verify_manifest() const;

    std::filesystem::path dest_;
    State         state_ = State::Magic;
    std::string   pending_;             // accumulates the current fixed/name field
    std::size_t   need_ = 4;            // bytes required to complete it (magic = 4)
    std::uint32_t count_ = 0;           // total entries declared in the header
    std::uint32_t done_count_ = 0;      // entries fully written so far
    std::string   cur_name_;            // current entry's archived name
    std::uint64_t data_remaining_ = 0;  // bytes left to write for current entry
    std::ofstream cur_file_;            // current entry's open output stream
    Sha1          cur_sha_;             // digest of the current entry's bytes
    std::map<std::string, std::string> observed_;  // name -> recomputed digest
    std::string   manifest_;            // the manifest entry's bytes, if present
    bool          manifest_seen_ = false;
};

}  // namespace niobium::fhetch_transport
