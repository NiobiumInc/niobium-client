"""Tests for semantic analysis."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexer import lex
from parser import parse
from semantic import analyze


def check(source: str):
    tokens = lex(source)
    program = parse(tokens)
    return analyze(program)


def test_const_evaluation():
    sa = check("const X: u32 = 8")
    sym = sa.global_scope.lookup("X")
    assert sym is not None
    assert sym.is_const
    assert sym.const_value == 8


def test_const_arithmetic():
    sa = check("""
    const A: u32 = 4
    const B: u32 = A * 2
    """)
    sym = sa.global_scope.lookup("B")
    assert sym.const_value == 8


def test_enum_registration():
    sa = check("enum Size { Small, Medium, Large }")
    assert "Size" in sa.type_registry
    assert sa.type_registry["Size"].variants == ["Small", "Medium", "Large"]
    # Variants should be registered as constants
    sym = sa.global_scope.lookup("Small")
    assert sym is not None
    assert sym.is_const


def test_struct_registration():
    sa = check("struct Point { x: f64, y: f64 }")
    assert "Point" in sa.type_registry
    assert "x" in sa.type_registry["Point"].fields
    assert "y" in sa.type_registry["Point"].fields


def test_wire_registration():
    sa = check("wire Params { key: PublicKey }")
    assert "Params" in sa.type_registry
    assert "Params" in sa.wire_types


def test_wire_rejects_secret_key():
    sa = check("wire BadWire { sk: SecretKey }")
    assert sa.errors.has_errors()
    err_text = sa.errors.errors[0].message
    assert "SecretKey" in err_text
    assert "trust boundary" in err_text


def test_domain_enforcement_server_decrypt():
    sa = check("""
    @server
    fn bad_server() {
        let x = decrypt(sk, ct)
    }
    """)
    assert sa.errors.has_errors()
    err_text = sa.errors.errors[0].message
    assert "decrypt" in err_text
    assert "server" in err_text


def test_domain_enforcement_server_load_sk():
    sa = check("""
    @server
    fn bad_server() {
        let sk = load_secret_key()
    }
    """)
    assert sa.errors.has_errors()
    err_text = sa.errors.errors[0].message
    assert "load_secret_key" in err_text


def test_domain_client_decrypt_ok():
    sa = check("""
    @client
    fn good_client() {
        let x = decrypt(sk, ct)
    }
    """)
    # Should not error — client can decrypt
    domain_errors = [e for e in sa.errors.errors if "server" in e.message.lower()]
    assert len(domain_errors) == 0


def test_shared_fn_no_domain_check():
    sa = check("""
    fn helper(a: f64, b: f64) -> f64 {
        return a + b
    }
    """)
    assert not sa.errors.has_errors()


def test_scheme_config():
    sa = check("""
    scheme CKKS {
        depth: 23
        security: 128-classic
    }
    """)
    assert sa.scheme_config["depth"] == 23
    assert sa.max_depth == 23


def test_requires_capabilities():
    sa = check("requires { add, mul, rotate, chebyshev }")
    assert sa.required_capabilities == ["add", "mul", "rotate", "chebyshev"]


def test_fn_with_params():
    sa = check("""
    fn process(x: u32, y: f64) -> f64 {
        return y
    }
    """)
    sym = sa.global_scope.lookup("process")
    assert sym is not None


def test_let_type_inference():
    sa = check("""
    fn f() {
        let x = 42
        let y = 3.14
        let z = "hello"
    }
    """)
    assert not sa.errors.has_errors()


def test_undefined_name_warning():
    sa = check("""
    fn f() {
        let x = undefined_var
    }
    """)
    # Should produce a warning
    assert len(sa.errors.warnings) > 0


def test_for_loop_binding():
    sa = check("""
    fn f() {
        for i in items {
            let x = i
        }
    }
    """)
    # i should be bound in the for body
    assert not sa.errors.has_errors()


def test_depth_exceeded_error():
    # A multiplication chain deeper than the scheme budget is a hard error
    # (a static lower bound exceeding the budget guarantees noise overflow).
    sa = check("""
    scheme CKKS { security: not_set depth: 1 }
    fn f(a: enc<f64>, b: enc<f64>, c: enc<f64>) -> enc<f64> {
        return (a * b) * c
    }
    """)
    assert any("depth" in str(e).lower() for e in sa.errors.errors), sa.errors.errors


def test_depth_overprovision_warning():
    # Straight-line program (no loops, no depth-opaque constructs): a budget
    # far above the tracked chain warns about oversized ciphertexts.
    sa = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>, b: enc<f64>) -> enc<f64> {
        return (a * b) * a
    }
    """)
    assert any("greatly exceeds" in w for w in sa.errors.warnings), sa.errors.warnings


def test_depth_overprovision_suppressed_when_opaque():
    # chebyshev's internal depth is not modeled — the over-provision warning
    # must stay silent rather than give a false economy recommendation.
    sa = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>, b: enc<f64>) -> enc<f64> {
        let x = a * b
        return chebyshev(|v| v, x, domain: [-1.0, 1.0], degree: 59)
    }
    """)
    assert not any("greatly exceeds" in w for w in sa.errors.warnings), sa.errors.warnings


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
