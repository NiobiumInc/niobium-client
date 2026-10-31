"""Compile-time parameters: `nbc compile --set NAME=VALUE` overrides the value
of a top-level `const NAME: <type> = <default>`.

The override replaces the constant's initializer with a literal of the
declared type before semantic analysis, so everything downstream (the
emitted constexpr, compile-time-evaluated domains and degrees, loop bounds,
the instance table) sees the overridden value exactly as if it had been
written in the source. The source value stays the default.

    const PARAM1: u32 = 16
    nbc compile app.niob --set PARAM1=64

NAME is the constant's name, or `<module>::NAME` when the same name is
declared in more than one module.

`nbc compile --scheme FIELD=VALUE` overrides a field of the scheme block in
the same way, for the fields that trade run time against device memory and
change neither the security, nor the results, nor the rotation keys:
`linear_transform_memory` (N GB or N MB) and `linear_transform_batch` (N).
Recorded as `scheme::FIELD` next to the `--set` parameters. The product mode
(`linear_transform`) is not overridable: fused and loop need rotation keys
the circuit's own rotation set must declare.

    nbc compile app.niob --backend fideslib --scheme linear_transform_memory=20GB --scheme linear_transform_batch=4
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ast_nodes as ast
from .errors import CompileError
from .modules import fqn

_UNSIGNED = {"u8", "u16", "u32", "u64", "usize"}
_SIGNED = {"i8", "i16", "i32", "i64", "isize"}
_FLOAT = {"f32", "f64"}


@dataclass
class ParamOverride:
    name: str          # canonical (module-qualified) name of the constant
    type_name: str     # its declared primitive type
    value: object      # the parsed value
    text: str          # the value as given on the command line


def _split(arg: str) -> tuple[str, str]:
    if "=" not in arg:
        raise CompileError(f"--set {arg!r}: expected NAME=VALUE")
    name, value = arg.split("=", 1)
    name, value = name.strip(), value.strip()
    if not name or not value:
        raise CompileError(f"--set {arg!r}: expected NAME=VALUE")
    return name, value


def _literal(const: ast.ConstDecl, text: str) -> tuple[ast.Expr, str, object]:
    """The literal node of `text` for the constant's declared type."""
    t = const.type_ann
    tname = t.name if isinstance(t, ast.PrimitiveType) else None
    if tname is None:
        # no annotation: the type of the source initializer's literal
        v = const.value
        tname = {ast.IntLiteral: "i64", ast.FloatLiteral: "f64",
                 ast.BoolLiteral: "bool", ast.StringLiteral: "string"}.get(type(v))
    if tname is None:
        raise CompileError(
            f"--set {const.name}: only constants of a primitive type (integer, float, "
            f"bool, string) can be set", const.loc)
    where = f"--set {const.name}={text}"
    if tname in _UNSIGNED or tname in _SIGNED:
        try:
            n = int(text, 0)
        except ValueError:
            raise CompileError(f"{where}: {const.name} is {tname}, {text!r} is not an integer", const.loc)
        if tname in _UNSIGNED and n < 0:
            raise CompileError(f"{where}: {const.name} is {tname}, which cannot be negative", const.loc)
        return ast.IntLiteral(value=n), tname, n
    if tname in _FLOAT:
        try:
            x = float(text)
        except ValueError:
            raise CompileError(f"{where}: {const.name} is {tname}, {text!r} is not a number", const.loc)
        return ast.FloatLiteral(value=x), tname, x
    if tname == "bool":
        if text not in ("true", "false"):
            raise CompileError(f"{where}: {const.name} is bool, expected true or false", const.loc)
        return ast.BoolLiteral(value=text == "true"), tname, text == "true"
    if tname == "string":
        return ast.StringLiteral(value=text), tname, text
    raise CompileError(f"{where}: constants of type {tname} cannot be set", const.loc)


def apply_overrides(program: ast.Program, sets: list[str] | None) -> list[ParamOverride]:
    """Apply the `--set NAME=VALUE` overrides to the program's top-level
    constants, in place. Returns what was set, in the given order. An unknown
    or ambiguous name, a name given twice or a value of the wrong type is a
    CompileError."""
    if not sets:
        return []
    consts = [i for i in program.items if isinstance(i, ast.ConstDecl)]
    out: list[ParamOverride] = []
    seen: set[str] = set()
    for arg in sets:
        name, text = _split(arg)
        matches = [c for c in consts if name in (c.name, fqn(c.module, c.name))]
        if not matches:
            known = ", ".join(sorted(fqn(c.module, c.name) for c in consts)) or "none"
            raise CompileError(f"--set {name}: no top-level const of that name (constants: {known})")
        if len(matches) > 1:
            names = ", ".join(sorted(fqn(c.module, c.name) for c in matches))
            raise CompileError(f"--set {name}: ambiguous, declared as {names}; qualify it as <module>::{name}")
        const = matches[0]
        canon = fqn(const.module, const.name)
        if canon in seen:
            raise CompileError(f"--set {name}: given more than once")
        seen.add(canon)
        lit, tname, value = _literal(const, text)
        lit.loc = const.value.loc if const.value is not None else const.loc
        lit.module = const.module
        const.value = lit
        out.append(ParamOverride(canon, tname, value, text))
    return out


# The scheme fields --scheme may override: how much device memory a product
# may hold. They change neither the security (the moduli, the key), nor the
# results, nor the rotation keys. `linear_transform` (the mode) is not one of
# them: fused and loop need a rotation key per baby and giant step, which only
# the circuit's own rotation set (requires { rotate(...) }) provides, so a
# mode change is a change to the circuit.
SCHEME_OVERRIDABLE = ("linear_transform_memory", "linear_transform_batch")


def _scheme_value(field: str, text: str):
    t = text.strip()
    import re
    if re.fullmatch(r"\d+", t):
        return int(t)
    m = re.fullmatch(r"(\d+)\s*(GB|MB)", t)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", t):
        return t
    raise CompileError(f"--scheme {field}={text}: expected a word, a whole number, or N GB / N MB")


def apply_scheme_overrides(program: ast.Program, schemes: list[str] | None) -> list[ParamOverride]:
    """Apply the `--scheme FIELD=VALUE` overrides to the program's scheme
    block, in place (a field the block lacks is added). Only the fields in
    SCHEME_OVERRIDABLE; the code generator validates the values."""
    if not schemes:
        return []
    blocks = [i for i in program.items if isinstance(i, ast.SchemeDecl)]
    if not blocks:
        raise CompileError("--scheme: the program has no scheme block")
    out: list[ParamOverride] = []
    seen: set[str] = set()
    for arg in schemes:
        if "=" not in arg:
            raise CompileError(f"--scheme {arg!r}: expected FIELD=VALUE")
        field, text = (x.strip() for x in arg.split("=", 1))
        if field not in SCHEME_OVERRIDABLE:
            why = (" (the mode decides which rotation keys key generation must make: change it in the "
                   "scheme block, with a rotation set that has them)" if field == "linear_transform" else "")
            raise CompileError(f"--scheme {field}: not overridable{why}; only {', '.join(SCHEME_OVERRIDABLE)}, "
                               "the memory choices that change neither the security, the results nor the keys")
        if field in seen:
            raise CompileError(f"--scheme {field}: given more than once")
        seen.add(field)
        value = _scheme_value(field, text)
        for b in blocks:
            hit = [f for f in b.fields if f.key == field]
            if hit:
                for f in hit:
                    f.value = value
            else:
                b.fields.append(ast.SchemeField(loc=b.loc, key=field, value=value))
        out.append(ParamOverride(f"scheme::{field}", "scheme", value, text))
    return out
