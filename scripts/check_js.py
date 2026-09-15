#!/usr/bin/env python3
"""Scan inline JavaScript for the syntax errors that blank a page.

There is no Node on this machine, so the browser is the first thing that would ever
execute this code. An unbalanced brace or an unterminated template literal shows up as
a completely blank page with one line in a console nobody is looking at, which is a
miserable way to find out.

This is a scanner, not a parser: it tokenises strings, template literals, comments and
regex literals well enough to check that everything balances and nothing is left open.
It cannot judge semantics. It catches the class of mistake that actually happens when
writing a large inline script by hand.

    python3 scripts/check_js.py web/*.html
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# A "/" starts a regex when the previous meaningful character cannot end an expression.
REGEX_OK_AFTER = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}
KEYWORD_BEFORE_REGEX = ("return", "typeof", "instanceof", "in", "of", "new", "delete",
                        "void", "throw", "case", "do", "else")


def scan(js: str, label: str) -> list[str]:
    problems: list[str] = []
    stack: list[tuple[str, int]] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    # Template literals nest: `a ${ `b` } c`. Track depth so ${...} braces match up.
    template_depth: list[int] = []

    i, line, prev = 0, 1, ""
    n = len(js)
    while i < n:
        c = js[i]
        if c == "\n":
            line += 1
            i += 1
            continue

        # comments
        if c == "/" and i + 1 < n and js[i + 1] == "/":
            j = js.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and js[i + 1] == "*":
            j = js.find("*/", i + 2)
            if j == -1:
                problems.append(f"{label}:{line}: unterminated block comment")
                break
            line += js.count("\n", i, j)
            i = j + 2
            continue

        # strings
        if c in "'\"":
            j, ok = i + 1, False
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "\n":
                    break
                if js[j] == c:
                    ok = True
                    break
                j += 1
            if not ok:
                problems.append(f"{label}:{line}: unterminated {c} string")
                break
            i = j + 1
            prev = c
            continue

        # template literal
        if c == "`":
            j = i + 1
            closed = False
            while j < n:
                if js[j] == "\\":
                    j += 2
                    continue
                if js[j] == "`":
                    closed = True
                    break
                if js[j] == "$" and j + 1 < n and js[j + 1] == "{":
                    # hand control back to the main loop inside the substitution
                    template_depth.append(len(stack))
                    stack.append(("${", line))
                    i = j + 2
                    break
                if js[j] == "\n":
                    line += 1
                j += 1
            else:
                problems.append(f"{label}:{line}: unterminated template literal")
                break
            if closed:
                line += js.count("\n", i, j)
                i = j + 1
                prev = "`"
            continue

        # regex literal
        if c == "/":
            p = prev.strip()
            starts_regex = (
                not p
                or p[-1] in REGEX_OK_AFTER
                or any(re.search(rf"\b{k}$", p) for k in KEYWORD_BEFORE_REGEX)
            )
            if starts_regex:
                j, ok, in_class = i + 1, False, False
                while j < n:
                    if js[j] == "\\":
                        j += 2
                        continue
                    if js[j] == "[":
                        in_class = True
                    elif js[j] == "]":
                        in_class = False
                    elif js[j] == "/" and not in_class:
                        ok = True
                        break
                    elif js[j] == "\n":
                        break
                    j += 1
                if ok:
                    i = j + 1
                    while i < n and js[i].isalpha():
                        i += 1
                    prev = "/"
                    continue

        if c in "([{":
            stack.append((c, line))
        elif c in ")]}":
            if c == "}" and stack and stack[-1][0] == "${":
                stack.pop()
                if template_depth:
                    template_depth.pop()
                # Resume the template literal this substitution sat inside. If neither
                # a closing backtick nor another substitution turns up, the template was
                # never terminated, which is the exact mistake a long inline script
                # invites and the one that blanks a page with no useful error.
                j = i + 1
                resumed = None
                while j < n:
                    if js[j] == "\\":
                        j += 2
                        continue
                    if js[j] == "`":
                        resumed = "close"
                        break
                    if js[j] == "$" and j + 1 < n and js[j + 1] == "{":
                        stack.append(("${", line))
                        resumed = "subst"
                        break
                    if js[j] == "\n":
                        line += 1
                    j += 1
                if resumed is None:
                    problems.append(f"{label}:{line}: unterminated template literal")
                    break
                i = j + 1 if resumed == "close" else j + 2
                continue
            if not stack:
                problems.append(f"{label}:{line}: stray '{c}'")
                break
            opener, opened = stack.pop()
            if opener != pairs[c]:
                problems.append(f"{label}:{line}: '{c}' closes '{opener}' opened at line {opened}")
                break

        if not c.isspace():
            prev = (prev + c)[-24:]
        i += 1

    for opener, opened in stack:
        problems.append(f"{label}: '{opener}' opened at line {opened} was never closed")
    return problems


def check_i18n(text: str, label: str) -> list[str]:
    """Every t("key") must exist, and both languages must define the same keys.

    A missing key renders as a blank label, which looks like a layout bug rather than
    a translation gap and so tends to survive a long time.
    """
    m = re.search(r"const I18N=\{(.*?)\n\};", text, re.S)
    if not m:
        return []
    blocks = re.split(r"\n\s*(th|en):\s*\{", m.group(1))
    langs: dict[str, set] = {}
    for i in range(1, len(blocks), 2):
        keys = set(re.findall(r'(?:^|,|\{)\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', blocks[i + 1]))
        langs[blocks[i]] = keys
    th, en = langs.get("th", set()), langs.get("en", set())
    out = []
    for missing, where in ((th - en, "en"), (en - th, "th")):
        if missing:
            out.append(f"{label}: {where} is missing {sorted(missing)}")
    # The lookbehind stops ".get(" and similar from matching the t( call.
    used = set(re.findall(r'(?<![A-Za-z0-9_.$])t\("([A-Za-z_][A-Za-z0-9_]*)"\)', text))
    undefined = used - th
    if undefined:
        out.append(f"{label}: t() uses undefined keys {sorted(undefined)}")
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_js.py <file.html|file.js> ...")
        return 2
    total = 0
    for name in argv:
        p = Path(name)
        text = p.read_text(encoding="utf-8")
        blocks = []
        if p.suffix == ".js":
            blocks = [(text, 1)]
        else:
            for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", text, re.S):
                blocks.append((m.group(1), text.count("\n", 0, m.start(1)) + 1))
        if not blocks:
            print(f"  {p.name}: no inline script")
            continue
        found: list[str] = []
        for js, offset in blocks:
            found += scan(js, f"{p.name}(+{offset})")
        found += check_i18n(text, p.name)
        if found:
            total += len(found)
            print(f"  {p.name}: {len(found)} problem(s)")
            for f in found[:6]:
                print(f"      {f}")
        else:
            size = sum(len(b) for b, _ in blocks)
            keys = len(re.findall(r'(?:^|,|\{)\s*[A-Za-z_][A-Za-z0-9_]*\s*:',
                                  re.search(r"th:\s*\{(.*?)\n\s*en:", text, re.S).group(1))
                       ) if "const I18N=" in text else 0
            extra = f", {keys} i18n keys" if keys else ""
            print(f"  {p.name}: ok ({len(blocks)} block(s), {size:,} chars{extra})")
    print()
    print("FAIL" if total else "all inline scripts balance")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
