"""Tests for modules: `module <name>`, `use <module>`, the search path, the
canonical-name resolver and the namespaced C++ the generator emits."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import xcomp.ast_nodes as ast
from xcomp.codegen import generate
from xcomp.errors import CompileError
from xcomp.modules import (ModuleError, ModuleLoader, base_name, fqn,
                           load_program, module_of)
from xcomp.semantic import analyze


# A small library module: a struct, an enum, a const, path helpers, a wire,
# an FHE function, a stage, and its own scheme + requires.
LIB = """
module lib

enum Size { Toy, Full }
const SCALE: f64 = 2.0
struct Instance { size: Size, ring_dim: u32 }
fn instance(size: Size) -> Instance {
    match size {
        Toy  => Instance { size, ring_dim: 2048 },
        Full => Instance { size, ring_dim: 65536 },
    }
}
fn iodir(inst: Instance) -> path  { root() / "io" }
fn keydir(inst: Instance) -> path { iodir(inst) / "keys" }
fn encdir(inst: Instance) -> path { iodir(inst) / "enc" }
wire CryptoParams { context: CryptoContext, public_key: PublicKey, eval_mult_key: EvalMultKey }
wire EncOut { ciphertext: enc<f64> }
fn twice(ct: enc<f64>) -> enc<f64> { ct * SCALE }
scheme CKKS { depth: 4 }
requires { add, mul, rotate }
@server @stage("compute")
fn compute(inst: Instance) -> reads(CryptoParams, EncOut), writes(EncOut) {
    let params = load(CryptoParams, from: keydir(inst))
    let x = load(EncOut, from: encdir(inst) / "in.bin").ciphertext
    return EncOut { ciphertext: twice(x) }
}
"""

APP = """
module app

use lib

// Same names as lib's: a different namespace, so no collision.
fn iodir(inst: lib::Instance) -> path  { lib::iodir(inst) }
fn keydir(inst: lib::Instance) -> path { lib::keydir(inst) }
fn encdir(inst: lib::Instance) -> path { lib::encdir(inst) }
const SCALE: f64 = 3.0
scheme CKKS { depth: 4 }
requires { add, mul }
@server @stage("compute")
fn compute(inst: lib::Instance) -> reads(lib::CryptoParams, lib::EncOut), writes(lib::EncOut) {
    let params = load(lib::CryptoParams, from: keydir(inst))
    let x = load(lib::EncOut, from: encdir(inst) / "in.bin").ciphertext
    let y = lib::twice(x) * SCALE
    match inst.size { lib::Toy => y, _ => y }
    return lib::EncOut { ciphertext: y }
}
"""


def _write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def _tree(**files):
    """A temp dir holding `files` ({relative path: text}); returns (dir, paths)."""
    d = Path(tempfile.mkdtemp(prefix="nbmod_"))
    paths = {rel: _write(d, rel, text) for rel, text in files.items()}
    return d, paths


def _compile(root_files, module_paths=None):
    program, loader = load_program(root_files, module_paths)
    sa = analyze(program)
    assert not sa.errors.has_errors(), sa.errors.report()
    return program, loader, generate(program, sa)


# ----- naming helpers -----

def test_fqn_helpers():
    assert fqn("", "x") == "x"
    assert fqn("m", "x") == "m::x"
    assert base_name("m::x") == "x" and base_name("x") == "x"
    assert module_of("m::x") == "m" and module_of("x") == ""


# ----- loading -----

def test_import_resolves_through_search_path():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    program, loader, files = _compile([p["app/server.niob"]])
    assert program.module_name == "app"
    assert loader.order == ["lib"]
    assert loader.modules["lib"].files == [p["lib/shared.niob"]]


def test_default_search_path_is_the_parent_of_the_inputs_dir():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    # No --module-path: app/.. (= d) is searched, where lib/ sits.
    program, loader, _ = _compile([p["app/server.niob"]])
    assert "lib" in loader.modules


def test_explicit_module_path_finds_a_module_elsewhere():
    d1, p1 = _tree(**{"app/server.niob": APP})
    d2, p2 = _tree(**{"libs/lib/shared.niob": LIB})
    try:
        load_program([p1["app/server.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "module 'lib' not found" in str(e)
        assert "--module-path" in str(e)
    program, loader, _ = _compile([p1["app/server.niob"]], [d2 / "libs"])
    assert loader.modules["lib"].files == [p2["libs/lib/shared.niob"]]


def test_module_split_across_files_is_one_module():
    lib_a, lib_b = LIB.split("wire CryptoParams", 1)
    lib_b = "module lib\n\nwire CryptoParams" + lib_b
    d, p = _tree(**{"lib/shared.niob": lib_a, "lib/server.niob": lib_b,
                    "app/server.niob": APP})
    program, loader, _ = _compile([p["app/server.niob"]])
    assert sorted(f.name for f in loader.modules["lib"].files) == ["server.niob", "shared.niob"]


def test_root_files_must_agree_on_the_module():
    d, p = _tree(**{"a.niob": "module x\nconst A: u32 = 1", "b.niob": "const B: u32 = 2"})
    try:
        load_program([p["a.niob"], p["b.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "same module" in str(e)


def test_import_cycle_is_an_error():
    d, p = _tree(**{"a/m.niob": "module a\nuse b\nconst X: u32 = 1",
                    "b/m.niob": "module b\nuse a\nconst Y: u32 = 1"})
    try:
        load_program([p["a/m.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "cycle" in str(e)


def test_self_import_is_an_error():
    d, p = _tree(**{"a/m.niob": "module a\nuse a\nconst X: u32 = 1"})
    try:
        load_program([p["a/m.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "imports itself" in str(e)


def test_file_glob_use_is_accepted_and_module_glob_rejected():
    d, p = _tree(**{"lib/shared.niob": LIB,
                    "app/a.niob": "module app\nuse shared::*\nuse lib\nconst X: u32 = 1"})
    program, loader, _ = _compile([p["app/a.niob"]])
    assert "lib" in loader.modules
    _write(d, "app2/a.niob", "module app2\nuse lib::*\nconst X: u32 = 1")
    try:
        load_program([d / "app2/a.niob"])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "`use lib`" in str(e) and "lib::name" in str(e)


def test_unimported_module_qualifier_is_an_error():
    d, p = _tree(**{"lib/shared.niob": LIB,
                    "app/a.niob": "module app\nfn f(i: lib::Instance) -> path { lib::iodir(i) }"})
    try:
        load_program([p["app/a.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "unknown module 'lib'" in str(e)


def test_unknown_member_of_imported_module_is_an_error():
    d, p = _tree(**{"lib/shared.niob": LIB,
                    "app/a.niob": "module app\nuse lib\nfn f(i: lib::Instance) -> path { lib::nowhere(i) }"})
    try:
        load_program([p["app/a.niob"]])
        assert False, "expected ModuleError"
    except ModuleError as e:
        assert "declares no 'nowhere'" in str(e)


# ----- resolution -----

def _fn(program, module, name):
    return next(i for i in program.items
                if isinstance(i, ast.FnDecl) and i.module == module and i.name == name)


def test_references_are_canonical_in_both_modules():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    program, loader, _ = _compile([p["app/server.niob"]])
    # Inside lib, a bare reference to its own declaration is lib::name.
    lib_keydir = _fn(program, "lib", "keydir")
    call = lib_keydir.body.stmts[0].expr.left if hasattr(lib_keydir.body.stmts[0], "expr") else None
    text = repr(lib_keydir.body)
    assert "lib::iodir" in text
    assert lib_keydir.params[0].type_ann.name == "lib::Instance"
    # Inside app, lib's names stay qualified and app's own become app::name.
    app_compute = _fn(program, "app", "compute")
    text = repr(app_compute.body)
    assert "app::keydir" in text and "app::encdir" in text and "app::SCALE" in text
    assert "lib::twice" in text and "lib::CryptoParams" in text and "lib::Toy" in text
    # Declarations keep their bare name; the module tag carries the rest.
    assert app_compute.name == "compute" and app_compute.module == "app"


def test_locals_shadow_module_declarations():
    src = ("module m\nconst N: u32 = 4\n"
           "fn f(N: u32) -> u32 { let k = N + 1\n return k }\n"
           "fn g() -> u32 { let N = 7\n return N }\n"
           "fn h() -> u32 { N }\n")
    d, p = _tree(**{"m/a.niob": src})
    program, _, _ = _compile([p["m/a.niob"]])
    assert "m::N" not in repr(_fn(program, "m", "f").body)
    assert "m::N" not in repr(_fn(program, "m", "g").body)
    assert "m::N" in repr(_fn(program, "m", "h").body)


def test_root_without_module_keyword_is_untouched():
    src = "const N: u32 = 4\nfn h() -> u32 { N }\n"
    d, p = _tree(**{"a.niob": src})
    program, loader, _ = _compile([p["a.niob"]])
    assert program.module_name == ""
    assert _fn(program, "", "h").body.stmts[0].expr.name == "N"


def test_requires_are_unioned_and_imported_scheme_dropped():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    program, loader, _ = _compile([p["app/server.niob"]])
    reqs = [i for i in program.items if isinstance(i, ast.RequiresDecl)]
    assert len(reqs) == 1 and reqs[0].capabilities == ["add", "mul", "rotate"]
    schemes = [i for i in program.items if isinstance(i, ast.SchemeDecl)]
    assert len(schemes) == 1 and schemes[0].module == "app"
    assert loader.warnings == []  # identical schemes: no warning


def test_differing_imported_scheme_warns():
    d, p = _tree(**{"lib/shared.niob": LIB,
                    "app/server.niob": APP.replace("depth: 4", "depth: 6")})
    program, loader, _ = _compile([p["app/server.niob"]])
    assert len(loader.warnings) == 1 and "scheme that differs" in loader.warnings[0]


# ----- generated C++ -----

def test_generated_cpp_has_one_namespace_per_module():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    _, _, files = _compile([p["app/server.niob"]])
    h = files["nb_shared.h"]
    assert h.index("namespace lib {") < h.index("}  // namespace lib") \
        < h.index("namespace app {") < h.index("}  // namespace app")
    lib_block = h[h.index("namespace lib {"):h.index("}  // namespace lib")]
    app_block = h[h.index("namespace app {"):h.index("}  // namespace app")]
    assert "struct Instance {" in lib_block and "enum Size {" in lib_block
    assert "constexpr double SCALE = 2.0;" in lib_block
    assert "constexpr double SCALE = 3.0;" in app_block
    assert "std::filesystem::path keydir(lib::Instance inst);" in app_block
    cpp = files["nb_shared.cpp"]
    assert cpp.index("namespace lib {") < cpp.index("namespace app {")
    assert "return lib::iodir(inst);" in cpp or "lib::iodir(inst)" in cpp


def test_imported_stage_is_prefixed_and_own_stage_is_not():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    _, _, files = _compile([p["app/server.niob"]])
    assert "compute.cpp" in files and "lib_compute.cpp" in files
    cmake = files["CMakeLists.txt"]
    assert "\n  compute\n" in cmake and "\n  lib_compute\n" in cmake
    own = files["compute.cpp"]
    assert "namespace app {" in own and "result = app::compute(cc, inst);" in own
    assert "app::keydir(inst)" in own and "app::encdir(inst)" in own
    # main() takes its Instance helpers from the module declaring the type.
    assert "static_cast<lib::Size>" in own and "auto inst = lib::instance(size);" in own
    imported = files["lib_compute.cpp"]
    assert "namespace lib {" in imported and "result = lib::compute(cc, inst);" in imported
    assert "lib::keydir(inst)" in imported and "lib::encdir(inst)" in imported


def test_reference_twin_mirrors_modules():
    d, p = _tree(**{"lib/shared.niob": LIB, "app/server.niob": APP})
    _, _, files = _compile([p["app/server.niob"]])
    ref = files["compute_ref.cpp"]
    assert "namespace nbref {" in ref
    assert "namespace lib {" in ref and "using namespace ::lib;" in ref
    assert "using EncOut = ::lib::EncOutRef;" in ref
    assert "namespace app {" in ref and "using namespace ::app;" in ref
    assert "nbref::app::compute(inst)" in ref


def test_unnamed_root_generates_exactly_as_before():
    """A program without `module` must not gain namespaces."""
    src = ("enum Size { Toy }\nstruct Instance { size: Size, ring_dim: u32 }\n"
           "fn instance(size: Size) -> Instance { Instance { size, ring_dim: 2048 } }\n"
           "fn keydir(inst: Instance) -> path { root() / \"k\" }\n"
           "wire EncOut { ciphertext: enc<f64> }\n"
           "@client @stage(\"enc_stage\")\nfn enc_stage(inst: Instance) -> writes(EncOut) {\n"
           "    let ct: enc<f64> = zero()\n    return EncOut { ciphertext: ct }\n}\n")
    d, p = _tree(**{"a.niob": src})
    _, _, files = _compile([p["a.niob"]])
    assert "namespace " not in files["nb_shared.h"].replace("namespace fs", "").replace("namespace nb_plain", "").replace("namespace lbcrypto", "")
    assert "auto inst = instance(size);" in files["enc_stage.cpp"]


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
