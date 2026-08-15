#!/bin/bash
# ============================================================================
# test_transport_mult.sh — one-shot client→server→compiler→client round trip.
#
# 1. Spawns nbcc_fhetch_replay_server in the background.
# 2. Ensures the transport forwarder is first on PATH so mult_server's
#    --target=FUNC_SIM dispatch hits it instead of the compiler binary.
# 3. Regenerates keys + ciphertexts (mult_client), runs mult_server with
#    --target=FUNC_SIM, decrypts.
# 4. Tears the server down even on failure.
#
# Exit 0 on decrypt PASS, non-zero otherwise. Suitable for CI.
#
# Overrides:
#   A, B               Plaintext operands (default 7, 13).
#   PORT               Server port (default 9443).
#   TARGET             Replay target passed to mult_server (default FUNC_SIM).
#   RING_DIM           Ring dimension passed to mult_client (default 2048).
#   NIOBIUM_COMPILER_ROOT   Path to niobium-compiler checkout.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT_ROOT="$(cd "$HERE/.." && pwd)"
: "${NIOBIUM_COMPILER_ROOT:=$(cd "$CLIENT_ROOT/../.." && pwd)}"
: "${NIOBIUM_COMPILER_BUILD:=$NIOBIUM_COMPILER_ROOT/build}"
: "${PORT:=9443}"
: "${TARGET:=FUNC_SIM}"
: "${A:=7}"
: "${B:=13}"
: "${RING_DIM:=2048}"

BUILD="$CLIENT_ROOT/build"
TRANSPORT_DIR="$BUILD/src/fhetch_transport"

# niobium_client.env is written by `make config-client-release` and records
# the OpenFHE install dir the build was linked against. Source it so we point
# LD_LIBRARY_PATH at the right place on Linux, where there's no rpath fallback.
# In compiler-driven builds this picks up the parent's openfhe install; in
# standalone builds it just records the client's own vendored install.
ENV_FILE="$BUILD/niobium_client.env"
[[ -f "$ENV_FILE" ]] && . "$ENV_FILE"
: "${OPENFHE_LIB:=$CLIENT_ROOT/vendor/lib/openfhe/lib}"

mult_client="$BUILD/examples/mult_client"
mult_server="$BUILD/examples/mult_server"
mult_decrypt="$BUILD/examples/mult_decrypt"
forwarder="$TRANSPORT_DIR/nbcc_fhetch_replay"

for bin in "$mult_client" "$mult_server" "$mult_decrypt" "$forwarder"; do
  [[ -x "$bin" ]] || { echo "error: not built: $bin" >&2; exit 2; }
done

SERVER_LOG="$(mktemp)"
SERVER_PID=""

cleanup() {
  set +e
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill -TERM "$SERVER_PID" 2>/dev/null
    # Give the server up to 5s to drain; fall back to SIGKILL.
    for _ in 1 2 3 4 5; do
      kill -0 "$SERVER_PID" 2>/dev/null || break
      sleep 1
    done
    kill -0 "$SERVER_PID" 2>/dev/null && kill -KILL "$SERVER_PID" 2>/dev/null
  fi
  echo
  echo "=== server log ==="
  cat "$SERVER_LOG"
  rm -f "$SERVER_LOG"
}
trap cleanup EXIT

echo "=== [1/5] starting fhetch_server (port $PORT) ==="
PORT="$PORT" BIND=127.0.0.1 \
NIOBIUM_COMPILER_ROOT="$NIOBIUM_COMPILER_ROOT" \
NIOBIUM_COMPILER_BUILD="$NIOBIUM_COMPILER_BUILD" \
"$HERE/fhetch_server.sh" > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

# Wait until /healthz answers or the server bails out.
for i in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "server up (pid=$SERVER_PID)"
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "error: server exited before healthz was reachable" >&2
    exit 3
  fi
  sleep 0.1
done
if ! curl -sf "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "error: server did not become healthy within 5s" >&2
  exit 3
fi

echo
echo "=== [2/5] mult_client (keygen + encrypt a=$A b=$B) ==="
cd "$CLIENT_ROOT"
rm -rf mult_keys mult_server_workload_*
LD_LIBRARY_PATH="$OPENFHE_LIB" "$mult_client" mult_keys "$A" "$B" "$RING_DIM"

echo
echo "=== [3/5] mult_server --target=$TARGET (through transport) ==="
# Put the forwarder first on PATH so Compiler::replay()'s system() call
# hits it — matches the production install layout.
export PATH="$TRANSPORT_DIR:$PATH"
export NBCC_FHETCH_SERVER="http://127.0.0.1:$PORT"
# Clear the loader search path for this one: mult_server dispatches to the
# compiler's replay binary, and a child process inherits our search path,
# which would override the rpath the replay binary uses to find the OpenFHE
# it was built against. Both binaries resolve their own libraries via rpath,
# so no search path is needed here. (mult_client / mult_decrypt above and
# below are client-only and keep theirs.)
env -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH \
  "$mult_server" mult_keys --target="$TARGET" --no-ring-dim-check

echo
echo "=== [4/5] mult_decrypt ==="
# Capture instead of `| tee /dev/stderr |`: /dev/stderr is a magic symlink to
# /proc/self/fd/2, so tee re-opens the underlying inode and needs write
# permission on it. In a container whose stderr pipe is owned by root while the
# script runs as a non-root uid, that fails with EACCES. Reprinting through the
# already-open fd 2 avoids the re-open. It also drops the grep -q/pipefail
# race, where grep exiting on match could SIGPIPE tee and fail a passing run.
set +e
decrypt_out=$(LD_LIBRARY_PATH="$OPENFHE_LIB" "$mult_decrypt" mult_keys 2>&1)
decrypt_rc=$?
set -e
printf '%s\n' "$decrypt_out" >&2
if [ "$decrypt_rc" -ne 0 ] || ! grep -q '^\[PASS\]' <<<"$decrypt_out"; then
  echo
  echo "=== ✗ transport round-trip FAIL (decrypt) ==="
  exit 1
fi
echo
echo "decrypt PASS — continuing to binary comparison"

echo
echo "=== [5/5] binary comparison ==="
# Compute the hashes of the result openfhe computed, the result our pipeline writes to the final destination,
# the ciphertext template the compiler uses, and the serialized probe of the compiler
OPENFHE_RESULT_HASH=$(sha256sum mult_keys/ct_result_openfhe.bin | awk '{print $1}')
echo "OpenFHE Gold Standard"
echo $OPENFHE_RESULT_HASH

FINAL_RESULT_HASH=$(sha256sum mult_keys/ct_result.bin | awk '{print $1}') 
echo "Compiler Pipeline Result"
echo $FINAL_RESULT_HASH

CT_TEMPLATE_HASH=$(sha256sum mult_server_workload_ckks_mult/ciphertext_templates/result.template | awk '{print $1}')
echo "Compiler Internal Ciphertext Template"
echo $CT_TEMPLATE_HASH

CT_PROBE_HASH=$(sha256sum mult_server_workload_ckks_mult/serialized_probes/result.ct | awk '{print $1}')
echo "Compiler Internal Serialized Probe"
echo $CT_PROBE_HASH

if [ "$OPENFHE_RESULT_HASH" != "$FINAL_RESULT_HASH" ]; then
  echo
  echo "Results are not byte identical"
  echo "=== ✗ transport round-trip FAIL ==="
  exit 1
else
  echo  
  echo "Results are byte identical"
  echo "=== ✓ transport round-trip PASS ==="
  exit 0
fi
