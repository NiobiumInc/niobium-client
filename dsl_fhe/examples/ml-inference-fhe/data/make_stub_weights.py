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
"""Write zero-filled STUB MLP weights so the ml-inference example is
self-contained and links/runs without the real trained model.

These are NOT real weights — paired with the stub mnist() (mlp_openfhe.cpp),
they let the pipeline build and run without producing meaningful inference.
For real results, fetch the trained weights from the ml-inference submission
package (see README.md in this directory) and point ML_WEIGHT_DIR at them.

Usage: make_stub_weights.py [OUTPUT_DIR]   (default: this directory)
"""
import struct
import sys
from pathlib import Path

# (filename, float count) — 784->512->10 MLP.
WEIGHTS = [
    ("fc1_weight.bin", 512 * 784),
    ("fc1_bias.bin", 512),
    ("fc2_weight.bin", 10 * 512),
    ("fc2_bias.bin", 10),
]


def main(argv):
    out = Path(argv[0]) if argv else Path(__file__).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    for name, count in WEIGHTS:
        path = out / name
        with open(path, "wb") as f:
            f.write(struct.pack(f"<{count}f", *([0.0] * count)))
        print(f"wrote stub {path} ({count} float32 zeros)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
