#!/usr/bin/env python3
"""nbcc.py — Niobium Compiler Configuration wrapper.

Generates a deterministic .niobium_compiler.yml from CLI flags,
sets NIOBIUM_CONFIG to point to it, and execs the target program.

Usage:
    nbcc.py --name NAME [options] -- EXECUTABLE [ARGS...]

The generated yml is written to .nbcc/<hash>.yml in CWD, where <hash>
is derived from the yml content. Identical CLI flags produce the same
file, so record and replay runs find the same config automatically.
"""

import argparse
import hashlib
import os
import sys


def build_yaml(args):
    """Build a YAML string from parsed args (no pyyaml dependency)."""
    lines = []

    # program section
    lines.append("program:")
    lines.append(f"  name: {args.name!r}")
    if args.version:
        lines.append(f"  version: {args.version!r}")
    if args.description:
        lines.append(f"  description: {args.description!r}")

    # cache_parameters section
    if args.cache:
        lines.append("cache_parameters:")
        for pair in args.cache:
            k, v = pair.split("=", 1)
            lines.append(f"  {k}: {v!r}")

    # keys section
    if args.keys_mult or args.keys_auto:
        lines.append("keys:")
        if args.keys_mult:
            lines.append(f"  eval_mult: {args.keys_mult!r}")
        if args.keys_auto:
            lines.append(f"  eval_automorphism: {args.keys_auto!r}")

    # compiler section — only emit keys that were explicitly set
    compiler_lines = []

    def add_value(key, value):
        if value is not None:
            compiler_lines.append(f"  {key}: {value!r}")

    def add_bool(key, value):
        if value is not None:
            compiler_lines.append(f"  {key}: {'true' if value else 'false'}")

    add_value("target", args.target)
    add_value("optimization", args.optimization)
    add_value("registers", args.registers)
    add_value("memory", args.memory)
    add_bool("niobium_hw", args.niobium_hw)
    add_bool("hollow", args.hollow)
    # fence: --fence sets True, --no-fence sets False, neither sets None
    add_bool("fence", args.fence)
    add_value("noop", args.noop)
    add_value("multiplier", args.multiplier)
    add_value("config_sectors", args.config_sectors)
    add_bool("binary_json", args.binary_json)
    add_bool("no_cereal_binary", args.no_cereal_binary)
    add_bool("transform_bin_to_json", args.transform_bin_to_json)
    add_bool("no_preserve_input_ciphertexts", args.no_preserve_input_ciphertexts)
    add_bool("formal", args.formal)
    add_bool("lock_timing", args.lock_timing)

    if compiler_lines:
        lines.append("compiler:")
        lines.extend(compiler_lines)

    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Niobium Compiler Configuration wrapper",
        usage="%(prog)s --name NAME [options] -- EXECUTABLE [ARGS...]",
    )

    # Program
    parser.add_argument("--name", required=True, help="Program name")
    parser.add_argument("--version", default=None, help="Program version")
    parser.add_argument("--description", default=None, help="Program description")

    # Cache parameters (repeatable)
    parser.add_argument("--cache", action="append", metavar="KEY=VALUE",
                        help="Cache parameter (repeatable)")

    # Keys
    parser.add_argument("--keys-mult", default=None, help="Path to eval mult key")
    parser.add_argument("--keys-auto", default=None, help="Path to eval automorphism key")

    # Compiler flags
    parser.add_argument("--target", default="FUNC_SIM",
                        help="Target (default: FUNC_SIM)")
    parser.add_argument("-O", "--optimization", default=None,
                        help="Optimization level")
    parser.add_argument("--registers", default=None, help="Number of registers")
    parser.add_argument("--memory", default=None, help="Memory in GB")
    parser.add_argument("--niobium-hw", action="store_true", default=None,
                        help="Enable niobium hardware mode")
    parser.add_argument("--hollow", action="store_true", default=None,
                        help="Enable hollow mode")

    # Fence (mutually exclusive)
    fence_group = parser.add_mutually_exclusive_group()
    fence_group.add_argument("--fence", action="store_true", dest="fence", default=None)
    fence_group.add_argument("--no-fence", action="store_false", dest="fence")

    parser.add_argument("--noop", default=None, help="Noop value")
    parser.add_argument("--multiplier", default=None,
                        choices=["standard", "shoup"], help="Multiplier type")
    parser.add_argument("--config-sectors", default=None, help="Config sectors")

    # Binary/JSON (mutually exclusive)
    json_group = parser.add_mutually_exclusive_group()
    json_group.add_argument("--binary-json", action="store_true",
                            dest="binary_json", default=None)
    json_group.add_argument("--ascii-json", action="store_false",
                            dest="binary_json")

    parser.add_argument("--no-cereal-binary", action="store_true", default=None)
    parser.add_argument("--transform-bin-to-json", action="store_true", default=None)
    parser.add_argument("--no-preserve-input-ciphertexts", action="store_true",
                        default=None)
    parser.add_argument("--formal", action="store_true", default=None)
    parser.add_argument("--lock-timing", action="store_true", default=None)

    # Positional: everything after --
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="EXECUTABLE [ARGS...] (after --)")

    args = parser.parse_args()

    # Strip leading '--' from command
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("No command specified. Use: nbcc.py [options] -- EXECUTABLE [ARGS...]")
    args.command = cmd

    return args


def main():
    args = parse_args()

    yaml_content = build_yaml(args)

    # Deterministic filename from content hash
    digest = hashlib.sha256(yaml_content.encode()).hexdigest()[:16]
    nbcc_dir = os.path.join(".", ".nbcc")
    os.makedirs(nbcc_dir, exist_ok=True)
    yml_path = os.path.join(nbcc_dir, f"{digest}.yml")

    with open(yml_path, "w") as f:
        f.write(yaml_content)

    # Set env and exec
    os.environ["NIOBIUM_CONFIG"] = yml_path
    executable = args.command[0]
    os.execvp(executable, args.command)


if __name__ == "__main__":
    main()
