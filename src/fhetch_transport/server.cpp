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

#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <limits>
#include <fstream>
#include <iterator>
#include <mutex>
#include <sstream>
#include <string>
#include <system_error>
#include <vector>

#include <spawn.h>     // posix_spawnp — replaces popen()/system()
#include <sys/wait.h>  // waitpid, WIFEXITED/WEXITSTATUS
#include <unistd.h>    // mkdtemp (macOS/BSD), pipe, read, close

// NOLINTNEXTLINE(cppcoreguidelines-avoid-non-const-global-variables)
// -- POSIX defines environ as char**; it cannot be const-qualified and still be
//    the thing posix_spawnp() accepts. Not ours to change.
extern char** environ;

namespace {

namespace fs  = std::filesystem;
namespace nft = niobium::fhetch_transport;

struct ServerArgs {
    std::string bind = "0.0.0.0";
    int         port = nft::kDefaultPort;
    std::string compiler_bin;
    std::string timing_root;  // NBCC_FHETCH_TIMING_ROOT; empty → feature off
    // Include compiler/unpacker output in HTTP error bodies. Off by default so
    // the daemon does not hand server-side paths and internals to whoever can
    // reach the port; a server-side flag, never settable from a request.
    bool        return_logs = false;
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
        "                      then to \"nbcc_fhetch_replay\" on PATH.\n"
        "  --return-logs       Include compiler/unpacker output in HTTP error\n"
        "                      bodies. Off by default: those messages disclose\n"
        "                      server-side paths. Intended for CI and local\n"
        "                      debugging, not for a shared deployment.\n";
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
        else if (a == "--return-logs")                out.return_logs = true;
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

// The target is stricter than is_safe_cli_token() because it does not stay a
// CLI token: the compiler resolves it as a *path component* (devices/<target>/
// spec.yaml). is_safe_cli_token() permits '/' and does not reject "..", so a
// value that is perfectly safe to hand to a shell can still walk out of the
// devices directory and load an attacker-chosen spec.yaml. Restrict to 
// letters, digits, '_' and '.' and ban any ".." sequence or leading/trailing
// dot
bool is_safe_target(const std::string& s) {
    if (s.empty() || s.size() > 64) return false;
    if (s.front() == '.' || s.back() == '.') return false;
    if (s.find("..") != std::string::npos) return false;
    for (char c : s) {
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') || c == '_' || c == '.')
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

// Read the tail of the compiler's customer log, if it wrote one.
//
// Returned to the client even though --return-logs is off: unlike the captured
// stdout/stderr, this log is curated for the user and carries no internal
// identifiers or paths (see kCustomerLogFile). Empty string when there is
// nothing to relay, leaving the caller's generic report in place.
std::string read_customer_log(const std::string& tempdir) {
    namespace fs = std::filesystem;
    std::error_code ec;
    const fs::path path = fs::path(tempdir) / nft::kCustomerLogFile;

    const auto size = fs::file_size(path, ec);
    if (ec) return {};                       // absent or unreadable

    std::ifstream in(path, std::ios::binary);
    if (!in) return {};

    // Take the tail, then drop the partial first line so every line returned
    // is a whole record.
    if (size > nft::kCustomerLogMaxBytes) {
        in.seekg(static_cast<std::streamoff>(size - nft::kCustomerLogMaxBytes));
        std::string discard;
        std::getline(in, discard);
    }
    std::string out((std::istreambuf_iterator<char>(in)),
                    std::istreambuf_iterator<char>());
    return out;
}

// Sentinel: the child could not be started at all (bad path, no exec perm, out
// of fds). Distinct from any status a started child can report.
constexpr int kSpawnFailed = -1;

// Run `bin` with `args` as argv[1..], capturing the child's stdout+stderr into
// `log` (may be null to discard). Returns the child's exit status, 128+signal
// if it was killed, or kSpawnFailed.
//
// No shell is involved. posix_spawnp() receives an explicit argv array, so a
// value that would be a metacharacter to `sh` is inert here: it arrives at the
// child as one literal argument. That removes quoting/injection concerns for 
// every value on this command line
//
// `extra_env` holds "KEY=VALUE" assignments layered over the parent
// environment, replacing any inherited assignment of the same key. argv and
// envp are both built in the parent because posix_spawn() gives us no window to
// allocate in the child — and the request threads make post-fork malloc unsafe.
int run_capture(const std::string& bin,
                const std::vector<std::string>& args,
                const std::vector<std::string>& extra_env,
                std::string* log) {
    int fds[2];
    if (::pipe(fds) != 0) {
        return kSpawnFailed;
    }


    // posix_spawnp() wants char*, so keep our own mutable copies and hand out
    // data() rather than casting away const on the caller's strings. Pointers
    // are taken only once argv_storage has stopped growing.
    std::vector<std::string> argv_storage;
    argv_storage.reserve(args.size() + 1);
    argv_storage.push_back(bin);
    for (const auto& arg : args) {
        argv_storage.push_back(arg);
    }
    std::vector<char*> argv;
    argv.reserve(argv_storage.size() + 1);
    for (auto& entry : argv_storage) {
        argv.push_back(entry.data());
    }
    argv.push_back(nullptr);

    // Environment: inherit the parent's unchanged in the common case. Only when
    // a caller asks for an extra assignment do we build a private copy for this
    // child.
    //
    // do NOT do: setenv() in the server process. mutating our own environment
    // would race between concurrent requests and could route one job's telemetry
    // into another job's directory. Per-child envp has no such coupling.
    char** envp = environ;
    std::vector<std::string> env_storage;  // outlives the spawn below
    std::vector<char*>       env_ptrs;
    if (!extra_env.empty()) {
        for (char** ep = environ; ep != nullptr && *ep != nullptr; ++ep) {
            const std::string inherited(*ep);
            // Drop an inherited assignment that extra_env is about to replace.
            const bool overridden = std::any_of(
                extra_env.begin(), extra_env.end(),
                [&inherited](const std::string& assignment) {
                    const auto eq = assignment.find('=');
                    return eq != std::string::npos &&
                           inherited.compare(0, eq + 1, assignment, 0, eq + 1) == 0;
                });
            if (!overridden) {
                env_storage.push_back(inherited);
            }
        }
        for (const auto& assignment : extra_env) {
            env_storage.push_back(assignment);
        }
        // Pointers are taken only after env_storage stops growing.
        env_ptrs.reserve(env_storage.size() + 1);
        for (auto& entry : env_storage) {
            env_ptrs.push_back(entry.data());
        }
        env_ptrs.push_back(nullptr);
        envp = env_ptrs.data();
    }

    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_addclose(&actions, fds[0]);
    posix_spawn_file_actions_adddup2(&actions, fds[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, fds[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&actions, fds[1]);

    pid_t pid = 0;
    const int spawn_rc = ::posix_spawnp(&pid, bin.c_str(), &actions, nullptr,
                                        argv.data(), envp);
    posix_spawn_file_actions_destroy(&actions);

    // The parent must drop the write end or the read below never sees EOF.
    ::close(fds[1]);
    if (spawn_rc != 0) {
        ::close(fds[0]);
        return kSpawnFailed;
    }

    char buf[4096];
    while (true) {
        const ssize_t n = ::read(fds[0], buf, sizeof(buf));
        if (n > 0) {
            if (log != nullptr) {
                log->append(buf, static_cast<std::size_t>(n));
            }
            continue;
        }
        if (n < 0 && errno == EINTR) {
            continue;
        }
        break;
    }
    ::close(fds[0]);

    int status = 0;
    while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {
        // retry
    }
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return kSpawnFailed;
}

struct Handler {
    std::string compiler_bin;
    std::string timing_root;  // empty → per-job timing dir disabled
    bool        return_logs = false;  // --return-logs; see ServerArgs

    // Compose an error body for the client. `summary` is always safe to send;
    // `detail` (a compiler log, or an unpacker message carrying our temp path)
    // is appended only when the operator opted in with --return-logs. Logs
    // contain server internal info and should not be passed to the client

    std::string detail_for_client(const std::string& summary,
                                  const std::string& detail) const {
        if (!return_logs) {
            return summary + "\n(server log withheld; run the server with "
                             "--return-logs to include it)\n";
        }
        return summary + "\n---log---\n" + detail;
    }

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
        if (!is_safe_target(target)) {
            reject(400, "header " + std::string(nft::kTargetHeader) +
                        " must match [A-Za-z0-9_.]+ (no '..', no leading/"
                        "trailing '.')\n");
            return;
        }
        if (!project.empty() && !is_safe_cli_token(project)) {
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
            // ArchiveUnpacker embeds the output path in its errors, so what()
            // discloses our mkdtemp directory — gate it like any other detail.
            std::cerr << "[nbcc_fhetch_replay_server] archive unpack failed: "
                      << e.what() << "\n";
            res.status = 400;
            res.set_content(detail_for_client("archive unpack failed", e.what()),
                            "text/plain");
            // Drop the partially-extracted tree: leaving it behind both grows
            // unboundedly and preserves attacker-planted files at a path the
            // error above would otherwise have just disclosed.
            if (!tempdir.empty()) {
                std::error_code ec; fs::remove_all(tempdir, ec);
            }
            return;
        }

        // ---- Invoke the compiler binary -----------------------------
        // argv is assembled as a vector, not a shell string: see run_capture().
        std::vector<std::string> args{"--project=" + tempdir,
                                      "--target=" + target};
        if (!opt_flag.empty()) args.push_back(opt_flag);  // pre-validated -O<n>

        // Optional per-job timing dir → NB_TIMING_SUMMARY_DIR for the compiler.
        // Path is <root>/<job-id>, both server-controlled (root from env, job id
        // validated to [A-Za-z0-9-]+). Create it so the compiler can write; the
        // caller (Fog worker) collects and removes it after the run.
        std::vector<std::string> extra_env;
        if (!timing_dir.empty()) {
            std::error_code ec;
            fs::create_directories(timing_dir, ec);
            if (ec) {
                std::cerr << "[nbcc_fhetch_replay_server] could not create timing dir "
                          << timing_dir << ": " << ec.message() << " (skipping)\n";
            } else {
                extra_env.push_back("NB_TIMING_SUMMARY_DIR=" + timing_dir);
            }
        }

        std::string log;
        const int exit_code = run_capture(compiler_bin, args, extra_env, &log);

        // The log always goes to the server's own stdout; whether any of it
        // reaches the client is the operator's call (--return-logs).
        std::cout << log;

        if (exit_code == kSpawnFailed) {
            res.status = 500;
            res.set_content("could not spawn compiler binary\n", "text/plain");
            std::error_code ec; fs::remove_all(tempdir, ec);
            return;
        }

        if (exit_code != 0) {
            // Prefer what the compiler wrote for the user. Read before the
            // tempdir goes away below.
            const std::string customer = read_customer_log(tempdir);
            res.status = 500;
            res.set_content(!customer.empty()
                                ? customer
                                : detail_for_client(
                                      "nbcc_fhetch_replay exited " +
                                          std::to_string(exit_code),
                                      log),
                            "text/plain");
            std::error_code ec; fs::remove_all(tempdir, ec);
            return;
        }

        // ---- Pack serialized_probes/ and return ---------------------
        fs::path probes = fs::path(tempdir) / "serialized_probes";
        if (!fs::exists(probes)) {
            res.status = 500;
            res.set_content(detail_for_client(
                                "compiler succeeded but wrote no serialized_probes/",
                                log),
                            "text/plain");
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
    srv.set_write_timeout(60 * 120, 0); // 2 hr - match read/write
    std::signal(SIGINT,  shutdown_handler);
    std::signal(SIGTERM, shutdown_handler);

    Handler handler{args.compiler_bin, args.timing_root, args.return_logs};
    srv.Post(nft::kReplayPath,
             [&handler](const httplib::Request& req, httplib::Response& res,
                        const httplib::ContentReader& content_reader) {
                 handler(req, res, content_reader);
             });

    srv.Get("/healthz",
            [](const httplib::Request&, httplib::Response& res) {
                res.set_content("ok\n", "text/plain");
            });

    // Pre-flight: resolve the compiler binary exactly the way the request path
    // will, and fail loud at startup if it's not callable. Much better than
    // handing every incoming replay an opaque 500. Reusing run_capture() keeps
    // PATH lookup identical to the actual invocation site; a kSpawnFailed here
    // is precisely "could not be executed" (what `sh` used to report as 127).
    // The child's own exit status is ignored — some binaries exit non-zero on
    // --help, and that was never what this check was about.
    {
        const int rc = run_capture(args.compiler_bin, {"--help"}, {}, nullptr);
        if (rc == kSpawnFailed) {
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
