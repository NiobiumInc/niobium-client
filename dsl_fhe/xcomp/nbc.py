#!/usr/bin/env python3
"""nbc — the nb FHE language cross-compiler.

Usage:
    nbc compile file1.niob [file2.niob ...] [--outdir DIR]
    nbc check   file1.niob [file2.niob ...]
    nbc lex     file1.niob
    nbc parse   file1.niob
"""

from __future__ import annotations
import argparse
import sys
import os
import json
from pathlib import Path

from .lexer import lex, Token, TT
from .parser import parse
from .semantic import analyze as semantic_analyze
from .codegen import generate
from .errors import CompileError
from .modules import load_program


# One-line pointer to the design skill. Printed to stderr by check/compile so an
# agent that lands on `nbc` without having read the docs still gets nudged to the
# 8-stage design guide before hand-writing a circuit. See discoverability notes in
# dsl_fhe/AGENTS.md.
_DESIGN_SKILL = ".claude/skills/fhe-application-design/SKILL.md"


def _print_design_banner():
    print(
        f"nbc: building an FHE app? Read the design skill first — {_DESIGN_SKILL} "
        f"(8 stages; don't skip the design work).",
        file=sys.stderr,
    )


def cmd_lex(args):
    """Tokenize input files and print tokens."""
    for path in args.files:
        source = Path(path).read_text()
        try:
            tokens = lex(source, path)
            for tok in tokens:
                if tok.type != TT.EOF:
                    print(f"  {tok.loc!s:20s}  {tok.type.name:16s}  {tok.value!r}")
            print(f"\n{len(tokens) - 1} tokens")
        except CompileError as e:
            print(e.format(), file=sys.stderr)
            return 1
    return 0


def cmd_parse(args):
    """Parse input files and print AST summary."""
    for path in args.files:
        source = Path(path).read_text()
        try:
            tokens = lex(source, path)
            program = parse(tokens)
            _print_ast(program, indent=0)
        except CompileError as e:
            print(e.format(), file=sys.stderr)
            return 1
    return 0


def cmd_check(args):
    """Parse and run semantic analysis on input files."""
    _print_design_banner()
    try:
        program, loader = load_program(args.files, args.module_path)
        sa = semantic_analyze(program)
        if sa.errors.has_errors():
            print(sa.errors.report(), file=sys.stderr)
            return 1
        for w in loader.warnings + sa.errors.warnings:
            print(w, file=sys.stderr)
        for n in sa.errors.notes:
            print(n, file=sys.stderr)
        print(f"OK: {sum(1 for i in program.items)} declarations, "
              f"{len(loader.warnings) + len(sa.errors.warnings)} warnings, 0 errors.")
        return 0
    except CompileError as e:
        print(e.format(), file=sys.stderr)
        return 1


def cmd_compile(args):
    """Compile .niob files to OpenFHE C++."""
    _print_design_banner()
    try:
        program, loader = load_program(args.files, args.module_path)
        sa = semantic_analyze(program)

        if sa.errors.has_errors():
            print(sa.errors.report(), file=sys.stderr)
            return 1

        files = generate(program, sa, backend=args.backend)

        # Warnings from module loading, analysis AND code generation
        # (heuristic fallbacks).
        for w in loader.warnings + sa.errors.warnings:
            print(w, file=sys.stderr)
        for n in sa.errors.notes:
            print(n, file=sys.stderr)

        outdir = Path(args.outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        for fname, content in files.items():
            outpath = outdir / fname
            outpath.write_text(content)
            print(f"  wrote {outpath} ({len(content)} bytes)")

        print(f"\nGenerated {len(files)} file(s) in {outdir}/")
        return 0

    except CompileError as e:
        print(e.format(), file=sys.stderr)
        return 1


def _print_ast(node, indent=0):
    """Pretty-print an AST node."""
    prefix = "  " * indent
    name = type(node).__name__

    # Import to check types
    from . import ast_nodes as ast

    if isinstance(node, ast.Program):
        mod = f" module {node.module_name}" if node.module_name else ""
        print(f"{prefix}Program{mod} ({len(node.items)} items)")
        for item in node.items:
            _print_ast(item, indent + 1)

    elif isinstance(node, ast.UseDecl):
        if node.is_module_import:
            print(f"{prefix}Use module {node.module_path[0]}")
        else:
            path = "::".join(node.module_path)
            print(f"{prefix}Use {path}::{node.imported}")

    elif isinstance(node, ast.ConstDecl):
        print(f"{prefix}Const {node.name}")

    elif isinstance(node, ast.EnumDecl):
        print(f"{prefix}Enum {node.name} [{', '.join(node.variants)}]")

    elif isinstance(node, ast.StructDecl):
        fields = [f.name for f in node.fields]
        print(f"{prefix}Struct {node.name} [{', '.join(fields)}]")

    elif isinstance(node, ast.WireDecl):
        fields = [f.name for f in node.fields]
        print(f"{prefix}Wire {node.name} [{', '.join(fields)}]")

    elif isinstance(node, ast.SchemeDecl):
        print(f"{prefix}Scheme {node.name} ({len(node.fields)} fields)")

    elif isinstance(node, ast.RequiresDecl):
        print(f"{prefix}Requires [{', '.join(node.capabilities)}]")

    elif isinstance(node, ast.DomainDecl):
        print(f"{prefix}Domain {node.name}")

    elif isinstance(node, ast.FnDecl):
        anns = " ".join(f"@{a.name}" for a in node.annotations)
        params = ", ".join(p.name for p in node.params)
        print(f"{prefix}Fn {anns} {node.name}({params})")
        if node.body:
            print(f"{prefix}  body: {len(node.body.stmts)} statements")

    else:
        print(f"{prefix}{name}")


def main():
    parser = argparse.ArgumentParser(
        prog="nbc",
        description="nb FHE language cross-compiler",
    )
    subparsers = parser.add_subparsers(dest="command")

    # lex
    p_lex = subparsers.add_parser("lex", help="Tokenize and print tokens")
    p_lex.add_argument("files", nargs="+", help="Input .niob files")

    # parse
    p_parse = subparsers.add_parser("parse", help="Parse and print AST")
    p_parse.add_argument("files", nargs="+", help="Input .niob files")

    # The module search path: where `use <module>` finds the files declaring
    # `module <name>` (each directory and one level below it). Given
    # directories come first; the parent of the inputs' directory and the
    # inputs' directory itself are always searched after them.
    module_path_help = ("Directory searched for `use`d modules (repeatable); "
                        "the inputs' parent directory is always searched too")

    # check
    p_check = subparsers.add_parser("check", help="Semantic analysis only")
    p_check.add_argument("files", nargs="+", help="Input .niob files")
    p_check.add_argument("--module-path", action="append", default=[],
                         metavar="DIR", help=module_path_help)

    # compile
    p_compile = subparsers.add_parser(
        "compile", help="Compile to C++ (OpenFHE, or FIDESlib with --backend fideslib)")
    p_compile.add_argument("files", nargs="+", help="Input .niob files")
    p_compile.add_argument("--outdir", default="nb_out", help="Output directory")
    p_compile.add_argument(
        "--backend", choices=["openfhe", "fideslib"], default="openfhe",
        help="The CKKS library the stages are generated against: openfhe "
             "(the client's instrumented OpenFHE, with the Niobium record/"
             "replay instrumentation) or fideslib (the FIDESlib facade: the "
             "server stages run on the engine FIDESLIB_BACKEND names at run "
             "time, cpu, cuda or haze; the client stages stay on the host)")
    p_compile.add_argument("--module-path", action="append", default=[],
                           metavar="DIR", help=module_path_help)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "lex": cmd_lex,
        "parse": cmd_parse,
        "check": cmd_check,
        "compile": cmd_compile,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main() or 0)
