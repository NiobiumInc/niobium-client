"""AST node definitions for the nb language."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from errors import SourceLocation


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """Base class for all AST nodes."""
    loc: SourceLocation = field(default_factory=SourceLocation, repr=False)


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

@dataclass
class Program(Node):
    items: list[Node] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level declarations
# ---------------------------------------------------------------------------

@dataclass
class UseDecl(Node):
    """use shared::*"""
    module_path: list[str] = field(default_factory=list)
    imported: str = "*"  # identifier or "*"


@dataclass
class ConstDecl(Node):
    """const PAYLOAD_DIM: u32 = 8"""
    name: str = ""
    type_ann: TypeExpr | None = None
    value: Expr | None = None


@dataclass
class EnumDecl(Node):
    """enum InstanceSize { Toy, Small, ... }"""
    name: str = ""
    variants: list[str] = field(default_factory=list)


@dataclass
class StructDecl(Node):
    """struct Instance { size: InstanceSize, ... }"""
    name: str = ""
    fields: list[FieldDecl] = field(default_factory=list)


@dataclass
class WireDecl(Node):
    """wire CryptoParams { ... }"""
    name: str = ""
    fields: list[FieldDecl] = field(default_factory=list)


@dataclass
class FieldDecl(Node):
    """A field in a struct or wire type."""
    name: str = ""
    type_ann: TypeExpr | None = None


@dataclass
class SchemeDecl(Node):
    """scheme CKKS { security: 128-classic, ... }"""
    name: str = ""
    fields: list[SchemeField] = field(default_factory=list)


@dataclass
class SchemeField(Node):
    """A key-value pair in a scheme block."""
    key: str = ""
    value: Any = None  # string, int, or list


@dataclass
class ExternDecl(Node):
    """extern weights from \"vector_constants\" """
    name: str = ""          # e.g. "weights"
    source: str = ""        # e.g. "vector_constants"


@dataclass
class RequiresDecl(Node):
    """requires { add, mul, rotate }"""
    capabilities: list[str] = field(default_factory=list)


@dataclass
class DomainDecl(Node):
    """domain client { has: SecretKey, ... }"""
    name: str = ""
    clauses: list[DomainClause] = field(default_factory=list)


@dataclass
class DomainClause(Node):
    kind: str = ""  # "has", "can", "cannot"
    items: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

@dataclass
class Annotation(Node):
    """@server, @stage("name"), @hardware(cache_key: [...])"""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

@dataclass
class FnDecl(Node):
    """fn name(params) -> return_type { body }"""
    name: str = ""
    annotations: list[Annotation] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    return_type: TypeExpr | None = None
    io_specs: list[IoSpec] = field(default_factory=list)
    body: Block | None = None


@dataclass
class Param(Node):
    name: str = ""
    type_ann: TypeExpr | None = None
    default: Expr | None = None


@dataclass
class IoSpec(Node):
    """reads(CryptoParams), writes(EncryptedResult)"""
    kind: str = ""  # "reads", "writes", "reads_plaintext", "writes_plaintext"
    types: list[IoType] = field(default_factory=list)


@dataclass
class IoType(Node):
    """A type reference in an IO spec, optionally indexed."""
    type_name: str = ""
    index: Expr | None = None  # e.g., IntermediateResult[batch_id]
    path_expr: Expr | None = None  # for from: keydir(inst)


# ---------------------------------------------------------------------------
# Type expressions
# ---------------------------------------------------------------------------

@dataclass
class TypeExpr(Node):
    """Base for type expressions."""
    pass


@dataclass
class PrimitiveType(TypeExpr):
    name: str = ""  # "u32", "f64", "bool", "string", "path"


@dataclass
class NamedType(TypeExpr):
    name: str = ""
    sub: str | None = None  # for Foo.Bar


@dataclass
class EncType(TypeExpr):
    inner: TypeExpr | None = None


@dataclass
class VecType(TypeExpr):
    elem: TypeExpr | None = None
    size: Expr | None = None


@dataclass
class MatType(TypeExpr):
    elem: TypeExpr | None = None
    rows: Expr | None = None
    cols: Expr | None = None


@dataclass
class TupleType(TypeExpr):
    elements: list[TypeExpr] = field(default_factory=list)


@dataclass
class FnType(TypeExpr):
    param_types: list[TypeExpr] = field(default_factory=list)
    return_type: TypeExpr | None = None


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

@dataclass
class Block(Node):
    stmts: list[Node] = field(default_factory=list)


@dataclass
class LetStmt(Node):
    name: str = ""
    type_ann: TypeExpr | None = None
    value: Expr | None = None
    tuple_names: list[str] | None = None  # for destructured let (a, b) = ...


@dataclass
class AssignStmt(Node):
    target: Expr | None = None
    value: Expr | None = None


@dataclass
class ReturnStmt(Node):
    value: Expr | None = None


@dataclass
class AssertStmt(Node):
    condition: Expr | None = None
    message: str | None = None


@dataclass
class IfStmt(Node):
    condition: Expr | None = None
    then_block: Block | None = None
    else_block: Block | IfStmt | None = None


@dataclass
class ForStmt(Node):
    pattern: ForPattern | None = None
    iterable: Expr | None = None
    body: Block | None = None


@dataclass
class MatchStmt(Node):
    subject: Expr | None = None
    arms: list[MatchArm] = field(default_factory=list)


@dataclass
class ExprStmt(Node):
    expr: Expr | None = None


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------

@dataclass
class Expr(Node):
    """Base for all expressions."""
    pass


@dataclass
class IntLiteral(Expr):
    value: int = 0


@dataclass
class FloatLiteral(Expr):
    value: float = 0.0


@dataclass
class StringLiteral(Expr):
    value: str = ""


@dataclass
class BoolLiteral(Expr):
    value: bool = False


@dataclass
class Ident(Expr):
    name: str = ""


@dataclass
class BinaryExpr(Expr):
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None


@dataclass
class UnaryExpr(Expr):
    op: str = ""
    operand: Expr | None = None


@dataclass
class CastExpr(Expr):
    expr: Expr | None = None
    target_type: TypeExpr | None = None


@dataclass
class PipeExpr(Expr):
    left: Expr | None = None
    right: Expr | None = None


@dataclass
class CallExpr(Expr):
    func: Expr | None = None
    args: list[Arg] = field(default_factory=list)
    type_args: list = field(default_factory=list)


@dataclass
class Arg(Node):
    name: str | None = None  # None for positional
    value: Expr | None = None


@dataclass
class FieldAccess(Expr):
    obj: Expr | None = None
    field_name: str = ""


@dataclass
class MethodCall(Expr):
    obj: Expr | None = None
    method: str = ""
    args: list[Arg] = field(default_factory=list)


@dataclass
class IndexExpr(Expr):
    obj: Expr | None = None
    index: Expr | None = None


@dataclass
class SliceExpr(Expr):
    obj: Expr | None = None
    start: Expr | None = None
    end: Expr | None = None


@dataclass
class ArrayLiteral(Expr):
    elements: list[Expr] = field(default_factory=list)


@dataclass
class StructLiteral(Expr):
    type_name: str = ""
    fields: list[FieldInit] = field(default_factory=list)


@dataclass
class FieldInit(Node):
    name: str = ""
    value: Expr | None = None  # None means shorthand (name == value ident)


@dataclass
class Closure(Expr):
    params: list[Param] = field(default_factory=list)
    body: Expr | Block | None = None


@dataclass
class ForExpr(Expr):
    pattern: ForPattern | None = None
    iterable: Expr | None = None
    body: Block | None = None


@dataclass
class IfExpr(Expr):
    condition: Expr | None = None
    then_block: Block | None = None
    else_block: Block | IfExpr | None = None


@dataclass
class MatchExpr(Expr):
    subject: Expr | None = None
    arms: list[MatchArm] = field(default_factory=list)


@dataclass
class RangeExpr(Expr):
    start: Expr | None = None
    end: Expr | None = None
    inclusive: bool = False


@dataclass
class ForPattern(Node):
    names: list[str] = field(default_factory=list)  # 1 elem = simple, 2 = destructured


@dataclass
class MatchArm(Node):
    pattern: Pattern | None = None
    body: Expr | Block | None = None


@dataclass
class Pattern(Node):
    pass


@dataclass
class IdentPattern(Pattern):
    name: str = ""


@dataclass
class WildcardPattern(Pattern):
    pass


@dataclass
class LiteralPattern(Pattern):
    value: IntLiteral | FloatLiteral | StringLiteral | None = None


@dataclass
class StructPattern(Pattern):
    type_name: str = ""
    fields: list[FieldPattern] = field(default_factory=list)


@dataclass
class FieldPattern(Node):
    name: str = ""
    pattern: Pattern | None = None


@dataclass
class SchemeOverride(Expr):
    """scheme.override(security: not_set, ring_dim: 2048)"""
    field_name: str = ""  # "override"
    args: list[Arg] = field(default_factory=list)
