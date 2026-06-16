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
"""Plaintext preprocessing for the set-membership example.

Encodes the server's name dataset and the client's query name into the binary
matrices the DSL stages load (everything string-shaped happens here; the FHE
circuit only sees fixed-length numeric vectors):

  io/<profile>/dataset.bin — row-major float64, (padded_rows x L); one encoded
      name per row, rows zero-padded to a multiple of n_slots (a zero row can
      never match a real query: real first characters encode to 1..26).
  io/<profile>/query.bin   — float64 (n_slots x L): the encoded query
      replicated down each column, so each column encrypts to a ciphertext
      with that position's character code in every SIMD slot.

Encoding (mirrors the original openfhe-set-membership preprocessing):
  Exact   — lowercase, strip non-letters, a-z -> 1..26, pad/truncate to L=20.
  Soundex — phonetic hash (letter + 3 digits, e.g. Robert -> R163), L=4.

Prints the cleartext expected match count (the FHE score the client should
decrypt) so tests can pass it to decrypt_verify.

Usage:
  encode_names.py PROFILE --query NAME [--dataset FILE]
    PROFILE: 0=Exact (toy)  1=Soundex (toy)  2=ExactFull
  Without --dataset, a deterministic built-in sample of common first+last
  name combinations is used.
"""
import argparse
import struct
import sys
from pathlib import Path

# (profile_name, n_slots, name_len) per profile — must mirror shared.niob
PROFILES = {
    0: ("exact", 1024, 20),
    1: ("soundex", 1024, 4),
    2: ("exact_full", 32768, 20),
}

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent


# ── Name encoding (exact + Soundex) ─────────────────────────────────────

def letters_only(s: str) -> str:
    return "".join(c for c in s if c.isalpha())


SOUNDEX_CODES = {
    **dict.fromkeys("BFPV", "1"), **dict.fromkeys("CGJKQSXZ", "2"),
    **dict.fromkeys("DT", "3"), "L": "4", **dict.fromkeys("MN", "5"), "R": "6",
}


def soundex(name: str) -> str:
    alpha = letters_only(name)
    if not alpha:
        return "A000"
    result = alpha[0].upper()
    prev = SOUNDEX_CODES.get(alpha[0].upper(), "0")
    for c in alpha[1:]:
        if len(result) >= 4:
            break
        code = SOUNDEX_CODES.get(c.upper(), "0")
        if code != "0" and code != prev:
            result += code
        prev = code
    return (result + "000")[:4]


def encode_char(c: str) -> int:
    if "a" <= c <= "z":
        return ord(c) - ord("a") + 1
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 1
    if "0" <= c <= "9":
        return ord(c) - ord("0")
    return 0


def encode_name(name: str, soundex_mode: bool, name_len: int) -> list[int]:
    processed = soundex(name) if soundex_mode else letters_only(name).lower()
    enc = [0] * name_len
    for i, c in enumerate(processed[:name_len]):
        enc[i] = encode_char(c)
    return enc


# ── Built-in sample dataset ──────────────────────────────────────────────
# Deterministic combinations of common US first/last names (public census
# data) — a stand-in for the server's private dataset.

FIRST = ["James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael",
         "Linda", "David", "Elizabeth", "William", "Barbara", "Richard",
         "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen"]
LAST = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
        "Davis", "Rodriguez", "Martinez"]


def sample_names() -> list[str]:
    return [f"{FIRST[i % len(FIRST)]} {LAST[(i // len(FIRST)) % len(LAST)]}"
            for i in range(188)]


# ── Binary writers ───────────────────────────────────────────────────────

def write_doubles(path: Path, rows: list[list[float]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        for r in rows:
            f.write(struct.pack(f"<{len(r)}d", *r))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("profile", type=int, choices=sorted(PROFILES))
    ap.add_argument("--query", required=True, help="name to search for")
    ap.add_argument("--dataset", help="text file, one name per line "
                                      "(default: built-in sample)")
    args = ap.parse_args()

    pname, n_slots, name_len = PROFILES[args.profile]
    soundex_mode = args.profile == 1
    iodir = EXAMPLE_ROOT / "io" / pname

    if args.dataset:
        names = [ln.strip() for ln in open(args.dataset) if ln.strip()]
    else:
        names = sample_names()

    encoded = [encode_name(n, soundex_mode, name_len) for n in names]
    query = encode_name(args.query, soundex_mode, name_len)

    # Dataset: zero-pad rows to a multiple of n_slots
    padded = -(-len(encoded) // n_slots) * n_slots
    rows = [[float(v) for v in e] for e in encoded]
    rows += [[0.0] * name_len] * (padded - len(encoded))
    write_doubles(iodir / "dataset.bin", rows)

    # Query: replicated to n_slots rows
    write_doubles(iodir / "query.bin", [[float(v) for v in query]] * n_slots)

    expected = sum(1 for e in encoded if e == query)
    mode = "soundex" if soundex_mode else "exact"
    print(f"encoded {len(names)} names ({padded} padded rows, {mode}, L={name_len}) "
          f"-> {iodir}")
    print(f"query {args.query!r} expected matches: {expected}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
