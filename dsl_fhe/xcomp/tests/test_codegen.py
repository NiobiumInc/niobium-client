"""Tests for OpenFHE C++ code generation."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lexer import lex
from parser import parse
from semantic import analyze
from codegen import generate


def compile_str(source: str) -> dict[str, str]:
    tokens = lex(source)
    program = parse(tokens)
    sa = analyze(program)
    return generate(program, sa)


def test_shared_header_constants():
    files = compile_str("const X: u32 = 8")
    header = files["nb_shared.h"]
    assert "constexpr uint32_t X = 8;" in header


def test_shared_header_enum():
    files = compile_str("enum Size { Small, Large }")
    header = files["nb_shared.h"]
    assert "enum Size" in header
    assert "Small = 0" in header
    assert "Large = 1" in header


def test_shared_header_struct():
    files = compile_str("struct Point { x: f64, y: f64 }")
    header = files["nb_shared.h"]
    assert "struct Point" in header
    assert "double x;" in header
    assert "double y;" in header


def test_enc_type_to_ciphertext():
    files = compile_str("""
    fn f(x: enc<f64>) -> enc<f64> {
        return x
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "Ciphertext<DCRTPoly>" in impl


def test_vec_type_to_vector():
    files = compile_str("""
    fn f(x: vec<f64>) -> vec<f64> {
        return x
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "std::vector<double>" in impl


def test_stage_generates_cpp():
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    @server @stage(name: "compute")
    fn compute(inst: Instance) -> enc<f64> {
        return zero()
    }
    """)
    assert "compute.cpp" in files
    cpp = files["compute.cpp"]
    assert "int main(" in cpp
    assert "auto size = static_cast" in cpp


def test_stage_with_hardware():
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    @server @stage(name: "compute") @hardware(cache_key: ["wl"])
    fn compute(inst: Instance) -> enc<f64> {
        return zero()
    }
    """)
    cpp = files["compute.cpp"]
    # Client API (libnbfhetch) — no compiler-only NIOBIUM_COMPILER gating.
    assert "NIOBIUM_COMPILER" not in cpp
    assert '#include "niobium/compiler.h"' in cpp
    assert "niobium::compiler().init" in cpp
    # Cooperative auto-tagging: host owns lifecycle, hooks tag inputs/keys.
    assert "enable_auto_tagging" in cpp
    assert "cache_parameters" in cpp
    assert "is_cache_valid" in cpp
    assert 'probe("result"' in cpp
    # replay() takes no Target argument in the client API.
    assert "replay()" in cpp
    assert "Target" not in cpp
    assert "global_key_cache" not in cpp
    assert "niobium_hw" not in cpp


def test_bool_param_flag():
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    @server @stage(name: "test")
    fn test_fn(inst: Instance, count_only: bool) -> enc<f64> {
        return zero()
    }
    """)
    cpp = files["test.cpp"]
    assert "bool count_only = false;" in cpp
    assert '--count_only' in cpp


def test_key_loading_for_server():
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    @server @stage(name: "srv") @hardware(cache_key: ["wl"])
    fn srv(inst: Instance) -> enc<f64> {
        return zero()
    }
    """)
    cpp = files["srv.cpp"]
    assert "CryptoContext<DCRTPoly> cc;" in cpp
    assert "DeserializeFromFile" in cpp
    assert "DeserializeEvalMultKey" in cpp
    assert "DeserializeEvalAutomorphismKey" in cpp
    # In cooperative auto-tagging mode the context/keys/inputs are captured by
    # the instrumented-OpenFHE deserialize hooks, so the generated code no
    # longer emits explicit capture_crypto_context()/tag_keys()/tag_input().
    assert "capture_crypto_context" not in cpp
    assert "tag_keys" not in cpp
    assert "enable_auto_tagging" in cpp


def test_shared_fn_forward_decl():
    files = compile_str("""
    fn helper(x: f64) -> f64 {
        return x
    }
    """)
    header = files["nb_shared.h"]
    assert "double helper(double x);" in header
    impl = files["nb_shared.cpp"]
    assert "double helper(double x) {" in impl


def test_pipe_desugar():
    """Pipe should desugar to function calls in output."""
    files = compile_str("""
    fn f() {
        let x = data |> process
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "process(data)" in impl


def test_norelin_operator():
    files = compile_str("""
    fn f() {
        let x = ct1 *_norelin ct2
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalMultNoRelin" in impl


def test_closure_generation():
    files = compile_str("""
    fn f() {
        let g = |x| x + 1
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "[&]" in impl


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
