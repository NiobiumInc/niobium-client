# Auto-Facade: Transparent Record/Replay for OpenFHE Programs

The auto-facade (`libnbclient_autofacade`, built when
`NIOBIUM_CLIENT_WITH_AUTO_FACADE=ON`) provides **zero-boilerplate**
record/replay instrumentation for existing OpenFHE programs. No source
changes are required — the facade intercepts FHE operations automatically
via patched OpenFHE headers (shipped in the `niobium-fhetch` submodule's
vendored OpenFHE) and a YAML config file discovered via `NIOBIUM_CONFIG`.

**When to use which approach:**

| | Auto-Facade | Manual `niobium::compiler()` SDK |
|---|---|---|
| Source code changes | None | Required |
| Config mechanism | YAML file via `nbcc.py` / `NIOBIUM_CONFIG` | C++ API calls |
| Setup effort | Minimal | Full control |
| Fine-grained cache params | Via YAML / CLI | Via `cache_parameters()` |
| Skip expensive plaintext work on replay | Automatic | Manual `is_cache_valid()` check |
| Multiple named outputs | Automatic (via `Decrypt` or `SerializeToFile`) | Manual `probe()` calls |

Use the auto-facade for quick integration with existing programs. Use the
manual SDK (see `examples/bootstrap/`, `examples/mult/`, `examples/simple_ops/`)
when you need precise control over caching, cache keys, or want to skip
expensive plaintext computation on replay.

## Where this lives in the code

- **`libnbfhetch`** (in `vendor/niobium-fhetch`) provides *weak* stub
  implementations of the `niobium_auto::*` hook set, so a program that
  only links libnbfhetch still links cleanly and the hooks are no-ops.
- **`libnbclient_autofacade`** (this repo, `src/auto_facade/AutoFacade.cpp`)
  provides *strong* definitions for those hooks. When an executable
  links both libraries, the linker picks the strong symbols and the
  auto-facade becomes active.
- **YAML config** is discovered via the `NIOBIUM_CONFIG` env var. The
  `tools/nbcc.py` wrapper generates the config, sets the env var, and
  execs the user binary — users never author the YAML by hand unless
  they want to.
- **Runtime machinery** (scheme proxy, SerializeToFile / Eval* hooks) is
  in the Niobium-instrumented OpenFHE branch the submodule points at
  (`niobium_auto_hooks.h`, `niobium_auto_scheme.h`).

Porting note: this was lifted from
[`niobium-compiler` PR #1035](https://github.com/NiobiumInc/niobium-compiler/pull/1035).
The compiler-specific bits (FPGA/QEMU targets, hardware-mode flags,
deep cache-aware key reuse) were dropped to keep the client port small;
for those, use the compiler flow.

---

## Quick Start

**1. Build with auto-facade enabled:**
```bash
make build NIOBIUM_AUTO_FACADE=1
```

**2. Run with `nbcc.py`** — generates a config file, sets `NIOBIUM_CONFIG`, and execs the program:
```bash
python3 tools/nbcc.py --name my_program --cache param1=value1 \
    --keys-mult keys/mk.bin --keys-auto keys/rk.bin \
    -- ./dbuild/my_program [args...]
```

**3. First run — records the FHE operations.** Second run with the same `nbcc.py` args — **replays on hardware (or simulator).**

**Alternative: manual config file** — place `<exe_stem>.niobium_compiler.yml` in CWD or set `NIOBIUM_CONFIG=/path/to/config.yml`.

**4. No source changes needed.** See [`examples/auto/ciphers_ops_server_auto.cpp`](examples/auto/ciphers_ops_server_auto.cpp) for a reference example.

---

## Config Discovery

The facade searches for a config file in this order (first match wins):

1. **`NIOBIUM_CONFIG` env var** — explicit path to a YAML config file
2. `<CWD>/<exe_stem>.niobium_compiler.yml`
3. `<CWD>/.niobium_compiler.yml`
4. `<exe_dir>/<exe_stem>.niobium_compiler.yml`
5. `<exe_dir>/.niobium_compiler.yml`

**If no config file is found, the auto-facade stays dormant** — no compiler init, no recording, no replay. This is important because client/keygen binaries compiled with `NIOBIUM_AUTO_FACADE` should not activate the facade.

---

## Config File Reference

```yaml
program:
  name:        string   # Program identity — included in cache key. Default: "auto_facade"
  version:     string   # Program version — included in cache key. Default: "1.0"
  description: string   # Human-readable description. Default: "Auto-facade program"

# Arbitrary key-value pairs that distinguish recordings from each other.
# All entries are included in the cache key, so changing any value
# invalidates the cache and triggers a fresh recording.
cache_parameters:
  <key>: <value>        # e.g. wl: "1", op: "ADD"

keys:
  eval_mult:         string   # Path to EvalMult key file (optional)
  eval_automorphism: string   # Path to EvalAutomorphism key file (optional)

# Compiler flags — converted to synthetic argc/argv and passed to Compiler::init().
# All fields are optional; omitted fields use init() defaults.
compiler:
  target:       FUNC_SIM       # FUNC_SIM | FHE_SIM | QEMU_SIM | FPGA1 | FPGA2
  optimization: "3"            # -O level
  registers:    "32"           # --registers
  memory:       "16"           # --memory (GB)
  niobium_hw:   true           # --niobium_hw
  hollow:       false          # enable_hollow_mode (not passed to init)
  fence:        true           # --fence / --no-fence
  noop:         "0"            # --noop
  multiplier:   "shoup"        # --multiplier (standard | shoup)
  config_sectors: "4"          # --config-sectors
  binary_json:  true           # --binary-json / --ascii-json
  no_cereal_binary: false      # --no-cereal-binary
  transform_bin_to_json: false # --transform-bin-to-json
  no_preserve_input_ciphertexts: false
  formal:       false          # --formal
  lock_timing:  false          # --lock-timing
```

---

## `nbcc.py` Wrapper

`tools/nbcc.py` generates deterministic config files from CLI flags. Same flags always produce the same config file (content-hashed), so recording and replay runs find the same cache automatically.

```bash
python3 tools/nbcc.py --name NAME \
    [--cache KEY=VALUE ...] \
    [--keys-mult PATH] [--keys-auto PATH] \
    [--target TARGET] [-O LEVEL] \
    [--registers N] [--memory GB] \
    [--niobium-hw] [--hollow] \
    [--fence | --no-fence] \
    -- EXECUTABLE [ARGS...]
```

**Behavior:**
1. Builds a YAML string from the CLI options
2. Writes to `.nbcc/<sha256[:16]>.yml` in CWD
3. Sets `NIOBIUM_CONFIG=.nbcc/<hash>.yml`
4. `os.execvp()` the target executable

**Example:**
```bash
# Recording
python3 tools/nbcc.py --name my_add --cache wl=0 --cache op=ADD \
    --keys-mult io/toy/keys/mk.bin --keys-auto io/toy/keys/rk.bin \
    -- ./dbuild/auto_ciphers_ops 0 output_a.bin output_b.bin 10 ADD

# Replay (same command — deterministic config → cache hit)
python3 tools/nbcc.py --name my_add --cache wl=0 --cache op=ADD \
    --keys-mult io/toy/keys/mk.bin --keys-auto io/toy/keys/rk.bin \
    -- ./dbuild/auto_ciphers_ops 0 output_a.bin output_b.bin 10 ADD
```

---

## Build Instructions

```bash
# Debug mode (builds everything including openfhe with auto-facade headers)
make build NIOBIUM_AUTO_FACADE=1

# Release mode
make build-release NIOBIUM_AUTO_FACADE=1

# Clean rebuild (required after openfhe header changes)
make clean
make build NIOBIUM_AUTO_FACADE=1
```

---

## How It Works

The auto-facade operates through transparent hooks, all guarded by `#ifdef NIOBIUM_AUTO_FACADE`.

### Config and Init

`lazy_init()` runs once on first CryptoContext deserialization or when `Compiler::start()` is called:
1. Finds config file (via `NIOBIUM_CONFIG` or search order)
2. If no config found → stays **dormant** (returns immediately)
3. Parses YAML and builds synthetic `argc`/`argv` from the `compiler:` section
4. Calls `Compiler::init(argc, argv)` to configure the compiler
5. Calls `set_program_info()`, `cache_parameters()`, `cached_key()`, `capture_crypto_context()`
6. Checks `is_cache_valid()` to determine mode **before** setting hollow:
   - **Miss (record)** → sets `enable_hollow_mode()` if `hollow: true`
   - **Hit (replay)** → calls `replay(target)`, sets `g_replay_mode = true`
7. `on_deserialize_crypto_context` installs the scheme proxy, then calls `start()` if recording

### Replay No-Ops

When `g_replay_mode = true`, all FHE compute operations go through `NiobiumAutoScheme`, which returns a lightweight dummy ciphertext (preserving CC pointer, key tag, and encoding type) without executing any polynomial arithmetic. The `g_replay_noop_count` atomic counter tracks how many operations were no-oped.

### Output Capture (Probe and Result)

Output ciphertexts are captured at two interception points — **whichever the program hits first** for a given ciphertext:

**`SerializeToFile`** — when the program writes a result ciphertext to disk:
- **Recording:** calls `compiler().probe(filename_stem, ct)` and proceeds with normal file write
- **Replay:** calls `compiler().result(cc, filename_stem, hw_ct)` and writes the HW result to the file

**`Decrypt`** — when the program decrypts a result ciphertext:
- **Recording:** calls `compiler().probe(name, ct)` (if not already probed via serialize), then proceeds with normal Decrypt (paused from the instruction trace)
- **Replay:** calls `compiler().result(cc, name, hw_ct)`, substitutes the dummy ciphertext with the HW result, then proceeds with normal Decrypt

A ciphertext is only probed once — if `SerializeToFile` already probed it, `Decrypt` reuses the same probe name. Probe names from `SerializeToFile` are filename stems; probe names from `Decrypt` are auto-incremented (`output_0`, `output_1`, ...).

### CryptoContext Normalization

Each ciphertext file serialized independently by cereal contains its own CryptoContext instance. To prevent OpenFHE's `TypeCheck` from rejecting operations between ciphertexts from different files, `on_deserialize_ciphertext` normalizes all ciphertext CC pointers to the shared `g_cc`.

### atexit Handler

`AutoFacade.cpp` registers a process-exit handler that calls `compiler().stop()` to finalize and persist the recording. Before stopping, it swaps the `NiobiumAutoScheme` proxy back to the real scheme so cereal serialization succeeds.

---

## Recording and Replay Flows

### Recording

```
Program starts
    │
    ├─ DeserializeFromFile(cc.bin, cc)
    │       └─ on_deserialize_crypto_context(cc)
    │               └─ lazy_init() → loads config, init(), determines RECORD mode
    │               └─ installs NiobiumAutoScheme proxy
    │               └─ start() → recording begins
    │
    ├─ DeserializeFromFile(input.bin, ct)
    │       └─ on_deserialize_ciphertext() → normalizes CC, tag_input()
    │
    ├─ EvalAdd(ct1, ct2)
    │       └─ NiobiumAutoScheme forwards to real scheme → real math
    │
    ├─ Decrypt(sk, result, &pt)
    │       └─ on_decrypt() → probe("output_0", result)
    │          pause recording → real Decrypt → resume recording
    │
    └─ Program exits
            └─ atexit → stop() → recording finalized
```

### Replay

```
Program starts
    │
    ├─ DeserializeFromFile(cc.bin, cc)
    │       └─ lazy_init() → is_cache_valid() → true → replay()
    │
    ├─ DeserializeFromFile(input.bin, ct)
    │       └─ cached_input() / tag_input()
    │
    ├─ EvalAdd(ct1, ct2)
    │       └─ NiobiumAutoScheme → dummy() → no-op (noop_count++)
    │
    ├─ Decrypt(sk, dummy_result, &pt)
    │       └─ on_decrypt() → result(cc, "output_0", hw_ct)
    │          substitutes dummy with HW result → real Decrypt on hw_ct
    │
    └─ Program exits normally
```

---

## FHE Operations Intercepted

All compute operations go through `NiobiumAutoScheme` (and its sub-proxies `NiobiumAutoFHE`, `NiobiumAutoAdvancedSHE`). In replay mode, every operation returning a ciphertext returns a dummy via `dummy()`.

The following methods are **not** compute ops but are wrapped with pause/resume guards during recording:

- `MakePackedPlaintext` — returns nullptr in replay mode
- `MakeCKKSPackedPlaintext` (both `double` and `complex<double>` overloads) — returns nullptr in replay mode
- `Decrypt` — probes output or fetches HW result via `on_decrypt`

---

## Testing

### Auto-Facade Operation Tests

```bash
# Runs FUNC_SIM + FHE_SIM + NB_HW + hollow (81 tests for non-hollow modes)
make test-ops-auto

# Release mode
make test-ops-auto-release

# QEMU mode (requires QEMU infrastructure)
make test-ops-auto-qemu
```

The test infrastructure uses `ops_test.py --auto` which wraps the `auto_ciphers_ops` binary with `nbcc.py`. Each test:
1. Generates keys (`ciphers_ops_cache_keys`)
2. Creates encrypted inputs (`ciphers_ops_client`)
3. Runs recording via `nbcc.py -- auto_ciphers_ops ...`
4. Runs replay via the same command (deterministic config → cache hit)
5. Verifies the replayed result matches the expected value

### Coverage Test

```bash
python3 tests/auto_facade/test_auto_facade_coverage.py
```

Validates that `niobium_auto_scheme.h` overrides all virtual functions from the OpenFHE base classes and that all compute ops have replay guards.

---

## Limitations

- **One recording per process.** `lazy_init` fires once. Programs that process multiple independent datasets in a single run will have them folded into one recording.
- **Expensive plaintext work still runs during recording.** `MakePackedPlaintext`/`Decrypt` are only excluded from the instruction *trace*, not skipped entirely. To skip the full computation on replay, check `compiler().is_cache_valid()` manually (manual SDK pattern).

---

## Key Source Files

| File | Purpose |
|------|---------|
| `src/AutoFacade.cpp` | Hook implementations, config loading, `init()` call, state machine |
| `tools/nbcc.py` | CLI wrapper — generates deterministic YAML configs |
| `vendor/openfhe/src/pke/include/niobium_auto_hooks.h` | Hook declarations, `g_replay_mode`, `g_replay_noop_count` |
| `vendor/openfhe/src/pke/include/niobium_auto_scheme.h` | Proxy classes for all intercepted FHE ops |
| `vendor/openfhe/src/pke/include/cryptocontext.h` | Decrypt hook, MakePlaintext replay guards, pause/resume |
| `vendor/openfhe/src/pke/include/ciphertext-ser.h` | Ciphertext serialize/deserialize interception |
| `vendor/openfhe/src/pke/include/cryptocontext-ser.h` | CryptoContext deserialize interception |
| `examples/auto/ciphers_ops_server_auto.cpp` | Reference example (no SDK boilerplate) |
| `tests/auto_facade/test_auto_facade_coverage.py` | Static analysis: validates proxy coverage |
| `run_scripts/add_auto_hollow.sh` | Repro script for hollow recording + replay |

For the manual instrumentation API, see [RECORD_REPLAY.md](RECORD_REPLAY.md).
