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

echo "=== [1/4] starting fhetch_server (port $PORT) ==="
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
echo "=== [2/4] mult_client (keygen + encrypt a=$A b=$B) ==="
cd "$CLIENT_ROOT"
rm -rf mult_keys mult_server_workload_*
LD_LIBRARY_PATH="$OPENFHE_LIB" "$mult_client" mult_keys "$A" "$B"

echo
echo "=== [3/4] mult_server --target=$TARGET (through transport) ==="
# Put the forwarder first on PATH so Compiler::replay()'s system() call
# hits it — matches the production install layout.
export PATH="$TRANSPORT_DIR:$PATH"
export NBCC_FHETCH_SERVER="http://127.0.0.1:$PORT"
LD_LIBRARY_PATH="$OPENFHE_LIB" "$mult_server" mult_keys --target="$TARGET"

echo
echo "=== [4/4] mult_decrypt ==="
if LD_LIBRARY_PATH="$OPENFHE_LIB" "$mult_decrypt" mult_keys | tee /dev/stderr | grep -q '^\[PASS\]'; then
  echo
  echo "=== ✓ transport round-trip PASS ==="
  exit 0
else
  echo
  echo "=== ✗ transport round-trip FAIL ==="
  exit 1
fi
