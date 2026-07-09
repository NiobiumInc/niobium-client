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
set -euxo pipefail

wheel="$1"
dest="$2"
ofhe_lib="${OPENFHE_INSTALL_DIR:-$PWD/vendor/lib/openfhe}/lib"

pip install -q wheel delocate

work="$(mktemp -d)"
python -m wheel unpack -d "$work" "$wheel"
pkg="$(echo "$work"/niobium_client-*/niobium_client)"

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
python -m wheel pack -d "$repacked" "$work"/niobium_client-*/

# Verify + tag only; the bundled libs are already in place (@loader_path), so exclude
# them from delocate's grafting/renaming.
delocate-wheel --exclude libnbfhetch --exclude libOPENFHE -w "$dest" "$repacked"/*.whl
