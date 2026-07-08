"""Recursive descent parser for the nb FHE language."""

from __future__ import annotations
from .lexer import Token, TT
from .errors import ParseError, SourceLocation
from . import ast_nodes as ast


# Type constructor keywords that disambiguate from comparison operators
TYPE_CONSTRUCTORS = {TT.ENC, TT.VEC, TT.MAT}

# Primitive type tokens
PRIMITIVE_TYPES = {
    TT.BOOL, TT.U8, TT.U16, TT.U32, TT.U64,
    TT.I8, TT.I16, TT.I32, TT.I64,
    TT.F32, TT.F64, TT.STRING_T, TT.PATH_T,
}


class Parser:
    """Recursive descent parser producing an AST."""

    def __init__(self, tokens: list[Token], known_types: set[str] | None = None):
        self.tokens = tokens
        self.pos = 0
        # Known type names for struct-literal vs block disambiguation
        self.known_types: set[str] = known_types or set()

    # ----- Token navigation -----

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def peek_type(self) -> TT:
        return self.tokens[self.pos].type

    def peek_at(self, offset: int) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]  # EOF

    def loc(self) -> SourceLocation:
        return self.peek().loc

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def expect(self, tt: TT, context: str = "") -> Token:
        tok = self.peek()
        if tok.type != tt:
            ctx = f" in {context}" if context else ""
            raise ParseError(
                f"expected {tt.name}, got {tok.type.name} ({tok.value!r}){ctx}",
                tok.loc,
            )
        return self.advance()

    def match(self, *types: TT) -> Token | None:
        if self.peek_type() in types:
            return self.advance()
        return None

    def at(self, *types: TT) -> bool:
        return self.peek_type() in types

    def expect_gt(self, context: str = ""):
        """Expect a > token, splitting >> into > + > if needed."""
        if self.match(TT.GT):
            return
        if self.at(TT.SHR):
            # Split >> into two >: consume >> and insert a > at current pos
            tok = self.advance()
            gt_tok = Token(TT.GT, ">", tok.loc)
            self.tokens.insert(self.pos, gt_tok)
            return
        self.expect(TT.GT, context)

    # ----- Program -----

    def parse_program(self) -> ast.Program:
        prog = ast.Program(loc=self.loc())
        while not self.at(TT.EOF):
            prog.items.append(self.parse_top_level_item())
        return prog

    def parse_top_level_item(self) -> ast.Node:
        tt = self.peek_type()
        if tt == TT.USE:
            return self.parse_use_decl()
        if tt == TT.CONST:
            return self.parse_const_decl()
        if tt == TT.ENUM:
            return self.parse_enum_decl()
        if tt == TT.STRUCT:
            return self.parse_struct_decl()
        if tt == TT.WIRE:
            return self.parse_wire_decl()
        if tt == TT.SCHEME:
            return self.parse_scheme_decl()
        if tt == TT.REQUIRES:
            return self.parse_requires_decl()
        if tt == TT.DOMAIN:
            return self.parse_domain_decl()
        if tt == TT.EXTERN:
            return self.parse_extern_decl()
        if tt == TT.ANNOTATION or tt == TT.FN:
            return self.parse_fn_decl()
        raise ParseError(
            f"unexpected token {self.peek().type.name} ({self.peek().value!r}) "
            f"at top level",
            self.loc(),
        )

    # ----- Use -----

    def parse_use_decl(self) -> ast.UseDecl:
        loc = self.loc()
        self.expect(TT.USE)
        path = [self.expect(TT.IDENT).value]
        while self.match(TT.COLONCOLON):
            if self.at(TT.STAR):
                self.advance()
                return ast.UseDecl(loc=loc, module_path=path, imported="*")
            path.append(self.expect(TT.IDENT).value)
        # If no ::*, the last element is the imported name
        if len(path) > 1:
            imported = path.pop()
            return ast.UseDecl(loc=loc, module_path=path, imported=imported)
        return ast.UseDecl(loc=loc, module_path=path, imported="*")

    # ----- Extern -----

    def parse_extern_decl(self) -> ast.ExternDecl:
        """Parse: extern <name> from \"<source>\""""
        loc = self.loc()
        self.expect(TT.EXTERN)
        name = self.expect(TT.IDENT).value
        # 'from' is a contextual keyword (not reserved — it's used as a named arg elsewhere)
        tok = self.expect(TT.IDENT)
        if tok.value != "from":
            raise ParseError(f"expected 'from', got '{tok.value}'", tok.loc)
        source = self.expect(TT.STRING).value
        return ast.ExternDecl(loc=loc, name=name, source=source)

    # ----- Const -----

    def parse_const_decl(self) -> ast.ConstDecl:
        loc = self.loc()
        self.expect(TT.CONST)
        name = self.expect(TT.IDENT).value
        self.expect(TT.COLON)
        type_ann = self.parse_type()
        self.expect(TT.EQ)
        value = self.parse_expr()
        return ast.ConstDecl(loc=loc, name=name, type_ann=type_ann, value=value)

    # ----- Enum -----

    def parse_enum_decl(self) -> ast.EnumDecl:
        loc = self.loc()
        self.expect(TT.ENUM)
        name = self.expect(TT.IDENT).value
        self.known_types.add(name)
        self.expect(TT.LBRACE)
        variants = []
        while not self.at(TT.RBRACE):
            variants.append(self.expect(TT.IDENT).value)
            if not self.match(TT.COMMA):
                break
        self.expect(TT.RBRACE)
        return ast.EnumDecl(loc=loc, name=name, variants=variants)

    # ----- Struct / Wire -----

    def parse_struct_fields(self) -> list[ast.FieldDecl]:
        fields = []
        while not self.at(TT.RBRACE):
            floc = self.loc()
            fname = self.expect(TT.IDENT).value
            self.expect(TT.COLON)
            ftype = self.parse_type()
            fields.append(ast.FieldDecl(loc=floc, name=fname, type_ann=ftype))
            if not self.match(TT.COMMA):
                break
        return fields

    def parse_struct_decl(self) -> ast.StructDecl:
        loc = self.loc()
        self.expect(TT.STRUCT)
        name = self.expect(TT.IDENT).value
        self.known_types.add(name)
        self.expect(TT.LBRACE)
        fields = self.parse_struct_fields()
        self.expect(TT.RBRACE)
        return ast.StructDecl(loc=loc, name=name, fields=fields)

    def parse_wire_decl(self) -> ast.WireDecl:
        loc = self.loc()
        self.expect(TT.WIRE)
        name = self.expect(TT.IDENT).value
        self.known_types.add(name)
        self.expect(TT.LBRACE)
        fields = self.parse_struct_fields()
        self.expect(TT.RBRACE)
        return ast.WireDecl(loc=loc, name=name, fields=fields)

    # ----- Scheme -----

    def parse_scheme_decl(self) -> ast.SchemeDecl:
        loc = self.loc()
        self.expect(TT.SCHEME)
        name = self.expect(TT.IDENT).value
        self.expect(TT.LBRACE)
        fields = []
        while not self.at(TT.RBRACE):
            floc = self.loc()
            key = self.expect(TT.IDENT).value
            self.expect(TT.COLON)
            value = self.parse_scheme_value()
            fields.append(ast.SchemeField(loc=floc, key=key, value=value))
        self.expect(TT.RBRACE)
        return ast.SchemeDecl(loc=loc, name=name, fields=fields)

    def parse_scheme_value(self):
        """Parse scheme field value: ident, int, 'auto', int ident, int-ident, ident-ident."""
        if self.at(TT.AUTO):
            self.advance()
            return "auto"
        if self.at(TT.INT):
            val = self.advance().value
            # Check for "128-classic" pattern (INT MINUS IDENT)
            if self.at(TT.MINUS):
                self.advance()
                word2 = self.advance().value
                return f"{val}-{word2}"
            # Check for "42 bits" pattern (INT IDENT) — only if the
            # ident is a known unit, not the start of the next field
            UNITS = {"bits", "bytes", "levels"}
            if self.at(TT.IDENT) and self.peek().value in UNITS:
                unit = self.advance().value
                return f"{val} {unit}"
            return val
        if self.at(TT.IDENT) or self.peek_type() in PRIMITIVE_TYPES or self.at(TT.BOOL):
            word = self.advance().value
            # Check for "uniform-ternary" hyphenated pattern
            if self.at(TT.MINUS):
                self.advance()
                word2 = self.advance().value
                return f"{word}-{word2}"
            return word
        raise ParseError(f"expected scheme value", self.loc())

    # ----- Requires -----

    def parse_requires_decl(self) -> ast.RequiresDecl:
        loc = self.loc()
        self.expect(TT.REQUIRES)
        self.expect(TT.LBRACE)
        caps = self.parse_ident_list()
        self.expect(TT.RBRACE)
        return ast.RequiresDecl(loc=loc, capabilities=caps)

    def parse_ident_list(self) -> list[str]:
        items = [self.expect(TT.IDENT).value]
        while self.match(TT.COMMA):
            if self.at(TT.RBRACE):
                break
            items.append(self.expect(TT.IDENT).value)
        return items

    # ----- Domain -----

    def parse_domain_decl(self) -> ast.DomainDecl:
        loc = self.loc()
        self.expect(TT.DOMAIN)
        name = self.expect(TT.IDENT).value
        self.expect(TT.LBRACE)
        clauses = []
        while not self.at(TT.RBRACE):
            cloc = self.loc()
            kind_tok = self.advance()
            if kind_tok.value not in ("has", "can", "cannot"):
                raise ParseError(f"expected 'has', 'can', or 'cannot'", cloc)
            self.expect(TT.COLON)
            items = self.parse_ident_list()
            clauses.append(ast.DomainClause(loc=cloc, kind=kind_tok.value, items=items))
        self.expect(TT.RBRACE)
        return ast.DomainDecl(loc=loc, name=name, clauses=clauses)

    # ----- Function -----

    def parse_fn_decl(self) -> ast.FnDecl:
        loc = self.loc()
        annotations = []
        while self.at(TT.ANNOTATION):
            annotations.append(self.parse_annotation())

        self.expect(TT.FN)
        name = self.expect(TT.IDENT).value
        self.expect(TT.LPAREN)
        params = []
        while not self.at(TT.RPAREN):
            params.append(self.parse_param())
            if not self.match(TT.COMMA):
                break
        self.expect(TT.RPAREN)

        return_type = None
        io_specs = []
        if self.match(TT.ARROW):
            return_type, io_specs = self.parse_return_spec()

        body = self.parse_block()
        return ast.FnDecl(
            loc=loc, name=name, annotations=annotations,
            params=params, return_type=return_type,
            io_specs=io_specs, body=body,
        )

    def parse_annotation(self) -> ast.Annotation:
        loc = self.loc()
        tok = self.expect(TT.ANNOTATION)
        args = {}
        if self.match(TT.LPAREN):
            pos_idx = 0
            while not self.at(TT.RPAREN):
                # Check for named arg: IDENT COLON value
                if self.at(TT.IDENT) and self.peek_at(1).type == TT.COLON:
                    key = self.advance().value
                    self.advance()  # colon
                    val = self.parse_annotation_value()
                    args[key] = val
                else:
                    # Positional argument
                    val = self.parse_annotation_value()
                    args[f"_pos_{pos_idx}"] = val
                    pos_idx += 1
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.RPAREN)
            # Convenience: @stage("name") -> name = "name"
            if "_pos_0" in args and len(args) == 1:
                args["name"] = args.pop("_pos_0")
        return ast.Annotation(loc=loc, name=tok.value, args=args)

    def parse_annotation_value(self):
        if self.at(TT.STRING):
            return self.advance().value
        if self.at(TT.TRUE):
            self.advance()
            return True
        if self.at(TT.FALSE):
            self.advance()
            return False
        if self.at(TT.LBRACKET):
            self.advance()
            items = []
            while not self.at(TT.RBRACKET):
                items.append(self.expect(TT.STRING).value)
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.RBRACKET)
            return items
        if self.at(TT.IDENT):
            return self.advance().value
        raise ParseError("expected annotation value", self.loc())

    def parse_param(self) -> ast.Param:
        loc = self.loc()
        name = self.expect(TT.IDENT).value
        self.expect(TT.COLON)
        type_ann = self.parse_type()
        default = None
        if self.match(TT.EQ):
            default = self.parse_expr()
        return ast.Param(loc=loc, name=name, type_ann=type_ann, default=default)

    def parse_return_spec(self) -> tuple[ast.TypeExpr | None, list[ast.IoSpec]]:
        """Parse return type and/or IO specs after ->."""
        io_kinds = {"reads", "writes", "reads_plaintext", "writes_plaintext"}

        # Check if this is an IO spec
        if self.at(TT.IDENT) and self.peek().value in io_kinds:
            io_specs = self.parse_io_specs()
            return None, io_specs

        # Otherwise it's a type, possibly followed by IO specs
        ret_type = self.parse_type()

        # Check for trailing comma + io specs (not used in current examples
        # but supported by grammar)
        return ret_type, []

    def parse_io_specs(self) -> list[ast.IoSpec]:
        specs = []
        while self.at(TT.IDENT) and self.peek().value in (
            "reads", "writes", "reads_plaintext", "writes_plaintext"
        ):
            loc = self.loc()
            kind = self.advance().value
            self.expect(TT.LPAREN)
            types = []
            while not self.at(TT.RPAREN):
                types.append(self.parse_io_type())
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.RPAREN)
            specs.append(ast.IoSpec(loc=loc, kind=kind, types=types))
            self.match(TT.COMMA)  # optional comma between specs
        return specs

    def parse_io_type(self) -> ast.IoType:
        loc = self.loc()
        # Could be a type name or a path expression
        # Type name: uppercase IDENT not followed by (
        if (self.at(TT.IDENT)
            and self.peek().value[0].isupper()
            and self.peek_at(1).type != TT.LPAREN):
            name = self.advance().value
            index = None
            # Check for [index]
            if self.match(TT.LBRACKET):
                if self.at(TT.STAR):
                    self.advance()
                    index = ast.Ident(loc=self.loc(), name="*")
                else:
                    index = self.parse_expr()
                self.expect(TT.RBRACKET)
            return ast.IoType(loc=loc, type_name=name, index=index, path_expr=None)
        # Path expression (e.g., iodir(inst) / "file.bin")
        expr = self.parse_expr()
        return ast.IoType(loc=loc, type_name="", path_expr=expr)

    # ----- Types -----

    def parse_type(self) -> ast.TypeExpr:
        loc = self.loc()
        tt = self.peek_type()

        if tt in PRIMITIVE_TYPES:
            name = self.advance().value
            return ast.PrimitiveType(loc=loc, name=name)

        if tt == TT.BOOL:
            self.advance()
            return ast.PrimitiveType(loc=loc, name="bool")

        if tt == TT.ENC:
            self.advance()
            self.expect(TT.LT)
            inner = self.parse_type()
            self.expect_gt()
            return ast.EncType(loc=loc, inner=inner)

        if tt == TT.VEC:
            self.advance()
            self.expect(TT.LT)
            elem = self.parse_type()
            size = None
            if self.match(TT.COMMA):
                size = self.parse_expr()
            self.expect_gt()
            return ast.VecType(loc=loc, elem=elem, size=size)

        if tt == TT.MAT:
            self.advance()
            self.expect(TT.LT)
            elem = self.parse_type()
            self.expect_gt()
            rows = None
            cols = None
            if self.match(TT.LBRACKET):
                rows = self.parse_expr()
                self.expect(TT.COMMA)
                cols = self.parse_expr()
                self.expect(TT.RBRACKET)
            return ast.MatType(loc=loc, elem=elem, rows=rows, cols=cols)

        if tt == TT.FN:
            self.advance()
            self.expect(TT.LPAREN)
            param_types = []
            while not self.at(TT.RPAREN):
                param_types.append(self.parse_type())
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.RPAREN)
            self.expect(TT.ARROW)
            ret = self.parse_type()
            return ast.FnType(loc=loc, param_types=param_types, return_type=ret)

        if tt == TT.LPAREN:
            # Tuple type: (T1, T2, ...)
            self.advance()
            elems = []
            while not self.at(TT.RPAREN):
                elems.append(self.parse_type())
                if not self.match(TT.COMMA):
                    break
            self.expect(TT.RPAREN)
            return ast.TupleType(loc=loc, elements=elems)

        if tt == TT.IDENT:
            name = self.advance().value
            sub = None
            if self.match(TT.DOT):
                sub = self.expect(TT.IDENT).value
            return ast.NamedType(loc=loc, name=name, sub=sub)

        raise ParseError(f"expected type, got {tt.name}", loc)

    # ----- Block -----

    def parse_block(self) -> ast.Block:
        loc = self.loc()
        self.expect(TT.LBRACE)
        stmts = []
        while not self.at(TT.RBRACE):
            stmts.append(self.parse_statement())
        self.expect(TT.RBRACE)
        return ast.Block(loc=loc, stmts=stmts)

    # ----- Statements -----

    def parse_statement(self) -> ast.Node:
        tt = self.peek_type()

        if tt == TT.LET:
            return self.parse_let_stmt()
        if tt == TT.RETURN:
            return self.parse_return_stmt()
        if tt == TT.ASSERT:
            return self.parse_assert_stmt()
        if tt == TT.IF:
            return self.parse_if_stmt()
        if tt == TT.FOR:
            return self.parse_for_stmt()
        if tt == TT.MATCH:
            return self.parse_match_stmt()

        # Expression statement or assignment
        expr = self.parse_expr()
        if self.match(TT.EQ):
            value = self.parse_expr()
            return ast.AssignStmt(loc=expr.loc, target=expr, value=value)
        return ast.ExprStmt(loc=expr.loc, expr=expr)

    def parse_let_stmt(self) -> ast.LetStmt:
        loc = self.loc()
        self.expect(TT.LET)
        # Handle destructuring: let (a, b) = ...
        if self.match(TT.LPAREN):
            names = [self.expect(TT.IDENT).value]
            while self.match(TT.COMMA):
                names.append(self.expect(TT.IDENT).value)
            self.expect(TT.RPAREN)
            self.expect(TT.EQ)
            value = self.parse_expr()
            # Represent as a let with a tuple-like name and preserved originals
            return ast.LetStmt(loc=loc, name="_".join(names), value=value, tuple_names=names)
        name = self.expect(TT.IDENT).value
        type_ann = None
        if self.match(TT.COLON):
            type_ann = self.parse_type()
        self.expect(TT.EQ)
        value = self.parse_expr()
        return ast.LetStmt(loc=loc, name=name, type_ann=type_ann, value=value)

    def parse_return_stmt(self) -> ast.ReturnStmt:
        loc = self.loc()
        self.expect(TT.RETURN)
        value = None
        if not self.at(TT.RBRACE):
            value = self.parse_expr()
        return ast.ReturnStmt(loc=loc, value=value)

    def parse_assert_stmt(self) -> ast.AssertStmt:
        loc = self.loc()
        self.expect(TT.ASSERT)
        cond = self.parse_expr()
        msg = None
        if self.match(TT.COMMA):
            msg = self.expect(TT.STRING).value
        return ast.AssertStmt(loc=loc, condition=cond, message=msg)

    def parse_if_stmt(self) -> ast.IfStmt:
        loc = self.loc()
        self.expect(TT.IF)
        cond = self.parse_expr()
        then_block = self.parse_block()
        else_block = None
        if self.match(TT.ELSE):
            if self.at(TT.IF):
                else_block = self.parse_if_stmt()
            else:
                else_block = self.parse_block()
        return ast.IfStmt(loc=loc, condition=cond, then_block=then_block,
                          else_block=else_block)

    def parse_for_stmt(self) -> ast.ForStmt:
        loc = self.loc()
        self.expect(TT.FOR)
        pattern = self.parse_for_pattern()
        self.expect(TT.IN)
        iterable = self.parse_expr()
        body = self.parse_block()
        return ast.ForStmt(loc=loc, pattern=pattern, iterable=iterable, body=body)

    def parse_for_pattern(self) -> ast.ForPattern:
        loc = self.loc()
        if self.match(TT.LPAREN):
            names = [self.expect(TT.IDENT).value]
            self.expect(TT.COMMA)
            names.append(self.expect(TT.IDENT).value)
            self.expect(TT.RPAREN)
            return ast.ForPattern(loc=loc, names=names)
        name = self.expect(TT.IDENT).value
        return ast.ForPattern(loc=loc, names=[name])

    def parse_match_stmt(self) -> ast.MatchStmt:
        loc = self.loc()
        self.expect(TT.MATCH)
        subject = self.parse_expr()
        self.expect(TT.LBRACE)
        arms = []
        while not self.at(TT.RBRACE):
            arms.append(self.parse_match_arm())
        self.expect(TT.RBRACE)
        return ast.MatchStmt(loc=loc, subject=subject, arms=arms)

    def parse_match_arm(self) -> ast.MatchArm:
        loc = self.loc()
        pattern = self.parse_pattern()
        self.expect(TT.FAT_ARROW)
        if self.at(TT.LBRACE):
            body = self.parse_block()
        elif self.at(TT.RETURN):
            # Allow return statements in match arms without braces
            body = self.parse_return_stmt()
        else:
            body = self.parse_expr()
        self.match(TT.COMMA)
        return ast.MatchArm(loc=loc, pattern=pattern, body=body)

    def parse_pattern(self) -> ast.Pattern:
        loc = self.loc()
        if self.at(TT.INT):
            return ast.LiteralPattern(
                loc=loc, value=ast.IntLiteral(loc=loc, value=self.advance().value)
            )
        if self.at(TT.FLOAT):
            return ast.LiteralPattern(
                loc=loc, value=ast.FloatLiteral(loc=loc, value=self.advance().value)
            )
        if self.at(TT.STRING):
            return ast.LiteralPattern(
                loc=loc, value=ast.StringLiteral(loc=loc, value=self.advance().value)
            )
        if self.at(TT.IDENT):
            name = self.advance().value
            if name == "_":
                return ast.WildcardPattern(loc=loc)
            if self.at(TT.LBRACE):
                self.advance()
                field_patterns = []
                while not self.at(TT.RBRACE):
                    fp_loc = self.loc()
                    fp_name = self.expect(TT.IDENT).value
                    fp_pat = None
                    if self.match(TT.COLON):
                        fp_pat = self.parse_pattern()
                    field_patterns.append(
                        ast.FieldPattern(loc=fp_loc, name=fp_name, pattern=fp_pat)
                    )
                    if not self.match(TT.COMMA):
                        break
                self.expect(TT.RBRACE)
                return ast.StructPattern(loc=loc, type_name=name, fields=field_patterns)
            return ast.IdentPattern(loc=loc, name=name)
        raise ParseError(f"expected pattern", loc)

    # ----- Expressions (precedence climbing) -----

    def parse_expr(self) -> ast.Expr:
        return self.parse_pipe_expr()

    def parse_pipe_expr(self) -> ast.Expr:
        left = self.parse_range_expr()
        while self.match(TT.PIPE_OP):
            # Handle `|> as T` (pipe into cast)
            if self.match(TT.AS):
                target = self.parse_type()
                left = ast.CastExpr(loc=left.loc, expr=left, target_type=target)
            else:
                right = self.parse_range_expr()
                left = ast.PipeExpr(loc=left.loc, left=left, right=right)
        return left

    def parse_range_expr(self) -> ast.Expr:
        left = self.parse_or_expr()
        if self.at(TT.DOTDOT, TT.DOTDOTEQ):
            inclusive = self.peek_type() == TT.DOTDOTEQ
            self.advance()
            right = self.parse_or_expr()
            return ast.RangeExpr(loc=left.loc, start=left, end=right,
                                 inclusive=inclusive)
        return left

    def parse_or_expr(self) -> ast.Expr:
        left = self.parse_and_expr()
        while self.match(TT.OR):
            right = self.parse_and_expr()
            left = ast.BinaryExpr(loc=left.loc, op="||", left=left, right=right)
        return left

    def parse_and_expr(self) -> ast.Expr:
        left = self.parse_eq_expr()
        while self.match(TT.AND):
            right = self.parse_eq_expr()
            left = ast.BinaryExpr(loc=left.loc, op="&&", left=left, right=right)
        return left

    def parse_eq_expr(self) -> ast.Expr:
        left = self.parse_cmp_expr()
        if self.at(TT.EQ_EQ, TT.BANG_EQ, TT.TILDE_EQ):
            op = self.advance().value
            right = self.parse_cmp_expr()
            left = ast.BinaryExpr(loc=left.loc, op=op, left=left, right=right)
        return left

    def parse_cmp_expr(self) -> ast.Expr:
        left = self.parse_shift_expr()
        if self.at(TT.LT, TT.GT, TT.LT_EQ, TT.GT_EQ):
            # Don't consume < if it could be a type parameter (enc<T>)
            op = self.advance().value
            right = self.parse_add_expr()
            left = ast.BinaryExpr(loc=left.loc, op=op, left=left, right=right)
        return left

    def parse_shift_expr(self) -> ast.Expr:
        left = self.parse_add_expr()
        while self.at(TT.SHL, TT.SHR):
            op = self.advance().value
            right = self.parse_add_expr()
            left = ast.BinaryExpr(loc=left.loc, op=op, left=left, right=right)
        return left

    def parse_add_expr(self) -> ast.Expr:
        left = self.parse_mul_expr()
        while self.at(TT.PLUS, TT.MINUS):
            op = self.advance().value
            right = self.parse_mul_expr()
            left = ast.BinaryExpr(loc=left.loc, op=op, left=left, right=right)
        return left

    def parse_mul_expr(self) -> ast.Expr:
        left = self.parse_pow_expr()
        while self.at(TT.STAR, TT.SLASH, TT.PERCENT, TT.STAR_NORELIN):
            op = self.advance().value
            right = self.parse_pow_expr()
            left = ast.BinaryExpr(loc=left.loc, op=op, left=left, right=right)
        return left

    def parse_pow_expr(self) -> ast.Expr:
        base = self.parse_unary_expr()
        if self.match(TT.CARET):
            exp = self.parse_pow_expr()  # right-associative
            return ast.BinaryExpr(loc=base.loc, op="^", left=base, right=exp)
        return base

    def parse_unary_expr(self) -> ast.Expr:
        if self.at(TT.MINUS, TT.BANG):
            loc = self.loc()
            op = self.advance().value
            operand = self.parse_unary_expr()
            return ast.UnaryExpr(loc=loc, op=op, operand=operand)
        return self.parse_cast_expr()

    def parse_cast_expr(self) -> ast.Expr:
        expr = self.parse_postfix_expr()
        if self.match(TT.AS):
            target = self.parse_type()
            return ast.CastExpr(loc=expr.loc, expr=expr, target_type=target)
        return expr

    def _try_turbofish(self) -> list[ast.TypeExpr] | None:
        """Try to parse <Type> turbofish. Returns type args or None (restoring pos)."""
        saved = self.pos
        if not self.match(TT.LT):
            return None
        # Check if this looks like a type start
        tt = self.peek_type()
        if (tt not in PRIMITIVE_TYPES and tt != TT.BOOL and tt != TT.IDENT
                and tt not in TYPE_CONSTRUCTORS):
            self.pos = saved
            return None
        try:
            t = self.parse_type()
            if self.at(TT.GT) or self.at(TT.SHR):
                self.expect_gt()
                if self.at(TT.LPAREN):
                    return [t]
            self.pos = saved
            return None
        except Exception:
            self.pos = saved
            return None

    def parse_postfix_expr(self) -> ast.Expr:
        expr = self.parse_primary_expr()
        while True:
            if self.at(TT.DOT):
                self.advance()
                name = self.expect(TT.IDENT).value
                if self.at(TT.LPAREN):
                    self.advance()
                    args = self.parse_arg_list()
                    self.expect(TT.RPAREN)
                    expr = ast.MethodCall(loc=expr.loc, obj=expr, method=name, args=args)
                else:
                    expr = ast.FieldAccess(loc=expr.loc, obj=expr, field_name=name)
            elif self.at(TT.LBRACKET):
                self.advance()
                idx = self.parse_expr()
                # If parse_expr returned a RangeExpr, treat as slice
                if isinstance(idx, ast.RangeExpr):
                    # Handle multidim slice: matrix[i..j, col]
                    if self.match(TT.COMMA):
                        col = self.parse_expr()
                        self.expect(TT.RBRACKET)
                        sliced = ast.SliceExpr(loc=expr.loc, obj=expr,
                                               start=idx.start, end=idx.end)
                        expr = ast.IndexExpr(loc=expr.loc, obj=sliced, index=col)
                    else:
                        self.expect(TT.RBRACKET)
                        expr = ast.SliceExpr(loc=expr.loc, obj=expr,
                                             start=idx.start, end=idx.end)
                elif self.match(TT.COMMA):
                    # Handle multidim index: matrix[i, j]
                    col = self.parse_expr()
                    self.expect(TT.RBRACKET)
                    expr = ast.IndexExpr(
                        loc=expr.loc,
                        obj=ast.IndexExpr(loc=expr.loc, obj=expr, index=idx),
                        index=col)
                else:
                    self.expect(TT.RBRACKET)
                    expr = ast.IndexExpr(loc=expr.loc, obj=expr, index=idx)
            elif self.at(TT.LT) and isinstance(expr, ast.Ident):
                # Try turbofish: ident<Type>(args)
                type_args = self._try_turbofish()
                if type_args is not None:
                    self.advance()  # (
                    args = self.parse_arg_list()
                    self.expect(TT.RPAREN)
                    expr = ast.CallExpr(loc=expr.loc, func=expr, args=args,
                                        type_args=type_args)
                else:
                    break
            elif self.at(TT.LPAREN):
                # Disambiguate: function call only if expr is callable
                self.advance()
                args = self.parse_arg_list()
                self.expect(TT.RPAREN)
                expr = ast.CallExpr(loc=expr.loc, func=expr, args=args)
            else:
                break
        return expr

    def parse_primary_expr(self) -> ast.Expr:
        loc = self.loc()
        tt = self.peek_type()

        # Operator-as-value: +, -, * used as function references (e.g. reduce(+, xs))
        if tt in (TT.PLUS, TT.MINUS, TT.STAR):
            next_tt = self.peek_at(1).type
            if next_tt in (TT.COMMA, TT.RPAREN):
                op = self.advance().value
                return ast.Ident(loc=loc, name=f"op_{op}")

        if tt == TT.INT:
            return ast.IntLiteral(loc=loc, value=self.advance().value)

        if tt == TT.FLOAT:
            return ast.FloatLiteral(loc=loc, value=self.advance().value)

        if tt == TT.STRING:
            return ast.StringLiteral(loc=loc, value=self.advance().value)

        if tt == TT.TRUE:
            self.advance()
            return ast.BoolLiteral(loc=loc, value=True)

        if tt == TT.FALSE:
            self.advance()
            return ast.BoolLiteral(loc=loc, value=False)

        # Keywords that can be used as identifiers in expression context
        if tt in (TT.SCHEME, TT.WIRE, TT.DOMAIN):
            name = self.advance().value
            return ast.Ident(loc=loc, name=name)

        if tt == TT.PIPE:
            return self.parse_closure()

        if tt == TT.LBRACKET:
            return self.parse_array_literal()

        if tt == TT.LPAREN:
            self.advance()
            # Check for (expr, expr) tuple or (0..n).rev()
            expr = self.parse_expr()
            if self.match(TT.COMMA):
                # Tuple — for now parse as array
                elems = [expr]
                while not self.at(TT.RPAREN):
                    elems.append(self.parse_expr())
                    if not self.match(TT.COMMA):
                        break
                self.expect(TT.RPAREN)
                return ast.ArrayLiteral(loc=loc, elements=elems)
            self.expect(TT.RPAREN)
            return expr  # grouping

        if tt == TT.FOR:
            return self.parse_for_expr()

        if tt == TT.IF:
            return self.parse_if_expr()

        if tt == TT.MATCH:
            return self.parse_match_expr()

        if tt == TT.IDENT:
            name = self.advance().value
            # Check for struct literal: Name { ... }
            # Either name is a known type or we see Name { ident: ... } pattern
            if self.at(TT.LBRACE) and (
                name in self.known_types
                or (name[0].isupper()
                    and self.peek_at(1).type == TT.IDENT
                    and self.peek_at(2).type in (TT.COLON, TT.COMMA, TT.RBRACE))
            ):
                return self.parse_struct_literal(name, loc)
            return ast.Ident(loc=loc, name=name)

        # Numeric range starting with literal: 0..n
        if tt == TT.INT:
            val = self.advance().value
            if self.at(TT.DOTDOT, TT.DOTDOTEQ):
                inclusive = self.peek_type() == TT.DOTDOTEQ
                self.advance()
                end = self.parse_expr()
                return ast.RangeExpr(
                    loc=loc,
                    start=ast.IntLiteral(loc=loc, value=val),
                    end=end,
                    inclusive=inclusive,
                )
            return ast.IntLiteral(loc=loc, value=val)

        raise ParseError(f"expected expression, got {tt.name} ({self.peek().value!r})", loc)

    def parse_closure(self) -> ast.Closure:
        loc = self.loc()
        self.expect(TT.PIPE)
        params = []
        while not self.at(TT.PIPE):
            ploc = self.loc()
            name = self.expect(TT.IDENT).value
            type_ann = None
            if self.match(TT.COLON):
                type_ann = self.parse_type()
            params.append(ast.Param(loc=ploc, name=name, type_ann=type_ann))
            if not self.match(TT.COMMA):
                break
        self.expect(TT.PIPE)
        if self.at(TT.LBRACE):
            body = self.parse_block()
        else:
            body = self.parse_expr()
        return ast.Closure(loc=loc, params=params, body=body)

    def parse_array_literal(self) -> ast.ArrayLiteral:
        loc = self.loc()
        self.expect(TT.LBRACKET)
        elems = []
        while not self.at(TT.RBRACKET):
            elems.append(self.parse_expr())
            if not self.match(TT.COMMA):
                break
        self.expect(TT.RBRACKET)
        return ast.ArrayLiteral(loc=loc, elements=elems)

    def parse_struct_literal(self, name: str, loc: SourceLocation) -> ast.StructLiteral:
        self.expect(TT.LBRACE)
        fields = []
        while not self.at(TT.RBRACE):
            floc = self.loc()
            fname = self.expect(TT.IDENT).value
            value = None
            if self.match(TT.COLON):
                value = self.parse_expr()
            fields.append(ast.FieldInit(loc=floc, name=fname, value=value))
            if not self.match(TT.COMMA):
                break
        self.expect(TT.RBRACE)
        return ast.StructLiteral(loc=loc, type_name=name, fields=fields)

    def parse_for_expr(self) -> ast.ForExpr:
        loc = self.loc()
        self.expect(TT.FOR)
        pattern = self.parse_for_pattern()
        self.expect(TT.IN)
        iterable = self.parse_expr()
        body = self.parse_block()
        return ast.ForExpr(loc=loc, pattern=pattern, iterable=iterable, body=body)

    def parse_if_expr(self) -> ast.IfExpr:
        loc = self.loc()
        self.expect(TT.IF)
        cond = self.parse_expr()
        then_block = self.parse_block()
        self.expect(TT.ELSE)
        if self.at(TT.IF):
            else_block = self.parse_if_expr()
        else:
            else_block = self.parse_block()
        return ast.IfExpr(loc=loc, condition=cond, then_block=then_block,
                          else_block=else_block)

    def parse_match_expr(self) -> ast.MatchExpr:
        loc = self.loc()
        self.expect(TT.MATCH)
        subject = self.parse_expr()
        self.expect(TT.LBRACE)
        arms = []
        while not self.at(TT.RBRACE):
            arms.append(self.parse_match_arm())
        self.expect(TT.RBRACE)
        return ast.MatchExpr(loc=loc, subject=subject, arms=arms)

    def _is_named_arg(self) -> bool:
        """Check if current position is a named argument (name: value)."""
        if self.peek_at(1).type != TT.COLON:
            return False
        # IDENT or contextual keywords used as arg names
        return self.at(TT.IDENT) or self.peek_type() in (
            TT.DOMAIN, TT.SCHEME, TT.WIRE,
        )

    def parse_arg_list(self) -> list[ast.Arg]:
        args = []
        while not self.at(TT.RPAREN):
            loc = self.loc()
            # Check for named argument: name: value
            if self._is_named_arg():
                name = self.advance().value
                self.advance()  # :
                value = self.parse_expr()
                args.append(ast.Arg(loc=loc, name=name, value=value))
            else:
                value = self.parse_expr()
                args.append(ast.Arg(loc=loc, name=None, value=value))
            if not self.match(TT.COMMA):
                break
        return args


def parse(tokens: list[Token], known_types: set[str] | None = None) -> ast.Program:
    """Convenience function to parse a token list into an AST."""
    p = Parser(tokens, known_types)
    return p.parse_program()
