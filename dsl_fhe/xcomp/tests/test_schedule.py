"""The stage schedule (nbc compile --schedule, nb_schedule.json).

A server entry point that only calls stages, in for loops over ranges, gets a
schedule: its steps, their symbolic arguments and the state each one reads
and writes. One that does anything else gets none and a one-line reason. The
document is validated against dsl_fhe/schemas/stage_schedule.schema.json with
the validator of test_run_metadata.py.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DSL = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, DSL)
sys.path.insert(0, HERE)

from xcomp.lexer import lex
from xcomp.parser import parse
from xcomp.semantic import analyze
from xcomp.codegen import generate
from xcomp.params import apply_overrides
from xcomp import schedule
from test_run_metadata import load_schema, validate


PRELUDE = """
enum InstanceSize { Toy }
struct Instance { size: u32 }
fn instance(size: u32) -> Instance { Instance { size: size } }
fn keydir(inst: Instance) -> path { root() / "keys" }
fn encdir(inst: Instance) -> path { root() / "enc" }
fn statedir(inst: Instance, i: u32) -> path { encdir(inst) / ("step_" + str(i)) }
const ROUNDS: u32 = 3
const PARTS: u32 = 2
wire CryptoParams { context: CryptoContext, public_key: PublicKey }
wire Input { x: enc<vec<f64>> }
wire State { ciphertext: enc<vec<f64>> }
wire Part { ciphertext: enc<vec<f64>> }
scheme CKKS {
    security: not_set
    depth: 4
    precision: 50 bits
    first_mod: 60 bits
}
requires { add, mul }

@client @stage("key_generation")
fn generate_keys(inst: Instance) -> writes(CryptoParams) {
    let keys = keygen()
    return CryptoParams { context: keys.context, public_key: keys.public }
}

@server @stage("prepare")
@hardware(cache_key: ["workload_size"])
fn prepare(inst: Instance) -> reads(CryptoParams, Input) {
    let params = load(CryptoParams, from: keydir(inst))
    let x = load(Input, from: encdir(inst)).x
    save(State { ciphertext: x * x }, to: encdir(inst) / "state.bin")
}

// reads and writes the same file
@server @stage("update")
@hardware(cache_key: ["workload_size", "i", "j"])
fn update(inst: Instance, i: u32 = 0, j: u32 = 0, scale: f64 = 0.5) -> reads(CryptoParams, State) {
    let params = load(CryptoParams, from: keydir(inst))
    let s = load(State, from: encdir(inst) / "state.bin").ciphertext
    save(Part { ciphertext: s * scale }, to: statedir(inst, i) / ("part_" + str(j)) / "part.bin")
    save(State { ciphertext: s + s }, to: encdir(inst) / "state.bin")
}

@server @stage("combine")
@hardware(cache_key: ["workload_size", "i"])
fn combine(inst: Instance, i: u32 = 0) -> reads(CryptoParams, State, Part) {
    let params = load(CryptoParams, from: keydir(inst))
    let s = load(State, from: encdir(inst) / "state.bin").ciphertext
    let a = load(Part, from: statedir(inst, i) / "part_0" / "part.bin").ciphertext
    save(State { ciphertext: s + a }, to: encdir(inst) / "state.bin")
}
"""

# The test circuit: one entry point with a schedule (nested loops, bounds
# from an argument and from build parameters), one without.
CIRCUIT = PRELUDE + """
@server @stage("run")
fn run(inst: Instance, first: u32 = 0) -> reads(CryptoParams) {
    prepare(inst)
    for i in first..ROUNDS {
        for j in 0..PARTS {
            update(inst, i, j)
        }
        combine(inst, i)
    }
}

@server @stage("run_mixed")
fn run_mixed(inst: Instance) -> reads(CryptoParams, State) {
    prepare(inst)
    let s = load(State, from: encdir(inst) / "state.bin").ciphertext
    save(State { ciphertext: s * s }, to: encdir(inst) / "state.bin")
}
"""

# the line of run_mixed's arithmetic, which its reason names
MIXED_LINE = CIRCUIT.splitlines().index('    save(State { ciphertext: s * s }, to: encdir(inst) / "state.bin")') + 1


def build(source, sets=None):
    program = parse(lex(source, "c.niob"))
    params = apply_overrides(program, sets)
    sa = analyze(program)
    assert not sa.errors.has_errors(), sa.errors.report()
    doc, reasons = schedule.build(program, sa)
    return doc, reasons, program, sa, params


def compact(node):
    """A step or loop with its expressions as text: what the tests compare."""
    t = schedule.text
    if "for" in node:
        return ("for", node["for"], t(node["from"]), t(node["to"]), [compact(n) for n in node["body"]])
    st = lambda lst: [(s["wire"], s["path"] and s["path"]["text"]) for s in lst]
    return (node["stage"], {k: t(v) for k, v in node["args"].items()}, st(node["reads"]), st(node["writes"]))


KEYS = ("CryptoParams", "keydir(inst)")
STATE = ("State", 'encdir(inst) / "state.bin"')
RUN = [
    ("prepare", {"inst": "inst"}, [KEYS, ("Input", "encdir(inst)")], [STATE]),
    ("for", "i", "first", "ROUNDS", [
        ("for", "j", "0", "PARTS", [
            ("update", {"inst": "inst", "i": "i", "j": "j", "scale": "0.5"}, [KEYS, STATE],
             [("Part", 'statedir(inst, i) / ("part_" + str(j)) / "part.bin"'), STATE]),
        ]),
        ("combine", {"inst": "inst", "i": "i"},
         [KEYS, STATE, ("Part", 'statedir(inst, i) / "part_0" / "part.bin"')], [STATE]),
    ]),
]


def test_qualifying_entry_point():
    doc, reasons, *_ = build(CIRCUIT)
    assert doc["schema"] == "nb.stage_schedule" and doc["version"] == 1
    assert [e["entry"] for e in doc["entries"]] == ["run"]
    run = doc["entries"][0]
    assert run["args"] == [{"name": "inst", "type": "Instance"}, {"name": "first", "type": "u32", "default": 0}]
    assert [compact(n) for n in run["steps"]] == RUN
    prepare = run["steps"][0]
    assert prepare["hardware"] == {"cache_key": ["workload_size"]}
    assert prepare["args"] == {"inst": {"arg": "inst"}}


def test_nested_loops_keep_their_variables():
    doc, *_ = build(CIRCUIT)
    outer = doc["entries"][0]["steps"][1]
    assert (outer["for"], outer["from"], outer["to"]) == ("i", {"arg": "first"}, {"param": "ROUNDS"})
    inner = outer["body"][0]
    assert (inner["for"], inner["from"], inner["to"]) == ("j", 0, {"param": "PARTS"})
    part = inner["body"][0]["writes"][0]["path"]
    assert part["vars"] == ["i", "j"]
    # the path is the stage's, its parameters replaced by the step's arguments
    assert part["expr"] == {"op": "/", "args": [
        {"op": "/", "args": [{"call": "statedir", "args": [{"arg": "inst"}, {"var": "i"}]},
                             {"op": "+", "args": ["part_", {"call": "str", "args": [{"var": "j"}]}]}]},
        "part.bin"]}


def test_build_parameters_from_set():
    doc, *_ = build(CIRCUIT)
    assert doc["params"] == {"PARTS": 2, "ROUNDS": 3}
    doc, *_ = build(CIRCUIT, ["ROUNDS=5", "PARTS=4"])
    assert doc["params"] == {"PARTS": 4, "ROUNDS": 5}
    # the loops stay loops: the bound names the parameter, the value is in params
    assert doc["entries"][0]["steps"][1]["to"] == {"param": "ROUNDS"}


def test_stage_reads_and_writes_the_same_file():
    doc, *_ = build(CIRCUIT)
    update = doc["entries"][0]["steps"][1]["body"][0]["body"][0]
    assert update["stage"] == "update"
    read = [s for s in update["reads"] if s["wire"] == "State"]
    written = [s for s in update["writes"] if s["wire"] == "State"]
    assert len(read) == 1 and read == written


def test_non_qualifying_entry_point_has_a_reason():
    doc, reasons, *_ = build(CIRCUIT)
    assert all(e["entry"] != "run_mixed" for e in doc["entries"])
    assert reasons == [f"nbc: no stage schedule for run_mixed: c.niob:{MIXED_LINE}: "
                       "arithmetic on encrypted values outside a stage"]


def entry(body, params="inst: Instance"):
    return PRELUDE + f"\n@server @stage(\"go\")\nfn go({params}) -> reads(CryptoParams) {{\n{body}\n}}\n"


def reason(source):
    doc, reasons, *_ = build(source)
    assert doc is None, doc
    assert len(reasons) == 1 and reasons[0].startswith("nbc: no stage schedule for go: c.niob:"), reasons
    return reasons[0].split(": ", 3)[3]


def test_reasons():
    cases = [
        ("prepare(inst)\nlet k = PARTS * 2", "a value computed outside a stage (let k)"),
        ("prepare(inst)\nlet s = load(State, from: encdir(inst) / \"state.bin\")", "state loaded outside a stage (let s)"),
        ("prepare(inst)\nif PARTS > 1 { combine(inst, 0) }", "a branch outside a stage: every step of a schedule runs"),
        ("prepare(inst)\nprint(\"x\")", "a call to print, which is not a stage"),
        ("for i in 0..ROUNDS { update(inst, i, n) }",
         "stage update, argument j: n is not a loop variable, an argument or a constant"),
        ("for i in 0..ROUNDS { update(inst, i, statedir(inst, i)) }",
         "stage update, argument j: not a loop variable, an argument, a constant or arithmetic on them"),
        ("for i in [1, 2] { combine(inst, i) }", "a for loop over a value, not over a range lo..hi"),
        ("generate_keys(inst)", "stage key_generation: a client stage, not a server stage"),
    ]
    for body, want in cases:
        assert reason(entry(body)) == want, (body, reason(entry(body)))


def test_reasons_from_the_stages():
    def callee(fn):
        return PRELUDE + fn + '\n@server @stage("go")\nfn go(inst: Instance) -> reads(CryptoParams) {\n    k(inst, 1)\n}\n'
    cases = [
        ('@server @stage("k")\nfn k(inst: Instance, i: u32) -> reads(State) writes(State) {\n'
         '    let s = load(State, from: statedir(inst, i) / "s.bin")\n    return State { ciphertext: s.ciphertext }\n}',
         "stage k returns its result; a step's results must be saved with save()"),
        ('@server @stage("k")\nfn k(inst: Instance, i: u32) -> reads(State) {\n    for c in 0..PARTS {\n'
         '        let s = load(State, from: statedir(inst, c) / "s.bin")\n    }\n}',
         "stage k: load() inside a loop, a branch or a closure"),
        ('fn peek(inst: Instance) -> enc<vec<f64>> { load(State, from: encdir(inst) / "s.bin").ciphertext }\n'
         '@server @stage("k")\nfn k(inst: Instance, i: u32) {\n'
         '    save(State { ciphertext: peek(inst) }, to: encdir(inst) / "t.bin")\n}',
         "stage k: peek() calls load(); a step's state must be loaded and saved by the stage itself"),
        ('@server @stage("k")\nfn k(inst: Instance, i: u32) {\n    let s = load(State, from: encdir(inst) / "s.bin")\n'
         '    let t = s\n    save(t, to: encdir(inst) / "t.bin")\n}',
         "stage k: save() of a value whose wire type is not known (save a WireType { ... } literal or a loaded wire)"),
    ]
    for fn, want in cases:
        got = reason(callee(fn))
        assert got == want, (fn, got)


def test_symbolic_arguments_and_local_paths():
    # arithmetic on loop variables, an inclusive range, a default, a field of
    # an argument, and a path through a let of the stage (no @hardware: the
    # recording's own rule on save paths does not apply)
    src = PRELUDE + """
@server @stage("k")
fn k(inst: Instance, i: u32, w: u32 = PARTS) -> reads(State) {
    let base = statedir(inst, i + 1)
    let s = load(State, from: base / "in.bin")
    save(s, to: base / "out.bin")
}

@server @stage("go")
fn go(inst: Instance, n: u32 = 4) -> reads(CryptoParams) {
    for i in 1..=n {
        k(inst, ROUNDS * i - 1)
        k(inst, inst.size, w: 2)
    }
}
"""
    doc, reasons, *_ = build(src)
    assert reasons == []
    loop = doc["entries"][0]["steps"][0]
    assert loop["to"] == {"op": "+", "args": [{"arg": "n"}, 1]}
    a, b = [compact(n) for n in loop["body"]]
    assert a == ("k", {"inst": "inst", "i": "ROUNDS * i - 1", "w": "PARTS"},
                 [("State", 'statedir(inst, ROUNDS * i - 1 + 1) / "in.bin"')],
                 [("State", 'statedir(inst, ROUNDS * i - 1 + 1) / "out.bin"')])
    assert b[1] == {"inst": "inst", "i": "inst.size", "w": "2"}
    assert doc["params"] == {"PARTS": 2, "ROUNDS": 3}


def test_schedule_validates_against_the_schema():
    s = load_schema("stage_schedule")
    assert s["properties"]["schema"]["const"] == "nb.stage_schedule"
    assert s["properties"]["version"]["const"] == 1
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    doc, *_ = build(CIRCUIT)
    validate(doc, s)
    validate(build(CIRCUIT, ["ROUNDS=7"])[0], s)
    bad = json.loads(json.dumps(doc))
    for mutate in (lambda d: d.update(version=2),
                   lambda d: d["entries"][0]["steps"][0].pop("writes"),
                   lambda d: d["entries"][0]["steps"][1].update(to={"name": "ROUNDS"}),
                   lambda d: d["entries"][0]["steps"][0]["args"].update(inst={"op": "^", "args": [1, 2]})):
        d = json.loads(json.dumps(doc))
        mutate(d)
        try:
            validate(d, s)
        except AssertionError:
            continue
        raise AssertionError(f"accepted {d}")


def test_entry_point_compiles_its_stages():
    # the entry point's binary runs the stages it calls: their bodies are in
    # its translation unit, each called with the context
    files = generate(*build(CIRCUIT)[2:4])
    run = files["run.cpp"]
    for fn in ("prepare", "update", "combine"):
        assert f"void {fn}(CryptoContext<DCRTPoly> cc, Instance inst" in run
    assert "uint32_t i = 0, uint32_t j = 0, double scale = 0.5);" in run
    assert "update(cc, inst, i, j);" in run and "combine(cc, inst, i);" in run
    assert "niobium::compiler().start()" not in run   # a called stage is not recorded on its own
    assert "run_ref.cpp" not in files and "update_ref.cpp" in files
    assert "#include \"niobium/compiler.h\"" in files["update.cpp"]


def test_nbc_writes_the_schedule_only_when_asked():
    with tempfile.TemporaryDirectory(prefix="nb_schedule_") as work:
        src = os.path.join(work, "c.niob")
        with open(src, "w") as f:
            f.write(CIRCUIT)
        env = {**os.environ, "PYTHONPATH": DSL}
        cmd = [sys.executable, "-m", "xcomp.nbc", "compile", src]
        off = subprocess.run(cmd + ["--outdir", os.path.join(work, "off")], cwd=os.path.join(DSL, "xcomp"),
                             env=env, capture_output=True, text=True)
        assert off.returncode == 0, off.stderr
        assert not os.path.exists(os.path.join(work, "off", "nb_schedule.json"))
        assert "no stage schedule" not in off.stderr
        on = subprocess.run(cmd + ["--outdir", os.path.join(work, "on"), "--schedule", "--set", "PARTS=6"],
                            cwd=os.path.join(DSL, "xcomp"), env=env, capture_output=True, text=True)
        assert on.returncode == 0, on.stderr
        with open(os.path.join(work, "on", "nb_schedule.json")) as f:
            doc = json.load(f)
        assert doc["params"] == {"PARTS": 6, "ROUNDS": 3}
        validate(doc, load_schema("stage_schedule"))
        assert [l for l in on.stderr.splitlines() if "no stage schedule" in l] == [
            f"nbc: no stage schedule for run_mixed: {src}:{MIXED_LINE}: "
            "arithmetic on encrypted values outside a stage"]


def test_no_entry_point():
    doc, reasons, *_ = build(PRELUDE)
    assert doc is None and reasons == []


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
            print(f"  FAIL  {t.__name__}: {e!r}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
