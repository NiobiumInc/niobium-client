# nb Language Grammar

## Formal Syntax Specification

This document defines the complete grammar for the `nb` FHE domain-specific language
using Extended Backus-Naur Form (EBNF). The grammar is LL(1)-friendly with a few
exceptions noted below.

---

## 1. Notation

```
=           definition
|           alternation
( ... )     grouping
[ ... ]     optional (0 or 1)
{ ... }     repetition (0 or more)
' ... '     terminal string (keyword or punctuation)
< ... >     terminal class (from lexer)
(* ... *)   comment
```

---

## 2. Lexical Grammar

### 2.1 Tokens

```ebnf
(* Identifiers and literals *)
IDENT       = LETTER { LETTER | DIGIT | '_' } ;
INT_LIT     = DIGIT { DIGIT } ;
FLOAT_LIT   = DIGIT { DIGIT } '.' DIGIT { DIGIT } ;
STRING_LIT  = '"' { CHAR - '"' | '\\"' } '"' ;
LETTER      = 'a'..'z' | 'A'..'Z' | '_' ;
DIGIT       = '0'..'9' ;
CHAR        = (* any Unicode character *) ;

(* Keywords — reserved, cannot be used as identifiers *)
keyword     = 'fn' | 'let' | 'if' | 'else' | 'for' | 'in' | 'match'
            | 'return' | 'const' | 'use' | 'struct' | 'enum' | 'wire'
            | 'scheme' | 'requires' | 'domain' | 'assert' | 'true' | 'false'
            | 'bool' | 'u8' | 'u16' | 'u32' | 'u64' | 'i8' | 'i16'
            | 'i32' | 'i64' | 'f32' | 'f64' | 'string' | 'path'
            | 'enc' | 'vec' | 'mat' | 'as' | 'extern' ;

(* Annotation names — prefixed with @ *)
ANNOTATION  = '@' IDENT ;

(* Comments *)
line_comment  = '//' { CHAR - NEWLINE } NEWLINE ;
block_comment = '/*' { CHAR } '*/' ;
```

### 2.2 Operators and Punctuation

```ebnf
(* Arithmetic and comparison *)
PLUS        = '+' ;
MINUS       = '-' ;
STAR        = '*' ;
SLASH       = '/' ;
PERCENT     = '%' ;
CARET       = '^' ;
EQ_EQ       = '==' ;
BANG_EQ     = '!=' ;
LT          = '<' ;
GT          = '>' ;
LT_EQ       = '<=' ;
GT_EQ       = '>=' ;
TILDE_EQ    = '~=' ;       (* approximate equality for CKKS *)

(* Assignment and binding *)
EQ          = '=' ;
ARROW       = '->' ;
FAT_ARROW   = '=>' ;
PIPE        = '|>' ;
STAR_NORELIN = '*_norelin' ; (* single token: multiply without relinearization *)

(* Delimiters *)
LPAREN      = '(' ;
RPAREN      = ')' ;
LBRACKET    = '[' ;
RBRACKET    = ']' ;
LBRACE      = '{' ;
RBRACE      = '}' ;
COMMA       = ',' ;
COLON       = ':' ;
SEMICOLON   = ';' ;     (* optional statement terminator *)
DOT         = '.' ;
DOTDOT      = '..' ;
DOTDOTEQ    = '..=' ;
```

---

## 3. Syntactic Grammar

### 3.1 Program Structure

```ebnf
program         = { top_level_item } EOF ;

top_level_item  = use_decl
                | const_decl
                | enum_decl
                | struct_decl
                | wire_decl
                | scheme_decl
                | requires_decl
                | domain_decl
                | extern_decl
                | fn_decl ;
```

### 3.2 Declarations

```ebnf
(* Use declarations *)
use_decl        = 'use' module_path '::' ( IDENT | '*' ) ;
module_path     = IDENT { '::' IDENT } ;

(* Constants *)
const_decl      = 'const' IDENT ':' type '=' expr ;

(* Enumerations *)
enum_decl       = 'enum' IDENT '{' enum_variants '}' ;
enum_variants   = IDENT { ',' IDENT } [ ',' ] ;

(* Structures *)
struct_decl     = 'struct' IDENT '{' struct_fields '}' ;
struct_fields   = struct_field { ',' struct_field } [ ',' ] ;
struct_field    = IDENT ':' type ;

(* Wire types — serializable types for cross-domain communication *)
wire_decl       = 'wire' IDENT '{' struct_fields '}' ;

(* Scheme configuration *)
scheme_decl     = 'scheme' IDENT '{' { scheme_field } '}' ;
scheme_field    = IDENT ':' scheme_value ;
scheme_value    = IDENT                         (* enum-like: flexible_auto *)
                | IDENT '-' IDENT               (* hyphenated: 128-classic *)
                | INT_LIT IDENT                 (* with unit: 42 bits *)
                | INT_LIT                       (* bare number *)
                | 'auto' ;

(* Requires declaration — operations needed for key generation *)
requires_decl   = 'requires' '{' ident_list '}' ;
ident_list      = IDENT { ',' IDENT } [ ',' ] ;

(* Domain declaration — trust boundary specification *)
domain_decl     = 'domain' IDENT '{'
                      { domain_clause }
                  '}' ;
domain_clause   = ( 'has' | 'can' | 'cannot' ) ':' ident_list ;

(* External module declaration — include and link external C++ *)
extern_decl     = 'extern' IDENT 'from' STRING_LIT ;
                  (* Note: 'from' is a contextual keyword (parsed as IDENT) *)
```

### 3.3 Function Declarations

```ebnf
fn_decl         = { annotation } 'fn' IDENT
                  '(' [ param_list ] ')'
                  [ '->' return_spec ]
                  [ where_clause ]
                  block ;

annotation      = ANNOTATION [ '(' annotation_args ')' ] ;
annotation_args = annotation_arg { ',' annotation_arg } ;
annotation_arg  = IDENT ':' annotation_value ;
annotation_value = STRING_LIT
                 | '[' string_list ']'
                 | IDENT
                 | 'true' | 'false' ;
string_list     = STRING_LIT { ',' STRING_LIT } ;

param_list      = param { ',' param } [ ',' ] ;
param           = IDENT ':' type [ '=' expr ] ;   (* default values allowed *)

return_spec     = type                             (* simple return type *)
                | io_spec { ',' io_spec } ;        (* with reads/writes *)
io_spec         = ( 'reads' | 'writes' | 'reads_plaintext' | 'writes_plaintext' )
                  '(' type_or_path { ',' type_or_path } ')' ;
type_or_path    = type [ '[' expr ']' ]            (* indexed wire type *)
                | expr ;                           (* path expression *)

where_clause    = 'where' constraint { ',' constraint } ;
constraint      = IDENT ':' type_bound ;
type_bound      = IDENT { '+' IDENT } ;
```

### 3.4 Types

```ebnf
type            = primitive_type
                | enc_type
                | vec_type
                | mat_type
                | tuple_type
                | named_type
                | fn_type ;

primitive_type  = 'bool'
                | 'u8' | 'u16' | 'u32' | 'u64'
                | 'i8' | 'i16' | 'i32' | 'i64'
                | 'f32' | 'f64'
                | 'string'
                | 'path' ;

enc_type        = 'enc' '<' type '>' ;            (* encrypted type *)

vec_type        = 'vec' '<' type [ ',' expr ] '>' ;  (* optionally sized *)

mat_type        = 'mat' '<' type '>'
                  [ '[' expr ',' expr ']' ] ;      (* optionally sized *)

tuple_type      = '(' type ',' type { ',' type } ')' ;  (* 2+ element tuple *)

named_type      = IDENT [ '.' IDENT ] ;            (* e.g., EncryptedDB.Batch *)

fn_type         = 'fn' '(' [ type_list ] ')' '->' type ;
type_list       = type { ',' type } ;
```

### 3.5 Statements

```ebnf
block           = '{' { statement } '}' ;

statement       = let_stmt
                | assign_stmt
                | return_stmt
                | assert_stmt
                | if_stmt
                | for_stmt
                | match_stmt
                | expr_stmt ;

let_stmt        = 'let' IDENT [ ':' type ] '=' expr ;

assign_stmt     = place_expr '=' expr ;

return_stmt     = 'return' [ expr ] ;

assert_stmt     = 'assert' expr [ ',' STRING_LIT ] ;

expr_stmt       = expr ;
```

### 3.6 Control Flow

```ebnf
if_stmt         = 'if' expr block [ 'else' ( if_stmt | block ) ] ;

for_stmt        = 'for' for_pattern 'in' expr block ;
for_pattern     = IDENT                            (* simple: for x in ... *)
                | '(' IDENT ',' IDENT ')' ;        (* destructured: for (i, x) in ... *)

match_stmt      = 'match' expr '{'
                      { match_arm }
                  '}' ;
match_arm       = pattern '=>' ( expr | block ) [ ',' ] ;
pattern         = IDENT                            (* variant or binding *)
                | '_'                              (* wildcard *)
                | INT_LIT | FLOAT_LIT | STRING_LIT (* literal *)
                | IDENT '{' field_patterns '}' ;   (* struct pattern *)
field_patterns  = field_pattern { ',' field_pattern } ;
field_pattern   = IDENT [ ':' pattern ] ;
```

### 3.7 Expressions

Precedence (lowest to highest):

| Level | Operators          | Associativity | Description                  |
|-------|--------------------|---------------|------------------------------|
| 1     | `\|>`              | left          | pipe                         |
| 2     | `\|\|`             | left          | logical or                   |
| 3     | `&&`               | left          | logical and                  |
| 4     | `==` `!=` `~=`     | none          | equality / approx equality   |
| 5     | `<` `>` `<=` `>=`  | none          | comparison                   |
| 6     | `+` `-`            | left          | additive                     |
| 7     | `*` `/` `%` `*_norelin` | left     | multiplicative               |
| 8     | `^`                | right         | exponentiation               |
| 9     | `-` `!`            | prefix        | unary negation / logical not |
| 10    | `.` `[` `(`        | left          | access, index, call          |
| 11    | `as`               | left          | type cast                    |

```ebnf
expr            = pipe_expr ;

pipe_expr       = or_expr { '|>' or_expr } ;

or_expr         = and_expr { '||' and_expr } ;

and_expr        = eq_expr { '&&' eq_expr } ;

eq_expr         = cmp_expr [ ( '==' | '!=' | '~=' ) cmp_expr ] ;

cmp_expr        = add_expr [ ( '<' | '>' | '<=' | '>=' ) add_expr ] ;

add_expr        = mul_expr { ( '+' | '-' ) mul_expr } ;

mul_expr        = pow_expr { ( '*' | '/' | '%' | '*_norelin' ) pow_expr } ;

pow_expr        = unary_expr [ '^' pow_expr ] ;    (* right-associative *)

unary_expr      = ( '-' | '!' ) unary_expr
                | cast_expr ;

cast_expr       = postfix_expr [ 'as' type ] ;

postfix_expr    = primary_expr { postfix_op } ;
postfix_op      = '.' IDENT                        (* field access *)
                | '.' IDENT '(' [ arg_list ] ')'   (* method call *)
                | '[' expr ']'                      (* index *)
                | '[' expr '..' expr ']'            (* slice *)
                | '(' [ arg_list ] ')' ;            (* function call *)

primary_expr    = INT_LIT
                | FLOAT_LIT
                | STRING_LIT
                | 'true' | 'false'
                | IDENT
                | grouped_expr
                | array_literal
                | struct_literal
                | closure
                | for_expr
                | if_expr
                | match_expr
                | range_expr ;

grouped_expr    = '(' expr ')' ;

(* Tuples use the same syntax as grouping but with commas *)
(* Single-element parens = grouping, multi-element = tuple *)
(* tuple_expr = '(' expr ',' expr { ',' expr } ')' ; *)

array_literal   = '[' [ expr { ',' expr } [ ',' ] ] ']' ;

struct_literal  = IDENT '{' field_inits '}' ;
field_inits     = field_init { ',' field_init } [ ',' ] ;
field_init      = IDENT [ ':' expr ] ;             (* shorthand: { size } = { size: size } *)

closure         = '|' [ param_list ] '|' expr
                | '|' [ param_list ] '|' block ;

for_expr        = 'for' for_pattern 'in' expr block ;
                  (* for-expressions produce a value: vec of block results *)

if_expr         = 'if' expr block 'else' ( if_expr | block ) ;
                  (* if-expressions must have else branch *)

match_expr      = 'match' expr '{' { match_arm } '}' ;

range_expr      = expr '..' expr                   (* exclusive *)
                | expr '..=' expr                  (* inclusive *)
                | '(' expr '..' expr ')' '.' IDENT '(' ')' ;  (* (0..n).rev() *)
```

### 3.8 Argument Lists

```ebnf
arg_list        = arg { ',' arg } [ ',' ] ;
arg             = [ IDENT ':' ] expr ;             (* named or positional *)
```

### 3.9 Place Expressions (assignable)

```ebnf
place_expr      = IDENT { place_suffix } ;
place_suffix    = '.' IDENT
                | '[' expr ']' ;
```

---

## 4. Semantic Rules (Informal)

These are not part of the grammar but are enforced by the semantic analyzer.

### 4.1 Domain Enforcement

```
RULE domain-separation:
  If a function is annotated @server, then within its body and all
  transitively called functions:
    - References to SecretKey type are forbidden
    - Calls to decrypt() are forbidden
    - Calls to save_secret_key() are forbidden
    - Calls to load_secret_key() are forbidden

RULE wire-type-safety:
  Wire types may only contain:
    - Primitive types
    - enc<T> types
    - vec<T> and mat<T> types
    - Other wire types
  Wire types may NOT contain:
    - SecretKey
    - fn types (closures)
    - mutable references
```

### 4.2 Type Rules

```
RULE enc-propagation:
  If either operand of +, -, * is enc<T>, the result is enc<T>.
  enc<T> op enc<T>  ->  enc<T>
  enc<T> op T       ->  enc<T>     (cipher-plain, cheaper)
  T      op enc<T>  ->  enc<T>     (plain-cipher, cheaper)

RULE norelin-typing:
  The *_norelin operator is only valid between enc<T> operands.
  The result type is enc<T> but carries a "needs-relin" flag.
  Adding a needs-relin value to another value is allowed
  (the flag propagates). Only relin() clears the flag.

RULE depth-tracking:
  Each enc<T> value carries a multiplicative depth counter.
  * and *_norelin increment depth by 1.
  chebyshev() increments depth by ceil(log2(degree)).
  bootstrap() resets depth to 0.
  If depth exceeds scheme.depth, emit a compile error unless
  scheme.bootstrap == auto, in which case insert bootstrap.

RULE cast-rules:
  'as' casts are permitted between:
    - Numeric types (u32 -> i64, f64 -> i16 with rounding, etc.)
    - enc<T> types are NOT castable (no implicit re-encryption)
```

### 4.3 Stage Rules

```
RULE stage-compilation:
  Each function annotated with @stage("name") compiles to a
  separate binary named after the stage. The binary accepts
  command-line arguments matching the function's parameters:
    - Instance/InstanceSize parameters -> positional int argument
    - bool parameters -> --flag_name CLI flag
    - String parameters -> --param_name value

RULE stage-io:
  Functions with reads(...) / writes(...) return specs generate
  serialization code at stage boundaries. The reads() types are
  deserialized from the io directory at entry; writes() types
  are serialized to the io directory at exit.
```

### 4.4 Hardware Annotation Rules

```
RULE hardware-instrumentation:
  A function annotated with @hardware(cache_key: [...]) generates Niobium client
  (libnbfhetch) record/replay instrumentation:
    1. init() + enable_auto_tagging() + cache parameters from the cache_key list
    2. is_cache_valid() check
    3. Recording path: start() -> body -> probe() -> stop()
    4. Replay path (always, local FHETCH simulator): replay() -> result()
  Inputs, evaluation keys, and the crypto context are tagged automatically by the
  instrumented-OpenFHE deserialize hooks (cooperative auto-tagging) — there are no
  generated tag_input() calls. probe() calls are generated from writes() parameters.

RULE hardware-domain:
  @hardware may only appear on @server functions.
  It is a compile error to annotate a @client function with @hardware.
```

---

## 5. Disambiguation Rules

### 5.1 Expression vs. Statement

A `for`, `if`, or `match` at the start of a statement is parsed as a statement.
When these appear as the right-hand side of `let ... =` or inside an expression
context (e.g., argument to a function), they are parsed as expressions.

```
let x = if cond { a } else { b }    (* if-expression *)
if cond { do_something() }           (* if-statement, no else required *)
```

### 5.2 Struct Literal vs. Block

After an identifier, `{` is ambiguous between a struct literal and a block.
Resolution: if the identifier is a known type name, parse as struct literal.
Otherwise, parse as a block. This requires the parser to maintain a set of
known type names.

### 5.3 Generics vs. Comparison

`enc<T>` is ambiguous with `enc < T > ...` (comparison). Resolution: `enc`,
`vec`, and `mat` are reserved type constructors. When followed by `<`, always
parse as a generic type, not a comparison.

### 5.4 Range in Array Index

`a[1..3]` is a slice, `a[1]` is an index. The parser checks for `..` after
the first expression inside brackets.

---

## 6. Example Parse Trees

### 6.1 Simple function

```nb
@server @stage("compute")
fn add_encrypted(a: enc<f64>, b: enc<f64>) -> enc<f64> {
    return a + b
}
```

Parses as:
```
FnDecl
  annotations: [@server, @stage("compute")]
  name: "add_encrypted"
  params: [(a, enc<f64>), (b, enc<f64>)]
  return_type: enc<f64>
  body: Block
    ReturnStmt
      BinaryExpr(+)
        Ident("a")
        Ident("b")
```

### 6.2 Pipe expression

```nb
let result = data |> transpose |> batch(n_slots(inst)) |> scale(0.5)
```

Parses as (left-associative):
```
LetStmt
  name: "result"
  value: PipeExpr
    PipeExpr
      PipeExpr
        Ident("data")
        Ident("transpose")
      Call("batch", [Call("n_slots", [Ident("inst")])])
    Call("scale", [Float(0.5)])
```

Pipe semantics: `a |> f` desugars to `f(a)`, `a |> f(b)` desugars to `f(a, b)`.

### 6.3 For-expression

```nb
let encrypted = for row in batch {
    encrypt(pk, row, level: 5)
}
```

Parses as:
```
LetStmt
  name: "encrypted"
  value: ForExpr
    pattern: "row"
    iterable: Ident("batch")
    body: Block
      Call("encrypt", [Ident("pk"), Ident("row"), named("level", Int(5))])
  (* result type: vec<enc<...>> — the vec of each iteration's result *)
```

### 6.4 Wire type and domain interaction

```nb
wire CryptoParams {
    context: CryptoContext,
    public_key: PublicKey,
}

@server
fn compute(params: CryptoParams) -> enc<f64> {
    let sk = load_secret_key()    // ERROR: load_secret_key not in server domain
    // ...
}
```

The semantic analyzer rejects `load_secret_key()` because it is in the `client`
domain's `can` set but not in `server`'s.

---

## 7. Built-in Functions with Special Syntax

These functions use standard call syntax (`IDENT '(' args ')'`) but have special
semantics in the codegen that are worth noting:

### 7.1 Type-Parameterized Functions

```ebnf
(* These accept an optional type argument using generic syntax *)
type_param_call = ( 'vec_zeros' | 'mat_zeros' | 'load_matrix' | 'load_vec' )
                  [ '<' type '>' ] '(' arg_list ')' ;
```

Examples: `vec_zeros<enc<vec<f64>>>(n)`, `load_matrix<f32>(path, dim)`.

### 7.2 Named-Argument Functions

Several built-ins use named arguments:

```nb
encrypt(pk, data, level: 5)              // named: level
encrypt(pk, data, slots: 1)              // named: slots
chebyshev(fn, ct, domain: [-1, 1], degree: 59)  // named: domain, degree
running_sums(cts, stride: n_cols, depth: 3)      // named: stride, depth
slot_replicator(degrees, input_reps: n)  // named: input_reps
slot_mask(n_slots, n_cols, row_range: a..b)      // named: row_range (range expr)
load(Type, from: path)                   // named: from
save(WireType{...}, to: path)            // named: to
scheme.override(security: not_set, ring_dim: 2048)  // named: all
```

### 7.3 Type Cast Syntax

```nb
int(expr)        // cast to integer — compiles to int(expr) in C++
expr |> as i16   // pipe-style cast
expr as i16      // inline cast
```

### 7.4 Wire Type Access After Load

```nb
load(EncryptedInput, from: path).ciphertext  // field access on load result
load(EncryptedQuery, from: path).query       // access wire field directly
```

---

## 8. Reserved for Future Extensions

The following are reserved but not yet defined in the grammar:

- `async` / `await` — for asynchronous FHE operations
- `trait` / `impl` — for generic programming over schemes
- `macro` — for compile-time code generation
- `extern` — now implemented for `extern name from "module"` declarations
- `module` — for explicit module declarations (currently implicit from file name)
- `pub` / `priv` — for visibility control within modules
- `test` — for inline test declarations (`@test fn ...`)
