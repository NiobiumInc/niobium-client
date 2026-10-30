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
