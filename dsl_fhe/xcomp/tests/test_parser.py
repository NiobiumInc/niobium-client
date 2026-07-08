"""Tests for the nb parser."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcomp.lexer import lex
from xcomp.parser import parse
import xcomp.ast_nodes as ast


def parse_str(source: str) -> ast.Program:
    tokens = lex(source)
    return parse(tokens)


def test_const_decl():
    prog = parse_str("const X: u32 = 42")
    assert len(prog.items) == 1
    c = prog.items[0]
    assert isinstance(c, ast.ConstDecl)
    assert c.name == "X"
    assert isinstance(c.type_ann, ast.PrimitiveType)
    assert c.type_ann.name == "u32"
    assert isinstance(c.value, ast.IntLiteral)
    assert c.value.value == 42


def test_enum_decl():
    prog = parse_str("enum Color { Red, Green, Blue }")
    assert len(prog.items) == 1
    e = prog.items[0]
    assert isinstance(e, ast.EnumDecl)
    assert e.name == "Color"
    assert e.variants == ["Red", "Green", "Blue"]


def test_struct_decl():
    prog = parse_str("struct Point { x: f64, y: f64 }")
    s = prog.items[0]
    assert isinstance(s, ast.StructDecl)
    assert s.name == "Point"
    assert len(s.fields) == 2
    assert s.fields[0].name == "x"


def test_wire_decl():
    prog = parse_str("wire Params { key: PublicKey, ctx: CryptoContext }")
    w = prog.items[0]
    assert isinstance(w, ast.WireDecl)
    assert w.name == "Params"
    assert len(w.fields) == 2


def test_scheme_decl():
    prog = parse_str("""scheme CKKS {
        security: 128-classic
        precision: 42 bits
        depth: 23
        bootstrap: auto
    }""")
    s = prog.items[0]
    assert isinstance(s, ast.SchemeDecl)
    assert s.name == "CKKS"
    assert len(s.fields) == 4
    assert s.fields[0].key == "security"
    assert s.fields[0].value == "128-classic"
    assert s.fields[1].value == "42 bits"
    assert s.fields[2].value == 23
    assert s.fields[3].value == "auto"


def test_requires_decl():
    prog = parse_str("requires { add, mul, rotate }")
    r = prog.items[0]
    assert isinstance(r, ast.RequiresDecl)
    assert r.capabilities == ["add", "mul", "rotate"]


def test_use_decl():
    prog = parse_str("use shared::*")
    u = prog.items[0]
    assert isinstance(u, ast.UseDecl)
    assert u.module_path == ["shared"]
    assert u.imported == "*"


def test_simple_fn():
    prog = parse_str("""
    fn add(a: f64, b: f64) -> f64 {
        return a + b
    }
    """)
    fn = prog.items[0]
    assert isinstance(fn, ast.FnDecl)
    assert fn.name == "add"
    assert len(fn.params) == 2
    assert isinstance(fn.return_type, ast.PrimitiveType)
    assert fn.return_type.name == "f64"


def test_annotated_fn():
    prog = parse_str("""
    @server @stage(name: "compute")
    fn compute(inst: Instance) -> enc<f64> {
        return zero()
    }
    """)
    fn = prog.items[0]
    assert isinstance(fn, ast.FnDecl)
    assert len(fn.annotations) == 2
    assert fn.annotations[0].name == "server"
    assert fn.annotations[1].name == "stage"
    assert fn.annotations[1].args["name"] == "compute"


def test_enc_type():
    prog = parse_str("fn f(x: enc<vec<f64>>) -> enc<f64> { return x }")
    fn = prog.items[0]
    p = fn.params[0]
    assert isinstance(p.type_ann, ast.EncType)
    assert isinstance(p.type_ann.inner, ast.VecType)
    assert isinstance(p.type_ann.inner.elem, ast.PrimitiveType)


def test_let_stmt():
    prog = parse_str("fn f() { let x: u32 = 5 }")
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.LetStmt)
    assert stmt.name == "x"
    assert isinstance(stmt.value, ast.IntLiteral)


def test_if_stmt():
    prog = parse_str("""
    fn f(x: bool) {
        if x {
            return 1
        } else {
            return 2
        }
    }
    """)
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.IfStmt)
    assert stmt.else_block is not None


def test_for_stmt():
    prog = parse_str("""
    fn f() {
        for i in items {
            process(i)
        }
    }
    """)
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.ForStmt)
    assert stmt.pattern.names == ["i"]


def test_for_destructured():
    prog = parse_str("""
    fn f() {
        for (i, x) in enumerate(items) {
            process(i, x)
        }
    }
    """)
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.ForStmt)
    assert stmt.pattern.names == ["i", "x"]


def test_match_stmt():
    prog = parse_str("""
    enum Size { Small, Large }
    fn f(s: Size) {
        match s {
            Small => return 1,
            Large => return 2,
        }
    }
    """)
    fn = prog.items[1]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.MatchStmt)
    assert len(stmt.arms) == 2


def test_binary_expr():
    prog = parse_str("fn f() { let x = a + b * c }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    # Should parse as a + (b * c) due to precedence
    assert isinstance(let.value, ast.BinaryExpr)
    assert let.value.op == "+"
    assert isinstance(let.value.right, ast.BinaryExpr)
    assert let.value.right.op == "*"


def test_pipe_expr():
    prog = parse_str("fn f() { let x = data |> transpose |> batch(n) }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.PipeExpr)
    # Left-associative: (data |> transpose) |> batch(n)
    assert isinstance(let.value.left, ast.PipeExpr)


def test_star_norelin():
    prog = parse_str("fn f() { let x = a *_norelin b }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.BinaryExpr)
    assert let.value.op == "*_norelin"


def test_closure():
    prog = parse_str("fn f() { let g = |x| x + 1 }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.Closure)
    assert len(let.value.params) == 1
    assert let.value.params[0].name == "x"


def test_named_args():
    prog = parse_str("fn f() { call(a, name: b, level: 5) }")
    fn = prog.items[0]
    expr_stmt = fn.body.stmts[0]
    call = expr_stmt.expr
    assert isinstance(call, ast.CallExpr)
    assert len(call.args) == 3
    assert call.args[0].name is None
    assert call.args[1].name == "name"
    assert call.args[2].name == "level"


def test_field_access():
    prog = parse_str("fn f() { let x = inst.ring_dim }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.FieldAccess)
    assert let.value.field_name == "ring_dim"


def test_method_call():
    prog = parse_str("fn f() { list.push(item) }")
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt.expr, ast.MethodCall)
    assert stmt.expr.method == "push"


def test_array_literal():
    prog = parse_str("fn f() { let x = [1, 2, 3] }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.ArrayLiteral)
    assert len(let.value.elements) == 3


def test_struct_literal():
    prog = parse_str("""
    struct Point { x: f64, y: f64 }
    fn f() { let p = Point { x: 1.0, y: 2.0 } }
    """)
    fn = prog.items[1]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.StructLiteral)
    assert let.value.type_name == "Point"
    assert len(let.value.fields) == 2


def test_assert_stmt():
    prog = parse_str('fn f() { assert x > 0, "must be positive" }')
    fn = prog.items[0]
    stmt = fn.body.stmts[0]
    assert isinstance(stmt, ast.AssertStmt)
    assert stmt.message == "must be positive"


def test_index_expr():
    prog = parse_str("fn f() { let x = arr[i] }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.IndexExpr)


def test_unary_negation():
    prog = parse_str("fn f() { let x = -y }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.UnaryExpr)
    assert let.value.op == "-"


def test_cast_expr():
    prog = parse_str("fn f() { let x = y as i64 }")
    fn = prog.items[0]
    let = fn.body.stmts[0]
    assert isinstance(let.value, ast.CastExpr)


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
