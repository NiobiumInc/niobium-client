// Copyright (C) 2023-2026, All rights reserved by Niobium Microsystems.
//
// AutoFacade.cpp — Transparent record/replay facade for unmodified OpenFHE programs.
//
// Implements the niobium_auto::* hook functions declared in niobium_auto_hooks.h.
// Hooks are called from patched OpenFHE headers (ciphertext-ser.h, cryptocontext-ser.h,
// cryptocontext.h) when OPENFHE_CPROBES is defined.
//
// Config is loaded from (first match wins):
//   0. NIOBIUM_CONFIG env var           (explicit path override)
//   1. <exe_stem>.niobium_compiler.yml  (CWD, then exe dir)
//   2. .niobium_compiler.yml            (CWD, then exe dir)

#include "niobium/compiler.h"

#include "ciphertext-ser.h"
#include "cryptocontext-ser.h"
#include "key/key-ser.h"
#include "niobium_auto_scheme.h"
#include "openfhe.h"

#include <yaml-cpp/yaml.h>

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#endif

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

// ---------------------------------------------------------------------------
// Global flags — read by header hooks in cryptocontext.h
// ---------------------------------------------------------------------------
bool g_replay_mode = false;
std::atomic<uint64_t> g_replay_noop_count{0};

// Forward declaration for the loader exposed by libnbfhetch. Declared
// at top-level (outside the niobium_auto namespace) so the symbol
// resolves correctly at link time. Implemented in
// vendor/niobium-fhetch/src/auto_facade.cpp.
namespace niobium { namespace detail {
bool load_plaintext_input_file(const std::string& name);
}}

namespace niobium_auto {

// ---------------------------------------------------------------------------
// unwrap_scheme — strip NiobiumAutoScheme proxy for serialization
// ---------------------------------------------------------------------------
std::shared_ptr<lbcrypto::SchemeBase<lbcrypto::DCRTPoly>> unwrap_scheme(
    const std::shared_ptr<lbcrypto::SchemeBase<lbcrypto::DCRTPoly>> &scheme) {
  auto *proxy = dynamic_cast<lbcrypto::NiobiumAutoScheme *>(scheme.get());
  return proxy ? proxy->GetRealScheme() : scheme;
}

// ---------------------------------------------------------------------------
// Internal state
// ---------------------------------------------------------------------------

// Mode determined by lazy_init: controls what on_deserialize_crypto_context
// does after lazy_init returns, and what Compiler::start() does.
enum class Mode { UNINITIALIZED, DORMANT, RECORD, REPLAY };
static Mode g_mode{Mode::UNINITIALIZED};

static std::atomic<bool> g_initialized{false};
static std::once_flag g_init_once;
static lbcrypto::CryptoContext<lbcrypto::DCRTPoly> g_cc;
static bool g_atexit_registered{false};

// True between start() and stop() — does NOT toggle with pause/resume.
// Used by hooks in cryptocontext.h to guard pause/resume calls.
static bool g_recording{false};

// Auto-incrementing probe counter for Decrypt-time probing
static uint64_t g_probe_counter{0};

// One-shot flag: tag_keys() must fire at the SAME point in the program
// on record and replay so the FHETCH compact-address allocator hands
// key polynomials the same addresses on both runs. Calling it from
// atexit (record) and ensure_replayed (replay) creates an asymmetry —
// in record, Eval ops have already consumed many address slots for
// intermediates by then; in replay, the proxy short-circuits Eval ops
// so the allocator is still empty. Anchoring tag_keys to the first
// on_deserialize_ciphertext hook gives both runs an identical
// allocator state (CC + keys loaded, ciphertexts about to be tagged).
static std::atomic<bool> g_keys_tagged{false};

// Auto-incrementing counter for plaintexts intercepted via on_make_plaintext.
// The program is deterministic so the Nth Make*Plaintext call gets the same
// "pt_<N>" name on record and replay — record's tag_input writes
// <prog>.input_pt_<N>.bin/.ids, and replay's loader reads them back into
// captured_inputs at the same FHETCH addresses.
static uint64_t g_plaintext_counter{0};

// Re-entrancy guard: niobium-fhetch's tag/bootstrap paths may transitively
// trigger Make*Plaintext while we're already in the hook. Without this the
// counter would race.
static thread_local bool g_in_make_plaintext_hook = false;

// Track probed ciphertext pointers -> probe name, to avoid double-probing
// and to let on_decrypt use the same name that on_serialize used.
static std::unordered_map<const void*, std::string> g_probed_cts;

// Thread-local re-entry flag set when the facade itself issues
// OpenFHE deserializations (e.g. Compiler::result loading a
// serialized_probes/<name>.ct). The on_deserialize_ciphertext hook
// consults this to avoid tagging facade-internal I/O as user inputs.
static thread_local bool g_in_facade_io = false;
struct InFacadeIoGuard {
  InFacadeIoGuard() { g_in_facade_io = true; }
  ~InFacadeIoGuard() { g_in_facade_io = false; }
};

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

struct Config {
  std::string name{"auto_facade"};
  std::string version{"1.0"};
  std::string description{"Auto-facade program"};

  niobium::Compiler::CacheParameters cache_params;

  std::string key_eval_mult;
  std::string key_eval_automorphism;

  // Raw compiler section — converted to synthetic argv for init()
  YAML::Node compiler_node;
};

static Config g_config;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::string path_stem(const std::string &filepath) {
  return std::filesystem::path(filepath).stem().string();
}

static std::filesystem::path find_config() {
  if (const char* env = std::getenv("NIOBIUM_CONFIG")) {
    std::filesystem::path p(env);
    if (std::filesystem::exists(p))
      return p;
    std::cerr << "[niobium_auto] Warning: NIOBIUM_CONFIG=" << env
              << " not found, falling back to search\n";
  }
  return {};
}

static void load_config(const std::filesystem::path &path) {
  if (path.empty())
    return;

  try {
    YAML::Node root = YAML::LoadFile(path.string());

    if (auto prog = root["program"]) {
      if (prog["name"])
        g_config.name = prog["name"].as<std::string>();
      if (prog["version"])
        g_config.version = prog["version"].as<std::string>();
      if (prog["description"])
        g_config.description = prog["description"].as<std::string>();
    }

    if (auto cp = root["cache_parameters"]) {
      for (const auto &kv : cp) {
        g_config.cache_params.emplace_back(kv.first.as<std::string>(),
                                           kv.second.as<std::string>());
      }
    }

    if (auto keys = root["keys"]) {
      if (keys["eval_mult"])
        g_config.key_eval_mult = keys["eval_mult"].as<std::string>();
      if (keys["eval_automorphism"])
        g_config.key_eval_automorphism = keys["eval_automorphism"].as<std::string>();
    }

    if (auto comp = root["compiler"])
      g_config.compiler_node = comp;
  } catch (const std::exception &ex) {
    std::cerr << "[niobium_auto] Warning: failed to parse config " << path << ": " << ex.what() << "\n";
  }
}

// ---------------------------------------------------------------------------
// build_init_argv — convert compiler YAML node to synthetic argc/argv for init()
// ---------------------------------------------------------------------------

// niobium-compiler provides a niobium::getExecutablePath() helper; for the
// client we inline a small fallback. argv[0] only needs to be a non-empty
// string — Compiler::init() just stashes it for diagnostics.
static std::string executable_path_or_fallback() {
#if defined(__linux__)
  try {
    return std::filesystem::read_symlink("/proc/self/exe").string();
  } catch (...) {}
#elif defined(__APPLE__)
  char buf[4096];
  uint32_t size = sizeof(buf);
  if (_NSGetExecutablePath(buf, &size) == 0) return std::string(buf);
#endif
  return "niobium_auto_facade";
}

static std::vector<std::string> build_init_argv(const YAML::Node &node) {
  std::vector<std::string> args;
  args.push_back(executable_path_or_fallback());

  if (!node || !node.IsMap())
    return args;

  // Helper to add a flag if a key exists and is truthy
  auto add_bool_flag = [&](const char *key, const char *flag_true,
                           const char *flag_false = nullptr) {
    if (node[key]) {
      if (node[key].as<bool>(false))
        args.push_back(flag_true);
      else if (flag_false)
        args.push_back(flag_false);
    }
  };

  // Helper to add --flag=value if key exists
  auto add_value_flag = [&](const char *key, const char *flag) {
    if (node[key])
      args.push_back(std::string(flag) + "=" + node[key].as<std::string>());
  };

  add_value_flag("target", "--target");
  if (node["optimization"])
    args.push_back("-O" + node["optimization"].as<std::string>());
  add_value_flag("registers", "--registers");
  add_value_flag("memory", "--memory");
  add_bool_flag("niobium_hw", "--niobium_hw");
  add_bool_flag("fence", "--fence", "--no-fence");
  add_value_flag("noop", "--noop");
  add_value_flag("multiplier", "--multiplier");
  add_value_flag("config_sectors", "--config-sectors");
  add_bool_flag("binary_json", "--binary-json", "--ascii-json");
  add_bool_flag("no_cereal_binary", "--no-cereal-binary");
  add_bool_flag("transform_bin_to_json", "--transform-bin-to-json");
  add_bool_flag("no_preserve_input_ciphertexts", "--no-preserve-input-ciphertexts");
  add_bool_flag("formal", "--formal");
  add_bool_flag("lock_timing", "--lock-timing");

  return args;
}

// ---------------------------------------------------------------------------
// atexit handler — calls stop() if recording is still in progress
// ---------------------------------------------------------------------------

static void atexit_handler() {
  if (g_recording) {
    // Swap the NiobiumAutoScheme proxy back to the real scheme before
    // stop() serializes the recording.  The openfhe library's
    // CryptoContextImpl::save is compiled without OPENFHE_CPROBES,
    // so it serializes m_scheme directly — if that points to
    // NiobiumAutoScheme, cereal will fail (unregistered polymorphic type).
    if (g_cc) {
      auto real = unwrap_scheme(g_cc->GetScheme());
      if (real != g_cc->GetScheme())
        g_cc->SetScheme(real);
    }
    // tag_keys() already fired lazily in on_deserialize_ciphertext (so
    // record + replay share an allocator state at key-tag time). Just
    // close out the recording.
    niobium::compiler().stop();
    g_recording = false;
    // Note: do NOT call replay() here. Replay inside the destructor path
    // (atexit) was observed to segfault intermittently when downstream
    // cereal/OpenFHE globals are torn down mid-serialization. Replay runs
    // on the *next* invocation (replay pass) via ensure_replayed(); see
    // below for how captured_outputs is rehydrated from outputs.json.
  }
}

static void register_atexit() {
  if (!g_atexit_registered) {
    std::atexit(atexit_handler);
    g_atexit_registered = true;
  }
}

// ---------------------------------------------------------------------------
// Hook implementations
// ---------------------------------------------------------------------------

void lazy_init(const lbcrypto::CryptoContext<lbcrypto::DCRTPoly> &cc) {
  // Fast path: already initialised
  if (g_initialized.load(std::memory_order_acquire))
    return;

  std::call_once(g_init_once, [&cc]() {
    // Load config from file — if no config is found, stay dormant
    // (client/keygen binaries compiled with OPENFHE_CPROBES should not
    // activate auto-facade when no yml config is present)
    auto cfg_path = find_config();
    if (cfg_path.empty()) {
      g_mode = Mode::DORMANT;
      g_initialized.store(true, std::memory_order_release);
      return;
    }
    load_config(cfg_path);

    // Build synthetic argv from compiler section and call init()
    if (g_config.compiler_node && g_config.compiler_node.IsMap()) {
      auto args = build_init_argv(g_config.compiler_node);
      std::vector<char*> argv_ptrs;
      for (auto& a : args) argv_ptrs.push_back(a.data());
      int argc = static_cast<int>(argv_ptrs.size());
      niobium::compiler().init(argc, argv_ptrs.data());
    }

    // Program info
    niobium::compiler().set_program_info(g_config.name, g_config.version, g_config.description);
    niobium::compiler().set_build_info("(auto-facade)", 0, "(auto-facade)");

    // Cache parameters
    niobium::compiler().cache_parameters(g_config.cache_params);

    // Key paths: niobium-fhetch's Compiler doesn't expose the cached_key()
    // cache-aware helper that niobium-compiler has. Keys travel through the
    // capture_crypto_context() + tag_keys() path instead, so we only store
    // the paths on g_config for later reference. TODO: add cache-aware key
    // registration if it becomes material for this repo's workflows.

    // Capture crypto context (use stored cc if available, fall back to parameter)
    const auto &ctx = g_cc ? g_cc : cc;
    niobium::compiler().capture_crypto_context(ctx);
    // Evaluation keys are tag_keys'd later (inside atexit_handler) — at
    // this point in the flow the user hasn't yet called
    // DeserializeEvalMultKey / DeserializeEvalAutomorphismKey, so
    // cc->GetAllEvalMultKeys() returns empty and tag_keys would no-op.

    // Determine mode EARLY — this informs whether hollow_mode should be set
    bool cache_valid = niobium::compiler().is_cache_valid();

    if (cache_valid) {
      // REPLAY mode — mark only; the actual replay() call is deferred until
      // every input has been tagged (see ensure_replayed()). niobium-fhetch's
      // replay() consumes captured_inputs in-memory rather than re-reading
      // the cached workload dir, so we have to wait for on_deserialize_ciphertext
      // to populate them before firing replay.
      g_replay_mode = true;
      g_mode = Mode::REPLAY;
    } else {
      // RECORD — hollow mode only applies to recording
      if (g_config.compiler_node &&
          g_config.compiler_node["hollow"] &&
          g_config.compiler_node["hollow"].as<bool>(false))
        niobium::compiler().enable_hollow_mode(true);
      g_mode = Mode::RECORD;
    }

    register_atexit();
    g_initialized.store(true, std::memory_order_release);

    // NOTE: Does NOT call start(). Callers are responsible:
    //   - on_deserialize_crypto_context calls start() after installing scheme proxy
    //   - Compiler::start() calls lazy_init() then proceeds with its own start logic
  });
}

void lazy_init() {
  if (g_cc)
    lazy_init(g_cc);
}

bool is_replaying() {
  lazy_init();
  return g_replay_mode;
}

void on_deserialize_crypto_context(lbcrypto::CryptoContext<lbcrypto::DCRTPoly> &cc) {
  if (!cc)
    return;
  g_cc = cc;

  // Determine record/replay mode first, while the real scheme is still in place.
  // capture_crypto_context inside lazy_init serialises the CC to compute a cache key;
  // NiobiumAutoScheme is not a registered Cereal type so it must NOT be installed yet.
  lazy_init(cc);

  // In DORMANT mode (no YAML config, explicit API in use) do not install the
  // proxy scheme.  NiobiumAutoScheme is not registered with cereal, so leaving
  // it in the CryptoContext causes key serialization to fail (only a .tmp file
  // is produced), which breaks QEMU/FPGA replay on the next run.
  if (g_mode == Mode::DORMANT)
    return;

  // Install the NiobiumAutoScheme proxy on both record and replay (matches
  // the niobium-compiler auto-facade contract). In record the proxy is
  // a pure passthrough (forwards to m_real); in replay it short-circuits
  // compute ops to a dummy. A previous workaround installed only on
  // replay to chase a MUL/relin precision bug — the actual cause was
  // tag_keys() timing (see below), not the proxy install. Removing the
  // asymmetry brings the client flow in line with the compiler reference.
  cc->SetScheme(std::make_shared<lbcrypto::NiobiumAutoScheme>(cc->GetScheme(), cc));

  // If auto-facade determined recording mode, start now.
  // The running_p() guard prevents double-starting if Compiler::start() already ran.
  if (g_mode == Mode::RECORD && !niobium::compiler().running_p()) {
    niobium::compiler().start();
    g_recording = true;
  }
}

void on_deserialize_ciphertext(const std::string &filepath,
                               lbcrypto::Ciphertext<lbcrypto::DCRTPoly> &ct) {
  if (!ct)
    return;

  // Each ciphertext file is serialized independently, so cereal creates a
  // separate CryptoContext instance per file.  Reassign to the shared g_cc so
  // all ciphertexts compare equal in TypeCheck (operator!=).
  if (g_cc && ct->GetCryptoContext() != g_cc) {
    // CryptoObject::context is protected — cast to access it directly.
    // This is safe: CiphertextImpl inherits CryptoObject<DCRTPoly>.
    struct CryptoObjectAccessor : lbcrypto::CryptoObject<lbcrypto::DCRTPoly> {
      using lbcrypto::CryptoObject<lbcrypto::DCRTPoly>::context;
    };
    static_cast<CryptoObjectAccessor*>(
        static_cast<lbcrypto::CryptoObject<lbcrypto::DCRTPoly>*>(ct.get()))
        ->context = g_cc;
  }

  // CC is always deserialized before ciphertexts, so lazy_init must have fired.
  if (!g_initialized.load(std::memory_order_acquire)) {
    std::cerr << "[niobium_auto] Warning: ciphertext deserialized before crypto context — "
                 "input will not be tagged\n";
    return;
  }

  // In DORMANT mode the explicit compiler API is in use; skip auto-tagging to
  // avoid injecting duplicate addr-id mappings that won't be loaded on replay.
  if (g_mode == Mode::DORMANT)
    return;

  // Guard against re-entrant deserializations the auto-facade itself
  // issues (Compiler::result loads serialized_probes/<name>.ct via
  // Serial::DeserializeFromFile → fires this hook). Tagging that file
  // as an input pollutes captured_inputs with the template's stale
  // placeholder polynomial values at the same addresses the simulator
  // is about to write computed output to, which corrupts replay.
  if (g_in_facade_io) return;

  // Tag the eval keys lazily on first user-ciphertext deserialize. By this
  // point user code has already loaded the CC and the eval keys (cc.bin
  // and the eval-key files come before the first input ciphertext in every
  // observed flow); the address allocator state is identical between
  // record and replay (both have CC tags + key tags only, no intermediates).
  // Calling tag_keys later (atexit on record / ensure_replayed on replay)
  // produces asymmetric addresses because record's Eval ops have consumed
  // many address slots while replay's proxied Eval ops have consumed
  // zero — the trace then references record-time key addresses that the
  // replay sim never populated.
  bool expected = false;
  if (g_keys_tagged.compare_exchange_strong(expected, true,
                                            std::memory_order_acq_rel)) {
    if (g_cc) {
      niobium::compiler().tag_keys(g_cc);
    }
  }

  // niobium-fhetch's Compiler doesn't expose cached_input()'s skip-if-present
  // fast path; tag the input unconditionally. The library-side dedup
  // protection in captured_inputs handles accidental double-registration.
  //
  // Use the full path with separators escaped (so the resulting name is a
  // single filename-safe token), not just the stem: programs that load
  // multiple ciphertexts with the same stem from different directories
  // (e.g. fetch-by-similarity's mat_vec_mult reads batch<j>/row_<i>.bin —
  // every batch has its own row_0000.bin) would otherwise collide on the
  // shared name "row_0000", overwriting each other's .bin/.ids files on
  // disk and losing earlier batches' addresses.
  auto unique_name = [](const std::string& path) {
    // Strip trailing .bin (if any) then replace path separators with '_'.
    std::string s = path;
    const std::string ext = ".bin";
    if (s.size() >= ext.size() && s.compare(s.size() - ext.size(), ext.size(), ext) == 0)
      s.resize(s.size() - ext.size());
    for (auto& c : s)
      if (c == '/' || c == '\\' || c == ':' || c == ' ')
        c = '_';
    // Drop any leading '_' from absolute paths so the .input_<name>.bin
    // file doesn't end up named ".input__Users_...".
    size_t first = s.find_first_not_of('_');
    if (first != std::string::npos && first > 0)
      s.erase(0, first);
    return s;
  };
  // Append a per-filepath load counter so repeated deserializes of the
  // SAME filepath each get their own `.input_<name>_<N>.bin/.ids` pair
  // on disk. tag_input<Ciphertext> overwrites the on-disk files on
  // every call, so without this counter only the LAST load's addr_ids
  // survive on disk for a given path — even though in-memory
  // captured_inputs accumulates them all. Cross-process replay reads
  // disk, so it misses every load except the last. (Hit by FBS's
  // mat_vec_mult outer-i loop, which reloads each
  // batch<k>/payload_<j>.bin once per i ∈ [1, MaxNMatch].)
  static std::unordered_map<std::string, size_t> g_load_counter;
  const std::string base = unique_name(filepath);
  size_t n = g_load_counter[base]++;
  std::string name = base + "_load_" + std::to_string(n);
  niobium::compiler().tag_input(name, ct, filepath);
}

// Fire Compiler::replay() on the first output-serialize in REPLAY mode.
// By this point every input ciphertext has already come through
// on_deserialize_ciphertext → Compiler::tag_input, so captured_inputs is
// fully populated and Compiler::replay() can run to completion with
// every live-in address backed by real values.
static std::atomic<bool> g_replay_done{false};
static void ensure_replayed() {
  // Set the flag BEFORE running replay so nested calls into the hooks
  // (e.g. reconstruct_probes → SerializeToFile → on_serialize_ciphertext
  // → ensure_replayed) short-circuit instead of starting a second replay.
  bool expected = false;
  if (g_replay_done.compare_exchange_strong(expected, true,
                                            std::memory_order_acq_rel)) {
    // tag_keys() already fired lazily in on_deserialize_ciphertext so
    // the FHETCH compact-address allocator handed keys the same
    // addresses on record and replay. Just run the simulator.
    niobium::compiler().replay();
  }
}

bool on_serialize_ciphertext(const std::string &filepath,
                             const lbcrypto::Ciphertext<lbcrypto::DCRTPoly> &ct) {
  if (!g_initialized.load(std::memory_order_acquire))
    return false;

  const std::string stem = path_stem(filepath);

  if (g_replay_mode) {
    ensure_replayed();
    // Retrieve the hardware-computed result and write it to the output file.
    // If result() succeeds we substitute the HW ct and tell the caller to
    // skip the normal write (return true). If it fails — which is expected
    // when the very first replay pass is still inside reconstruct_probes
    // writing this exact file — we return false so the normal
    // Serial::SerializeToFile proceeds and actually produces the file.
    if (g_cc) {
      lbcrypto::Ciphertext<lbcrypto::DCRTPoly> hw_ct;
      InFacadeIoGuard _facade_io;
      if (niobium::compiler().result(g_cc, stem, hw_ct) && hw_ct) {
        g_probed_cts[ct.get()] = stem; // let on_decrypt know this ct was handled
        std::ofstream file(filepath, std::ios::out | std::ios::binary);
        if (file.is_open()) {
          lbcrypto::Serial::Serialize(hw_ct, file, lbcrypto::SerType::BINARY);
          file.close();
        }
        // Update the in-memory ciphertext with the HW result so subsequent
        // Decrypt on this ct gets the real data instead of dummy polynomials.
        ct->SetElements(hw_ct->GetElements());
        return true; // result found + HW ct written, skip normal serialize
      }
    }
    return false; // fall back to normal SerializeToFile
  }

  if (g_recording) {
    // Probe output for instruction trace using the filename as probe name.
    niobium::compiler().probe(stem, ct);
    g_probed_cts[ct.get()] = stem;
    return false; // Let normal serialize proceed (writes actual ciphertext to file)
  }

  return false;
}

bool is_recording() {
  return g_recording;
}

static std::string next_probe_name() {
  return "output_" + std::to_string(g_probe_counter++);
}

bool on_decrypt(lbcrypto::Ciphertext<lbcrypto::DCRTPoly> &ct) {
  if (!g_initialized.load(std::memory_order_acquire))
    return false;

  // Determine the probe name: reuse the serialize name if this ct was already
  // probed via SerializeToFile, otherwise generate a new counter-based name.
  auto it = g_probed_cts.find(ct.get());
  bool already_probed = (it != g_probed_cts.end());
  std::string name = already_probed ? it->second : next_probe_name();

  if (g_recording) {
    if (!already_probed) {
      niobium::compiler().probe(name, ct);
      g_probed_cts[ct.get()] = name;
    }
    return false; // proceed with normal decrypt
  }

  if (g_replay_mode) {
    ensure_replayed();
    // Look up the HW-computed result by the same name used during recording
    if (g_cc) {
      lbcrypto::Ciphertext<lbcrypto::DCRTPoly> hw_ct;
      InFacadeIoGuard _facade_io;
      if (niobium::compiler().result(g_cc, name, hw_ct) && hw_ct) {
        ct = hw_ct; // substitute the dummy with the real HW result
        return false; // proceed with normal decrypt on the HW result
      }
    }
  }

  return false;
}

void on_make_plaintext(lbcrypto::Plaintext &pt) {
  // Activate only when the auto-facade is actually driving the run.
  if (!g_initialized.load(std::memory_order_acquire)) return;
  if (g_mode == Mode::DORMANT || g_mode == Mode::UNINITIALIZED) return;
  if (g_in_make_plaintext_hook) return;
  g_in_make_plaintext_hook = true;
  struct Guard { ~Guard() { g_in_make_plaintext_hook = false; } } guard;

  // Deterministic name — counter advances in lock-step on record + replay
  // (program is deterministic, hook fires at every Make*Plaintext site).
  std::string name = "pt_" + std::to_string(g_plaintext_counter++);

  if (g_mode == Mode::RECORD) {
    // Real pt: tag it as an input — write .bin/.ids files + push entries
    // into captured_inputs so the FHETCH simulator finds the plaintext
    // polynomial values as live-in data.
    if (pt) {
      niobium::compiler().tag_input(name, pt);
    }
    return;
  }

  // g_mode == REPLAY: pt is null (cryptocontext.h's Make*Plaintext returns
  // nullptr in replay as a shortcut). Rehydrate captured_inputs from the
  // <prog>.input_pt_<N>.bin/.ids files the recording wrote. Addresses on
  // disk are the record-time FHETCH addresses, which is what the trace
  // expects.
  if (::niobium::detail::load_plaintext_input_file(name)) {
    std::cout << "[niobium_auto] Rehydrated plaintext '" << name
              << "' from disk for replay\n";
  } else {
    std::cerr << "[niobium_auto] Warning: failed to load plaintext '"
              << name << "' from disk on replay\n";
  }
}

} // namespace niobium_auto
