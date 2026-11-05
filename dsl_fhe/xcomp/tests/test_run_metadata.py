"""Run metadata: the recipe and plaintext vector sidecars, against their schemas.

The generated nb_recipe helpers (bsgs_diagonals, the recipes of a data
transform's diagonals, the plaintext vector writer) need only the standard
library. They are taken from the header generated for a small circuit,
compiled with a driver that makes the calls the generated stage makes, and
run with NB_DT_RECIPES and NB_PLAINTEXT_VECTORS set; every line written is
validated against dsl_fhe/schemas/, and the values against
dsl_fhe/docs/RUN_METADATA.md. Skipped when no C++ compiler is found.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
SCHEMAS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "schemas")

from xcomp.lexer import lex
from xcomp.parser import parse
from xcomp.semantic import analyze
from xcomp.codegen import generate, RECIPE_HELPERS


CIRCUIT = """
enum InstanceSize { Toy }
struct Instance { size: u32 }
fn instance(size: u32) -> Instance { Instance { size: size } }
fn keydir(inst: Instance) -> path { root() / "keys" }
fn encdir(inst: Instance) -> path { root() / "enc" }
wire CryptoParams { context: CryptoContext, public_key: PublicKey }
wire Input { x: enc<f64> }
wire Output { y: enc<f64> }
scheme CKKS {
    security: not_set
    depth: 4
    precision: 50 bits
    first_mod: 60 bits
}
requires { add, mul, rotate([1, 2]) }

@client @stage("key_generation")
fn generate_keys(inst: Instance) -> writes(CryptoParams) {
    let keys = keygen()
    return CryptoParams { context: keys.context, public_key: keys.public }
}

@server @stage("compute")
fn compute(inst: Instance) -> reads(Input) writes(Output) {
    let i = load(Input, from: encdir(inst))
    let w = load_matrix<f64>(root() / "w.bin", 4)
    let d = bsgs_diagonals(w, 1, 1, 2, 3, 2, 8, 16, 0, 1, 0.5)
    let y = linear_transform(i.x, d, 2) + [1.0, 2.0]
    return Output { y: y * [0.5, 0.25, 0.125] }
}
"""

DRIVER = r"""
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
@HELPERS@
// A plaintext as the back ends' handles expose it: a pointer to an object
// with the level and slots it was encoded at.
struct Pt { uint32_t level, slots; uint32_t GetLevel() const { return level; } uint32_t GetSlots() const { return slots; } };
using Plaintext = std::shared_ptr<Pt>;

static void product(const std::vector<std::vector<double>>& d, uint32_t level, uint32_t slots) {
    // what the generated linear_transform does with its diagonals
    const nb_recipe::Diagonals* rc = nb_recipe::find(d);
    if (nb_recipe::on()) nb_recipe::emit(rc, d.size(), level, slots);
}
static void dump(const std::string& f, const std::vector<std::vector<double>>& d) {
    std::ofstream o(f, std::ios::binary);
    for (auto& v : d) o.write(reinterpret_cast<const char*>(v.data()), std::streamsize(v.size() * sizeof(double)));
}

int main(int argc, char** argv) {
    const std::string w_file = argv[1], out = argv[2];
    // load_matrix's reader: rows of 4 float64, then note_source
    std::vector<std::vector<double>> w;
    {
        std::ifstream f(w_file, std::ios::binary);
        std::vector<double> r(4);
        while (f.read(reinterpret_cast<char*>(r.data()), 4 * sizeof(double))) w.push_back(r);
        nb_recipe::note_source(w_file, 4, w);
    }
    auto d = nb_bsgs_diagonals(w, 1, 1, 2, 3, 2, 8, 16, 0, 1, 0.5);
    dump(out + "/d.f64", d);
    product(d, 3, 16);                                  // product 0: the recipe of set 0
    product(d, 2, 16);                                  // product 1: the same set again
    std::vector<std::vector<double>> other(3, std::vector<double>(16, 1.0));
    product(other, 3, 16);                              // product 2: no recipe
    auto w2 = w;
    w2[0][0] += 1.0;                                    // not the loaded matrix any more
    auto e = nb_bsgs_diagonals(w2, 0, 0, 3, 2, 1, 4, 8, 0, 0, -2.0);
    product(e, 1, 8);                                   // product 3: set 1, file null
    d[0][1] = 7.0;                                      // changed after it was built
    product(d, 3, 16);                                  // product 4: no recipe
    try { nb_bsgs_diagonals(w, 3, 0, 2, 1, 1, 4, 8, 0, 0, 1.0); return 3; }
    catch (const std::runtime_error&) {}                // rows 3 .. 4 are not in w
    // the ciphertext-vector plaintexts of the stage, in order
    Plaintext a = std::make_shared<Pt>(Pt{3, 16}), b = std::make_shared<Pt>(Pt{2, 16});
    std::vector<double> va = {1.0, 2.0}, vb = {0.5, 0.25, 0.125};
    nb_recipe::plaintext(&a, va);
    nb_recipe::plaintext(&b, vb);
    return 0;
}
"""


# ── a minimal JSON Schema (2020-12) validator: the keywords dsl_fhe/schemas use ──

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs"}
_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(value, schema, where="$", root=None):
    root = schema if root is None else root
    for key in schema:
        if key not in _ANNOTATIONS | {"type", "const", "enum", "required", "properties", "additionalProperties",
                                      "minimum", "maximum", "pattern", "items", "minItems", "maxItems",
                                      "anyOf", "$ref"}:
            raise AssertionError(f"{where}: the test's validator does not know the keyword {key}")
    if "$ref" in schema:
        ref = schema["$ref"]
        assert ref.startswith("#/"), f"{where}: only local references: {ref}"
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        validate(value, target, where, root)
    if "anyOf" in schema:
        errors = []
        for i, sub in enumerate(schema["anyOf"]):
            try:
                validate(value, sub, where, root)
                break
            except AssertionError as e:
                errors.append(str(e))
        else:
            raise AssertionError(f"{where}: matches none of anyOf: {'; '.join(errors)}")
    if "enum" in schema:
        assert any(value == v and type(value) is type(v) for v in schema["enum"]), \
            f"{where}: {value!r} is not one of {schema['enum']}"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(_TYPES[t](value) for t in types), f"{where}: {value!r} is not {types}"
    if "const" in schema:
        assert value == schema["const"] and type(value) is type(schema["const"]), \
            f"{where}: {value!r} is not {schema['const']!r}"
    if isinstance(value, dict):
        for k in schema.get("required", []):
            assert k in value, f"{where}: {k} is missing"
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                validate(v, props[k], f"{where}.{k}", root)
            else:
                assert extra is not False, f"{where}: unexpected {k}"
                if isinstance(extra, dict):
                    validate(v, extra, f"{where}.{k}", root)
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0), f"{where}: fewer than {schema['minItems']} items"
        assert "maxItems" not in schema or len(value) <= schema["maxItems"], \
            f"{where}: more than {schema['maxItems']} items"
        if "items" in schema:
            for i, v in enumerate(value):
                validate(v, schema["items"], f"{where}[{i}]", root)
    if _TYPES["number"](value):
        if "minimum" in schema:
            assert value >= schema["minimum"], f"{where}: {value} < {schema['minimum']}"
        if "maximum" in schema:
            assert value <= schema["maximum"], f"{where}: {value} > {schema['maximum']}"
    if isinstance(value, str) and "pattern" in schema:
        assert re.search(schema["pattern"], value), f"{where}: {value!r} does not match {schema['pattern']}"


def load_schema(name):
    with open(os.path.join(SCHEMAS, f"{name}.schema.json")) as f:
        s = json.load(f)
    assert s["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    return s


def matrix_digest(m):
    """The matrix digest of dsl_fhe/docs/RUN_METADATA.md."""
    mask, prime = (1 << 64) - 1, 0x100000001b3
    h = 0xcbf29ce484222325 ^ len(m)
    for row in m:
        h = ((h ^ len(row)) * prime) & mask
        for v in row:
            u = struct.unpack("<Q", struct.pack("<d", v))[0]
            h = ((h ^ u) * prime) & mask
            h ^= h >> 31
    return f"{h:016x}"


def bsgs(w, row0, col0, width, count, bstep, region, length, r_lo, r_hi, s):
    """bsgs_diagonals as dsl_fhe/docs/RUN_METADATA.md states it."""
    d = [[0.0 * s] * length for _ in range(count)]
    for k in range(count):
        shift = bstep * (k // bstep)
        for r in range(r_lo, r_hi + 1):
            for i in range(width):
                d[k][r * region + shift + i] = w[row0 + (i + k) % width][col0 + i] * s
    return d


# ── tests ────────────────────────────────────────────────────────────────────

def test_schemas_are_well_formed():
    for name, sid in (("data_transform_recipe", "nb.data_transform_recipe"), ("plaintext_vector", "nb.plaintext_vector")):
        s = load_schema(name)
        assert s["properties"]["schema"]["const"] == sid
        assert s["properties"]["version"]["const"] == 1
        assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
        validate({}, {"type": "object"})  # the validator itself runs


def test_validator_rejects():
    s = load_schema("plaintext_vector")
    good = {"schema": "nb.plaintext_vector", "version": 1, "tag": "pv:0", "length": 2, "level": 1,
            "slots": 8, "noise_scale_deg": 1}
    validate(good, s)
    for bad in ({**good, "version": 2}, {**good, "tag": "pv:01"}, {**good, "length": -1},
                {**good, "extra": 0}, {k: v for k, v in good.items() if k != "level"}, {**good, "slots": True}):
        try:
            validate(bad, s)
        except AssertionError:
            continue
        raise AssertionError(f"accepted {bad}")


def _generated_helpers():
    lexed = lex(CIRCUIT)
    program = parse(lexed)
    files = generate(program, analyze(program))
    header = files["nb_shared.h"]
    helpers = RECIPE_HELPERS.strip("\n")
    assert helpers in header, "the generated header does not carry the nb_recipe helpers verbatim"
    stage = files["compute.cpp"] + files["nb_shared.cpp"]
    assert "nb_bsgs_diagonals(w, 1, 1, 2, 3, 2, 8, 16, 0, 1, 0.5)" in stage
    assert stage.count("nb_recipe::plaintext(&_pt, _v);") == 2
    assert "nb_recipe::note_source(fname, record_dim, result);" in header
    return helpers


def test_sidecars_validate_against_the_schemas():
    helpers = _generated_helpers()
    cxx = shutil.which(os.environ.get("CXX", "g++")) or shutil.which("c++")
    if not cxx:
        print("    (skipped: no C++ compiler)")
        return
    work = tempfile.mkdtemp(prefix="nb_run_metadata_")
    try:
        src = os.path.join(work, "driver.cpp")
        with open(src, "w") as f:
            f.write(DRIVER.replace("@HELPERS@", helpers))
        exe = os.path.join(work, "driver")
        subprocess.run([cxx, "-std=c++17", "-O1", "-o", exe, src], check=True, capture_output=True, text=True)
        w = [[float(r * 4 + c) + 0.5 for c in range(4)] for r in range(4)]
        w_file = os.path.join(work, "w.bin")
        with open(w_file, "wb") as f:
            f.write(b"".join(struct.pack("<4d", *row) for row in w))
        recipes, vectors = os.path.join(work, "recipes.jsonl"), os.path.join(work, "vectors")
        env = {**os.environ, "NB_DT_RECIPES": recipes, "NB_PLAINTEXT_VECTORS": vectors}
        env.pop("NB_LT_RECIPES", None)
        subprocess.run([exe, w_file, work], check=True, env=env, capture_output=True, text=True)

        # the recipes: one line per product, in the order they ran
        rs = load_schema("data_transform_recipe")
        with open(recipes) as f:
            lines = f.read().split("\n")
        assert lines[-1] == "" and all(lines[:-1]), "one record per line, each ended by a newline"
        recs = [json.loads(l) for l in lines[:-1]]
        for r in recs:
            validate(r, rs)
        assert [r["product"] for r in recs] == [0, 1, 2, 3, 4]
        assert [r["recipe"] is None for r in recs] == [False, False, True, False, True]
        r0, r1, r3 = recs[0]["recipe"], recs[1]["recipe"], recs[3]["recipe"]
        assert r0 == r1 and r0["set"] == 0 and r3["set"] == 1
        assert (recs[0]["level"], recs[1]["level"], recs[0]["slots"], recs[0]["diagonals"]) == (3, 2, 16, 3)
        assert r0["file"] == w_file and (r0["rows"], r0["cols"]) == (4, 4)
        assert r0["digest"] == matrix_digest(w)
        assert {k: r0[k] for k in ("row0", "col0", "width", "count", "bstep", "region", "length", "r_lo", "r_hi",
                                   "scale")} == dict(row0=1, col0=1, width=2, count=3, bstep=2, region=8, length=16,
                                                     r_lo=0, r_hi=1, scale=0.5)
        w2 = [row[:] for row in w]
        w2[0][0] += 1.0
        assert r3["file"] is None and r3["digest"] == matrix_digest(w2) and r3["scale"] == -2.0
        # the line format, field order included, is the documented one
        assert lines[0].startswith('{"schema": "nb.data_transform_recipe", "version": 1, "product": 0, '
                                   '"diagonals": 3, "level": 3, "slots": 16, "noise_scale_deg": 1, '
                                   '"recipe": {"kind": "bsgs_diagonals", "set": 0, "file": ')
        # the diagonals are the documented ones: the recipe rebuilds them
        with open(os.path.join(work, "d.f64"), "rb") as f:
            raw = f.read()
        built = [list(struct.unpack(f"<{r0['length']}d", raw[k * 8 * r0['length']:(k + 1) * 8 * r0['length']]))
                 for k in range(r0["count"])]
        args = [r0[k] for k in ("row0", "col0", "width", "count", "bstep", "region", "length", "r_lo", "r_hi",
                                "scale")]
        assert built == bsgs(w, *args)

        # the plaintext vectors: one line each, in the order they were encoded
        vs = load_schema("plaintext_vector")
        with open(os.path.join(vectors, "vectors.jsonl")) as f:
            vrecs = [json.loads(l) for l in f.read().splitlines()]
        for v in vrecs:
            validate(v, vs)
        assert [(v["tag"], v["length"], v["level"], v["slots"]) for v in vrecs] == [("pv:0", 2, 3, 16),
                                                                                  ("pv:1", 3, 2, 16)]
        for v, want in zip(vrecs, ([1.0, 2.0], [0.5, 0.25, 0.125])):
            with open(os.path.join(vectors, f"pv_{v['tag'][3:]}.f64"), "rb") as f:
                data = f.read()
            assert len(data) == 8 * v["length"] and list(struct.unpack(f"<{v['length']}d", data)) == want
        assert sorted(os.listdir(vectors)) == ["pv_0.f64", "pv_1.f64", "vectors.jsonl"]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_recipes_off_by_default():
    # a run that sets neither variable writes nothing and keeps no registry
    helpers = RECIPE_HELPERS
    assert "if (!on()) return;" in helpers and "if (!f) return;" in helpers
    assert helpers.count('std::getenv("') == 3  # NB_DT_RECIPES, NB_LT_RECIPES, NB_PLAINTEXT_VECTORS


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
