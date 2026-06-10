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
    # Record/replay gate: ALL FHE ops on the record pass only; replay()/result()
    # exclusively in the cache-valid else-branch (zero FHE ops). The record run
    # must serialize OpenFHE's own result — replay must NOT run after recording
    # in the same pass (that overwrote correct results with sim output).
    assert "const bool _nb_replaying = niobium::compiler().is_cache_valid();" in cpp
    record_branch = cpp.split("if (!_nb_replaying) {")[1].split("} else {")[0]
    assert "replay()" not in record_branch
    assert "stop()" in record_branch


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


def _make_gen(source: str):
    """Build a CodeGenerator over `source` (for testing internals directly)."""
    import ast_nodes as ast
    from codegen import CodeGenerator
    program = parse(lex(source))
    sa = analyze(program)
    gen = CodeGenerator(program, sa)
    fn = next((i for i in program.items
               if isinstance(i, ast.FnDecl) and i.name == "generate_keys"), None)
    return gen, fn


KEYGEN_SRC = """
enum Sz {{ Toy, Big }}
struct Instance {{ size: Sz, ring_dim: u32 }}
wire CryptoParams {{ context: CryptoContext, public_key: PublicKey,
                     eval_mult_key: EvalMultKey, eval_rot_keys: EvalAutomorphismKeys }}
scheme CKKS {{ security: 128-classic {ring} depth: 20 }}
requires {{ add, mul, rotate }}
fn generate_keys(inst: Instance, mult_depth: u32 = 3) -> writes(CryptoParams) {{
    scheme.override(depth: mult_depth)
    if inst.size == Toy {{ scheme.override(security: not_set) }}
    let keys = keygen()
}}
"""


def test_rotation_keygen_dynamic_ring_dim():
    # ring_dim comes from the Instance struct (no literal in the scheme block):
    # rotation indices must be built at runtime from inst.ring_dim.
    gen, fn = _make_gen(KEYGEN_SRC.format(ring=""))
    gen._current_fn = fn
    code = gen._gen_keygen()
    assert "EvalRotateKeyGen" in code
    assert "inst.ring_dim / 2" in code      # runtime index range
    assert "_rot_indices" in code


def test_rotation_keygen_static_ring_dim():
    # ring_dim is a literal in the scheme block: indices listed at compile time.
    gen, fn = _make_gen(KEYGEN_SRC.format(ring="ring_dim: 2048"))
    gen._current_fn = fn
    code = gen._gen_keygen()
    assert "EvalRotateKeyGen" in code
    assert "_rot_indices = {1, 2," in code   # static literal vector


def test_depth_override_is_runtime():
    # scheme.override(depth: mult_depth) must wire the CLI param into the depth,
    # not silently keep the static scheme value.
    gen, fn = _make_gen(KEYGEN_SRC.format(ring=""))
    gen._current_fn = fn
    code = gen._gen_keygen()
    assert "SetMultiplicativeDepth(_nb_depth)" in code
    assert gen._fn_depth_override(fn) is not None


def test_scheme_override_detected_when_nested():
    # The security override lives inside an if-block; the detector must find it
    # regardless of nesting (regression guard for the one-level-deep scan).
    gen, fn = _make_gen(KEYGEN_SRC.format(ring=""))
    assert gen._fn_has_scheme_override(fn) is True


def test_encrypted_var_heuristic():
    # The prefix/name heuristic is a *fallback* used only when no type info is
    # available. Guard the known-good classifications, and pin the known
    # false-positives so any future type-driven fix is an intentional change.
    import ast_nodes as ast
    gen, _ = _make_gen(KEYGEN_SRC.format(ring=""))
    enc = lambda n: gen._is_encrypted_expr(ast.Ident(name=n))
    assert enc("ct") and enc("acc") and enc("result")     # genuinely encrypted
    assert not enc("THRESHOLD") and not enc("PAYLOAD_DIM")  # ALL_CAPS constants
    # KNOWN false positives — these are plaintext but match an encrypted prefix.
    # See CLAUDE.md "prefix-based encrypted detection". Update if types replace it.
    assert enc("result_index")   # matches "result"
    assert enc("hidden_dim")     # matches "hidden"
    assert enc("recon_loss")     # matches "recon"


def test_ct_minus_column_slice():
    # ct - <2D column slice> must wrap the vector in MakeCKKSPackedPlaintext
    # AND materialize it as an lvalue (OpenFHE's EvalSub takes Plaintext&,
    # which can't bind a freshly-created rvalue).
    files = compile_str("""
    fn f(eqry: vec<enc<vec<f64>>>, dataset: mat<f64>, n: u32) -> enc<vec<f64>> {
        let column = dataset[0..n, 0]
        let diff = eqry[0] - column
        return diff * diff
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "MakeCKKSPackedPlaintext(column)" in impl
    assert "auto _pt = " in impl          # lvalue materialization for EvalSub
    assert "EvalSub(eqry[0], _pt)" in impl


def test_enc_flow_beats_name_heuristic():
    # A plainly-named local bound to an encrypted expression must still be
    # classified encrypted (structural let-binding flow), and an
    # encrypted-sounding name bound to plaintext must stay plain.
    files = compile_str("""
    fn f(ct: enc<vec<f64>>, n: u32) -> enc<vec<f64>> {
        let total = slot_sum(ct, n)
        return total * total
    }
    fn g(xs: vec<f64>) -> u32 {
        let result_count = len(xs)
        return result_count - 1
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalMult(total, total)" in impl          # flow: enc by structure
    assert "(result_count - 1)" in impl              # flow: plain by structure
    assert "EvalSub(result_count" not in impl


def test_decrypt_output_is_plain():
    # decrypt() yields a plaintext vector; locals derived from it must not be
    # treated as ciphertexts even with encrypted-sounding names.
    files = compile_str("""
    fn f(expected: f64) -> f64 {
        let sk = load_secret_key(root() / "sk.bin")
        let result = decrypt(sk, ct)
        let score = result[0]
        return score - expected
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "(score - expected)" in impl
    assert "EvalSub(score" not in impl


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
