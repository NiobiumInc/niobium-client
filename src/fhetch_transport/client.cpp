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

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <string>

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
// Returns {scheme_host, path_prefix}. We only care about the origin; any path
// component in the URL is ignored (we always POST to kReplayPath).
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
    std::string request_body;
    try {
        request_body = nft::pack_directory(project_dir, [](const fs::path& rel) {
            return rel.empty() || *rel.begin() != "serialized_probes";
        });
    } catch (const std::exception& e) {
        std::cerr << "[nbcc_fhetch_replay] pack failed: " << e.what() << "\n";
        return 2;
    }

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

    std::cout << "[nbcc_fhetch_replay] POSTing " << request_body.size()
              << " bytes (project=" << project_dir.filename().string()
              << ", target=" << args.target
              << ") → " << host << replay_path << "\n";

    // ---- POST and wait -------------------------------------------------
    httplib::Client cli(host);
    cli.set_read_timeout(60 * 120, 0);  // 2 hr — FUNC_SIM_HW on large workloads can exceed 30 min
    cli.set_write_timeout(60, 0);
    cli.set_connection_timeout(10, 0);

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

    auto res = cli.Post(replay_path, headers,
                        request_body, nft::kArchiveContentType);
    if (!res) {
        std::cerr << "[nbcc_fhetch_replay] HTTP POST failed: "
                  << httplib::to_string(res.error()) << "\n";
        return 3;
    }
    if (res->status != 200) {
        std::cerr << "[nbcc_fhetch_replay] server returned " << res->status
                  << ": " << res->body << "\n";
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
