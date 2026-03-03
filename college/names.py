from __future__ import annotations

"""Name generation utilities for the College subsystem.

This module is intentionally small and dependency-free.

Goals:
  - Greatly reduce the chance of duplicate names (immersion killer).
  - Be deterministic given the caller-provided RNG and call order.
  - Work even before the JSON name bank is provided (dev fallback).

Integration points (planned):
  - college.generation.generate_player_profile(...) should call
    generate_unique_full_name(rng, used_name_keys)
  - college.service should build used_name_keys once per batch by reading
    existing NBA + College names from DB and passing it down.

Name bank files (you will supply the content):
  - NBA/data/names/first_names.json
  - NBA/data/names/last_names.json

File format (recommended): JSON list of strings.
  Example: ["Alex", "Jordan", "Taylor", ...]
"""

import json
import os
import string
from typing import Iterable, List, Optional, Sequence, Set, Tuple


# ----------------------------
# Name bank paths
# ----------------------------


def _get_data_dir() -> str:
    """Resolve the project's DATA_DIR.

    The repo uses a top-level `config.py` (not a package) that defines DATA_DIR.
    Importing it here keeps paths consistent with the rest of the project.
    """
    # NOTE: Absolute import by design. The runner puts the project root on sys.path.
    import config as app_config  # type: ignore

    return str(getattr(app_config, "DATA_DIR"))


NAMES_DIR = os.path.join(_get_data_dir(), "names")
FIRST_NAMES_PATH = os.path.join(NAMES_DIR, "first_names.json")
LAST_NAMES_PATH = os.path.join(NAMES_DIR, "last_names.json")


# ----------------------------
# Fallback name bank (dev only)
# ----------------------------


_FALLBACK_FIRST_NAMES: Sequence[str] = (
    "Alex", "Jordan", "Taylor", "Chris", "Devin", "Cameron", "Morgan", "Jaden", "Casey", "Riley",
    "Marcus", "Darius", "Ethan", "Noah", "Liam", "Aiden", "Kai", "Miles", "Zion", "Logan",
    "Trevor", "Isaiah", "Aaron", "Damon", "Bryce", "Julian", "Cole", "Grant", "Reed", "Cyrus",
)

_FALLBACK_LAST_NAMES: Sequence[str] = (
    "Walker", "Johnson", "Williams", "Brown", "Miller", "Davis", "Anderson", "Moore", "Taylor", "Thomas",
    "Jackson", "White", "Harris", "Martin", "Thompson", "Garcia", "Martinez", "Robinson", "Clark", "Lewis",
    "Young", "Allen", "King", "Wright", "Scott", "Green", "Baker", "Adams", "Nelson", "Carter",
)


# ----------------------------
# Lazy-loaded caches
# ----------------------------


_FIRST_CACHE: Optional[List[str]] = None
_LAST_CACHE: Optional[List[str]] = None


def _clean_token(s: str) -> str:
    """Normalize a single name token (safety cleanup)."""
    return " ".join(str(s).strip().split())


def _load_json_list(path: str) -> List[str]:
    """Load a JSON list[str] with basic validation/cleanup."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Name bank must be a JSON list: {path}")

    out: List[str] = []
    seen: Set[str] = set()
    for raw in data:
        if not isinstance(raw, str):
            continue
        tok = _clean_token(raw)
        if not tok:
            continue
        key = tok.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(tok)

    if not out:
        raise ValueError(f"Name bank is empty after cleanup: {path}")

    return out


def get_name_bank(*, strict: bool = False) -> Tuple[List[str], List[str]]:
    """Return (first_names, last_names).

    - If JSON files exist, they are loaded and cached.
    - If missing:
        * strict=False (default): use fallback and print a warning.
        * strict=True: raise RuntimeError.
    """
    global _FIRST_CACHE, _LAST_CACHE
    if _FIRST_CACHE is not None and _LAST_CACHE is not None:
        return _FIRST_CACHE, _LAST_CACHE

    has_first = os.path.exists(FIRST_NAMES_PATH)
    has_last = os.path.exists(LAST_NAMES_PATH)

    if has_first and has_last:
        first = _load_json_list(FIRST_NAMES_PATH)
        last = _load_json_list(LAST_NAMES_PATH)
        _FIRST_CACHE, _LAST_CACHE = first, last
        return first, last

    if strict:
        missing = []
        if not has_first:
            missing.append(FIRST_NAMES_PATH)
        if not has_last:
            missing.append(LAST_NAMES_PATH)
        raise RuntimeError(f"Missing name bank JSON file(s): {', '.join(missing)}")

    # Dev fallback (keeps the game runnable before JSON is supplied).
    if not os.path.isdir(NAMES_DIR):
        print(f"[WARN] names dir missing: {NAMES_DIR} (using fallback name bank)")
    else:
        print(
            "[WARN] name bank JSON missing; using fallback name bank. "
            f"Expected: {FIRST_NAMES_PATH} and {LAST_NAMES_PATH}"
        )

    _FIRST_CACHE = list(_FALLBACK_FIRST_NAMES)
    _LAST_CACHE = list(_FALLBACK_LAST_NAMES)
    return _FIRST_CACHE, _LAST_CACHE


# ----------------------------
# Uniqueness helpers
# ----------------------------


def build_used_name_keys(names: Iterable[str]) -> Set[str]:
    """Build a canonical set of used-name keys (casefolded full names)."""
    out: Set[str] = set()
    for n in names:
        if not n:
            continue
        s = _clean_token(n)
        if not s:
            continue
        out.add(s.casefold())
    return out


_SUFFIXES: Sequence[str] = ("Jr.", "II", "III", "IV")


def _reserve_if_unique(candidate: str, used_name_keys: Set[str]) -> bool:
    key = candidate.casefold()
    if key in used_name_keys:
        return False
    used_name_keys.add(key)
    return True


def generate_unique_full_name(rng, used_name_keys: Set[str], *, strict_bank: bool = False) -> str:
    """Generate a full name that is effectively unique.

    Parameters
    ----------
    rng:
        random.Random-like object. Must provide choice(), randint(), and random().
        The caller controls seeding for determinism.
    used_name_keys:
        Set of canonical keys (casefolded full names). This function WILL mutate
        the set by reserving the generated name's key.
    strict_bank:
        If True, require JSON name banks to exist; otherwise fallback is allowed.

    Strategy (in order):
      1) "First Last" (many retries)
      2) "First M. Last" (middle initial)
      3) "First Last Jr./II/III/IV"
      4) "First Last-OtherLast" (hyphenated last)
      5) Final fallback: numeric tail "First Last 4321" (should be extremely rare)
    """
    first_names, last_names = get_name_bank(strict=strict_bank)

    # Lightweight one-time warning if the bank is tiny.
    if len(first_names) < 50 or len(last_names) < 50:
        if getattr(generate_unique_full_name, "_warned_small_bank", False) is False:
            print(
                f"[WARN] name bank looks small (first={len(first_names)}, last={len(last_names)}). "
                "Consider providing larger JSON lists to reduce suffix usage."
            )
            setattr(generate_unique_full_name, "_warned_small_bank", True)

    # 1) Base form: First Last
    for _ in range(40):
        cand = f"{rng.choice(first_names)} {rng.choice(last_names)}"
        if _reserve_if_unique(cand, used_name_keys):
            return cand

    # 2) Middle initial: First M. Last
    letters = string.ascii_uppercase
    for _ in range(30):
        cand = f"{rng.choice(first_names)} {rng.choice(letters)}. {rng.choice(last_names)}"
        if _reserve_if_unique(cand, used_name_keys):
            return cand

    # 3) Suffix
    for _ in range(30):
        cand = f"{rng.choice(first_names)} {rng.choice(last_names)} {rng.choice(_SUFFIXES)}"
        if _reserve_if_unique(cand, used_name_keys):
            return cand

    # 4) Hyphenated last
    for _ in range(30):
        ln1 = rng.choice(last_names)
        ln2 = rng.choice(last_names)
        if ln2 == ln1:
            continue
        cand = f"{rng.choice(first_names)} {ln1}-{ln2}"
        if _reserve_if_unique(cand, used_name_keys):
            return cand

    # 5) Final fallback: numeric tail
    for _ in range(200):
        num = int(rng.randint(2, 9999))
        cand = f"{rng.choice(first_names)} {rng.choice(last_names)} {num}"
        if _reserve_if_unique(cand, used_name_keys):
            return cand

    raise RuntimeError("Failed to generate a unique player name after exhaustive retries.")
