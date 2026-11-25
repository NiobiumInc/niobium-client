#!/usr/bin/env bash
# ============================================================================
# measure.sh LABEL -- CMD [ARGS...] — run CMD and report what it cost the VM.
#
# For sizing the runner of the compiler-ops-tests job (compiler-tests.yml).
# CMD's own output passes through untouched (the caller decides what may be
# shown); this adds one line of machine numbers, and a row in
# $GITHUB_STEP_SUMMARY when it is set:
#
#   wall      elapsed seconds
#   cpu       user + sys CPU seconds of CMD and its children
#   cpu_util  cpu / (wall × nproc): how much of the VM it kept busy
#   rss_max   peak RSS of the single largest process (GNU time %M). Under
#             make -j this is ONE compiler, not the sum; see mem_peak.
#   mem_peak  peak of system-wide used memory (MemTotal − MemAvailable), sampled
#             every SAMPLE_SECS (default 2), minus what was in use at the start
#   load_peak peak 1-minute load average while CMD ran
#
# Exit status is CMD's.
# ============================================================================
set -uo pipefail

label="${1:?usage: measure.sh LABEL -- CMD [ARGS...]}"
shift
[[ "${1:-}" == "--" ]] && shift
[[ $# -gt 0 ]] || { echo "usage: measure.sh LABEL -- CMD [ARGS...]" >&2; exit 2; }

: "${SAMPLE_SECS:=2}"
command -v /usr/bin/time >/dev/null || { echo "measure.sh: /usr/bin/time missing (apt-get install time)" >&2; exit 2; }

used_mib() { awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{printf "%d\n", (t-a)/1024}' /proc/meminfo; }

tmp="$(mktemp -d)"
base="$(used_mib)"

# Sampler: "<used MiB> <load1>" per line until killed.
( while :; do echo "$(used_mib) $(cut -d' ' -f1 /proc/loadavg)"; sleep "$SAMPLE_SECS"; done ) > "$tmp/samples" &
sampler=$!

/usr/bin/time -f '%e %U %S %M' -o "$tmp/time" "$@"
rc=$?

kill "$sampler" 2>/dev/null
wait "$sampler" 2>/dev/null
echo "$(used_mib) $(cut -d' ' -f1 /proc/loadavg)" >> "$tmp/samples"

# GNU time prepends "Command exited with non-zero status N" on failure; the
# numbers are always the last line.
read -r wall user sys rss_kib < <(tail -n 1 "$tmp/time")
read -r mem_peak load_peak < <(awk -v b="$base" '{ if ($1-b > m) m = $1-b; if ($2 > l) l = $2 } END { printf "%d %.1f\n", m, l }' "$tmp/samples")
ncpu="$(nproc)"
cpu="$(awk -v u="$user" -v s="$sys" 'BEGIN{printf "%.0f", u+s}')"
util="$(awk -v c="$cpu" -v w="$wall" -v n="$ncpu" 'BEGIN{ if (w > 0) printf "%.0f%%", 100*c/(w*n); else print "n/a" }')"
rss_mib=$((rss_kib / 1024))
wall_s="$(awk -v w="$wall" 'BEGIN{printf "%.0f", w}')"
status=$([[ $rc -eq 0 ]] && echo ok || echo "exit $rc")

echo "measure: phase=$label wall=${wall_s}s cpu=${cpu}s cpu_util=$util rss_max=${rss_mib}MiB mem_peak=${mem_peak}MiB load_peak=$load_peak status=$status"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  echo "| $label | ${wall_s} | ${cpu} | $util | ${rss_mib} | ${mem_peak} | $load_peak | $status |" >> "$GITHUB_STEP_SUMMARY"
fi

rm -r "$tmp"
exit "$rc"
