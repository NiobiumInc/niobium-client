#!/usr/bin/env bash
# ============================================================================
# compiler_ops.sh SUITE — run an ops example with its trace replayed through
# the compiler: client → server --target=$TARGET → decrypt, per case.
#
#   SUITE: plaintext_add | simple_ops | simple_ops_rotation | matrix_ops
#
# Used by the compiler-ops-tests job in .github/workflows/compiler-tests.yml.
# That repo is public, so this prints ONLY a PASS/FAIL line per case (and the
# stage that failed, with its exit code). Every example's stdout and stderr go
# to /dev/null, because the server's carries the compiler's output. Reproduce
# a failure locally to see it.
#
# The cases mirror the Makefile's local-replay targets (test-plaintext-add-release,
# test-simple-ops-release, test-simple-ops-rotation-matrix-release,
# test-matrix-ops-release); keep the lists in step.
#
# With a compiler target, a case passes only if the replay really went through
# nbcc_fhetch_replay: its output directory (nbcc_fhetch_replay_source_*, which
# only the compiler writes) must exist after the server ran. The check prints
# nothing of the compiler's, so the log stays PASS/FAIL only.
#
# Env:
#   TARGET                 replay target (default FUNC_SIM). "local" replays
#                          through fhetch_sim as a subprocess (NBCC_FHETCH_SIM or
#                          PATH), which exercises this script without a compiler.
#   NIOBIUM_COMPILER_ROOT  where build/nbcc_fhetch_replay lives (not for "local").
#   RING_DIM               client ring dimension (default 2048, as TEST_RING_DIM).
#   STEP_TIMEOUT           seconds per client/server/decrypt run (default 900).
#   WORK_DIR               where keys and traces go (default: a new temp dir,
#                          removed when every case passed and kept, with its
#                          path printed, when one failed).
#
# Exit: 0 all cases passed, 1 a case failed, 2 bad usage or missing binaries.
# ============================================================================
# No -e on purpose: a failing case must not stop the run, run_case counts it.
# -u still stops on an unset variable or array, so a typo cannot pass silently.
set -uo pipefail

suite="${1:-}"
case "$suite" in
  plaintext_add|simple_ops|matrix_ops) example="$suite" ;;
  simple_ops_rotation) example=simple_ops ;;
  *) echo "usage: $0 plaintext_add|simple_ops|simple_ops_rotation|matrix_ops" >&2; exit 2 ;;
esac

: "${TARGET:=FUNC_SIM}"
: "${RING_DIM:=2048}"
: "${STEP_TIMEOUT:=900}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$ROOT/build/examples"

for b in "${example}_client" "${example}_server" "${example}_decrypt"; do
  [[ -x "$BIN/$b" ]] || { echo "::error::not built: build/examples/$b (run make release)"; exit 2; }
done

REPLAY=""
if [[ "$TARGET" != "local" ]]; then
  REPLAY="${NIOBIUM_COMPILER_ROOT:-}/build/nbcc_fhetch_replay"
  [[ -x "$REPLAY" ]] || { echo "::error::nbcc_fhetch_replay not found under NIOBIUM_COMPILER_ROOT"; exit 2; }
fi

total=0
fail=0

if [[ -n "${WORK_DIR:-}" ]]; then
  WORK="$WORK_DIR"
  mkdir -p "$WORK"
else
  WORK="$(mktemp -d)"
  # Our own temp dir: drop it on success, keep it to debug a failure.
  trap '[[ $fail -eq 0 ]] && rm -r "$WORK" || echo "work dir kept: $WORK"' EXIT
fi

# Quiet run with a time limit; the exit status is the example's (124 = timeout).
quiet() { timeout "$STEP_TIMEOUT" "$@" >/dev/null 2>&1; }

# The server is the only step that reaches the compiler. Same environment as
# test-mult-target-release: NBCC_FHETCH_REPLAY names the binary, and
# LD_LIBRARY_PATH is dropped so the replay binary resolves its own libraries.
server() {
  NBCC_FHETCH_REPLAY="$REPLAY" timeout "$STEP_TIMEOUT" \
    env -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH \
    "$@" --no-ring-dim-check --target="$TARGET" >/dev/null 2>&1
}

# run_case LABEL — runs the CLIENT, SERVER and DECRYPT arrays in a fresh
# directory and prints one line.
run_case() {
  local label="$1" stage="" rc=0 dir
  dir="$(mktemp -d "$WORK/case.XXXXXX")"
  pushd "$dir" >/dev/null || exit 2
  quiet "${CLIENT[@]}"   || { rc=$?; stage=client; }
  [[ -z "$stage" ]] && { server "${SERVER[@]}" || { rc=$?; stage=server; }; }
  if [[ -z "$stage" && "$TARGET" != "local" ]] \
     && [[ -z "$(find . -maxdepth 2 -type d -name 'nbcc_fhetch_replay_source_*' -print -quit)" ]]; then
    stage=replay
  fi
  [[ -z "$stage" ]] && { quiet "${DECRYPT[@]}" || { rc=$?; stage=decrypt; }; }
  popd >/dev/null || exit 2
  total=$((total + 1))
  if [[ -z "$stage" ]]; then
    echo "$suite $label: PASS"
  elif [[ "$stage" == replay ]]; then
    fail=$((fail + 1))
    echo "$suite $label: FAIL (server passed but left no nbcc_fhetch_replay output: not replayed by the compiler)"
  else
    fail=$((fail + 1))
    echo "$suite $label: FAIL ($stage exited $rc$([[ $rc -eq 124 ]] && echo ', timeout'))"
  fi
}

case "$suite" in
  plaintext_add)
    CLIENT=("$BIN/plaintext_add_client" keys "$RING_DIM")
    SERVER=("$BIN/plaintext_add_server" keys)
    DECRYPT=("$BIN/plaintext_add_decrypt" keys)
    run_case default
    ;;

  simple_ops)
    # Same ops and operands (5, 6) as test-simple-ops-release.
    for op in ADD SUB NEG ADDI SUBI MULI ADD_ADD ADD_SUB MUL MUL_ADD ADD_MUL MUL_MUL \
              ALL_NO_MUL ADD_NEG NEG_MUL_ADD ADD_NEG_MUL_ADD MAT_PATTERN MULI_ADDI \
              ADDI_MULI MULI_ADD ROTATE_ADD ADD_ROTATE ROTATE_MUL ROTATE_ROTATE \
              MULI_ROT_ADD_MUL_SUBI MORPH; do
      CLIENT=("$BIN/simple_ops_client" keys 5 6 "$RING_DIM")
      SERVER=("$BIN/simple_ops_server" keys "$op")
      DECRYPT=("$BIN/simple_ops_decrypt" keys "$op")
      run_case "$op"
    done
    ;;

  simple_ops_rotation)
    # Same sweep as test-simple-ops-rotation-matrix-release: the 5 ops whose
    # rotation amount comes from --immediate × both slot periods × every
    # rotation amount (80 cases). Keys are made per case here, not per period.
    for period in 16 64; do
      for op in ROTATE_ADD ADD_ROTATE ROTATE_MUL ROTATE_ROTATE MULI_ROT_ADD_MUL_SUBI; do
        for rot in 1 -1 2 -2 5 -5 8 -8; do
          CLIENT=("$BIN/simple_ops_client" keys 5 6 "$RING_DIM" --slot-period="$period")
          SERVER=("$BIN/simple_ops_server" keys "$op" --immediate="$rot")
          DECRYPT=("$BIN/simple_ops_decrypt" keys "$op" --immediate="$rot" --slot-period="$period")
          run_case "$op period=$period rot=$rot"
        done
      done
    done
    ;;

  matrix_ops)
    # Same shapes as test-matrix-ops-release: rows1 cols1 rows2 cols2.
    for dims in "2 2 2 2" "4 2 2 3"; do
      read -r -a d <<< "$dims"
      CLIENT=("$BIN/matrix_ops_client" keys "${d[@]}" "$RING_DIM")
      SERVER=("$BIN/matrix_ops_server" keys "${d[@]}")
      DECRYPT=("$BIN/matrix_ops_decrypt" keys "${d[@]}")
      run_case "${dims// /x}"
    done
    ;;
esac

echo "$suite: $((total - fail))/$total passed (target $TARGET)"
[[ $fail -eq 0 ]]
