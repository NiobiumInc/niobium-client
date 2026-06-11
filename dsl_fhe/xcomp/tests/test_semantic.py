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


def test_chebyshev_depth_modeled_with_literal_degree():
    # A literal (or const) degree makes chebyshev a MODELED subcircuit:
    # ceil(log2(d+1)) + 1 levels are charged (59 -> 7), so the over-provision
    # warning fires accurately on a straight-line program...
    sa = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>, b: enc<f64>) -> enc<f64> {
        let x = a * b
        return chebyshev(|v| v, x, domain: [-1.0, 1.0], degree: 59)
    }
    """)
    assert sa.observed_max_depth == 8, sa.observed_max_depth  # 1 mul + 7 cheb
    assert any("greatly exceeds" in w for w in sa.errors.warnings), sa.errors.warnings
    # ...and a chain exceeding the budget warns.
    sa2 = check("""
    scheme CKKS { security: not_set depth: 6 }
    fn f(a: enc<f64>) -> enc<f64> {
        return chebyshev(|v| v, a, domain: [-1.0, 1.0], degree: 59)
    }
    """)
    assert any("chebyshev chain depth" in w for w in sa2.errors.warnings), sa2.errors.warnings


def test_depth_overprovision_suppressed_when_opaque():
    # A NON-literal degree keeps chebyshev depth-opaque — the over-provision
    # warning must stay silent rather than give a false recommendation.
    sa = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>, b: enc<f64>, d: u32) -> enc<f64> {
        let x = a * b
        return chebyshev(|v| v, x, domain: [-1.0, 1.0], degree: d)
    }
    """)
    assert not any("greatly exceeds" in w for w in sa.errors.warnings), sa.errors.warnings


def test_parameter_advisor():
    # logQ = 60 + 18*50 = 960 -> 128-classic needs N >= 65536. A declared
    # ring_dim below target without a security override warns; with an
    # override it is only noted.
    src = """
    scheme CKKS { security: 128-classic precision: 50 first_mod: 60 depth: 18 }
    struct Instance { ring_dim: u32 }
    fn instance() -> Instance { return Instance { ring_dim: 2048 } }
    """
    sa = check(src)
    assert any("needs ring_dim >= 65536" in n for n in sa.errors.notes), sa.errors.notes
    assert any("cannot reach 128-classic" in w for w in sa.errors.warnings), sa.errors.warnings

    sa2 = check(src + """
    fn keygen_stage() -> u32 {
        scheme.override(security: not_set)
        return 0
    }
    """)
    assert not any("cannot reach" in w for w in sa2.errors.warnings), sa2.errors.warnings
    assert any("dev profiles" in n for n in sa2.errors.notes), sa2.errors.notes


def test_chebyshev_max_error_selects_degree():
    # max_error: resolves a degree from the ladder at compile time, charges
    # the implied depth, and reports a note. Sigmoid on [-5,5] @ 1e-3 -> 13.
    sa = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>) -> enc<f64> {
        return chebyshev(|x| 1.0 / (1.0 + exp(0.0 - x)), a,
                         domain: [-5.0, 5.0], max_error: 0.001)
    }
    """)
    assert not sa.errors.has_errors(), sa.errors.errors
    assert any("selected degree 13" in n for n in sa.errors.notes), sa.errors.notes
    assert sa.observed_max_depth == 5, sa.observed_max_depth  # ceil(log2(14))+1

    # Unreachable tolerance -> hard error with guidance.
    sa2 = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>) -> enc<f64> {
        return chebyshev(|x| abs(x), a, domain: [-1.0, 1.0], max_error: 0.0000001)
    }
    """)
    assert any("unreachable" in str(e) for e in sa2.errors.errors), sa2.errors.errors

    # Non-evaluable closure -> error directing to explicit degree.
    sa3 = check("""
    scheme CKKS { security: not_set depth: 20 }
    fn f(a: enc<f64>, b: enc<f64>) -> enc<f64> {
        return chebyshev(|x| x * b, a, domain: [-1.0, 1.0], max_error: 0.001)
    }
    """)
    assert any("not compile-time evaluable" in str(e) for e in sa3.errors.errors), sa3.errors.errors


def test_encryptors_independent_forbids_cross_owner_packing():
    common = """
    scheme CKKS { security: not_set depth: 5 }
    struct Instance { ring_dim: u32, n_slots: u32, n_feat: u32 }
    fn datadir(inst: Instance) -> path { root() / "d" }
    wire CryptoParams { context: CryptoContext, public_key: PublicKey,
                        eval_mult_key: EvalMultKey }
    wire EncRecord { ct: enc<vec<f64>> }
    """
    # Column-major packing (slots = records) in an independent-encryptor
    # stage is the privacy violation the skill warns about -> compile error.
    sa = check(common + """
    @client @stage(name: "encrypt_all")
    @encryptors(independent)
    fn encrypt_all(inst: Instance) -> reads(CryptoParams), writes(EncRecord) {
        let params = load(CryptoParams, from: datadir(inst))
        let m = load_matrix<f64>(datadir(inst) / "all_owners.bin", inst.n_feat)
        let column = m[0..inst.n_slots, 0]
        return EncRecord { ct: encrypt(params.public_key, column) }
    }
    """)
    assert any("cross-owner" in str(e) for e in sa.errors.errors), sa.errors.errors

    # Per-record (row) packing is fine in independent mode.
    sa2 = check(common + """
    @client @stage(name: "encrypt_one")
    @encryptors(independent)
    fn encrypt_one(inst: Instance, owner: u32) -> reads(CryptoParams), writes(EncRecord) {
        let params = load(CryptoParams, from: datadir(inst))
        let m = load_matrix<f64>(datadir(inst) / "mine.bin", inst.n_feat)
        let row = m[owner]
        return EncRecord { ct: encrypt(params.public_key, row) }
    }
    """)
    assert not any("cross-owner" in str(e) for e in sa2.errors.errors), sa2.errors.errors

    # Without the annotation (single-encryptor default), column-major
    # packing remains the recommended pattern.
    sa3 = check(common + """
    @client @stage(name: "encrypt_db")
    fn encrypt_db(inst: Instance) -> reads(CryptoParams), writes(EncRecord) {
        let params = load(CryptoParams, from: datadir(inst))
        let m = load_matrix<f64>(datadir(inst) / "my_db.bin", inst.n_feat)
        let column = m[0..inst.n_slots, 0]
        return EncRecord { ct: encrypt(params.public_key, column) }
    }
    """)
    assert not any("cross-owner" in str(e) for e in sa3.errors.errors), sa3.errors.errors

    # Unknown mode is rejected.
    sa4 = check(common + """
    @client @stage(name: "f")
    @encryptors(sideways)
    fn f(inst: Instance) -> u32 { return 0 }
    """)
    assert any("unknown mode" in str(e) for e in sa4.errors.errors), sa4.errors.errors


def test_parameter_advisor_headroom():
    # A ring_dim pinned ABOVE the security minimum (hardware target) gets a
    # headroom report: spare modulus bits -> larger q_i (capped at the
    # 59-bit limb) and/or more depth.
    sa = check("""
    scheme CKKS { security: 128-classic precision: 50 first_mod: 60 depth: 15 }
    struct Instance { ring_dim: u32 }
    fn instance() -> Instance { return Instance { ring_dim: 65536 } }
    """)
    note = next((n for n in sa.errors.notes if "headroom" in n), "")
    assert "headroom at N=65536: 962 bits" in note, sa.errors.notes
    assert "q_i up to 59 (+9 bits/level precision)" in note, note
    assert "depth up to 34 (+19 levels)" in note, note


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
