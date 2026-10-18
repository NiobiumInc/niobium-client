"""Modules: loading `use`d modules and resolving qualified names.

A module is the set of `.niob` files that declare `module <name>`. A file
without the declaration belongs to the root module, which keeps the global
C++ namespace, so a program written before modules existed compiles to the
same C++ it always did. The files handed to `nbc compile` form the root
module; `use <name>` in any of them imports another module, whose files are
found through the module search path: every `*.niob` directly in a search
directory or one level below it whose first declaration is `module <name>`.

Every declaration of a module (functions, constants, enums and their
variants, structs, wires) is referenced as `<module>::name` from outside the
module and either bare or `<module>::name` inside it. The resolver rewrites
each reference in a loaded module to that canonical form, so the analyzer
and the generator key every table by the canonical name and never see a
collision: two modules may both declare `Instance` or `encdir`. The
generator emits a module's declarations inside `namespace <module> { }`, so
the canonical name is also the C++ name.

What an imported module contributes to the program:
  - its declarations, in `namespace <module>`;
  - its `@stage` functions, as binaries named `<module>_<stage>`;
  - its `requires` capabilities, unioned into the root's (keygen must make
    the keys the imported code uses).
Its `scheme` and `domain` declarations are dropped: one program has one
scheme, the root's. A differing imported scheme is reported as a warning.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields as dc_fields, is_dataclass
from pathlib import Path

from . import ast_nodes as ast
from .errors import CompileError, SourceLocation
from .lexer import lex, Token, TT
from .parser import parse


class ModuleError(CompileError):
    """A module could not be found, loaded, or a qualified name resolved."""
    pass


DECL_TYPES = (ast.FnDecl, ast.ConstDecl, ast.EnumDecl,
              ast.StructDecl, ast.WireDecl)

# The file names a `use <name>::*` may refer to within the same module. The
# form predates modules, names a file, not a module, and has no effect.
FILE_GLOBS = ("shared", "client", "server")


def fqn(module: str, name: str) -> str:
    """Canonical name of declaration `name` of `module` ("" = root)."""
    return f"{module}::{name}" if module else name


def base_name(name: str) -> str:
    """`fraud_flag::Instance` -> `Instance`; a bare name is returned as is."""
    return name.rsplit("::", 1)[-1]


def module_of(name: str) -> str:
    """`fraud_flag::Instance` -> `fraud_flag`; "" for a bare name."""
    return name.rsplit("::", 1)[0] if "::" in name else ""


def declared_names(items) -> set[str]:
    """Top-level names a list of items declares, enum variants included."""
    names: set[str] = set()
    for item in items:
        if isinstance(item, DECL_TYPES):
            names.add(item.name)
        if isinstance(item, ast.EnumDecl):
            names.update(item.variants)
    return names


def type_names_in_tokens(tokens: list[Token]) -> set[str]:
    """struct/wire/enum names, from the tokens alone (the parser needs them
    before it sees the declarations, to tell `Name { .. }` from a block)."""
    names: set[str] = set()
    for i, t in enumerate(tokens[:-1]):
        if t.type in (TT.STRUCT, TT.WIRE, TT.ENUM) and tokens[i + 1].type == TT.IDENT:
            names.add(tokens[i + 1].value)
    return names


def module_name_in_tokens(tokens: list[Token]) -> str:
    """The `module <name>` a token stream declares, "" when none."""
    for i, t in enumerate(tokens[:-1]):
        if t.type == TT.MODULE and tokens[i + 1].type == TT.IDENT:
            return tokens[i + 1].value
    return ""


def module_imports_in_tokens(tokens: list[Token]) -> list[str]:
    """Names of the modules a token stream imports with `use <name>`, in
    order, without duplicates. `use <name>::...` is the file-glob form."""
    out: list[str] = []
    for i, t in enumerate(tokens[:-1]):
        if (t.type == TT.USE and tokens[i + 1].type == TT.IDENT
                and tokens[i + 2].type != TT.COLONCOLON):
            name = tokens[i + 1].value
            if name not in out:
                out.append(name)
    return out


@dataclass
class Module:
    name: str                      # "" for the root module
    files: list[Path]
    programs: list[ast.Program] = field(default_factory=list)
    items: list[ast.Node] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    decl_names: set[str] = field(default_factory=set)
    type_names: set[str] = field(default_factory=set)


class ModuleLoader:
    """Loads the root module and, transitively, everything it imports."""

    def __init__(self, search_paths: list[Path]):
        self.search_paths = [Path(p) for p in search_paths]
        self.modules: dict[str, Module] = {}
        self.order: list[str] = []        # imported modules, dependencies first
        self.warnings: list[str] = []
        self._loading: list[str] = []
        self._scan_cache: dict[Path, str] = {}
        self._root_files: set[Path] = set()

    # ----- Public entry point -----

    def load(self, root_files: list[str | Path]) -> tuple[ast.Program, Module]:
        """Parse the root files, load every module they import, resolve all
        names and merge everything into one Program (imports first)."""
        root_paths = [Path(f) for f in root_files]
        self._root_files = {p.resolve() for p in root_paths}
        token_lists = [lex(p.read_text(), str(p)) for p in root_paths]

        names = {module_name_in_tokens(toks) for toks in token_lists}
        if len(names) > 1:
            listing = ", ".join(
                f"{p} declares {module_name_in_tokens(t) or 'no module'}"
                for p, t in zip(root_paths, token_lists))
            raise ModuleError(
                "the files of a program must declare the same module (or "
                f"none): {listing}")
        root_name = names.pop() if names else ""

        root = Module(name=root_name, files=root_paths)
        self._loading.append(root_name)
        self._parse_module(root, token_lists)
        self._loading.pop()

        program = ast.Program(module_name=root_name)
        program.items = self._merge(root)
        return program, root

    # ----- Loading -----

    def _parse_module(self, mod: Module, token_lists: list[list[Token]]):
        """Load the imports named by the tokens first (their type names feed
        the parser), then parse each file, tag and resolve its items."""
        imports: list[str] = []
        for toks in token_lists:
            for name in module_imports_in_tokens(toks):
                if name not in imports:
                    imports.append(name)
        for name in imports:
            if name == mod.name or name in self._loading:
                chain = " -> ".join(self._loading + [name])
                raise ModuleError(
                    f"module {name!r} imports itself (cycle: {chain})")
            if name not in self.modules:
                self._load_import(name)
        mod.imports = imports

        known: set[str] = set()
        for toks in token_lists:
            known |= type_names_in_tokens(toks)
        mod.type_names = set(known)
        for name in imports:
            known |= {fqn(name, t) for t in self.modules[name].type_names}
        # A module may qualify its own types too (`fraud_flag::Instance`).
        if mod.name:
            known |= {fqn(mod.name, t) for t in mod.type_names}

        for toks in token_lists:
            prog = parse(toks, set(known))
            mod.programs.append(prog)
            for item in prog.items:
                item.module = mod.name
                mod.items.append(item)
        mod.decl_names = declared_names(mod.items)

        for item in mod.items:
            if isinstance(item, ast.UseDecl):
                self._check_use(item, mod)
        NameResolver(mod, self).resolve()

    def _load_import(self, name: str):
        files = self._find_module_files(name)
        if not files:
            where = ", ".join(str(p) for p in self.search_paths) or "(none)"
            raise ModuleError(
                f"module {name!r} not found: no .niob file declaring "
                f"`module {name}` under the module search path ({where}); "
                "pass --module-path DIR")
        mod = Module(name=name, files=files)
        self._loading.append(name)
        token_lists = [lex(p.read_text(), str(p)) for p in files]
        self._parse_module(mod, token_lists)
        self._loading.pop()
        self.modules[name] = mod
        self.order.append(name)

    def _find_module_files(self, name: str) -> list[Path]:
        """The files declaring `module <name>`, from the first search
        directory that holds any. A directory contributes the .niob files
        directly in it and one level below it; the root's own files never
        count (a module cannot import the program that imports it)."""
        for d in self.search_paths:
            if not d.is_dir():
                continue
            candidates = sorted(d.glob("*.niob")) + sorted(d.glob("*/*.niob"))
            found = [p for p in candidates
                     if p.resolve() not in self._root_files
                     and self._scan_module_name(p) == name]
            if found:
                return found
        return []

    def _scan_module_name(self, path: Path) -> str:
        key = path.resolve()
        if key not in self._scan_cache:
            try:
                self._scan_cache[key] = module_name_in_tokens(
                    lex(path.read_text(), str(path)))
            except (CompileError, OSError, UnicodeDecodeError):
                # A file that does not lex is not a candidate; it is reported
                # only if something actually tries to compile it.
                self._scan_cache[key] = ""
        return self._scan_cache[key]

    def _check_use(self, use: ast.UseDecl, mod: Module):
        if use.is_module_import:
            return
        head = use.module_path[0]
        if len(use.module_path) == 1 and head in FILE_GLOBS and use.imported == "*":
            return  # `use shared::*`: a file of this module, no effect
        written = f"use {'::'.join(use.module_path)}::{use.imported}"
        raise ModuleError(
            f"`{written}`: {head!r} is not a file of this module (shared, "
            f"client, server); a module is imported whole, with `use {head}`, "
            f"and its declarations are referenced as {head}::name", use.loc)

    # ----- Merging -----

    def _merge(self, root: Module) -> list[ast.Node]:
        """Imported modules' items in dependency order, then the root's, with
        one scheme and one requires declaration for the whole program."""
        root_scheme = next((i for i in root.items if isinstance(i, ast.SchemeDecl)), None)
        root_requires = next((i for i in root.items if isinstance(i, ast.RequiresDecl)), None)
        extra_caps: list[str] = []

        merged: list[ast.Node] = []
        for name in self.order:
            mod = self.modules[name]
            for item in mod.items:
                if isinstance(item, (ast.UseDecl, ast.DomainDecl)):
                    continue
                if isinstance(item, ast.SchemeDecl):
                    if root_scheme is not None and not _same_scheme(root_scheme, item):
                        self.warnings.append(
                            f"{item.loc}: warning: module {name!r} declares a "
                            f"scheme that differs from the program's; the "
                            f"program's scheme applies to the imported code")
                    elif root_scheme is None:
                        root_scheme = item
                        merged.append(item)
                    continue
                if isinstance(item, ast.RequiresDecl):
                    for cap in item.capabilities:
                        if cap not in extra_caps:
                            extra_caps.append(cap)
                    continue
                merged.append(item)
        merged.extend(root.items)

        if extra_caps:
            if root_requires is None:
                root_requires = ast.RequiresDecl(capabilities=[])
                merged.append(root_requires)
            for cap in extra_caps:
                if cap not in root_requires.capabilities:
                    root_requires.capabilities.append(cap)
        return merged


def _same_scheme(a: ast.SchemeDecl, b: ast.SchemeDecl) -> bool:
    return (a.name == b.name
            and {f.key: f.value for f in a.fields} == {f.key: f.value for f in b.fields})


class NameResolver:
    """Rewrites every reference in a module to its canonical name.

    A bare name that the module declares becomes `<module>::name` (left bare
    in the root module, whose namespace is global). A qualified name must
    name an imported module (or the module itself) and a declaration of it.
    Local bindings (parameters, let, for, closure and match bindings) shadow
    module declarations and are left alone."""

    def __init__(self, mod: Module, loader: ModuleLoader):
        self.mod = mod
        self.loader = loader
        self.imports = set(mod.imports)
        self.scopes: list[set[str]] = []

    def resolve(self):
        for item in self.mod.items:
            if isinstance(item, (ast.UseDecl, ast.ExternDecl, ast.SchemeDecl,
                                 ast.DomainDecl)):
                continue
            if isinstance(item, ast.FnDecl):
                self._resolve_fn(item)
            else:
                self._walk(item)

    # ----- Scopes -----

    def _push(self, names=()):
        self.scopes.append(set(names))

    def _pop(self):
        self.scopes.pop()

    def _bind(self, name: str):
        if self.scopes:
            self.scopes[-1].add(name)

    def _is_local(self, name: str) -> bool:
        return any(name in s for s in self.scopes)

    # ----- Name rewriting -----

    def _canon(self, name: str, loc, what: str) -> str:
        if "::" in name:
            mod, rest = name.split("::", 1)
            if "::" in rest:
                raise ModuleError(f"{what} {name!r}: only one module "
                                  "qualifier is allowed", loc)
            if mod == self.mod.name:
                target = self.mod
            elif mod in self.imports:
                target = self.loader.modules[mod]
            elif mod in self.loader.modules or mod in self.loader._loading:
                raise ModuleError(
                    f"{what} {name!r}: module {mod!r} is not imported here "
                    f"(add `use {mod}`)", loc)
            else:
                raise ModuleError(f"{what} {name!r}: unknown module {mod!r}", loc)
            if rest not in target.decl_names:
                raise ModuleError(
                    f"{what} {name!r}: module {mod!r} declares no {rest!r}", loc)
            return fqn(target.name, rest)
        if (self.mod.name and name in self.mod.decl_names
                and not self._is_local(name)):
            return fqn(self.mod.name, name)
        return name

    def _resolve_fn(self, fn: ast.FnDecl):
        self._push(p.name for p in fn.params)
        for p in fn.params:
            self._walk(p.type_ann)
            self._walk(p.default)
        self._walk(fn.return_type)
        for spec in fn.io_specs:
            self._walk(spec)
        self._walk(fn.body)
        self._pop()

    def _walk(self, node):
        if node is None:
            return
        if isinstance(node, list):
            for n in node:
                self._walk(n)
            return
        if isinstance(node, dict):
            for v in node.values():
                self._walk(v)
            return
        if not is_dataclass(node):
            return

        # Reference sites.
        if isinstance(node, ast.Ident):
            node.name = self._canon(node.name, node.loc, "name")
            return
        if isinstance(node, ast.NamedType):
            node.name = self._canon(node.name, node.loc, "type")
            return
        if isinstance(node, ast.StructLiteral):
            node.type_name = self._canon(node.type_name, node.loc, "struct literal")
            for fi in node.fields:
                self._walk(fi.value)
            return
        if isinstance(node, ast.IoType):
            if node.type_name:
                node.type_name = self._canon(node.type_name, node.loc, "io type")
            self._walk(node.index)
            self._walk(node.path_expr)
            return
        if isinstance(node, ast.StructPattern):
            node.type_name = self._canon(node.type_name, node.loc, "pattern")
            for fp in node.fields:
                if fp.pattern is None:
                    self._bind(fp.name)
                else:
                    self._walk(fp.pattern)
            return
        if isinstance(node, ast.IdentPattern):
            # An enum variant (declared) or a fresh binding (not declared).
            if "::" in node.name or node.name in self.mod.decl_names:
                node.name = self._canon(node.name, node.loc, "pattern")
            else:
                self._bind(node.name)
            return

        # Binding sites.
        if isinstance(node, ast.LetStmt):
            self._walk(node.type_ann)
            self._walk(node.value)
            for n in (node.tuple_names or [node.name]):
                self._bind(n)
            return
        if isinstance(node, (ast.ForStmt, ast.ForExpr)):
            self._walk(node.iterable)
            self._push(node.pattern.names if node.pattern else ())
            self._walk(node.body)
            self._pop()
            return
        if isinstance(node, ast.Closure):
            self._push(p.name for p in node.params)
            for p in node.params:
                self._walk(p.type_ann)
            self._walk(node.body)
            self._pop()
            return
        if isinstance(node, ast.MatchArm):
            self._push()
            self._walk(node.pattern)
            self._walk(node.body)
            self._pop()
            return
        if isinstance(node, ast.Block):
            self._push()
            for s in node.stmts:
                self._walk(s)
            self._pop()
            return

        for f in dc_fields(node):
            if f.name in ("loc", "module"):
                continue
            self._walk(getattr(node, f.name))


def default_search_paths(files: list[str | Path]) -> list[Path]:
    """The directories searched for modules when none is given: the parent
    of each input file's directory (so sibling modules such as examples/*
    find each other), then the input file's own directory."""
    out: list[Path] = []
    for f in files:
        d = Path(f).resolve().parent
        for cand in (d.parent, d):
            if cand not in out:
                out.append(cand)
    return out


def load_program(files: list[str | Path],
                 module_paths: list[str | Path] | None = None,
                 ) -> tuple[ast.Program, ModuleLoader]:
    """Load `files` as the root module with every module they import. Explicit
    search directories come first, the defaults after them."""
    paths = [Path(p) for p in (module_paths or [])]
    for d in default_search_paths(files):
        if d not in paths:
            paths.append(d)
    loader = ModuleLoader(paths)
    program, _root = loader.load(files)
    return program, loader
