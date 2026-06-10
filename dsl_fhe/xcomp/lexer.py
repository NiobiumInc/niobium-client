"""Lexer for the nb FHE domain-specific language."""

from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass
from errors import SourceLocation, LexError


class TT(Enum):
    """Token types."""
    # Literals
    INT = auto()
    FLOAT = auto()
    STRING = auto()
    IDENT = auto()
    ANNOTATION = auto()  # @name

    # Keywords
    FN = auto()
    LET = auto()
    IF = auto()
    ELSE = auto()
    FOR = auto()
    IN = auto()
    MATCH = auto()
    RETURN = auto()
    CONST = auto()
    USE = auto()
    STRUCT = auto()
    ENUM = auto()
    WIRE = auto()
    SCHEME = auto()
    REQUIRES = auto()
    DOMAIN = auto()
    ASSERT = auto()
    TRUE = auto()
    FALSE = auto()
    AS = auto()
    HAS = auto()
    CAN = auto()
    CANNOT = auto()
    EXTERN = auto()

    # Type keywords
    BOOL = auto()
    U8 = auto()
    U16 = auto()
    U32 = auto()
    U64 = auto()
    I8 = auto()
    I16 = auto()
    I32 = auto()
    I64 = auto()
    F32 = auto()
    F64 = auto()
    STRING_T = auto()   # "string" as type
    PATH_T = auto()     # "path" as type
    ENC = auto()
    VEC = auto()
    MAT = auto()
    AUTO = auto()

    # Operators
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    PERCENT = auto()
    CARET = auto()
    STAR_NORELIN = auto()   # *_norelin
    PIPE_OP = auto()        # |>
    PIPE = auto()           # | (for closures)
    OR = auto()             # ||
    AND = auto()            # &&
    BANG = auto()           # !
    EQ_EQ = auto()
    BANG_EQ = auto()
    LT = auto()
    GT = auto()
    LT_EQ = auto()
    GT_EQ = auto()
    SHL = auto()     # <<
    SHR = auto()     # >>
    TILDE_EQ = auto()      # ~=
    EQ = auto()
    ARROW = auto()          # ->
    FAT_ARROW = auto()      # =>
    COLONCOLON = auto()     # ::

    # Delimiters
    LPAREN = auto()
    RPAREN = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    LBRACE = auto()
    RBRACE = auto()
    COMMA = auto()
    COLON = auto()
    DOT = auto()
    DOTDOT = auto()         # ..
    DOTDOTEQ = auto()       # ..=

    # Special
    EOF = auto()
    NEWLINE = auto()  # only used internally, not emitted


KEYWORDS: dict[str, TT] = {
    "fn": TT.FN,
    "let": TT.LET,
    "if": TT.IF,
    "else": TT.ELSE,
    "for": TT.FOR,
    "in": TT.IN,
    "match": TT.MATCH,
    "return": TT.RETURN,
    "const": TT.CONST,
    "use": TT.USE,
    "struct": TT.STRUCT,
    "enum": TT.ENUM,
    "wire": TT.WIRE,
    "scheme": TT.SCHEME,
    "requires": TT.REQUIRES,
    "domain": TT.DOMAIN,
    "assert": TT.ASSERT,
    "true": TT.TRUE,
    "false": TT.FALSE,
    "as": TT.AS,
    "has": TT.HAS,
    "can": TT.CAN,
    "cannot": TT.CANNOT,
    "extern": TT.EXTERN,
    "bool": TT.BOOL,
    "u8": TT.U8,
    "u16": TT.U16,
    "u32": TT.U32,
    "u64": TT.U64,
    "i8": TT.I8,
    "i16": TT.I16,
    "i32": TT.I32,
    "i64": TT.I64,
    "f32": TT.F32,
    "f64": TT.F64,
    "string": TT.STRING_T,
    "path": TT.PATH_T,
    "enc": TT.ENC,
    "vec": TT.VEC,
    "mat": TT.MAT,
    "auto": TT.AUTO,
}


@dataclass
class Token:
    type: TT
    value: str | int | float
    loc: SourceLocation

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, {self.loc})"


class Lexer:
    """Tokenizer for nb source files."""

    def __init__(self, source: str, filename: str = "<stdin>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: list[Token] = []

    def loc(self) -> SourceLocation:
        return SourceLocation(self.filename, self.line, self.col)

    def peek(self) -> str:
        if self.pos >= len(self.source):
            return "\0"
        return self.source[self.pos]

    def peek_at(self, offset: int) -> str:
        p = self.pos + offset
        if p >= len(self.source):
            return "\0"
        return self.source[p]

    def advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def skip_whitespace_and_comments(self):
        while self.pos < len(self.source):
            ch = self.peek()
            # Whitespace
            if ch in " \t\r\n":
                self.advance()
                continue
            # Line comment
            if ch == "/" and self.peek_at(1) == "/":
                while self.pos < len(self.source) and self.peek() != "\n":
                    self.advance()
                continue
            # Block comment
            if ch == "/" and self.peek_at(1) == "*":
                start_loc = self.loc()
                self.advance()  # /
                self.advance()  # *
                depth = 1
                while self.pos < len(self.source) and depth > 0:
                    if self.peek() == "/" and self.peek_at(1) == "*":
                        depth += 1
                        self.advance()
                        self.advance()
                    elif self.peek() == "*" and self.peek_at(1) == "/":
                        depth -= 1
                        self.advance()
                        self.advance()
                    else:
                        self.advance()
                if depth > 0:
                    raise LexError("unterminated block comment", start_loc)
                continue
            break

    def read_string(self) -> str:
        start_loc = self.loc()
        self.advance()  # opening "
        chars = []
        while self.pos < len(self.source):
            ch = self.peek()
            if ch == '"':
                self.advance()
                return "".join(chars)
            if ch == "\\":
                self.advance()
                esc = self.advance()
                if esc == "n":
                    chars.append("\n")
                elif esc == "t":
                    chars.append("\t")
                elif esc == "\\":
                    chars.append("\\")
                elif esc == '"':
                    chars.append('"')
                else:
                    chars.append(esc)
            elif ch == "\n":
                raise LexError("unterminated string literal", start_loc)
            else:
                chars.append(self.advance())
        raise LexError("unterminated string literal", start_loc)

    def read_number(self) -> Token:
        start_loc = self.loc()
        chars = []
        while self.pos < len(self.source) and (self.peek().isdigit() or self.peek() == "_"):
            ch = self.advance()
            if ch != "_":
                chars.append(ch)

        # Check for float
        if self.peek() == "." and self.peek_at(1) != ".":
            chars.append(self.advance())  # .
            while self.pos < len(self.source) and (self.peek().isdigit() or self.peek() == "_"):
                ch = self.advance()
                if ch != "_":
                    chars.append(ch)
            return Token(TT.FLOAT, float("".join(chars)), start_loc)

        return Token(TT.INT, int("".join(chars)), start_loc)

    def read_ident_or_keyword(self) -> Token:
        start_loc = self.loc()
        chars = []
        while self.pos < len(self.source) and (self.peek().isalnum() or self.peek() == "_"):
            chars.append(self.advance())
        word = "".join(chars)

        # Check for *_norelin: if word is part of "*_norelin" after a *
        # This is handled in the main lex loop for * instead

        tt = KEYWORDS.get(word, TT.IDENT)
        if tt == TT.TRUE:
            return Token(tt, True, start_loc)
        if tt == TT.FALSE:
            return Token(tt, False, start_loc)
        return Token(tt, word, start_loc)

    def lex_all(self) -> list[Token]:
        """Tokenize the entire source and return a token list."""
        while True:
            self.skip_whitespace_and_comments()
            if self.pos >= len(self.source):
                self.tokens.append(Token(TT.EOF, "", self.loc()))
                break

            start_loc = self.loc()
            ch = self.peek()

            # Numbers
            if ch.isdigit():
                self.tokens.append(self.read_number())
                continue

            # Identifiers and keywords
            if ch.isalpha() or ch == "_":
                self.tokens.append(self.read_ident_or_keyword())
                continue

            # Strings
            if ch == '"':
                val = self.read_string()
                self.tokens.append(Token(TT.STRING, val, start_loc))
                continue

            # Annotation @name
            if ch == "@":
                self.advance()
                if self.pos < len(self.source) and (self.peek().isalpha() or self.peek() == "_"):
                    name_chars = []
                    while self.pos < len(self.source) and (self.peek().isalnum() or self.peek() == "_"):
                        name_chars.append(self.advance())
                    self.tokens.append(Token(TT.ANNOTATION, "".join(name_chars), start_loc))
                else:
                    raise LexError("expected annotation name after '@'", start_loc)
                continue

            # Multi-char operators
            self.advance()  # consume ch

            if ch == "|":
                if self.peek() == ">":
                    self.advance()
                    self.tokens.append(Token(TT.PIPE_OP, "|>", start_loc))
                elif self.peek() == "|":
                    self.advance()
                    self.tokens.append(Token(TT.OR, "||", start_loc))
                else:
                    self.tokens.append(Token(TT.PIPE, "|", start_loc))
                continue

            if ch == "&" and self.peek() == "&":
                self.advance()
                self.tokens.append(Token(TT.AND, "&&", start_loc))
                continue

            if ch == "=":
                if self.peek() == "=":
                    self.advance()
                    self.tokens.append(Token(TT.EQ_EQ, "==", start_loc))
                elif self.peek() == ">":
                    self.advance()
                    self.tokens.append(Token(TT.FAT_ARROW, "=>", start_loc))
                else:
                    self.tokens.append(Token(TT.EQ, "=", start_loc))
                continue

            if ch == "!":
                if self.peek() == "=":
                    self.advance()
                    self.tokens.append(Token(TT.BANG_EQ, "!=", start_loc))
                else:
                    self.tokens.append(Token(TT.BANG, "!", start_loc))
                continue

            if ch == "<":
                if self.peek() == "=":
                    self.advance()
                    self.tokens.append(Token(TT.LT_EQ, "<=", start_loc))
                elif self.peek() == "<":
                    self.advance()
                    self.tokens.append(Token(TT.SHL, "<<", start_loc))
                else:
                    self.tokens.append(Token(TT.LT, "<", start_loc))
                continue

            if ch == ">":
                if self.peek() == "=":
                    self.advance()
                    self.tokens.append(Token(TT.GT_EQ, ">=", start_loc))
                elif self.peek() == ">":
                    self.advance()
                    self.tokens.append(Token(TT.SHR, ">>", start_loc))
                else:
                    self.tokens.append(Token(TT.GT, ">", start_loc))
                continue

            if ch == "~":
                if self.peek() == "=":
                    self.advance()
                    self.tokens.append(Token(TT.TILDE_EQ, "~=", start_loc))
                else:
                    raise LexError(f"unexpected character '~'", start_loc)
                continue

            if ch == "-":
                if self.peek() == ">":
                    self.advance()
                    self.tokens.append(Token(TT.ARROW, "->", start_loc))
                else:
                    self.tokens.append(Token(TT.MINUS, "-", start_loc))
                continue

            if ch == ".":
                if self.peek() == ".":
                    self.advance()
                    if self.peek() == "=":
                        self.advance()
                        self.tokens.append(Token(TT.DOTDOTEQ, "..=", start_loc))
                    else:
                        self.tokens.append(Token(TT.DOTDOT, "..", start_loc))
                else:
                    self.tokens.append(Token(TT.DOT, ".", start_loc))
                continue

            if ch == ":":
                if self.peek() == ":":
                    self.advance()
                    self.tokens.append(Token(TT.COLONCOLON, "::", start_loc))
                else:
                    self.tokens.append(Token(TT.COLON, ":", start_loc))
                continue

            if ch == "*":
                # Check for *_norelin
                if self.peek() == "_":
                    saved_pos = self.pos
                    saved_line = self.line
                    saved_col = self.col
                    # Try to read _norelin
                    rest = self.source[self.pos:self.pos + 8]
                    if rest == "_norelin":
                        for _ in range(8):
                            self.advance()
                        self.tokens.append(Token(TT.STAR_NORELIN, "*_norelin", start_loc))
                        continue
                self.tokens.append(Token(TT.STAR, "*", start_loc))
                continue

            # Single-char tokens
            simple = {
                "+": TT.PLUS,
                "/": TT.SLASH,
                "%": TT.PERCENT,
                "^": TT.CARET,
                "(": TT.LPAREN,
                ")": TT.RPAREN,
                "[": TT.LBRACKET,
                "]": TT.RBRACKET,
                "{": TT.LBRACE,
                "}": TT.RBRACE,
                ",": TT.COMMA,
            }
            if ch in simple:
                self.tokens.append(Token(simple[ch], ch, start_loc))
                continue

            raise LexError(f"unexpected character {ch!r}", start_loc)

        return self.tokens


def lex(source: str, filename: str = "<stdin>") -> list[Token]:
    """Convenience function to tokenize source code."""
    return Lexer(source, filename).lex_all()
