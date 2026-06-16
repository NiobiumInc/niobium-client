#!/usr/bin/env python3
# Copyright 2024-present Niobium Microsystems, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Write STUB KitNET model + dataset assets so the fhe-NetworkMonitor example is
self-contained and builds/runs without the real trained model.

These are NOT the real KitNET ensemble or Mirai traffic — they are minimal,
structurally-valid zero-filled stand-ins. Paired with the example's DSL the
pipeline builds and runs end-to-end (keygen + encrypt + encrypted inference)
without producing meaningful anomaly scores. For real detection, fetch the
trained model + dataset from the fhe-NetworkMonitor submission package (see
README.md in this directory).

The binary layout mirrors what the generated `load_kitnet_model()` and
`read2vecs<double>()` expect (see nb_out/nb_shared.{h,cpp}).

Usage: make_stub_assets.py [PROFILE]   PROFILE in {TOY, MINI, FULL} (default TOY)
"""
import struct
import sys
from pathlib import Path

# profile -> (n_features, n_slots). Mirrors instance() in shared.niob.
PROFILES = {
    "TOY":  (2, 1024),
    "MINI": (5, 32768),
    "FULL": (50, 32768),
}

# Chebyshev approx order recorded in the header. The DSL recomputes Chebyshev
# coefficients itself (CHEB_ORDER), so these coefficient values are unused — only
# the count (apx_ord+1) matters for parsing.
APX_ORD = 5


def build_model(n_features: int) -> bytes:
    num_ae = 1
    vis_ae = n_features          # one autoencoder over all features
    hid_ae = 1
    vis_ad = num_ae * vis_ae     # detector sees one residual per (ae, feature)
    hid_ad = 1

    out = bytearray()
    # Header: 7 x uint16
    out += struct.pack("<7H", num_ae, n_features, vis_ae, hid_ae, vis_ad, hid_ad, APX_ORD)
    # sigmoid + tanh Chebyshev coeffs: (apx_ord+1) doubles each (values unused)
    out += struct.pack(f"<{APX_ORD + 1}d", *([0.0] * (APX_ORD + 1)))
    out += struct.pack(f"<{APX_ORD + 1}d", *([0.0] * (APX_ORD + 1)))
    # feature_map: num_ae x vis_ae uint16 — AE 0 maps to features [0..vis_ae)
    for _ in range(num_ae):
        out += struct.pack(f"<{vis_ae}H", *range(vis_ae))
    # ensemble: per AE -> W[vis_ae*hid_ae], hbias[hid_ae], rbias[vis_ae] doubles
    for _ in range(num_ae):
        out += struct.pack(f"<{vis_ae * hid_ae}d", *([0.0] * (vis_ae * hid_ae)))
        out += struct.pack(f"<{hid_ae}d", *([0.0] * hid_ae))
        out += struct.pack(f"<{vis_ae}d", *([0.0] * vis_ae))
    # detector: W[vis_ad*hid_ad], hbias[hid_ad], rbias[vis_ad] doubles
    out += struct.pack(f"<{vis_ad * hid_ad}d", *([0.0] * (vis_ad * hid_ad)))
    out += struct.pack(f"<{hid_ad}d", *([0.0] * hid_ad))
    out += struct.pack(f"<{vis_ad}d", *([0.0] * vis_ad))
    return bytes(out)


def main(argv):
    profile = (argv[0] if argv else "TOY").upper()
    if profile not in PROFILES:
        print(f"unknown profile {profile!r}; choose from {list(PROFILES)}", file=sys.stderr)
        return 2
    n_features, n_slots = PROFILES[profile]
    here = Path(__file__).resolve().parent
    (here / "models").mkdir(parents=True, exist_ok=True)
    (here / "datasets").mkdir(parents=True, exist_ok=True)

    model_path = here / "models" / f"Mirai_model_{profile}.bin"
    model_path.write_bytes(build_model(n_features))
    print(f"wrote stub model {model_path} (profile {profile}, {n_features} features)")

    # Dataset: n_slots packets x n_features doubles (zeros). read2vecs derives the
    # record count from the file size, and encrypt asserts size >= n_slots.
    data_path = here / "datasets" / "Mirai_first_batch_32K.bin"
    data_path.write_bytes(struct.pack(f"<{n_slots * n_features}d",
                                      *([0.0] * (n_slots * n_features))))
    print(f"wrote stub dataset {data_path} ({n_slots} packets x {n_features} features)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
