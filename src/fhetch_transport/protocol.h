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

namespace niobium::fhetch_transport {

// ---- HTTP endpoint -----------------------------------------------------
constexpr const char* kReplayPath = "/replay";

// Content type the body-archive uses (request + response).
constexpr const char* kArchiveContentType = "application/x-niobium-archive";

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

// ---- Defaults ----------------------------------------------------------
constexpr const char* kDefaultServerEnv  = "NBCC_FHETCH_SERVER";
constexpr const char* kDefaultServerAddr = "http://127.0.0.1:9443";
constexpr int         kDefaultPort       = 9443;

// Server-side override of the compiler binary it will exec.
// Default is "nbcc_fhetch_replay" (resolved via PATH).
constexpr const char* kServerCompilerBinEnv = "NBCC_FHETCH_COMPILER_BIN";

}  // namespace niobium::fhetch_transport
