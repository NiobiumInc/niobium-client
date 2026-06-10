#!/usr/bin/env python3
"""Relicense vendored support files under the niobium-client open-source license.

The fetch-by-similarity example vendors external support sources (C++:
utils.h, slot_replication.*, running_sums.*; Python harness scripts) that
originally shipped carrying a dual "Apache-2.0 OR Niobium proprietary" header.
This repo is open source, so we replace that Niobium dual-license block with the
same plain Apache-2.0 header the rest of the client uses (see vendor/niobium-fhetch
headers). Any third-party attribution that follows (e.g. an Amazon Web Services
notice) and any shebang line are left untouched.

Handles both C++ (`//`) and Python/Make (`#`) comment styles. Idempotent: files
without the Niobium block are left unchanged, so it is safe to run on every copy.

Usage: apply_client_license.py FILE [FILE ...]
"""

from __future__ import annotations

import sys

# First and last line (sans comment prefix) of the Niobium dual-license block.
NIOBIUM_COPYRIGHT = "Copyright (C) 2023-2026 Niobium Microsystems, Inc."
NIOBIUM_BLOCK_END = "from the Product."

# The plain Apache-2.0 notice used across the niobium-client sources.
_LICENSE_BODY = [
    "Copyright 2024-present Niobium Microsystems, Inc.",
    "",
    'Licensed under the Apache License, Version 2.0 (the "License");',
    "you may not use this file except in compliance with the License.",
    "You may obtain a copy of the License at",
    "",
    "    http://www.apache.org/licenses/LICENSE-2.0",
    "",
    "Unless required by applicable law or agreed to in writing, software",
    'distributed under the License is distributed on an "AS IS" BASIS,',
    "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.",
    "See the License for the specific language governing permissions and",
    "limitations under the License.",
]


def _header(prefix: str) -> str:
    return "".join((prefix + " " + line).rstrip() + "\n" for line in _LICENSE_BODY)


def relicense(text: str) -> str | None:
    """Return the relicensed text, or None if there is nothing to change."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return None

    idx = 0
    shebang = ""
    if lines[0].startswith("#!"):
        shebang = lines[0]
        idx = 1

    first = lines[idx].rstrip("\n") if idx < len(lines) else ""
    if first == "// " + NIOBIUM_COPYRIGHT:
        prefix = "//"
    elif first == "# " + NIOBIUM_COPYRIGHT:
        prefix = "#"
    else:
        return None  # no Niobium block at the top — nothing to do

    end_line = prefix + " " + NIOBIUM_BLOCK_END
    end = None
    for i in range(idx, len(lines)):
        if lines[i].rstrip("\n") == end_line:
            end = i
            break
    if end is None:
        return None  # malformed / unrecognized; leave it alone

    return shebang + _header(prefix) + "".join(lines[end + 1:])


def main(argv: list[str]) -> int:
    for path in argv:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            print(f"apply_client_license: cannot read {path}: {e}", file=sys.stderr)
            continue
        new_text = relicense(text)
        if new_text is None or new_text == text:
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
        print(f"apply_client_license: relicensed {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
