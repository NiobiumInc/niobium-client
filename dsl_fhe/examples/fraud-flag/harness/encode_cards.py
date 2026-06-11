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
"""Plaintext side of the fraud-flag example (skill Stage 3: ground truth).

Generates a deterministic flagged-card list, encodes cards as fixed-length
digit vectors, writes the binary matrices the DSL stages load, and computes
the cleartext expected match count the decrypt stage verifies against.

Encoding: each of the 16 digits d -> d+1 (values 1..10), so the zero rows
used to pad the last batch can never match a real query.

  io/<profile>/flagged.bin — row-major float64 (padded_rows x 16); the
      server's private list, rows zero-padded to a multiple of n_slots
  io/<profile>/query.bin   — float64 (n_slots x 16): the encoded query card
      replicated down each column

Usage:
  encode_cards.py PROFILE (--query-flagged INDEX | --query-card DIGITS)
    PROFILE: 0=Toy (1,024 slots)  1=Full (16,384 slots)
    --query-flagged N  query the N-th card OF the flagged list (expect 1)
    --query-card D...  query an explicit 16-digit card (expect 0 or 1)
"""
import argparse
import random
import struct
import sys
from pathlib import Path

PROFILES = {0: ("toy", 1024), 1: ("full", 16384)}
N_DIGITS = 16
N_FLAGGED = 5000
SEED = 42

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent


def gen_flagged() -> list[str]:
    rng = random.Random(SEED)
    cards = set()
    while len(cards) < N_FLAGGED:
        cards.add("".join(rng.choice("0123456789") for _ in range(N_DIGITS)))
    return sorted(cards)


def encode(card: str) -> list[float]:
    return [float(int(c) + 1) for c in card]


def write_doubles(path: Path, rows: list[list[float]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for r in rows:
            f.write(struct.pack(f"<{len(r)}d", *r))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile", type=int, choices=sorted(PROFILES))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--query-flagged", type=int,
                   help="query the N-th flagged card (expect 1)")
    g.add_argument("--query-card",
                   help="explicit 16-digit card number")
    args = ap.parse_args()

    pname, n_slots = PROFILES[args.profile]
    iodir = EXAMPLE_ROOT / "io" / pname

    flagged = gen_flagged()
    if args.query_flagged is not None:
        query = flagged[args.query_flagged % len(flagged)]
    else:
        query = args.query_card
        if len(query) != N_DIGITS or not query.isdigit():
            sys.exit(f"--query-card must be {N_DIGITS} digits")

    enc_flagged = [encode(c) for c in flagged]
    enc_query = encode(query)

    padded = -(-len(enc_flagged) // n_slots) * n_slots
    rows = enc_flagged + [[0.0] * N_DIGITS] * (padded - len(enc_flagged))
    write_doubles(iodir / "flagged.bin", rows)
    write_doubles(iodir / "query.bin", [enc_query] * n_slots)

    expected = sum(1 for e in enc_flagged if e == enc_query)
    print(f"encoded {len(flagged)} flagged cards "
          f"({padded} padded rows, {padded // n_slots} batches) -> {iodir}")
    print(f"query {query} expected matches: {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
