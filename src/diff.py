"""Strict unified diffs. Line numbers in Edit are zero-based, half-open."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


class Unsupported(ValueError):
    """The patch cannot be attributed to baseline dependencies safely."""


@dataclass(frozen=True)
class Edit:
    old_start: int
    old_end: int
    new_start: int
    new_end: int


@dataclass
class Hunk:
    old: int
    new: int
    lines: list[tuple[str, str]]


@dataclass
class Change:
    path: str
    hunks: list[Hunk]
    kind: str = 'modify'

    def apply(self, source: str) -> tuple[str, list[Edit]]:
        if self.kind == 'add' and source:
            raise Unsupported('new file already exists')
        original = lf_lines(source)
        result = []
        edits = []
        cursor = 0
        for hunk in self.hunks:
            if not cursor <= hunk.old <= len(original):
                raise Unsupported("overlapping or out-of-range hunk")
            result.extend(original[cursor:hunk.old])
            cursor = hunk.old
            if len(result) != hunk.new:
                raise Unsupported("new coordinates do not match previous hunks")
            start = None
            for tag, line in hunk.lines:
                if tag == " " and start is not None:
                    edits.append(Edit(start[0], cursor, start[1], len(result)))
                    start = None
                elif tag != " " and start is None:
                    start = cursor, len(result)
                if tag != "+":
                    if cursor >= len(original) or original[cursor] != line:
                        raise Unsupported("hunk context differs from the pinned source")
                    cursor += 1
                if tag != "-":
                    result.append(line)
            if start is not None:
                edits.append(Edit(start[0], cursor, start[1], len(result)))
        result.extend(original[cursor:])
        if not edits:
            raise Unsupported("no changed lines")
        if self.kind == 'delete' and result:
            raise Unsupported('deleted file has remaining content')
        return "".join(result), edits


def lf_lines(text: str) -> list[str]:
    # str.splitlines also treats form feeds and several Unicode characters as
    # source lines, unlike git. Keep the absence of a final newline observable.
    pieces = text.replace("\r\n", "\n").split("\n")
    lines = [p + "\n" for p in pieces[:-1]]
    if pieces[-1]:
        lines.append(pieces[-1])
    return lines


_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?")


def header_path(line: str, side: str) -> str:
    name = line[4:].rstrip("\n").split("\t", 1)[0]
    if not name.startswith(side + "/"):
        raise Unsupported("new, deleted or quoted path")
    name = name[2:]
    if (not name or name.startswith("/") or "\\" in name or ":" in name or
            any(p in ("", ".", "..", ".git") for p in name.split("/")) or
            PurePosixPath(name).is_absolute()):
        raise Unsupported("unsupported path")
    return name


def parse_patch(text: str) -> list[Change]:
    lines = lf_lines(text)
    changes = []
    seen = set()
    i = 0
    while i < len(lines):
        git_header = None
        declared_kind = None
        if lines[i].startswith("diff --git "):
            git_header = lines[i].rstrip("\n")
            i += 1
            if i < len(lines) and lines[i].startswith(('new file mode ', 'deleted file mode ')):
                mode = lines[i].rstrip('\n')
                if mode not in {'new file mode 100644','deleted file mode 100644'}:
                    raise Unsupported('unsupported file mode')
                declared_kind = 'add' if mode.startswith('new ') else 'delete'
                i += 1
            if i < len(lines) and lines[i].startswith("index "):
                i += 1
        if (i + 1 >= len(lines) or not lines[i].startswith("--- ") or
                not lines[i + 1].startswith("+++ ")):
            raise Unsupported("unsupported diff metadata")
        old_null = lines[i][4:].rstrip('\n').split('\t',1)[0] == '/dev/null'
        new_null = lines[i+1][4:].rstrip('\n').split('\t',1)[0] == '/dev/null'
        if old_null and new_null:
            raise Unsupported('both paths are null')
        kind = 'add' if old_null else 'delete' if new_null else 'modify'
        if declared_kind is not None and declared_kind != kind:
            raise Unsupported('inconsistent creation/deletion metadata')
        path = header_path(lines[i+1], 'b') if old_null else header_path(lines[i], 'a')
        if (not new_null and path != header_path(lines[i + 1], "b")) or path in seen:
            raise Unsupported("renamed or repeated file")
        if git_header is not None and git_header != f"diff --git a/{path} b/{path}":
            raise Unsupported("ambiguous git paths")
        seen.add(path)
        i += 2
        hunks = []
        while i < len(lines) and lines[i].startswith("@@ "):
            match = _HEADER.fullmatch(lines[i].rstrip("\n"))
            if not match:
                raise Unsupported("bad hunk header")
            old, old_count, new, new_count = (int(x) if x is not None else 1 for x in match.groups())
            old -= int(old_count > 0)
            new -= int(new_count > 0)
            body = []
            i += 1
            while old_count or new_count:
                if i == len(lines) or not lines[i] or lines[i][0] not in " +-":
                    raise Unsupported("short hunk")
                tag, value = lines[i][0], lines[i][1:]
                i += 1
                if i < len(lines) and lines[i].rstrip("\n") == "\\ No newline at end of file":
                    value = value.removesuffix("\n")
                    i += 1
                body.append((tag, value))
                old_count -= int(tag != "+")
                new_count -= int(tag != "-")
                if min(old_count, new_count) < 0:
                    raise Unsupported("incorrect hunk counts")
            hunks.append(Hunk(old, new, body))
        if not hunks:
            raise Unsupported("missing hunks")
        changes.append(Change(path, hunks, kind))
    if not changes:
        raise Unsupported("empty patch")
    return changes
