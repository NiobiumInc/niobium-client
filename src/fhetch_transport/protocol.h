// Copyright 2024-present Niobium Microsystems, Inc.
// Licensed under the Apache License, Version 2.0.
//
// Shared constants for the Niobium FHETCH transport.
//
// The client-side `nbcc_fhetch_replay` executable (client.cpp in this
// directory) ships a fhetch project to `nbcc_fhetch_replay_server`
// (server.cpp) over HTTP. The server extracts it, hands it off to the
// compiler-side `nbcc_fhetch_replay`, and streams the resulting
// serialized probes back.
//
// Everything declared here is protocol surface shared by both sides —
// keep it minimal.

#pragma once

#include <cstddef>

namespace niobium::fhetch_transport {

// ---- HTTP endpoint -----------------------------------------------------
constexpr const char* kReplayPath = "/replay";

// Content type the body-archive uses (request + response).
constexpr const char* kArchiveContentType = "application/x-niobium-archive";

// ---- Customer log ------------------------------------------------------
// The compiler cannot answer the user directly: it runs behind this daemon,
// and the daemon does not forward its stdout/stderr, which carry internal
// logging and can name server-side paths (see --return-logs, off by default).
// So a compile that fails for a reason the *user* must act on -- "your client
// is too old for this compiler" -- would otherwise surface as a bare 500.
//
// The compiler keeps a separate, curated log of messages written for the user.
// It is generated under rules that keep it free of internal identifiers and
// paths, which is exactly why it can be returned when the internal log cannot.
// The compiler writes it into the project directory under this name.
//
// One NDJSON record per line: {ts, v, level, phase, msg}. Relaying it needs no
// JSON parser -- the daemon returns the bytes and the client prints the `msg`
// of each line, falling back to the whole line if it cannot find one.
constexpr const char* kCustomerLogFile = "niobium_customer.ndjson";

// Cap on how much of that log is returned. A failing run can produce many
// records; the tail is the part that explains the failure.
constexpr std::size_t kCustomerLogMaxBytes = 64 * 1024;

// ---- Headers -----------------------------------------------------------
// Target device id (FUNC_SIM, fpga5.2, …). Required on the request.
constexpr const char* kTargetHeader = "X-Target";

// Basename of the fhetch project directory (e.g. "mult_server_workload_ckks_mult").
// The server uses this to name the temp working tree and to scope error logs.
constexpr const char* kProjectNameHeader = "X-Project-Name";

// Optimization level for the compiler-side replay ("O0".."O3"). Optional; when
// absent the server forwards no -On and the compiler defaults to O0. The server
// turns this into a native -O<n> flag on the nbcc_fhetch_replay command line.
constexpr const char* kOptLevelHeader = "X-Opt-Level";

// Optional opaque job id. When set (and the server has a timing root
// configured, see kTimingRootEnv), the server points the compiler's
// NB_TIMING_SUMMARY_DIR at <root>/<job-id> so its per-run telemetry
// (timing_summary.json, replay.json, …) lands in a job-scoped directory the
// caller can associate with the job. The caller supplies only the id — never a
// path — so it can't steer the compiler's writes outside the server's root.
// Absent → NB_TIMING_SUMMARY_DIR is left untouched (direct/local path unchanged).
constexpr const char* kJobIdHeader = "X-Job-Id";

// ---- Defaults ----------------------------------------------------------
constexpr const char* kDefaultServerEnv  = "NBCC_FHETCH_SERVER";
constexpr const char* kDefaultServerAddr = "http://127.0.0.1:9443";
constexpr int         kDefaultPort       = 9443;

// Optional bearer token (the Fog per-job ticket). When set, the client sends
// it as `Authorization: Bearer <token>`. Absent → no auth header (the existing
// local/offline direct-POST path is unchanged). Normally exported by the Fog
// job wrapper (`fog submit`, scripts/fog) alongside NBCC_FHETCH_SERVER.
constexpr const char* kAuthTokenEnv = "NBCC_FHETCH_TOKEN";

// Server-side override of the compiler binary it will exec.
// Default is "nbcc_fhetch_replay" (resolved via PATH).
constexpr const char* kServerCompilerBinEnv = "NBCC_FHETCH_COMPILER_BIN";

// Server-side root under which per-job timing dirs are placed. When set and a
// request carries kJobIdHeader, the server exports
// NB_TIMING_SUMMARY_DIR=<root>/<job-id> to the compiler. Empty/unset → the
// timing-dir feature is off and the env var is left untouched.
constexpr const char* kTimingRootEnv = "NBCC_FHETCH_TIMING_ROOT";

}  // namespace niobium::fhetch_transport
