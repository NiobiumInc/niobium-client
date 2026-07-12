#!/usr/bin/env bash
# macOS wheel repair — self-contained wheel (macOS analogue of scripts/repair_wheel.sh).
#
# The extensions, libnbfhetch, and fhetch_sim already reference their deps as
# @rpath/libX (Mach-O). So we bundle all native deps (the OpenFHE dylibs + libnbfhetch)
# next to them and ensure @loader_path is on each artifact's rpath, so @rpath/libX
# resolves to the co-located copy. Then delocate only verifies + tags (excluding the
# libs we bundled) — it can't repair the fhetch_sim executable or the in-wheel companion
# lib via its normal grafting, same as auditwheel on Linux.
#
# Usage:  repair_wheel_macos.sh {wheel} {dest_dir}
#   OPENFHE_INSTALL_DIR overrides the substrate location (default: <cwd>/vendor/lib/openfhe).
if [[ $# -lt 2 ]]; then
    echo "usage: $(basename "$0") {wheel} {dest_dir}" >&2
    echo "  e.g. $(basename "$0") dist/niobium_sdk-*.whl wheelhouse" >&2
    exit 2
fi
set -euxo pipefail

wheel="$1"
dest="$2"
ofhe_lib="${OPENFHE_INSTALL_DIR:-$PWD/vendor/lib/openfhe}/lib"

# Interpreter: honor $PYTHON (e.g. .venv/bin/python), else the repo venv, else PATH.
# Invoking tools as `"$PY" -m …` avoids depending on bare pip/python/delocate-wheel
# being on PATH — they usually aren't for a manual run (macOS has python3, not python).
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
    if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python
    elif command -v python >/dev/null 2>&1; then PY=python
    else PY=python3; fi
fi

"$PY" -m pip install -q wheel delocate

work="$(mktemp -d)"
"$PY" -m wheel unpack -d "$work" "$wheel"
pkg="$(echo "$work"/niobium_sdk-*/niobium_sdk)"

# Bundle the OpenFHE runtime dylibs alongside the extensions (deref symlinks).
for dy in "$ofhe_lib"/libOPENFHE*.1.dylib; do
    cp -L "$dy" "$pkg"/
done

# Ensure @loader_path is on every native artifact's rpath so @rpath/libX resolves
# to the co-located copy (add is a no-op/harmless error if already present).
for f in "$pkg"/*.so "$pkg"/*.dylib "$pkg"/fhetch_sim; do
    [ -f "$f" ] || continue
    install_name_tool -add_rpath @loader_path "$f" 2>/dev/null || true
done

repacked="$(mktemp -d)"
"$PY" -m wheel pack -d "$repacked" "$work"/niobium_sdk-*/

# Verify + tag only; the bundled libs are already in place (@loader_path), so exclude
# them from delocate's grafting/renaming. Invoke delocate via -m so it doesn't need to
# be on PATH (only importable in $PY's env, which the pip install above guarantees).
"$PY" -m delocate.cmd.delocate_wheel --exclude libnbfhetch --exclude libOPENFHE -w "$dest" "$repacked"/*.whl
