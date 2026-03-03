from __future__ import annotations

"""Fail-fast grep to prevent OS clock usage.

This repository intentionally forbids using the host OS clock in game logic.

Run:
  python -m tools.check_no_os_time

Exit code:
  0 - clean
  1 - forbidden pattern found
"""

import os
import re
import sys
from pathlib import Path


FORBIDDEN_PATTERNS = [
    # datetime/date
    r"\bdate\.today\s*\(",
    r"\bdatetime\.now\s*\(",
    r"\bdatetime\.utcnow\s*\(",
    r"\b_dt\.datetime\.now\s*\(",
    r"\b_dt\.datetime\.utcnow\s*\(",
    # time
    r"\btime\.time\s*\(",
    r"\btime\.monotonic\s*\(",
]

EXCLUDE_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}

EXCLUDE_FILES = {
    # Centralized module is allowed to *mention* these strings in docstrings.
    "game_time.py",
    # This checker itself.
    "check_no_os_time.py",
}


def iter_py_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dn = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            if fn in EXCLUDE_FILES:
                continue
            yield dn / fn


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    compiled = [re.compile(p) for p in FORBIDDEN_PATTERNS]

    hits = []
    for fp in iter_py_files(root):
        try:
            text = fp.read_text(encoding='utf-8')
        except Exception:
            # If file can't be read, skip (should not happen in normal repo)
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            for rx in compiled:
                if rx.search(line):
                    hits.append((fp.relative_to(root), i, line.strip(), rx.pattern))

    if not hits:
        print('[OK] No forbidden OS clock usage found.')
        return 0

    print('[FAIL] Forbidden OS clock usage found:\n')
    for rel, ln, line, pat in hits:
        print(f'- {rel}:{ln}: {line}')
        print(f'  matched: {pat}')
    print('\nFix: route through game_time.py (in-game date SSOT).')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
