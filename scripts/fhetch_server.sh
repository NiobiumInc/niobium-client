#!/bin/bash
# ============================================================================
# fhetch_server.sh — start nbcc_fhetch_replay_server wired to a compiler build
#
# Resolves the paths a deployment normally has to handcraft:
#   - LD_LIBRARY_PATH for OpenFHE + libnbcc + NTL
#   - --exec path to the compiler-side nbcc_fhetch_replay
#
# Foreground by default. Override PORT / BIND / NIOBIUM_COMPILER_ROOT via env.
# Additional args after "--" go straight through to the server.
#
# Example:
#   NIOBIUM_COMPILER_ROOT=$HOME/niobium-compiler \
#     scripts/fhetch_server.sh
#
#   PORT=19443 scripts/fhetch_server.sh -- --bind 0.0.0.0
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CLIENT_ROOT="$(cd "$HERE/.." && pwd)"

# Default: compiler lives two levels up from the client (the submodule layout
# of niobium-compiler). Override NIOBIUM_COMPILER_ROOT for anything else.
: "${NIOBIUM_COMPILER_ROOT:=$(cd "$CLIENT_ROOT/../.." && pwd)}"

# Build dir used by the compiler's top-level Makefile. Debug uses dbuild/.
: "${NIOBIUM_COMPILER_BUILD:=$NIOBIUM_COMPILER_ROOT/build}"

: "${PORT:=9443}"
: "${BIND:=127.0.0.1}"

SERVER_BIN="$CLIENT_ROOT/build/src/fhetch_transport/nbcc_fhetch_replay_server"
COMPILER_BIN="$NIOBIUM_COMPILER_BUILD/nbcc_fhetch_replay"
OPENFHE_LIB="$CLIENT_ROOT/vendor/lib/openfhe/lib"
NTL_LIB="$NIOBIUM_COMPILER_ROOT/deps/photovoltaic/build/ntl/lib"

for path in "$SERVER_BIN" "$COMPILER_BIN"; do
  if [[ ! -x "$path" ]]; then
    echo "error: not executable: $path" >&2
    exit 2
  fi
done

export LD_LIBRARY_PATH="$OPENFHE_LIB:$NIOBIUM_COMPILER_BUILD:$NTL_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

echo "[fhetch_server.sh] compiler  = $COMPILER_BIN"
echo "[fhetch_server.sh] server    = $SERVER_BIN"
echo "[fhetch_server.sh] bind/port = $BIND:$PORT"
exec "$SERVER_BIN" --port "$PORT" --bind "$BIND" --exec "$COMPILER_BIN" "$@"
