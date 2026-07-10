#!/usr/bin/env bash
# Linux wheel repair for niobium_client — produce a self-contained manylinux wheel.
#
# auditwheel's default grafting doesn't fit our layout: it can't resolve the
# in-wheel companion lib (libnbfhetch, found by the extensions via $ORIGIN) and it
# won't repair the bundled fhetch_sim *executable* (it only rewrites .so's, and it
# renames grafted libs so an executable can't follow them). So instead we bundle
# ALL native deps (libnbfhetch + the OpenFHE runtime libs) next to the extensions +
# fhetch_sim, set every native artifact's RUNPATH to $ORIGIN so they resolve
# co-located, then run auditwheel only to *retag* manylinux — excluding the libs we
# bundled so it doesn't try to graft/rename them.
#
# Invoked from cibuildwheel's repair-wheel-command:  repair_wheel.sh {wheel} {dest_dir}
if [[ $# -lt 2 ]]; then
    echo "usage: $(basename "$0") {wheel} {dest_dir}" >&2
    exit 2
fi
set -euxo pipefail

wheel="$1"
dest="$2"
ofhe_lib="/project/vendor/lib/openfhe/lib"   # cibuildwheel mounts the project at /project

# Interpreter: cibuildwheel puts the target python on PATH; honor $PYTHON if set.
# (No .venv fallback here — this runs inside the manylinux container, where a mounted
# host venv would be the wrong architecture. patchelf + auditwheel come from the image.)
PY="${PYTHON:-python}"

"$PY" -m pip install -q wheel   # the `wheel` CLI unpack/pack regenerates RECORD after patchelf

work="$(mktemp -d)"
"$PY" -m wheel unpack -d "$work" "$wheel"
pkg="$(echo "$work"/niobium_client-*/niobium_client)"

# Bundle the OpenFHE runtime sonames alongside the extensions (deref symlinks).
for so in "$ofhe_lib"/libOPENFHE*.so.1; do
    cp -L "$so" "$pkg"/
done

# RUNPATH=$ORIGIN on every native artifact so it finds its co-located deps.
for f in "$pkg"/*.so "$pkg"/*.so.* "$pkg"/fhetch_sim; do
    [ -f "$f" ] || continue
    patchelf --set-rpath '$ORIGIN' "$f"
done

repacked="$(mktemp -d)"
"$PY" -m wheel pack -d "$repacked" "$work"/niobium_client-*/

# Retag manylinux only. The bundled libs are already in place ($ORIGIN), so exclude
# them from grafting/renaming; auditwheel just verifies the extensions' glibc + tags.
auditwheel repair \
    --exclude libnbfhetch.so.1 \
    --exclude libOPENFHEcore.so.1 \
    --exclude libOPENFHEpke.so.1 \
    --exclude libOPENFHEbinfhe.so.1 \
    -w "$dest" "$repacked"/*.whl
