#!/usr/bin/env python3
"""nbc — the nb FHE language cross-compiler.

Usage:
    nbc compile file1.nb [file2.nb ...] [--outdir DIR]
    nbc check   file1.nb [file2.nb ...]
    nbc lex     file1.nb
    nbc parse   file1.nb
"""

from __future__ import annotations
import argparse
import sys
import os
import json
from pathlib import Path

# Add xcomp directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lexer import lex, Token, TT
from parser import parse
from semantic import analyze as semantic_analyze
from codegen import generate
from errors import CompileError


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
    combined_source, tokens = _lex_all(args.files)
    if tokens is None:
        return 1

    try:
        program = parse(tokens)
        sa = semantic_analyze(program)
        if sa.errors.has_errors():
            print(sa.errors.report(), file=sys.stderr)
            return 1
        for w in sa.errors.warnings:
            print(w, file=sys.stderr)
        for n in sa.errors.notes:
            print(n, file=sys.stderr)
        print(f"OK: {sum(1 for i in program.items)} declarations, "
              f"{len(sa.errors.warnings)} warnings, 0 errors.")
        return 0
    except CompileError as e:
        print(e.format(), file=sys.stderr)
        return 1


def cmd_compile(args):
    """Compile .nb files to OpenFHE C++."""
    combined_source, tokens = _lex_all(args.files)
    if tokens is None:
        return 1

    try:
        program = parse(tokens)
        sa = semantic_analyze(program)

        if sa.errors.has_errors():
            print(sa.errors.report(), file=sys.stderr)
            return 1

        files = generate(program, sa)

        # Warnings from analysis AND code generation (heuristic fallbacks).
        for w in sa.errors.warnings:
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


def _lex_all(files: list[str]) -> tuple[str, list[Token] | None]:
    """Lex all input files and merge token streams."""
    all_tokens: list[Token] = []
    for path in files:
        source = Path(path).read_text()
        try:
            tokens = lex(source, path)
            # Remove the EOF token from all but the last file
            all_tokens.extend(t for t in tokens if t.type != TT.EOF)
        except CompileError as e:
            print(e.format(), file=sys.stderr)
            return "", None

    # Add a single EOF at the end
    if all_tokens:
        all_tokens.append(Token(TT.EOF, "", all_tokens[-1].loc))
    else:
        from errors import SourceLocation
        all_tokens.append(Token(TT.EOF, "", SourceLocation()))

    return "", all_tokens


def _print_ast(node, indent=0):
    """Pretty-print an AST node."""
    prefix = "  " * indent
    name = type(node).__name__

    # Import to check types
    import ast_nodes as ast

    if isinstance(node, ast.Program):
        print(f"{prefix}Program ({len(node.items)} items)")
        for item in node.items:
            _print_ast(item, indent + 1)

    elif isinstance(node, ast.UseDecl):
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
    p_lex.add_argument("files", nargs="+", help="Input .nb files")

    # parse
    p_parse = subparsers.add_parser("parse", help="Parse and print AST")
    p_parse.add_argument("files", nargs="+", help="Input .nb files")

    # check
    p_check = subparsers.add_parser("check", help="Semantic analysis only")
    p_check.add_argument("files", nargs="+", help="Input .nb files")

    # compile
    p_compile = subparsers.add_parser("compile", help="Compile to OpenFHE C++")
    p_compile.add_argument("files", nargs="+", help="Input .nb files")
    p_compile.add_argument("--outdir", default="nb_out", help="Output directory")

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
