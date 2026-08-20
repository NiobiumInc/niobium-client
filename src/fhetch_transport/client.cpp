// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// nbcc_fhetch_replay — client-side forwarder for the FHETCH transport.
//
// Interface-compatible drop-in for the compiler-side `nbcc_fhetch_replay`
// from niobium-compiler. Instead of doing the replay locally it bundles
// the fhetch project into a single TLV archive, POSTs it to a
// `nbcc_fhetch_replay_server` instance, and unpacks the returned
// serialized probes back into the project directory — exactly what
// `fhetch::Compiler::replay()` expects to read afterwards.
//
// Usage:
//   nbcc_fhetch_replay --project=<dir> --target=<target>
//
// Config:
//   NBCC_FHETCH_SERVER  base URL of the server (default http://127.0.0.1:9443)

#include "archive.h"
#include "protocol.h"

#include "httplib.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

namespace {

namespace fs = std::filesystem;

struct Args {
    std::string project;
    std::string target;
    std::string opt_level;   // O0..O3; empty → server defaults to O0
};

void print_usage() {
    std::cerr <<
        "Usage: nbcc_fhetch_replay --project=<dir> --target=<target>\n"
        "\n"
        "  --project=<dir>    fhetch project directory (contains fhetch_replay.json).\n"
        "  --target=<target>  Target device id, forwarded verbatim to the server.\n"
        "                     Use FOG to run on Niobium's stable FPGA device: the\n"
        "                     server resolves it to its pinned hardware id, so\n"
        "                     clients never need to know internal device names.\n"
        "  --opt-level=<lvl>  Optimization level (O0..O3) for the compiler-side\n"
        "                     replay. Optional; omitted means the server uses O0.\n"
        "\n"
        "Environment:\n"
        "  NBCC_FHETCH_SERVER  Base URL of the replay server. Default http://127.0.0.1:9443.\n";
}

Args parse(int argc, char** argv) {
    Args out;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a.rfind("--project=", 0) == 0)            out.project = a.substr(10);
        else if (a == "--project" && i + 1 < argc)    out.project = argv[++i];
        else if (a.rfind("--target=", 0) == 0)        out.target  = a.substr(9);
        else if (a == "--target"  && i + 1 < argc)    out.target  = argv[++i];
        else if (a.rfind("--opt-level=", 0) == 0)     out.opt_level = a.substr(12);
        else if (a == "--opt-level" && i + 1 < argc)  out.opt_level = argv[++i];
        else if (a == "-h" || a == "--help") {
            print_usage();
            std::exit(0);
        }
        // Any other arg is ignored — the compiler-side binary is the
        // authoritative driver for those flags; the forwarder doesn't need
        // to understand them.
    }
    return out;
}

// Split "http[s]://host[:port]" into what httplib's Client constructor wants.
// Returns {scheme_host, path_prefix}. The Fog wrapper bakes a per-job path
// (/jobs/<id>/run) into the URL; a bare origin yields an empty path_prefix and
// the caller falls back to kReplayPath.
std::pair<std::string, std::string> origin_of(const std::string& url) {
    auto scheme_sep = url.find("://");
    if (scheme_sep == std::string::npos) {
        // Bare host — assume http.
        return {"http://" + url, ""};
    }
    auto host_start = scheme_sep + 3;
    auto path_start = url.find('/', host_start);
    if (path_start == std::string::npos) {
        return {url, ""};
    }
    return {url.substr(0, path_start), url.substr(path_start)};
}

}  // namespace


// Render an error body from the server.
//
// When the compiler had something to say to the user, the body is its customer
// log: one NDJSON record per line, of which only `msg` is worth showing. We
// deliberately do not link a JSON parser for this -- the records are machine
// generated to a fixed schema, so finding `msg` is a substring away, and any
// line we cannot read is printed verbatim rather than dropped. Bodies that are
// not customer records (a plain daemon error) fall through unchanged.
std::string render_error_body(const std::string& body) {
    const std::string key = "\"msg\":\"";
    std::string out;
    std::size_t pos = 0;

    while (pos < body.size()) {
        std::size_t eol = body.find('\n', pos);
        if (eol == std::string::npos) eol = body.size();
        const std::string line = body.substr(pos, eol - pos);
        pos = eol + 1;
        if (line.empty()) continue;

        const std::size_t k = line.find(key);
        if (k == std::string::npos) {          // not a record we understand
            out += line;
            out += '\n';
            continue;
        }

        // Walk to the closing quote, honouring backslash escapes, and undo the
        // handful of escapes a JSON string can carry.
        std::string msg;
        for (std::size_t i = k + key.size(); i < line.size(); ++i) {
            const char c = line[i];
            if (c == '"') break;
            if (c != '\\') { msg += c; continue; }
            if (++i >= line.size()) break;
            switch (line[i]) {
                case 'n':  msg += '\n'; break;
                case 't':  msg += '\t'; break;
                case 'r':  break;              // drop bare CR
                case 'u':  msg += '?'; i += 4; break;  // non-ASCII: placeholder
                default:   msg += line[i];     // \" \\ \/ and anything else
            }
        }
        out += msg;
        out += '\n';
    }
    return out.empty() ? body : out;
}

int main(int argc, char** argv) {
    namespace nft = niobium::fhetch_transport;

    Args args = parse(argc, argv);
    if (args.project.empty() || args.target.empty()) {
        std::cerr << "[nbcc_fhetch_replay] --project and --target are required\n";
        print_usage();
        return 1;
    }

    fs::path project_dir = fs::absolute(args.project);
    if (!fs::exists(project_dir) || !fs::is_directory(project_dir)) {
        std::cerr << "[nbcc_fhetch_replay] project not a directory: "
                  << project_dir << "\n";
        return 1;
    }

    // Resolve the server URL. `local` is an explicit opt-in to the old behavior
    // of exec'ing the compiler binary directly — useful when the server isn't
    // deployed yet but the compiler binary is on PATH. For anything else we
    // treat it as an HTTP origin.
    const char* env = std::getenv(nft::kDefaultServerEnv);
    std::string server_url = env && *env ? env : nft::kDefaultServerAddr;

    // ---- Pack the project ----------------------------------------------
    // serialized_probes/ is the response payload — don't ship whatever stale
    // content the client may already have there.
    std::vector<nft::ArchiveEntry> entries;
    try {
        entries = nft::scan_directory(project_dir, [](const fs::path& rel) {
            return rel.empty() || *rel.begin() != "serialized_probes";
        });
    } catch (const std::exception& e) {
        std::cerr << "[nbcc_fhetch_replay] scan failed: " << e.what() << "\n";
        return 2;
    }
    const std::uint64_t body_len = nft::archive_content_length(entries);

    // POST path: honor a path baked into NBCC_FHETCH_SERVER (the Fog wrapper
    // points it at /jobs/<id>/run); a bare origin keeps the default /replay.
    auto [host, url_path] = origin_of(server_url);
    const std::string replay_path =
        (url_path.empty() || url_path == "/") ? nft::kReplayPath : url_path;

#ifndef CPPHTTPLIB_OPENSSL_SUPPORT
    if (host.rfind("https://", 0) == 0) {
        std::cerr << "[nbcc_fhetch_replay] built without TLS support — cannot POST to "
                  << "an https URL (" << host << "). Rebuild with OpenSSL "
                     "(see src/fhetch_transport/CMakeLists.txt).\n";
        return 3;
    }
#endif

    std::cout << "[nbcc_fhetch_replay] POSTing " << body_len
              << " bytes (streamed, project=" << project_dir.filename().string()
              << ", target=" << args.target
              << ") → " << host << replay_path << "\n";

    // ---- POST and wait -------------------------------------------------
    httplib::Client cli(host);
    cli.set_read_timeout(60 * 120, 0);  // 2 hr — FUNC_SIM_HW on large workloads can exceed 30 min
    cli.set_write_timeout(60, 0);
    cli.set_connection_timeout(10, 0);
    cli.set_payload_max_length(SIZE_MAX); // increase max payload

    httplib::Headers headers = {
        {nft::kTargetHeader,      args.target},
        {nft::kProjectNameHeader, project_dir.filename().string()},
    };
    // Only send the opt-level header when the caller asked for one; absent →
    // the server forwards no -On and the compiler defaults to O0.
    if (!args.opt_level.empty())
        headers.emplace(nft::kOptLevelHeader, args.opt_level);

    // Optional Fog per-job ticket. Absent → no header (local/offline path).
    if (const char* tok = std::getenv(nft::kAuthTokenEnv); tok && *tok)
        headers.emplace("Authorization", std::string("Bearer ") + tok);

    // Stream the archive straight to the socket (chunked transfer encoding) so
    // the whole trace never sits in RAM — the archive dominates forwarder
    // memory. stream_archive() emits everything in one provider invocation,
    // then we signal completion via sink.done().
    //
    // Progress: the emit lambda sees every byte on its way to the socket, so a
    // running total against body_len is all it takes — no need for httplib's
    // UploadProgress (which would mean giving up the unknown-length overload).
    // ponytail: reprint only when the whole percent changes; stream_archive
    // emits at 1 MiB granularity, so per-write printing would be ~1 line/MiB.
    std::uint64_t sent = 0;
    int last_pct = -1;
    auto res = cli.Post(
        replay_path, headers,
        [&](std::size_t /*offset*/, httplib::DataSink& sink) -> bool {
            try {
                if (!nft::stream_archive(entries,
                        [&](const char* d, std::size_t n) {
                            if (!sink.write(d, n)) return false;
                            sent += n;
                            const int pct = body_len
                                ? static_cast<int>(sent * 100 / body_len) : 100;
                            if (pct != last_pct) {
                                last_pct = pct;
                                std::fprintf(stderr,
                                    "\r[fog] upload %llu/%llu MB (%d%%)",
                                    static_cast<unsigned long long>(sent >> 20),
                                    static_cast<unsigned long long>(body_len >> 20),
                                    pct);
                            }
                            return true;
                        })) {
                    std::fprintf(stderr, "\n");
                    return false;  // sink closed
                }
                sink.done();
                // Last output before the Post() call blocks on the response —
                // say what we're waiting on so a long replay doesn't look hung.
                std::fprintf(stderr,
                    "\n[fog] upload complete — replaying on target %s, "
                    "waiting for the server\n", args.target.c_str());
                return true;
            } catch (const std::exception& e) {
                std::cerr << "\n[nbcc_fhetch_replay] stream failed: "
                          << e.what() << "\n";
                return false;
            }
        },
        nft::kArchiveContentType);
    if (!res) {
        std::cerr << "[nbcc_fhetch_replay] HTTP POST failed: "
                  << httplib::to_string(res.error()) << "\n";
        return 3;
    }
    if (res->status != 200) {
        std::cerr << "[nbcc_fhetch_replay] server returned " << res->status
                  << ":\n" << render_error_body(res->body);
        return 4;
    }

    // ---- Unpack probes -------------------------------------------------
    fs::path probes_dir = project_dir / "serialized_probes";
    try {
        fs::remove_all(probes_dir);
        auto n = nft::unpack_into(res->body, probes_dir);
        std::cout << "[nbcc_fhetch_replay] unpacked " << n
                  << " probe file(s) into " << probes_dir << "\n";
        if (n == 0) {
            std::cerr << "[nbcc_fhetch_replay] server returned an empty "
                         "archive — nothing for result() to consume\n";
            return 5;
        }
    } catch (const std::exception& e) {
        std::cerr << "[nbcc_fhetch_replay] unpack failed: " << e.what() << "\n";
        return 6;
    }

    return 0;
}
