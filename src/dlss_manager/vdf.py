"""Minimal parser for Valve's text KeyValues (VDF) format.

Steam's libraryfolders.vdf and appmanifest_*.acf files use this format. It's
simple enough (quoted strings + braces for nesting) that pulling in a
dependency for it isn't worth it.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def _unescape(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _tokenize(text: str) -> list[str]:
    tokens = []
    for m in _TOKEN_RE.finditer(text):
        if m.group(1) is not None:
            tokens.append(_unescape(m.group(1)))
        else:
            tokens.append(m.group(2))
    return tokens


def parse_vdf(text: str) -> dict:
    """Parse VDF text into nested dicts. Duplicate keys at the same level
    overwrite earlier ones, which is fine for our read-only use case."""
    tokens = _tokenize(text)
    pos = 0

    def parse_object() -> dict:
        nonlocal pos
        obj: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return obj
            key = tok
            pos += 1
            if pos >= len(tokens):
                obj[key] = ""
                break
            nxt = tokens[pos]
            if nxt == "{":
                pos += 1
                obj[key] = parse_object()
            else:
                obj[key] = nxt
                pos += 1
        return obj

    root: dict = {}
    while pos < len(tokens):
        key = tokens[pos]
        pos += 1
        if pos < len(tokens) and tokens[pos] == "{":
            pos += 1
            root[key] = parse_object()
        elif pos < len(tokens):
            root[key] = tokens[pos]
            pos += 1
    return root
