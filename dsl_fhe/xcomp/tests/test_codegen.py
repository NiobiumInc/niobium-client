"""Tests for OpenFHE C++ code generation."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcomp.lexer import lex
from xcomp.parser import parse
from xcomp.semantic import analyze
from xcomp.codegen import generate


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


def test_client_output_routes_to_upload_dir():
    """A @client stage's wire output is the server's input — it must serialize
    to the upload dir (ctxtupdir), not the download dir.

    Regression for the dir-routing bug where _find_output_dir routed every
    stage output to ctxtdowndir whenever that helper existed. With distinct
    upload/download dirs, the @client encrypt stage wrote the server's input
    into the download dir while the @server stage read it from the upload
    dir, producing an empty input wire and an out-of-bounds crash in the
    consuming stage. The producer dir must match the consumer's read dir:
    @client -> ctxtupdir, @server -> ctxtdowndir.
    """
    files = compile_str("""
    wire Payload { items: vec<enc<f64>> }
    struct Instance { ring_dim: u32 }
    enum InstanceSize { Dev }
    fn instance(size: InstanceSize) -> Instance { match size { Dev => Instance { ring_dim: 2048 } } }
    fn iodir(inst: Instance) -> path { root() / "io" }
    fn ctxtupdir(inst: Instance) -> path { iodir(inst) / "ct_in" }
    fn ctxtdowndir(inst: Instance) -> path { iodir(inst) / "ct_out" }
    @client @stage(name: "upload")
    fn upload(inst: Instance) -> writes(Payload) {
        return Payload { items: [zero()] }
    }
    @server @stage(name: "compute")
    fn compute(inst: Instance) -> reads(Payload), writes(Payload) {
        let p = load(Payload, from: ctxtupdir(inst))
        return Payload { items: [zero()] }
    }
    """)
    # @client upload writes where the @server reads its input from.
    up = files["upload.cpp"]
    assert "ctxtupdir(inst)" in up
    assert "ctxtdowndir(inst)" not in up
    # @server compute writes its result where the @client decrypt reads from.
    comp = files["compute.cpp"]
    assert "ctxtdowndir(inst)" in comp


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
    import xcomp.ast_nodes as ast
    from xcomp.codegen import CodeGenerator
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


def test_no_name_heuristic():
    # Encrypted-ness is fully structural — variable NAMES carry no meaning.
    # Names that the old prefix heuristic classified as encrypted (including
    # its known false positives) are all plaintext when unresolvable, and the
    # ALL_CAPS rule is gone with it.
    import xcomp.ast_nodes as ast
    gen, _ = _make_gen(KEYGEN_SRC.format(ring=""))
    enc = lambda n: gen._is_encrypted_expr(ast.Ident(name=n))
    for name in ("ct", "acc", "result", "eqry", "result_index",
                 "hidden_dim", "recon_loss", "THRESHOLD"):
        assert not enc(name), name
    # Structural flow still classifies, regardless of name.
    gen._enc_vars.add("zzz_totally_plain_sounding")
    assert enc("zzz_totally_plain_sounding")


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


def test_loop_element_enc_flow():
    # Iterating an encrypted collection yields encrypted elements, no matter
    # what the loop variable is called; range loops yield plain indices.
    files = compile_str("""
    fn f(xs: vec<enc<vec<f64>>>) -> enc<vec<f64>> {
        let acc_out: enc<vec<f64>> = zero()
        for item in xs {
            acc_out = acc_out + item * item
        }
        return acc_out
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalMult(item, item)" in impl


def test_wire_field_enc_flow():
    # Field accesses through a loaded wire struct resolve from the wire
    # declaration: enc fields are ciphertexts, plain fields are not.
    files = compile_str("""
    wire Payload { blob: enc<vec<f64>>, count: u32 }
    fn f(p: path, k: f64) -> enc<vec<f64>> {
        let w = load(Payload, from: p)
        let n = w.count
        let scaled = w.blob * (k / n)
        return scaled
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalMult(w.blob" in impl          # enc field -> FHE op
    assert "EvalMult(w.count" not in impl     # plain field stays plain


def test_destructured_tuple_enc_flow():
    # let (a, b) = user_fn(...) with a declared tuple return records each
    # position's encrypted-ness from the signature.
    files = compile_str("""
    fn produce(ct: enc<vec<f64>>) -> (vec<enc<vec<f64>>>, u32) {
        return ([ct], 1)
    }
    fn f(ct: enc<vec<f64>>) -> enc<vec<f64>> {
        let (parts, n) = produce(ct)
        let m = n + 1
        return parts[0] * parts[0]
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalMult(parts[0], parts[0])" in impl
    assert "(n + 1)" in impl                  # plain position stays plain


def test_vec_zeros_type_arg_enc():
    # vec_zeros<enc<...>> classifies from its explicit type argument.
    files = compile_str("""
    fn f(xs: vec<enc<vec<f64>>>, n: u32) -> vec<enc<vec<f64>>> {
        let buckets = vec_zeros<enc<vec<f64>>>(n)
        for i in 0..n {
            buckets[i] = buckets[i] + xs[i]
        }
        return buckets
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "NullSafeEvalAdd(cc, buckets[i], xs[i])" in impl


def test_unresolved_name_is_plain():
    # A name the structural machinery cannot reach generates PLAIN C++ (which
    # fails at C++ compile time if it was actually a ciphertext) — the fix is
    # an annotation, never a naming convention.
    files = compile_str("""
    fn f(xs: vec<f64>) -> f64 {
        let ct_mystery = xs[0]
        return ct_mystery + 1.0
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "(ct_mystery + 1.0)" in impl
    assert "EvalAdd(ct_mystery" not in impl


def test_wire_layout_is_field_type_driven():
    # One predictable serialization layout for every wire, driven by field
    # TYPES (not wire names): enc -> {field}.bin, vec<enc> -> {field}_<i>.bin,
    # vec<vec<enc>> -> batchNNNN/{field}_NNNN.bin — identical on save and load.
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    fn encdir(inst: Instance) -> path { root() / "enc" }
    wire Bundle {
        single: enc<vec<f64>>,
        many: vec<enc<vec<f64>>>,
        grid: vec<vec<enc<vec<f64>>>>,
    }
    @server @stage(name: "produce")
    fn produce(inst: Instance) -> writes(Bundle) {
        let out = load(Bundle, from: encdir(inst))
        return out
    }
    """)
    cpp = files["produce.cpp"]
    # save side
    assert '"single.bin"' in cpp
    assert '"single_" ' not in cpp
    assert '"many_" + std::to_string' in cpp
    assert '"grid_" + _is.str()' in cpp and '"batch" + _bs.str()' in cpp
    # load side mirrors the same names
    assert cpp.count('"single.bin"') >= 2
    assert cpp.count('"many_" + std::to_string') >= 2


def test_crypto_params_wire_by_structure():
    # The cc/pk/mk/rk key layout is selected by the wire CARRYING a
    # CryptoContext field — not by the wire being named "CryptoParams".
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    fn keydir(inst: Instance) -> path { root() / "keys" }
    wire MyKeys {
        context: CryptoContext,
        public_key: PublicKey,
        eval_mult_key: EvalMultKey,
    }
    fn use_keys(inst: Instance) -> u32 {
        let params = load(MyKeys, from: keydir(inst))
        return 0
    }
    """)
    impl = files["nb_shared.cpp"]
    assert '"cc.bin"' in impl and '"pk.bin"' in impl and '"mk.bin"' in impl
    assert "MyKeys p;" in impl


def test_reference_twin_generation():
    # Twinnable stages get a <stage>_ref.cpp cleartext twin: enc -> slot
    # vectors, FHE ops -> nb_plain helpers, chebyshev -> the true function,
    # wire IO -> .ref.bin files, inside namespace nbref with Ref wire aliases.
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    fn encdir(inst: Instance) -> path { root() / "enc" }
    wire Income { ct: enc<vec<f64>> }
    wire Outcome { score: enc<vec<f64>> }
    @server @stage(name: "compute")
    fn compute(inst: Instance) -> reads(Income), writes(Outcome) {
        let w = load(Income, from: encdir(inst))
        let acc = w.ct * w.ct
        let smooth = chebyshev(|x| x, acc, domain: [-1.0, 1.0], degree: 59)
        return Outcome { score: smooth }
    }
    """)
    assert "compute_ref.cpp" in files
    ref = files["compute_ref.cpp"]
    assert "namespace nbref" in ref
    assert "using Outcome = ::OutcomeRef;" in ref
    assert "nb_plain::mul(w.ct, w.ct)" in ref
    assert "nb_plain::apply(acc," in ref          # true function, not Chebyshev
    assert ".ref.bin" in ref
    assert "EvalMult" not in ref and "niobium" not in ref
    # The encrypted stage is unchanged alongside.
    assert "EvalMult" in files["compute.cpp"]


def test_reference_twin_skipped_for_extern():
    # Stages using non-twinnable constructs get no reference twin.
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    @server @stage(name: "compute")
    fn compute(inst: Instance, ct: enc<vec<f64>>) -> enc<vec<f64>> {
        return extern_call("magic", ct)
    }
    """)
    assert "compute_ref.cpp" not in files


def test_chebyshev_max_error_emits_selected_degree():
    # The degree chosen by the semantic analyzer from max_error: lands in
    # the generated EvalChebyshevFunction call.
    files = compile_str("""
    fn f(ct: enc<f64>) -> enc<f64> {
        return chebyshev(|x| 1.0 / (1.0 + exp(0.0 - x)), ct,
                         domain: [-5.0, 5.0], max_error: 0.001)
    }
    """)
    impl = files["nb_shared.cpp"]
    assert "EvalChebyshevFunction" in impl
    assert "5.0, 13)" in impl             # sigmoid @ 1e-3 -> ladder degree 13
    assert "max_error" not in impl        # compile-time only, not C++


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


def test_hardware_save_probe_and_rehydrate():
    """save() inside an @hardware stage must probe each output during the
    record pass and reconstruct it via result() in main()'s replay branch —
    the stage body never runs on a cache-valid run, so without this the
    saved files replay as stale/empty (furever-home bug)."""
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    wire EncOut { ciphertext: enc<f64> }
    fn outdir(inst: Instance) -> path { "io" / "out" }
    @server @stage(name: "compute") @hardware(cache_key: ["wl"])
    fn compute(inst: Instance) {
        let ct: enc<f64> = zero()
        save(EncOut { ciphertext: ct }, to: outdir(inst) / "first_output.bin")
        save(EncOut { ciphertext: ct }, to: outdir(inst) / "second_output.bin")
    }
    """)
    cpp = files["compute.cpp"]
    # Record pass: each save() also probes, making the ct a trace live-out.
    # The probe name is the save() filename's stem — nothing is hardcoded.
    assert 'niobium::compiler().probe("first_output"' in cpp
    assert 'niobium::compiler().probe("second_output"' in cpp
    # Replay branch: each save() site is rehydrated via result() and
    # re-serialized to the same path.
    replay_branch = cpp.split("} else {")[1]
    assert 'result(cc, "first_output"' in replay_branch
    assert 'result(cc, "second_output"' in replay_branch
    assert replay_branch.count("SerializeToFile") >= 2


def test_hardware_save_duplicate_stem_rejected():
    from xcomp.codegen import CodegenError
    try:
        compile_str("""
        struct Instance { ring_dim: u32 }
        wire EncOut { ciphertext: enc<f64> }
        fn encdir(inst: Instance) -> path { "io" / "encrypted" }
        @server @stage(name: "compute") @hardware(cache_key: ["wl"])
        fn compute(inst: Instance) {
            let ct: enc<f64> = zero()
            save(EncOut { ciphertext: ct }, to: encdir(inst) / "out.bin")
            save(EncOut { ciphertext: ct }, to: encdir(inst) / "sub" / "out.bin")
        }
        """)
        assert False, "expected CodegenError for duplicate save() stem"
    except CodegenError as e:
        assert "not unique" in str(e)


def test_save_field_detection_is_structural():
    """A plain field is never treated as a ciphertext by save(), regardless
    of its name — detection is _field_kind()-structural, with no name
    fallback. A plain-named-'result' wire in an @hardware stage must not be
    probed (it can't be a trace live-out)."""
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    wire Meta { result: f64 }
    fn outdir(inst: Instance) -> path { "io" / "out" }
    @server @stage(name: "compute") @hardware(cache_key: ["wl"])
    fn compute(inst: Instance) {
        save(Meta { result: 1.0 }, to: outdir(inst) / "meta.bin")
    }
    """)
    cpp = files["compute.cpp"]
    assert "probe(" not in cpp
    # Falls through to whole-struct serialization, not a .result field access.
    assert ".result, SerType::BINARY" not in cpp


def test_hardware_save_requires_server_stage():
    from xcomp.codegen import CodegenError
    try:
        compile_str("""
        struct Instance { ring_dim: u32 }
        wire EncOut { ciphertext: enc<f64> }
        fn outdir(inst: Instance) -> path { "io" / "out" }
        @client @stage(name: "cl") @hardware(cache_key: ["wl"])
        fn cl(inst: Instance) {
            let ct: enc<f64> = zero()
            save(EncOut { ciphertext: ct }, to: outdir(inst) / "out.bin")
        }
        """)
        assert False, "expected CodegenError for @client @hardware save()"
    except CodegenError as e:
        assert "@server" in str(e)


def test_client_save_has_no_probe():
    """Only @hardware stages probe their save()s — client stages and
    non-hardware servers keep the plain serialization."""
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    wire EncOut { ciphertext: enc<f64> }
    fn encdir(inst: Instance) -> path { "io" / "encrypted" }
    @client @stage(name: "enc")
    fn enc_stage(inst: Instance) {
        let ct: enc<f64> = zero()
        save(EncOut { ciphertext: ct }, to: encdir(inst) / "input.bin")
    }
    """)
    assert "probe(" not in files["enc.cpp"]


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
