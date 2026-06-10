"""Semantic analysis for the nb FHE language.

Performs:
  - Name resolution
  - Type checking
  - Domain enforcement (client/server separation)
  - Multiplicative depth tracking
  - Wire type safety verification
"""

from __future__ import annotations
import ast_nodes as ast
from nb_types import (
    NbType, TypeKind, Domain, SymbolTable, Symbol,
    PRIMITIVE_MAP, VOID, BOOL, U32, I64, F64, STRING, PATH, UNKNOWN,
    enc_of, vec_of, mat_of, is_numeric, is_encrypted, common_type,
    CLIENT_ONLY_FNS, SERVER_FORBIDDEN_TYPES,
)
from errors import (
    ErrorCollector, SemanticError, DomainError, TypeError_, DepthError,
    SourceLocation,
)


class SemanticAnalyzer:
    """Multi-pass semantic analyzer."""

    def __init__(self):
        self.errors = ErrorCollector()
        self.global_scope = SymbolTable(name="<global>")
        self.current_scope = self.global_scope
        self.current_domain: Domain = Domain.SHARED
        self.current_fn: str | None = None
        self.type_registry: dict[str, NbType] = {}
        self.wire_types: set[str] = set()
        self.enum_types: dict[str, list[str]] = {}
        self.scheme_config: dict[str, object] = {}
        self.required_capabilities: list[str] = []
        self.domain_defs: dict[str, ast.DomainDecl] = {}
        self.max_depth: int = 23  # default from scheme

        # Register predefined type names
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in types and functions."""
        # Built-in FHE types
        for name in ("CryptoContext", "PublicKey", "SecretKey",
                      "EvalMultKey", "EvalAutomorphismKeys", "KeyBundle",
                      "Plaintext"):
            self.type_registry[name] = NbType(TypeKind.STRUCT, name=name)

        # Built-in functions
        builtins = {
            "keygen": NbType(TypeKind.FN, return_type=NbType(TypeKind.STRUCT, name="KeyBundle")),
            "encrypt": NbType(TypeKind.FN, return_type=enc_of(F64)),
            "decrypt": NbType(TypeKind.FN, return_type=F64),
            "save_secret_key": NbType(TypeKind.FN, return_type=VOID),
            "load_secret_key": NbType(TypeKind.FN, return_type=NbType(TypeKind.STRUCT, name="SecretKey")),
            "load": NbType(TypeKind.FN, return_type=UNKNOWN),
            "load_all": NbType(TypeKind.FN, return_type=UNKNOWN),
            "load_matrix": NbType(TypeKind.FN, return_type=UNKNOWN),
            "load_vec": NbType(TypeKind.FN, return_type=UNKNOWN),
            "tile": NbType(TypeKind.FN, return_type=UNKNOWN),
            "clone": NbType(TypeKind.FN, return_type=UNKNOWN),
            "zero": NbType(TypeKind.FN, return_type=UNKNOWN),
            "relin": NbType(TypeKind.FN, return_type=UNKNOWN),
            "rotate": NbType(TypeKind.FN, return_type=UNKNOWN),
            "chebyshev": NbType(TypeKind.FN, return_type=UNKNOWN),
            "running_sums": NbType(TypeKind.FN, return_type=VOID),
            "slot_replicator": NbType(TypeKind.FN, return_type=UNKNOWN),
            "slot_sum": NbType(TypeKind.FN, return_type=UNKNOWN),
            "slot_mask": NbType(TypeKind.FN, return_type=UNKNOWN),
            "reduce": NbType(TypeKind.FN, return_type=UNKNOWN),
            "map": NbType(TypeKind.FN, return_type=UNKNOWN),
            "zip_map": NbType(TypeKind.FN, return_type=UNKNOWN),
            "len": NbType(TypeKind.FN, return_type=U32),
            "rows": NbType(TypeKind.FN, return_type=U32),
            "round": NbType(TypeKind.FN, return_type=I64),
            "ceil_div": NbType(TypeKind.FN, return_type=U32),
            "log2": NbType(TypeKind.FN, return_type=U32),
            "exp": NbType(TypeKind.FN, return_type=F64),
            "sort": NbType(TypeKind.FN, return_type=UNKNOWN),
            "argmax": NbType(TypeKind.FN, return_type=UNKNOWN),
            "enumerate": NbType(TypeKind.FN, return_type=UNKNOWN),
            "stride": NbType(TypeKind.FN, return_type=UNKNOWN),
            "transpose": NbType(TypeKind.FN, return_type=UNKNOWN),
            "batch": NbType(TypeKind.FN, return_type=UNKNOWN),
            "scale": NbType(TypeKind.FN, return_type=UNKNOWN),
            "prepend_column": NbType(TypeKind.FN, return_type=UNKNOWN),
            "root": NbType(TypeKind.FN, return_type=PATH),
            "total_sums": NbType(TypeKind.FN, return_type=UNKNOWN),
            "vec_zeros": NbType(TypeKind.FN, return_type=UNKNOWN),
            "mat_zeros": NbType(TypeKind.FN, return_type=UNKNOWN),
            "to_matrix_form": NbType(TypeKind.FN, return_type=UNKNOWN),
        }
        for name, ty in builtins.items():
            self.global_scope.define(Symbol(name, ty))

    # ===== Entry point =====

    def analyze(self, program: ast.Program):
        """Run all analysis passes."""
        # Pass 1: collect type declarations and register names
        self._pass_collect_types(program)
        # Pass 2: collect function signatures
        self._pass_collect_functions(program)
        # Pass 3: check function bodies
        self._pass_check_bodies(program)
        # Pass 4: verify wire type safety
        self._pass_verify_wire_types()

    # ===== Pass 1: Collect types =====

    def _pass_collect_types(self, program: ast.Program):
        for item in program.items:
            if isinstance(item, ast.EnumDecl):
                self.enum_types[item.name] = item.variants
                ty = NbType(TypeKind.ENUM, name=item.name, variants=item.variants)
                self.type_registry[item.name] = ty
                # Register each variant as a constant
                for v in item.variants:
                    self.global_scope.define(Symbol(v, ty, is_const=True, const_value=v))

            elif isinstance(item, ast.StructDecl):
                fields = {}
                for f in item.fields:
                    fields[f.name] = self._resolve_type_expr(f.type_ann)
                ty = NbType(TypeKind.STRUCT, name=item.name, fields=fields)
                self.type_registry[item.name] = ty

            elif isinstance(item, ast.WireDecl):
                fields = {}
                for f in item.fields:
                    fields[f.name] = self._resolve_type_expr(f.type_ann)
                ty = NbType(TypeKind.WIRE, name=item.name, fields=fields)
                self.type_registry[item.name] = ty
                self.wire_types.add(item.name)

            elif isinstance(item, ast.ConstDecl):
                ty = self._resolve_type_expr(item.type_ann)
                val = self._eval_const(item.value)
                self.global_scope.define(
                    Symbol(item.name, ty, is_const=True, const_value=val)
                )

            elif isinstance(item, ast.SchemeDecl):
                for f in item.fields:
                    self.scheme_config[f.key] = f.value
                if "depth" in self.scheme_config:
                    try:
                        self.max_depth = int(self.scheme_config["depth"])
                    except (ValueError, TypeError):
                        pass

            elif isinstance(item, ast.RequiresDecl):
                self.required_capabilities = item.capabilities

            elif isinstance(item, ast.DomainDecl):
                self.domain_defs[item.name] = item

    # ===== Pass 2: Collect function signatures =====

    def _pass_collect_functions(self, program: ast.Program):
        for item in program.items:
            if isinstance(item, ast.FnDecl):
                param_types = []
                for p in item.params:
                    pt = self._resolve_type_expr(p.type_ann) if p.type_ann else UNKNOWN
                    param_types.append(pt)
                ret = self._resolve_type_expr(item.return_type) if item.return_type else VOID
                fn_type = NbType(TypeKind.FN, param_types=param_types, return_type=ret)
                domain = self._fn_domain(item)
                self.global_scope.define(Symbol(item.name, fn_type, domain=domain))

    # ===== Pass 3: Check function bodies =====

    def _pass_check_bodies(self, program: ast.Program):
        for item in program.items:
            if isinstance(item, ast.FnDecl):
                self._check_fn(item)

    def _check_fn(self, fn: ast.FnDecl):
        self.current_fn = fn.name
        self.current_domain = self._fn_domain(fn)
        scope = self.current_scope.child(fn.name)

        # Bind parameters
        for p in fn.params:
            ty = self._resolve_type_expr(p.type_ann) if p.type_ann else UNKNOWN
            scope.define(Symbol(p.name, ty))

        prev_scope = self.current_scope
        self.current_scope = scope
        if fn.body:
            self._check_block(fn.body)
        self.current_scope = prev_scope
        self.current_fn = None
        self.current_domain = Domain.SHARED

    def _check_block(self, block: ast.Block):
        for stmt in block.stmts:
            self._check_stmt(stmt)

    def _check_stmt(self, stmt: ast.Node):
        if isinstance(stmt, ast.LetStmt):
            val_type = self._check_expr(stmt.value) if stmt.value else UNKNOWN
            declared_type = self._resolve_type_expr(stmt.type_ann) if stmt.type_ann else val_type
            self.current_scope.define(Symbol(stmt.name, declared_type))

        elif isinstance(stmt, ast.AssignStmt):
            self._check_expr(stmt.target)
            self._check_expr(stmt.value)

        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                self._check_expr(stmt.value)

        elif isinstance(stmt, ast.AssertStmt):
            self._check_expr(stmt.condition)

        elif isinstance(stmt, ast.IfStmt):
            self._check_expr(stmt.condition)
            self._check_block(stmt.then_block)
            if isinstance(stmt.else_block, ast.Block):
                self._check_block(stmt.else_block)
            elif isinstance(stmt.else_block, ast.IfStmt):
                self._check_stmt(stmt.else_block)

        elif isinstance(stmt, ast.ForStmt):
            self._check_expr(stmt.iterable)
            scope = self.current_scope.child("for")
            for name in stmt.pattern.names:
                scope.define(Symbol(name, UNKNOWN))
            prev = self.current_scope
            self.current_scope = scope
            self._check_block(stmt.body)
            self.current_scope = prev

        elif isinstance(stmt, ast.MatchStmt):
            self._check_expr(stmt.subject)
            for arm in stmt.arms:
                self._check_match_arm(arm)

        elif isinstance(stmt, ast.ExprStmt):
            self._check_expr(stmt.expr)

    def _check_match_arm(self, arm: ast.MatchArm):
        scope = self.current_scope.child("match_arm")
        if isinstance(arm.pattern, ast.IdentPattern):
            scope.define(Symbol(arm.pattern.name, UNKNOWN))
        prev = self.current_scope
        self.current_scope = scope
        if isinstance(arm.body, ast.Block):
            self._check_block(arm.body)
        elif isinstance(arm.body, ast.Expr):
            self._check_expr(arm.body)
        self.current_scope = prev

    # ===== Expression type checking =====

    def _check_expr(self, expr: ast.Expr | None) -> NbType:
        if expr is None:
            return VOID

        if isinstance(expr, ast.IntLiteral):
            return U32

        if isinstance(expr, ast.FloatLiteral):
            return F64

        if isinstance(expr, ast.StringLiteral):
            return STRING

        if isinstance(expr, ast.BoolLiteral):
            return BOOL

        if isinstance(expr, ast.Ident):
            sym = self.current_scope.lookup(expr.name)
            if sym is None:
                # Could be a type name used as value (enum variant, etc.)
                if expr.name in self.type_registry:
                    return self.type_registry[expr.name]
                self.errors.warn(f"undefined name '{expr.name}'", expr.loc)
                return UNKNOWN
            return sym.type

        if isinstance(expr, ast.BinaryExpr):
            left_t = self._check_expr(expr.left)
            right_t = self._check_expr(expr.right)
            # Domain check for encrypted operations
            if expr.op == "*_norelin":
                # Only error if we can definitively say neither is encrypted
                # (skip check when types are unknown/inferred)
                if (left_t.kind != TypeKind.UNKNOWN and right_t.kind != TypeKind.UNKNOWN
                        and not (is_encrypted(left_t) or is_encrypted(right_t))):
                    self.errors.error(TypeError_(
                        "*_norelin requires at least one encrypted operand",
                        expr.loc,
                    ))
                result = common_type(left_t, right_t)
                result.needs_relin = True
                return result
            if expr.op in ("+", "-", "*", "/"):
                result = common_type(left_t, right_t)
                if expr.op == "*" and is_encrypted(result):
                    result.depth = max(
                        getattr(left_t, 'depth', 0),
                        getattr(right_t, 'depth', 0),
                    ) + 1
                    if result.depth > self.max_depth:
                        self.errors.error(DepthError(
                            f"multiplicative depth {result.depth} exceeds "
                            f"maximum {self.max_depth} in function '{self.current_fn}'",
                            expr.loc,
                        ))
                return result
            if expr.op in ("^",):
                return common_type(left_t, right_t)
            if expr.op in ("==", "!=", "~=", "<", ">", "<=", ">="):
                return BOOL
            if expr.op in ("&&", "||"):
                return BOOL
            return common_type(left_t, right_t)

        if isinstance(expr, ast.UnaryExpr):
            operand_t = self._check_expr(expr.operand)
            if expr.op == "!":
                return BOOL
            return operand_t

        if isinstance(expr, ast.CastExpr):
            self._check_expr(expr.expr)
            return self._resolve_type_expr(expr.target_type)

        if isinstance(expr, ast.PipeExpr):
            left_t = self._check_expr(expr.left)
            # Pipe desugars: left |> f => f(left), left |> f(x) => f(left, x)
            self._check_expr(expr.right)
            return UNKNOWN  # return type depends on the piped function

        if isinstance(expr, ast.CallExpr):
            func_type = self._check_expr(expr.func)
            for arg in expr.args:
                self._check_expr(arg.value)
            # Domain enforcement
            if isinstance(expr.func, ast.Ident):
                self._check_domain_call(expr.func.name, expr.loc)
            if func_type.kind == TypeKind.FN and func_type.return_type:
                return func_type.return_type
            return UNKNOWN

        if isinstance(expr, ast.FieldAccess):
            obj_t = self._check_expr(expr.obj)
            if obj_t.kind in (TypeKind.STRUCT, TypeKind.WIRE):
                if expr.field_name in obj_t.fields:
                    return obj_t.fields[expr.field_name]
            return UNKNOWN

        if isinstance(expr, ast.MethodCall):
            self._check_expr(expr.obj)
            for arg in expr.args:
                self._check_expr(arg.value)
            return UNKNOWN

        if isinstance(expr, ast.IndexExpr):
            self._check_expr(expr.obj)
            self._check_expr(expr.index)
            return UNKNOWN

        if isinstance(expr, ast.SliceExpr):
            self._check_expr(expr.obj)
            self._check_expr(expr.start)
            self._check_expr(expr.end)
            return UNKNOWN

        if isinstance(expr, ast.ArrayLiteral):
            elem_type = UNKNOWN
            for e in expr.elements:
                elem_type = self._check_expr(e)
            return vec_of(elem_type)

        if isinstance(expr, ast.StructLiteral):
            ty = self.type_registry.get(expr.type_name, UNKNOWN)
            for fi in expr.fields:
                if fi.value:
                    self._check_expr(fi.value)
            return ty

        if isinstance(expr, ast.Closure):
            # Check closure body
            scope = self.current_scope.child("closure")
            for p in expr.params:
                pt = self._resolve_type_expr(p.type_ann) if p.type_ann else UNKNOWN
                scope.define(Symbol(p.name, pt))
            prev = self.current_scope
            self.current_scope = scope
            if isinstance(expr.body, ast.Block):
                self._check_block(expr.body)
            elif isinstance(expr.body, ast.Expr):
                self._check_expr(expr.body)
            self.current_scope = prev
            return NbType(TypeKind.CLOSURE)

        if isinstance(expr, ast.ForExpr):
            self._check_expr(expr.iterable)
            scope = self.current_scope.child("for_expr")
            for name in expr.pattern.names:
                scope.define(Symbol(name, UNKNOWN))
            prev = self.current_scope
            self.current_scope = scope
            self._check_block(expr.body)
            self.current_scope = prev
            return UNKNOWN  # vec of body result

        if isinstance(expr, ast.IfExpr):
            self._check_expr(expr.condition)
            self._check_block(expr.then_block)
            if isinstance(expr.else_block, ast.Block):
                self._check_block(expr.else_block)
            elif isinstance(expr.else_block, ast.IfExpr):
                self._check_expr(expr.else_block)
            return UNKNOWN

        if isinstance(expr, ast.MatchExpr):
            self._check_expr(expr.subject)
            for arm in expr.arms:
                self._check_match_arm(arm)
            return UNKNOWN

        if isinstance(expr, ast.RangeExpr):
            self._check_expr(expr.start)
            self._check_expr(expr.end)
            return UNKNOWN

        return UNKNOWN

    # ===== Domain enforcement =====

    def _fn_domain(self, fn: ast.FnDecl) -> Domain:
        for ann in fn.annotations:
            if ann.name == "client":
                return Domain.CLIENT
            if ann.name == "server":
                return Domain.SERVER
        return Domain.SHARED

    def _check_domain_call(self, fn_name: str, loc: SourceLocation):
        """Check that the current domain allows calling this function."""
        if self.current_domain == Domain.SERVER:
            if fn_name in CLIENT_ONLY_FNS:
                self.errors.error(DomainError(
                    f"function '{fn_name}' is not available in the server domain. "
                    f"Called from '{self.current_fn}'.",
                    loc,
                ))

    # ===== Wire type safety =====

    def _pass_verify_wire_types(self):
        """Verify that wire types don't contain forbidden types."""
        for wname in self.wire_types:
            wtype = self.type_registry.get(wname)
            if wtype and wtype.fields:
                for fname, ftype in wtype.fields.items():
                    if ftype.name in SERVER_FORBIDDEN_TYPES:
                        self.errors.error(DomainError(
                            f"wire type '{wname}' contains forbidden type "
                            f"'{ftype.name}' in field '{fname}'. "
                            f"SecretKey cannot cross the trust boundary.",
                        ))
                    if ftype.kind == TypeKind.FN:
                        self.errors.error(TypeError_(
                            f"wire type '{wname}' cannot contain function type "
                            f"in field '{fname}'.",
                        ))

    # ===== Type resolution =====

    def _resolve_type_expr(self, texpr: ast.TypeExpr | None) -> NbType:
        if texpr is None:
            return UNKNOWN

        if isinstance(texpr, ast.PrimitiveType):
            return PRIMITIVE_MAP.get(texpr.name, UNKNOWN)

        if isinstance(texpr, ast.NamedType):
            ty = self.type_registry.get(texpr.name, UNKNOWN)
            if ty == UNKNOWN:
                # Don't warn for standard FHE types we haven't registered
                pass
            return ty

        if isinstance(texpr, ast.EncType):
            inner = self._resolve_type_expr(texpr.inner)
            return enc_of(inner)

        if isinstance(texpr, ast.VecType):
            elem = self._resolve_type_expr(texpr.elem)
            return vec_of(elem)

        if isinstance(texpr, ast.MatType):
            elem = self._resolve_type_expr(texpr.elem)
            return mat_of(elem)

        if isinstance(texpr, ast.FnType):
            params = [self._resolve_type_expr(p) for p in texpr.param_types]
            ret = self._resolve_type_expr(texpr.return_type)
            return NbType(TypeKind.FN, param_types=params, return_type=ret)

        return UNKNOWN

    # ===== Constant evaluation =====

    def _eval_const(self, expr: ast.Expr | None) -> object:
        if expr is None:
            return None
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        if isinstance(expr, ast.FloatLiteral):
            return expr.value
        if isinstance(expr, ast.StringLiteral):
            return expr.value
        if isinstance(expr, ast.BoolLiteral):
            return expr.value
        if isinstance(expr, ast.BinaryExpr):
            left = self._eval_const(expr.left)
            right = self._eval_const(expr.right)
            if left is not None and right is not None:
                ops = {
                    "+": lambda a, b: a + b,
                    "-": lambda a, b: a - b,
                    "*": lambda a, b: a * b,
                    "/": lambda a, b: a // b if isinstance(a, int) else a / b,
                }
                if expr.op in ops:
                    return ops[expr.op](left, right)
        if isinstance(expr, ast.Ident):
            sym = self.global_scope.lookup(expr.name)
            if sym and sym.is_const:
                return sym.const_value
        return None


def analyze(program: ast.Program) -> SemanticAnalyzer:
    """Convenience function to run semantic analysis."""
    sa = SemanticAnalyzer()
    sa.analyze(program)
    return sa
