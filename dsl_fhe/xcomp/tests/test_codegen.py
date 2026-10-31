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
    # ... and encoded at the ciphertext's level, with the ciphertext bound once.
    assert ("auto _ct = eqry[0]; auto _pt = cc->MakeCKKSPackedPlaintext(column, 1, "
            "_ct->GetLevel(), nullptr, _ct->GetSlots()); return cc->EvalSub(_ct, _pt);") in impl


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




# ── bootstrap / rotate(<indices>) / codegen fixes ────────────────────────────

BOOTSTRAP_PRELUDE = """
enum InstanceSize { Toy, Small }
struct Instance { size: InstanceSize, ring_dim: u32, tokens: u32 }
fn instance(size: InstanceSize) -> Instance {
    match size {
        Toy   => Instance { size, ring_dim: 8192, tokens: 100 },
        Small => Instance { size, ring_dim: 65536, tokens: 800 },
    }
}
fn keydir(inst: Instance) -> path { root() / "keys" }
fn encdir(inst: Instance) -> path { root() / "enc" }
fn rotation_indices(inst: Instance) -> vec<i32> {
    let idx: vec<i32> = []
    idx.push(1)
    idx.push(-3)
    return idx
}
wire CryptoParams {
    context: CryptoContext,
    public_key: PublicKey,
    eval_mult_key: EvalMultKey,
    eval_rot_keys: EvalAutomorphismKeys,
}
wire EncryptedInputs { pages: vec<enc<vec<f64>>> }
wire EncryptedResult { ciphertext: enc<vec<f64>> }
"""


def test_bootstrap_keygen_setup_and_call():
    """`requires { bootstrap }` drives key generation (FHE feature, setup,
    bootstrap keys, depth on top of the refresh budget), the server-side
    setup after the context is loaded, and `bootstrap(ct)` itself."""
    files = compile_str(BOOTSTRAP_PRELUDE + """
    scheme CKKS {
        security: not_set
        depth: 14
        bootstrap_budget: 4 levels
    }
    requires { add, mul, rotate(rotation_indices(inst)), bootstrap }

    @client @stage("key_generation")
    fn generate_keys(inst: Instance) -> writes(CryptoParams) {
        let keys = keygen()
        return CryptoParams {
            context: keys.context, public_key: keys.public,
            eval_mult_key: keys.eval_mult, eval_rot_keys: keys.eval_rot,
        }
    }

    @server @stage("compute")
    fn compute(inst: Instance) -> reads(CryptoParams, EncryptedInputs), writes(EncryptedResult) {
        let params = load(CryptoParams, from: keydir(inst))
        let inputs = load(EncryptedInputs, from: encdir(inst))
        let ct = inputs.pages[0] * inputs.pages[0]
        ct = bootstrap(ct)
        return EncryptedResult { ciphertext: ct }
    }
    """)
    kg = files["key_generation.cpp"]
    assert "std::vector<uint32_t> _level_budget = {4, 4};" in kg
    assert ("parameters.SetMultiplicativeDepth(14 + "
            "FHECKKSRNS::GetBootstrapDepth(_level_budget, UNIFORM_TERNARY));") in kg
    assert "cc->Enable(FHE);" in kg
    assert "cc->EvalBootstrapSetup(_level_budget);" in kg
    assert "cc->EvalBootstrapKeyGen(kp.secretKey, cc->GetRingDimension() / 2);" in kg
    # rotate(<expr>): keys for exactly those indices, not the 1..N/2-1 range
    assert "auto _rot_src = rotation_indices(inst);" in kg
    assert "std::vector<int> _rot_indices(_rot_src.begin(), _rot_src.end());" in kg
    assert "cc->EvalRotateKeyGen(kp.secretKey, _rot_indices);" in kg
    assert "_rot_indices.push_back(_i)" not in kg

    srv = files["compute.cpp"]
    assert "cc->EvalBootstrapSetup(std::vector<uint32_t>{4, 4});" in srv
    assert srv.index('"cc.bin"') < srv.index("EvalBootstrapSetup")
    assert "ct = cc->EvalBootstrap(ct);" in srv
    # the cleartext twin treats a refresh as the identity
    ref = files["compute_ref.cpp"]
    assert "ct = (ct);" in ref
    assert "EvalBootstrap" not in ref


def test_bootstrap_budget_defaults_to_three():
    files = compile_str(BOOTSTRAP_PRELUDE + """
    scheme CKKS { security: not_set depth: 5 }
    requires { add, bootstrap }
    @client @stage("key_generation")
    fn generate_keys(inst: Instance) -> writes(CryptoParams) {
        let keys = keygen()
        return CryptoParams {
            context: keys.context, public_key: keys.public,
            eval_mult_key: keys.eval_mult, eval_rot_keys: keys.eval_rot,
        }
    }
    """)
    assert "std::vector<uint32_t> _level_budget = {3, 3};" in files["key_generation.cpp"]


def test_bootstrap_call_requires_capability():
    from xcomp.codegen import CodegenError
    try:
        compile_str(BOOTSTRAP_PRELUDE + """
        scheme CKKS { security: not_set depth: 5 }
        requires { add, mul }
        @server @stage("compute")
        fn compute(inst: Instance) -> reads(EncryptedInputs), writes(EncryptedResult) {
            let inputs = load(EncryptedInputs, from: encdir(inst))
            return EncryptedResult { ciphertext: bootstrap(inputs.pages[0]) }
        }
        """)
        assert False, "expected CodegenError"
    except CodegenError as e:
        assert "requires" in str(e)


ITER_STAGES = """
    @client @stage("key_generation")
    fn generate_keys(inst: Instance) -> writes(CryptoParams) {
        let keys = keygen()
        return CryptoParams {
            context: keys.context, public_key: keys.public,
            eval_mult_key: keys.eval_mult, eval_rot_keys: keys.eval_rot,
        }
    }
    @server @stage("compute")
    fn compute(inst: Instance) -> reads(CryptoParams, EncryptedInputs), writes(EncryptedResult) {
        let params = load(CryptoParams, from: keydir(inst))
        let inputs = load(EncryptedInputs, from: encdir(inst))
        let ct = bootstrap(inputs.pages[0] * inputs.pages[0])
        let cheap = bootstrap(ct, iterations: 1)
        let fine = bootstrap(cheap, precision: 12)
        return EncryptedResult { ciphertext: fine }
    }
"""


def test_bootstrap_iterations_scheme_and_call():
    """`bootstrap_iterations: 2` emits OpenFHE's iterative bootstrap with the
    scheme's precision and reserves one more level in keygen; a call may
    lower the iterations or name its own precision; the twin stays the
    identity."""
    files = compile_str(BOOTSTRAP_PRELUDE + """
    scheme CKKS {
        security: not_set
        depth: 14
        bootstrap_budget: 3 levels
        bootstrap_iterations: 2
        bootstrap_precision: 17 bits
    }
    requires { add, mul, bootstrap }
    """ + ITER_STAGES)
    kg = files["key_generation.cpp"]
    assert ("parameters.SetMultiplicativeDepth(14 + "
            "FHECKKSRNS::GetBootstrapDepth(_level_budget, UNIFORM_TERNARY) + 1);") in kg
    srv = files["compute.cpp"]
    assert "cc->EvalBootstrap(ct, 2, 17)" not in srv  # the first call refreshes the product
    assert ", 2, 17)" in srv
    assert "cheap = cc->EvalBootstrap(ct);" in srv
    assert "fine = cc->EvalBootstrap(cheap, 2, 12);" in srv
    ref = files["compute_ref.cpp"]
    assert "EvalBootstrap" not in ref
    # without the field: the plain call, the plain depth
    files = compile_str(BOOTSTRAP_PRELUDE + """
    scheme CKKS { security: not_set depth: 14 }
    requires { add, mul, bootstrap }
    """ + ITER_STAGES.replace("bootstrap(cheap, precision: 12)", "bootstrap(cheap)"))
    assert "GetBootstrapDepth(_level_budget, UNIFORM_TERNARY));" in files["key_generation.cpp"]
    assert "cc->EvalBootstrap(ct, 2" not in files["compute.cpp"]


def test_bootstrap_iterations_rejected():
    from xcomp.codegen import CodegenError
    base = BOOTSTRAP_PRELUDE + """
    scheme CKKS { security: not_set depth: 14 %s }
    requires { add, mul, bootstrap }
    """

    def rejected(source: str, needle: str):
        try:
            compile_str(source)
            assert False, f"expected CodegenError mentioning {needle!r}"
        except CodegenError as e:
            assert needle in str(e), str(e)

    rejected(base % "bootstrap_iterations: 3" + ITER_STAGES, "1 or 2")
    # 2 iterations without a precision anywhere
    rejected(base % "bootstrap_iterations: 2" + ITER_STAGES, "precision")
    # a call asking for more iterations than keygen reserved
    rejected(base % "" + ITER_STAGES.replace("iterations: 1", "iterations: 2, precision: 10"),
             "bootstrap_iterations: 2")
    rejected(base % "" + ITER_STAGES.replace("iterations: 1", "iters: 1"), "unknown argument")


def test_enumerate_index_advances_inside_loop():
    files = compile_str(BOOTSTRAP_PRELUDE + """
    fn f(pages: vec<enc<vec<f64>>>) -> enc<vec<f64>> {
        let acc: enc<vec<f64>> = zero()
        for (p, ct_page) in enumerate(pages) {
            acc = acc + ct_page * (p |> as f64)
        }
        return acc
    }
    """)
    body = files["nb_shared.cpp"]
    loop = body[body.index("for (auto& ct_page : pages) {"):]
    close = loop.index("\n  }\n")          # end of the for body
    assert "p++;" in loop[:close], "index must advance inside the loop body"


def test_let_in_sibling_blocks_redeclares():
    files = compile_str(BOOTSTRAP_PRELUDE + """
    fn f(n: u32) -> u32 {
        let total = 0
        for i in 0..n {
            let d = i * 2
            total = total + d
        }
        for i in 0..n {
            let d = i * 3
            total = total + d
        }
        return total
    }
    """)
    body = files["nb_shared.cpp"]
    assert body.count("auto d = ") == 2, body


def test_encrypt_evaluates_source_once():
    files = compile_str(BOOTSTRAP_PRELUDE + """
    fn make_row(inst: Instance, i: u32) -> vec<f64> {
        let v = vec_zeros<f64>(4)
        return v
    }
    @client @stage("encrypt_inputs")
    fn encrypt_inputs(inst: Instance) -> reads(CryptoParams), writes(EncryptedInputs) {
        let params = load(CryptoParams, from: keydir(inst))
        let pages = for i in 0..2 { encrypt(params.public_key, make_row(inst, i)) }
        return EncryptedInputs { pages: pages }
    }
    """)
    cpp = files["encrypt_inputs.cpp"]
    assert cpp.count("make_row(inst, i)") == 1, cpp
    assert "const auto& _src = make_row(inst, i);" in cpp
    assert "std::vector<double>(_src.begin(), _src.end())" in cpp
    ref = files["encrypt_inputs_ref.cpp"]
    assert ref.count("make_row(inst, i)") == 1, ref


def test_ct_plus_user_vector_materializes_plaintext():
    """EvalAdd(ct, Plaintext&) needs an lvalue plaintext: `f(ct) + mask_fn()`
    with a user `-> vec<f64>` function wraps the mask and binds it first."""
    files = compile_str(BOOTSTRAP_PRELUDE + """
    fn mask(inst: Instance) -> vec<f64> { return vec_zeros<f64>(4) }
    fn g(ct: enc<vec<f64>>) -> enc<vec<f64>> { return ct }
    fn f(inst: Instance, ct: enc<vec<f64>>) -> enc<vec<f64>> {
        let a = g(ct) + mask(inst)
        let b = mask(inst) + g(ct)
        let m = mask(inst)
        return a + b * m
    }
    """)
    body = files["nb_shared.cpp"]
    pt = "cc->MakeCKKSPackedPlaintext(mask(inst), 1, _ct->GetLevel(), nullptr, _ct->GetSlots())"
    assert (f"auto a = [&]() {{ auto _ct = g(cc, ct); auto _pt = {pt}; "
            "return cc->EvalAdd(_ct, _pt); }();") in body
    assert (f"auto b = [&]() {{ auto _ct = g(cc, ct); auto _pt = {pt}; "
            "return cc->EvalAdd(_ct, _pt); }();") in body
    assert ("[&]() { auto _ct = b; auto _pt = cc->MakeCKKSPackedPlaintext(m, 1, _ct->GetLevel(), "
            "nullptr, _ct->GetSlots()); return cc->EvalMult(_ct, _pt); }()") in body



def test_requires_arg_on_other_capability_rejected():
    """Only rotate(<expr>) consumes an argument; any other capability with one
    is an error instead of a silently ignored argument."""
    from xcomp.codegen import CodegenError
    try:
        compile_str(BOOTSTRAP_PRELUDE + """
        scheme CKKS { security: not_set depth: 5 }
        requires { add, mul(rotation_indices(inst)) }
        @client @stage("key_generation")
        fn generate_keys(inst: Instance) -> writes(CryptoParams) {
            let keys = keygen()
            return CryptoParams { context: keys.context, public_key: keys.public,
                                  eval_mult_key: keys.eval_mult, eval_rot_keys: keys.eval_rot }
        }
        """)
        assert False, "expected CodegenError"
    except CodegenError as e:
        assert "'mul'" in str(e) and "rotate" in str(e)


def test_bootstrap_budget_override_rejected():
    """keygen and the server-side EvalBootstrapSetup must use one budget: a
    per-stage scheme.override(bootstrap_budget: ...) is refused."""
    from xcomp.codegen import CodegenError
    try:
        compile_str(BOOTSTRAP_PRELUDE + """
        scheme CKKS { security: not_set depth: 5 }
        requires { add, mul, bootstrap }
        @server @stage("compute")
        fn compute(inst: Instance) -> reads(EncryptedInputs), writes(EncryptedResult) {
            scheme.override(bootstrap_budget: 2)
            let inputs = load(EncryptedInputs, from: encdir(inst))
            return EncryptedResult { ciphertext: bootstrap(inputs.pages[0]) }
        }
        """)
        assert False, "expected CodegenError"
    except CodegenError as e:
        assert "bootstrap_budget" in str(e)

def test_cmake_dependency_overrides():
    """The generated CMake takes OPENFHE_INSTALL_DIR and
    NIOBIUM_CLIENT_FHETCH_DIR, defaulting both under NIOBIUM_CLIENT_ROOT, so a
    parent build (niobium-compiler) can supply its own trees. Every OpenFHE
    and FHETCH path must derive from those two, never from the root directly."""
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
    cmake = files["CMakeLists.txt"]
    assert 'if(NOT OPENFHE_INSTALL_DIR)' in cmake
    assert 'set(OPENFHE_INSTALL_DIR "${NIOBIUM_CLIENT_ROOT}/vendor/lib/openfhe")' in cmake
    assert 'if(NOT NIOBIUM_CLIENT_FHETCH_DIR)' in cmake
    assert 'set(NIOBIUM_CLIENT_FHETCH_DIR "${NIOBIUM_CLIENT_ROOT}/vendor/niobium-fhetch")' in cmake
    assert 'set(OPENFHE_LIBRARY_PATH "${OPENFHE_INSTALL_DIR}/lib")' in cmake
    assert 'set(OPENFHE_INCLUDE_PATH "${OPENFHE_INSTALL_DIR}/include/openfhe")' in cmake
    assert 'set(FHETCH_INCLUDE_PATH "${NIOBIUM_CLIENT_FHETCH_DIR}/include")' in cmake
    assert '"${NIOBIUM_CLIENT_FHETCH_DIR}/build")' in cmake
    assert "${NIOBIUM_CLIENT_ROOT}/vendor/lib/openfhe/" not in cmake
    assert "${NIOBIUM_CLIENT_ROOT}/vendor/niobium-fhetch/" not in cmake


FIDESLIB_SRC = """
struct Instance { size: u32 }
fn instance(size: u32) -> Instance { Instance { size: size } }
fn keydir(inst: Instance) -> path { root() / "keys" }
fn encdir(inst: Instance) -> path { root() / "enc" }
wire CryptoParams { context: CryptoContext, public_key: PublicKey }
wire Input { x: enc<f64> }
wire Output { y: enc<f64> }
scheme CKKS {
    security: not_set
    depth: 8
    precision: 50 bits
    first_mod: 60 bits
    bootstrap_budget: 3 levels
}
requires { add, mul, rotate([1, 2]), bootstrap }

@client @stage("key_generation")
fn generate_keys(inst: Instance) -> writes(CryptoParams) {
    let keys = keygen()
    return CryptoParams { context: keys.context, public_key: keys.public }
}

@client @stage("encrypt")
fn encrypt_input(inst: Instance, v: f64) -> writes(Input) {
    let p = load(CryptoParams, from: keydir(inst))
    return Input { x: encrypt(p.public_key, [v]) }
}

@server @stage("compute")
@hardware(cache_key: ["size"])
fn compute(inst: Instance) -> reads(Input) writes(Output) {
    let i = load(Input, from: encdir(inst))
    let a = rotate(i.x, 1) + i.x * i.x
    let b = chebyshev(|t| t * t, a, domain: [-1.0, 1.0], degree: 13)
    return Output { y: bootstrap(b) }
}
"""


def compile_fideslib(source: str) -> dict[str, str]:
    tokens = lex(source)
    program = parse(tokens)
    sa = analyze(program)
    return generate(program, sa, backend="fideslib")


def test_fideslib_backend_header_and_glue():
    files = compile_fideslib(FIDESLIB_SRC)
    header = files["nb_shared.h"]
    assert "#include <fideslib.hpp>" in header
    assert "#include <Interop.hpp>" in header
    assert "using namespace fideslib;" in header
    assert "using namespace lbcrypto;" not in header
    assert '#include "nb_fideslib.h"' in header
    glue = files["nb_fideslib.h"]
    assert "fideslib::ImportCryptoContext(" in glue
    assert "fideslib::ExportCiphertext(" in glue
    assert "MarkOutput(" in glue and "RecoverHostCiphertext(" in glue
    assert "cc.reset();" in glue  # the engine dies inside main(), not at exit


def test_fideslib_backend_keygen_on_the_host():
    files = compile_fideslib(FIDESLIB_SRC)
    keygen = files["key_generation.cpp"]
    assert "parameters.SetBackend(Backend::CPU);" in keygen
    assert "parameters.SetReducedNoise(LinkedOpenFheReducedNoise());" in keygen
    assert "lbcrypto::FHECKKSRNS::GetBootstrapDepth(_level_budget, lbcrypto::UNIFORM_TERNARY)" in keygen
    assert "nb_fideslib::set_context(cc);" in keygen
    assert "cc->slots_bootstrap.push_back(cc->GetRingDimension() / 2);" in keygen
    assert "EvalSumKeyGen" not in keygen
    assert "cc->EvalRotateKeyGen(kp.secretKey," in keygen
    # The context field of the key pair comes from the glue, not the key.
    assert "GetCryptoContext()" not in keygen


def test_fideslib_backend_server_imports_the_context():
    files = compile_fideslib(FIDESLIB_SRC)
    server = files["compute.cpp"]
    assert "auto cc = nb_fideslib::import_context(_stored_cc);" in server
    assert "cc->slots_bootstrap = {cc->GetRingDimension() / 2};" in server
    assert "cc->EvalBootstrapSetup(std::vector<uint32_t>{3, 3}, std::vector<uint32_t>{0, 0}, cc->GetRingDimension() / 2);" in server
    assert "cc->LoadContext(pk);" in server
    assert "nb_fideslib::flush_outputs();" in server
    # No Niobium instrumentation: the haze engine records by itself.
    assert "niobium::" not in server
    assert "_nb_hollow_record" not in server
    assert "is_cache_valid" not in server


def test_fideslib_backend_chebyshev_and_encrypt():
    files = compile_fideslib(FIDESLIB_SRC)
    server = files["compute.cpp"]
    assert "CryptoContextImpl<DCRTPoly>::GetChebyshevCoefficients(_f, (-1.0), 1.0, 13)" in server
    assert "cc->EvalChebyshevSeries(" in server
    assert "EvalChebyshevFunction" not in server
    assert "cc->EvalBootstrap(" in server
    enc = files["encrypt.cpp"]
    assert "auto _cc = nb_fideslib::context();" in enc
    assert "return _cc->Encrypt(p.public_key, _pt);" in enc
    assert "nb_fideslib::set_context(p.context);" in enc


def test_fideslib_backend_cmake():
    files = compile_fideslib(FIDESLIB_SRC)
    cmake = files["CMakeLists.txt"]
    assert "find_package(fideslib CONFIG REQUIRED" in cmake
    assert "link_libraries(fideslib::fideslib ${OPENFHE_LIBRARIES})" in cmake
    assert "FIDESLIB_INSTALL_DIR" in cmake and "if(OPENFHE_INSTALL_DIR)" in cmake
    assert "nbfhetch" not in cmake
    assert "OPENFHE_CPROBES" not in cmake
    assert "autofacade" not in cmake


def test_fideslib_backend_rejects_what_the_facade_lacks():
    import xcomp.codegen as cg
    src = FIDESLIB_SRC.replace("return Output { y: bootstrap(b) }",
                               "return Output { y: slot_sum(b, 4) }")
    try:
        compile_fideslib(src)
        assert False, "slot_sum must be rejected under the fideslib backend"
    except cg.CodegenError as e:
        assert "slot_sum" in str(e)
    # The default backend still accepts it.
    files = compile_str(src)
    assert "cc->EvalSum(" in files["compute.cpp"]


def test_fideslib_backend_openfhe_default_unchanged():
    files = compile_str(FIDESLIB_SRC)
    assert "nb_fideslib.h" not in files
    assert "using namespace lbcrypto;" in files["nb_shared.h"]
    assert "niobium::compiler().init(argc, argv);" in files["compute.cpp"]
    assert "cc->EvalChebyshevFunction(" in files["compute.cpp"]


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


# ── linear_transform: the baby-step giant-step diagonal product ──────────────

LT_SRC = FIDESLIB_SRC.replace(
    "    let a = rotate(i.x, 1) + i.x * i.x\n",
    "    let d = vec_zeros<vec<f64>>(4)\n"
    "    for k in 0..4 {\n"
    "        d[k] = vec_zeros<f64>(8)\n"
    "        d[k][k] = 1.0\n"
    "    }\n"
    "    let one = linear_transform(i.x, d, 2)\n"
    "    let many = linear_transforms(i.x, [d, d], 2)\n"
    "    let a = one + many[0] + many[1]\n")


def test_linear_transform_openfhe_runs_the_loop():
    files = compile_str(LT_SRC)
    server = files["compute.cpp"]
    assert "nb_linear_transform(cc, i.x, d, static_cast<uint32_t>(2))" in server
    assert "nb_linear_transforms(cc, i.x," in server
    header = files["nb_shared.h"]
    assert "CT nb_linear_transform_from(" in header
    assert "return nb_linear_transform_loop(cc, ct, d, bstep);" in header
    # The baby rotations are computed once and shared by every set.
    assert "auto baby = nb_baby_rotations(cc, ct, n);" in header
    assert "LinearTransformInPlace" not in header


def test_linear_transform_fideslib_uses_the_fused_transform():
    files = compile_fideslib(LT_SRC)
    header = files["nb_shared.h"]
    assert "cc->LinearTransformInPlace(r, static_cast<int>(part.size()), static_cast<int>(bstep), pts, 1, static_cast<int>(k0));" in header
    assert "cc->LinearTransformMany(ct, static_cast<int>(len), static_cast<int>(bstep), pts, 1, static_cast<int>(k0));" in header
    # bounded device memory: chunks of whole giant steps under the scheme's
    # linear_transform_memory (6 GB by default)
    assert "std::atof(e) * 1e9 : 6e+09; }();" in header
    assert "size_t chunk = std::max<size_t>(1, giants) * bstep;" in header
    # haze has no fused transform: the loop, the same result
    assert "fideslib::BackendFromEnv() == fideslib::Backend::HAZE" in header
    # the only environment read: NB_LT_MEMORY_GB, a run-time override of the
    # chunk budget (the mode itself is static: its rotation keys are keygen's)
    import re
    assert re.findall(r'getenv\("([A-Z_]+)"\)', header) == ["NB_LT_MEMORY_GB"]
    # diagonals at the level the products happen at
    assert "ct->GetLevel() + (ct->GetNoiseScaleDeg() == 2 ? 1 : 0)" in header
    assert "cc->MakeCKKSPackedPlaintextDeviceMany(d, 1, level, ct->GetSlots())" in header
    assert "nb_linear_transform(cc, i.x, d, static_cast<uint32_t>(2))" in files["compute.cpp"]


def test_linear_transform_twin():
    files = compile_str(LT_SRC)
    header = files["nb_shared.h"]
    assert "inline Slots linear_transform(const Slots& x, const std::vector<Slots>& d, uint32_t bstep)" in header
    ref = files.get("compute_ref.cpp", "")
    if ref:
        assert "nb_plain::linear_transform(" in ref


def test_linear_transform_rejects_a_plaintext_input():
    import xcomp.codegen as cg
    src = LT_SRC.replace("linear_transform(i.x, d, 2)", "linear_transform(d[0], d, 2)")
    try:
        compile_str(src)
        assert False, "a plaintext first argument must be rejected"
    except cg.CodegenError as e:
        assert "must be a ciphertext" in str(e)


def test_key_switch_digits():
    src = FIDESLIB_SRC.replace("    bootstrap_budget: 3 levels\n", "    bootstrap_budget: 3 levels\n    key_switch_digits: 6\n")
    assert "key_switch_digits: 6" in src
    for files in (compile_str(src), compile_fideslib(src)):
        assert "parameters.SetNumLargeDigits(6);" in files["key_generation.cpp"]
    assert "SetNumLargeDigits" not in compile_str(FIDESLIB_SRC)["key_generation.cpp"]


def test_bootstrap_depth():
    src = FIDESLIB_SRC.replace("    bootstrap_budget: 3 levels\n", "    bootstrap_budget: 3 levels\n    bootstrap_depth: 15 levels\n")
    for files in (compile_str(src), compile_fideslib(src)):
        kg = files["key_generation.cpp"]
        assert "parameters.SetMultiplicativeDepth(8 + 15);" in kg, kg[kg.find("SetMultiplicativeDepth"):][:80]
        assert "GetBootstrapDepth" not in kg.split("SetMultiplicativeDepth")[1].split(";")[0]


def _lt_scheme(src, fields):
    return src.replace("    bootstrap_budget: 3 levels\n", "    bootstrap_budget: 3 levels\n" + fields)


def test_linear_transform_scheme_fields():
    import xcomp.codegen as cg
    loop = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: loop\n"))["nb_shared.h"]
    assert "LinearTransformInPlace" not in loop and "LinearTransformMany" not in loop
    assert "return nb_linear_transform_loop(cc, ct, d, bstep);" in loop
    mem = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform_memory: 1500 MB\n"))["nb_shared.h"]
    assert "std::atof(e) * 1e9 : 1.5e+09; }();" in mem
    for bad, msg in (("    linear_transform: maybe\n", "fused, loop or chained"),
                     ("    linear_transform_memory: 3 bytes\n", "N GB or N MB")):
        try:
            compile_fideslib(_lt_scheme(LT_SRC, bad))
            assert False, bad
        except cg.CodegenError as e:
            assert msg in str(e)


def test_linear_transform_chained():
    import xcomp.codegen as cg
    for files in (compile_str(_lt_scheme(LT_SRC, "    linear_transform: chained\n")),
                  compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: chained\n"))):
        h = files["nb_shared.h"]
        assert "LinearTransformInPlace" not in h
        # baby steps chained by 1, giant steps by Horner with bstep
        assert "baby[b] = b == 0 ? ct : cc->EvalRotate(baby[b - 1], 1);" in h
        assert "cc->EvalRotate(y, static_cast<int32_t>(bstep))" in h
        assert "static_cast<int32_t>(g * bstep)" not in h
    loop = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: loop\n"))["nb_shared.h"]
    assert "cc->EvalRotate(ct, static_cast<int32_t>(b))" in loop


def test_linear_transform_chained_device_encodes_a_giant_step():
    # chained on fideslib: each giant step's diagonals encoded as one device
    # batch at the baby steps' level; loop and OpenFHE keep one host-encoded
    # diagonal at a time
    chained = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: chained\n"))["nb_shared.h"]
    assert "cc->MakeCKKSPackedPlaintextDeviceMany(batch, 1, baby[0]->GetLevel(), baby[0]->GetSlots())" in chained
    assert "cc->EvalMult(baby[b0 + b], pts[b])" in chained
    assert "MakeCKKSPackedPlaintext(d[g * bstep + b]" not in chained
    for files in (compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: loop\n")),
                  compile_str(_lt_scheme(LT_SRC, "    linear_transform: chained\n"))):
        h = files["nb_shared.h"]
        assert "MakeCKKSPackedPlaintext(d[g * bstep + b], 1, baby[b]->GetLevel()" in h
        assert "MakeCKKSPackedPlaintextDeviceMany(batch" not in h


# A shared helper taking a plaintext vector parameter, and a scalar on the
# left of a ciphertext: the two plaintext-operand forms of `affine`.
PLAIN_OPERAND_SRC = FIDESLIB_SRC.replace(
    '@client @stage("key_generation")',
    "fn affine(x: enc<vec<f64>>, m: vec<f64>) -> enc<vec<f64>> {\n"
    '    return 2.0 * (x * m) - 1.0\n'
    '}\n\n@client @stage("key_generation")').replace(
    "    return Output { y: bootstrap(b) }",
    "    return Output { y: bootstrap(affine(b, [0.5, 0.25])) }")


def test_vec_parameter_operand_is_encoded():
    for files in (compile_str(PLAIN_OPERAND_SRC), compile_fideslib(PLAIN_OPERAND_SRC)):
        shared = files["nb_shared.cpp"]
        body = shared[shared.find("affine("):]
        # the vec<f64> parameter becomes a packed plaintext at the ciphertext's level
        assert "MakeCKKSPackedPlaintext(m, 1, _ct->GetLevel()" in body, body[:400]
        assert "EvalMult(x, m)" not in body


def test_twin_takes_a_scalar_on_the_left():
    files = compile_str(PLAIN_OPERAND_SRC)
    header = "\n".join(v for k, v in files.items() if k.endswith(".h"))
    assert "inline Slots mul(double s, Slots a)" in header
    assert "inline Slots sub(double s, Slots a)" in header
    assert "inline Slots add(double s, Slots a)" in header


def _decl_line(header: str, name: str) -> str:
    return next(l for l in header.splitlines() if f" {name}(" in l)


def test_cc_for_vec_of_ciphertexts_param():
    """A function whose only ciphertexts come in a vec<enc<...>> still
    takes the crypto context: its body's FHE calls pass cc."""
    files = compile_str("""
    fn twice(x: enc<f64>) -> enc<f64> { return x + x }
    fn first_twice(xs: vec<enc<f64>>) -> f64 {
        let y = twice(xs[0])
        return 0.0
    }
    """)
    header = files["nb_shared.h"]
    assert "CryptoContext<DCRTPoly> cc" in _decl_line(header, "first_twice")
    assert "twice(cc, " in files["nb_shared.cpp"]


def test_cc_for_tuple_and_wire_returns():
    """A ciphertext inside a tuple or a wire return type needs cc too."""
    files = compile_str("""
    wire Pair { a: enc<f64>, b: enc<f64> }
    fn split(xs: vec<enc<f64>>) -> (vec<enc<f64>>, vec<enc<f64>>) { return (xs, xs) }
    fn pack(n: u32) -> Pair { let z: enc<f64> = zero()
        return Pair { a: z, b: z } }
    """)
    header = files["nb_shared.h"]
    assert "CryptoContext<DCRTPoly> cc" in _decl_line(header, "split")
    assert "CryptoContext<DCRTPoly> cc" in _decl_line(header, "pack")


def test_cc_through_the_call_graph():
    """No ciphertext in the signature, but a call (direct or not) to a
    function that takes cc: the caller needs cc in scope."""
    files = compile_str("""
    fn leaf(x: enc<f64>) -> enc<f64> { return x }
    fn mid(n: u32) -> u32 { let z: enc<f64> = zero()
        let y = leaf(z)
        return n }
    fn top(n: u32) -> u32 { return mid(n) }
    fn plain(n: u32) -> u32 { return n + 1 }
    """)
    header = files["nb_shared.h"]
    assert "CryptoContext<DCRTPoly> cc" in _decl_line(header, "mid")
    assert "CryptoContext<DCRTPoly> cc" in _decl_line(header, "top")
    assert "cc" not in _decl_line(header, "plain")
    assert "mid(cc, " in files["nb_shared.cpp"]


def test_load_from_computed_path_pairs_with_save():
    """save(W, to: f(c)) writes a file at f(c); load(W, from: f(c)) must read
    that file, and still read a stage result's directory layout when the
    path is a directory."""
    files = compile_str("""
    struct Instance { ring_dim: u32 }
    wire EncOut { ciphertext: enc<f64> }
    fn part(inst: Instance, c: u32) -> path { "io" / ("part_" + str(c) + ".bin") }
    @client @stage(name: "put")
    fn put(inst: Instance) {
        let ct: enc<f64> = zero()
        save(EncOut { ciphertext: ct }, to: part(inst, 1))
    }
    @server @stage(name: "get")
    fn get(inst: Instance) -> writes(EncOut) {
        let w = load(EncOut, from: part(inst, 1))
        return w
    }
    """)
    cpp = files["get.cpp"]
    assert 'fs::is_regular_file(_p) ? _p : _p / "ciphertext.bin"' in cpp
    ref = files.get("get_ref.cpp", "")
    if ref:
        assert '_p / "ciphertext.ref.bin"' in ref and '.string() + ".ref"' in ref


def _switches(cpp: str) -> list[str]:
    """The bodies of the generated switch statements, outermost braces only."""
    out = []
    i = cpp.find("switch (")
    while i >= 0:
        j = cpp.index("{", i)
        depth, k = 0, j
        while True:
            depth += {"{": 1, "}": -1}.get(cpp[k], 0)
            if depth == 0:
                break
            k += 1
        out.append(cpp[j:k + 1])
        i = cpp.find("switch (", k)
    return out


def test_match_wildcard_is_the_only_default():
    # `_ =>` parses as a WildcardPattern: it is the switch's default arm, and
    # no unreachable default may be added next to it (two default labels do
    # not compile), in a match expression and in a match statement
    files = compile_str("""
    enum Size { Small, Medium, Large }
    fn is_small(s: Size) -> bool {
        match s {
            Small => true,
            _ => false,
        }
    }
    fn width(s: Size) -> u32 {
        let w = 1
        match s {
            Large => { w = 4 }
            _ => { w = 2 }
        }
        return w
    }
    """)
    cpp = files["nb_shared.cpp"]
    switches = _switches(cpp)
    assert len(switches) == 2, cpp
    for body in switches:
        assert body.count("default:") == 1, body
        assert "__builtin_unreachable" not in body, body
        assert "unhandled pattern" not in body, body
    # a match with no wildcard still gets the unreachable default
    full = compile_str("""
    enum Size { Small, Large }
    fn is_small(s: Size) -> bool {
        match s {
            Small => true,
            Large => false,
        }
    }
    """)["nb_shared.cpp"]
    (body,) = _switches(full)
    assert body.count("default:") == 1 and "__builtin_unreachable" in body, body


def test_linear_transform_batch():
    import xcomp.codegen as cg
    # chained on fideslib: a giant step encoded in batches of nb_lt_batch,
    # NB_LT_BATCH at run time over the scheme's linear_transform_batch
    # (default 0, a whole giant step)
    dflt = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: chained\n"))["nb_shared.h"]
    assert "inline size_t nb_lt_batch(size_t bstep)" in dflt
    assert 'std::getenv("NB_LT_BATCH")' in dflt and ": 0L;" in dflt
    assert "for (size_t b0 = 0; b0 < n; b0 += nb)" in dflt
    assert "#include <cstdlib>" in dflt
    eight = compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: chained\n    linear_transform_batch: 8\n"))["nb_shared.h"]
    assert ": 8L;" in eight
    # no batching where nothing is device-encoded
    for files in (compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: loop\n")),
                  compile_str(_lt_scheme(LT_SRC, "    linear_transform: chained\n"))):
        assert "nb_lt_batch" not in files["nb_shared.h"]
    # a negative batch is refused (the parser takes no negative scheme value)
    from xcomp.errors import ParseError
    try:
        compile_fideslib(_lt_scheme(LT_SRC, "    linear_transform: chained\n    linear_transform_batch: -1\n"))
        assert False, "linear_transform_batch: -1 accepted"
    except (cg.CodegenError, ParseError):
        pass
