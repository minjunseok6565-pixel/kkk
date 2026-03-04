#!/usr/bin/env python3
"""Verify whether split CSS can safely replace static/NBA.css."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
NBA_CSS = ROOT / "static" / "NBA.css"
INDEX_CSS = ROOT / "static" / "css" / "index.css"


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _split_top_level_rules(css_text: str) -> list[str]:
    css_text = _strip_comments(css_text)
    rules: list[str] = []
    i = 0
    n = len(css_text)

    while i < n:
        while i < n and css_text[i].isspace():
            i += 1
        if i >= n:
            break

        start = i
        depth = 0
        in_string: str | None = None

        while i < n:
            ch = css_text[i]

            if in_string is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_string:
                    in_string = None
                i += 1
                continue

            if ch in ('"', "'"):
                in_string = ch
                i += 1
                continue

            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    rules.append(css_text[start:i].strip())
                    break
            elif ch == ";" and depth == 0:
                i += 1
                rules.append(css_text[start:i].strip())
                break

            i += 1
        else:
            trailing = css_text[start:].strip()
            if trailing:
                rules.append(trailing)
            break

    return rules


def _normalize(rule: str) -> str:
    return re.sub(r"\s+", " ", rule).strip()


def _parse_imports(index_text: str) -> list[Path]:
    imports = re.findall(r'@import\s+"([^"]+)"\s*;', index_text)
    return [INDEX_CSS.parent / rel for rel in imports]


def _header_and_properties(rule: str) -> tuple[str | None, set[str] | None]:
    if "{" not in rule:
        return None, None

    header, block = rule.split("{", 1)
    header = header.strip()

    if header.startswith("@"):
        return header, None

    body = block.rsplit("}", 1)[0]
    properties: set[str] = set()
    for decl in body.split(";"):
        if ":" not in decl:
            continue
        prop = decl.split(":", 1)[0].strip()
        if prop:
            properties.add(prop)
    return header, properties


def main() -> int:
    nba_css = NBA_CSS.read_text(encoding="utf-8")
    index_css = INDEX_CSS.read_text(encoding="utf-8")

    import_files = _parse_imports(index_css)
    concatenated = "\n\n".join(path.read_text(encoding="utf-8") for path in import_files)

    print(f"NBA.css bytes: {len(nba_css.encode('utf-8'))}")
    print(f"Split concat bytes: {len(concatenated.encode('utf-8'))}")

    exact_match = nba_css == concatenated
    print(f"Exact text match: {'YES' if exact_match else 'NO'}")

    nba_rules = [_normalize(r) for r in _split_top_level_rules(nba_css)]
    split_rules = [_normalize(r) for r in _split_top_level_rules(concatenated)]

    nba_counter = Counter(nba_rules)
    split_counter = Counter(split_rules)

    missing = list((nba_counter - split_counter).elements())
    extra = list((split_counter - nba_counter).elements())

    print(f"Top-level rules in NBA.css: {len(nba_rules)}")
    print(f"Top-level rules in split concat: {len(split_rules)}")
    print(f"Missing normalized top-level rules: {len(missing)}")
    print(f"Extra normalized top-level rules: {len(extra)}")

    order_sensitive_drift: list[tuple[str, list[str], list[str]]] = []
    props_nba: dict[str, list[set[str] | None]] = defaultdict(list)
    seq_nba: dict[str, list[str]] = defaultdict(list)
    seq_split: dict[str, list[str]] = defaultdict(list)

    nba_raw_rules = _split_top_level_rules(nba_css)
    split_raw_rules = _split_top_level_rules(concatenated)

    for rule in nba_raw_rules:
        header, props = _header_and_properties(rule)
        if header is None:
            continue
        props_nba[header].append(props)
        seq_nba[header].append(_normalize(rule))

    for rule in split_raw_rules:
        header, _ = _header_and_properties(rule)
        if header is None:
            continue
        seq_split[header].append(_normalize(rule))

    for header, sequence in seq_nba.items():
        if header.startswith("@") or len(sequence) < 2:
            continue

        prop_list = props_nba[header]
        overlap = False
        for i in range(len(prop_list)):
            for j in range(i + 1, len(prop_list)):
                left = prop_list[i]
                right = prop_list[j]
                if left is None or right is None:
                    continue
                if left & right:
                    overlap = True

        if overlap and sequence != seq_split.get(header, []):
            order_sensitive_drift.append((header, sequence, seq_split.get(header, [])))

    print(f"Potential order-sensitive selector sequence drift: {len(order_sensitive_drift)}")
    for header, before, after in order_sensitive_drift:
        print(f"- {header}")
        print(f"  NBA.css occurrence count: {len(before)}")
        print(f"  split occurrence count:   {len(after)}")

    if not exact_match:
        print("Exact text differs (informational): OK if rule coverage and selector sequence checks pass.")

    if not missing and not extra and not order_sensitive_drift:
        print("\nResult: SAFE_TO_DELETE_NBA_CSS")
        return 0

    print("\nResult: NOT_SAFE_TO_DELETE_NBA_CSS_YET")
    return 1


if __name__ == "__main__":
    sys.exit(main())
