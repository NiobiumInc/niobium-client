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
"""Compile-time Chebyshev approximation analysis (pure stdlib).

The closure passed to `chebyshev(|x| f(x), ...)` is plain arithmetic the
compiler can evaluate numerically. This module:

  - evaluates such closures (and the pure user functions they call) at
    sample points (`ClosureEvaluator`),
  - fits Chebyshev interpolants and measures the max approximation error
    on the domain (`max_error`),
  - selects the minimal degree from OpenFHE's recommended ladder meeting a
    target error (`select_degree`).

Used by the semantic analyzer (to resolve `max_error:` into a degree and
charge the implied depth) and by codegen (to emit the selected degree).
"""

from __future__ import annotations
import math
from . import ast_nodes as ast


class Unevaluable(Exception):
    """The expression uses constructs the compile-time evaluator can't run."""


# OpenFHE's recommended Paterson-Stockmeyer degree ladder; depth per entry
# is ceil(log2(d+1)) + 1.
DEGREE_LADDER = [5, 13, 27, 59, 119, 247, 495, 1007, 2031]

_MATH_FNS = {
    "exp": math.exp,
    "tanh": math.tanh,
    "abs": abs,
    "log2": math.log2,
}


class ClosureEvaluator:
    """Numerically evaluates a DSL closure of one parameter.

    consts: {name: float} for declared consts; fns: {name: FnDecl} for user
    functions (evaluated transitively when their bodies are pure arithmetic:
    a sequence of let-bindings ending in a return).
    """

    MAX_CALL_DEPTH = 16

    def __init__(self, consts: dict, fns: dict):
        self.consts = consts
        self.fns = fns

    def make_fn(self, closure: ast.Closure):
        """Return a python callable f(x) for the closure, or raise
        Unevaluable. Probes the closure at one point to fail fast."""
        if not closure.params or len(closure.params) != 1:
            raise Unevaluable("closure must take exactly one parameter")
        pname = closure.params[0].name

        def f(x: float) -> float:
            return self._eval_body(closure.body, {pname: float(x)}, 0)

        f(0.0)  # probe: raises Unevaluable on unsupported constructs
        return f

    # -- internals --------------------------------------------------------

    def _eval_body(self, body, env, depth):
        # Closure bodies are a single expression; fn bodies are Blocks.
        if isinstance(body, ast.Block):
            local = dict(env)
            for stmt in body.stmts:
                if isinstance(stmt, ast.LetStmt) and stmt.value is not None:
                    local[stmt.name] = self._eval(stmt.value, local, depth)
                elif isinstance(stmt, ast.ReturnStmt):
                    return self._eval(stmt.value, local, depth)
                elif isinstance(stmt, ast.ExprStmt):
                    return self._eval(stmt.expr, local, depth)
                else:
                    raise Unevaluable(f"statement {type(stmt).__name__}")
            raise Unevaluable("function body has no return")
        return self._eval(body, env, depth)

    def _eval(self, e, env, depth):
        if isinstance(e, (ast.IntLiteral, ast.FloatLiteral)):
            return float(e.value)
        if isinstance(e, ast.Ident):
            if e.name in env:
                return env[e.name]
            if e.name in self.consts:
                return float(self.consts[e.name])
            raise Unevaluable(f"unknown name '{e.name}'")
        if isinstance(e, ast.UnaryExpr):
            v = self._eval(e.operand, env, depth)
            if e.op == "-":
                return -v
            raise Unevaluable(f"unary {e.op}")
        if isinstance(e, ast.BinaryExpr):
            l = self._eval(e.left, env, depth)
            r = self._eval(e.right, env, depth)
            if e.op == "+":
                return l + r
            if e.op in ("-",):
                return l - r
            if e.op in ("*", "*_norelin"):
                return l * r
            if e.op == "/":
                return l / r
            if e.op == "^":
                return l ** r
            raise Unevaluable(f"operator {e.op}")
        if isinstance(e, ast.CallExpr) and isinstance(e.func, ast.Ident):
            name = e.func.name
            args = [self._eval(a.value, env, depth)
                    for a in e.args if not a.name]
            if name in _MATH_FNS:
                return _MATH_FNS[name](*args)
            fn = self.fns.get(name)
            if fn is not None and fn.body is not None:
                if depth >= self.MAX_CALL_DEPTH:
                    raise Unevaluable("call depth limit")
                if len(args) != len(fn.params):
                    raise Unevaluable(f"arity mismatch calling {name}")
                callee_env = {p.name: v for p, v in zip(fn.params, args)}
                return self._eval_body(fn.body, callee_env, depth + 1)
            raise Unevaluable(f"call to '{name}'")
        raise Unevaluable(type(e).__name__)


def _cheb_nodes_fit(f, lo, hi, degree):
    """Chebyshev interpolation coefficients (degree+1 of them) for f on
    [lo, hi], via the cosine formula at Chebyshev nodes."""
    n = degree + 1
    # f sampled at Chebyshev nodes mapped to [lo, hi]
    fx = []
    for k in range(n):
        t = math.cos(math.pi * (k + 0.5) / n)            # node in [-1, 1]
        x = 0.5 * (hi - lo) * t + 0.5 * (hi + lo)
        fx.append(f(x))
    coeffs = []
    for j in range(n):
        s = 0.0
        for k in range(n):
            s += fx[k] * math.cos(math.pi * j * (k + 0.5) / n)
        coeffs.append((2.0 / n) * s)
    coeffs[0] *= 0.5
    return coeffs


def _cheb_eval(coeffs, lo, hi, x):
    """Clenshaw evaluation of the Chebyshev series at x in [lo, hi]."""
    t = (2.0 * x - lo - hi) / (hi - lo)
    b1 = b2 = 0.0
    for c in reversed(coeffs[1:]):
        b1, b2 = 2.0 * t * b1 - b2 + c, b1
    return t * b1 - b2 + coeffs[0]


def max_error(f, lo, hi, degree, samples: int = 2001) -> float:
    """Max |f - chebfit_degree(f)| on a dense grid over [lo, hi]."""
    coeffs = _cheb_nodes_fit(f, lo, hi, degree)
    worst = 0.0
    for i in range(samples):
        x = lo + (hi - lo) * i / (samples - 1)
        err = abs(f(x) - _cheb_eval(coeffs, lo, hi, x))
        if err > worst:
            worst = err
    return worst


def select_degree(f, lo, hi, target: float):
    """Minimal ladder degree whose interpolant meets `target` max error on
    [lo, hi]. Returns (degree, est_error) or (None, best_error)."""
    best = float("inf")
    for d in DEGREE_LADDER:
        err = max_error(f, lo, hi, d)
        best = min(best, err)
        if err <= target:
            return d, err
    return None, best


def depth_for_degree(d: int) -> int:
    return math.ceil(math.log2(d + 1)) + 1
