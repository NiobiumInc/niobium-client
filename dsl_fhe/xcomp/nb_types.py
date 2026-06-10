"""Type system for the nb FHE language."""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum, auto


class TypeKind(Enum):
    VOID = auto()
    BOOL = auto()
    INT = auto()       # u8..u64, i8..i64
    FLOAT = auto()     # f32, f64
    STRING = auto()
    PATH = auto()
    VEC = auto()
    MAT = auto()
    ENC = auto()       # encrypted wrapper
    STRUCT = auto()
    WIRE = auto()
    ENUM = auto()
    FN = auto()
    CLOSURE = auto()
    UNKNOWN = auto()


@dataclass
class NbType:
    """A resolved type in the nb type system."""
    kind: TypeKind
    name: str = ""          # for named types (struct/wire/enum)
    elem: NbType | None = None  # for vec, mat, enc
    size: int | None = None     # for vec
    rows: int | None = None     # for mat
    cols: int | None = None     # for mat
    width: int = 0          # bit width for int/float (8, 16, 32, 64)
    signed: bool = True     # for int types
    param_types: list[NbType] = field(default_factory=list)   # for fn
    return_type: NbType | None = None                         # for fn
    fields: dict[str, NbType] = field(default_factory=dict)   # for struct/wire
    variants: list[str] = field(default_factory=list)         # for enum
    needs_relin: bool = False   # multiplicative depth tracking
    depth: int = 0              # current multiplicative depth
    is_encrypted: bool = False  # convenience: True if kind==ENC or inside enc

    def __repr__(self):
        if self.kind == TypeKind.VOID:
            return "void"
        if self.kind == TypeKind.BOOL:
            return "bool"
        if self.kind == TypeKind.INT:
            prefix = "i" if self.signed else "u"
            return f"{prefix}{self.width}"
        if self.kind == TypeKind.FLOAT:
            return f"f{self.width}"
        if self.kind == TypeKind.STRING:
            return "string"
        if self.kind == TypeKind.PATH:
            return "path"
        if self.kind == TypeKind.VEC:
            s = f"vec<{self.elem}>"
            return s
        if self.kind == TypeKind.MAT:
            return f"mat<{self.elem}>"
        if self.kind == TypeKind.ENC:
            return f"enc<{self.elem}>"
        if self.kind in (TypeKind.STRUCT, TypeKind.WIRE, TypeKind.ENUM):
            return self.name
        if self.kind == TypeKind.FN:
            params = ", ".join(str(p) for p in self.param_types)
            return f"fn({params}) -> {self.return_type}"
        if self.kind == TypeKind.UNKNOWN:
            return "<?>"
        return f"NbType({self.kind})"

    def __eq__(self, other):
        if not isinstance(other, NbType):
            return False
        if self.kind != other.kind:
            return False
        if self.kind == TypeKind.INT:
            return self.width == other.width and self.signed == other.signed
        if self.kind == TypeKind.FLOAT:
            return self.width == other.width
        if self.kind == TypeKind.ENC:
            return self.elem == other.elem
        if self.kind == TypeKind.VEC:
            return self.elem == other.elem
        if self.kind == TypeKind.MAT:
            return self.elem == other.elem
        if self.kind in (TypeKind.STRUCT, TypeKind.WIRE, TypeKind.ENUM):
            return self.name == other.name
        return True

    def __hash__(self):
        return hash((self.kind, self.name, self.width, self.signed))


# ----- Predefined types -----

VOID = NbType(TypeKind.VOID)
BOOL = NbType(TypeKind.BOOL)
U8 = NbType(TypeKind.INT, width=8, signed=False)
U16 = NbType(TypeKind.INT, width=16, signed=False)
U32 = NbType(TypeKind.INT, width=32, signed=False)
U64 = NbType(TypeKind.INT, width=64, signed=False)
I8 = NbType(TypeKind.INT, width=8, signed=True)
I16 = NbType(TypeKind.INT, width=16, signed=True)
I32 = NbType(TypeKind.INT, width=32, signed=True)
I64 = NbType(TypeKind.INT, width=64, signed=True)
F32 = NbType(TypeKind.FLOAT, width=32)
F64 = NbType(TypeKind.FLOAT, width=64)
STRING = NbType(TypeKind.STRING)
PATH = NbType(TypeKind.PATH)
UNKNOWN = NbType(TypeKind.UNKNOWN)

PRIMITIVE_MAP = {
    "bool": BOOL,
    "u8": U8, "u16": U16, "u32": U32, "u64": U64,
    "i8": I8, "i16": I16, "i32": I32, "i64": I64,
    "f32": F32, "f64": F64,
    "string": STRING, "path": PATH,
}


def enc_of(inner: NbType) -> NbType:
    return NbType(TypeKind.ENC, elem=inner, is_encrypted=True)


def vec_of(elem: NbType, size: int | None = None) -> NbType:
    return NbType(TypeKind.VEC, elem=elem, size=size,
                  is_encrypted=elem.is_encrypted)


def mat_of(elem: NbType) -> NbType:
    return NbType(TypeKind.MAT, elem=elem, is_encrypted=elem.is_encrypted)


def is_numeric(t: NbType) -> bool:
    return t.kind in (TypeKind.INT, TypeKind.FLOAT)


def is_encrypted(t: NbType) -> bool:
    return t.kind == TypeKind.ENC or t.is_encrypted


def common_type(a: NbType, b: NbType) -> NbType:
    """Determine the result type of a binary operation."""
    # enc propagation — return a *copy* so depth mutations don't
    # leak back into the original symbol's type
    if a.kind == TypeKind.ENC and b.kind == TypeKind.ENC:
        import copy
        return copy.copy(a)
    if a.kind == TypeKind.ENC:
        import copy
        return copy.copy(a)
    if b.kind == TypeKind.ENC:
        import copy
        return copy.copy(b)
    # numeric promotion
    if a.kind == TypeKind.FLOAT or b.kind == TypeKind.FLOAT:
        return F64
    if a.kind == TypeKind.INT and b.kind == TypeKind.INT:
        w = max(a.width, b.width)
        s = a.signed or b.signed
        return NbType(TypeKind.INT, width=w, signed=s)
    return UNKNOWN


# ----- Domain tracking -----

class Domain(Enum):
    CLIENT = "client"
    SERVER = "server"
    SHARED = "shared"  # visible to both


# Capabilities restricted by domain
CLIENT_ONLY_FNS = {"decrypt", "keygen", "save_secret_key", "load_secret_key"}
SERVER_FORBIDDEN_TYPES = {"SecretKey"}


# ----- Symbol table -----

@dataclass
class Symbol:
    name: str
    type: NbType
    domain: Domain = Domain.SHARED
    is_const: bool = False
    const_value: object = None  # for compile-time constants


class SymbolTable:
    """Nested scope symbol table."""

    def __init__(self, parent: SymbolTable | None = None, name: str = "<global>"):
        self.parent = parent
        self.name = name
        self.symbols: dict[str, Symbol] = {}

    def define(self, sym: Symbol):
        self.symbols[sym.name] = sym

    def lookup(self, name: str) -> Symbol | None:
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def child(self, name: str = "<block>") -> SymbolTable:
        return SymbolTable(parent=self, name=name)
