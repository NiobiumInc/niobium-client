"""Tests for the nb lexer."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from xcomp.lexer import lex, TT
from xcomp.errors import LexError


def test_basic_tokens():
    tokens = lex("let x = 42")
    types = [t.type for t in tokens]
    assert types == [TT.LET, TT.IDENT, TT.EQ, TT.INT, TT.EOF]
    assert tokens[1].value == "x"
    assert tokens[3].value == 42


def test_float():
    tokens = lex("3.14")
    assert tokens[0].type == TT.FLOAT
    assert tokens[0].value == 3.14


def test_string():
    tokens = lex('"hello world"')
    assert tokens[0].type == TT.STRING
    assert tokens[0].value == "hello world"


def test_annotation():
    tokens = lex("@server @stage")
    assert tokens[0].type == TT.ANNOTATION
    assert tokens[0].value == "server"
    assert tokens[1].type == TT.ANNOTATION
    assert tokens[1].value == "stage"


def test_keywords():
    tokens = lex("fn let if else for in match return const use struct enum wire")
    kw_types = [t.type for t in tokens if t.type != TT.EOF]
    assert kw_types == [
        TT.FN, TT.LET, TT.IF, TT.ELSE, TT.FOR, TT.IN,
        TT.MATCH, TT.RETURN, TT.CONST, TT.USE, TT.STRUCT,
        TT.ENUM, TT.WIRE,
    ]


def test_type_keywords():
    tokens = lex("enc vec mat u32 f64 bool string path")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.ENC, TT.VEC, TT.MAT, TT.U32, TT.F64,
                     TT.BOOL, TT.STRING_T, TT.PATH_T]


def test_operators():
    tokens = lex("+ - * / % ^ |> || && == != < > <= >= ~= -> => ..")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [
        TT.PLUS, TT.MINUS, TT.STAR, TT.SLASH, TT.PERCENT, TT.CARET,
        TT.PIPE_OP, TT.OR, TT.AND, TT.EQ_EQ, TT.BANG_EQ,
        TT.LT, TT.GT, TT.LT_EQ, TT.GT_EQ, TT.TILDE_EQ,
        TT.ARROW, TT.FAT_ARROW, TT.DOTDOT,
    ]


def test_star_norelin():
    tokens = lex("a *_norelin b")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.IDENT, TT.STAR_NORELIN, TT.IDENT]


def test_delimiters():
    tokens = lex("( ) [ ] { } , : . ::")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [
        TT.LPAREN, TT.RPAREN, TT.LBRACKET, TT.RBRACKET,
        TT.LBRACE, TT.RBRACE, TT.COMMA, TT.COLON, TT.DOT,
        TT.COLONCOLON,
    ]


def test_comments():
    tokens = lex("a // line comment\nb")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.IDENT, TT.IDENT]


def test_block_comments():
    tokens = lex("a /* block */ b")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.IDENT, TT.IDENT]


def test_line_tracking():
    tokens = lex("a\nb\nc")
    assert tokens[0].loc.line == 1
    assert tokens[1].loc.line == 2
    assert tokens[2].loc.line == 3


def test_const_decl():
    tokens = lex("const PAYLOAD_DIM: u32 = 8")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.CONST, TT.IDENT, TT.COLON, TT.U32, TT.EQ, TT.INT]


def test_fn_signature():
    tokens = lex("fn add(a: enc<f64>, b: enc<f64>) -> enc<f64>")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [
        TT.FN, TT.IDENT, TT.LPAREN,
        TT.IDENT, TT.COLON, TT.ENC, TT.LT, TT.F64, TT.GT, TT.COMMA,
        TT.IDENT, TT.COLON, TT.ENC, TT.LT, TT.F64, TT.GT,
        TT.RPAREN, TT.ARROW, TT.ENC, TT.LT, TT.F64, TT.GT,
    ]


def test_pipe_operator():
    tokens = lex("data |> transpose |> batch(n)")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [
        TT.IDENT, TT.PIPE_OP, TT.IDENT, TT.PIPE_OP,
        TT.IDENT, TT.LPAREN, TT.IDENT, TT.RPAREN,
    ]


def test_closure():
    tokens = lex("|x| x + 1")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.PIPE, TT.IDENT, TT.PIPE, TT.IDENT, TT.PLUS, TT.INT]


def test_range():
    tokens = lex("0..n")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.INT, TT.DOTDOT, TT.IDENT]


def test_range_inclusive():
    tokens = lex("1..=8")
    types = [t.type for t in tokens if t.type != TT.EOF]
    assert types == [TT.INT, TT.DOTDOTEQ, TT.INT]


def test_error_unterminated_string():
    try:
        lex('"unterminated')
        assert False, "should have raised"
    except LexError:
        pass


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
