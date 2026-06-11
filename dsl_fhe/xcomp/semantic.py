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
from builtins_registry import BUILTINS, DEPTH_OPAQUE_FNS
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
        # Best-effort static depth accounting (loop bodies counted once):
        # the deepest multiplication chain observed, and whether any encrypted
        # multiply sits inside a loop (which makes the static count a lower
        # bound only — suppresses the over-provision warning).
        self.observed_max_depth: int = 0
        self._loop_depth: int = 0
        self._enc_mul_in_loop: bool = False
        self._depth_opaque: bool = False
        # Parameter-advisor inputs: ring_dim literals seen in Instance-style
        # struct literals, and whether any scheme.override(security: ...)
        # exists (per-profile dev overrides make static N-vs-security checks
        # advisory rather than errors).
        self.ring_dims: set[int] = set()
        self.has_security_override: bool = False

        # Register predefined type names
        self._register_builtins()

    def _register_builtins(self):
        """Register built-in types and functions."""
        # Built-in FHE types
        for name in ("CryptoContext", "PublicKey", "SecretKey",
                      "EvalMultKey", "EvalAutomorphismKeys", "KeyBundle",
                      "Plaintext"):
            self.type_registry[name] = NbType(TypeKind.STRUCT, name=name)

        # Built-in functions — classification facts come from the unified
        # registry in builtins.py (single source of truth shared with codegen).
        kind_map = {
            "enc": lambda: enc_of(F64),
            "u32": lambda: U32, "i64": lambda: I64, "f64": lambda: F64,
            "string": lambda: STRING, "path": lambda: PATH, "void": lambda: VOID,
            "keybundle": lambda: NbType(TypeKind.STRUCT, name="KeyBundle"),
            "secretkey": lambda: NbType(TypeKind.STRUCT, name="SecretKey"),
            "plain": lambda: UNKNOWN,    # plaintext, precise type not modeled
            "unknown": lambda: UNKNOWN,
        }
        builtins = {
            b.name: NbType(TypeKind.FN, return_type=kind_map[b.returns]())
            for b in BUILTINS
        }
        for name, ty in builtins.items():
            self.global_scope.define(Symbol(name, ty))

        # Operator-as-value names (e.g. reduce(+, xs) parses the operator as
        # `op_+`), and the special `scheme` config object plus the scheme-level
        # literals usable in scheme.override(...). Registered so they don't
        # surface as spurious "undefined name" warnings.
        for name in ("op_+", "op_-", "op_*", "op_/", "scheme", "not_set"):
            self.global_scope.define(Symbol(name, UNKNOWN))

    # Max log2(Q) per ring dimension for a CLASSICAL security target with
    # ternary secrets (homomorphicencryption.org standard; >=65536
    # extrapolated by doubling — treat as estimates).
    HESTD_MAX_LOGQ = {
        "128-classic": {1024: 27, 2048: 54, 4096: 109, 8192: 218,
                        16384: 438, 32768: 881, 65536: 1772, 131072: 3524},
        "192-classic": {1024: 19, 2048: 37, 4096: 75, 8192: 152,
                        16384: 305, 32768: 611, 65536: 1222, 131072: 2444},
        "256-classic": {1024: 14, 2048: 29, 4096: 58, 8192: 118,
                        16384: 237, 32768: 476, 65536: 952, 131072: 1904},
    }

    def _advise_parameters(self):
        security = str(self.scheme_config.get("security", "")).strip()
        table = self.HESTD_MAX_LOGQ.get(security)
        if table is None:
            return  # not_set or unknown level: nothing to check
        try:
            q_i = int(str(self.scheme_config.get("precision", 0)).split()[0])
            first = int(str(self.scheme_config.get("first_mod", 0)).split()[0])
        except (ValueError, IndexError):
            return
        if q_i <= 0 or self.max_depth <= 0:
            return
        log_q = first + self.max_depth * q_i
        min_n = next((n for n in sorted(table) if table[n] >= log_q), None)
        msg = (f"params: logQ ~= {log_q} bits (first_mod {first} + depth "
               f"{self.max_depth} x q_i {q_i}); {security} needs "
               + (f"ring_dim >= {min_n}" if min_n else
                  f"more than ring_dim 131072 — reduce depth or q_i"))
        if self.ring_dims:
            ok = sorted(n for n in self.ring_dims if min_n and n >= min_n)
            low = sorted(n for n in self.ring_dims if not min_n or n < min_n)
            if ok:
                msg += f"; declared ring_dims OK: {ok}"
            if low:
                msg += (f"; below target: {low}"
                        + (" (covered by scheme.override(security: not_set) "
                           "dev profiles)" if self.has_security_override
                           else ""))
                if not self.has_security_override:
                    self.errors.warn(
                        f"ring_dim {low} cannot reach {security} at "
                        f"logQ ~= {log_q} bits (needs >= {min_n}); lower "
                        f"depth/q_i or raise ring_dim", None)
        self.errors.note(msg)

    def _resolve_int_arg(self, call: ast.CallExpr, name: str):
        """Resolve a named argument to an int when it is a literal or a
        declared const; None otherwise."""
        for a in call.args:
            if a.name == name:
                v = a.value
                if isinstance(v, ast.IntLiteral):
                    return int(v.value)
                if isinstance(v, ast.Ident):
                    sym = self.global_scope.lookup(v.name)
                    if sym is not None and sym.is_const and isinstance(
                            sym.const_value, int):
                        return sym.const_value
                return None
        return None

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
        # Pass 5: depth-budget sanity. Over-provisioned depth makes every
        # ciphertext linearly larger (size ~ depth+1) for no benefit. Only
        # meaningful when the static count is exact: no encrypted multiplies
        # inside loops (loop bodies are counted once, so the static depth is
        # just a lower bound there).
        if (self.observed_max_depth > 0
                and not self._enc_mul_in_loop
                and not self._depth_opaque
                and self.max_depth > max(2 * self.observed_max_depth,
                                         self.observed_max_depth + 8)):
            self.errors.warn(
                f"scheme depth {self.max_depth} greatly exceeds the deepest "
                f"tracked multiplication chain ({self.observed_max_depth}); "
                f"ciphertexts are ~{(self.max_depth + 1) / (self.observed_max_depth + 1):.1f}x "
                f"larger than needed — consider lowering depth",
                None,
            )
        # Pass 6: security/parameter frontier advisor. logQ ~= first_mod +
        # depth * q_i; the HE standard bounds logQ per ring dimension for a
        # given security level. Surfacing the numbers makes the
        # security-vs-accuracy tradeoff (N vs q_i vs depth vs approximation
        # degree) visible at compile time.
        self._advise_parameters()

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
            if stmt.tuple_names and len(stmt.tuple_names) > 1:
                # Destructured binding: let (a, b) = expr — define each name.
                for n in stmt.tuple_names:
                    if n != "_":
                        self.current_scope.define(Symbol(n, UNKNOWN))
            else:
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
            self._loop_depth += 1
            self._check_block(stmt.body)
            self._loop_depth -= 1
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
                self._depth_opaque = True  # deferred-relin depth not modeled
                return result
            if expr.op in ("+", "-", "*", "/"):
                result = common_type(left_t, right_t)
                if expr.op == "*" and is_encrypted(result):
                    result.depth = max(
                        getattr(left_t, 'depth', 0),
                        getattr(right_t, 'depth', 0),
                    ) + 1
                    self.observed_max_depth = max(self.observed_max_depth,
                                                  result.depth)
                    if self._loop_depth > 0:
                        self._enc_mul_in_loop = True
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
            arg_types = [self._check_expr(arg.value) for arg in expr.args]
            # Domain enforcement
            if isinstance(expr.func, ast.Ident):
                self._check_domain_call(expr.func.name, expr.loc)
                # Chebyshev with a statically-resolvable degree is a MODELED
                # subcircuit: OpenFHE's Paterson-Stockmeyer evaluation consumes
                # ceil(log2(degree+1)) + 1 levels (matches the documented
                # degree->depth table: 5->4, 13->5, 27->6, 59->7, 119->8, ...).
                if expr.func.name == "chebyshev":
                    d = self._resolve_int_arg(expr, "degree")
                    if d is not None and d > 0:
                        import math
                        used = math.ceil(math.log2(d + 1)) + 1
                        in_depth = 0
                        positional = [a for a in expr.args if not a.name]
                        if len(positional) > 1:
                            idx = expr.args.index(positional[1])
                            in_depth = getattr(arg_types[idx], "depth", 0) or 0
                        result = enc_of(F64)
                        result.depth = in_depth + used
                        self.observed_max_depth = max(self.observed_max_depth,
                                                      result.depth)
                        if self._loop_depth > 0:
                            self._enc_mul_in_loop = True
                        if result.depth > self.max_depth:
                            # Warning (not error): the per-degree model is the
                            # documented table, but implementations may differ
                            # by a level.
                            self.errors.warn(
                                f"chebyshev chain depth ~{result.depth} "
                                f"(degree {d} consumes {used} levels) exceeds "
                                f"scheme depth {self.max_depth}",
                                expr.loc)
                        return result
                # Depth-opaque constructs: their internal multiplicative depth
                # (chebyshev with non-literal degree, external C++, closure
                # bodies applied by combinators) is not statically modeled, so
                # the over-provision check must stay silent for this program.
                if expr.func.name in DEPTH_OPAQUE_FNS:
                    self._depth_opaque = True
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
            if (isinstance(expr.obj, ast.Ident) and expr.obj.name == "scheme"
                    and expr.method == "override"
                    and any(a.name == "security" for a in expr.args)):
                self.has_security_override = True
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
                if fi.name == "ring_dim" and isinstance(fi.value, ast.IntLiteral):
                    self.ring_dims.add(int(fi.value.value))
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
            self._loop_depth += 1
            self._check_block(expr.body)
            self._loop_depth -= 1
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
