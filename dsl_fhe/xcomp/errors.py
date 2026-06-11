"""Error reporting for the nb compiler."""


class SourceLocation:
    """A position in source code."""

    __slots__ = ("file", "line", "col")

    def __init__(self, file: str = "<unknown>", line: int = 1, col: int = 1):
        self.file = file
        self.line = line
        self.col = col

    def __repr__(self):
        return f"{self.file}:{self.line}:{self.col}"


class CompileError(Exception):
    """Base class for all compilation errors."""

    def __init__(self, message: str, loc: SourceLocation | None = None):
        self.message = message
        self.loc = loc
        super().__init__(self.format())

    def format(self) -> str:
        if self.loc:
            return f"{self.loc}: error: {self.message}"
        return f"error: {self.message}"


class LexError(CompileError):
    """Error during lexical analysis."""
    pass


class ParseError(CompileError):
    """Error during parsing."""
    pass


class SemanticError(CompileError):
    """Error during semantic analysis."""
    pass


class DomainError(SemanticError):
    """Trust domain violation."""
    pass


class TypeError_(SemanticError):
    """Type checking error (underscore to avoid shadowing builtin)."""
    pass


class DepthError(SemanticError):
    """Multiplicative depth exceeded."""
    pass


class ErrorCollector:
    """Collects multiple errors before aborting."""

    def __init__(self):
        self.errors: list[CompileError] = []
        self.warnings: list[str] = []
        # Informational notes (parameter/accuracy advisories) — printed but
        # not counted as warnings.
        self.notes: list[str] = []

    def error(self, err: CompileError):
        self.errors.append(err)

    def warn(self, message: str, loc: SourceLocation | None = None):
        prefix = f"{loc}: " if loc else ""
        self.warnings.append(f"{prefix}warning: {message}")

    def note(self, message: str):
        self.notes.append(f"note: {message}")

    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def report(self) -> str:
        lines = []
        for w in self.warnings:
            lines.append(w)
        for e in self.errors:
            lines.append(e.format())
        if self.errors:
            lines.append(f"\n{len(self.errors)} error(s) found.")
        return "\n".join(lines)
