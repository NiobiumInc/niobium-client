"""OpenFHE C++ code generator for the nb FHE language.

Translates a checked AST into OpenFHE C++ source files.
Each @stage function produces a separate .cpp file with a main().
"""

from __future__ import annotations
from dataclasses import dataclass, field, fields, is_dataclass
from typing import TextIO
import io

import ast_nodes as ast
from semantic import SemanticAnalyzer
from nb_types import Domain


# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

# nb type -> C++ type string
CPP_TYPE_MAP = {
    "bool": "bool",
    "u8": "uint8_t",
    "u16": "uint16_t",
    "u32": "uint32_t",
    "u64": "uint64_t",
    "i8": "int8_t",
    "i16": "int16_t",
    "i32": "int32_t",
    "i64": "int64_t",
    "f32": "float",
    "f64": "double",
    "string": "std::string",
    "path": "std::filesystem::path",
}

# nb binary operator -> C++ operator (for plaintext operations)
CPP_OP_MAP = {
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    "%": "%",
    "^": "",  # special-cased: std::pow
    "==": "==",
    "!=": "!=",
    "<": "<",
    ">": ">",
    "<=": "<=",
    ">=": ">=",
    "&&": "&&",
    "||": "||",
    "~=": "==",
    "<<": "<<",
    ">>": ">>",
}

# nb FHE op -> OpenFHE method
FHE_ADD = "EvalAdd"
FHE_SUB = "EvalSub"
FHE_MUL = "EvalMult"
FHE_MUL_NORELIN = "EvalMultNoRelin"
FHE_ROTATE = "EvalRotate"
FHE_RELIN = "RelinearizeInPlace"

# Standard OpenFHE includes
OPENFHE_INCLUDES = [
    "openfhe.h",
    "ciphertext-ser.h",
    "cryptocontext-ser.h",
    "key/key-ser.h",
    "scheme/ckksrns/ckksrns-ser.h",
]

NIOBIUM_INCLUDE = "niobium/compiler.h"

# Names that indicate encrypted ciphertext variables (exact names or safe prefixes)
# IMPORTANT: Avoid short prefixes that match plaintext constant names
ENCRYPTED_PREFIXES = (
    "ct_", "ct",  # ct or ct_something
    "enc_", "encrypted",
    "eqry", "edb", "eres",
    "result",
    "acc",  # accumulator variable
    "indicator",
    "replicated",
    "masked",
    "payload_block",  # NOT "payload" — conflicts with PAYLOAD_DIM constant
    "to_replicate",
    "accumulator",
    "matches",
    "qry_slot",
    "residual",  # NID: reconstruction residuals (enc vectors)
    "hidden",    # NID: hidden layer ciphertexts
    "recon",     # NID: reconstruction layer ciphertexts
)

# Exact encrypted variable names (not prefix-based)
ENCRYPTED_EXACT_NAMES = {
    "ct", "acc", "part", "eqry", "edb",
    "mse", "diff", "score",  # NID: anomaly detector accumulators
}

# Functions whose return value is always a ciphertext
ENCRYPTED_RETURN_FNS = {
    "encrypt", "rotate", "relin", "chebyshev", "clone",
    "reduce", "slot_sum", "total_sums", "zero",
    "get_encrypted_payload", "get_ctxt",
    "mat_vec_mult_single", "compact_and_extract",
    "extract_payload",
    "negate", "mul_monomial", "dispatch",
    "kitnet_ckks", "anomaly_detector_forward",
}

# DSL built-in functions that operate on plaintext data (never produce FHE ops)
PLAINTEXT_ONLY_FNS = {
    "n_slots", "n_ctxts", "n_cols", "max_n_match",
    "len", "rows", "ceil_div", "log2", "round", "abs",
    "stride", "instance", "instance_name",
    "datadir", "iodir", "keydir", "encdir", "root",
}

# Functions that need cc as first argument (FHE shared functions)
FHE_SHARED_FNS = {
    "mat_vec_mult", "mat_vec_mult_single", "compact_and_extract",
    "extract_payload", "total_sums",
    "dispatch", "large_add_mul",
    "kitnet_ckks", "autoencoder_forward", "anomaly_detector_forward",
}


@dataclass
class StageInfo:
    """Metadata about a @stage function for code generation."""
    name: str
    fn: ast.FnDecl
    domain: Domain
    hardware: dict | None = None
    io_specs: list[ast.IoSpec] = field(default_factory=list)


class CodeGenerator:
    """Generates OpenFHE C++ from a checked nb AST."""

    def __init__(self, program: ast.Program, analyzer: SemanticAnalyzer):
        self.program = program
        self.sa = analyzer
        self.indent_level = 0
        self.out: TextIO = io.StringIO()
        self.stages: list[StageInfo] = []
        self.shared_fns: list[ast.FnDecl] = []
        self.consts: list[ast.ConstDecl] = []
        self.enums: list[ast.EnumDecl] = []
        self.structs: list[ast.StructDecl] = []
        self.wires: list[ast.WireDecl] = []
        self.externs: list[ast.ExternDecl] = []
        self.scheme: ast.SchemeDecl | None = None
        self.requires: list[str] = []
        self._current_fn: ast.FnDecl | None = None
        self._in_return_expr: bool = False  # set when generating return expression value
        self._keygen_vars: set[str] = set()  # variables assigned from keygen()
        self._local_var_cpp_types: dict[str, str] = {}  # track local variable C++ types
        self._declared_vars: set[str] = set()  # track declared variable names for let-rebinding
        self._classify_items()

    def _classify_items(self):
        for item in self.program.items:
            if isinstance(item, ast.FnDecl):
                stage = self._get_stage(item)
                if stage:
                    self.stages.append(stage)
                else:
                    self.shared_fns.append(item)
            elif isinstance(item, ast.ConstDecl):
                self.consts.append(item)
            elif isinstance(item, ast.EnumDecl):
                self.enums.append(item)
            elif isinstance(item, ast.StructDecl):
                self.structs.append(item)
            elif isinstance(item, ast.WireDecl):
                self.wires.append(item)
            elif isinstance(item, ast.ExternDecl):
                self.externs.append(item)
            elif isinstance(item, ast.SchemeDecl):
                self.scheme = item
            elif isinstance(item, ast.RequiresDecl):
                self.requires = item.capabilities

    def _get_stage(self, fn: ast.FnDecl) -> StageInfo | None:
        stage_name = None
        domain = Domain.SHARED
        hardware = None
        for ann in fn.annotations:
            if ann.name == "stage":
                stage_name = ann.args.get("name", fn.name)
            if ann.name == "client":
                domain = Domain.CLIENT
            if ann.name == "server":
                domain = Domain.SERVER
            if ann.name == "hardware":
                hardware = ann.args
        if stage_name is None:
            return None
        return StageInfo(
            name=stage_name if isinstance(stage_name, str) else fn.name,
            fn=fn,
            domain=domain,
            hardware=hardware,
            io_specs=fn.io_specs,
        )

    # ===== Output helpers =====

    def w(self, text: str = ""):
        self.out.write(text)

    def wl(self, text: str = ""):
        self.out.write("  " * self.indent_level + text + "\n")

    def blank(self):
        self.out.write("\n")

    def indent(self):
        self.indent_level += 1

    def dedent(self):
        self.indent_level -= 1

    # ===== Determine if a function uses FHE operations =====

    def _is_extern_wrapper(self, fn: ast.FnDecl) -> bool:
        """Check if a function body is a single extern_call — these delegate to external C++."""
        stmts = fn.body.stmts if isinstance(fn.body, ast.Block) else (fn.body or [])
        if len(stmts) != 1:
            return False
        stmt = stmts[0]
        # Either a bare extern_call expression or return extern_call(...)
        expr = None
        if isinstance(stmt, ast.ExprStmt) and stmt.expr:
            expr = stmt.expr
        elif isinstance(stmt, ast.ReturnStmt) and stmt.value:
            expr = stmt.value
        if expr and isinstance(expr, ast.CallExpr):
            if isinstance(expr.func, ast.Ident) and expr.func.name == "extern_call":
                return True
        return False

    def _get_extern_call_name(self, fn: ast.FnDecl) -> str | None:
        """Get the external function name from an extern wrapper function."""
        if not self._is_extern_wrapper(fn):
            return None
        stmts = fn.body.stmts if isinstance(fn.body, ast.Block) else (fn.body or [])
        stmt = stmts[0]
        expr = stmt.expr if isinstance(stmt, ast.ExprStmt) else stmt.value
        if expr and isinstance(expr, ast.CallExpr) and expr.args:
            arg0 = expr.args[0].value
            if isinstance(arg0, ast.StringLiteral):
                return arg0.value
        return None

    def _fn_uses_fhe(self, fn: ast.FnDecl) -> bool:
        """Check if a function body references encrypted types or FHE operations."""
        if fn.return_type and isinstance(fn.return_type, ast.EncType):
            return True
        if fn.return_type and isinstance(fn.return_type, ast.VecType):
            if isinstance(fn.return_type.elem, ast.EncType):
                return True
        for p in fn.params:
            if p.type_ann and isinstance(p.type_ann, ast.EncType):
                return True
        return fn.name in FHE_SHARED_FNS

    # ===== Generate all outputs =====

    def generate_all(self) -> dict[str, str]:
        """Generate all output files. Returns {filename: content}."""
        files = {}
        files["nb_shared.h"] = self._gen_shared_header()
        for stage in self.stages:
            fname = f"{stage.name}.cpp"
            files[fname] = self._gen_stage_file(stage)
        if self.shared_fns:
            files["nb_shared.cpp"] = self._gen_shared_impl()
        files["CMakeLists.txt"] = self._gen_cmake()
        return files

    def _gen_cmake(self) -> str:
        """Generate CMakeLists.txt for the compiled stages."""
        # Determine extra shared sources from requires capabilities
        extra_src = []
        if "replicate" in self.requires:
            extra_src.append("slot_replication.cpp")
        if "running_sums" in self.requires:
            extra_src.append("running_sums.cpp")

        stage_names = [s.name for s in self.stages]
        shared_src = " ".join(["nb_shared.cpp"] + extra_src)
        stages_list = "\n  ".join(stage_names)

        # @hardware server stages link the auto-facade lib whole-archive so its
        # strong deserialize-hook symbols override libnbfhetch's weak stubs and
        # cooperative auto-tagging activates. Whole-archive is required because
        # nothing in the generated .cpp references those symbols by name.
        hw_stage_names = [s.name for s in self.stages
                          if s.domain == Domain.SERVER and s.hardware]
        autofacade_link_lines = []
        for stage_name in hw_stage_names:
            autofacade_link_lines.append(
                f'target_link_libraries({stage_name} PRIVATE ${{NB_AUTOFACADE_LIB}} ${{NB_YAMLCPP_LIB}})')
            autofacade_link_lines.append("if(UNIX AND NOT APPLE)")
            autofacade_link_lines.append(
                f'  target_link_options({stage_name} PRIVATE '
                f'"LINKER:--whole-archive,${{NB_AUTOFACADE_LIB}},--no-whole-archive")')
            autofacade_link_lines.append("elseif(APPLE)")
            autofacade_link_lines.append(
                f'  target_link_options({stage_name} PRIVATE '
                f'"LINKER:-force_load,${{NB_AUTOFACADE_LIB}}")')
            autofacade_link_lines.append("endif()")
        autofacade_link_block = "\n".join(autofacade_link_lines)

        # Collect extern source libraries and include directories
        extern_cmake_lines = []
        extern_lib_names = []
        # Add include paths for external sources (SUBMISSION_DIR headers + LOCAL_SRC_DIR bridge headers)
        extern_cmake_lines.append('if(DEFINED SUBMISSION_DIR)')
        extern_cmake_lines.append('  include_directories(${SUBMISSION_DIR}/include)')
        extern_cmake_lines.append('endif()')
        extern_cmake_lines.append('if(DEFINED LOCAL_SRC_DIR)')
        extern_cmake_lines.append('  include_directories(${LOCAL_SRC_DIR})')
        extern_cmake_lines.append('endif()')
        if self.externs:
            extern_cmake_lines.append(
                '# External source libraries (from extern declarations)')
            for ext in self.externs:
                extern_cmake_lines.append(
                    f'if(EXISTS "${{SUBMISSION_DIR}}/src/{ext.source}.cpp")')
                extern_cmake_lines.append(
                    f'  add_library({ext.source} ${{SUBMISSION_DIR}}/src/{ext.source}.cpp)')
                extern_cmake_lines.append(f'endif()')
                extern_lib_names.append(ext.source)
        # Detect extern_call targets: find wrapper functions and add their sources.
        # Checks SUBMISSION_DIR/src first, then LOCAL_SRC_DIR (for local bridges/wrappers).
        # e.g. mlp -> mlp_openfhe.cpp, mlp_function_split_0.cpp, mlp_encryption_utils.cpp,
        #            mlp_common.cpp (shared utils), mlp_bridge.cpp (local DSL wrapper)
        for fn in self.shared_fns:
            ext_name = self._get_extern_call_name(fn)
            if ext_name and ext_name not in [e.source for e in self.externs]:
                for suffix in ["_openfhe", "_function_split_0", "_encryption_utils",
                               "_common", "_bridge"]:
                    lib_name = f"{ext_name}{suffix}"
                    extern_cmake_lines.append(
                        f'if(EXISTS "${{SUBMISSION_DIR}}/src/{lib_name}.cpp")')
                    extern_cmake_lines.append(
                        f'  add_library({lib_name} ${{SUBMISSION_DIR}}/src/{lib_name}.cpp)')
                    extern_cmake_lines.append(
                        f'elseif(DEFINED LOCAL_SRC_DIR AND EXISTS "${{LOCAL_SRC_DIR}}/{lib_name}.cpp")')
                    extern_cmake_lines.append(
                        f'  add_library({lib_name} ${{LOCAL_SRC_DIR}}/{lib_name}.cpp)')
                    extern_cmake_lines.append(f'endif()')
                    extern_lib_names.append(lib_name)

        extern_block = "\n".join(extern_cmake_lines)
        # Link extern libraries to server stages
        # List libraries twice to handle cross-references between static libs
        extern_link_block = ""
        if extern_lib_names:
            server_stages = [s.name for s in self.stages
                             if s.domain == Domain.SERVER]
            for stage_name in server_stages:
                for lib in extern_lib_names:
                    extern_link_block += (
                        f'\nif(TARGET {lib})\n'
                        f'  target_link_libraries({stage_name} PRIVATE {lib})\n'
                        f'endif()')
                # Repeat to resolve cross-references between static libs
                for lib in extern_lib_names:
                    extern_link_block += (
                        f'\nif(TARGET {lib})\n'
                        f'  target_link_libraries({stage_name} PRIVATE {lib})\n'
                        f'endif()')

        return f"""\
# Auto-generated by nbc — do not edit
cmake_minimum_required(VERSION 3.14)
project(nb_generated LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)

# The instrumented OpenFHE serialization headers (ciphertext-ser.h,
# cryptocontext-ser.h) fire the niobium_auto deserialize hooks only when
# OPENFHE_CPROBES is defined. libnbfhetch exports this as a PUBLIC compile
# definition, but since we link it by path (not as a CMake target) we must
# define it here so cooperative auto-tagging activates.
add_compile_definitions(OPENFHE_CPROBES)

# Locate the niobium-client repo root. It holds the vendored, Niobium-
# instrumented OpenFHE (vendor/lib/openfhe) and the FHETCH client library
# (vendor/niobium-fhetch, built into libnbfhetch). Walk up from the source dir.
if(NOT DEFINED NIOBIUM_CLIENT_ROOT)
  set(_search_dir "${{CMAKE_CURRENT_SOURCE_DIR}}")
  foreach(_i RANGE 12)
    if(EXISTS "${{_search_dir}}/vendor/niobium-fhetch/include/niobium/compiler.h")
      set(NIOBIUM_CLIENT_ROOT "${{_search_dir}}" CACHE PATH "Path to niobium-client repo root")
      break()
    endif()
    get_filename_component(_search_dir "${{_search_dir}}/.." ABSOLUTE)
  endforeach()
  if(NOT DEFINED NIOBIUM_CLIENT_ROOT)
    message(FATAL_ERROR "Cannot find niobium-client root. Set -DNIOBIUM_CLIENT_ROOT=...")
  endif()
endif()

set(OPENFHE_LIBRARY_PATH "${{NIOBIUM_CLIENT_ROOT}}/vendor/lib/openfhe/lib")
set(OPENFHE_INCLUDE_PATH "${{NIOBIUM_CLIENT_ROOT}}/vendor/lib/openfhe/include/openfhe")
set(OPENFHE_LIBRARIES OPENFHEpke OPENFHEcore OPENFHEbinfhe)

# FHETCH client library headers (niobium/compiler.h, fhetch_api.h, ...).
set(FHETCH_INCLUDE_PATH "${{NIOBIUM_CLIENT_ROOT}}/vendor/niobium-fhetch/include")

include_directories(${{CMAKE_CURRENT_SOURCE_DIR}})
include_directories(${{OPENFHE_INCLUDE_PATH}})
include_directories(${{OPENFHE_INCLUDE_PATH}}/core)
include_directories(${{OPENFHE_INCLUDE_PATH}}/pke)
include_directories(${{OPENFHE_INCLUDE_PATH}}/binfhe)
include_directories(${{OPENFHE_INCLUDE_PATH}}/third-party/include)
include_directories(${{FHETCH_INCLUDE_PATH}})

# Find the prebuilt libnbfhetch (built by the client's `make build`). Both the
# top-level build tree and niobium-fhetch's own build tree are searched.
set(FHETCH_LIB_DIRS
  "${{NIOBIUM_CLIENT_ROOT}}/build/vendor/niobium-fhetch"
  "${{NIOBIUM_CLIENT_ROOT}}/vendor/niobium-fhetch/build")
find_library(NBFHETCH_LIB
  NAMES nbfhetch
  PATHS ${{FHETCH_LIB_DIRS}}
  NO_DEFAULT_PATH)
if(NOT NBFHETCH_LIB)
  message(FATAL_ERROR
    "Cannot find libnbfhetch. Build the client first (e.g. `make build` at the "
    "niobium-client root), or set -DNIOBIUM_CLIENT_ROOT=...")
endif()

# Find libniobium_client_autofacade — provides the strong deserialize-hook
# implementations that drive cooperative auto-tagging on @hardware server
# stages (built when NIOBIUM_CLIENT_WITH_AUTO_FACADE=ON, the default).
find_library(NB_AUTOFACADE_LIB
  NAMES niobium_client_autofacade
  PATHS "${{NIOBIUM_CLIENT_ROOT}}/build/src/auto_facade"
  NO_DEFAULT_PATH)
if(NOT NB_AUTOFACADE_LIB)
  message(FATAL_ERROR
    "Cannot find libniobium_client_autofacade. Rebuild the client with "
    "-DNIOBIUM_CLIENT_WITH_AUTO_FACADE=ON, or set -DNIOBIUM_CLIENT_ROOT=...")
endif()

# The auto-facade reads its (optional) YAML config via yaml-cpp. Prefer a
# system install; otherwise use the copy FetchContent built under the client.
find_library(NB_YAMLCPP_LIB
  NAMES yaml-cpp
  PATHS "${{NIOBIUM_CLIENT_ROOT}}/build/_deps/yaml_cpp-build")
if(NOT NB_YAMLCPP_LIB)
  message(FATAL_ERROR "Cannot find yaml-cpp (needed by libniobium_client_autofacade).")
endif()

link_directories(${{OPENFHE_LIBRARY_PATH}})
link_libraries(${{OPENFHE_LIBRARIES}} ${{NBFHETCH_LIB}})

if(NOT APPLE)
  add_link_options(-Wl,--no-as-needed)
endif()

set(_NB_RPATH "${{OPENFHE_LIBRARY_PATH}};${{FHETCH_LIB_DIRS}};${{NIOBIUM_CLIENT_ROOT}}/build/_deps/yaml_cpp-build")
set(CMAKE_BUILD_RPATH "${{_NB_RPATH}}")
set(CMAKE_INSTALL_RPATH "${{_NB_RPATH}}")

{extern_block}

set(SHARED_SRC {shared_src})

set(STAGES
  {stages_list}
)

foreach(stage ${{STAGES}})
  add_executable(${{stage}} ${{stage}}.cpp ${{SHARED_SRC}})
endforeach()
{extern_link_block}

# Cooperative auto-tagging for @hardware server stages.
{autofacade_link_block}
"""

    # ===== Shared header =====

    def _gen_shared_header(self) -> str:
        self.out = io.StringIO()
        guard = "NB_SHARED_H_"
        self.wl(f"#ifndef {guard}")
        self.wl(f"#define {guard}")
        self.wl("// Auto-generated by nbc from .nb source files")
        self.wl("// DO NOT EDIT — changes will be overwritten")
        self.blank()
        self.wl("#include <vector>")
        self.wl("#include <string>")
        self.wl("#include <filesystem>")
        self.wl("#include <cmath>")
        self.wl("#include <cassert>")
        self.wl("#include <iostream>")
        self.wl("#include <fstream>")
        self.wl("#include <set>")
        self.wl("#include <sstream>")
        self.wl("#include <iomanip>")
        self.wl("#include <algorithm>")
        self.wl("#include <functional>")
        self.blank()
        for inc in OPENFHE_INCLUDES:
            self.wl(f'#include "{inc}"')
        # Include support libraries based on requires capabilities
        if "replicate" in self.requires:
            self.wl('#include "slot_replication.h"')
        if "running_sums" in self.requires:
            self.wl('#include "running_sums.h"')
        self.blank()
        # External module includes
        for ext in self.externs:
            self.wl(f'#include "{ext.source}.h"')
        # Include headers for extern_call targets
        # e.g. mlp -> mlp_openfhe.h (model declaration), mlp_bridge.h (DSL bridge declaration)
        for fn in self.shared_fns:
            ext_name = self._get_extern_call_name(fn)
            if ext_name:
                self.wl(f'#include "{ext_name}_openfhe.h"')
                self.wl(f'#include "{ext_name}_bridge.h"')
        self.blank()
        self.wl("using namespace lbcrypto;")
        self.wl("namespace fs = std::filesystem;")
        self.blank()

        # Utility template: read2vecs
        self._gen_utility_templates()
        self.blank()

        # Constants
        for c in self.consts:
            cpp_type = self._type_to_cpp(c.type_ann)
            cpp_val = self._expr_to_cpp(c.value)
            self.wl(f"constexpr {cpp_type} {c.name} = {cpp_val};")
        if self.consts:
            self.blank()

        # Enums
        for e in self.enums:
            self.wl(f"enum {e.name} {{")
            self.indent()
            for i, v in enumerate(e.variants):
                comma = "," if i < len(e.variants) - 1 else ""
                self.wl(f"{v} = {i}{comma}")
            self.dedent()
            self.wl("};")
            self.blank()

        # Struct / Wire types as C++ structs
        # Eval key fields in CryptoParams are excluded — they are stored
        # in the CryptoContext and serialized via stream APIs
        SKIP_WIRE_FIELDS = {
            "CryptoParams": {"eval_mult_key", "eval_rot_keys"},
        }
        for s in self.structs + self.wires:
            skip_fields = SKIP_WIRE_FIELDS.get(s.name, set())
            self.wl(f"struct {s.name} {{")
            self.indent()
            for f in s.fields:
                if f.name in skip_fields:
                    continue
                cpp_type = self._type_to_cpp(f.type_ann)
                self.wl(f"{cpp_type} {f.name};")
            self.dedent()
            self.wl("};")
            self.blank()

        # EncryptedDB::Batch — flattened version for single-batch operations
        # Each batch has rows[dim] and payloads[dim] (one level less nesting)
        edb_wire = next((w for w in self.wires if w.name == "EncryptedDB"), None)
        if edb_wire:
            self.wl("struct EncryptedDBBatch {")
            self.indent()
            for f in edb_wire.fields:
                # Flatten: vec<vec<enc<...>>> → vec<enc<...>>
                cpp_type = self._type_to_cpp(f.type_ann)
                # Remove one level of std::vector nesting
                if cpp_type.startswith("std::vector<std::vector<"):
                    cpp_type = "std::vector<" + cpp_type[len("std::vector<std::vector<"):-1]
                self.wl(f"{cpp_type} {f.name};")
            self.dedent()
            self.wl("};")
            self.blank()

        # Forward declarations for shared functions (with default parameter values)
        # Skip extern wrappers — they delegate to external C++ functions
        for fn in self.shared_fns:
            if self._is_extern_wrapper(fn):
                continue
            self._gen_fn_decl(fn, with_cc=self._fn_uses_fhe(fn),
                              emit_defaults=True)
            self.w(";\n")

        # Forward declare load_kitnet_model if KitNETModel struct exists
        if any(s.name == "KitNETModel" for s in self.structs):
            self.wl("KitNETModel load_kitnet_model(std::filesystem::path path);")
        self.blank()

        self.wl(f"#endif  // {guard}")
        return self.out.getvalue()

    def _gen_utility_templates(self):
        """Generate utility template functions in the shared header."""
        # read2vecs - binary file reader
        self.wl("// Utility: read a binary file into a vector of vectors")
        self.wl("template<typename T>")
        self.wl("std::vector<std::vector<T>> read2vecs(std::filesystem::path fname, int record_dim) {")
        self.indent()
        self.wl("std::ifstream file(fname, std::ios::binary);")
        self.wl('if (!file.is_open()) throw std::runtime_error("Cannot open " + fname.string());')
        self.wl("file.seekg(0, std::ios::end);")
        self.wl("auto nbytes = file.tellg();")
        self.wl("file.seekg(0, std::ios::beg);")
        self.wl("auto nrecords = nbytes / (record_dim * sizeof(T));")
        self.wl("std::vector<std::vector<T>> result(nrecords);")
        self.wl("for (auto& r : result) {")
        self.indent()
        self.wl("r.resize(record_dim);")
        self.wl("file.read(reinterpret_cast<char*>(r.data()), record_dim * sizeof(T));")
        self.dedent()
        self.wl("}")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # read1vec - read single vector from binary file
        self.wl("template<typename T>")
        self.wl("std::vector<T> read1vec(std::filesystem::path fname, int dim) {")
        self.indent()
        self.wl("auto vecs = read2vecs<T>(fname, dim);")
        self.wl("if (vecs.empty()) return {};")
        self.wl("return vecs[0];")
        self.dedent()
        self.wl("}")
        self.blank()

        # read_text_matrix - read space-separated text file into vector of vectors
        self.wl("// Utility: read a text file (space-separated) into a vector of vectors")
        self.wl("template<typename T>")
        self.wl("std::vector<std::vector<T>> read_text_matrix(std::filesystem::path fname, int record_dim) {")
        self.indent()
        self.wl("std::ifstream file(fname);")
        self.wl('if (!file.is_open()) throw std::runtime_error("Cannot open " + fname.string());')
        self.wl("std::vector<std::vector<T>> result;")
        self.wl("std::string line;")
        self.wl("while (std::getline(file, line)) {")
        self.indent()
        self.wl("if (line.empty()) continue;")
        self.wl("std::istringstream iss(line);")
        self.wl("std::vector<T> row;")
        self.wl("T val;")
        self.wl("while (iss >> val) row.push_back(val);")
        self.wl("if ((int)row.size() >= record_dim) row.resize(record_dim);")
        self.wl("result.push_back(std::move(row));")
        self.dedent()
        self.wl("}")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # write2disk - binary file writer
        self.wl("template<typename T>")
        self.wl("void write2disk(const std::vector<std::vector<T>>& data, std::filesystem::path fname) {")
        self.indent()
        self.wl("std::ofstream file(fname, std::ios::binary);")
        self.wl('if (!file.is_open()) throw std::runtime_error("Cannot open " + fname.string());')
        self.wl("for (const auto& r : data)")
        self.wl("  file.write(reinterpret_cast<const char*>(r.data()), r.size() * sizeof(T));")
        self.dedent()
        self.wl("}")
        self.blank()

        # write2disk overload for flat vector
        self.wl("template<typename T>")
        self.wl("void write2disk(const std::vector<T>& data, std::filesystem::path fname) {")
        self.indent()
        self.wl("std::ofstream file(fname, std::ios::binary);")
        self.wl('if (!file.is_open()) throw std::runtime_error("Cannot open " + fname.string());')
        self.wl("file.write(reinterpret_cast<const char*>(data.data()), data.size() * sizeof(T));")
        self.dedent()
        self.wl("}")
        self.blank()

        # transpose_matrix
        self.wl("template<typename T>")
        self.wl("std::vector<std::vector<T>> transpose_matrix(const std::vector<std::vector<T>>& m) {")
        self.indent()
        self.wl("if (m.empty()) return {};")
        self.wl("size_t rows = m.size(), cols = m[0].size();")
        self.wl("std::vector<std::vector<T>> result(cols, std::vector<T>(rows));")
        self.wl("for (size_t i = 0; i < rows; i++)")
        self.wl("  for (size_t j = 0; j < cols; j++) result[j][i] = m[i][j];")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # batch_rows - split matrix rows into batches
        self.wl("template<typename T>")
        self.wl("std::vector<std::vector<std::vector<T>>> batch_rows(")
        self.wl("    const std::vector<std::vector<T>>& data, size_t batch_size) {")
        self.indent()
        self.wl("std::vector<std::vector<std::vector<T>>> result;")
        self.wl("for (size_t i = 0; i < data.size(); i += batch_size) {")
        self.indent()
        self.wl("size_t end = std::min(i + batch_size, data.size());")
        self.wl("result.emplace_back(data.begin() + i, data.begin() + end);")
        self.dedent()
        self.wl("}")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # tile - repeat a vector to fill n slots
        self.wl("template<typename T>")
        self.wl("std::vector<T> tile(const std::vector<T>& v, size_t n) {")
        self.indent()
        self.wl("std::vector<T> result(n);")
        self.wl("for (size_t i = 0; i < n; i++) result[i] = v[i % v.size()];")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # prepend_column - prepend a value as first column to each row
        self.wl("template<typename T, typename V>")
        self.wl("std::vector<std::vector<T>> prepend_column(")
        self.wl("    const std::vector<std::vector<T>>& data, V val) {")
        self.indent()
        self.wl("std::vector<std::vector<T>> result;")
        self.wl("for (const auto& row : data) {")
        self.indent()
        self.wl("std::vector<T> r = {static_cast<T>(val)};")
        self.wl("r.insert(r.end(), row.begin(), row.end());")
        self.wl("result.push_back(r);")
        self.dedent()
        self.wl("}")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # scale_matrix - multiply all elements by a factor (always returns double)
        self.wl("template<typename T>")
        self.wl("std::vector<std::vector<std::vector<double>>> scale_batched(")
        self.wl("    const std::vector<std::vector<std::vector<T>>>& batches, double factor) {")
        self.indent()
        self.wl("std::vector<std::vector<std::vector<double>>> result;")
        self.wl("for (auto& batch : batches) {")
        self.wl("  result.push_back({});")
        self.wl("  for (auto& row : batch) {")
        self.wl("    result.back().push_back({});")
        self.wl("    for (auto& val : row) result.back().back().push_back(val * factor);")
        self.wl("  }")
        self.wl("}")
        self.wl("return result;")
        self.dedent()
        self.wl("}")
        self.blank()

        # slot_mask - create a plaintext mask vector
        # Null-safe EvalAdd: handles accumulation into uninitialized ciphertexts
        self.wl("inline Ciphertext<DCRTPoly> NullSafeEvalAdd(")
        self.wl("    CryptoContext<DCRTPoly> cc, Ciphertext<DCRTPoly> a, Ciphertext<DCRTPoly> b) {")
        self.indent()
        self.wl("if (a == nullptr) return b;")
        self.wl("return cc->EvalAdd(a, b);")
        self.dedent()
        self.wl("}")
        self.blank()

        self.wl("inline std::vector<double> slot_mask(size_t n_slots, size_t n_cols,")
        self.wl("                                     size_t row_start, size_t row_end) {")
        self.indent()
        self.wl("std::vector<double> mask(n_slots, 0.0);")
        self.wl("for (size_t col = 0; col < n_cols; col++)")
        self.wl("  for (size_t row = row_start; row < row_end; row++)")
        self.wl("    mask[col + row * n_cols] = 1.0;")
        self.wl("return mask;")
        self.dedent()
        self.wl("}")
        self.blank()

        # root - project root directory
        self.wl("inline std::filesystem::path root() {")
        self.indent()
        self.wl("// Returns the project root directory")
        self.wl('auto p = std::filesystem::current_path();')
        self.wl("return p;")
        self.dedent()
        self.wl("}")
        self.blank()

        # vector_union - merge multiple int vectors into sorted unique set
        self.wl("template<typename T>")
        self.wl("std::vector<T> vector_union(std::vector<std::vector<T>>& vecs) {")
        self.indent()
        self.wl("std::set<T> s;")
        self.wl("for (const auto& v : vecs) s.insert(v.begin(), v.end());")
        self.wl("return std::vector<T>(s.begin(), s.end());")
        self.dedent()
        self.wl("}")

    # ===== Shared implementation =====

    def _gen_shared_impl(self) -> str:
        self.out = io.StringIO()
        self.wl("// Auto-generated by nbc from .nb source files")
        self.wl('#include "nb_shared.h"')
        self.blank()

        for fn in self.shared_fns:
            # Skip extern wrapper functions — their body is a single extern_call
            if self._is_extern_wrapper(fn):
                continue
            self._current_fn = fn
            self._local_var_cpp_types.clear()
            self._declared_vars.clear()
            self._gen_fn_impl(fn, with_cc=self._fn_uses_fhe(fn))
            self._current_fn = None
            self.blank()

        # Generate load_kitnet_model if KitNETModel struct exists
        if any(s.name == "KitNETModel" for s in self.structs):
            self._gen_load_kitnet_model()
            self.blank()

        return self.out.getvalue()

    def _gen_load_kitnet_model(self):
        """Generate load_kitnet_model() that reads the KitNET binary model format."""
        self.wl("KitNETModel load_kitnet_model(std::filesystem::path path) {")
        self.indent()
        self.wl("KitNETModel m;")
        self.wl("std::ifstream f(path, std::ios::binary);")
        self.wl('if (!f.is_open()) throw std::runtime_error("Cannot open model: " + path.string());')
        self.blank()
        # Phase 1: Header (7 x uint16)
        self.wl("// Read header (7 x uint16)")
        self.wl("uint16_t hdr[7];")
        self.wl("f.read(reinterpret_cast<char*>(hdr), sizeof(hdr));")
        self.wl("m.header.num_ae  = hdr[0];")
        self.wl("m.header.num_feat = hdr[1];")
        self.wl("m.header.vis_ae  = hdr[2];")
        self.wl("m.header.hid_ae  = hdr[3];")
        self.wl("m.header.vis_ad  = hdr[4];")
        self.wl("m.header.hid_ad  = hdr[5];")
        self.wl("m.header.apx_ord = hdr[6];")
        self.blank()
        # Phase 2: Chebyshev coefficients
        self.wl("// Read sigmoid coefficients (apx_ord+1 doubles, first doubled)")
        self.wl("for (int i = 0; i <= m.header.apx_ord; i++) {")
        self.indent()
        self.wl("double v; f.read(reinterpret_cast<char*>(&v), sizeof(v));")
        self.wl("m.sig_coeffs.push_back(i == 0 ? 2.0 * v : v);")
        self.dedent()
        self.wl("}")
        self.wl("// Read tanh coefficients (apx_ord+1 doubles)")
        self.wl("for (int i = 0; i <= m.header.apx_ord; i++) {")
        self.indent()
        self.wl("double v; f.read(reinterpret_cast<char*>(&v), sizeof(v));")
        self.wl("m.tanh_coeffs.push_back(v);")
        self.dedent()
        self.wl("}")
        self.blank()
        # Phase 3: Feature maps
        self.wl("// Read feature maps (num_ae x vis_ae x uint16)")
        self.wl("m.feature_map.resize(m.header.num_ae);")
        self.wl("for (int i = 0; i < m.header.num_ae; i++) {")
        self.indent()
        self.wl("for (int j = 0; j < m.header.vis_ae; j++) {")
        self.indent()
        self.wl("uint16_t idx; f.read(reinterpret_cast<char*>(&idx), sizeof(idx));")
        self.wl("m.feature_map[i].push_back(idx);")
        self.dedent()
        self.wl("}")
        self.dedent()
        self.wl("}")
        self.blank()
        # Phase 4: Autoencoders
        self.wl("// Read autoencoder weights")
        self.wl("m.ensemble.resize(m.header.num_ae);")
        self.wl("for (int k = 0; k < m.header.num_ae; k++) {")
        self.indent()
        self.wl("auto& ae = m.ensemble[k];")
        self.wl("ae.n_visible = m.header.vis_ae;")
        self.wl("ae.n_hidden = m.header.hid_ae;")
        self.wl("ae.W.resize(ae.n_visible, std::vector<double>(ae.n_hidden));")
        self.wl("for (uint32_t i = 0; i < ae.n_visible; i++)")
        self.indent()
        self.wl("for (uint32_t j = 0; j < ae.n_hidden; j++)")
        self.indent()
        self.wl("f.read(reinterpret_cast<char*>(&ae.W[i][j]), sizeof(double));")
        self.dedent()
        self.dedent()
        self.wl("ae.hbias.resize(ae.n_hidden);")
        self.wl("f.read(reinterpret_cast<char*>(ae.hbias.data()), ae.n_hidden * sizeof(double));")
        self.wl("ae.rbias.resize(ae.n_visible);")
        self.wl("f.read(reinterpret_cast<char*>(ae.rbias.data()), ae.n_visible * sizeof(double));")
        self.dedent()
        self.wl("}")
        self.blank()
        # Phase 5: Anomaly detector
        self.wl("// Read anomaly detector weights")
        self.wl("auto& ad = m.detector;")
        self.wl("ad.vis_dim = m.header.vis_ad;")
        self.wl("ad.hid_dim = m.header.hid_ad;")
        self.wl("ad.W.resize(ad.vis_dim, std::vector<double>(ad.hid_dim));")
        self.wl("for (uint32_t i = 0; i < ad.vis_dim; i++)")
        self.indent()
        self.wl("for (uint32_t j = 0; j < ad.hid_dim; j++)")
        self.indent()
        self.wl("f.read(reinterpret_cast<char*>(&ad.W[i][j]), sizeof(double));")
        self.dedent()
        self.dedent()
        self.wl("ad.hbias.resize(ad.hid_dim);")
        self.wl("f.read(reinterpret_cast<char*>(ad.hbias.data()), ad.hid_dim * sizeof(double));")
        self.wl("ad.rbias.resize(ad.vis_dim);")
        self.wl("f.read(reinterpret_cast<char*>(ad.rbias.data()), ad.vis_dim * sizeof(double));")
        self.blank()
        self.wl("return m;")
        self.dedent()
        self.wl("}")

    # ===== Stage file =====

    def _gen_stage_file(self, stage: StageInfo) -> str:
        self.out = io.StringIO()
        self.wl(f"// Auto-generated by nbc — stage: {stage.name}")
        self.wl(f"// Domain: {stage.domain.value}")
        self.wl("// DO NOT EDIT — changes will be overwritten")
        self.blank()
        self.wl('#include "nb_shared.h"')
        self.blank()

        if stage.hardware:
            self.wl(f'#include "{NIOBIUM_INCLUDE}"')
            self.blank()
            # File-scope hollow-recording flag, set from main()'s --hollow flag
            # and read by the recording bracket injected into the stage function.
            self.wl("static bool _nb_hollow_record = false;")
            self.blank()

        # Generate the stage function body. While the stage's own body is being
        # emitted, _current_stage_hardware drives tag_input() injection after
        # each input load() (see _gen_let_stmt).
        self._current_fn = stage.fn
        self._current_stage_hardware = bool(stage.hardware)
        self._local_var_cpp_types.clear()
        self._declared_vars.clear()
        self._gen_fn_impl(stage.fn, with_cc=(stage.domain == Domain.SERVER))
        self._current_stage_hardware = False
        self._current_fn = None
        self.blank()

        # Generate main()
        self._gen_main(stage)

        return self.out.getvalue()

    # ===== main() generation =====

    def _gen_main(self, stage: StageInfo):
        self.wl("int main(int argc, char* argv[]) {")
        self.indent()

        # Argument parsing
        self.wl("if (argc < 2 || !std::isdigit(argv[1][0])) {")
        self.indent()
        self.wl(f'std::cout << "Usage: " << argv[0] << " instance-size [options]\\n";')
        self.wl(f'std::cout << "  Instance-size: 0-4\\n";')
        self.wl("return 0;")
        self.dedent()
        self.wl("}")
        self.blank()

        has_instance_param = any(
            p.name == "inst" or (p.type_ann and isinstance(p.type_ann, ast.NamedType)
                                  and p.type_ann.name == "Instance")
            for p in stage.fn.params
        )

        if has_instance_param:
            # Find the enum type used by instance() function
            size_enum = "InstanceSize"  # default
            inst_fn = next((f for f in self.shared_fns if f.name == "instance"), None)
            if inst_fn and inst_fn.params:
                p0_type = inst_fn.params[0].type_ann
                if isinstance(p0_type, ast.NamedType):
                    size_enum = p0_type.name
            self.wl(f"auto size = static_cast<{size_enum}>(std::stoi(argv[1]));")
            self.wl("auto inst = instance(size);")
            self.blank()

        # Parse bool flags
        bool_params = [p for p in stage.fn.params
                       if p.type_ann and isinstance(p.type_ann, ast.PrimitiveType)
                       and p.type_ann.name == "bool"]
        # Extra flags for hardware stages. --hollow skips expensive polynomial
        # math during recording (structure + probes preserved); replay then
        # reconstructs the real values via the FHETCH simulator.
        if stage.hardware:
            self.wl("bool hollow_record = false;")
        for bp in bool_params:
            self.wl(f"bool {bp.name} = false;")

        self.wl("for (int i = 2; i < argc; i++) {")
        self.indent()
        self.wl("std::string arg = argv[i];")
        for bp in bool_params:
            self.wl(f'if (arg == "--{bp.name}") {{ {bp.name} = true; }}')
        if stage.hardware:
            self.wl('if (arg == "--hollow") { hollow_record = true; }')
        self.dedent()
        self.wl("}")
        self.blank()

        # Parse positional params (after instance-size) that aren't bool/inst
        pos_idx = 2  # argv[1] is instance-size
        for p in stage.fn.params:
            if p.name == "inst" or p.name == "batch_id":
                continue
            if p.type_ann and isinstance(p.type_ann, ast.PrimitiveType) and p.type_ann.name == "bool":
                continue
            if p.type_ann and isinstance(p.type_ann, ast.NamedType):
                # Enum parameter — parse as int and cast
                enum_name = p.type_ann.name
                if any(e.name == enum_name for e in self.enums):
                    if p.default is not None:
                        self.wl(f"{enum_name} {p.name} = static_cast<{enum_name}>((argc > {pos_idx}) ? std::stoi(argv[{pos_idx}]) : {self._expr_to_cpp(p.default)});")
                    else:
                        self.wl(f"{enum_name} {p.name} = static_cast<{enum_name}>(std::stoi(argv[{pos_idx}]));")
                    pos_idx += 1
                    continue
            if p.type_ann and isinstance(p.type_ann, ast.PrimitiveType):
                ptype = p.type_ann.name
                if ptype in ("f64", "f32", "double"):
                    default = self._expr_to_cpp(p.default) if p.default is not None else "0.0"
                    self.wl(f"double {p.name} = (argc > {pos_idx}) ? std::stod(argv[{pos_idx}]) : {default};")
                    pos_idx += 1
                    continue
                if ptype in ("u32", "i32", "u64", "i64", "int"):
                    default = self._expr_to_cpp(p.default) if p.default is not None else "0"
                    self.wl(f"int {p.name} = (argc > {pos_idx}) ? std::stoi(argv[{pos_idx}]) : {default};")
                    pos_idx += 1
                    continue

        # Parse batch_id if present
        batch_param = next((p for p in stage.fn.params if p.name == "batch_id"), None)
        if batch_param:
            self.wl("if (argc < 3 || !std::isdigit(argv[2][0])) {")
            self.indent()
            self.wl('std::cerr << "Missing batch_id argument\\n";')
            self.wl("return 1;")
            self.dedent()
            self.wl("}")
            self.wl("int batch_id = std::stoi(argv[2]);")
            self.blank()

        # Niobium hardware init
        if stage.hardware:
            self._gen_niobium_init(stage)

        # Key loading for @server stages
        if stage.domain == Domain.SERVER:
            self._gen_key_loading(stage)

        # Hand the --hollow flag to the recording bracket inside the stage
        # function (start() is emitted there, after the input loads, so inputs
        # are tagged before recording begins).
        if stage.hardware:
            self.wl("_nb_hollow_record = hollow_record;")
            self.blank()

        # Call the stage function
        call_args = []
        for p in stage.fn.params:
            if p.name == "inst":
                call_args.append("inst")
            elif p.name == "batch_id":
                call_args.append("batch_id")
            elif p.type_ann and isinstance(p.type_ann, ast.PrimitiveType) and p.type_ann.name == "bool":
                call_args.append(p.name)
            else:
                call_args.append(p.name)

        # Server stages get cc as first arg
        if stage.domain == Domain.SERVER:
            call_args = ["cc"] + call_args

        fn_name = stage.fn.name
        args_str = ", ".join(call_args)

        has_return = self._fn_has_return(stage.fn)
        if stage.hardware:
            # Record/replay gate — mirrors the canonical client integration
            # (fetch-by-similarity NIOBIUM_INTEGRATION.md): ALL FHE ops run only
            # on the record pass, which serializes OpenFHE's own result; a
            # cache-valid run executes ZERO FHE ops and reconstructs the output
            # from the cached trace via replay()/result().
            self.wl("const bool _nb_replaying = niobium::compiler().is_cache_valid();")
            if has_return:
                wire = self._stage_result_wire(stage)
                result_type = wire.name if wire else "Ciphertext<DCRTPoly>"
                self.wl(f"{result_type} result;")
            self.wl("if (!_nb_replaying) {")
            self.indent()
            if has_return:
                self.wl(f"result = {fn_name}({args_str});")
            else:
                self.wl(f"{fn_name}({args_str});")
            if has_return:
                self._gen_result_io(stage, "probe")
            self.wl("niobium::compiler().stop();")
            self.wl("niobium::compiler().enable_hollow_mode(false);")
            self.dedent()
            self.wl("} else {")
            self.indent()
            self.wl('std::cout << "[nb] Cached trace found — replaying (no FHE ops)" << std::endl;')
            self.wl("if (!niobium::compiler().replay()) {")
            self.indent()
            self.wl('std::cerr << "[ERROR] FHETCH replay failed!" << std::endl;')
            self.wl("return 1;")
            self.dedent()
            self.wl("}")
            if has_return:
                self._gen_result_io(stage, "rehydrate")
            self.dedent()
            self.wl("}")
        else:
            if has_return:
                self.wl(f"auto result = {fn_name}({args_str});")
            else:
                self.wl(f"{fn_name}({args_str});")

        # Save result — on a record run this is OpenFHE's own output; on a
        # replay run it was reconstructed from the cached trace.
        if has_return:
            self._gen_result_serialization(stage)

        self.blank()
        self.wl("return 0;")
        self.dedent()
        self.wl("}")

    def _walk_ast(self, node):
        """Yield every AST (dataclass) node in the subtree, recursing through
        lists/tuples. Used to find constructs anywhere in a function body,
        regardless of nesting (if/else, match arms, loops)."""
        if is_dataclass(node):
            yield node
            for f in fields(node):
                yield from self._walk_ast(getattr(node, f.name))
        elif isinstance(node, (list, tuple)):
            for item in node:
                yield from self._walk_ast(item)

    def _scheme_overrides(self, fn: ast.FnDecl | None):
        """Return a list of named-arg dicts for every scheme.override(...) call
        anywhere in fn's body (recursively)."""
        out = []
        if fn is None or not fn.body:
            return out
        for node in self._walk_ast(fn.body):
            if isinstance(node, ast.MethodCall) and node.method == "override":
                out.append({a.name: a.value for a in node.args if a.name})
        return out

    def _fn_has_scheme_override(self, fn: ast.FnDecl | None = None) -> bool:
        """True if fn contains a scheme.override(security: not_set) anywhere."""
        for ov in self._scheme_overrides(fn):
            v = ov.get("security")
            if isinstance(v, ast.Ident) and v.name == "not_set":
                return True
        return False

    def _fn_depth_override(self, fn: ast.FnDecl | None = None):
        """Return the AST expr for a scheme.override(depth: X) if present, else None."""
        for ov in self._scheme_overrides(fn):
            if "depth" in ov:
                return ov["depth"]
        return None

    def _scheme_depth(self) -> int:
        """Static multiplicative depth declared in the scheme block."""
        if self.scheme:
            for f in self.scheme.fields:
                if f.key == "depth":
                    return int(str(f.value).split()[0])
        return 23

    SEC_MAP = {
        "not_set": "HEStd_NotSet",
        "128-classic": "HEStd_128_classic",
        "128_classic": "HEStd_128_classic",
        "192-classic": "HEStd_192_classic",
        "256-classic": "HEStd_256_classic",
    }

    def _scheme_security_cpp(self) -> str:
        """The OpenFHE security-level enum for the scheme's declared security."""
        sec_val = "128-classic"
        if self.scheme:
            for f in self.scheme.fields:
                if f.key == "security":
                    sec_val = str(f.value)
        return self.SEC_MAP.get(sec_val, "HEStd_128_classic")

    def _fn_has_return(self, fn: ast.FnDecl) -> bool:
        """Check if a function has explicit return statements."""
        if fn.return_type:
            return True
        if fn.body:
            for stmt in fn.body.stmts:
                if isinstance(stmt, ast.ReturnStmt):
                    return True
                if isinstance(stmt, ast.IfStmt):
                    if self._block_has_return(stmt.then_block):
                        return True
        return False

    def _block_has_return(self, block) -> bool:
        if not block:
            return False
        if isinstance(block, ast.Block):
            for stmt in block.stmts:
                if isinstance(stmt, ast.ReturnStmt):
                    return True
        return False

    # ===== Result serialization (io_specs) =====

    def _gen_result_serialization(self, stage: StageInfo):
        """Generate serialization code for the stage's result based on io_specs."""
        writes_specs = [s for s in stage.io_specs
                        if s.kind in ("writes", "writes_plaintext")]
        if not writes_specs:
            self.wl("(void)result;")
            return

        for spec in writes_specs:
            if spec.kind == "writes_plaintext":
                self._gen_serialize_plaintext(stage, spec)
                continue
            for io_type in spec.types:
                type_name = io_type.type_name
                # Find the wire type definition
                wire = next((w for w in self.wires if w.name == type_name), None)
                if type_name == "CryptoParams":
                    self._gen_serialize_crypto_params(stage)
                elif io_type.index is not None and wire:
                    # Indexed wire type: IntermediateResult[batch_id]
                    self._gen_serialize_indexed_wire(stage, type_name, wire, io_type.index)
                elif wire:
                    self._gen_serialize_wire(stage, type_name, wire)
                else:
                    # Simple type — serialize directly
                    path_expr = (self._expr_to_cpp(io_type.path_expr)
                                 if io_type.path_expr else self._find_output_dir(stage))
                    fname = type_name.lower() + ".bin"
                    self.wl(f"Serial::SerializeToFile({path_expr} / \"{fname}\", result, SerType::BINARY);")

    def _gen_serialize_crypto_params(self, stage: StageInfo):
        """Generate serialization for CryptoParams wire type (special OpenFHE handling)."""
        key_dir = self._find_key_dir(stage)
        self.wl("// Serialize CryptoParams to individual files")
        self.wl(f"auto _dir = {key_dir};")
        self.wl("fs::create_directories(_dir);")
        self.wl("auto _cc = result.context;")
        self.wl('Serial::SerializeToFile(_dir / "cc.bin", _cc, SerType::BINARY);')
        self.wl('Serial::SerializeToFile(_dir / "pk.bin", result.public_key, SerType::BINARY);')
        self.wl("// Eval keys require stream-based serialization")
        self.wl('{')
        self.indent()
        self.wl('std::ofstream mk_file(_dir / "mk.bin", std::ios::out | std::ios::binary);')
        self.wl('_cc->SerializeEvalMultKey(mk_file, SerType::BINARY);')
        self.dedent()
        self.wl('}')
        self.wl('{')
        self.indent()
        self.wl('std::ofstream rk_file(_dir / "rk.bin", std::ios::out | std::ios::binary);')
        self.wl('_cc->SerializeEvalAutomorphismKey(rk_file, SerType::BINARY);')
        self.dedent()
        self.wl('}')

    def _gen_serialize_plaintext(self, stage: StageInfo, spec):
        """Generate serialization for writes_plaintext io_spec."""
        if spec.types:
            path_expr = self._expr_to_cpp(spec.types[0].path_expr) if spec.types[0].path_expr else "iodir(inst)"
        else:
            path_expr = "iodir(inst)"
        self.wl(f"// Write plaintext result to disk")
        self.wl(f"auto _out_path = {path_expr};")
        self.wl("fs::create_directories(_out_path.parent_path());")
        self.wl("write2disk(result, _out_path);")

    def _gen_serialize_wire(self, stage: StageInfo, type_name: str,
                            wire: ast.WireDecl):
        """Generate serialization for a generic wire type."""
        path_expr = self._find_output_dir(stage)

        if type_name == "EncryptedDB":
            self._gen_serialize_encrypted_db(path_expr)
            return
        if type_name == "EncryptedQuery":
            self.wl(f"// Serialize {type_name}")
            self.wl(f"auto _dir = {path_expr};")
            self.wl("fs::create_directories(_dir);")
            self.wl(f'Serial::SerializeToFile(_dir / "eqry.bin", result.query, SerType::BINARY);')
            return
        if type_name == "EncryptedResult":
            # Determine the field name from the wire definition
            ct_field = wire.fields[0].name if wire.fields else "result"
            self.wl(f"// Serialize {type_name}")
            self.wl(f"auto _dir = {path_expr};")
            self.wl("fs::create_directories(_dir);")
            self.wl(f'Serial::SerializeToFile(_dir / "eres.bin", result.{ct_field}, SerType::BINARY);')
            return

        # Generic wire type — field-by-field serialization
        self.wl(f"// Serialize {type_name}")
        self.wl(f"auto _dir = {path_expr};")
        self.wl("fs::create_directories(_dir);")
        for f in wire.fields:
            # Check if the field is a vector of ciphertexts (vec<enc<T>>)
            if (f.type_ann and isinstance(f.type_ann, ast.VecType)
                    and f.type_ann.elem and isinstance(f.type_ann.elem, ast.EncType)):
                # Serialize each ciphertext separately as field_N.bin
                self.wl(f"for (size_t _i = 0; _i < result.{f.name}.size(); _i++) {{")
                self.indent()
                self.wl(f'auto _fname = _dir / ("{f.name}_" + std::to_string(_i) + ".bin");')
                self.wl(f"Serial::SerializeToFile(_fname, result.{f.name}[_i], SerType::BINARY);")
                self.dedent()
                self.wl("}")
            else:
                fname = f"{type_name.lower()}_{f.name}.bin"
                self.wl(f'Serial::SerializeToFile(_dir / "{fname}", result.{f.name}, SerType::BINARY);')

    def _gen_serialize_encrypted_db(self, path_expr: str):
        """Generate serialization for EncryptedDB into batch directories."""
        self.wl("// Serialize EncryptedDB to batch directories")
        self.wl(f"auto _enc_dir = {path_expr};")
        self.wl("for (size_t _b = 0; _b < result.rows.size(); _b++) {")
        self.indent()
        self.wl('std::stringstream _ss; _ss << std::setw(4) << std::setfill(\'0\') << _b;')
        self.wl('auto _bdir = _enc_dir / ("batch" + _ss.str());')
        self.wl("fs::create_directories(_bdir);")
        self.wl("for (size_t _i = 0; _i < result.rows[_b].size(); _i++) {")
        self.indent()
        self.wl('std::stringstream ss; ss << std::setw(4) << std::setfill(\'0\') << _i;')
        self.wl('Serial::SerializeToFile(_bdir / ("row_" + ss.str() + ".bin"), result.rows[_b][_i], SerType::BINARY);')
        self.dedent()
        self.wl("}")
        self.wl("for (size_t _i = 0; _i < result.payloads[_b].size(); _i++) {")
        self.indent()
        self.wl('std::stringstream ss; ss << std::setw(4) << std::setfill(\'0\') << _i;')
        self.wl('Serial::SerializeToFile(_bdir / ("payload_" + ss.str() + ".bin"), result.payloads[_b][_i], SerType::BINARY);')
        self.dedent()
        self.wl("}")
        self.dedent()
        self.wl("}")

    def _find_output_dir(self, stage: StageInfo) -> str:
        """Find the output directory for a stage from writes io specs or shared functions."""
        # Check if ctxtdowndir or encdir functions exist
        fn_names = {f.name for f in self.shared_fns}
        if "ctxtdowndir" in fn_names:
            return "ctxtdowndir(inst)"
        if "encdir" in fn_names:
            return "encdir(inst)"
        return "iodir(inst)"

    def _gen_serialize_indexed_wire(self, stage: StageInfo, type_name: str,
                                     wire: ast.WireDecl, index_expr):
        """Generate serialization for indexed wire types like EncryptedResult[batch_id]."""
        idx_cpp = self._expr_to_cpp(index_expr) if isinstance(index_expr, ast.Expr) else str(index_expr)
        out_dir = self._find_output_dir(stage)

        # Determine filename prefix from type name
        # EncryptedResult -> "cipher_result_", IntermediateResult -> intermediate/
        if type_name in ("IntermediateResult",):
            self.wl(f"// Serialize {type_name}[{idx_cpp}] to intermediate directory")
            self.wl(f'auto _idir = {out_dir} / "intermediate";')
            file_pattern = f'std::to_string({idx_cpp}) + ".bin"'
        else:
            self.wl(f"// Serialize {type_name}[{idx_cpp}]")
            self.wl(f"auto _idir = {out_dir};")
            prefix = type_name.lower().replace("encrypted", "cipher_") + "_"
            file_pattern = f'"{prefix}" + std::to_string({idx_cpp}) + ".bin"'
        self.wl("fs::create_directories(_idir);")
        # For single-field wire types, serialize the field directly
        if len(wire.fields) == 1:
            f = wire.fields[0]
            self.wl(f'Serial::SerializeToFile(_idir / ({file_pattern}), result.{f.name}, SerType::BINARY);')
        else:
            for f in wire.fields:
                self.wl(f'Serial::SerializeToFile(_idir / ({file_pattern.replace(".bin", f"_{f.name}.bin")}), result.{f.name}, SerType::BINARY);')

    # ===== Niobium instrumentation generation =====

    def _gen_niobium_init(self, stage: StageInfo):
        self.wl("niobium::compiler().init(argc, argv);")
        # Cooperative (host-driven) auto-tagging: the host owns the
        # init/start/stop/probe/replay/result lifecycle, while input/key/context
        # tagging happens automatically via the instrumented-OpenFHE deserialize
        # hooks (provided by libniobium_client_autofacade). This anchors tagging
        # to deterministic deserialization points so input addresses align with
        # the recorded trace. Must be set before the CryptoContext is loaded.
        self.wl("niobium::compiler().enable_auto_tagging();")
        cache_keys = stage.hardware.get("cache_key", [])
        if cache_keys:
            self.wl("niobium::Compiler::CacheParameters nb_params;")
            for i, key in enumerate(cache_keys):
                self.wl(f'nb_params.push_back({{"{key}", argv[{i + 1}]}});')
            self.wl("niobium::compiler().cache_parameters(nb_params);")
        self.wl(f'niobium::compiler().set_program_info("{stage.name}", "1.0", '
                f'"Auto-generated from nb DSL");')
        self.wl("niobium::compiler().set_build_info(__FILE__, __LINE__, __TIMESTAMP__);")
        self.blank()

    def _find_key_dir(self, stage: StageInfo) -> str:
        """Extract the key directory from load(CryptoParams, from: ...) or shared functions."""
        stmts = stage.fn.body.stmts if isinstance(stage.fn.body, ast.Block) else []
        for stmt in stmts:
            if isinstance(stmt, ast.LetStmt) and stmt.value and isinstance(stmt.value, ast.CallExpr):
                call = stmt.value
                if isinstance(call.func, ast.Ident) and call.func.name == "load":
                    positional = [a for a in call.args if not a.name]
                    named = {a.name: a.value for a in call.args if a.name}
                    if positional and isinstance(positional[0].value, ast.Ident):
                        if positional[0].value.name == "CryptoParams" and "from" in named:
                            return self._expr_to_cpp(named["from"])
        # Fallback: check which key directory function exists
        fn_names = {f.name for f in self.shared_fns}
        if "pubkeydir" in fn_names:
            return "pubkeydir(inst)"
        return "keydir(inst)"

    def _gen_key_loading(self, stage: StageInfo):
        key_dir = self._find_key_dir(stage)
        self.wl("// Load crypto context and keys from disk")
        self.wl("CryptoContext<DCRTPoly> cc;")
        self.wl(f'if (!Serial::DeserializeFromFile({key_dir} / "cc.bin", cc, SerType::BINARY)) {{')
        self.indent()
        self.wl('throw std::runtime_error("Failed to load CryptoContext");')
        self.dedent()
        self.wl("}")
        self.blank()

        self.wl("PublicKey<DCRTPoly> pk;")
        self.wl(f'if (!Serial::DeserializeFromFile({key_dir} / "pk.bin", pk, SerType::BINARY)) {{')
        self.indent()
        self.wl('throw std::runtime_error("Failed to load PublicKey");')
        self.dedent()
        self.wl("}")
        self.blank()

        # Eval keys — plain OpenFHE deserialization. The FHETCH client captures
        # their polynomial data via tag_keys(cc) below.
        for key_file, key_method, key_type in [
            ("mk.bin", "DeserializeEvalMultKey", "EvalMult"),
            ("rk.bin", "DeserializeEvalAutomorphismKey", "EvalAutomorphism"),
        ]:
            self.wl(f'std::ifstream {key_type.lower()}_file({key_dir} / "{key_file}", '
                    f"std::ios::in | std::ios::binary);")
            self.wl(f"if (!{key_type.lower()}_file.is_open() || "
                    f"!cc->{key_method}({key_type.lower()}_file, SerType::BINARY)) {{")
            self.indent()
            self.wl(f'throw std::runtime_error("Failed to load {key_type} key");')
            self.dedent()
            self.wl("}")
            self.blank()

        # No explicit capture_crypto_context()/tag_keys()/tag_input() here: in
        # cooperative auto-tagging mode the instrumented-OpenFHE deserialize
        # hooks capture the context (on cc.bin load above), tag the eval keys,
        # and tag each input ciphertext as it is deserialized — at deterministic
        # points that keep record/replay addresses aligned.

    def _stage_result_wire(self, stage: StageInfo):
        """Return the wire definition this stage writes (for probe/rehydrate)."""
        for spec in stage.io_specs:
            if spec.kind == "writes":
                for io_type in spec.types:
                    wire = next((w for w in self.wires
                                 if w.name == io_type.type_name), None)
                    if wire:
                        return wire
        return None

    def _gen_result_io(self, stage: StageInfo, action: str):
        """Emit probe() (action='probe') or result() rehydration calls for every
        ciphertext in the stage's result wire. Each ciphertext gets a unique
        probe name so single, vector, and nested (DB) results all round-trip.
        The local wire struct is always named `result`."""
        wire = self._stage_result_wire(stage)
        if wire is None:
            # Unknown shape — treat the whole result as one ciphertext.
            self._emit_ct_io(action, '"result"', "result")
            return

        single_field = len(wire.fields) == 1
        for f in wire.fields:
            ann = f.type_ann
            base = "result" if single_field else f"result_{f.name}"
            if ann and isinstance(ann, ast.EncType):
                self._emit_ct_io(action, f'"{base}"', f"result.{f.name}")
            elif (ann and isinstance(ann, ast.VecType) and ann.elem
                  and isinstance(ann.elem, ast.EncType)):
                self.wl(f"for (size_t _i = 0; _i < result.{f.name}.size(); ++_i) {{")
                self.indent()
                self._emit_ct_io(action,
                                 f'"{base}_" + std::to_string(_i)',
                                 f"result.{f.name}[_i]")
                self.dedent()
                self.wl("}")
            elif (ann and isinstance(ann, ast.VecType) and ann.elem
                  and isinstance(ann.elem, ast.VecType)
                  and ann.elem.elem and isinstance(ann.elem.elem, ast.EncType)):
                # vec<vec<enc>> — e.g. EncryptedDB rows/payloads
                self.wl(f"for (size_t _b = 0; _b < result.{f.name}.size(); ++_b)")
                self.wl(f"for (size_t _i = 0; _i < result.{f.name}[_b].size(); ++_i) {{")
                self.indent()
                self._emit_ct_io(action,
                                 f'"{base}_" + std::to_string(_b) + "_" + std::to_string(_i)',
                                 f"result.{f.name}[_b][_i]")
                self.dedent()
                self.wl("}")

    def _emit_ct_io(self, action: str, name_expr: str, lvalue: str):
        if action == "probe":
            self.wl(f"niobium::compiler().probe({name_expr}, {lvalue});")
        else:
            self.wl(f"if (!niobium::compiler().result(cc, {name_expr}, {lvalue})) {{")
            self.indent()
            self.wl('std::cerr << "[ERROR] Result retrieval failed!" << std::endl;')
            self.wl("return 1;")
            self.dedent()
            self.wl("}")

    # ===== Function generation =====

    def _gen_fn_decl(self, fn: ast.FnDecl, with_cc: bool = False,
                     emit_defaults: bool = False):
        ret = self._fn_return_type(fn)
        params_list = []
        if with_cc:
            params_list.append("CryptoContext<DCRTPoly> cc")
        for p in fn.params:
            decl = f"{self._type_to_cpp(p.type_ann)} {p.name}"
            if emit_defaults and p.default is not None:
                decl += f" = {self._expr_to_cpp(p.default)}"
            params_list.append(decl)
        params = ", ".join(params_list)
        self.w(f"{ret} {fn.name}({params})")

    def _fn_return_type(self, fn: ast.FnDecl) -> str:
        """Determine the C++ return type for a function."""
        if fn.return_type:
            return self._type_to_cpp(fn.return_type)
        # Check if there's a return statement in the body
        if fn.body:
            for stmt in fn.body.stmts:
                if isinstance(stmt, ast.ReturnStmt) and stmt.value:
                    return "auto"
                if isinstance(stmt, ast.IfStmt):
                    if self._block_has_return(stmt.then_block):
                        return "auto"
        return "void"

    def _gen_fn_impl(self, fn: ast.FnDecl, with_cc: bool = False):
        self._gen_fn_decl(fn, with_cc=with_cc)
        self.w(" {\n")
        self.indent()
        # Emit mutable scheme parameters that scheme.override(...) can change,
        # initialized from the scheme declaration so it stays the source of
        # truth (e.g. non-Toy instances keep the declared security level; a Toy
        # branch may override it to not_set).
        if self._fn_has_scheme_override(fn):
            self.wl(f"auto _sec_level = {self._scheme_security_cpp()};")
        if self._fn_depth_override(fn) is not None:
            self.wl(f"auto _nb_depth = {self._scheme_depth()};")
        if fn.body:
            stmts = fn.body.stmts
            # Check for implicit return: if last statement is an ExprStmt
            # and function has a non-void return type, add implicit return
            if stmts and self._fn_return_type(fn) != "void":
                last = stmts[-1]
                if isinstance(last, ast.ExprStmt):
                    # Generate all but last, then add return for last
                    self._gen_stmts_with_record_start(stmts[:-1])
                    val = self._expr_to_cpp(last.expr)
                    self.wl(f"return {val};")
                else:
                    self._gen_stmts_with_record_start(stmts)
            else:
                self._gen_stmts_with_record_start(stmts)
        self.dedent()
        self.wl("}")

    def _gen_stmts_with_record_start(self, stmts):
        """Emit statements. In a @hardware stage, inject the FHETCH recording
        start after the leading input load()s (so their tag_input() calls run
        before start() and the input addresses align with the recorded trace),
        and before the first compute statement."""
        if not getattr(self, "_current_stage_hardware", False):
            for s in stmts:
                self._gen_stmt(s)
            return
        started = False
        for s in stmts:
            if not started and not self._is_input_load_let(s):
                self._emit_hw_record_start()
                started = True
            self._gen_stmt(s)
        if not started:
            # Degenerate: nothing but loads — still need to bracket the trace.
            self._emit_hw_record_start()

    def _is_input_load_let(self, stmt) -> bool:
        """True for `let x = load(...)` or `let x = load(...).field` bindings."""
        if not isinstance(stmt, ast.LetStmt) or stmt.value is None:
            return False
        val = stmt.value
        if isinstance(val, ast.FieldAccess):
            val = val.obj
        return (isinstance(val, ast.CallExpr) and isinstance(val.func, ast.Ident)
                and val.func.name == "load")

    def _emit_hw_record_start(self):
        # Inputs/keys/context are auto-tagged by the deserialize hooks during the
        # load()s above (cooperative mode). Begin recording once all inputs are
        # tagged and before the first compute statement.
        self.wl("if (!niobium::compiler().is_cache_valid()) {")
        self.indent()
        self.wl('std::cout << "[nb] Recording OpenFHE operations" << std::endl;')
        self.wl("niobium::compiler().enable_hollow_mode(_nb_hollow_record);")
        self.wl("niobium::compiler().start();")
        self.dedent()
        self.wl("}")
        self.blank()

    def _gen_block_contents(self, block: ast.Block):
        for stmt in block.stmts:
            self._gen_stmt(stmt)

    # ===== Statement generation =====

    def _gen_stmt(self, stmt: ast.Node):
        if isinstance(stmt, ast.LetStmt):
            self._gen_let_stmt(stmt)

        elif isinstance(stmt, ast.AssignStmt):
            target = self._expr_to_cpp(stmt.target)
            value = self._expr_to_cpp(stmt.value)
            self.wl(f"{target} = {value};")

        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                self._in_return_expr = True
                val = self._expr_to_cpp(stmt.value)
                self._in_return_expr = False
                self.wl(f"return {val};")
            else:
                self.wl("return;")

        elif isinstance(stmt, ast.AssertStmt):
            cond = self._expr_to_cpp(stmt.condition)
            if stmt.message:
                self.wl(f'if (!({cond})) {{ throw std::runtime_error("{stmt.message}"); }}')
            else:
                self.wl(f"assert({cond});")

        elif isinstance(stmt, ast.IfStmt):
            self._gen_if_stmt(stmt)

        elif isinstance(stmt, ast.ForStmt):
            self._gen_for(stmt)

        elif isinstance(stmt, ast.MatchStmt):
            self._gen_match(stmt)

        elif isinstance(stmt, ast.ExprStmt):
            val = self._expr_to_cpp(stmt.expr)
            self.wl(f"{val};")

    def _gen_let_stmt(self, stmt: ast.LetStmt):
        if stmt.type_ann:
            cpp_type = self._type_to_cpp(stmt.type_ann)
        else:
            cpp_type = "auto"

        if stmt.value:
            # Handle for-expression assigned to a let
            if isinstance(stmt.value, ast.ForExpr):
                self._gen_for_expr_as_stmt(stmt.name, cpp_type, stmt.value)
                return
            # Handle if-expression assigned to a let (destructuring)
            if isinstance(stmt.value, ast.IfExpr):
                self._current_tuple_names = stmt.tuple_names
                self._gen_if_expr_as_stmt(stmt.name, cpp_type, stmt.value)
                self._current_tuple_names = None
                return
            val = self._expr_to_cpp(stmt.value)
            # Track keygen result variables for field access mapping
            if (isinstance(stmt.value, ast.CallExpr) and
                isinstance(stmt.value.func, ast.Ident) and
                stmt.value.func.name == "keygen"):
                self._keygen_vars.add(stmt.name)
            # Handle zero() initialization — use nullptr for Ciphertext
            if isinstance(stmt.value, ast.CallExpr) and isinstance(stmt.value.func, ast.Ident):
                if stmt.value.func.name == "zero":
                    if cpp_type == "Ciphertext<DCRTPoly>":
                        self.wl(f"{cpp_type} {stmt.name};  // initialized to null")
                        return
            # Destructured binding: let (a, b) = expr → auto [a, b] = expr
            if stmt.tuple_names and len(stmt.tuple_names) > 1:
                names = ", ".join(stmt.tuple_names)
                self.wl(f"auto [{names}] = {val};")
                return
            # Track local variable types for later use (e.g. in ArrayLiteral inference)
            inferred_type = self._infer_expr_cpp_type(stmt.value)
            if inferred_type:
                self._local_var_cpp_types[stmt.name] = inferred_type
            # Let-rebinding: if variable already declared in this scope, use assignment
            if stmt.name in self._declared_vars:
                self.wl(f"{stmt.name} = {val};")
            else:
                self._declared_vars.add(stmt.name)
                self.wl(f"{cpp_type} {stmt.name} = {val};")
        else:
            self.wl(f"{cpp_type} {stmt.name};")


    def _gen_if_stmt(self, stmt: ast.IfStmt):
        cond = self._expr_to_cpp(stmt.condition)
        self.wl(f"if ({cond}) {{")
        self.indent()
        self._gen_block_contents(stmt.then_block)
        self.dedent()
        if stmt.else_block:
            if isinstance(stmt.else_block, ast.IfStmt):
                self.w("  " * self.indent_level + "} else ")
                self._gen_if_chain(stmt.else_block)
            else:
                self.wl("} else {")
                self.indent()
                self._gen_block_contents(stmt.else_block)
                self.dedent()
                self.wl("}")
        else:
            self.wl("}")

    def _gen_if_chain(self, stmt: ast.IfStmt):
        cond = self._expr_to_cpp(stmt.condition)
        self.w(f"if ({cond}) {{\n")
        self.indent()
        self._gen_block_contents(stmt.then_block)
        self.dedent()
        if stmt.else_block:
            if isinstance(stmt.else_block, ast.IfStmt):
                self.w("  " * self.indent_level + "} else ")
                self._gen_if_chain(stmt.else_block)
            else:
                self.wl("} else {")
                self.indent()
                self._gen_block_contents(stmt.else_block)
                self.dedent()
                self.wl("}")
        else:
            self.wl("}")

    def _gen_for(self, stmt: ast.ForStmt):
        if len(stmt.pattern.names) == 1:
            var = stmt.pattern.names[0]
            if isinstance(stmt.iterable, ast.RangeExpr):
                start = self._expr_to_cpp(stmt.iterable.start)
                end = self._expr_to_cpp(stmt.iterable.end)
                op = "<=" if stmt.iterable.inclusive else "<"
                self.wl(f"for (auto {var} = {start}; {var} {op} {end}; {var}++) {{")
            elif isinstance(stmt.iterable, ast.MethodCall) and stmt.iterable.method == "rev":
                # Handle (0..n).rev() — reverse range
                inner = stmt.iterable.obj
                if isinstance(inner, ast.RangeExpr):
                    start = self._expr_to_cpp(inner.start)
                    end = self._expr_to_cpp(inner.end)
                    self.wl(f"for (int {var} = {end} - 1; {var} >= {start}; {var}--) {{")
                else:
                    iterable = self._expr_to_cpp(stmt.iterable)
                    self.wl(f"for (auto& {var} : {iterable}) {{")
            else:
                iterable = self._expr_to_cpp(stmt.iterable)
                self.wl(f"for (auto& {var} : {iterable}) {{")
        else:
            idx, val = stmt.pattern.names
            if isinstance(stmt.iterable, ast.MethodCall) and stmt.iterable.method == "replicate":
                # Special case for slot replicator iteration
                obj = self._expr_to_cpp(stmt.iterable.obj)
                args = [self._expr_to_cpp(a.value) for a in stmt.iterable.args]
                self.wl(f"{{ size_t {idx} = 0;")
                self.wl(f"for (auto {val} = {obj}.init({', '.join(args)}); "
                        f"{val} != nullptr; "
                        f"{val} = {obj}.next_replica(), {idx}++) {{")
            elif isinstance(stmt.iterable, ast.CallExpr) and isinstance(stmt.iterable.func, ast.Ident) and stmt.iterable.func.name == "enumerate":
                # enumerate(collection) → index + value iteration
                inner_args = [self._expr_to_cpp(a.value) for a in stmt.iterable.args]
                collection = inner_args[0] if inner_args else "collection"
                self.wl(f"{{ size_t {idx} = 0;")
                self.wl(f"for (auto& {val} : {collection}) {{")
            else:
                iterable = self._expr_to_cpp(stmt.iterable)
                self.wl(f"{{ size_t {idx} = 0;")
                self.wl(f"for (auto& {val} : {iterable}) {{")

        self.indent()
        self._gen_block_contents(stmt.body)
        self.dedent()
        self.wl("}")

        if len(stmt.pattern.names) == 2:
            if not (isinstance(stmt.iterable, ast.MethodCall) and stmt.iterable.method == "replicate"):
                idx = stmt.pattern.names[0]
                self.wl(f"{idx}++;")
            self.wl("}")

    def _gen_match(self, stmt: ast.MatchStmt):
        subject = self._expr_to_cpp(stmt.subject)
        self.wl(f"switch ({subject}) {{")
        self.indent()
        for arm in stmt.arms:
            if isinstance(arm.pattern, ast.IdentPattern):
                if arm.pattern.name == "_":
                    self.wl("default: {")
                else:
                    self.wl(f"case {arm.pattern.name}: {{")
            elif isinstance(arm.pattern, ast.LiteralPattern):
                val = self._expr_to_cpp(arm.pattern.value)
                self.wl(f"case {val}: {{")
            else:
                self.wl(f"/* unhandled pattern */ {{")

            self.indent()
            if isinstance(arm.body, ast.Block):
                # For multi-statement blocks, emit all but last stmt normally,
                # then emit the last ExprStmt as a return
                stmts = arm.body.stmts
                if stmts:
                    for s in stmts[:-1]:
                        self._gen_stmt(s)
                    last = stmts[-1]
                    if isinstance(last, ast.ExprStmt):
                        val = self._expr_to_cpp(last.expr)
                        self.wl(f"return {val};")
                    elif isinstance(last, ast.ReturnStmt):
                        self._gen_stmt(last)
                    else:
                        self._gen_stmt(last)
            elif isinstance(arm.body, ast.ReturnStmt):
                self._gen_stmt(arm.body)
            else:
                val = self._expr_to_cpp(arm.body)
                self.wl(f"return {val};")
            self.wl("break;")
            self.dedent()
            self.wl("}")
        # Add default: __builtin_unreachable() if no default arm was generated
        has_default = any(
            isinstance(arm.pattern, ast.IdentPattern) and arm.pattern.name == "_"
            for arm in stmt.arms
        )
        if not has_default:
            self.wl("default: __builtin_unreachable();")
        self.dedent()
        self.wl("}")

    # ===== For-expression codegen (vector comprehension) =====

    def _gen_for_expr_as_stmt(self, name: str, cpp_type: str, expr: ast.ForExpr):
        """Generate a for-expression as a statement block producing a vector."""
        if len(expr.pattern.names) == 1:
            var = expr.pattern.names[0]
        else:
            var = expr.pattern.names[0]

        # Determine the loop body's last expression
        body_stmts = expr.body.stmts if expr.body else []

        if isinstance(expr.iterable, ast.RangeExpr):
            start = self._expr_to_cpp(expr.iterable.start)
            end = self._expr_to_cpp(expr.iterable.end)
            op = "<=" if expr.iterable.inclusive else "<"

            # Figure out the accumulation pattern from body
            if body_stmts:
                last = body_stmts[-1]
                # Simple transform: each iteration produces a value to collect
                if isinstance(last, ast.ExprStmt) or isinstance(last, ast.Expr):
                    # Determine element type — prefer explicit cast type to avoid
                    # decltype referencing the loop variable before it's in scope
                    last_ast = last.expr if isinstance(last, ast.ExprStmt) else last
                    elem_type = None
                    if isinstance(last_ast, ast.CastExpr) and last_ast.target_type:
                        elem_type = self._type_to_cpp(last_ast.target_type)
                    elif self._is_encrypted_expr(last_ast):
                        elem_type = "Ciphertext<DCRTPoly>"
                    if elem_type:
                        self.wl(f"std::vector<{elem_type}> {name};")
                    else:
                        self.wl(f"std::vector<decltype({self._expr_to_cpp(last_ast)})> {name};")
                    self.wl(f"for (auto {var} = {start}; {var} {op} {end}; {var}++) {{")
                    self.indent()
                    for s in body_stmts[:-1]:
                        self._gen_stmt(s)
                    last_expr = last.expr if isinstance(last, ast.ExprStmt) else last
                    val = self._expr_to_cpp(last_expr)
                    self.wl(f"{name}.push_back({val});")
                    self.dedent()
                    self.wl("}")
                    return

            # Fallback: generate as loop with auto type
            self.wl(f"{cpp_type} {name};")
            self.wl(f"for (auto {var} = {start}; {var} {op} {end}; {var}++) {{")
            self.indent()
            for s in body_stmts:
                self._gen_stmt(s)
            self.dedent()
            self.wl("}")
        else:
            iterable = self._expr_to_cpp(expr.iterable)
            # Transform loop: apply body to each element, collect results
            # Determine element type for vector declaration
            effective_type = cpp_type
            if effective_type == "auto" and body_stmts:
                last = body_stmts[-1]
                if isinstance(last, ast.ExprStmt):
                    if self._is_encrypted_expr(last.expr):
                        effective_type = "std::vector<Ciphertext<DCRTPoly>>"
                    else:
                        effective_type = "std::vector<decltype(0)>"
            # Also detect nested ForStmt as collecting expression
            if effective_type == "auto" and body_stmts:
                last = body_stmts[-1]
                if isinstance(last, ast.ForStmt):
                    effective_type = "std::vector<std::vector<Ciphertext<DCRTPoly>>>"
            self.wl(f"{effective_type} {name};")
            self.wl(f"for (auto& {var} : {iterable}) {{")
            self.indent()
            if body_stmts:
                for s in body_stmts[:-1]:
                    self._gen_stmt(s)
                last = body_stmts[-1]
                if isinstance(last, ast.ExprStmt):
                    val = self._expr_to_cpp(last.expr)
                    self.wl(f"{name}.push_back({val});")
                elif isinstance(last, ast.ForStmt):
                    # Nested for collecting results — convert to inline IIFE
                    inner_for_expr = ast.ForExpr(
                        loc=last.loc, pattern=last.pattern,
                        iterable=last.iterable, body=last.body)
                    val = self._gen_for_expr_inline(inner_for_expr)
                    self.wl(f"{name}.push_back({val});")
                else:
                    self._gen_stmt(last)
            self.dedent()
            self.wl("}")

    # ===== If-expression codegen =====

    def _gen_if_expr_as_stmt(self, name: str, cpp_type: str, expr: ast.IfExpr):
        """Generate an if-expression as a statement with branches."""
        cond = self._expr_to_cpp(expr.condition)

        # Try to extract simple values from then/else blocks
        then_val = self._block_result_expr(expr.then_block)
        else_val = self._block_result_expr(expr.else_block) if expr.else_block else None

        if then_val and else_val:
            then_cpp = self._expr_to_cpp(then_val)
            else_cpp = self._expr_to_cpp(else_val)
            # Check if these are tuple-like (array literals for destructuring)
            # Get the original tuple names from the LetStmt if available
            tuple_names = getattr(self, '_current_tuple_names', None)
            if isinstance(then_val, ast.ArrayLiteral) and tuple_names:
                parts = tuple_names
                self.wl(f"// Destructured if-expression")
                for i, p in enumerate(parts):
                    tv = self._expr_to_cpp(then_val.elements[i]) if i < len(then_val.elements) else "0"
                    ev = self._expr_to_cpp(else_val.elements[i]) if isinstance(else_val, ast.ArrayLiteral) and i < len(else_val.elements) else "0"
                    self.wl(f"auto {p} = ({cond}) ? {tv} : {ev};")
            else:
                self.wl(f"auto {name} = ({cond}) ? {then_cpp} : {else_cpp};")
        else:
            # Full block if-expression
            self.wl(f"{cpp_type} {name};")
            self.wl(f"if ({cond}) {{")
            self.indent()
            if expr.then_block:
                self._gen_block_contents(expr.then_block)
            self.dedent()
            if expr.else_block:
                self.wl("} else {")
                self.indent()
                if isinstance(expr.else_block, ast.Block):
                    self._gen_block_contents(expr.else_block)
                self.dedent()
            self.wl("}")

    def _block_result_expr(self, block) -> ast.Expr | None:
        """Extract the result expression from a block (last stmt if it's an ExprStmt)."""
        if isinstance(block, ast.Block) and block.stmts:
            last = block.stmts[-1]
            if isinstance(last, ast.ExprStmt):
                return last.expr
        if isinstance(block, ast.Expr):
            return block
        return None

    # ===== Expression generation =====

    def _expr_to_cpp(self, expr: ast.Expr | None) -> str:
        if expr is None:
            return ""

        if isinstance(expr, ast.IntLiteral):
            return str(expr.value)

        if isinstance(expr, ast.FloatLiteral):
            return str(expr.value)

        if isinstance(expr, ast.StringLiteral):
            return f'"{expr.value}"'

        if isinstance(expr, ast.BoolLiteral):
            return "true" if expr.value else "false"

        if isinstance(expr, ast.Ident):
            # Map operator-as-value identifiers
            if expr.name == "op_+":
                return "std::plus<>()"
            if expr.name == "op_*":
                return "std::multiplies<>()"
            return expr.name

        if isinstance(expr, ast.BinaryExpr):
            return self._gen_binary_expr(expr)

        if isinstance(expr, ast.UnaryExpr):
            operand = self._expr_to_cpp(expr.operand)
            if expr.op == "-":
                if self._is_encrypted_expr(expr.operand):
                    return f"cc->EvalNegate({operand})"
                return f"(-{operand})"
            return f"(!{operand})"

        if isinstance(expr, ast.CastExpr):
            inner = self._expr_to_cpp(expr.expr)
            target = self._type_to_cpp(expr.target_type)
            return f"static_cast<{target}>({inner})"

        if isinstance(expr, ast.PipeExpr):
            return self._gen_pipe_expr(expr)

        if isinstance(expr, ast.CallExpr):
            return self._gen_call_expr(expr)

        if isinstance(expr, ast.FieldAccess):
            obj = self._expr_to_cpp(expr.obj)
            field = expr.field_name
            # Map keygen result fields (KeyPair<DCRTPoly>)
            if isinstance(expr.obj, ast.Ident) and expr.obj.name in self._keygen_vars:
                kp_map = {
                    "context": f"{obj}.publicKey->GetCryptoContext()",
                    "public": f"{obj}.publicKey",
                    "public_key": f"{obj}.publicKey",
                    "secret": f"{obj}.secretKey",
                    # Eval keys are stored in the CryptoContext, not on the KeyPair
                    # They get serialized separately via stream APIs
                    "eval_mult": "nullptr /* eval keys in cc */",
                    "eval_rot": "nullptr /* eval keys in cc */",
                }
                if field in kp_map:
                    return kp_map[field]
            # Map DSL field names that conflict with C++ keywords
            if field == "public":
                field = "public_key"
            return f"{obj}.{field}"

        if isinstance(expr, ast.MethodCall):
            return self._gen_method_call(expr)

        if isinstance(expr, ast.IndexExpr):
            # Detect 2D slice: matrix[i..j, col] → extract column col from rows i..j
            if isinstance(expr.obj, ast.SliceExpr):
                mat = self._expr_to_cpp(expr.obj.obj)
                start = self._expr_to_cpp(expr.obj.start)
                end = self._expr_to_cpp(expr.obj.end)
                col = self._expr_to_cpp(expr.index)
                return (f"[&]() {{ auto& _m = {mat}; "
                        f"std::remove_reference_t<decltype(_m[0])> _col; "
                        f"for (auto _k = {start}; _k < {end}; _k++) "
                        f"_col.push_back(_m[_k][{col}]); return _col; }}()")
            obj = self._expr_to_cpp(expr.obj)
            idx = self._expr_to_cpp(expr.index)
            return f"{obj}[{idx}]"

        if isinstance(expr, ast.SliceExpr):
            obj = self._expr_to_cpp(expr.obj)
            start = self._expr_to_cpp(expr.start)
            end = self._expr_to_cpp(expr.end)
            return (f"std::vector<decltype({obj})::value_type>"
                    f"({obj}.begin() + {start}, {obj}.begin() + {end})")

        if isinstance(expr, ast.ArrayLiteral):
            elems = [self._expr_to_cpp(e) for e in expr.elements]
            # Check if this is a tuple expression (parsed from parenthesized comma-separated values)
            # If the current function has a TupleType return, generate std::make_pair/tuple
            # Only do this when we are actually in a return expression context
            if (self._in_return_expr
                    and self._current_fn and isinstance(self._current_fn.return_type, ast.TupleType)
                    and len(expr.elements) == len(self._current_fn.return_type.elements)):
                if len(elems) == 2:
                    return f"std::make_pair({elems[0]}, {elems[1]})"
                return f"std::make_tuple({', '.join(elems)})"
            # Detect nested array literals that need explicit vector types
            # (bare brace-init-lists can't be deduced by auto or returned)
            has_inner_array = any(isinstance(e, ast.ArrayLiteral) for e in expr.elements)
            if has_inner_array:
                # Infer inner element type from the first inner array's elements
                inner = expr.elements[0]
                if isinstance(inner, ast.ArrayLiteral) and inner.elements:
                    first_elem = inner.elements[0]
                    if isinstance(first_elem, ast.CastExpr) and first_elem.target_type:
                        inner_type = self._type_to_cpp(first_elem.target_type)
                    elif isinstance(first_elem, ast.Ident) and first_elem.name in self._local_var_cpp_types:
                        inner_type = self._local_var_cpp_types[first_elem.name]
                    elif isinstance(first_elem, ast.IntLiteral):
                        inner_type = "int64_t"
                    else:
                        inner_type = "double"
                    inner_strs = [self._expr_to_cpp(e) for e in expr.elements]
                    return (f"std::vector<std::vector<{inner_type}>>"
                            "{" + ", ".join(inner_strs) + "}")
            return "{" + ", ".join(elems) + "}"

        if isinstance(expr, ast.StructLiteral):
            # Skip fields that were excluded from the C++ struct definition
            SKIP_WIRE_FIELDS = {
                "CryptoParams": {"eval_mult_key", "eval_rot_keys"},
            }
            skip = SKIP_WIRE_FIELDS.get(expr.type_name, set())
            # Find wire definition for enc field detection
            wire_def = next((w for w in self.wires if w.name == expr.type_name), None)
            enc_fields = set()
            if wire_def:
                for wf in wire_def.fields:
                    if isinstance(wf.type_ann, ast.EncType):
                        enc_fields.add(wf.name)
            fields = []
            for fi in expr.fields:
                if fi.name in skip:
                    continue
                val = self._expr_to_cpp(fi.value) if fi.value else fi.name
                # Cast ConstCiphertext to Ciphertext for enc wire fields
                if fi.name in enc_fields:
                    val = f"std::const_pointer_cast<CiphertextImpl<DCRTPoly>>({val})"
                fields.append(f".{fi.name} = {val}")
            return f"{expr.type_name}{{{', '.join(fields)}}}"

        if isinstance(expr, ast.Closure):
            return self._gen_closure(expr)

        if isinstance(expr, ast.ForExpr):
            return self._gen_for_expr_inline(expr)

        if isinstance(expr, ast.IfExpr):
            return self._gen_if_expr_inline(expr)

        if isinstance(expr, ast.MatchExpr):
            return self._gen_match_expr_inline(expr)

        if isinstance(expr, ast.RangeExpr):
            # Ranges used inline (not in for loops) - shouldn't normally happen
            start = self._expr_to_cpp(expr.start)
            end = self._expr_to_cpp(expr.end)
            return f"/* range {start}..{end} */"

        return f"/* unknown expr: {type(expr).__name__} */"

    def _gen_binary_expr(self, expr: ast.BinaryExpr) -> str:
        left = self._expr_to_cpp(expr.left)
        right = self._expr_to_cpp(expr.right)

        # FHE-aware operator mapping: only when at least one operand is encrypted
        left_enc = self._is_encrypted_expr(expr.left)
        right_enc = self._is_encrypted_expr(expr.right)
        either_enc = left_enc or right_enc

        if either_enc:
            # When mixing ciphertext with plaintext vector, wrap vector in plaintext
            def _maybe_wrap_plaintext(cpp_str, is_enc, ast_expr):
                if is_enc:
                    return cpp_str
                # Check if the expression is likely a vector (function returning vector, etc.)
                if self._is_vector_expr(ast_expr):
                    return f"cc->MakeCKKSPackedPlaintext({cpp_str})"
                return cpp_str
            l = _maybe_wrap_plaintext(left, left_enc, expr.left)
            r = _maybe_wrap_plaintext(right, right_enc, expr.right)
            if expr.op == "+":
                # Use NullSafeEvalAdd only when both are ciphertexts;
                # for enc + scalar, use cc->EvalAdd directly
                if left_enc and right_enc:
                    return f"NullSafeEvalAdd(cc, {l}, {r})"
                return f"cc->EvalAdd({l}, {r})"
            if expr.op == "-":
                return f"cc->{FHE_SUB}({l}, {r})"
            if expr.op == "*":
                return f"cc->{FHE_MUL}({l}, {r})"
            if expr.op == "*_norelin":
                return f"cc->{FHE_MUL_NORELIN}({l}, {r})"

        # Plain *_norelin still maps to the FHE call
        if expr.op == "*_norelin":
            return f"cc->{FHE_MUL_NORELIN}({left}, {right})"

        if expr.op == "^":
            return f"std::pow({left}, {right})"
        if expr.op == "/":
            return f"({left} / {right})"

        cpp_op = CPP_OP_MAP.get(expr.op, expr.op)
        return f"({left} {cpp_op} {right})"

    def _gen_pipe_expr(self, expr: ast.PipeExpr) -> str:
        left = self._expr_to_cpp(expr.left)
        if isinstance(expr.right, ast.CallExpr):
            func = self._expr_to_cpp(expr.right.func)
            args = [left] + [self._expr_to_cpp(a.value) for a in expr.right.args]

            # Map piped DSL functions to C++ equivalents
            if isinstance(expr.right.func, ast.Ident):
                fname = expr.right.func.name
                if fname == "transpose":
                    return f"transpose_matrix({left})"
                if fname == "batch":
                    return f"batch_rows({left}, {args[1]})"
                if fname == "scale":
                    return f"scale_batched({left}, {args[1]})"
                if fname == "slot_sum":
                    return f"cc->EvalSum({left}, {args[1]})"

            return f"{func}({', '.join(args)})"
        # Bare Ident on the right side of pipe (e.g., x |> transpose)
        if isinstance(expr.right, ast.Ident):
            fname = expr.right.name
            if fname == "transpose":
                return f"transpose_matrix({left})"
            if fname == "relin":
                return (f"[&]() {{ auto tmp = {left}; "
                        f"cc->RelinearizeInPlace(tmp); return tmp; }}()")
            return f"{fname}({left})"
        right = self._expr_to_cpp(expr.right)
        return f"{right}({left})"

    def _gen_call_expr(self, expr: ast.CallExpr) -> str:
        func = self._expr_to_cpp(expr.func)
        args = [self._expr_to_cpp(a.value) for a in expr.args]
        named = {a.name: self._expr_to_cpp(a.value) for a in expr.args if a.name}

        if isinstance(expr.func, ast.Ident):
            fname = expr.func.name
            # Map built-in FHE functions
            if fname == "rotate":
                return f"cc->{FHE_ROTATE}({', '.join(args)})"
            if fname == "relin":
                # relin does in-place, but if used as expression value, wrap
                return (f"[&]() {{ auto tmp = {args[0]}; "
                        f"cc->{FHE_RELIN}(tmp); return tmp; }}()")
            if fname == "chebyshev":
                return self._gen_chebyshev(expr.args)
            if fname == "slot_sum":
                return f"cc->EvalSum({', '.join(args)})"
            if fname == "encrypt":
                return self._gen_encrypt(expr.args)
            if fname == "decrypt":
                return self._gen_decrypt(expr.args)
            if fname == "reduce":
                return self._gen_reduce(expr.args)
            if fname == "clone":
                # clone on a vector of ciphertexts: deep copy
                return (f"[&]() {{ auto v = {args[0]}; "
                        f"for (auto& ct : v) if (ct) ct = ct->Clone(); "
                        f"return v; }}()")
            if fname == "zero":
                return "Ciphertext<DCRTPoly>()"

            # Running sums (statement-level call, modifies in-place)
            if fname == "running_sums":
                stride_val = named.get("stride", args[1] if len(args) > 1 else "1")
                depth_val = named.get("depth", args[2] if len(args) > 2 else "0")
                return (f"[&]() {{ RunningSums _rs(cc, {stride_val}, {depth_val}); "
                        f"_rs.eval_in_place({args[0]}); }}()")

            # Standard library mappings
            if fname == "len":
                return f"{args[0]}.size()"
            if fname == "rows":
                return f"{args[0]}.size()"
            if fname == "round":
                return f"std::round({', '.join(args)})"
            if fname == "ceil_div":
                return f"(({args[0]} + {args[1]} - 1) / {args[1]})"
            if fname == "log2":
                return f"static_cast<int>(std::log2({args[0]}))"
            if fname == "exp":
                return f"std::exp({', '.join(args)})"
            if fname == "abs":
                return f"std::abs({', '.join(args)})"
            if fname == "sort":
                return (f"[&]() {{ auto v = {args[0]}; "
                        f"std::sort(v.begin(), v.end()); return v; }}()")
            if fname == "argmax":
                return (f"[&]() {{ auto v = {args[0]}; "
                        f"auto it = std::max_element(v.begin(), v.end()); "
                        f"return std::make_pair("
                        f"static_cast<uint32_t>(std::distance(v.begin(), it)), *it); }}()")
            if fname == "vec_zeros":
                # vec_zeros<T>(n) -> std::vector<T>(n)
                if expr.type_args:
                    inner = self._type_to_cpp(expr.type_args[0])
                    return f"std::vector<{inner}>({args[0]})"
                return f"std::vector<double>({args[0]}, 0.0)"
            if fname == "mat_zeros":
                if expr.type_args:
                    inner = self._type_to_cpp(expr.type_args[0])
                    return (f"std::vector<std::vector<{inner}>>"
                            f"({args[0]}, std::vector<{inner}>({args[1]}, 0))")
                return (f"std::vector<std::vector<double>>"
                        f"({args[0]}, std::vector<double>({args[1]}, 0.0))")
            if fname == "stride":
                return (f"[&]() {{ std::vector<int> r; "
                        f"for (auto i = {args[0]}; i < {args[1]}; i += {args[2]}) "
                        f"r.push_back(i); return r; }}()")
            if fname == "n_slots":
                return f"n_slots({args[0]})"
            if fname == "n_ctxts":
                return f"n_ctxts({args[0]})"
            if fname == "n_cols":
                return f"n_cols({args[0]})"
            if fname == "max_n_match":
                return f"max_n_match({args[0]})"
            if fname == "instance":
                return f"instance({args[0]})"
            if fname == "datadir":
                return f"datadir({args[0]})"
            if fname == "iodir":
                return f"iodir({args[0]})"
            if fname == "keydir":
                return f"keydir({args[0]})"
            if fname == "encdir":
                return f"encdir({args[0]})"
            if fname == "root":
                return "root()"

            # FHE built-in operations
            if fname == "negate":
                return f"cc->EvalNegate({args[0]})"
            if fname == "mul_monomial":
                return f"cc->GetScheme()->MultByMonomial({args[0]}, {args[1]})"

            # Type-parameterized functions
            if fname == "load_matrix":
                if expr.type_args:
                    inner = self._type_to_cpp(expr.type_args[0])
                    # Detect text vs binary by file extension in the path arg
                    path_arg = args[0] if args else ""
                    if '.txt' in path_arg or '.csv' in path_arg:
                        return f"read_text_matrix<{inner}>({', '.join(args)})"
                    return f"read2vecs<{inner}>({', '.join(args)})"
                return f"read2vecs<double>({', '.join(args)})"
            if fname == "load_vec":
                if expr.type_args:
                    inner = self._type_to_cpp(expr.type_args[0])
                    return f"read1vec<{inner}>({', '.join(args)})"
                return f"read1vec<double>({', '.join(args)})"

            if fname == "load_model":
                path = args[0] if args else '"model.bin"'
                return f"load_kitnet_model({path})"

            # DSL built-ins: load, save, print, etc.
            if fname == "load":
                return self._gen_load(expr.args)
            if fname == "load_all":
                return self._gen_load_all(expr.args)
            if fname == "save":
                return self._gen_save(expr.args)
            if fname == "save_secret_key":
                return self._gen_save_secret_key(expr.args)
            if fname == "load_secret_key":
                return self._gen_load_secret_key(expr.args)
            if fname == "print":
                return f"std::cout << {args[0]} << std::endl"
            if fname == "keygen":
                return self._gen_keygen()

            # Slot replicator construction — needs cc and vector<int> degrees
            if fname == "slot_replicator":
                input_reps = named.get("input_reps", args[1] if len(args) > 1 else "1")
                return (f"DFSSlotReplicator(cc, "
                        f"std::vector<int>({args[0]}.begin(), {args[0]}.end()), "
                        f"{input_reps})")

            # slot_mask with named args
            if fname == "slot_mask":
                row_range = named.get("row_range", "")
                if row_range:
                    # row_range is a range expression rendered as "/* range ... */"
                    # Try to extract from the actual AST instead
                    range_arg = next((a for a in expr.args if a.name == "row_range"), None)
                    if range_arg and isinstance(range_arg.value, ast.RangeExpr):
                        rstart = self._expr_to_cpp(range_arg.value.start)
                        rend = self._expr_to_cpp(range_arg.value.end)
                        return f"slot_mask({args[0]}, {args[1]}, {rstart}, {rend})"
                return f"slot_mask({', '.join(args)})"

            # map and zip_map
            if fname == "map":
                collection = args[0]
                fn_arg = args[1] if len(args) > 1 else ""
                # Check if the second arg is 'relin'
                relin_arg = expr.args[1].value if len(expr.args) > 1 else None
                if isinstance(relin_arg, ast.Ident) and relin_arg.name == "relin":
                    return (f"[&]() {{ auto v = {collection}; "
                            f"for (auto& ct : v) cc->RelinearizeInPlace(ct); "
                            f"return v; }}()")
                # Check for closure with FHE ops
                if isinstance(relin_arg, ast.Closure) and isinstance(relin_arg.body, ast.BinaryExpr):
                    op = relin_arg.body.op
                    p = relin_arg.params[0].name if relin_arg.params else "x"
                    right_val = self._expr_to_cpp(relin_arg.body.right)
                    if op == "-" and self._is_encrypted_expr(expr.args[0].value):
                        return (f"[&]() {{ decltype({collection}) r; "
                                f"for (auto& {p} : {collection}) "
                                f"r.push_back(cc->EvalSub({p}, {right_val})); "
                                f"return r; }}()")
                    if op == "+" and self._is_encrypted_expr(expr.args[0].value):
                        return (f"[&]() {{ decltype({collection}) r; "
                                f"for (auto& {p} : {collection}) "
                                f"r.push_back(cc->EvalAdd({p}, {right_val})); "
                                f"return r; }}()")
                    if op == "*" and self._is_encrypted_expr(expr.args[0].value):
                        return (f"[&]() {{ decltype({collection}) r; "
                                f"for (auto& {p} : {collection}) "
                                f"r.push_back(cc->EvalMult({p}, {right_val})); "
                                f"return r; }}()")
                return (f"[&]() {{ decltype({collection}) r; "
                        f"for (auto& x : {collection}) r.push_back({fn_arg}(x)); "
                        f"return r; }}()")

            if fname == "zip_map":
                a_coll = args[0]
                b_coll = args[1] if len(args) > 1 else ""
                # Get the closure AST to determine if we need FHE ops
                closure_arg = expr.args[2].value if len(expr.args) > 2 else None
                if isinstance(closure_arg, ast.Closure) and isinstance(closure_arg.body, ast.BinaryExpr):
                    op = closure_arg.body.op
                    p1 = closure_arg.params[0].name if closure_arg.params else "a"
                    p2 = closure_arg.params[1].name if len(closure_arg.params) > 1 else "b"
                    # FHE binary ops on ciphertext collections
                    if op == "*" and self._is_encrypted_expr(expr.args[0].value):
                        return (f"[&]() {{ decltype({a_coll}) r; "
                                f"for (size_t i = 0; i < {a_coll}.size(); i++) "
                                f"r.push_back(cc->EvalMult({a_coll}[i], {b_coll}[i])); "
                                f"return r; }}()")
                    if op == "+" and self._is_encrypted_expr(expr.args[0].value):
                        return (f"[&]() {{ decltype({a_coll}) r; "
                                f"for (size_t i = 0; i < {a_coll}.size(); i++) "
                                f"r.push_back(cc->EvalAdd({a_coll}[i], {b_coll}[i])); "
                                f"return r; }}()")
                fn_arg = args[2] if len(args) > 2 else ""
                return (f"[&]() {{ decltype({a_coll}) r; "
                        f"for (size_t i = 0; i < {a_coll}.size(); i++) "
                        f"r.push_back({fn_arg}({a_coll}[i], {b_coll}[i])); "
                        f"return r; }}()")

            # transpose and batch as standalone calls
            if fname == "transpose":
                return f"transpose_matrix({', '.join(args)})"
            if fname == "batch":
                return f"batch_rows({', '.join(args)})"
            if fname == "tile":
                return f"tile({', '.join(args)})"
            if fname == "str":
                return f"std::to_string({args[0]})"
            if fname == "prepend_column":
                return f"prepend_column({', '.join(args)})"
            if fname == "scale":
                return f"scale_batched({', '.join(args)})"

            # enumerate
            if fname == "enumerate":
                return args[0] if args else "/* enumerate */"

            # extern_call("func_name", arg1, arg2, ...) -> func_name(cc, arg1, arg2, ...)
            if fname == "extern_call":
                if args:
                    ext_name = args[0].strip('"')
                    ext_args = args[1:]
                    return f"{ext_name}(cc, {', '.join(ext_args)})" if ext_args else f"{ext_name}(cc)"
                return "/* extern_call: missing function name */"

            # FHE shared functions need cc as first arg
            # Check the hardcoded set first, then auto-detect from function signature
            if fname in FHE_SHARED_FNS:
                return f"{fname}(cc, {', '.join(args)})"
            fhe_fn = next((f for f in self.shared_fns if f.name == fname), None)
            if fhe_fn and self._fn_uses_fhe(fhe_fn):
                return f"{fname}(cc, {', '.join(args)})"

            # Extern wrapper functions: call the external C++ function with cc
            ext_fn = next((f for f in self.shared_fns
                           if f.name == fname and self._is_extern_wrapper(f)), None)
            if ext_fn:
                ext_name = self._get_extern_call_name(ext_fn) or fname
                return f"{ext_name}(cc, {', '.join(args)})"

        return f"{func}({', '.join(args)})"

    def _gen_method_call(self, expr: ast.MethodCall) -> str:
        obj = self._expr_to_cpp(expr.obj)
        args = [self._expr_to_cpp(a.value) for a in expr.args]
        method = expr.method

        # Map common methods
        if method == "push":
            return f"{obj}.push_back({', '.join(args)})"
        if method == "size":
            return f"{obj}.size()"
        if method == "rev":
            # Usually handled in for-loop context
            return f"{obj}"
        if method == "override":
            # scheme.override(security: not_set, depth: D, ring_dim: N) — assign
            # the corresponding mutable scheme variables (declared in _gen_fn_impl).
            # ring_dim override is intentionally a no-op: ring_dim is taken from
            # the Instance struct, which is the single source of truth.
            named_args = {}
            for a in expr.args:
                if a.name:
                    named_args[a.name] = self._expr_to_cpp(a.value)
            assigns = []
            if named_args.get("security") == "not_set":
                assigns.append("_sec_level = HEStd_NotSet")
            if "depth" in named_args:
                assigns.append(f"_nb_depth = {named_args['depth']}")
            if assigns:
                return ", ".join(assigns)
            return f"/* scheme.override({', '.join(args)}) */"
        if method == "replicate":
            return f"{obj}.replicate({', '.join(args)})"

        return f"{obj}.{method}({', '.join(args)})"

    def _gen_closure(self, expr: ast.Closure) -> str:
        params = []
        for p in expr.params:
            if p.type_ann:
                params.append(f"{self._type_to_cpp(p.type_ann)} {p.name}")
            else:
                params.append(f"auto {p.name}")
        if isinstance(expr.body, ast.Block):
            # Multi-statement closure
            saved_out = self.out
            saved_indent = self.indent_level
            self.out = io.StringIO()
            self.indent_level = 0
            self._gen_block_contents(expr.body)
            body_str = self.out.getvalue().rstrip()
            self.out = saved_out
            self.indent_level = saved_indent
            return f"[&]({', '.join(params)}) {{ {body_str} }}"
        else:
            body = self._expr_to_cpp(expr.body)
            return f"[&]({', '.join(params)}) {{ return {body}; }}"

    def _gen_for_expr_inline(self, expr: ast.ForExpr) -> str:
        """Generate a for-expression inline as an IIFE."""
        has_destructure = len(expr.pattern.names) == 2
        if has_destructure:
            idx_var = expr.pattern.names[0]
            val_var = expr.pattern.names[1]
        else:
            val_var = expr.pattern.names[0]
            idx_var = None

        body_stmts = expr.body.stmts if expr.body else []

        # Build a lambda
        saved_out = self.out
        saved_indent = self.indent_level
        self.out = io.StringIO()
        self.indent_level = 1

        # Determine element type from body
        last_expr_str = ""
        inner_is_for = False
        if body_stmts:
            last = body_stmts[-1]
            if isinstance(last, ast.ExprStmt):
                last_expr_str = self._expr_to_cpp(last.expr)
            elif isinstance(last, ast.ForStmt):
                # Nested for-statement that collects results (e.g., nested encrypt loops)
                # Convert to ForExpr and generate as inline IIFE
                inner_for_expr = ast.ForExpr(
                    loc=last.loc, pattern=last.pattern,
                    iterable=last.iterable, body=last.body)
                last_expr_str = self._gen_for_expr_inline(inner_for_expr)
                inner_is_for = True

        # Determine result type hint for the vector
        # Use Ciphertext<DCRTPoly> for FHE functions, otherwise auto-deduced
        result_elem_type = "auto"
        if last_expr_str:
            if inner_is_for:
                # Nested for produces a vector; outer collects vectors of vectors
                result_elem_type = "std::vector<Ciphertext<DCRTPoly>>"
            elif body_stmts:
                last = body_stmts[-1]
                last_ast = last.expr if isinstance(last, ast.ExprStmt) else last
                if self._is_encrypted_expr(last_ast):
                    result_elem_type = "Ciphertext<DCRTPoly>"

        # Helper to generate result vector type
        def result_type_decl():
            if result_elem_type == "auto":
                return "std::vector<decltype(0)> _result;  // auto-deduced"
            return f"std::vector<{result_elem_type}> _result;"

        # Helper to wrap body with optional index tracking
        def gen_loop_body(include_push=True):
            if has_destructure:
                self.wl(f"size_t {idx_var} = 0;")
            for s in (body_stmts[:-1] if include_push and last_expr_str else body_stmts):
                self._gen_stmt(s)
            if include_push and last_expr_str:
                self.wl(f"_result.push_back({last_expr_str});")
            if has_destructure:
                self.wl(f"{idx_var}++;")

        if isinstance(expr.iterable, ast.RangeExpr):
            start = self._expr_to_cpp(expr.iterable.start)
            end = self._expr_to_cpp(expr.iterable.end)
            op = "<=" if expr.iterable.inclusive else "<"

            if last_expr_str:
                self.wl(result_type_decl())
                self.wl(f"for (auto {val_var} = {start}; {val_var} {op} {end}; {val_var}++) {{")
                self.indent()
                gen_loop_body(include_push=True)
                self.dedent()
                self.wl("}")
                self.wl("return _result;")
            else:
                self.wl(f"for (auto {val_var} = {start}; {val_var} {op} {end}; {val_var}++) {{")
                self.indent()
                gen_loop_body(include_push=False)
                self.dedent()
                self.wl("}")
        else:
            # Resolve enumerate() to get the actual collection
            iterable_expr = expr.iterable
            if (isinstance(iterable_expr, ast.CallExpr) and
                isinstance(iterable_expr.func, ast.Ident) and
                iterable_expr.func.name == "enumerate"):
                # enumerate(coll) → iterate over coll with index
                iterable = self._expr_to_cpp(iterable_expr.args[0].value) if iterable_expr.args else "/* enumerate */"
            else:
                iterable = self._expr_to_cpp(iterable_expr)

            if last_expr_str:
                self.wl(result_type_decl())
                if has_destructure:
                    self.wl(f"{{ size_t {idx_var} = 0;")
                self.wl(f"for (auto& {val_var} : {iterable}) {{")
                self.indent()
                for s in body_stmts[:-1]:
                    self._gen_stmt(s)
                self.wl(f"_result.push_back({last_expr_str});")
                if has_destructure:
                    self.wl(f"{idx_var}++;")
                self.dedent()
                self.wl("}")
                if has_destructure:
                    self.wl("}")
                self.wl("return _result;")
            else:
                if has_destructure:
                    self.wl(f"{{ size_t {idx_var} = 0;")
                self.wl(f"for (auto& {val_var} : {iterable}) {{")
                self.indent()
                for s in body_stmts:
                    self._gen_stmt(s)
                if has_destructure:
                    self.wl(f"{idx_var}++;")
                self.dedent()
                self.wl("}")
                if has_destructure:
                    self.wl("}")

        loop_code = self.out.getvalue()
        self.out = saved_out
        self.indent_level = saved_indent

        return f"[&]() {{ {loop_code.strip()} }}()"

    def _gen_if_expr_inline(self, expr: ast.IfExpr) -> str:
        """Generate an if-expression as a ternary or IIFE."""
        cond = self._expr_to_cpp(expr.condition)
        then_val = self._block_result_expr(expr.then_block)
        else_val = self._block_result_expr(expr.else_block) if expr.else_block else None

        if then_val and else_val:
            then_cpp = self._expr_to_cpp(then_val)
            else_cpp = self._expr_to_cpp(else_val)
            return f"(({cond}) ? {then_cpp} : {else_cpp})"

        return f"/* if-expr */"

    def _gen_match_expr_inline(self, expr: ast.MatchExpr) -> str:
        """Generate a match expression inline as a lambda with switch."""
        subject = self._expr_to_cpp(expr.subject)
        # For multi-statement arms, generate the lambda body using the
        # normal block codegen infrastructure (so let-rebinding etc. work).
        saved_out = self.out
        saved_indent = self.indent_level
        self.out = io.StringIO()
        self.indent_level = 0

        self.wl(f"switch ({subject}) {{")
        self.indent()
        for arm in expr.arms:
            if isinstance(arm.pattern, ast.IdentPattern):
                if arm.pattern.name == "_":
                    self.wl("default: {")
                else:
                    self.wl(f"case {arm.pattern.name}: {{")
            elif isinstance(arm.pattern, ast.LiteralPattern):
                self.wl(f"case {self._expr_to_cpp(arm.pattern.value)}: {{")
            else:
                self.wl("default: {")

            self.indent()
            if isinstance(arm.body, ast.Block):
                stmts = arm.body.stmts
                if stmts:
                    for s in stmts[:-1]:
                        self._gen_stmt(s)
                    last = stmts[-1]
                    if isinstance(last, ast.ExprStmt):
                        val = self._expr_to_cpp(last.expr)
                        self.wl(f"return {val};")
                    elif isinstance(last, ast.ReturnStmt):
                        self._gen_stmt(last)
                    else:
                        self._gen_stmt(last)
            elif isinstance(arm.body, ast.Expr):
                self.wl(f"return {self._expr_to_cpp(arm.body)};")
            self.wl("break; }")
            self.dedent()

        has_default = any(
            isinstance(arm.pattern, ast.IdentPattern) and arm.pattern.name == "_"
            for arm in expr.arms
        )
        if not has_default:
            self.wl("default: __builtin_unreachable();")
        self.dedent()
        self.wl("}")

        switch_code = self.out.getvalue()
        self.out = saved_out
        self.indent_level = saved_indent

        return f"[&]() {{ {switch_code.strip()} }}()"

    # ===== FHE-specific code generation helpers =====

    def _gen_chebyshev(self, args: list[ast.Arg]) -> str:
        named = {a.name: self._expr_to_cpp(a.value) for a in args if a.name}
        positional = [self._expr_to_cpp(a.value) for a in args if not a.name]

        func = positional[0] if len(positional) > 0 else "func"
        ct = positional[1] if len(positional) > 1 else named.get("ct", "ct")
        domain = named.get("domain", "{-1.0, 1.0}")
        degree = named.get("degree", "59")

        lower, upper = "-1.0", "1.0"
        if domain.startswith("{") or domain.startswith("["):
            clean = domain.strip("{}[]")
            parts = clean.split(",")
            if len(parts) == 2:
                lower, upper = parts[0].strip(), parts[1].strip()

        return f"cc->EvalChebyshevFunction({func}, {ct}, {lower}, {upper}, {degree})"

    def _gen_encrypt(self, args: list[ast.Arg]) -> str:
        named = {a.name: self._expr_to_cpp(a.value) for a in args if a.name}
        positional_args = [a for a in args if not a.name]
        positional = [self._expr_to_cpp(a.value) for a in positional_args]
        pk = positional[0] if positional else "pk"
        data = positional[1] if len(positional) > 1 else "data"
        level = named.get("level", "0")
        # Determine how to construct the data vector:
        # If the source is an array literal like [val], use brace-init: std::vector<double>{val}
        # Otherwise assume it's already a vector and copy-construct
        data_ast = positional_args[1].value if len(positional_args) > 1 else None
        if isinstance(data_ast, ast.ArrayLiteral):
            data_init = f"std::vector<double>{data}"
        else:
            # Use iterator construction to handle type conversion (e.g. float→double)
            data_init = f"std::vector<double>({data}.begin(), {data}.end())"
        return (f"[&]() {{ auto _cc = {pk}->GetCryptoContext(); "
                f"auto _data = {data_init}; "
                f"return _cc->Encrypt({pk}, "
                f"_cc->MakeCKKSPackedPlaintext(_data, 1, {level})); }}()")

    def _gen_decrypt(self, args: list[ast.Arg]) -> str:
        positional = [self._expr_to_cpp(a.value) for a in args if not a.name]
        sk = positional[0] if positional else "sk"
        ct = positional[1] if len(positional) > 1 else "ct"
        return (f"[&]() {{ Plaintext pt; "
                f"{sk}->GetCryptoContext()->Decrypt({sk}, {ct}, &pt); "
                f"return pt->GetRealPackedValue(); }}()")

    def _gen_reduce(self, args: list[ast.Arg]) -> str:
        positional = [self._expr_to_cpp(a.value) for a in args if not a.name]
        op = positional[0] if positional else "std::plus<>()"
        vec = positional[1] if len(positional) > 1 else "vec"
        # For + on ciphertexts, use EvalAddInPlace accumulation
        if op in ("std::plus<>()", "+"):
            return (f"[&]() {{ auto acc = {vec}[0]; "
                    f"for (size_t i = 1; i < {vec}.size(); i++) "
                    f"cc->EvalAddInPlace(acc, {vec}[i]); "
                    f"return acc; }}()")
        return f"reduce({', '.join(positional)})"

    def _gen_load(self, args: list[ast.Arg]) -> str:
        """Generate serialization load for wire types."""
        positional = [a for a in args if not a.name]
        named = {a.name: self._expr_to_cpp(a.value) for a in args if a.name}
        from_path = named.get("from", "")

        type_name = "Unknown"
        index_expr = None
        if positional:
            type_expr = positional[0].value
            if isinstance(type_expr, ast.Ident):
                type_name = type_expr.name
            elif isinstance(type_expr, ast.IndexExpr):
                # EncryptedDB[batch_id] — indexed load
                if isinstance(type_expr.obj, ast.Ident):
                    type_name = type_expr.obj.name
                index_expr = self._expr_to_cpp(type_expr.index)
            else:
                type_name = self._expr_to_cpp(type_expr)

        # Generate deserialization based on the wire type
        if type_name == "CryptoParams":
            # In server functions, cc and eval keys are already loaded by main()
            if self._current_fn and any(
                si.fn.name == self._current_fn.name and si.domain == Domain.SERVER
                for si in self.stages
            ):
                return "CryptoParams{}"  # no-op: keys already loaded in main()
            return self._gen_load_crypto_params(from_path)
        if type_name == "EncryptedDB":
            # Load individual ciphertext files from batch directories
            load_batch = (
                f"[&](fs::path _bdir) {{ "
                f"std::vector<Ciphertext<DCRTPoly>> _rows, _payloads; "
                f"for (int _i = 0; ; _i++) {{ "
                f"  std::stringstream ss; ss << std::setw(4) << std::setfill('0') << _i; "
                f"  auto f = _bdir / (\"row_\" + ss.str() + \".bin\"); "
                f"  if (!fs::exists(f)) break; "
                f"  Ciphertext<DCRTPoly> ct; Serial::DeserializeFromFile(f, ct, SerType::BINARY); "
                f"  _rows.push_back(ct); }} "
                f"for (int _i = 0; ; _i++) {{ "
                f"  std::stringstream ss; ss << std::setw(4) << std::setfill('0') << _i; "
                f"  auto f = _bdir / (\"payload_\" + ss.str() + \".bin\"); "
                f"  if (!fs::exists(f)) break; "
                f"  Ciphertext<DCRTPoly> ct; Serial::DeserializeFromFile(f, ct, SerType::BINARY); "
                f"  _payloads.push_back(ct); }} "
                f"return std::make_pair(_rows, _payloads); }}"
            )
            if index_expr:
                # Single batch load → EncryptedDBBatch (flattened)
                return (f"[&]() {{ std::stringstream _ss; _ss << std::setw(4) << std::setfill('0') << {index_expr}; "
                        f"auto [_rows, _payloads] = {load_batch}({from_path} / (\"batch\" + _ss.str())); "
                        f"return EncryptedDBBatch{{_rows, _payloads}}; }}()")
            # Full DB load — all batches
            return (f"[&]() {{ EncryptedDB edb; "
                    f"for (int _b = 0; ; _b++) {{ "
                    f"  std::stringstream _ss; _ss << std::setw(4) << std::setfill('0') << _b; "
                    f"  auto _bdir = {from_path} / (\"batch\" + _ss.str()); "
                    f"  if (!fs::exists(_bdir)) break; "
                    f"  auto [_rows, _payloads] = {load_batch}(_bdir); "
                    f"  edb.rows.push_back(_rows); edb.payloads.push_back(_payloads); }} "
                    f"return edb; }}()")
        if type_name == "EncryptedQuery":
            return (f"[&]() {{ EncryptedQuery eq; "
                    f"Ciphertext<DCRTPoly> ct; "
                    f'Serial::DeserializeFromFile({from_path} / "eqry.bin", ct, SerType::BINARY); '
                    f"eq.query = ct; return eq; }}()")
        if type_name == "EncryptedResult":
            # Look up actual field name from wire definition
            wire_def = next((w for w in self.wires if w.name == "EncryptedResult"), None)
            field_name = "ciphertext"  # default
            if wire_def:
                ct_fields = [f for f in wire_def.fields
                             if (f.type_ann and isinstance(f.type_ann, ast.EncType))
                             or f.name in ("ciphertext", "score", "query", "result")]
                if ct_fields:
                    field_name = ct_fields[0].name
            # If the from: path looks like a specific file (contains .bin in a
            # string literal or concat), load from it directly. Otherwise
            # append "eres.bin" as the default filename within a directory.
            if '".bin"' in from_path or '.bin"' in from_path:
                return (f"[&]() {{ EncryptedResult er; "
                        f"Ciphertext<DCRTPoly> ct; "
                        f'Serial::DeserializeFromFile(fs::path({from_path}), ct, SerType::BINARY); '
                        f"er.{field_name} = ct; return er; }}()")
            return (f"[&]() {{ EncryptedResult er; "
                    f"Ciphertext<DCRTPoly> ct; "
                    f'Serial::DeserializeFromFile(fs::path({from_path}) / "eres.bin", ct, SerType::BINARY); '
                    f"er.{field_name} = ct; return er; }}()")

        # Generic wire type — look up the wire definition
        wire_def = next((w for w in self.wires if w.name == type_name), None)
        if wire_def and from_path:
            # Check for vec<enc<T>> fields (vector of ciphertexts)
            vec_ct_fields = [f for f in wire_def.fields
                             if (f.type_ann and isinstance(f.type_ann, ast.VecType)
                                 and f.type_ann.elem and isinstance(f.type_ann.elem, ast.EncType))]
            if vec_ct_fields:
                field = vec_ct_fields[0].name
                return (f"[&]() {{ {type_name} _w; "
                        f"for (int _i = 0; ; _i++) {{ "
                        f'auto _f = fs::path({from_path}) / ("{field}_" + std::to_string(_i) + ".bin"); '
                        f"if (!fs::exists(_f)) break; "
                        f"Ciphertext<DCRTPoly> _ct; Serial::DeserializeFromFile(_f, _ct, SerType::BINARY); "
                        f"_w.{field}.push_back(_ct); }} "
                        f"return _w; }}()")
            # Single ciphertext field — load directly from the given path
            # (matches _gen_save which writes directly to the to: path)
            ct_fields = [f for f in wire_def.fields
                         if (f.type_ann and isinstance(f.type_ann, ast.EncType))
                         or f.name in ("ciphertext", "score", "query", "result")]
            if len(ct_fields) == 1:
                field = ct_fields[0].name
                return (f"[&]() {{ {type_name} _w; "
                        f"Ciphertext<DCRTPoly> _ct; "
                        f'Serial::DeserializeFromFile(fs::path({from_path}), _ct, SerType::BINARY); '
                        f"_w.{field} = _ct; return _w; }}()")
            if len(ct_fields) > 1:
                # Multi-field enc wire type — load each field from {dir}/{field_name}.bin
                parts = [f"[&]() {{ {type_name} _w; auto _dir = fs::path({from_path}); "]
                for f in ct_fields:
                    parts.append(
                        f"{{ Ciphertext<DCRTPoly> _ct; "
                        f'Serial::DeserializeFromFile(_dir / "{f.name}.bin", _ct, SerType::BINARY); '
                        f"_w.{f.name} = _ct; }} ")
                parts.append("return _w; }()")
                return "".join(parts)
        return f"/* load({type_name}) */"

    def _gen_load_crypto_params(self, path: str) -> str:
        return (f"[&]() {{ CryptoParams p; "
                f'Serial::DeserializeFromFile({path} / "cc.bin", p.context, SerType::BINARY); '
                f'Serial::DeserializeFromFile({path} / "pk.bin", p.public_key, SerType::BINARY); '
                f"{{ std::ifstream mk({path} / \"mk.bin\", std::ios::binary); "
                f"p.context->DeserializeEvalMultKey(mk, SerType::BINARY); }} "
                f"{{ std::ifstream rk({path} / \"rk.bin\", std::ios::binary); "
                f"p.context->DeserializeEvalAutomorphismKey(rk, SerType::BINARY); }} "
                f"return p; }}()")

    def _gen_load_all(self, args: list[ast.Arg]) -> str:
        """Generate load_all for intermediate results."""
        positional = [a for a in args if not a.name]
        named = {a.name: self._expr_to_cpp(a.value) for a in args if a.name}
        from_path = named.get("from", "")
        return (f"[&]() {{ std::vector<Ciphertext<DCRTPoly>> results; "
                f"for (int i = 0; fs::exists({from_path} / (std::to_string(i) + \".bin\")); i++) {{ "
                f"Ciphertext<DCRTPoly> ct; "
                f'Serial::DeserializeFromFile({from_path} / (std::to_string(i) + ".bin"), ct, SerType::BINARY); '
                f"results.push_back(ct); "
                f"}} return results; }}()")

    def _gen_save(self, args: list[ast.Arg]) -> str:
        """Generate serialization for wire types: save(WireType{...}, to: path)."""
        positional = [a for a in args if not a.name]
        named = {a.name: self._expr_to_cpp(a.value) for a in args if a.name}
        to_path = named.get("to", "")
        if not to_path:
            return f"/* save: missing 'to:' argument */"
        data_expr = self._expr_to_cpp(positional[0].value) if positional else "data"

        # Determine wire type from the first positional argument
        type_name = None
        if positional and isinstance(positional[0].value, ast.StructLiteral):
            type_name = positional[0].value.type_name
        elif positional and isinstance(positional[0].value, ast.Ident):
            type_name = positional[0].value.name

        # Look up wire definition for single-ciphertext wire types
        if type_name:
            wire_def = next((w for w in self.wires if w.name == type_name), None)
            if wire_def:
                ct_fields = [f for f in wire_def.fields
                             if (f.type_ann and isinstance(f.type_ann, ast.EncType))
                             or f.name in ("ciphertext", "score", "query", "result")]
                if len(ct_fields) == 1:
                    field = ct_fields[0].name
                    return (f"[&]() {{ auto _dir = {to_path}; "
                            f"fs::create_directories(_dir.parent_path()); "
                            f"Serial::SerializeToFile(_dir, {data_expr}.{field}, SerType::BINARY); }}()")
                if len(ct_fields) > 1:
                    # Multi-field enc wire type — save each field to {dir}/{field_name}.bin
                    parts = [f"[&]() {{ auto _dir = fs::path({to_path}); "
                             f"fs::create_directories(_dir); "]
                    for f in ct_fields:
                        parts.append(
                            f'Serial::SerializeToFile(_dir / "{f.name}.bin", '
                            f"{data_expr}.{f.name}, SerType::BINARY); ")
                    parts.append("}()")
                    return "".join(parts)
        return f"Serial::SerializeToFile({to_path}, {data_expr}, SerType::BINARY)"

    def _gen_save_secret_key(self, args: list[ast.Arg]) -> str:
        positional = [self._expr_to_cpp(a.value) for a in args]
        sk = positional[0] if positional else "sk"
        path = positional[1] if len(positional) > 1 else '"sk.bin"'
        return (f"[&]() {{ auto _p = fs::path({path}); "
                f"fs::create_directories(_p.parent_path()); "
                f"Serial::SerializeToFile(_p, {sk}, SerType::BINARY); }}()")

    def _gen_load_secret_key(self, args: list[ast.Arg]) -> str:
        positional = [self._expr_to_cpp(a.value) for a in args]
        path = positional[0] if positional else '"sk.bin"'
        # OpenFHE requires the CryptoContext to be loaded first.
        # If pubkeydir is a separate function (different from sk dir), use it for cc.
        fn_names = {f.name for f in self.shared_fns}
        if "pubkeydir" in fn_names and "seckeydir" in fn_names:
            cc_dir = "pubkeydir(inst)"
        else:
            cc_dir = "_skpath.parent_path()"
        return (f"[&]() {{ auto _skpath = fs::path({path}); "
                f"CryptoContext<DCRTPoly> _cc; "
                f'Serial::DeserializeFromFile({cc_dir} / "cc.bin", _cc, SerType::BINARY); '
                f"PrivateKey<DCRTPoly> sk; "
                f"Serial::DeserializeFromFile(_skpath, sk, SerType::BINARY); "
                f"return sk; }}()")

    def _gen_keygen(self) -> str:
        """Generate key generation code as an IIFE returning a KeyPair."""
        # Check if the current function has a scheme.override with security: not_set
        has_security_override = self._fn_has_scheme_override(self._current_fn)
        # Determine which rotation keys are needed from requires capabilities
        has_replicate = "replicate" in self.requires
        has_running_sums = "running_sums" in self.requires

        # Read scheme configuration
        scheme_cfg = {}
        if self.scheme:
            for f in self.scheme.fields:
                scheme_cfg[f.key] = f.value

        # Security level — _sec_level (mutable) when an override is present,
        # otherwise the scheme's declared level.
        if has_security_override:
            sec_level = "_sec_level"
        else:
            sec_level = self._scheme_security_cpp()

        # Key distribution
        key_dist_val = scheme_cfg.get("key_dist", "uniform_ternary")
        key_dist_map = {
            "uniform_ternary": "UNIFORM_TERNARY",
            "sparse_ternary": "SPARSE_TERNARY",
            "gaussian": "GAUSSIAN",
        }
        key_dist = key_dist_map.get(str(key_dist_val), "UNIFORM_TERNARY")

        # Scaling technique
        scaling_val = scheme_cfg.get("scaling", "flexible_auto")
        scaling_map = {
            "flexible_auto": "FLEXIBLEAUTO",
            "fixed_auto": "FIXEDAUTO",
            "fixed_manual": "FIXEDMANUAL",
        }
        scaling = scaling_map.get(str(scaling_val), "FLEXIBLEAUTO")

        # Numeric parameters from scheme config. A scheme.override(depth: X)
        # makes the depth runtime-configurable via the mutable _nb_depth var.
        if self._fn_depth_override(self._current_fn) is not None:
            depth = "_nb_depth"
        else:
            depth = scheme_cfg.get("depth", 23)
        precision_raw = scheme_cfg.get("precision", 42)  # scaling_mod_size
        # Handle "54 bits" format
        precision = int(str(precision_raw).split()[0]) if precision_raw else 42
        first_mod_raw = scheme_cfg.get("first_mod", None)
        first_mod = int(str(first_mod_raw).split()[0]) if first_mod_raw else None

        lines = [
            "[&]() {",
            "    CCParams<CryptoContextCKKSRNS> parameters;",
            f"    parameters.SetSecretKeyDist({key_dist});",
            f"    parameters.SetSecurityLevel({sec_level});",
            f"    parameters.SetMultiplicativeDepth({depth});",
            f"    parameters.SetScalingModSize({precision});",
        ]
        if first_mod is not None:
            lines.append(f"    parameters.SetFirstModSize({first_mod});")
        ring_dim = scheme_cfg.get("ring_dim", None)
        # Does the Instance struct carry a ring_dim field (dynamic ring size)?
        inst_struct = next((s for s in self.structs if s.name == "Instance"), None)
        has_ring_dim_field = bool(inst_struct and any(
            f.name == "ring_dim" for f in inst_struct.fields))
        if ring_dim is not None:
            lines.append(f"    parameters.SetRingDim({ring_dim});")
        else:
            # Only emit SetRingDim if the Instance struct has a ring_dim field
            if has_ring_dim_field:
                lines.append("    parameters.SetRingDim(inst.ring_dim);")
        lines += [
            f"    parameters.SetScalingTechnique({scaling});",
            "    parameters.SetKeySwitchTechnique(HYBRID);",
            "    auto cc = GenCryptoContext(parameters);",
            "    cc->Enable(PKE); cc->Enable(KEYSWITCH);",
            "    cc->Enable(LEVELEDSHE); cc->Enable(ADVANCEDSHE);",
            "    auto kp = cc->KeyGen();",
            "    cc->EvalMultKeyGen(kp.secretKey);",
        ]
        # Generate rotation key indices from capabilities
        rot_parts = []
        has_rotate = "rotate" in self.requires
        if has_replicate:
            lines.append("    auto _degrees = std::vector<int>(inst.degrees.begin(), inst.degrees.end());")
            lines.append("    auto rots4reps = DFSSlotReplicator::get_rotation_amounts(_degrees);")
            rot_parts.append("rots4reps")
        if has_running_sums:
            lines.append("    auto shifts_rs = RunningSums::get_shift_amounts("
                         "n_slots(inst), n_cols(inst), RUNNING_SUM_LEVELS);")
            rot_parts.append("shifts_rs")
        # Generate keys for slot rotations requested via rotate(ct, k). When the
        # program also uses replicate/running_sums, those contribute their own
        # specific rotation indices (above) and we don't add the full range.
        # Otherwise (e.g. the `simple` example: bare rotate with a runtime index)
        # we generate the whole index set so any rotate(ct, k) has a key:
        #  - static ring_dim literal -> list indices at compile time;
        #  - ring_dim from the Instance struct -> build them at run time.
        # Limitation: covers positive indices 1..n_slots-1 only; a very large
        # ring makes this many keys, and negative indices aren't covered.
        if has_rotate and not has_replicate and not has_running_sums:
            if ring_dim is not None:
                n_slots = int(ring_dim) // 2
                rot_indices = ", ".join(str(i) for i in range(1, n_slots))
                lines.append(f"    std::vector<int> _rot_indices = {{{rot_indices}}};")
                rot_parts.append("_rot_indices")
            elif has_ring_dim_field:
                lines.append("    std::vector<int> _rot_indices;")
                lines.append("    for (int _i = 1; _i < (int)(inst.ring_dim / 2); ++_i) "
                             "_rot_indices.push_back(_i);")
                rot_parts.append("_rot_indices")
        if rot_parts:
            # Use EvalRotateKeyGen when 'rotate' capability is present,
            # EvalAtIndexKeyGen for legacy replicate/running_sums
            keygen_fn = "EvalRotateKeyGen" if has_rotate else "EvalAtIndexKeyGen"
            if len(rot_parts) == 1:
                lines.append(f"    cc->{keygen_fn}(kp.secretKey, {rot_parts[0]});")
            else:
                lines.append(f"    std::vector<std::vector<int>> _all_rots = {{{', '.join(rot_parts)}}};")
                lines.append(f"    cc->{keygen_fn}(kp.secretKey, vector_union(_all_rots));")
        lines.append("    cc->EvalSumKeyGen(kp.secretKey);")
        lines.append("    return kp;")
        lines.append("  }()")
        return "\n".join(lines)

    def _has_enc_type(self, type_ann: ast.TypeExpr | None) -> bool:
        """Check if a type annotation contains enc<...> anywhere."""
        if type_ann is None:
            return False
        if isinstance(type_ann, ast.EncType):
            return True
        if isinstance(type_ann, ast.VecType):
            return self._has_enc_type(type_ann.elem)
        if isinstance(type_ann, ast.TupleType):
            return any(self._has_enc_type(e) for e in type_ann.elements)
        return False

    def _is_encrypted_expr(self, expr: ast.Expr | None) -> bool:
        """Heuristic: check if an expression is likely encrypted."""
        if expr is None:
            return False
        if isinstance(expr, ast.Ident):
            sym = self.sa.global_scope.lookup(expr.name)
            if sym and hasattr(sym.type, 'is_encrypted') and sym.type.is_encrypted:
                return True
            # Check current function parameters for enc<T> type annotations
            if self._current_fn:
                for p in self._current_fn.params:
                    if p.name == expr.name and self._has_enc_type(p.type_ann):
                        return True
            # ALL_CAPS names are constants, never encrypted
            if expr.name.isupper() or expr.name.startswith("PAYLOAD") or expr.name.startswith("MAX_") or expr.name.startswith("RUNNING_") or expr.name.startswith("THRESHOLD"):
                return False
            name = expr.name.lower()
            if name in ENCRYPTED_EXACT_NAMES:
                return True
            if name.startswith(ENCRYPTED_PREFIXES):
                return True
        if isinstance(expr, ast.CallExpr) and isinstance(expr.func, ast.Ident):
            if expr.func.name in ENCRYPTED_RETURN_FNS:
                return True
            # Functions that are NOT encrypted
            if expr.func.name in PLAINTEXT_ONLY_FNS:
                return False
        if isinstance(expr, ast.FieldAccess):
            if expr.field_name in ("query", "result", "data"):
                return True
        if isinstance(expr, ast.BinaryExpr):
            return (self._is_encrypted_expr(expr.left) or
                    self._is_encrypted_expr(expr.right))
        if isinstance(expr, ast.IndexExpr):
            return self._is_encrypted_expr(expr.obj)
        # Method calls - check the object
        if isinstance(expr, ast.MethodCall):
            return self._is_encrypted_expr(expr.obj)
        # Pipe expressions - check the left side
        if isinstance(expr, ast.PipeExpr):
            # slot_sum, reduce produce encrypted results
            if isinstance(expr.right, ast.CallExpr) and isinstance(expr.right.func, ast.Ident):
                if expr.right.func.name in ("slot_sum",):
                    return True
            return self._is_encrypted_expr(expr.left)
        # Literals and numeric expressions are never encrypted
        if isinstance(expr, (ast.IntLiteral, ast.FloatLiteral,
                             ast.StringLiteral, ast.BoolLiteral)):
            return False
        return False

    # Functions that return std::vector<double> (plaintext vectors)
    VECTOR_RETURN_FNS = {"slot_mask", "tile", "to_matrix_form", "decode_payloads"}

    def _is_vector_expr(self, expr: ast.Expr | None) -> bool:
        """Heuristic: check if an expression produces a std::vector (not a scalar)."""
        if expr is None:
            return False
        if isinstance(expr, ast.CallExpr) and isinstance(expr.func, ast.Ident):
            return expr.func.name in self.VECTOR_RETURN_FNS
        if isinstance(expr, ast.Ident):
            # Check if this variable was assigned from a vector-returning function
            return self._local_var_cpp_types.get(expr.name, "").startswith("std::vector")
        if isinstance(expr, ast.ArrayLiteral):
            return True
        return False

    def _infer_expr_cpp_type(self, expr: ast.Expr | None) -> str | None:
        """Try to infer the C++ type of an expression."""
        if expr is None:
            return None
        if isinstance(expr, ast.CastExpr) and expr.target_type:
            return self._type_to_cpp(expr.target_type)
        if isinstance(expr, ast.CallExpr) and isinstance(expr.func, ast.Ident):
            if expr.func.name in self.VECTOR_RETURN_FNS:
                return "std::vector<double>"
            if expr.func.name == "round":
                return "double"
        if isinstance(expr, ast.IntLiteral):
            return "int64_t"
        if isinstance(expr, ast.FloatLiteral):
            return "double"
        return None

    # ===== Type mapping =====

    def _type_to_cpp(self, texpr: ast.TypeExpr | None) -> str:
        if texpr is None:
            return "auto"

        if isinstance(texpr, ast.PrimitiveType):
            return CPP_TYPE_MAP.get(texpr.name, texpr.name)

        if isinstance(texpr, ast.NamedType):
            name = texpr.name
            if texpr.sub:
                name += f"::{texpr.sub}"
            fhe_map = {
                "CryptoContext": "CryptoContext<DCRTPoly>",
                "PublicKey": "PublicKey<DCRTPoly>",
                "SecretKey": "PrivateKey<DCRTPoly>",
                "EvalMultKey": "std::shared_ptr<std::map<usint, EvalKey<DCRTPoly>>>",
                "EvalAutomorphismKeys": "std::shared_ptr<std::map<usint, EvalKey<DCRTPoly>>>",
                "KeyBundle": "KeyPair<DCRTPoly>",
                "Plaintext": "Plaintext",
                "EncryptedDB::Batch": "EncryptedDBBatch",
            }
            return fhe_map.get(name, name)

        if isinstance(texpr, ast.EncType):
            return "Ciphertext<DCRTPoly>"

        if isinstance(texpr, ast.VecType):
            elem = self._type_to_cpp(texpr.elem)
            return f"std::vector<{elem}>"

        if isinstance(texpr, ast.MatType):
            elem = self._type_to_cpp(texpr.elem)
            return f"std::vector<std::vector<{elem}>>"

        if isinstance(texpr, ast.TupleType):
            elems = ", ".join(self._type_to_cpp(e) for e in texpr.elements)
            return f"std::pair<{elems}>" if len(texpr.elements) == 2 else f"std::tuple<{elems}>"

        if isinstance(texpr, ast.FnType):
            ret = self._type_to_cpp(texpr.return_type)
            params = ", ".join(self._type_to_cpp(p) for p in texpr.param_types)
            return f"std::function<{ret}({params})>"

        return "auto"


def generate(program: ast.Program, analyzer: SemanticAnalyzer) -> dict[str, str]:
    """Convenience function to generate all C++ files."""
    gen = CodeGenerator(program, analyzer)
    return gen.generate_all()
