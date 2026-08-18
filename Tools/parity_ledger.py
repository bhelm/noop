#!/usr/bin/env python3
"""Inventory and gate declared Swift/Kotlin parity contracts.

The ledger is deliberately lexical. It does not compile either language and uses only
the Python standard library. A checked-in twin map records every file and function in
scope; a JSON baseline records known findings. Normal runs fail only for findings that
are not in that baseline.

Usage:
  python3 Tools/parity_ledger.py
  python3 Tools/parity_ledger.py --no-baseline
  python3 Tools/parity_ledger.py --bootstrap-map --write-baseline
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = ROOT / "Tools/parity_twin_map.json"
DEFAULT_BASELINE = ROOT / "Tools/parity_ledger_baseline.json"

SWIFT_GLOBS = (
    "Packages/StrandAnalytics/Sources/**/*.swift",
    "Packages/StrandImport/Sources/**/*.swift",
    "Packages/WhoopStore/Sources/**/*.swift",
    "Packages/WhoopProtocol/Sources/**/*.swift",
    "Packages/OuraProtocol/Sources/**/*.swift",
)
KOTLIN_GLOBS = (
    "android/app/src/main/java/com/noop/analytics/**/*.kt",
    "android/app/src/main/java/com/noop/ingest/**/*.kt",
    "android/app/src/main/java/com/noop/data/**/*.kt",
    "android/app/src/main/java/com/noop/protocol/**/*.kt",
    "android/app/src/main/java/com/noop/oura/**/*.kt",
)
SWIFT_EXCLUDED_GLOBS = (
    "Packages/NoopLocalAccess/Sources/**/*.swift",
    "Packages/PolarProtocol/Sources/**/*.swift",
    "Packages/StrandDesign/Sources/**/*.swift",
    "Strand/**/*.swift",
    "StrandiOS*/**/*.swift",
    "NOOPWatch*/**/*.swift",
)
KOTLIN_EXCLUDED_GLOBS = (
    "android/app/src/main/java/com/noop/*.kt",
    "android/app/src/main/java/com/noop/ai/**/*.kt",
    "android/app/src/main/java/com/noop/alarm/**/*.kt",
    "android/app/src/main/java/com/noop/ble/**/*.kt",
    "android/app/src/main/java/com/noop/location/**/*.kt",
    "android/app/src/main/java/com/noop/notif/**/*.kt",
    "android/app/src/main/java/com/noop/polar/**/*.kt",
    "android/app/src/main/java/com/noop/testcentre/**/*.kt",
    "android/app/src/main/java/com/noop/ui/**/*.kt",
    "android/app/src/main/java/com/noop/update/**/*.kt",
    "android/app/src/main/java/com/noop/widget/**/*.kt",
)
PRODUCTION_GLOBS = (
    "Packages/**/Sources/**/*.swift",
    "Strand/**/*.swift",
    "StrandiOS*/**/*.swift",
    "NOOPWatch*/**/*.swift",
    "android/app/src/main/java/**/*.kt",
)
TEST_GLOBS = (
    "Packages/**/Tests/**/*.swift",
    "StrandTests/**/*.swift",
    "android/app/src/test/**/*.kt",
    "android/app/src/androidTest/**/*.kt",
)
REFERENCE_GLOBS = (
    "Packages/**/*.swift",
    "Strand/**/*.swift",
    "StrandiOS*/**/*.swift",
    "NOOPWatch*/**/*.swift",
    "android/**/*.kt",
)


@dataclass(frozen=True)
class Declaration:
    language: str
    path: str
    name: str
    arity: int
    line: int
    ordinal: int = 1
    kind: str = "function"
    owner_name: str | None = None
    offset: int = -1
    opening: int = -1

    @property
    def key(self) -> str:
        if self.kind == "property":
            return f"{self.path}::{self.name}@property#{self.ordinal}"
        return f"{self.path}::{self.name}/{self.arity}#{self.ordinal}"

    @property
    def owner(self) -> str:
        if self.owner_name:
            return self.owner_name
        stem = Path(self.path).stem
        return stem.replace("+Trace", "Trace")


@dataclass(frozen=True)
class Constant:
    language: str
    path: str
    name: str
    value: str | None
    display_value: str
    line: int
    owner_name: str | None = None

    @property
    def key(self) -> str:
        return f"{self.path}::{self.name}"

    @property
    def owner(self) -> str:
        return self.owner_name or Path(self.path).stem


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    text: str
    identity: str

    def output(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text}"


@dataclass
class ScanResult:
    findings: list[Finding]
    counters: dict[str, int]
    stats: dict[str, int]


@dataclass(frozen=True)
class TwinReference:
    language: str
    path: str
    line: int
    raw_target: str
    target_name: str
    target_owner: str | None
    attached_function: str | None


@dataclass(frozen=True)
class CallSite:
    name: str
    arity: int
    owner: str | None


def _paths(root: Path, globs: Iterable[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in globs:
        found.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(found)


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


@lru_cache(maxsize=None)
def _read_cached(path: str, modified_ns: int, size: int) -> str:
    del modified_ns, size
    return Path(path).read_text(errors="ignore")


def _read(path: Path) -> str:
    stat = path.stat()
    return _read_cached(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=None)
def _mask_non_code(text: str) -> str:
    """Replace comments and string contents with spaces, preserving newlines."""
    out = list(text)
    i = 0
    state = "code"
    block_depth = 0
    quote = ""
    while i < len(text):
        if state == "code":
            if text.startswith("//", i):
                out[i] = out[i + 1] = " "
                i += 2
                state = "line"
            elif text.startswith("/*", i):
                out[i] = out[i + 1] = " "
                i += 2
                block_depth = 1
                state = "block"
            elif text.startswith('"""', i):
                out[i : i + 3] = "   "
                i += 3
                quote = '"""'
                state = "string"
            elif text[i] in "\"'":
                quote = text[i]
                out[i] = " "
                i += 1
                state = "string"
            else:
                i += 1
        elif state == "line":
            if text[i] == "\n":
                state = "code"
            else:
                out[i] = " "
            i += 1
        elif state == "block":
            if text.startswith("/*", i):
                out[i] = out[i + 1] = " "
                block_depth += 1
                i += 2
            elif text.startswith("*/", i):
                out[i] = out[i + 1] = " "
                block_depth -= 1
                i += 2
                if block_depth == 0:
                    state = "code"
            else:
                if text[i] != "\n":
                    out[i] = " "
                i += 1
        else:
            if quote == '"""' and text.startswith(quote, i):
                out[i : i + 3] = "   "
                i += 3
                state = "code"
            elif quote != '"""' and text[i] == "\\" and i + 1 < len(text):
                if text[i] != "\n":
                    out[i] = " "
                if text[i + 1] != "\n":
                    out[i + 1] = " "
                i += 2
            elif quote != '"""' and text[i] == quote:
                out[i] = " "
                i += 1
                state = "code"
            else:
                if text[i] != "\n":
                    out[i] = " "
                i += 1
    return "".join(out)


def _arity(masked: str, opening: int) -> int | None:
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    segments = 0
    segment_has_token = False
    i = opening + 1
    while i < len(masked):
        char = masked[i]
        if char == "(" or char == "[" or char == "{":
            stack.append(char)
        elif char == "<":
            # Parameter lists use angle brackets for types. Do not treat Kotlin/Swift arrows as generics.
            if i + 1 >= len(masked) or masked[i + 1] not in "= ":
                stack.append(char)
        elif char in pairs:
            if char == ")" and not stack:
                return segments + (1 if segment_has_token else 0)
            if stack and stack[-1] == pairs[char]:
                stack.pop()
        elif char == "," and not stack:
            if segment_has_token:
                segments += 1
            segment_has_token = False
        elif not char.isspace() and not stack:
            segment_has_token = True
        i += 1
    return None


SWIFT_FUNC = re.compile(
    r"\bfunc\s+(`?[A-Za-z_][A-Za-z0-9_]*`?|[=!<>+\-*/%&|^~?.]+)\s*(?:<[^\n{}()]*>\s*)?\("
)
KOTLIN_FUNC = re.compile(
    r"\bfun\s+(?:<[^\n{}()]*>\s*)?([^\n{}()=]+?)\s*\("
)

TYPE_DECLARATION = {
    "swift": re.compile(r"\b(?:struct|class|enum|actor|protocol|extension)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    "kotlin": re.compile(
        r"\b(?:(?:data|sealed|enum|annotation|value)\s+)?(?:class|object|interface)\s+([A-Za-z_][A-Za-z0-9_]*)"
    ),
}


def _matching_brace(masked: str, opening: int) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return len(masked)


def _type_spans(masked: str, language: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for match in TYPE_DECLARATION[language].finditer(masked):
        opening = masked.find("{", match.end())
        if opening < 0:
            continue
        # Do not attach a type to a later, unrelated declaration when its body is absent.
        next_decl = TYPE_DECLARATION[language].search(masked, match.end())
        if next_decl and next_decl.start() < opening:
            continue
        spans.append((opening, _matching_brace(masked, opening), match.group(1)))
    return spans


def _owner_at(spans: list[tuple[int, int, str]], offset: int, fallback: str) -> str:
    containing = [item for item in spans if item[0] < offset < item[1]]
    return max(containing, key=lambda item: item[0])[2] if containing else fallback


def _receiver_owner(header: str) -> str | None:
    name_match = re.search(r"(`?[A-Za-z_][A-Za-z0-9_]*`?)\s*$", header)
    if not name_match:
        return None
    prefix = header[: name_match.start()].rstrip()
    if not prefix.endswith("."):
        return None
    receiver = prefix[:-1].strip().rstrip("?")
    # The receiver can contain nested generics; the leading nominal type is the useful owner.
    names = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", receiver)
    return names[0] if names else None


@lru_cache(maxsize=None)
def _parse_functions_cached(
    root_string: str, path_string: str, language: str, modified_ns: int, size: int
) -> tuple[Declaration, ...]:
    del modified_ns, size
    root = Path(root_string)
    path = Path(path_string)
    text = _read(path)
    masked = _mask_non_code(text)
    pattern = SWIFT_FUNC if language == "swift" else KOTLIN_FUNC
    rel = _relative(root, path)
    fallback_owner = path.stem.replace("+Trace", "Trace")
    spans = _type_spans(masked, language)
    out: list[Declaration] = []
    ordinals: Counter[tuple[str, int]] = Counter()
    for match in pattern.finditer(masked):
        arity = _arity(masked, match.end() - 1)
        if arity is None:
            continue
        header = match.group(1)
        if language == "kotlin":
            name_match = re.search(r"(`?[A-Za-z_][A-Za-z0-9_]*`?)\s*$", header)
            if not name_match:
                continue
            name = name_match.group(1).strip("`")
            name_offset = match.start(1) + name_match.start(1)
            receiver = _receiver_owner(header)
        else:
            name = header.strip("`")
            name_offset = match.start(1)
            receiver = None
        ordinals[(name, arity)] += 1
        out.append(
            Declaration(
                language=language,
                path=rel,
                name=name,
                arity=arity,
                line=text.count("\n", 0, match.start()) + 1,
                ordinal=ordinals[(name, arity)],
                owner_name=receiver or _owner_at(spans, match.start(), fallback_owner),
                offset=name_offset,
                opening=match.end() - 1,
            )
        )
    return tuple(out)


def parse_functions(root: Path, path: Path, language: str) -> list[Declaration]:
    stat = path.stat()
    return list(
        _parse_functions_cached(
            str(root.resolve()), str(path.resolve()), language, stat.st_mtime_ns, stat.st_size
        )
    )


@lru_cache(maxsize=None)
def _parse_properties_cached(
    root_string: str, path_string: str, language: str, modified_ns: int, size: int
) -> tuple[Declaration, ...]:
    """Inventory computed properties/getters, excluding stored fields."""
    del modified_ns, size
    root = Path(root_string)
    path = Path(path_string)
    text = _read(path)
    masked = _mask_non_code(text)
    rel = _relative(root, path)
    fallback_owner = path.stem.replace("+Trace", "Trace")
    spans = _type_spans(masked, language)
    if language == "swift":
        pattern = re.compile(
            r"\bvar\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^=\n{]+\{"
        )
    else:
        pattern = re.compile(
            r"\b(?:val|var)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
            r"(?:\s*:\s*[^=\n{]+)?\s*(?:\n[ \t]*)?get\s*\(\s*\)"
        )
    out: list[Declaration] = []
    ordinals: Counter[str] = Counter()
    for match in pattern.finditer(masked):
        name = match.group(1)
        ordinals[name] += 1
        out.append(
            Declaration(
                language=language,
                path=rel,
                name=name,
                arity=0,
                line=text.count("\n", 0, match.start()) + 1,
                ordinal=ordinals[name],
                kind="property",
                owner_name=_owner_at(spans, match.start(), fallback_owner),
                offset=match.start(1),
            )
        )
    return tuple(out)


def parse_properties(root: Path, path: Path, language: str) -> list[Declaration]:
    stat = path.stat()
    return list(
        _parse_properties_cached(
            str(root.resolve()), str(path.resolve()), language, stat.st_mtime_ns, stat.st_size
        )
    )


NUMBER_PATTERN = (
    r"(?:0[xX][0-9A-Fa-f_]+[lL]?|0[bB][01_]+[lL]?|0[oO][0-7_]+[lL]?|"
    r"(?:\d[\d_]*(?:\.[\d_]*)?|\.[\d_]+)(?:[eE][-+]?\d[\d_]*)?[fFdDlL]?)"
)
NUMBER_TOKEN = re.compile(NUMBER_PATTERN)


class _NumberExpression:
    def __init__(self, raw: str):
        self.raw = raw
        self.tokens = re.findall(
            NUMBER_PATTERN + r"|[()+\-*/]",
            raw,
        )
        self.index = 0

    def parse(self) -> Decimal:
        compact = re.sub(r"\s+", "", self.raw)
        if "".join(self.tokens) != compact or not self.tokens:
            raise InvalidOperation
        value = self._sum()
        if self.index != len(self.tokens):
            raise InvalidOperation
        return value

    def _sum(self) -> Decimal:
        value = self._product()
        while self._peek() in {"+", "-"}:
            operator = self._take()
            right = self._product()
            value = value + right if operator == "+" else value - right
        return value

    def _product(self) -> Decimal:
        value = self._unary()
        while self._peek() in {"*", "/"}:
            operator = self._take()
            right = self._unary()
            if operator == "*":
                value *= right
            else:
                if right == 0:
                    raise InvalidOperation
                value /= right
        return value

    def _unary(self) -> Decimal:
        if self._peek() in {"+", "-"}:
            operator = self._take()
            value = self._unary()
            return value if operator == "+" else -value
        if self._peek() == "(":
            self._take()
            value = self._sum()
            if self._take() != ")":
                raise InvalidOperation
            return value
        token = self._take()
        if not NUMBER_TOKEN.fullmatch(token):
            raise InvalidOperation
        number = token.replace("_", "").rstrip("fFdDlL")
        if number.lower().startswith(("0x", "0b", "0o")):
            return Decimal(int(number, 0))
        return Decimal(number)

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self) -> str:
        if self.index >= len(self.tokens):
            raise InvalidOperation
        token = self.tokens[self.index]
        self.index += 1
        return token


def _initializer(text: str, start: int) -> str:
    """Return exactly one single-line constant initializer, without its terminator/comment."""
    out: list[str] = []
    quote: str | None = None
    escaped = False
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if text.startswith("//", index):
            break
        if char in {'"', "'"}:
            quote = char
            out.append(char)
        elif char == "(":
            depth += 1
            out.append(char)
        elif char == ")":
            depth = max(0, depth - 1)
            out.append(char)
        elif char in "\n;," and depth == 0:
            break
        elif char == "}" and depth == 0:
            break
        else:
            out.append(char)
        index += 1
    return "".join(out).strip().rstrip(",").strip()


def _literal(raw: str) -> tuple[str, str] | None:
    value = raw.strip()
    if re.fullmatch(r'"(?:\\.|[^"\\])*"', value):
        token = value
        token_for_json = re.sub(
            r"\\u\{([0-9A-Fa-f]{1,8})\}",
            lambda item: "\\u" + item.group(1).zfill(4),
            token,
        )
        try:
            decoded = json.loads(token_for_json)
        except json.JSONDecodeError:
            decoded = token[1:-1]
        return f"string:{decoded}", token
    if value in {"true", "false"}:
        return f"bool:{value}", value
    if value in {"nil", "null"}:
        return "null", value
    try:
        number = _NumberExpression(value).parse()
        return f"number:{number.normalize()}", value
    except (InvalidOperation, ZeroDivisionError):
        return None


@lru_cache(maxsize=None)
def _parse_constants_cached(
    root_string: str, path_string: str, language: str, modified_ns: int, size: int
) -> tuple[Constant, ...]:
    del modified_ns, size
    root = Path(root_string)
    path = Path(path_string)
    text = _read(path)
    masked = _mask_non_code(text)
    if language == "swift":
        pattern = re.compile(r"\blet\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?=")
    else:
        pattern = re.compile(r"\bconst\s+val\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?=")
    rel = _relative(root, path)
    fallback_owner = path.stem.replace("+Trace", "Trace")
    spans = _type_spans(masked, language)
    out: list[Constant] = []
    for match in pattern.finditer(masked):
        if language == "swift":
            line_start = text.rfind("\n", 0, match.start()) + 1
            prefix = text[line_start : match.start()]
            # Kotlin's `const val` has static storage. Pair it only with a Swift `static
            # let` or a file-scope declaration, never with a local/instance `let`.
            if "static" not in prefix.split() and len(prefix) - len(prefix.lstrip()) > 4:
                continue
        raw = _initializer(text, match.end())
        parsed = _literal(raw)
        canonical, display = parsed if parsed is not None else (None, raw or "<empty>")
        out.append(
            Constant(
                language,
                rel,
                match.group(1),
                canonical,
                display,
                text.count("\n", 0, match.start()) + 1,
                _owner_at(spans, match.start(), fallback_owner),
            )
        )
    return tuple(out)


def parse_constants(root: Path, path: Path, language: str) -> list[Constant]:
    stat = path.stat()
    return list(
        _parse_constants_cached(
            str(root.resolve()), str(path.resolve()), language, stat.st_mtime_ns, stat.st_size
        )
    )


def _inventory(
    root: Path,
) -> tuple[
    list[Path],
    list[Path],
    list[Declaration],
    list[Declaration],
    list[Declaration],
    list[Declaration],
    list[Constant],
    list[Constant],
]:
    swift_files = _paths(root, SWIFT_GLOBS)
    kotlin_files = _paths(root, KOTLIN_GLOBS)
    swift_functions = [item for path in swift_files for item in parse_functions(root, path, "swift")]
    kotlin_functions = [item for path in kotlin_files for item in parse_functions(root, path, "kotlin")]
    swift_properties = [item for path in swift_files for item in parse_properties(root, path, "swift")]
    kotlin_properties = [item for path in kotlin_files for item in parse_properties(root, path, "kotlin")]
    swift_constants = [item for path in swift_files for item in parse_constants(root, path, "swift")]
    kotlin_constants = [item for path in kotlin_files for item in parse_constants(root, path, "kotlin")]
    return (
        swift_files,
        kotlin_files,
        swift_functions,
        kotlin_functions,
        swift_properties,
        kotlin_properties,
        swift_constants,
        kotlin_constants,
    )


def _annotation_count(files: list[Path]) -> int:
    pattern = re.compile(r"\b(?:twin|parity)\b", re.I)
    return sum(len(pattern.findall(_read(path))) for path in files)


def _normal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _comment_blocks(text: str, language: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for match in re.finditer(r"/\*[\s\S]*?\*/", text):
        start = text.count("\n", 0, match.start()) + 1
        end = text.count("\n", 0, match.end()) + 1
        out.append((start, end, match.group(0)))
    for match in re.finditer(r"(?m)(?:^[ \t]*//[^\n]*(?:\n|$))+", text):
        start = text.count("\n", 0, match.start()) + 1
        end = text.count("\n", 0, match.end()) + 1
        out.append((start, end, match.group(0)))
    return sorted(out)


REFERENCE_PATTERNS = (
    re.compile(r"\b(?:Kotlin|Swift)(?:'s)?\s+twin\s*(?:is\s*|of\s*|:\s*)?(?:the\s+)?`([^`]+)`", re.I),
    re.compile(r"\btwin\s+of\s+(?:the\s+)?(?:Kotlin|Swift)(?:'s)?\s+`([^`]+)`", re.I),
    re.compile(
        r"\b(?:Kotlin|Swift)(?:'s)?\s+twin\s*(?:is\s*|:\s*)?"
        r"([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)",
        re.I,
    ),
)


def _target(raw: str) -> tuple[str, str | None] | None:
    value = raw.strip()
    if "/" in value and value.lower().endswith((".swift", ".kt")):
        return Path(value).stem, None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?)*", value):
        return None
    pieces = value.split(".")
    if pieces[-1].lower() in {"swift", "kt"} and len(pieces) >= 2:
        return pieces[-2], None
    name = pieces[-1].split("(", 1)[0]
    owner = pieces[-2] if len(pieces) > 1 else None
    if len(pieces) > 2 and all(piece[:1].islower() for piece in pieces[:-1]):
        owner = None  # package-qualified type, e.g. com.noop.protocol.DeviceConfigWriteGate
    elif name[:1].isupper():
        owner = None  # module/type-qualified type, not an Owner.member reference
    return name, owner


def parse_twin_references(root: Path, files: list[Path], language: str, functions: list[Declaration]) -> list[TwinReference]:
    by_path: dict[str, list[Declaration]] = defaultdict(list)
    for declaration in functions:
        by_path[declaration.path].append(declaration)
    out: list[TwinReference] = []
    expected = "kotlin" if language == "swift" else "swift"
    for path in files:
        rel = _relative(root, path)
        text = _read(path)
        for start, end, comment in _comment_blocks(text, language):
            if "twin" not in comment.lower() or expected not in comment.lower():
                continue
            # Remove line-doc decoration without changing offsets, so wrapped unquoted
            # references remain machine-readable and line reporting stays exact.
            searchable = re.sub(
                r"(?m)^(\s*)(?:///|//|/\*\*?|\*) ?",
                lambda match: " " * len(match.group(0)),
                comment,
            )
            raw_targets: list[tuple[str, int]] = []
            for pattern in REFERENCE_PATTERNS:
                raw_targets.extend((match.group(1), match.start(1)) for match in pattern.finditer(searchable))
            seen: set[str] = set()
            for raw, offset in raw_targets:
                if raw in seen:
                    continue
                seen.add(raw)
                parsed = _target(raw)
                if parsed is None:
                    continue
                name, owner = parsed
                attached = next(
                    (decl.key for decl in by_path[rel] if end <= decl.line <= end + 4),
                    None,
                )
                line = start + searchable.count("\n", 0, offset)
                out.append(TwinReference(language, rel, line, raw, name, owner, attached))
    return out


def _resolve(reference: TwinReference, targets: list[Declaration]) -> list[Declaration]:
    matches = [decl for decl in targets if _normal_name(decl.name) == _normal_name(reference.target_name)]
    if reference.target_owner:
        matches = [decl for decl in matches if _normal_name(decl.owner) == _normal_name(reference.target_owner)]
    return matches


def _symbol_owners(root: Path, files: list[Path], declarations: list[Declaration]) -> dict[str, set[str]]:
    symbols: dict[str, set[str]] = defaultdict(set)
    for item in declarations:
        symbols[_normal_name(item.name)].update((_normal_name(item.owner), _normal_name(Path(item.path).stem)))
    for path in files:
        file_owner = _normal_name(path.stem)
        symbols[file_owner].add(file_owner)
        language = "swift" if path.suffix == ".swift" else "kotlin"
        text = _read(path)
        masked = _mask_non_code(text)
        spans = _type_spans(masked, language)
        for _, _, name in spans:
            symbols[_normal_name(name)].add(file_owner)
        declaration = re.compile(
            r"\b(?:typealias|let|var|val)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
        )
        for match in declaration.finditer(masked):
            owner = _owner_at(spans, match.start(), path.stem)
            symbols[_normal_name(match.group(1))].update((_normal_name(owner), file_owner))
    return symbols


def _reference_resolves(reference: TwinReference, symbols: dict[str, set[str]]) -> bool:
    owners = symbols.get(_normal_name(reference.target_name), set())
    if not owners:
        return False
    return reference.target_owner is None or _normal_name(reference.target_owner) in owners


def _symbol_names(files: list[Path], functions: list[Declaration]) -> set[str]:
    """Compatibility helper retained for callers outside this module."""
    names = {_normal_name(item.name) for item in functions}
    names.update(_normal_name(path.stem) for path in files)
    declaration = re.compile(
        r"\b(?:struct|class|enum|actor|protocol|object|interface|typealias|let|var|val)\s+([A-Za-z_][A-Za-z0-9_]*)\b"
    )
    for path in files:
        masked = _mask_non_code(_read(path))
        names.update(_normal_name(match.group(1)) for match in declaration.finditer(masked))
    return names


def _constant_pairing(
    swift: list[Constant], kotlin: list[Constant], file_pairs: set[tuple[str, str]] | None = None
) -> tuple[list[tuple[Constant, Constant]], list[tuple[str, list[Constant], list[Constant]]]]:
    """Pair normalized constant names, preferring type owner and then mapped/same-stem files."""
    file_pairs = file_pairs or set()
    sw_by_name: dict[str, list[Constant]] = defaultdict(list)
    kt_by_name: dict[str, list[Constant]] = defaultdict(list)
    for item in swift:
        sw_by_name[_normal_name(item.name)].append(item)
    for item in kotlin:
        kt_by_name[_normal_name(item.name)].append(item)
    pairs: list[tuple[Constant, Constant]] = []
    ambiguous: list[tuple[str, list[Constant], list[Constant]]] = []
    for name in sorted(sw_by_name.keys() & kt_by_name.keys()):
        left = list(sw_by_name[name])
        right = list(kt_by_name[name])

        def consume(predicate) -> None:
            nonlocal left, right
            edges = [(sw, kt) for sw in left for kt in right if predicate(sw, kt)]
            left_degree = Counter(id(sw) for sw, _ in edges)
            right_degree = Counter(id(kt) for _, kt in edges)
            chosen = [(sw, kt) for sw, kt in edges if left_degree[id(sw)] == 1 and right_degree[id(kt)] == 1]
            pairs.extend(chosen)
            chosen_left = {id(sw) for sw, _ in chosen}
            chosen_right = {id(kt) for _, kt in chosen}
            left = [item for item in left if id(item) not in chosen_left]
            right = [item for item in right if id(item) not in chosen_right]

        consume(lambda sw, kt: _normal_name(sw.owner) == _normal_name(kt.owner))
        consume(lambda sw, kt: (sw.path, kt.path) in file_pairs)
        if len(left) == 1 and len(right) == 1:
            pairs.append((left.pop(), right.pop()))
        if left and right:
            ambiguous.append((name, sorted(left, key=lambda item: item.key), sorted(right, key=lambda item: item.key)))
    return sorted(pairs, key=lambda pair: (pair[0].key, pair[1].key)), ambiguous


def _property_candidates(
    swift: list[Declaration], kotlin: list[Declaration]
) -> list[tuple[Declaration, Declaration]]:
    sw_by_identity: dict[tuple[str, str, str], list[Declaration]] = defaultdict(list)
    kt_by_identity: dict[tuple[str, str, str], list[Declaration]] = defaultdict(list)
    for item in swift:
        sw_by_identity[(_normal_name(item.name), _normal_name(item.owner), _normal_name(Path(item.path).stem))].append(item)
    for item in kotlin:
        kt_by_identity[(_normal_name(item.name), _normal_name(item.owner), _normal_name(Path(item.path).stem))].append(item)
    return [
        (sw_by_identity[key][0], kt_by_identity[key][0])
        for key in sorted(sw_by_identity.keys() & kt_by_identity.keys())
        if len(sw_by_identity[key]) == 1 and len(kt_by_identity[key]) == 1
    ]


def build_twin_map(root: Path) -> dict:
    """Build an honest inventory: declared pairs, suggestions, and explicit unpaired entries."""
    root = root.resolve()
    (
        sw_files,
        kt_files,
        sw_funcs,
        kt_funcs,
        sw_properties,
        kt_properties,
        sw_consts,
        kt_consts,
    ) = _inventory(root)
    refs = parse_twin_references(root, sw_files, "swift", sw_funcs) + parse_twin_references(root, kt_files, "kotlin", kt_funcs)
    sw_by_key = {item.key: item for item in sw_funcs}
    kt_by_key = {item.key: item for item in kt_funcs}
    pairs: set[tuple[str, str]] = set()
    for reference in refs:
        targets = kt_funcs if reference.language == "swift" else sw_funcs
        resolved = _resolve(reference, targets)
        if reference.attached_function and len(resolved) == 1:
            if reference.language == "swift":
                pairs.add((reference.attached_function, resolved[0].key))
            else:
                pairs.add((resolved[0].key, reference.attached_function))

    paired_sw = {left for left, _ in pairs}
    paired_kt = {right for _, right in pairs}
    file_pairs = sorted({(sw_by_key[left].path, kt_by_key[right].path) for left, right in pairs})
    property_pairs = _property_candidates(sw_properties, kt_properties)
    constant_pairs, _ = _constant_pairing(sw_consts, kt_consts, set(file_pairs))
    paired_sw_files = {left for left, _ in file_pairs}
    paired_kt_files = {right for _, right in file_pairs}

    sw_unpaired = [item for item in sw_funcs if item.key not in paired_sw]
    kt_unpaired = [item for item in kt_funcs if item.key not in paired_kt]
    sw_suggest: dict[tuple[str, int], list[Declaration]] = defaultdict(list)
    kt_suggest: dict[tuple[str, int], list[Declaration]] = defaultdict(list)
    for item in sw_unpaired:
        sw_suggest[(_normal_name(item.name), item.arity)].append(item)
    for item in kt_unpaired:
        kt_suggest[(_normal_name(item.name), item.arity)].append(item)
    function_suggestions = [
        {"swift": sw_suggest[key][0].key, "kotlin": kt_suggest[key][0].key}
        for key in sorted(sw_suggest.keys() & kt_suggest.keys())
        if len(sw_suggest[key]) == 1 and len(kt_suggest[key]) == 1
    ]

    sw_file_by_stem = defaultdict(list)
    kt_file_by_stem = defaultdict(list)
    for path in sw_files:
        sw_file_by_stem[_normal_name(path.stem)].append(_relative(root, path))
    for path in kt_files:
        kt_file_by_stem[_normal_name(path.stem)].append(_relative(root, path))
    file_suggestions = [
        {"swift": sw_file_by_stem[key][0], "kotlin": kt_file_by_stem[key][0]}
        for key in sorted(sw_file_by_stem.keys() & kt_file_by_stem.keys())
        if len(sw_file_by_stem[key]) == 1 and len(kt_file_by_stem[key]) == 1
        and sw_file_by_stem[key][0] not in paired_sw_files
        and kt_file_by_stem[key][0] not in paired_kt_files
    ]

    reason = "No machine-resolvable declared twin at bootstrap; explicit inventory exemption."
    return {
        "schema_version": 2,
        "scope": {
            "source_pairs": [
                {
                    "swift": "Packages/StrandAnalytics/Sources/**/*.swift",
                    "kotlin": "android/app/src/main/java/com/noop/analytics/**/*.kt",
                },
                {
                    "swift": "Packages/StrandImport/Sources/**/*.swift",
                    "kotlin": "android/app/src/main/java/com/noop/ingest/**/*.kt",
                },
                {
                    "swift": "Packages/WhoopStore/Sources/**/*.swift",
                    "kotlin": "android/app/src/main/java/com/noop/data/**/*.kt",
                },
                {
                    "swift": "Packages/WhoopProtocol/Sources/**/*.swift",
                    "kotlin": "android/app/src/main/java/com/noop/protocol/**/*.kt",
                },
                {
                    "swift": "Packages/OuraProtocol/Sources/**/*.swift",
                    "kotlin": "android/app/src/main/java/com/noop/oura/**/*.kt",
                },
            ],
            "swift_globs": list(SWIFT_GLOBS),
            "kotlin_globs": list(KOTLIN_GLOBS),
            "excluded_inventory_globs": {
                "reason": "Production trees intentionally outside the drift/01 inventory; twin references are still resolved repo-wide.",
                "swift": list(SWIFT_EXCLUDED_GLOBS),
                "kotlin": list(KOTLIN_EXCLUDED_GLOBS),
            },
        },
        "file_pairs": [
            {"swift": left, "kotlin": right, "evidence": "machine-resolvable declared function twin"}
            for left, right in file_pairs
        ],
        "function_pairs": [
            {"swift": left, "kotlin": right, "evidence": "declared twin reference"}
            for left, right in sorted(pairs)
        ],
        "property_pairs": [
            {"swift": left.key, "kotlin": right.key, "evidence": "same file/type owner and property name"}
            for left, right in property_pairs
        ],
        "constant_pairs": [
            {"swift": left.key, "kotlin": right.key, "evidence": "owner-disambiguated camelCase/SNAKE_CASE name"}
            for left, right in constant_pairs
        ],
        "unpaired_files": {
            "reason": reason,
            "swift": [_relative(root, path) for path in sw_files if _relative(root, path) not in paired_sw_files],
            "kotlin": [_relative(root, path) for path in kt_files if _relative(root, path) not in paired_kt_files],
        },
        "unpaired_functions": {
            "reason": reason,
            "swift": [item.key for item in sw_unpaired],
            "kotlin": [item.key for item in kt_unpaired],
        },
        "unpaired_properties": {
            "reason": reason,
            "swift": [item.key for item in sw_properties if item.key not in {left.key for left, _ in property_pairs}],
            "kotlin": [item.key for item in kt_properties if item.key not in {right.key for _, right in property_pairs}],
        },
        "name_only_suggestions": {
            "files": file_suggestions,
            "functions": function_suggestions,
            "note": "Suggestions are not asserted twins and do not remove explicit unpaired entries.",
        },
    }


def _finding(rule: str, path: str, line: int, text: str, identity: str) -> Finding:
    return Finding(rule, path, line, text, f"{rule}|{identity}")


def _mapped_sets(twin_map: dict) -> tuple[set[str], set[str], set[str]]:
    files: set[str] = set()
    functions: set[str] = set()
    properties: set[str] = set()
    for entry in twin_map.get("file_pairs", []):
        files.update((entry["swift"], entry["kotlin"]))
    unpaired_files = twin_map.get("unpaired_files", {})
    if isinstance(unpaired_files, dict):
        files.update(unpaired_files.get("swift", []))
        files.update(unpaired_files.get("kotlin", []))
    else:  # schema-v1 draft compatibility
        files.update(entry["path"] for entry in unpaired_files)
    for entry in twin_map.get("function_pairs", []):
        functions.update((entry["swift"], entry["kotlin"]))
    unpaired_functions = twin_map.get("unpaired_functions", {})
    if isinstance(unpaired_functions, dict):
        functions.update(unpaired_functions.get("swift", []))
        functions.update(unpaired_functions.get("kotlin", []))
    else:  # schema-v1 draft compatibility
        functions.update(entry["symbol"] for entry in unpaired_functions)
    for entry in twin_map.get("property_pairs", []):
        properties.update((entry["swift"], entry["kotlin"]))
    unpaired_properties = twin_map.get("unpaired_properties", {})
    if isinstance(unpaired_properties, dict):
        properties.update(unpaired_properties.get("swift", []))
        properties.update(unpaired_properties.get("kotlin", []))
    return files, functions, properties


def _call_sites(root: Path, globs: tuple[str, ...]) -> dict[str, list[CallSite]]:
    calls: dict[str, list[CallSite]] = {"swift": [], "kotlin": []}
    for path in _paths(root, globs):
        language = "swift" if path.suffix == ".swift" else "kotlin"
        text = _read(path)
        masked = _mask_non_code(text)
        declarations = parse_functions(root, path, language)
        declaration_openings = {item.opening for item in declarations}
        spans = _type_spans(masked, language)
        fallback_owner = path.stem.replace("+Trace", "Trace")
        for match in re.finditer(r"\b(`?[A-Za-z_][A-Za-z0-9_]*`?)\s*\(", masked):
            opening = match.end() - 1
            if opening in declaration_openings:
                continue
            arity = _arity(masked, opening)
            if arity is None:
                continue
            prefix = masked[max(0, match.start() - 100) : match.start()]
            receiver_match = re.search(r"(`?[A-Za-z_][A-Za-z0-9_]*`?)\s*\.\s*$", prefix)
            lexical_owner = _owner_at(spans, match.start(), fallback_owner)
            owner = receiver_match.group(1).strip("`") if receiver_match else None
            if owner in {"self", "this", "Self"}:
                owner = lexical_owner
            calls[language].append(CallSite(match.group(1).strip("`"), arity, owner))
    return calls


def _declaration_call_counts(
    declarations: list[Declaration], sites: dict[str, list[CallSite]]
) -> dict[str, int]:
    by_signature: dict[tuple[str, str, int], list[Declaration]] = defaultdict(list)
    for declaration in declarations:
        by_signature[(declaration.language, _normal_name(declaration.name), declaration.arity)].append(declaration)
    counts: Counter[str] = Counter()
    for language, language_sites in sites.items():
        for site in language_sites:
            candidates = by_signature.get((language, _normal_name(site.name), site.arity), [])
            if site.owner:
                owner = _normal_name(site.owner)
                candidates = [
                    item
                    for item in candidates
                    if owner in {_normal_name(item.owner), _normal_name(Path(item.path).stem)}
                ]
            else:
                owners = {_normal_name(item.owner) for item in candidates}
                if len(owners) != 1:
                    candidates = []
            for candidate in candidates:
                counts[candidate.key] += 1
    return dict(counts)


def scan(root: Path, twin_map: dict) -> ScanResult:
    root = root.resolve()
    (
        sw_files,
        kt_files,
        sw_funcs,
        kt_funcs,
        sw_properties,
        kt_properties,
        sw_consts,
        kt_consts,
    ) = _inventory(root)
    findings: list[Finding] = []
    mapped_files, mapped_functions, mapped_properties = _mapped_sets(twin_map)

    for language, files in (("Swift", sw_files), ("Kotlin", kt_files)):
        for path in files:
            rel = _relative(root, path)
            if rel not in mapped_files:
                findings.append(_finding("unmapped-file", rel, 1, f"new {language} file has no twin-map entry", rel))
    for declaration in sw_funcs + kt_funcs:
        if declaration.key not in mapped_functions:
            findings.append(
                _finding(
                    "unmapped-function",
                    declaration.path,
                    declaration.line,
                    f"{declaration.name}/{declaration.arity} has no twin-map entry",
                    declaration.key,
                )
            )
    for declaration in sw_properties + kt_properties:
        if declaration.key not in mapped_properties:
            findings.append(
                _finding(
                    "unmapped-property",
                    declaration.path,
                    declaration.line,
                    f"{declaration.owner}.{declaration.name} has no twin-map entry",
                    declaration.key,
                )
            )

    all_production_files = _paths(root, PRODUCTION_GLOBS)
    all_functions = [
        item
        for path in all_production_files
        for item in parse_functions(root, path, "swift" if path.suffix == ".swift" else "kotlin")
    ]
    all_properties = [
        item
        for path in all_production_files
        for item in parse_properties(root, path, "swift" if path.suffix == ".swift" else "kotlin")
    ]
    reference_files = _paths(root, REFERENCE_GLOBS)
    all_reference_declarations = [
        item
        for path in reference_files
        for item in (
            parse_functions(root, path, "swift" if path.suffix == ".swift" else "kotlin")
            + parse_properties(root, path, "swift" if path.suffix == ".swift" else "kotlin")
        )
    ]
    all_swift_files = [path for path in reference_files if path.suffix == ".swift"]
    all_kotlin_files = [path for path in reference_files if path.suffix == ".kt"]
    all_swift_functions = [item for item in all_functions if item.language == "swift"]
    all_kotlin_functions = [item for item in all_functions if item.language == "kotlin"]

    reference_swift_declarations = [item for item in all_reference_declarations if item.language == "swift"]
    reference_kotlin_declarations = [item for item in all_reference_declarations if item.language == "kotlin"]
    refs = parse_twin_references(root, all_swift_files, "swift", reference_swift_declarations) + parse_twin_references(
        root,
        all_kotlin_files,
        "kotlin",
        reference_kotlin_declarations,
    )
    swift_symbols = _symbol_owners(
        root,
        all_swift_files,
        reference_swift_declarations,
    )
    kotlin_symbols = _symbol_owners(
        root,
        all_kotlin_files,
        reference_kotlin_declarations,
    )
    resolved_refs = 0
    for reference in refs:
        target_symbols = kotlin_symbols if reference.language == "swift" else swift_symbols
        if _reference_resolves(reference, target_symbols):
            resolved_refs += 1
            continue
        target_language = "Kotlin" if reference.language == "swift" else "Swift"
        findings.append(
            _finding(
                "dead-twin-reference",
                reference.path,
                reference.line,
                f"{target_language} target {reference.raw_target} does not resolve",
                f"{reference.path}|{reference.raw_target}",
            )
        )

    constants_by_key = {item.key: item for item in sw_consts + kt_consts}
    file_pairs = {(entry["swift"], entry["kotlin"]) for entry in twin_map.get("file_pairs", [])}
    paired_constants, ambiguous_constants = _constant_pairing(sw_consts, kt_consts, file_pairs)
    dynamic_pairs = {(left.key, right.key) for left, right in paired_constants}
    mapped_pairs = {(entry["swift"], entry["kotlin"]) for entry in twin_map.get("constant_pairs", [])}
    for left_key, right_key in sorted(dynamic_pairs | mapped_pairs):
        left = constants_by_key.get(left_key)
        right = constants_by_key.get(right_key)
        if left is None or right is None:
            continue
        if left.value is None or right.value is None:
            findings.append(
                _finding(
                    "constant-unverifiable",
                    left.path,
                    left.line,
                    f"cannot fully evaluate {left.name}={left.display_value} against Kotlin {right.name}={right.display_value} ({right.path}:{right.line})",
                    f"{left_key}|{left.display_value}|{right_key}|{right.display_value}",
                )
            )
            continue
        if left.value == right.value:
            continue
        findings.append(
            _finding(
                "constant-value-mismatch",
                left.path,
                left.line,
                f"{left.name}={left.display_value} differs from Kotlin {right.name}={right.display_value} ({right.path}:{right.line})",
                f"{left_key}|{left.value}|{right_key}|{right.value}",
            )
        )

    for normal_name, left, right in ambiguous_constants:
        left_keys = ", ".join(item.key for item in left)
        right_keys = ", ".join(item.key for item in right)
        first = left[0]
        findings.append(
            _finding(
                "constant-ambiguous",
                first.path,
                first.line,
                f"{normal_name} cannot be paired uniquely: Swift [{left_keys}] vs Kotlin [{right_keys}]",
                f"{normal_name}|{'|'.join(item.key for item in left)}|{'|'.join(item.key for item in right)}",
            )
        )

    inventory_functions = sw_funcs + kt_funcs
    prod_calls = _declaration_call_counts(inventory_functions, _call_sites(root, PRODUCTION_GLOBS))
    test_calls = _declaration_call_counts(inventory_functions, _call_sites(root, TEST_GLOBS))
    for declaration in sw_funcs + kt_funcs:
        if test_calls.get(declaration.key, 0) > 0 and prod_calls.get(declaration.key, 0) == 0:
            findings.append(
                _finding(
                    "test-only-callsite",
                    declaration.path,
                    declaration.line,
                    f"{declaration.owner}.{declaration.name}/{declaration.arity} has {test_calls[declaration.key]} test callsite(s) and no production callsite",
                    declaration.key,
                )
            )

    day_string = [item for item in all_functions if item.name == "dayString"]
    ui_pearson = [
        item for item in all_functions
        if item.language == "kotlin" and item.name == "pearson" and item.path.startswith("android/app/src/main/java/com/noop/ui/")
    ]
    resting = [item for item in sw_funcs + kt_funcs if item.name in {"restingHR", "sessionRestingHR"}]
    for declaration in day_string:
        findings.append(
            _finding(
                "duplicate-implementation",
                declaration.path,
                declaration.line,
                f"dayString implementation {declaration.owner}.{declaration.name}/{declaration.arity}",
                f"dayString|{declaration.key}",
            )
        )
    for declaration in ui_pearson:
        findings.append(
            _finding(
                "duplicate-implementation",
                declaration.path,
                declaration.line,
                f"independent Android UI Pearson implementation {declaration.owner}.pearson/{declaration.arity}",
                f"android-ui-pearson|{declaration.key}",
            )
        )
    for declaration in resting:
        findings.append(
            _finding(
                "duplicate-implementation",
                declaration.path,
                declaration.line,
                f"resting-HR path {declaration.owner}.{declaration.name}/{declaration.arity}",
                f"resting-hr|{declaration.key}",
            )
        )

    findings.sort(key=lambda item: (item.path, item.line, item.rule, item.identity))
    counters = {
        "day_string_implementations": len(day_string),
        "resting_hr_paths": len({item.name for item in resting}),
        "android_ui_pearson_implementations": len(ui_pearson),
    }
    stats = {
        "swift_files": len(sw_files),
        "kotlin_files": len(kt_files),
        "swift_functions": len(sw_funcs),
        "kotlin_functions": len(kt_funcs),
        "swift_properties": len(sw_properties),
        "kotlin_properties": len(kt_properties),
        "swift_constants": len(sw_consts),
        "kotlin_constants": len(kt_consts),
        "swift_parity_annotations": _annotation_count(sw_files),
        "kotlin_parity_annotations": _annotation_count(kt_files),
        "declared_twin_references": len(refs),
        "resolved_twin_references": resolved_refs,
        "constant_pairs": len(dynamic_pairs | mapped_pairs),
    }
    return ScanResult(findings, counters, stats)


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n")


def _baseline(result: ScanResult) -> dict:
    return {
        "schema_version": 2,
        "findings": [
            {"identity": item.identity, "rule": item.rule, "path": item.path, "line": item.line, "text": item.text}
            for item in result.findings
        ],
        "counters": result.counters,
    }


def _summary(result: ScanResult) -> str:
    stats = result.stats
    return (
        f"{stats['swift_files']} Swift files, {stats['kotlin_files']} Kotlin files; "
        f"{stats['swift_functions']} Swift functions, {stats['kotlin_functions']} Kotlin functions; "
        f"{stats['swift_properties']} Swift properties, {stats['kotlin_properties']} Kotlin properties; "
        f"{stats['swift_constants']} Swift constants, {stats['kotlin_constants']} Kotlin constants; "
        f"annotations Swift={stats['swift_parity_annotations']}, Kotlin={stats['kotlin_parity_annotations']}; "
        f"{stats['declared_twin_references']} declared twin references "
        f"({stats['resolved_twin_references']} resolved); {stats['constant_pairs']} constant pairs; "
        f"counters dayString={result.counters['day_string_implementations']}, "
        f"resting-HR={result.counters['resting_hr_paths']}, "
        f"Android-UI-Pearson={result.counters['android_ui_pearson_implementations']}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--map", dest="map_path", type=Path, help="twin-map JSON path")
    parser.add_argument("--baseline", dest="baseline_path", type=Path, help="baseline JSON path")
    parser.add_argument("--no-baseline", action="store_true", help="show every current finding")
    parser.add_argument("--bootstrap-map", action="store_true", help="write a fresh inventory map before scanning")
    parser.add_argument("--write-baseline", action="store_true", help="replace the baseline with current findings")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    map_path = args.map_path or root / "Tools/parity_twin_map.json"
    baseline_path = args.baseline_path or root / "Tools/parity_ledger_baseline.json"
    if args.bootstrap_map:
        twin_map = build_twin_map(root)
        _write_json(map_path, twin_map)
        print(f"WROTE {map_path} ({len(twin_map['function_pairs'])} declared function pairs)")
    else:
        twin_map = _load_json(map_path, {})

    result = scan(root, twin_map)
    if args.write_baseline:
        _write_json(baseline_path, _baseline(result))
        print(f"WROTE {baseline_path} ({len(result.findings)} known findings)")
        print(f"OK {_summary(result)}")
        return 0

    baseline = {} if args.no_baseline else _load_json(baseline_path, {})
    known = {item["identity"] for item in baseline.get("findings", [])}
    regressions = [item for item in result.findings if item.identity not in known]
    baseline_counters = baseline.get("counters", {})
    counter_regressions = [
        (name, baseline_counters[name], count)
        for name, count in result.counters.items()
        if name in baseline_counters and count > baseline_counters[name]
    ]
    current = {item.identity for item in result.findings}
    improved = sorted(known - current)
    if improved and not args.no_baseline:
        print(f"IMPROVED {len(improved)} known finding(s) disappeared. Regenerate {baseline_path.name} after review.")

    if regressions or counter_regressions:
        total = len(regressions) + len(counter_regressions)
        print(f"FAIL {total} parity ledger finding(s) beyond the baseline:\n")
        for item in regressions:
            print(f"  {item.output()}")
        for name, was, now in counter_regressions:
            print(f"  {baseline_path.relative_to(root)}:1: duplicate-counter: {name} increased from {was} to {now}")
        print(f"\nScanned {_summary(result)}")
        return 1

    print(f"OK no NEW parity ledger findings ({len(result.findings)} baselined; {_summary(result)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
