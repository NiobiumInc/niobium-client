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
    add_bool("no_ring_dim_check", args.no_ring_dim_check)

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

    # fhetch_driver mode — on cache-valid runs, replay the recorded trace via
    # fhetch_driver (which loads inputs from the recorded .bin/.ids files at
    # their record-time FHETCH addresses) instead of re-executing the target
    # binary. Works around the auto-facade's cross-process allocator
    # divergence: re-running the user code in autoscheme mode assigns inputs
    # different addresses than the trace expects.
    parser.add_argument("--fhetch-driver", default=None, metavar="PATH",
                        help="Path to fhetch_driver binary. When set and the "
                             "trace already exists, exec the driver instead of "
                             "the target binary (cache-valid replay path).")
    parser.add_argument("--driver-cc", default=None, metavar="PATH",
                        help="Path to cc.bin (forwarded to fhetch_driver --cc)")
    parser.add_argument("--driver-ring-dim", default=None, metavar="N",
                        help="Ring dimension (forwarded to fhetch_driver --ring-dim)")
    parser.add_argument("--driver-output", action="append", metavar="NAME:PATH",
                        help="Output ciphertext to reconstruct (NAME:PATH, repeatable; "
                             "forwarded to fhetch_driver --output-ct)")

    # Compiler flags
    # "local" keeps recording + replay in-process via the FHETCH simulator.
    # Any other value (e.g. "FUNC_SIM", "fpga5.2") tells libnbfhetch's
    # Compiler::replay() to dispatch to the compiler-side nbcc_fhetch_replay
    # executable — which is absent from client-only test environments, so the
    # safe default has to be "local".
    parser.add_argument("--target", default="local",
                        help="Target (default: local — in-process FHETCH sim; "
                             "non-local values dispatch to nbcc_fhetch_replay)")
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
    parser.add_argument("--no-ring-dim-check", action="store_true", default=None,
                        help="Skip the Niobium hardware ring-dimension check")

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

    # If --fhetch-driver was supplied and the trace already exists for this
    # program name, dispatch to the driver to replay the recorded workload
    # instead of re-executing the target binary. The driver reads .bin/.ids
    # files written during recording and uses those addresses directly,
    # sidestepping the auto-facade's allocator divergence on cross-process
    # replay.
    if args.fhetch_driver:
        program_dir = os.path.join(".", args.name)
        trace_file = os.path.join(program_dir, f"{args.name}.fhetch")
        if os.path.exists(trace_file):
            if not args.driver_cc:
                sys.stderr.write("nbcc.py: --fhetch-driver requires --driver-cc\n")
                sys.exit(2)
            if not args.driver_ring_dim:
                sys.stderr.write("nbcc.py: --fhetch-driver requires --driver-ring-dim\n")
                sys.exit(2)
            driver_cmd = [
                args.fhetch_driver,
                trace_file,
                "--ring-dim", args.driver_ring_dim,
                "--source-dir", program_dir,
                "--cc", args.driver_cc,
            ]
            for spec in args.driver_output or []:
                driver_cmd += ["--output-ct", spec]
            print(f"nbcc.py: cache hit, exec fhetch_driver: {' '.join(driver_cmd)}")
            os.execvp(args.fhetch_driver, driver_cmd)

    executable = args.command[0]
    os.execvp(executable, args.command)


if __name__ == "__main__":
    main()
