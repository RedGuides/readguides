#!/usr/bin/env python3
"""
Rewrite `### {{ renderMember(...) }}` + `:   description` pairs as `/// member` blocks, in place.

    python automation/migrate_rendermember.py docs/datatype-debuff.md docs/tlo-debuff.md

sync_forks.py runs it on each fork after merging upstream.

Cyrillic lookalikes (`ѕtring`, `іnt`), there to stop the macro linking `[string]`, go back to ASCII.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RE_CALL = re.compile(r"^#{1,6}[ \t]*\{\{\s*renderMember\((?P<args>.*)\)\s*\}\}[ \t]*$")
RE_KWARG = re.compile(r"""(\w+)\s*=\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
RE_DD = re.compile(r"^:[ \t]{0,4}(.*)$")
INDENT = "    "
LOOKALIKES = str.maketrans({"\u0455": "s", "\u0456": "i"})  # Cyrillic ѕ, і


def take_body(lines: list[str], i: int) -> tuple[list[str], int]:
    """Def_list body from the `:` line `lines[i]` to a blank line followed by an unindented one."""
    body = [RE_DD.match(lines[i]).group(1)]
    i += 1
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines) and (lines[j].startswith((INDENT, "\t")) or RE_DD.match(lines[j])):
                body.extend([""] * (j - i))
                i = j
                continue
            break
        if RE_CALL.match(line) or line.lstrip().startswith("<!--"):
            break
        if dd := RE_DD.match(line):
            body += ["", dd.group(1)]
        elif line.startswith(INDENT):
            body.append(line[len(INDENT) :])
        elif line.startswith("\t"):
            body.append(line[1:])
        else:
            body.append(line)  # lazy continuation
        i += 1
    while body and not body[-1].strip():
        body.pop()
    return body, i


def migrate(text: str) -> tuple[str, int, int]:
    """Returns (new text, calls converted, lookalikes replaced)."""
    lines = text.split("\n")
    out: list[str] = []
    i = count = fixed = 0
    while i < len(lines):
        m = RE_CALL.match(lines[i])
        raw = {k: a or b for k, a, b in RE_KWARG.findall(m.group("args"))} if m else {}
        if "name" not in raw:
            out.append(lines[i])
            i += 1
            continue
        kwargs = {k: v.translate(LOOKALIKES) for k, v in raw.items()}
        fixed += sum(kwargs[k] != raw[k] for k in raw)
        count += 1
        i += 1
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        body: list[str] = []
        if j < len(lines) and RE_DD.match(lines[j]):
            body, i = take_body(lines, j)

        member = f"{kwargs['name']}[{kwargs['params']}]" if kwargs.get("params") else kwargs["name"]
        arg = f"{kwargs['type']} {member}" if kwargs.get("type") else member
        out.append(f"/// member | {arg}")
        if "toc_label" in kwargs:
            out.append(f"{INDENT}label: {kwargs['toc_label']}")
        out += body + ["///"]
    return "\n".join(out), count, fixed


def migrate_file(path: Path) -> tuple[int, int]:
    """Convert `path` in place, keeping its line endings."""
    raw = path.read_bytes().decode("utf-8")
    eol = "\r\n" if "\r\n" in raw else "\n"
    new, count, fixed = migrate(raw.replace("\r\n", "\n"))
    if count:
        path.write_bytes(new.replace("\n", eol).encode("utf-8"))
    return count, fixed


def main(paths: list[str]) -> None:
    for name in paths:
        path = Path(name)
        count, fixed = migrate_file(path)
        note = f", {fixed} Cyrillic lookalike(s) replaced" if fixed else ""
        print(f"{path}: {count} renderMember call(s){note}")


if __name__ == "__main__":
    main(sys.argv[1:])
