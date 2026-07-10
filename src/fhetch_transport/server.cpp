// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// nbcc_fhetch_replay_server — server-side daemon for the FHETCH transport.
//
// Listens on an HTTP port, accepts POST /replay requests containing a
// packed fhetch project plus an X-Target header, runs the compiler's
// `nbcc_fhetch_replay` executable against a temp copy of the project,
// and returns the resulting serialized_probes/ as a packed archive.
//
// Usage:
//   nbcc_fhetch_replay_server [--port N] [--bind addr]
//                             [--exec /path/to/nbcc_fhetch_replay]
//
// Environment (fallbacks for --exec):
//   NBCC_FHETCH_COMPILER_BIN  Absolute path to the compiler binary.
//                             Default: "nbcc_fhetch_replay" (looked up on PATH).

#include "archive.h"
#include "protocol.h"

#include "httplib.h"

#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <limits>
#include <mutex>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

#include <unistd.h>  // mkdtemp (macOS/BSD), getpid

namespace {

namespace fs  = std::filesystem;
namespace nft = niobium::fhetch_transport;

struct ServerArgs {
    std::string bind = "0.0.0.0";
    int         port = nft::kDefaultPort;
    std::string compiler_bin;
    std::string timing_root;  // NBCC_FHETCH_TIMING_ROOT; empty → feature off
};

void print_usage() {
    std::cerr <<
        "Usage: nbcc_fhetch_replay_server [--port N] [--bind addr]\n"
        "                                 [--exec /path/to/nbcc_fhetch_replay]\n"
        "\n"
        "  --port N            TCP port to listen on (default 9443).\n"
        "  --bind addr         Bind address (default 0.0.0.0).\n"
        "  --exec PATH         Compiler binary to invoke per request.\n"
        "                      Falls back to $NBCC_FHETCH_COMPILER_BIN,\n"
        "                      then to \"nbcc_fhetch_replay\" on PATH.\n";
}

ServerArgs parse(int argc, char** argv) {
    ServerArgs out;
    if (const char* env = std::getenv(nft::kServerCompilerBinEnv)) {
        if (*env) out.compiler_bin = env;
    }
    if (const char* env = std::getenv(nft::kTimingRootEnv)) {
        if (*env) out.timing_root = env;
    }
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a.rfind("--port=", 0) == 0)               out.port = std::atoi(a.c_str() + 7);
        else if (a == "--port" && i + 1 < argc)       out.port = std::atoi(argv[++i]);
        else if (a.rfind("--bind=", 0) == 0)          out.bind = a.substr(7);
        else if (a == "--bind" && i + 1 < argc)       out.bind = argv[++i];
        else if (a.rfind("--exec=", 0) == 0)          out.compiler_bin = a.substr(7);
        else if (a == "--exec" && i + 1 < argc)       out.compiler_bin = argv[++i];
        else if (a == "-h" || a == "--help") {
            print_usage();
            std::exit(0);
        } else {
            std::cerr << "nbcc_fhetch_replay_server: unknown arg '" << a << "'\n";
            print_usage();
            std::exit(2);
        }
    }
    if (out.compiler_bin.empty()) out.compiler_bin = "nbcc_fhetch_replay";
    return out;
}

// Shell-escape a path for a /bin/sh command line. The server assembles
// "<bin> --project=... --target=..." with values that come from headers /
// project names. Keep the surface strict: only [A-Za-z0-9_.+=/,:-] pass
// through unquoted; anything else triggers a rejection upstream in the
// handler. This matches what we actually want to allow (device ids,
// relative paths inside a temp dir).
bool is_safe_cli_token(const std::string& s) {
    if (s.empty()) return false;
    for (char c : s) {
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') ||
             c == '_' || c == '.' || c == '/' || c == '-' || c == '=' ||
             c == '+' || c == ':' || c == ',')
            continue;
        return false;
    }
    return true;
}

// A job id becomes a single path segment under the server's timing root, so it
// must be strict: letters, digits, and hyphen only — no '/', '.', or anything
// that could escape the root (a UUID satisfies this). Deliberately narrower
// than is_safe_cli_token, which permits '/' and '.'.
bool is_safe_job_id(const std::string& s) {
    if (s.empty() || s.size() > 128) return false;
    for (char c : s) {
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '-')
            continue;
        return false;
    }
    return true;
}

// Translate an X-Opt-Level header value ("O0".."O3", or bare "0".."3") into the
// native compiler flag "-O0".."-O3". Returns "" if empty or not a valid level,
// so the caller can reject malformed values and fall back to the O0 default.
std::string opt_level_to_flag(const std::string& v) {
    std::string s = v;
    if (!s.empty() && (s[0] == 'O' || s[0] == 'o')) s = s.substr(1);
    if (s.size() == 1 && s[0] >= '0' && s[0] <= '3') return std::string("-O") + s[0];
    return "";
}

std::string unique_tempdir(const std::string& prefix) {
    // mkdtemp() creates the dir atomically with good entropy — no seeding, no
    // retry loop, collision-free across concurrent request threads by design.
    auto tmpl = (fs::temp_directory_path() / (prefix + "_XXXXXX")).string();
    std::vector<char> buf(tmpl.begin(), tmpl.end());
    buf.push_back('\0');
    if (!::mkdtemp(buf.data())) throw std::runtime_error("could not create temp directory");
    return buf.data();
}

struct Handler {
    std::string compiler_bin;
    std::string timing_root;  // empty → per-job timing dir disabled

    void operator()(const httplib::Request& req, httplib::Response& res,
                    const httplib::ContentReader& content_reader) const {
        // On any pre-body validation failure we must still consume the
        // (possibly huge, still-streaming) request body, otherwise the client's
        // in-flight upload sees a broken connection instead of our error status.
        auto reject = [&](int status, const std::string& msg) {
            res.status = status;
            res.set_content(msg, "text/plain");
            content_reader([](const char*, std::size_t) { return true; });
        };

        // ---- Header validation ---------------------------------------
        auto target  = req.get_header_value(nft::kTargetHeader);
        auto project = req.get_header_value(nft::kProjectNameHeader);
        auto opt_in  = req.get_header_value(nft::kOptLevelHeader);
        auto job_id  = req.get_header_value(nft::kJobIdHeader);
        if (target.empty()) {
            reject(400, "missing header " + std::string(nft::kTargetHeader) + "\n");
            return;
        }
        if (!is_safe_cli_token(target) ||
            (!project.empty() && !is_safe_cli_token(project))) {
            reject(400, "header values must match [A-Za-z0-9_.+=/,:-]+\n");
            return;
        }
        if (!job_id.empty() && !is_safe_job_id(job_id)) {
            reject(400, "header " + std::string(nft::kJobIdHeader) +
                        " must match [A-Za-z0-9-]+\n");
            return;
        }

        // Per-job timing dir: derived server-side from the (validated) job id
        // under our own root — the caller never supplies a path. Empty unless
        // both a root is configured and a job id was sent.
        std::string timing_dir;
        if (!timing_root.empty() && !job_id.empty())
            timing_dir = timing_root + "/" + job_id;
        if (project.empty()) project = "niobium_fhetch_project";

        // Optional optimization level → native -O<n>. Absent means O0 (the
        // compiler-side default); a present-but-malformed value is rejected
        // rather than silently ignored.
        std::string opt_flag;
        if (!opt_in.empty()) {
            opt_flag = opt_level_to_flag(opt_in);
            if (opt_flag.empty()) {
                reject(400, "header " + std::string(nft::kOptLevelHeader) +
                            " must be one of O0,O1,O2,O3\n");
                return;
            }
        }

        // ---- Unpack the request (streamed to disk) ------------------
        // Feed the incoming body straight into the incremental unpacker so the
        // whole archive never sits in RAM. feed() throwing on malformed input
        // is caught inside the receiver (returning false stops the read) rather
        // than propagating through cpp-httplib's read loop.
        std::string tempdir;
        try {
            tempdir = unique_tempdir("nbcc_fhetch_server");
            nft::ArchiveUnpacker unpacker(tempdir);
            std::string unpack_err;
            bool read_ok = content_reader(
                [&](const char* data, std::size_t len) {
                    try {
                        unpacker.feed(data, len);
                        return true;
                    } catch (const std::exception& e) {
                        unpack_err = e.what();
                        return false;
                    }
                });
            if (!unpack_err.empty()) throw std::runtime_error(unpack_err);
            if (!read_ok) throw std::runtime_error("failed reading request body");
            auto n = unpacker.finish();
            std::cout << "[nbcc_fhetch_replay_server] unpacked " << n
                      << " files into " << tempdir
                      << " (target=" << target << ")\n";
        } catch (const std::exception& e) {
            res.status = 400;
            res.set_content(std::string("archive unpack failed: ") + e.what() + "\n",
                            "text/plain");
            return;
        }

        // ---- Invoke the compiler binary -----------------------------
        // We build a shell command with pre-validated tokens only and
        // capture stderr alongside stdout so failure diagnostics come
        // back to the client without a second round trip.
        std::ostringstream cmd;
        // Optional per-job timing dir → NB_TIMING_SUMMARY_DIR for the compiler.
        // Path is <root>/<job-id>, both server-controlled (root from env, job id
        // validated to [A-Za-z0-9-]+), so it's safe to inline as a `sh` env
        // assignment. Create it so the compiler can write; the caller (Fog
        // worker) collects and removes it after the run.
        if (!timing_dir.empty()) {
            std::error_code ec;
            fs::create_directories(timing_dir, ec);
            if (ec)
                std::cerr << "[nbcc_fhetch_replay_server] could not create timing dir "
                          << timing_dir << ": " << ec.message() << " (skipping)\n";
            else
                cmd << "NB_TIMING_SUMMARY_DIR=" << timing_dir << " ";
        }
        cmd << compiler_bin
            << " --project=" << tempdir
            << " --target="  << target;
        if (!opt_flag.empty()) cmd << " " << opt_flag;  // pre-validated -O<n>
        cmd << " 2>&1";

        std::string log;
        int exit_code = -1;
        {
            FILE* pipe = ::popen(cmd.str().c_str(), "r");
            if (!pipe) {
                res.status = 500;
                res.set_content("could not spawn compiler binary\n", "text/plain");
                std::error_code ec; fs::remove_all(tempdir, ec);
                return;
            }
            char buf[4096];
            while (std::size_t n = std::fread(buf, 1, sizeof(buf), pipe)) {
                log.append(buf, n);
            }
            exit_code = ::pclose(pipe);
            if (WIFEXITED(exit_code)) exit_code = WEXITSTATUS(exit_code);
        }
        std::cout << log;

        if (exit_code != 0) {
            res.status = 500;
            std::string msg = "nbcc_fhetch_replay exited " +
                              std::to_string(exit_code) + "\n---log---\n" + log;
            res.set_content(msg, "text/plain");
            std::error_code ec; fs::remove_all(tempdir, ec);
            return;
        }

        // ---- Pack serialized_probes/ and return ---------------------
        fs::path probes = fs::path(tempdir) / "serialized_probes";
        if (!fs::exists(probes)) {
            res.status = 500;
            res.set_content("compiler succeeded but wrote no serialized_probes/\n"
                            "---log---\n" + log, "text/plain");
            std::error_code ec; fs::remove_all(tempdir, ec);
            return;
        }

        try {
            auto body = nft::pack_directory(probes);
            res.status = 200;
            res.set_content(std::move(body), nft::kArchiveContentType);
        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(std::string("pack probes failed: ") + e.what() + "\n",
                            "text/plain");
        }

        std::error_code ec; fs::remove_all(tempdir, ec);
    }
};

// ------ Signal shutdown ---------------------------------------------------
// httplib::Server has its own stop() method — we hold a global pointer so
// the signal handler can reach it. This is the minimum machinery needed to
// let `kill -TERM` drain the listener cleanly.
httplib::Server* g_server_ptr = nullptr;
std::mutex       g_server_mu;

void shutdown_handler(int) {
    std::lock_guard<std::mutex> lock(g_server_mu);
    if (g_server_ptr) g_server_ptr->stop();
}

}  // namespace

int main(int argc, char** argv) {
    ServerArgs args = parse(argc, argv);

    httplib::Server srv;
    {
        std::lock_guard<std::mutex> lock(g_server_mu);
        g_server_ptr = &srv;
    }

    // fhetch projects can be very large (the .fhetch trace alone runs to
    // hundreds of MB for real workloads), so lift cpp-httplib's 100MB default
    // payload cap entirely and give the body read plenty of time — otherwise
    // the server closes the socket mid-upload and the client just sees a
    // "Failed to write connection" error.
    srv.set_payload_max_length((std::numeric_limits<size_t>::max)());
    srv.set_read_timeout(60 * 120, 0);  // 2 hr — matches client's read timeout
    std::signal(SIGINT,  shutdown_handler);
    std::signal(SIGTERM, shutdown_handler);

    Handler handler{args.compiler_bin, args.timing_root};
    srv.Post(nft::kReplayPath,
             [&handler](const httplib::Request& req, httplib::Response& res,
                        const httplib::ContentReader& content_reader) {
                 handler(req, res, content_reader);
             });

    srv.Get("/healthz",
            [](const httplib::Request&, httplib::Response& res) {
                res.set_content("ok\n", "text/plain");
            });

    // Pre-flight: resolve the compiler binary exactly the way popen() will,
    // and fail loud at startup if it's not callable. Much better than
    // handing every incoming replay an opaque 500. We let `sh -c "<bin>
    // --help"` do the work so PATH lookup and shebang handling match the
    // actual invocation site. Exit status 127 from sh means "not found".
    {
        std::string probe = args.compiler_bin + " --help >/dev/null 2>&1";
        int rc = std::system(probe.c_str());
        if (WIFEXITED(rc) && WEXITSTATUS(rc) == 127) {
            std::cerr << "[nbcc_fhetch_replay_server] compiler binary not found: '"
                      << args.compiler_bin << "'\n"
                      << "  set NBCC_FHETCH_COMPILER_BIN or pass --exec "
                         "/absolute/path/to/nbcc_fhetch_replay\n";
            return 1;
        }
    }

    std::cout << "[nbcc_fhetch_replay_server] listening on "
              << args.bind << ":" << args.port
              << " (exec=" << args.compiler_bin << ")\n";

    if (!srv.listen(args.bind, args.port)) {
        std::cerr << "[nbcc_fhetch_replay_server] listen() failed\n";
        return 1;
    }
    std::cout << "[nbcc_fhetch_replay_server] shutdown complete\n";
    return 0;
}
