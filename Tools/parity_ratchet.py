#!/usr/bin/env python3
"""Declaration-level completeness and coverage ratchet for Swift/Kotlin twins.

The lexer is deliberately lexical and standard-library only.  It extends the
existing ledger idiom (path + name + arity + ordinal), importing the ledger's
comment/string masking, declaration patterns, brace matching, and owner parser
instead of maintaining a second copy of those primitives.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import parity_ledger as ledger


ROOT = Path(__file__).resolve().parent.parent
SHARD_NAME = "parity-exempt.json"
LEDGER_BASELINE = "Tools/parity_ledger_baseline.json"
SWIFT_RUNNER = "Packages/StrandAnalytics/Tests/StrandAnalyticsTests/ParityRunner.swift"
KOTLIN_RUNNER = "android/app/src/test/java/com/noop/analytics/ParityRunner.kt"


class RatchetError(ValueError):
    """A ratchet input or invariant is invalid."""


class LexError(RatchetError):
    """Source syntax cannot be inventoried safely."""


@dataclass(frozen=True)
class ModulePair:
    name: str
    swift_dir: Path
    kotlin_dir: Path


@dataclass(frozen=True)
class Declaration:
    language: str
    path: str
    owner: str
    name: str
    kind: str
    arity: int
    defaults: tuple[int, ...]
    ordinal: int
    line: int
    end_line: int
    coverable: bool = True

    @property
    def key(self) -> str:
        if self.kind in {"function", "initializer"}:
            defaults = ",".join(str(item) for item in self.defaults) or "-"
            return (
                f"{self.path}::{self.name}/{self.arity}"
                f"[defaults={defaults}]#{self.ordinal}"
            )
        return f"{self.path}::{self.name}@{self.kind}#{self.ordinal}"


@dataclass(frozen=True)
class ShardEntry:
    key: str
    issue: int
    reason: str | None = None
    test: str | None = None


@dataclass(frozen=True)
class Shard:
    path: Path
    module: str
    exempt: tuple[ShardEntry, ...]
    platform_test: tuple[ShardEntry, ...]


@dataclass(frozen=True)
class CoverageMethod:
    name: str
    line: int
    executions: int
    # Parameter count from the JaCoCo method descriptor; None where the report
    # format carries no signature (LCOV FN records).
    arity: int | None = None


def _jvm_descriptor_arity(desc: str | None) -> int | None:
    """Count the parameters in a JVM method descriptor like (Ljava/util/List;IJ)V."""
    if not desc or not desc.startswith("("):
        return None
    index, count = 1, 0
    while index < len(desc) and desc[index] != ")":
        char = desc[index]
        if char == "[":
            index += 1
            continue
        if char == "L":
            end = desc.find(";", index)
            if end == -1:
                return None
            index = end + 1
        else:
            index += 1
        count += 1
    return count


@dataclass
class CoverageSource:
    lines: dict[int, int]
    methods: list[CoverageMethod]


def module_pairs(root: Path) -> list[ModulePair]:
    """Return ledger module pairs; a pair becomes sharp only when both shards exist."""
    pairs: list[ModulePair] = []
    for swift_glob, kotlin_glob in zip(ledger.SWIFT_GLOBS, ledger.KOTLIN_GLOBS):
        swift_scope = Path(swift_glob.split("/**", 1)[0])
        # SwiftPM's module directory is Sources/<package-name>; the ledger glob
        # intentionally stops at Sources so it can see auxiliary targets too.
        package_name = swift_scope.parts[1]
        swift_rel = (swift_scope / package_name).as_posix()
        kotlin_rel = kotlin_glob.split("/**", 1)[0]
        swift_name = Path(swift_rel).name
        kotlin_name = Path(kotlin_rel).name
        pairs.append(ModulePair(f"{swift_name}<->{kotlin_name}", root / swift_rel, root / kotlin_rel))
    return pairs


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _mask_comments(text: str) -> str:
    """Mask comments while preserving strings and line structure."""
    out = list(text)
    index = 0
    state = "code"
    quote = ""
    block_depth = 0
    while index < len(text):
        if state == "code":
            if text.startswith("//", index):
                out[index : index + 2] = "  "
                index += 2
                state = "line"
            elif text.startswith("/*", index):
                out[index : index + 2] = "  "
                index += 2
                block_depth = 1
                state = "block"
            elif text.startswith('"""', index):
                quote = '"""'
                index += 3
                state = "string"
            elif text[index] in "\"'":
                quote = text[index]
                index += 1
                state = "string"
            else:
                index += 1
        elif state == "line":
            if text[index] == "\n":
                state = "code"
            else:
                out[index] = " "
            index += 1
        elif state == "block":
            if text.startswith("/*", index):
                out[index : index + 2] = "  "
                index += 2
                block_depth += 1
            elif text.startswith("*/", index):
                out[index : index + 2] = "  "
                index += 2
                block_depth -= 1
                if block_depth == 0:
                    state = "code"
            else:
                if text[index] != "\n":
                    out[index] = " "
                index += 1
        elif quote == '"""' and text.startswith(quote, index):
            index += 3
            state = "code"
        elif quote != '"""' and text[index] == "\\" and index + 1 < len(text):
            index += 2
        elif quote != '"""' and text[index] == quote:
            index += 1
            state = "code"
        else:
            index += 1
    return "".join(out)


def _brace_depths(masked: str) -> list[int]:
    depths = [0] * (len(masked) + 1)
    depth = 0
    for index, char in enumerate(masked):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise LexError(f"unmatched closing brace at line {_line(masked, index)}")
    depths[len(masked)] = depth
    if depth:
        raise LexError("unclosed brace at end of file")
    return depths


def _matching(masked: str, opening: int, left: str, right: str) -> int:
    depth = 0
    for index in range(opening, len(masked)):
        char = masked[index]
        if char == left:
            depth += 1
        elif char == right:
            depth -= 1
            if depth == 0:
                return index
    raise LexError(f"unclosed {left!r} at line {_line(masked, opening)}")


def _top_level_segments(masked: str, opening: int, closing: int) -> list[tuple[int, int]]:
    pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
    stack: list[str] = []
    starts = [opening + 1]
    for index in range(opening + 1, closing):
        char = masked[index]
        if char in "([{":
            stack.append(char)
        elif char == "<":
            if index + 1 >= closing or masked[index + 1] not in "= ":
                stack.append(char)
        elif char in pairs:
            if stack and stack[-1] == pairs[char]:
                stack.pop()
        elif char == "," and not stack:
            starts.append(index + 1)
    ends = [item - 1 for item in starts[1:]] + [closing]
    return [(start, end) for start, end in zip(starts, ends) if masked[start:end].strip()]


def _defaults(masked: str, opening: int, closing: int) -> tuple[int, tuple[int, ...]]:
    segments = _top_level_segments(masked, opening, closing)
    defaulted: list[int] = []
    for position, (start, end) in enumerate(segments, 1):
        stack: list[str] = []
        for index in range(start, end):
            char = masked[index]
            if char in "([{<":
                stack.append(char)
            elif char in ")]}>":
                if stack:
                    stack.pop()
            elif char == "=" and not stack:
                if index + 1 < end and masked[index + 1] in "=>":
                    continue
                defaulted.append(position)
                break
    return len(segments), tuple(defaulted)


def _direct_context(
    spans: list[tuple[int, int, str]], depths: list[int], offset: int, fallback: str
) -> tuple[bool, str]:
    containing = [span for span in spans if span[0] < offset < span[1]]
    if not containing:
        return depths[offset] == 0, fallback
    opening, _closing, owner = max(containing, key=lambda item: item[0])
    return depths[offset] == depths[opening] + 1, owner


def _kotlin_type_layout(
    masked: str,
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]]:
    """Return real Kotlin body spans plus all primary-header extents.

    The ledger's intentionally loose owner parser can attach a bodyless data
    class to the next function body.  That is unsafe for a fail-closed
    declaration inventory because locals then look like member declarations.
    """
    spans: list[tuple[int, int, str]] = []
    headers: list[tuple[int, int, str]] = []
    for match in ledger.TYPE_DECLARATION["kotlin"].finditer(masked):
        index = match.end()
        stack: list[str] = []
        pairs = {")": "(", "]": "[", ">": "<"}
        header_end = len(masked)
        opening: int | None = None
        while index < len(masked):
            char = masked[index]
            if char in "([<":
                stack.append(char)
            elif char in pairs:
                if stack and stack[-1] == pairs[char]:
                    stack.pop()
            elif char == "{" and stack:
                index = ledger._matching_brace(masked, index)
            elif char == "{" and not stack:
                opening = index
                header_end = index
                break
            elif char == "\n" and not stack:
                next_code = index + 1
                while next_code < len(masked) and masked[next_code].isspace():
                    next_code += 1
                prior = masked[match.end() : index].rstrip()
                continuation = (
                    (prior and prior[-1] in ":,")
                    or masked.startswith(":", next_code)
                    or re.match(r"where\b", masked[next_code:]) is not None
                    or (next_code < len(masked) and masked[next_code] == "{")
                )
                if not continuation:
                    header_end = index
                    break
            index += 1
        headers.append((match.start(), header_end, match.group(1)))
        if opening is not None:
            spans.append((opening, ledger._matching_brace(masked, opening), match.group(1)))
    return spans, headers


def _type_layout(
    masked: str, language: str
) -> tuple[list[tuple[int, int, str]], list[tuple[int, int, str]]]:
    if language == "kotlin":
        return _kotlin_type_layout(masked)
    spans = ledger._type_spans(masked, language)
    headers: list[tuple[int, int, str]] = []
    for match in ledger.TYPE_DECLARATION[language].finditer(masked):
        opening = masked.find("{", match.end())
        if opening >= 0:
            headers.append((match.start(), opening, match.group(1)))
    return spans, headers


def _body_extent(masked: str, closing_paren: int, language: str) -> tuple[int, bool]:
    """Return body end offset and whether the declaration has executable code."""
    declaration_start = re.compile(
        r"(?:(?:@[A-Za-z_][A-Za-z0-9_.]*(?:\([^)]*\))?|public|private|fileprivate|"
        r"internal|protected|open|"
        r"static|final|override|required|convenience|mutating|nonmutating|abstract|"
        r"lazy|weak|unowned|const|lateinit|tailrec|suspend|inline|infix|operator|"
        r"external|data|sealed|annotation|value|inner|companion)\s+)*"
        r"(?:func|fun|var|let|val|class|struct|enum|protocol|extension|actor|object|"
        r"interface|init|deinit|subscript|constructor|typealias)\b"
    )
    index = closing_paren + 1
    nesting = 0
    while index < len(masked):
        char = masked[index]
        if char in "([<":
            nesting += 1
        elif char in ")]>":
            nesting = max(0, nesting - 1)
        elif not nesting and char == "{":
            return ledger._matching_brace(masked, index), True
        elif not nesting and language == "kotlin" and char == "=" and not masked.startswith("=>", index):
            newline = masked.find("\n", index)
            return (len(masked) if newline < 0 else newline), True
        elif not nesting and char == "\n":
            next_line = masked[index + 1 :].split("\n", 1)[0].strip()
            signature = masked[closing_paren + 1 : index].strip()
            if declaration_start.match(next_line) or next_line.startswith("}"):
                return closing_paren, False
            if not next_line or next_line.startswith("{"):
                index += 1
                continue
            if language == "swift":
                continuation = (
                    re.search(r"(?:->|\bwhere\b|\b(?:async|throws|rethrows)\b)", signature)
                    or re.match(r"(?:->|where\b|async\b|throws\b|rethrows\b)", next_line)
                )
            else:
                continuation = (
                    ":" in signature
                    or re.search(r"\bwhere\b", signature)
                    or re.match(r"(?::|where\b|=)", next_line)
                )
            if continuation or re.search(r"(?:->|:|,|<|&|\?|\.)\s*$", signature):
                index += 1
                continue
            return closing_paren, False
        elif not nesting and char == "}":
            return closing_paren, False
        index += 1
    return closing_paren, False


def _function_declarations(
    root: Path,
    path: Path,
    language: str,
    text: str,
    masked: str,
    spans: list[tuple[int, int, str]],
    depths: list[int],
) -> list[Declaration]:
    pattern = ledger.SWIFT_FUNC if language == "swift" else ledger.KOTLIN_FUNC
    parsed = {item.opening: item for item in ledger.parse_functions(root, path, language)}
    fallback = path.stem.replace("+Trace", "Trace")
    declarations: list[Declaration] = []
    ordinals: Counter[tuple[str, int, tuple[int, ...]]] = Counter()
    matched_keywords: set[int] = set()
    for match in pattern.finditer(masked):
        keyword = masked.find("func" if language == "swift" else "fun", match.start(), match.end())
        direct, owner = _direct_context(spans, depths, keyword, fallback)
        if not direct:
            continue
        matched_keywords.add(keyword)
        opening = match.end() - 1
        item = parsed.get(opening)
        if item is None:
            raise LexError(f"unclosed parameter list in {path}:{_line(text, keyword)}")
        closing = _matching(masked, opening, "(", ")")
        arity, defaults = _defaults(masked, opening, closing)
        if ledger._arity(masked, opening) != arity:
            raise LexError(f"unsupported parameter syntax in {path}:{item.line}")
        end, coverable = _body_extent(masked, closing, language)
        ordinal_key = (item.name, arity, defaults)
        ordinals[ordinal_key] += 1
        declarations.append(
            Declaration(
                language,
                ledger._relative(root, path),
                owner,
                item.name,
                "function",
                arity,
                defaults,
                ordinals[ordinal_key],
                item.line,
                _line(text, end),
                coverable,
            )
        )
    keyword_pattern = r"\bfunc\b" if language == "swift" else r"\bfun\b(?!\s+interface\b)"
    for keyword_match in re.finditer(keyword_pattern, masked):
        direct, _owner = _direct_context(spans, depths, keyword_match.start(), fallback)
        if direct and keyword_match.start() not in matched_keywords:
            raise LexError(
                f"unsupported {language} function syntax in {path}:{_line(text, keyword_match.start())}"
            )
    return declarations


def _swift_initializers(
    root: Path,
    path: Path,
    text: str,
    masked: str,
    spans: list[tuple[int, int, str]],
    depths: list[int],
) -> list[Declaration]:
    fallback = path.stem.replace("+Trace", "Trace")
    out: list[Declaration] = []
    ordinals: Counter[tuple[int, tuple[int, ...]]] = Counter()
    matched: set[int] = set()
    for match in re.finditer(r"\binit\s*(?:[?!]\s*)?\(", masked):
        direct, owner = _direct_context(spans, depths, match.start(), fallback)
        if not direct:
            continue
        matched.add(match.start())
        opening = match.end() - 1
        closing = _matching(masked, opening, "(", ")")
        arity, defaults = _defaults(masked, opening, closing)
        end, coverable = _body_extent(masked, closing, "swift")
        ordinal_key = (arity, defaults)
        ordinals[ordinal_key] += 1
        out.append(
            Declaration(
                "swift", ledger._relative(root, path), owner, "init", "initializer",
                arity, defaults, ordinals[ordinal_key], _line(text, match.start()),
                _line(text, end), coverable,
            )
        )
    for match in re.finditer(r"\binit\b", masked):
        direct, _owner = _direct_context(spans, depths, match.start(), fallback)
        # `.init(` call sites are not declarations.
        prefix = masked[max(0, match.start() - 2) : match.start()]
        if direct and not prefix.rstrip().endswith(".") and match.start() not in matched:
            raise LexError(f"unsupported Swift init syntax in {path}:{_line(text, match.start())}")
    return out


def _property_declarations(
    root: Path,
    path: Path,
    language: str,
    text: str,
    masked: str,
    spans: list[tuple[int, int, str]],
    depths: list[int],
) -> list[Declaration]:
    fallback = path.stem.replace("+Trace", "Trace")
    word = r"(?:var|let)" if language == "swift" else r"(?:val|var)"
    pattern = re.compile(rf"\b{word}\s+(`?[A-Za-z_][A-Za-z0-9_]*`?)")
    _layout_spans, type_headers = _type_layout(masked, language)
    out: list[Declaration] = []
    ordinals: Counter[tuple[str, str]] = Counter()
    matched_keywords: set[int] = set()
    matched_getters: set[int] = set()
    declaration_header = re.compile(
        r"(?m)^[ \t]*(?:(?:@[A-Za-z_][^\s]*|public|private|fileprivate|internal|protected|"
        r"open|static|final|override|required|convenience|mutating|nonmutating|lazy|weak|"
        r"unowned|abstract|const|lateinit|tailrec|suspend|inline|infix|operator|external|"
        r"data|sealed|annotation|value|inner|companion)\s+)*(?:func|fun|var|let|val|init\b|"
        r"constructor\b|struct\b|class\b|enum\b|actor\b|protocol\b|extension\b|object\b|interface\b)"
    )
    comment_masked = _mask_comments(text)
    for match in pattern.finditer(masked):
        direct, owner = _direct_context(spans, depths, match.start(), fallback)
        if not direct:
            continue
        matched_keywords.add(match.start())
        header = next(
            (item for item in type_headers if item[0] < match.start() < item[1]),
            None,
        )
        if header is not None:
            owner = header[2]
        name = match.group(1).strip("`")
        base_depth = depths[match.start()]
        containing = [item for item in spans if item[0] < match.start() < item[1]]
        limit = min((item[1] for item in containing), default=len(masked))
        if header is not None:
            stack: list[str] = []
            pairs = {")": "(", "]": "[", "}": "{", ">": "<"}
            limit = min(limit, header[1])
            for index in range(match.end(), limit):
                char = masked[index]
                if char in "([{<":
                    stack.append(char)
                elif char == ")" and not stack:
                    limit = index
                    break
                elif char in pairs:
                    if stack and stack[-1] == pairs[char]:
                        stack.pop()
                elif char == "," and not stack:
                    limit = index
                    break
        for candidate in declaration_header.finditer(masked, match.end(), limit):
            if depths[candidate.start()] == base_depth:
                limit = candidate.start()
                break
        for semicolon in re.finditer(r";", masked[match.end() : limit]):
            absolute = match.end() + semicolon.start()
            if depths[absolute] == base_depth:
                limit = absolute
                break
        tail = masked[match.end() : limit]
        equals = re.search(r"(?<![=!<>])=(?!=)", tail)
        getter = (
            re.search(r"(?<![.A-Za-z0-9_])get\s*\(\s*\)", tail)
            if language == "kotlin"
            else None
        )
        brace = tail.find("{")
        delegated = re.search(r"\bby\b", tail) if language == "kotlin" else None
        kind: str | None = None
        raw_tail = comment_masked[match.end() : limit]
        body_end = match.end() + len(raw_tail.rstrip())
        if language == "swift" and brace >= 0 and (equals is None or brace < equals.start()):
            kind = "computed-property"
            opening = match.end() + brace
            body_end = ledger._matching_brace(masked, opening)
        elif language == "kotlin" and getter is not None and (equals is None or getter.start() < equals.start()):
            kind = "computed-property"
            getter_absolute = match.end() + getter.start()
            matched_getters.add(getter_absolute)
            getter_brace = masked.find("{", getter_absolute, limit)
            getter_equals = masked.find("=", getter_absolute, limit)
            if getter_brace >= 0 and (getter_equals < 0 or getter_brace < getter_equals):
                body_end = ledger._matching_brace(masked, getter_brace)
        elif equals is not None or delegated is not None:
            kind = "property-initializer"
        if kind is None:
            if brace >= 0 or getter is not None:
                raise LexError(
                    f"unsupported {language} property syntax in {path}:{_line(text, match.start())}"
                )
            continue  # A stored/abstract property without executable code is known and excluded.
        ordinal_key = (name, kind)
        ordinals[ordinal_key] += 1
        out.append(
            Declaration(
                language, ledger._relative(root, path), owner, name, kind, 0, (),
                ordinals[ordinal_key], _line(text, match.start()), _line(text, body_end), True,
            )
        )
    for keyword_match in re.finditer(r"\b(?:var|let)\b" if language == "swift" else r"\b(?:val|var)\b", masked):
        direct, _owner = _direct_context(spans, depths, keyword_match.start(), fallback)
        if direct and keyword_match.start() not in matched_keywords:
            raise LexError(
                f"unsupported {language} property syntax in {path}:{_line(text, keyword_match.start())}"
            )
    if language == "kotlin":
        for getter_match in re.finditer(r"(?<![.A-Za-z0-9_])get\s*\(\s*\)", masked):
            direct, _owner = _direct_context(spans, depths, getter_match.start(), fallback)
            if direct and getter_match.start() not in matched_getters:
                raise LexError(
                    f"unsupported kotlin getter syntax in {path}:{_line(text, getter_match.start())}"
                )
    return out


def _kotlin_initializers(
    root: Path,
    path: Path,
    text: str,
    masked: str,
    spans: list[tuple[int, int, str]],
    depths: list[int],
) -> list[Declaration]:
    out: list[Declaration] = []
    fallback = path.stem.replace("+Trace", "Trace")
    ordinals: Counter[tuple[str, int, tuple[int, ...]]] = Counter()
    matched_tokens: set[int] = set()
    _layout_spans, type_headers = _type_layout(masked, "kotlin")
    companion_ranges: list[tuple[int, int, str]] = []
    for companion in re.finditer(r"\bcompanion\s+object(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\{", masked):
        opening = companion.end() - 1
        closing = ledger._matching_brace(masked, opening)
        owner = ledger._owner_at(spans, companion.start(), path.stem)
        companion_ranges.append((opening, closing, owner))
    for match in re.finditer(r"\binit\s*\{", masked):
        direct, owner = _direct_context(spans, depths, match.start(), fallback)
        name = "init"
        kind = "initializer"
        companion = next(
            (
                item
                for item in companion_ranges
                if item[0] < match.start() < item[1]
                and depths[match.start()] == depths[item[0]] + 1
            ),
            None,
        )
        if companion is not None:
            owner = companion[2]
            name = "<companion-init>"
            kind = "companion-init"
        elif not direct:
            raise LexError(f"unsupported Kotlin init syntax in {path}:{_line(text, match.start())}")
        matched_tokens.add(match.start())
        opening = match.end() - 1
        end = ledger._matching_brace(masked, opening)
        ordinal_key = (name, 0, ())
        ordinals[ordinal_key] += 1
        out.append(
            Declaration(
                "kotlin", ledger._relative(root, path), owner, name, kind, 0, (),
                ordinals[ordinal_key], _line(text, match.start()), _line(text, end), True,
            )
        )
    for match in re.finditer(r"\bconstructor\s*\(", masked):
        if any(start < match.start() < end for start, end, _owner in type_headers):
            matched_tokens.add(match.start())
            continue
        direct, owner = _direct_context(spans, depths, match.start(), fallback)
        if not direct:
            raise LexError(
                f"unsupported Kotlin constructor syntax in {path}:{_line(text, match.start())}"
            )
        matched_tokens.add(match.start())
        opening = match.end() - 1
        closing = _matching(masked, opening, "(", ")")
        arity, defaults = _defaults(masked, opening, closing)
        end, _body = _body_extent(masked, closing, "kotlin")
        if end == closing:
            newline = masked.find("\n", closing)
            end = len(masked) if newline < 0 else newline
        ordinal_key = ("constructor", arity, defaults)
        ordinals[ordinal_key] += 1
        out.append(
            Declaration(
                "kotlin", ledger._relative(root, path), owner, "constructor", "initializer",
                arity, defaults, ordinals[ordinal_key], _line(text, match.start()),
                _line(text, end), True,
            )
        )
    for token in re.finditer(r"\b(?:init|constructor)\b", masked):
        if token.start() not in matched_tokens:
            raise LexError(
                f"unsupported Kotlin {token.group(0)} syntax in {path}:{_line(text, token.start())}"
            )
    return out


def lex_file(root: Path, path: Path, language: str) -> list[Declaration]:
    """Inventory executable declarations or raise when syntax cannot be classified."""
    root = root.resolve()
    path = path.resolve()
    if language not in {"swift", "kotlin"}:
        raise ValueError(f"unknown language {language!r}")
    text = path.read_text(encoding="utf-8")
    masked = ledger._mask_non_code(text)
    depths = _brace_depths(masked)
    spans, _headers = _type_layout(masked, language)
    out = _function_declarations(root, path, language, text, masked, spans, depths)
    if language == "swift":
        out.extend(_swift_initializers(root, path, text, masked, spans, depths))
    out.extend(_property_declarations(root, path, language, text, masked, spans, depths))
    if language == "kotlin":
        out.extend(_kotlin_initializers(root, path, text, masked, spans, depths))
    keys = [item.key for item in out]
    if len(keys) != len(set(keys)):
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        raise LexError(f"declaration key collision in {path}: {duplicates}")
    return sorted(out, key=lambda item: (item.line, item.key))


def lex_tree(root: Path, directory: Path, language: str) -> list[Declaration]:
    suffix = ".swift" if language == "swift" else ".kt"
    return [
        declaration
        for path in sorted(directory.rglob(f"*{suffix}"))
        for declaration in lex_file(root, path, language)
    ]


def _entry(raw: object, category: str, path: Path) -> ShardEntry:
    if not isinstance(raw, dict):
        raise RatchetError(f"{path}: {category} entries must be objects")
    key = raw.get("key")
    if not isinstance(key, str) or not key:
        raise RatchetError(f"{path}: {category} entry needs a non-empty key")
    issue = raw.get("issue")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise RatchetError(f"{path}: {category} entry {key} needs a positive issue")
    reason = raw.get("reason")
    test = raw.get("test")
    if category == "exempt" and (not isinstance(reason, str) or not reason.strip()):
        raise RatchetError(f"{path}: exempt entry {key} needs a non-empty reason")
    if category == "platform-test" and (not isinstance(test, str) or not test.strip()):
        raise RatchetError(f"{path}: platform-test entry {key} needs a non-empty test reference")
    return ShardEntry(key, issue, reason if isinstance(reason, str) else None, test if isinstance(test, str) else None)


def load_shard(path: Path) -> Shard:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RatchetError(f"cannot read shard {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise RatchetError(f"{path}: schema_version must be 1")
    module = raw.get("module")
    if not isinstance(module, str) or not module:
        raise RatchetError(f"{path}: module must be a non-empty string")
    exempt_raw = raw.get("exempt")
    platform_raw = raw.get("platform-test")
    if not isinstance(exempt_raw, list) or not isinstance(platform_raw, list):
        raise RatchetError(f"{path}: exempt and platform-test must be arrays")
    exempt = tuple(_entry(item, "exempt", path) for item in exempt_raw)
    platform = tuple(_entry(item, "platform-test", path) for item in platform_raw)
    keys = [item.key for item in exempt + platform]
    if len(keys) != len(set(keys)):
        raise RatchetError(f"{path}: a declaration may occur only once")
    return Shard(path, module, exempt, platform)


def registered_differential(root: Path) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    try:
        # Registration names are string literals, so the ledger's normal source mask
        # would intentionally erase the very tokens we need here.  Anchor the raw-text
        # patterns to dispatcher syntax instead; prose/comments do not contain these
        # case-label/arrow forms in either runner.
        swift = (root / SWIFT_RUNNER).read_text(encoding="utf-8")
        kotlin = (root / KOTLIN_RUNNER).read_text(encoding="utf-8")
    except OSError as exc:
        return set(), [f"cannot read differential runner: {exc}"]
    swift_names = set(re.findall(r"\bcase\s+\"([A-Za-z_][A-Za-z0-9_]*)\"\s*:", swift))
    kotlin_names = set(re.findall(r"\"([A-Za-z_][A-Za-z0-9_]*)\"\s*->", kotlin))
    if swift_names != kotlin_names:
        errors.append(
            "differential runner registrations disagree: "
            f"swift-only={sorted(swift_names - kotlin_names)} "
            f"kotlin-only={sorted(kotlin_names - swift_names)}"
        )
    if not swift_names & kotlin_names:
        errors.append("differential runner registry is empty")
    return swift_names & kotlin_names, errors


def _resolved_differential(
    inventories: dict[str, list[Declaration]], registered: set[str]
) -> set[str]:
    return {
        name
        for name in registered
        if all(
            len(
                [
                    item
                    for item in inventories[language]
                    if item.kind == "function" and item.name == name
                ]
            )
            == 1
            for language in ("swift", "kotlin")
        )
    }


def _audit_module_pair(
    root: Path,
    pair: ModulePair,
    *,
    base_shards: set[str] | None = None,
    registered: set[str] | None = None,
) -> tuple[list[str], dict[str, int], set[str]]:
    counts = {"differential": 0, "platform-test": 0, "exempt": 0}
    swift_shard_path = pair.swift_dir / SHARD_NAME
    kotlin_shard_path = pair.kotlin_dir / SHARD_NAME
    exists = (swift_shard_path.exists(), kotlin_shard_path.exists())
    if not any(exists):
        relative_shards = {
            swift_shard_path.relative_to(root).as_posix(),
            kotlin_shard_path.relative_to(root).as_posix(),
        }
        if base_shards is not None and relative_shards & base_shards:
            return [f"{pair.name}: parity ratchet disarmed; merge base had shards, current tree has none"], counts, set()
        print(f"NOTICE: {pair.name}: no parity-exempt shards; module pair is not sharp yet")
        return [], counts, set()
    if not all(exists):
        missing = kotlin_shard_path if exists[0] else swift_shard_path
        return [f"{pair.name}: sharp module pair is missing {missing.relative_to(root)}"], counts, set()
    errors: list[str] = []
    try:
        shards = (load_shard(swift_shard_path), load_shard(kotlin_shard_path))
        inventories = {
            "swift": lex_tree(root, pair.swift_dir, "swift"),
            "kotlin": lex_tree(root, pair.kotlin_dir, "kotlin"),
        }
    except RatchetError as exc:
        return [str(exc)], counts, set()
    if registered is None:
        registered, registry_errors = registered_differential(root)
    else:
        registry_errors = []
    errors.extend(registry_errors)
    pair_registered = _resolved_differential(inventories, registered)
    differential_keys: set[str] = set()
    for language, declarations in inventories.items():
        for name in sorted(pair_registered):
            match = next(
                item for item in declarations if item.kind == "function" and item.name == name
            )
            differential_keys.add(match.key)
    counts["differential"] = len(pair_registered)
    for language, shard in zip(("swift", "kotlin"), shards):
        inventory_keys = {item.key for item in inventories[language]}
        exempt_keys = {item.key for item in shard.exempt}
        platform_keys = {item.key for item in shard.platform_test}
        counts["exempt"] += len(exempt_keys)
        counts["platform-test"] += len(platform_keys)
        for key in sorted((exempt_keys | platform_keys) - inventory_keys):
            errors.append(f"{shard.path.relative_to(root)}: stale declaration {key}")
        covered = exempt_keys | platform_keys | differential_keys
        for declaration in inventories[language]:
            if declaration.key not in covered:
                errors.append(
                    f"{declaration.path}:{declaration.line}: uncovered declaration {declaration.key}"
                )
        for key in sorted((exempt_keys | platform_keys) & differential_keys):
            errors.append(f"{shard.path.relative_to(root)}: differential declaration listed as exception: {key}")
    return errors, counts, pair_registered


def audit_module_pair(
    root: Path, pair: ModulePair, *, base_shards: set[str] | None = None
) -> tuple[list[str], dict[str, int]]:
    errors, counts, _resolved = _audit_module_pair(root, pair, base_shards=base_shards)
    return errors, counts


def audit_inventory(root: Path, *, base: str | None = None) -> tuple[list[str], dict[str, int]]:
    registered, errors = registered_differential(root)
    total = {"differential": len(registered), "platform-test": 0, "exempt": 0}
    resolved: set[str] = set()
    base_shards = _base_shards(root, base) if base is not None else None
    for pair in module_pairs(root):
        pair_errors, counts, pair_resolved = _audit_module_pair(
            root,
            pair,
            base_shards=base_shards,
            registered=registered,
        )
        errors.extend(pair_errors)
        resolved.update(pair_resolved)
        for key in ("platform-test", "exempt"):
            total[key] += counts[key]
    for name in sorted(registered - resolved):
        errors.append(
            f"differential function {name} does not resolve exactly once in any sharp module pair"
        )
    return errors, total


def parse_lcov(path: Path) -> dict[str, CoverageSource]:
    coverage: dict[str, CoverageSource] = {}
    current: str | None = None
    function_lines: list[tuple[int, str]] = []
    function_counts: Counter[str] = Counter()

    def finish_record() -> None:
        if current is None:
            return
        source = coverage[current]
        source.methods.extend(
            CoverageMethod(name, line, function_counts[name])
            for line, name in function_lines
        )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RatchetError(f"cannot read Swift LCOV report {path}: {exc}") from exc
    for line_number, raw in enumerate(lines, 1):
        if raw.startswith("SF:"):
            finish_record()
            current = raw[3:]
            coverage.setdefault(current, CoverageSource({}, []))
            function_lines = []
            function_counts = Counter()
        elif raw.startswith("FN:"):
            if current is None:
                raise RatchetError(f"{path}:{line_number}: FN record before SF")
            fields = raw[3:].split(",", 1)
            if len(fields) != 2:
                raise RatchetError(f"{path}:{line_number}: malformed FN record")
            function_lines.append((int(fields[0]), fields[1]))
        elif raw.startswith("FNDA:"):
            if current is None:
                raise RatchetError(f"{path}:{line_number}: FNDA record before SF")
            fields = raw[5:].split(",", 1)
            if len(fields) != 2:
                raise RatchetError(f"{path}:{line_number}: malformed FNDA record")
            function_counts[fields[1]] += int(fields[0])
        elif raw.startswith("DA:"):
            if current is None:
                raise RatchetError(f"{path}:{line_number}: DA record before SF")
            fields = raw[3:].split(",")
            if len(fields) < 2:
                raise RatchetError(f"{path}:{line_number}: malformed DA record")
            source_lines = coverage[current].lines
            source_lines[int(fields[0])] = source_lines.get(int(fields[0]), 0) + int(fields[1])
        elif raw == "end_of_record":
            finish_record()
            current = None
            function_lines = []
            function_counts = Counter()
    finish_record()
    if not coverage:
        raise RatchetError(f"Swift LCOV report {path} contains no source records")
    return coverage


def parse_jacoco(path: Path) -> dict[str, CoverageSource]:
    coverage: dict[str, CoverageSource] = {}
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        raise RatchetError(f"cannot read Kotlin JaCoCo report {path}: {exc}") from exc
    for package in tree.getroot().iter("package"):
        package_name = package.get("name", "")
        for source in package.findall("sourcefile"):
            name = source.get("name")
            if not name:
                continue
            source_path = f"{package_name}/{name}" if package_name else name
            record = coverage.setdefault(source_path, CoverageSource({}, []))
            for item in source.findall("line"):
                number = int(item.attrib["nr"])
                record.lines[number] = int(item.attrib.get("ci", "0"))
        for class_item in package.findall("class"):
            source_name = class_item.get("sourcefilename")
            if not source_name:
                continue
            source_path = f"{package_name}/{source_name}" if package_name else source_name
            record = coverage.setdefault(source_path, CoverageSource({}, []))
            for method in class_item.findall("method"):
                name = method.get("name")
                line = method.get("line")
                counter = next(
                    (item for item in method.findall("counter") if item.get("type") == "METHOD"),
                    None,
                )
                if name and line and counter is not None:
                    record.methods.append(
                        CoverageMethod(
                            name,
                            int(line),
                            int(counter.get("covered", "0")),
                            _jvm_descriptor_arity(method.get("desc")),
                        )
                    )
    if not coverage:
        raise RatchetError(f"Kotlin JaCoCo report {path} contains no source records")
    return coverage


def _coverage_for(
    report: dict[str, CoverageSource], declaration: Declaration
) -> CoverageSource | None:
    normalized = declaration.path.replace("\\", "/")
    candidates = [source for path, source in report.items() if normalized.endswith(path.replace("\\", "/")) or path.replace("\\", "/").endswith(normalized)]
    if len(candidates) == 1:
        return candidates[0]
    # JaCoCo paths begin at the package; filename is safe only when unique.
    filename_matches = [source for path, source in report.items() if Path(path).name == Path(normalized).name]
    return filename_matches[0] if len(filename_matches) == 1 else None


def _function_executions(
    source: CoverageSource, declaration: Declaration, language: str
) -> tuple[str, int]:
    """Return (found|missing|fallback, executions) for a function entry record."""
    if not source.methods:
        return "fallback", 0
    if language == "swift":
        encoded = f"{len(declaration.name)}{declaration.name}"
        named = [
            item
            for item in source.methods
            if item.name == declaration.name or encoded in item.name
        ]
        # Swift mangling may substitute repeated name fragments (for example
        # `trimpToStrain` becomes `trimpToC0_`).  Source position is still an
        # exact function-level record when there is one unambiguous entry.
        if not named:
            positioned = [
                item
                for item in source.methods
                if declaration.line <= item.line <= declaration.end_line
            ]
            if positioned:
                first_line = min(item.line for item in positioned)
                first = [item for item in positioned if item.line == first_line]
                if len(first) == 1:
                    return "found", first[0].executions
    else:
        named = [
            item
            for item in source.methods
            if item.name == declaration.name
            and (item.arity is None or item.arity == declaration.arity)
        ]
    candidates = [
        item
        for item in named
        if declaration.line <= item.line <= declaration.end_line
    ]
    if not candidates:
        if language == "kotlin" and len(named) == 1:
            return "found", named[0].executions
        return "missing", 0
    entry_line = min(item.line for item in candidates)
    return "found", sum(item.executions for item in candidates if item.line == entry_line)


def coverage_errors(
    declarations: dict[str, list[Declaration]],
    differential: set[str],
    *,
    swift_lcov: Path,
    kotlin_jacoco: Path,
    platform_keys: dict[str, set[str]] | None = None,
    differential_groups: list[tuple[dict[str, list[Declaration]], set[str]]] | None = None,
) -> list[str]:
    reports = {"swift": parse_lcov(swift_lcov), "kotlin": parse_jacoco(kotlin_jacoco)}
    reports_have_methods = {
        language: any(source.methods for source in report.values())
        for language, report in reports.items()
    }
    errors: list[str] = []
    selected: dict[str, list[Declaration]] = {"swift": [], "kotlin": []}
    groups = differential_groups or [(declarations, differential)]
    for group_declarations, group_names in groups:
        for language in selected:
            for name in sorted(group_names):
                matches = [
                    item
                    for item in group_declarations[language]
                    if item.kind == "function" and item.name == name
                ]
                if len(matches) != 1:
                    errors.append(
                        f"differential {language} function {name} resolves to {len(matches)} declarations"
                    )
                else:
                    selected[language].append(matches[0])
    for language in selected:
        if platform_keys:
            keyed = {item.key: item for item in declarations[language]}
            for key in sorted(platform_keys.get(language, set())):
                if key in keyed:
                    selected[language].append(keyed[key])
    for language, items in selected.items():
        for declaration in items:
            if not declaration.coverable:
                errors.append(f"{declaration.path}:{declaration.line}: {declaration.key} has no executable body")
                continue
            source = _coverage_for(reports[language], declaration)
            if source is None:
                errors.append(f"{declaration.path}:{declaration.line}: no {language} coverage source record")
                continue
            if declaration.kind == "function":
                if not source.methods and reports_have_methods[language]:
                    errors.append(
                        f"{declaration.path}:{declaration.line}: no {language} function coverage record for {declaration.key}"
                    )
                    continue
                method_status, executions = _function_executions(source, declaration, language)
                if method_status == "found":
                    if executions <= 0:
                        errors.append(
                            f"{declaration.path}:{declaration.line}: registered function was not executed ({declaration.key})"
                        )
                    continue
                if method_status == "missing":
                    errors.append(
                        f"{declaration.path}:{declaration.line}: no {language} function coverage record for {declaration.key}"
                    )
                    continue
            print(
                f"NOTICE: line coverage fallback for {declaration.path}:{declaration.line} ({declaration.key})"
            )
            instrumented = sorted(
                line for line in source.lines if declaration.line <= line <= declaration.end_line
            )
            if not instrumented:
                errors.append(f"{declaration.path}:{declaration.line}: no instrumented line for {declaration.key}")
                continue
            for line in instrumented:
                if source.lines[line] <= 0:
                    errors.append(f"{declaration.path}:{line}: registered declaration line was not executed ({declaration.key})")
    return errors


def verify_coverage(root: Path, swift_lcov: Path, kotlin_jacoco: Path) -> tuple[list[str], dict[str, int]]:
    inventory_errors, counts = audit_inventory(root)
    if inventory_errors:
        return inventory_errors, counts
    registered, errors = registered_differential(root)
    declarations: dict[str, list[Declaration]] = {"swift": [], "kotlin": []}
    differential_groups: list[tuple[dict[str, list[Declaration]], set[str]]] = []
    platform: dict[str, set[str]] = {"swift": set(), "kotlin": set()}
    for pair in module_pairs(root):
        swift_shard = pair.swift_dir / SHARD_NAME
        kotlin_shard = pair.kotlin_dir / SHARD_NAME
        if not (swift_shard.exists() and kotlin_shard.exists()):
            continue
        pair_declarations = {
            "swift": lex_tree(root, pair.swift_dir, "swift"),
            "kotlin": lex_tree(root, pair.kotlin_dir, "kotlin"),
        }
        pair_registered = _resolved_differential(pair_declarations, registered)
        differential_groups.append((pair_declarations, pair_registered))
        declarations["swift"].extend(pair_declarations["swift"])
        declarations["kotlin"].extend(pair_declarations["kotlin"])
        platform["swift"].update(item.key for item in load_shard(swift_shard).platform_test)
        platform["kotlin"].update(item.key for item in load_shard(kotlin_shard).platform_test)
    errors.extend(
        coverage_errors(
            declarations,
            registered,
            swift_lcov=swift_lcov,
            kotlin_jacoco=kotlin_jacoco,
            platform_keys=platform,
            differential_groups=differential_groups,
        )
    )
    return errors, counts


def _git(root: Path, args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root, text=True, stderr=subprocess.STDOUT).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.output.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.output else str(exc)
        raise RatchetError(f"git {' '.join(args)} failed: {detail}") from exc


def resolve_base(root: Path, base: str | None) -> str:
    if base:
        candidate = _git(root, ["rev-parse", "--verify", base])
        return _git(root, ["merge-base", "HEAD", candidate])
    return _git(root, ["merge-base", "HEAD", "origin/main"])


def _base_json(root: Path, base: str, relative: str) -> dict | None:
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{base}:{relative}"], cwd=root, text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RatchetError(f"{base}:{relative}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RatchetError(f"{base}:{relative}: JSON root must be an object")
    return value


def _current_shards(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(SHARD_NAME)
        if ".git" not in path.parts and ".build" not in path.parts and "build" not in path.parts
    )


def _base_shards(root: Path, base: str) -> set[str]:
    listing = _git(root, ["ls-tree", "-r", "--name-only", base])
    return {line for line in listing.splitlines() if line.endswith(f"/{SHARD_NAME}")}


def _issue_exists(issue: int) -> bool:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise RatchetError("GITHUB_REPOSITORY is required for CI issue validation")
    try:
        subprocess.run(
            ["gh", "api", f"repos/{repository}/issues/{issue}", "--silent"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RatchetError("gh is required for CI issue validation (or pass --offline)") from exc
    except subprocess.CalledProcessError:
        return False
    return True


def compare_ratchet(root: Path, base: str, *, offline: bool) -> list[str]:
    """Compare the working tree (not HEAD) with base, closing same-PR baseline holes."""
    root = root.resolve()
    errors: list[str] = []
    issues: set[int] = set()
    current_paths = _current_shards(root)
    current_relative = {path.relative_to(root).as_posix() for path in current_paths}
    for relative in sorted(_base_shards(root, base) - current_relative):
        errors.append(f"{relative}: merge-base ratchet shard was removed (disarmed)")
    for path in current_paths:
        current = load_shard(path)
        issues.update(item.issue for item in current.exempt + current.platform_test)

    baseline_path = root / LEDGER_BASELINE
    try:
        current_baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RatchetError(f"cannot read {LEDGER_BASELINE}: {exc}") from exc
    old_baseline = _base_json(root, base, LEDGER_BASELINE)
    if old_baseline is not None:
        old_findings = {
            item.get("identity") for item in old_baseline.get("findings", []) if isinstance(item, dict)
        }
        for item in current_baseline.get("findings", []):
            if not isinstance(item, dict) or not isinstance(item.get("identity"), str):
                errors.append(f"{LEDGER_BASELINE}: malformed finding entry")
                continue
            if item["identity"] not in old_findings:
                issue = item.get("issue")
                if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
                    errors.append(
                        f"{LEDGER_BASELINE}: new finding {item['identity']} needs issue"
                    )
                else:
                    issues.add(issue)
        old_counters = old_baseline.get("counters", {})
        current_counters = current_baseline.get("counters", {})
        if not isinstance(old_counters, dict) or not isinstance(current_counters, dict):
            errors.append(f"{LEDGER_BASELINE}: counters must be objects")
        else:
            for name, value in current_counters.items():
                old_value = old_counters.get(name, 0)
                if not isinstance(value, int) or not isinstance(old_value, int):
                    errors.append(f"{LEDGER_BASELINE}: counter {name} must be an integer")
                elif value > old_value:
                    errors.append(
                        f"{LEDGER_BASELINE}: counter {name} increased {old_value}->{value}"
                    )
    validate_online = not offline and os.environ.get("CI", "").lower() == "true"
    if validate_online:
        for issue in sorted(issues):
            if not _issue_exists(issue):
                errors.append(f"issue #{issue} does not exist or is not accessible")
    return errors


def _print_result(errors: Iterable[str], counts: dict[str, int]) -> int:
    errors = list(errors)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(
        "parity ratchet: "
        f"differential={counts['differential']} "
        f"platform-test={counts['platform-test']} exempt={counts['exempt']} "
        f"errors={len(errors)}"
    )
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="prove registered declarations were executed")
    verify.add_argument("--swift-lcov", type=Path)
    verify.add_argument("--kotlin-jacoco", type=Path)
    ratchet = subparsers.add_parser("ratchet", help="audit inventory and compare debt with merge base")
    ratchet.add_argument("--base", help="explicit base ref when origin/main is unavailable")
    ratchet.add_argument("--offline", action="store_true", help="skip CI gh issue existence checks")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "ratchet" and args.offline and os.environ.get("CI", "").lower() == "true":
            raise RatchetError("--offline is for local ratchet runs and is forbidden when CI=true")
        if args.command == "verify":
            missing = []
            if args.swift_lcov is None:
                missing.append("--swift-lcov")
            if args.kotlin_jacoco is None:
                missing.append("--kotlin-jacoco")
            if missing:
                print(f"verify requires {' and '.join(missing)}", file=sys.stderr)
                return 2
            errors, counts = verify_coverage(root, args.swift_lcov, args.kotlin_jacoco)
            return _print_result(errors, counts)
        base = resolve_base(root, args.base)
        inventory_errors, counts = audit_inventory(root, base=base)
        errors = inventory_errors + compare_ratchet(root, base, offline=args.offline)
        return _print_result(errors, counts)
    except RatchetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
