"""Tests for compile-time parameters: nbc compile --set NAME=VALUE."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcomp.codegen import generate
from xcomp.errors import CompileError
from xcomp.lexer import lex
from xcomp.params import apply_overrides
from xcomp.parser import parse
from xcomp.semantic import analyze

SRC = """
const PARAM1: u32 = 16
const PARAM2: f64 = 0.5
const PARAM3: bool = false
fn scaled(n: u32) -> f64 { return (n |> as f64) * PARAM2 }
fn total() -> u32 {
    let s = 0
    for i in 0..PARAM1 {
        s = s + i
    }
    return s
}
"""


def build(source: str, sets: list[str]):
    program = parse(lex(source))
    params = apply_overrides(program, sets)
    sa = analyze(program)
    assert not sa.errors.has_errors(), sa.errors.report()
    return params, generate(program, sa, params=params)


def test_default_without_set():
    params, files = build(SRC, [])
    assert params == []
    assert "constexpr uint32_t PARAM1 = 16;" in files["nb_shared.h"]
    assert "nb_params.json" not in files
    assert "nbc --set" not in files["nb_shared.h"]


def test_set_overrides_each_type():
    params, files = build(SRC, ["PARAM1=64", "PARAM2=0.25", "PARAM3=true"])
    header = files["nb_shared.h"]
    assert "constexpr uint32_t PARAM1 = 64;" in header
    assert "PARAM2 = 0.25" in header
    assert "PARAM3 = true" in header
    assert [(p.name, p.value) for p in params] == [("PARAM1", 64), ("PARAM2", 0.25), ("PARAM3", True)]
    assert json.loads(files["nb_params.json"]) == {"PARAM1": 64, "PARAM2": 0.25, "PARAM3": True}
    assert "// nbc --set PARAM1=64" in header


def test_set_reaches_compile_time_uses():
    """The override replaces the initializer before analysis: a loop bound
    written as the constant sees the new value through the constexpr."""
    _, files = build(SRC, ["PARAM1=3"])
    assert "constexpr uint32_t PARAM1 = 3;" in files["nb_shared.h"]
    assert "PARAM1" in files["nb_shared.cpp"]


@pytest.mark.parametrize("arg, message", [
    ("PARAM9=1", "no top-level const"),
    ("PARAM1=-4", "cannot be negative"),
    ("PARAM1=1.5", "not an integer"),
    ("PARAM2=abc", "not a number"),
    ("PARAM3=yes", "true or false"),
    ("PARAM1", "NAME=VALUE"),
])
def test_bad_set_is_an_error(arg, message):
    with pytest.raises(CompileError) as e:
        apply_overrides(parse(lex(SRC)), [arg])
    assert message in str(e.value)


def test_set_twice_is_an_error():
    with pytest.raises(CompileError) as e:
        apply_overrides(parse(lex(SRC)), ["PARAM1=2", "PARAM1=3"])
    assert "more than once" in str(e.value)


def test_hardware_stage_cache_parameters_carry_the_set():
    """A recording made with one value must not be replayed for another:
    the @hardware stage's cache parameters include every override."""
    src = """
    struct Instance { ring_dim: u32 }
    const PARAM1: u32 = 16
    wire EncOut { ciphertext: enc<f64> }
    fn outdir(inst: Instance) -> path { "io" / "out" }
    @server @stage(name: "compute") @hardware(cache_key: ["wl"])
    fn compute(inst: Instance) -> writes(EncOut) {
        let ct: enc<f64> = zero()
        return EncOut { ciphertext: ct }
    }
    """
    _, files = build(src, ["PARAM1=32"])
    assert 'nb_params.push_back({"nbc:PARAM1", "32"});' in files["compute.cpp"]
    _, files = build(src, [])
    assert "nbc:PARAM1" not in files["compute.cpp"]


# --scheme FIELD=VALUE: the scheme fields that trade run time against device
# memory, overridden for one build

def _scheme_build(field_lines: str, schemes: list[str]):
    from xcomp.params import apply_scheme_overrides
    from xcomp.tests.test_codegen import LT_SRC, _lt_scheme
    program = parse(lex(_lt_scheme(LT_SRC, field_lines)))
    params = apply_scheme_overrides(program, schemes)
    sa = analyze(program)
    assert not sa.errors.has_errors(), sa.errors.report()
    return params, generate(program, sa, backend="fideslib", params=params)


def test_scheme_override_sets_the_memory_and_is_recorded():
    params, files = _scheme_build("    linear_transform: fused\n", ["linear_transform_memory=20GB"])
    h = files["nb_shared.h"]
    assert "2e+10" in h                                               # the 20 GB budget
    assert "// nbc --scheme linear_transform_memory=20GB" in h
    recorded = json.loads(files["nb_params.json"])
    assert recorded["scheme::linear_transform_memory"] == "20 GB"


def test_scheme_override_adds_a_missing_field():
    _, files = _scheme_build("    linear_transform: chained\n", ["linear_transform_batch=4"])
    assert ": 4L;" in files["nb_shared.h"]


def test_scheme_override_refuses_other_fields():
    from xcomp.params import apply_scheme_overrides
    from xcomp.tests.test_codegen import LT_SRC, _lt_scheme
    program = parse(lex(_lt_scheme(LT_SRC, "    linear_transform: chained\n")))
    # the mode changes the rotation keys the circuit must declare: refused
    with pytest.raises(CompileError, match="rotation keys"):
        apply_scheme_overrides(program, ["linear_transform=fused"])
    for bad in (["key_switch_digits=4"], ["depth=12"], ["linear_transform_batch"],
                ["linear_transform_batch=2", "linear_transform_batch=4"], ["linear_transform_memory=lots of"]):
        with pytest.raises(CompileError):
            apply_scheme_overrides(program, bad)


def test_run_time_memory_overrides_are_generated():
    _, fused = _scheme_build("    linear_transform: fused\n", [])
    assert 'std::getenv("NB_LT_MEMORY_GB")' in fused["nb_shared.h"]
    _, chained = _scheme_build("    linear_transform: chained\n", [])
    assert 'std::getenv("NB_LT_BATCH")' in chained["nb_shared.h"]


def test_scheme_override_encode():
    _, files = _scheme_build("    linear_transform: chained\n", ["linear_transform_encode=host"])
    assert '!= "host" : false; }();' in files["nb_shared.h"]
    assert json.loads(files["nb_params.json"])["scheme::linear_transform_encode"] == "host"
