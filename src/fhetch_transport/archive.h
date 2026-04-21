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

#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace niobium::fhetch_transport {

// Pack every regular file under `root` whose relative path passes `filter`
// (default: accept all) into a single in-memory archive. The buffer is
// returned by value; caller is free to hand it straight to cpp-httplib.
//
// Throws std::runtime_error on unreadable files or malformed input.
std::string
pack_directory(const std::filesystem::path& root,
               const std::function<bool(const std::filesystem::path& rel)>&
                   filter = {});

// Reverse of pack_directory(). Writes each archived entry to
// `dest / entry_name`, creating parent directories as needed. Overwrites
// existing files without asking. Throws on malformed archives or on any
// path that would escape `dest`.
//
// Returns the number of files extracted.
std::size_t
unpack_into(const std::string& archive,
            const std::filesystem::path& dest);

}  // namespace niobium::fhetch_transport
