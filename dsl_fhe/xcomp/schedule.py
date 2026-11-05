"""The stage schedule: a server entry point's sequence of stages, as data.

`nbc compile --schedule` writes `<outdir>/nb_schedule.json` (schema
nb.stage_schedule, version 1) for every server stage whose body is only a
schedule of other stages: calls to `@server @stage` functions, possibly in
`for` loops over ranges. The loops stay loops; loop bounds and stage
arguments are kept as symbolic expressions over the loop variables, the entry
point's arguments and the build parameters (top-level constants, whose
compile-time values are recorded once). Every step records the stage it runs,
its arguments and the state it reads and writes: the wire types of its
`load`/`save` calls and of its `reads(...)`/`writes(...)` clauses, with their
paths as symbolic expressions of the step's arguments.

A server stage that calls another stage but does anything else (a value
computed outside a stage, a branch, an argument that is not symbolic, a
stage whose state cannot be described) has no schedule; the reason is given
as one line. The format is in dsl_fhe/docs/STAGE_SCHEDULE.md.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as ast
from .codegen import stage_binary_name
from .modules import base_name

FILE = "nb_schedule.json"
SCHEMA = "nb.stage_schedule"
VERSION = 1

# Integer and path operators a symbolic expression may use. "/" joins paths
# when its left operand is a path and divides otherwise, as in the language.
_OPS = ("+", "-", "*", "/", "%")
_PREC = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2}
# Constructs a load() or save() must not sit inside to have a fixed place in
# the step: a loop runs it a number of times, a branch maybe not at all.
_NESTED = (ast.ForStmt, ast.IfStmt, ast.MatchStmt, ast.ForExpr, ast.IfExpr,
           ast.MatchExpr, ast.Closure)


class NoSchedule(Exception):
    """Why an entry point has no schedule: one line, with its location."""

    def __init__(self, reason: str, node: ast.Node | None = None):
        loc = getattr(node, "loc", None)
        where = f"{loc.file}:{loc.line}: " if loc is not None else ""
        super().__init__(f"{where}{reason}")


def _domain(fn: ast.FnDecl) -> str:
    dom = "shared"
    for ann in fn.annotations:
        if ann.name in ("client", "server"):
            dom = ann.name
    return dom


def _walk(node, skip=()):
    """Every AST node under `node`; the subtrees of `skip` types are not
    entered (their root is yielded)."""
    if is_dataclass(node):
        yield node
        if isinstance(node, skip):
            return
        for f in fields(node):
            yield from _walk(getattr(node, f.name), skip)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item, skip)


def _calls(node, skip=()):
    for n in _walk(node, skip):
        if isinstance(n, ast.CallExpr) and isinstance(n.func, ast.Ident):
            yield n


def _type_text(t) -> str:
    if t is None:
        return "?"
    if isinstance(t, (ast.PrimitiveType, ast.NamedType)):
        return t.name
    if isinstance(t, ast.EncType):
        return f"enc<{_type_text(t.inner)}>"
    if isinstance(t, ast.VecType):
        return f"vec<{_type_text(t.elem)}>"
    if isinstance(t, ast.MatType):
        return f"mat<{_type_text(t.elem)}>"
    if isinstance(t, ast.TupleType):
        return "(" + ", ".join(_type_text(e) for e in t.elements) + ")"
    return type(t).__name__


def text(e) -> str:
    """A symbolic expression in the language's own spelling."""
    if isinstance(e, bool):
        return "true" if e else "false"
    if isinstance(e, str):
        return '"' + e.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(e, (int, float)):
        return repr(e)
    for k in ("var", "arg", "param"):
        if k in e:
            return e[k]
    if "field" in e:
        return f"{_wrap(e['of'], 3)}.{e['field']}"
    if "call" in e:
        return f"{e['call']}({', '.join(text(a) for a in e['args'])})"
    op, args = e["op"], e["args"]
    if len(args) == 1:
        return f"{op}{_wrap(args[0], 3)}"
    p = _PREC[op]
    return f"{_wrap(args[0], p)} {op} {_wrap(args[1], p + 1)}"


def _wrap(e, prec: int) -> str:
    if isinstance(e, dict) and "op" in e:
        mine = 3 if len(e["args"]) == 1 else _PREC[e["op"]]
        if mine < prec:
            return f"({text(e)})"
    return text(e)


def _loop_vars(e, out: list):
    if isinstance(e, dict):
        if "var" in e:
            if e["var"] not in out:
                out.append(e["var"])
            return
        for v in e.values():
            _loop_vars(v, out)
    elif isinstance(e, list):
        for v in e:
            _loop_vars(v, out)


class _Builder:
    def __init__(self, program: ast.Program, analyzer):
        self.program = program
        self.sa = analyzer
        root = program.module_name
        self.fns = {}
        self.stages = {}          # canonical function name -> (stage name, FnDecl)
        self.wires = {}
        for item in program.items:
            if isinstance(item, ast.FnDecl):
                canon = f"{item.module}::{item.name}" if item.module else item.name
                self.fns[canon] = item
                name = stage_binary_name(item, root)
                if name is not None:
                    self.stages[canon] = (name, item)
            elif isinstance(item, ast.WireDecl):
                canon = f"{item.module}::{item.name}" if item.module else item.name
                self.wires[canon] = item
        self.params: dict[str, object] = {}

    # ----- symbolic expressions -----

    def sym(self, e, env: dict, calls: bool, what: str):
        """`e` as a symbolic expression over `env` (name -> expression) and
        the build parameters; NoSchedule when it is anything else."""
        if isinstance(e, (ast.IntLiteral, ast.FloatLiteral, ast.BoolLiteral, ast.StringLiteral)):
            return e.value
        if isinstance(e, ast.Ident):
            if e.name in env:
                return env[e.name]
            sym = self.sa.global_scope.lookup(e.name)
            if sym is not None and getattr(sym, "is_const", False):
                v = sym.const_value
                if not isinstance(v, (bool, int, float, str)):
                    raise NoSchedule(f"{what}: the constant {e.name} has no compile-time value", e)
                self.params[e.name] = v
                return {"param": e.name}
            raise NoSchedule(f"{what}: {e.name} is not a loop variable, an argument or a constant", e)
        if isinstance(e, ast.FieldAccess):
            return {"field": e.field_name, "of": self.sym(e.obj, env, calls, what)}
        if isinstance(e, ast.UnaryExpr) and e.op == "-":
            v = self.sym(e.operand, env, calls, what)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return -v
            return {"op": "-", "args": [v]}
        if isinstance(e, ast.BinaryExpr) and e.op in _OPS:
            return {"op": e.op, "args": [self.sym(e.left, env, calls, what),
                                         self.sym(e.right, env, calls, what)]}
        if calls and isinstance(e, ast.CallExpr) and isinstance(e.func, ast.Ident) \
                and not any(a.name for a in e.args) and e.func.name not in self.stages \
                and e.func.name not in ("load", "save", "load_all"):
            return {"call": e.func.name, "args": [self.sym(a.value, env, calls, what) for a in e.args]}
        raise NoSchedule(f"{what}: not a loop variable, an argument, a constant or arithmetic on them", e)

    def path(self, e, env: dict, what: str):
        expr = self.sym(e, env, True, what)
        vars_: list = []
        _loop_vars(expr, vars_)
        return {"text": text(expr), "expr": expr, "vars": vars_}

    # ----- one entry point -----

    def entry(self, canon: str) -> dict:
        name, fn = self.stages[canon]
        args = []
        env = {}
        for p in fn.params:
            a = {"name": p.name, "type": _type_text(p.type_ann)}
            if p.default is not None:
                a["default"] = self.sym(p.default, {}, False, f"the default of {p.name}")
            args.append(a)
            env[p.name] = {"arg": p.name}
        for spec in fn.io_specs:
            if spec.kind in ("writes", "writes_plaintext"):
                raise NoSchedule("the entry point writes its result itself; a schedule's results are saved by "
                                 "its stages", spec)
        try:
            steps = self.steps(fn.body.stmts if fn.body else [], env)
        except NoSchedule:
            # Computing on loaded state is the reason that says most; it
            # comes after the load that is the first statement out of place.
            arith = self._arithmetic_on_state(fn.body)
            if arith is not None:
                raise NoSchedule("arithmetic on encrypted values outside a stage", arith) from None
            raise
        return {"entry": name, "args": args, "steps": steps}

    @staticmethod
    def _arithmetic_on_state(body):
        """The first statement of an entry point's body that computes on a
        value loaded in it (bound by a let from load(), or from such a value)."""
        state: set[str] = set()

        def mentions(e) -> bool:
            return any(isinstance(n, ast.Ident) and n.name in state for n in _walk(e))

        for s in _walk(body):
            if not isinstance(s, (ast.LetStmt, ast.AssignStmt, ast.ExprStmt)):
                continue
            value = s.value if not isinstance(s, ast.ExprStmt) else s.expr
            ops = [n for n in _walk(value) if isinstance(n, (ast.BinaryExpr, ast.UnaryExpr))]
            if any(mentions(n) for n in ops):
                return s
            if isinstance(s, ast.LetStmt) and (mentions(value) or any(
                    c.func.name in ("load", "load_all") for c in _calls(value))):
                state.update([s.name] if s.name else (s.tuple_names or []))
        return None

    def steps(self, stmts, env: dict) -> list:
        steps = []
        for s in stmts:
            if isinstance(s, ast.ForStmt):
                steps.append(self.loop(s, env))
            elif isinstance(s, ast.ExprStmt) and isinstance(s.expr, ast.CallExpr) \
                    and isinstance(s.expr.func, ast.Ident) and s.expr.func.name in self.stages:
                steps.append(self.step(s.expr, env))
            else:
                raise NoSchedule(self._why(s), s)
        return steps

    def _why(self, s) -> str:
        loaded = any(c.func.name in ("load", "load_all") for c in _calls(s))
        if isinstance(s, ast.LetStmt):
            names = s.name or ", ".join(s.tuple_names or [])
            if loaded:
                return f"state loaded outside a stage (let {names})"
            return f"a value computed outside a stage (let {names})"
        if isinstance(s, ast.AssignStmt):
            return "an assignment outside a stage"
        if isinstance(s, (ast.IfStmt, ast.MatchStmt)):
            return "a branch outside a stage: every step of a schedule runs"
        if isinstance(s, ast.ReturnStmt):
            return "the entry point returns a value; a schedule's results are saved by its stages"
        if isinstance(s, ast.ExprStmt) and isinstance(s.expr, ast.CallExpr) and isinstance(s.expr.func, ast.Ident):
            f = s.expr.func.name
            if f == "save":
                return "state saved outside a stage"
            if loaded:
                return "state loaded outside a stage"
            return f"a call to {f}, which is not a stage"
        return f"a statement other than a stage call or a for loop ({type(s).__name__})"

    def loop(self, s: ast.ForStmt, env: dict) -> dict:
        names = s.pattern.names if s.pattern else []
        if len(names) != 1 or not isinstance(s.iterable, ast.RangeExpr):
            raise NoSchedule("a for loop over a value, not over a range lo..hi", s)
        var = names[0]
        what = f"the bound of the loop over {var}"
        lo = self.sym(s.iterable.start, env, False, what)
        hi = self.sym(s.iterable.end, env, False, what)
        if s.iterable.inclusive:
            hi = hi + 1 if isinstance(hi, int) and not isinstance(hi, bool) else {"op": "+", "args": [hi, 1]}
        inner = dict(env)
        inner[var] = {"var": var}
        return {"for": var, "from": lo, "to": hi, "body": self.steps(s.body.stmts if s.body else [], inner)}

    def step(self, call: ast.CallExpr, env: dict) -> dict:
        canon = call.func.name
        name, fn = self.stages[canon]
        if _domain(fn) != "server":
            raise NoSchedule(f"stage {name}: a {_domain(fn)} stage, not a server stage", call)
        params = [p.name for p in fn.params]
        given: dict = {}
        positional = [a for a in call.args if not a.name]
        if len(positional) > len(params):
            raise NoSchedule(f"stage {name}: {len(positional)} arguments for {len(params)} parameters", call)
        for p, a in zip(params, positional):
            given[p] = a.value
        for a in call.args:
            if a.name:
                if a.name not in params or a.name in given:
                    raise NoSchedule(f"stage {name}: argument {a.name} is not a parameter or is given twice", call)
                given[a.name] = a.value
        args = {}
        for p in fn.params:
            if p.name in given:
                args[p.name] = self.sym(given[p.name], env, False, f"stage {name}, argument {p.name}")
            elif p.default is not None:
                args[p.name] = self.sym(p.default, {}, False, f"stage {name}, the default of {p.name}")
            else:
                raise NoSchedule(f"stage {name}: no value for its parameter {p.name}", call)
        reads, writes = self.state(name, fn, args)
        hw = next((a.args for a in fn.annotations if a.name == "hardware"), None)
        return {"stage": name, "hardware": None if hw is None else {"cache_key": list(hw.get("cache_key", []))},
                "args": args, "reads": reads, "writes": writes}

    # ----- the state one stage reads and writes -----

    def state(self, name: str, fn: ast.FnDecl, args: dict):
        what = f"stage {name}"
        body = fn.body.stmts if fn.body else []
        if any(isinstance(n, ast.ReturnStmt) and n.value is not None for n in _walk(body)) \
                or fn.return_type is not None:
            raise NoSchedule(f"{what} returns its result; a step's results must be saved with save()", fn)
        for c in _calls(body):
            if c.func.name in self.stages:
                raise NoSchedule(f"{what} calls the stage {self.stages[c.func.name][0]}; a step is one stage", c)
        self._helpers_keep_no_state(what, body, set())
        top = {id(c) for c in _calls(body, _NESTED)}
        for c in _calls(body):
            if c.func.name in ("load", "save", "load_all") and id(c) not in top:
                raise NoSchedule(f"{what}: {c.func.name}() inside a loop, a branch or a closure", c)
            if c.func.name == "load_all":
                raise NoSchedule(f"{what}: load_all() reads a set of files the schedule cannot name", c)

        env = dict(args)
        wire_vars: dict[str, str] = {}
        reads, writes = [], []

        def add(lst, entry):
            if entry not in lst:
                lst.append(entry)

        for s in body:
            for c in _calls(s, _NESTED):
                if c.func.name == "load":
                    add(reads, self._load(what, c, env))
                elif c.func.name == "save":
                    add(writes, self._save(what, c, env, wire_vars))
            if isinstance(s, ast.LetStmt) and s.name:
                v = s.value
                while isinstance(v, ast.FieldAccess):
                    v = v.obj
                env.pop(s.name, None)
                wire_vars.pop(s.name, None)
                if isinstance(v, ast.CallExpr) and isinstance(v.func, ast.Ident) and v.func.name == "load" \
                        and s.value is v:
                    w = self._load_wire(c=v)
                    if w:
                        wire_vars[s.name] = w[0]
                elif isinstance(s.value, ast.StructLiteral) and s.value.type_name in self.wires:
                    wire_vars[s.name] = s.value.type_name
                else:
                    try:
                        env[s.name] = self.sym(s.value, env, True, what)
                    except NoSchedule:
                        pass
            elif isinstance(s, ast.LetStmt):
                for n in s.tuple_names or []:
                    env.pop(n, None)
                    wire_vars.pop(n, None)
            for n in _walk(s):
                if isinstance(n, ast.AssignStmt) and isinstance(n.target, ast.Ident):
                    env.pop(n.target.name, None)
                    wire_vars.pop(n.target.name, None)

        # The clauses: a wire they name and no load()/save() of the stage
        # reaches is still state of the step, at a path the stage's main() picks.
        for spec in fn.io_specs:
            lst = reads if spec.kind.startswith("reads") else writes
            for t in spec.types:
                if t.type_name:
                    if any(r["wire"] == t.type_name for r in lst):
                        continue
                    entry = {"wire": t.type_name, "path": None}
                    if t.index is not None:
                        entry["index"] = ("*" if isinstance(t.index, ast.Ident) and t.index.name == "*"
                                          else self.sym(t.index, env, False, what))
                    add(lst, entry)
                elif t.path_expr is not None:
                    add(lst, {"wire": None, "path": self.path(t.path_expr, env, what)})
        return reads, writes

    def _load_wire(self, c: ast.CallExpr):
        positional = [a for a in c.args if not a.name]
        if not positional:
            return None
        t = positional[0].value
        index = None
        if isinstance(t, ast.IndexExpr) and isinstance(t.obj, ast.Ident):
            t, index = t.obj, t.index
        if isinstance(t, ast.Ident) and t.name in self.wires:
            return t.name, index
        return None

    def _load(self, what: str, c: ast.CallExpr, env: dict) -> dict:
        w = self._load_wire(c)
        if w is None:
            raise NoSchedule(f"{what}: load() of something that is not a wire type", c)
        src = next((a.value for a in c.args if a.name == "from"), None)
        entry = {"wire": w[0], "path": None if src is None else self.path(src, env, f"{what}, the path of load({w[0]})")}
        if w[1] is not None:
            entry["index"] = self.sym(w[1], env, False, f"{what}, the index of load({w[0]})")
        return entry

    def _save(self, what: str, c: ast.CallExpr, env: dict, wire_vars: dict) -> dict:
        positional = [a for a in c.args if not a.name]
        v = positional[0].value if positional else None
        wire = None
        if isinstance(v, ast.StructLiteral) and v.type_name in self.wires:
            wire = v.type_name
        elif isinstance(v, ast.Ident):
            wire = wire_vars.get(v.name)
        if wire is None:
            raise NoSchedule(f"{what}: save() of a value whose wire type is not known "
                             "(save a WireType { ... } literal or a loaded wire)", c)
        dst = next((a.value for a in c.args if a.name == "to"), None)
        if dst is None:
            raise NoSchedule(f"{what}: save({wire}) without to:", c)
        return {"wire": wire, "path": self.path(dst, env, f"{what}, the path of save({wire})")}

    def _helpers_keep_no_state(self, what: str, body, seen: set):
        for c in _calls(body):
            f = self.fns.get(c.func.name)
            if f is None or c.func.name in self.stages or c.func.name in seen:
                continue
            seen.add(c.func.name)
            for d in _calls(f.body):
                if d.func.name in ("load", "save", "load_all"):
                    raise NoSchedule(f"{what}: {base_name(c.func.name)}() calls {d.func.name}(); a step's state "
                                     "must be loaded and saved by the stage itself", c)
            self._helpers_keep_no_state(what, f.body, seen)


def build(program: ast.Program, analyzer) -> tuple[dict | None, list[str]]:
    """The schedule document of `program` and one line per server stage that
    calls another stage but has no schedule. The document is None when no
    entry point qualifies."""
    b = _Builder(program, analyzer)
    entries, reasons = [], []
    for canon, (name, fn) in b.stages.items():
        if _domain(fn) != "server" or not fn.body:
            continue
        if not any(c.func.name in b.stages and c.func.name != canon for c in _calls(fn.body)):
            continue  # a stage that calls no stage is a step, not an entry point
        saved = dict(b.params)
        try:
            entries.append(b.entry(canon))
        except NoSchedule as e:
            b.params = saved
            reasons.append(f"nbc: no stage schedule for {name}: {e}")
    if not entries:
        return None, reasons
    doc = {"schema": SCHEMA, "version": VERSION,
           "params": {k: b.params[k] for k in sorted(b.params)}, "entries": entries}
    return doc, reasons
