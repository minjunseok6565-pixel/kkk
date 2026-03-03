from __future__ import annotations

import json
import os
import re
import shutil
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional
from uuid import uuid4

import state
from config import SAVE_ROOT_DIR
from config_roster import DEFAULT_ROSTER_PATH
from league_repo import LeagueRepo
from team_utils import ui_cache_rebuild_all

_SAVE_LOCK = RLock()
_SLOT_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{2,63}$")
_SAVE_FORMAT_VERSION = 1
_DB_HASH_BLOCK_SIZE = 1024 * 1024
_RUNTIME_DB_NAME = "_active_runtime.sqlite3"
_DB_SSOT_KEYS = {
    "draft_picks",
    "swap_rights",
    "fixed_assets",
    "transactions",
    "contracts",
    "player_contracts",
    "active_contract_id_by_player",
    "free_agents",
    "gm_profiles",
}


class SaveError(ValueError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_save_root() -> Path:
    root = Path(SAVE_ROOT_DIR)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _validate_slot_id(slot_id: str) -> str:
    sid = str(slot_id or "").strip()
    if not sid:
        raise SaveError("slot_id is required")
    if not _SLOT_RE.fullmatch(sid):
        raise SaveError("slot_id must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{2,63}$")
    return sid


def _new_slot_id() -> str:
    return f"slot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def _slot_dir(slot_id: str) -> Path:
    return _ensure_save_root() / _validate_slot_id(slot_id)


def _runtime_db_path() -> Path:
    """Runtime DB used after load.

    Save slot DBs are immutable snapshots and should not be used as live DB paths.
    """
    return _ensure_save_root() / _RUNTIME_DB_NAME


def _tmp_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".tmp")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _atomic_copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        raise SaveError(f"source file not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(dst)
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def _prepare_runtime_db_from_slot(slot_db_path: Path) -> Path:
    runtime_db = _runtime_db_path()
    _atomic_copy_file(slot_db_path, runtime_db)
    return runtime_db


def _coerce_snapshot_season(snapshot: Dict[str, Any]) -> None:
    """Align runtime active season/schedule with snapshot before merging payload.

    export_save_state_snapshot excludes league.master_schedule by design, so if snapshot
    carries a different active_season_id than the just-bootstrapped runtime state, we must
    explicitly switch seasons and rebuild schedule prior to deep-merge import.
    """
    if not isinstance(snapshot, dict):
        return

    target_active = snapshot.get("active_season_id")
    if not target_active:
        return

    cur_active = state.get_active_season_id()
    if str(cur_active) != str(target_active):
        state.set_active_season_id(str(target_active))
        state.ensure_schedule_for_active_season(force=True)


def _meta_from_state(*, slot_id: str, slot_name: str, save_name: Optional[str], note: Optional[str], save_version: int) -> Dict[str, Any]:
    snap = state.export_full_state_snapshot()
    league = snap.get("league") if isinstance(snap, dict) else {}
    if not isinstance(league, dict):
        league = {}
    return {
        "slot_id": slot_id,
        "slot_name": slot_name,
        "save_name": save_name,
        "note": note,
        "save_version": int(save_version),
        "saved_at": _utc_now_iso(),
        "save_format_version": _SAVE_FORMAT_VERSION,
        "active_season_id": snap.get("active_season_id"),
        "turn": snap.get("turn"),
        "season_year": league.get("season_year"),
        "current_date": league.get("current_date"),
    }


def _read_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        with meta_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(_DB_HASH_BLOCK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _sha256_json(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_slot_files(slot_dir: Path, *, strict: bool = True) -> Dict[str, Any]:
    issues = []
    meta_path = slot_dir / "meta.json"
    db_path = slot_dir / "league.sqlite3"
    state_path = slot_dir / "state.partial.json"

    if not meta_path.exists():
        raise SaveError("slot meta.json is missing")
    if not db_path.exists():
        raise SaveError("slot league.sqlite3 is missing")
    if not state_path.exists():
        issues.append("state.partial.json is missing; runtime workflow state restore will be skipped")

    meta = _read_meta(meta_path)
    if not meta:
        raise SaveError("slot meta.json is invalid")

    version = int(meta.get("save_format_version") or 0)
    if version > _SAVE_FORMAT_VERSION:
        raise SaveError(f"unsupported save_format_version: {version}")

    hashes = meta.get("content_hashes") if isinstance(meta.get("content_hashes"), dict) else {}
    if hashes:
        expected_db = str(hashes.get("league.sqlite3") or "").strip()
        expected_state = str(hashes.get("state.partial.json") or "").strip()
        if expected_db:
            actual_db = _sha256_file(db_path)
            if actual_db != expected_db:
                msg = "league.sqlite3 checksum mismatch"
                if strict:
                    raise SaveError(msg)
                issues.append(msg)

        if expected_state and state_path.exists():
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    state_payload = json.load(f)
                if not isinstance(state_payload, dict):
                    raise SaveError("state.partial.json is invalid")
                actual_state = _sha256_json(state_payload)
                if actual_state != expected_state:
                    msg = "state.partial.json checksum mismatch"
                    if strict:
                        raise SaveError(msg)
                    issues.append(msg)
            except SaveError:
                raise
            except Exception as exc:
                raise SaveError(f"failed to parse state.partial.json: {exc}") from exc

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "meta": meta,
        "db_path": str(db_path),
        "state_path": str(state_path),
    }


def save_game(*, slot_id: str, save_name: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    with _SAVE_LOCK:
        sid = _validate_slot_id(slot_id)
        db_path = Path(state.get_db_path())

        slot_dir = _slot_dir(sid)
        slot_dir.mkdir(parents=True, exist_ok=True)

        meta_path = slot_dir / "meta.json"
        prev = _read_meta(meta_path)
        slot_name = str(prev.get("slot_name") or sid)
        save_version = int(prev.get("save_version") or 0) + 1

        _atomic_copy_file(db_path, slot_dir / "league.sqlite3")
        save_state = state.export_save_state_snapshot()
        _atomic_write_json(slot_dir / "state.partial.json", save_state)
        db_hash = _sha256_file(slot_dir / "league.sqlite3")
        state_hash = _sha256_json(save_state)

        meta = _meta_from_state(
            slot_id=sid,
            slot_name=slot_name,
            save_name=save_name,
            note=note,
            save_version=save_version,
        )
        meta["content_hashes"] = {
            "league.sqlite3": db_hash,
            "state.partial.json": state_hash,
        }
        _atomic_write_json(meta_path, meta)

        return {
            "ok": True,
            "slot_id": sid,
            "save_version": save_version,
            "saved_at": meta["saved_at"],
            "active_season_id": meta.get("active_season_id"),
            "current_date": meta.get("current_date"),
            "season_year": meta.get("season_year"),
            "content_hashes": meta.get("content_hashes"),
        }


def list_save_slots() -> Dict[str, Any]:
    with _SAVE_LOCK:
        root = _ensure_save_root()
        slots = []
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            sid = entry.name
            if not _SLOT_RE.fullmatch(sid):
                continue
            meta = _read_meta(entry / "meta.json")
            if not meta:
                continue
            slots.append(
                {
                    "slot_id": sid,
                    "slot_name": meta.get("slot_name") or sid,
                    "save_version": int(meta.get("save_version") or 0),
                    "saved_at": meta.get("saved_at"),
                    "save_name": meta.get("save_name"),
                    "note": meta.get("note"),
                    "season_year": meta.get("season_year"),
                    "current_date": meta.get("current_date"),
                    "user_team_id": meta.get("user_team_id"),
                    "save_format_version": int(meta.get("save_format_version") or 0),
                }
            )

        slots.sort(key=lambda x: str(x.get("saved_at") or ""), reverse=True)
        return {"ok": True, "slots": slots}


def get_save_slot_detail(*, slot_id: str, strict: bool = False) -> Dict[str, Any]:
    with _SAVE_LOCK:
        sid = _validate_slot_id(slot_id)
        slot_dir = _slot_dir(sid)
        if not slot_dir.exists():
            raise SaveError(f"slot not found: {sid}")
        verify = _verify_slot_files(slot_dir, strict=strict)
        meta = verify["meta"]
        return {
            "ok": True,
            "slot_id": sid,
            "slot_name": meta.get("slot_name") or sid,
            "meta": meta,
            "integrity": {
                "ok": verify["ok"],
                "issues": verify["issues"],
            },
        }


def load_game(*, slot_id: str, strict: bool = True, expected_save_version: Optional[int] = None) -> Dict[str, Any]:
    with _SAVE_LOCK:
        sid = _validate_slot_id(slot_id)
        slot_dir = _slot_dir(sid)
        if not slot_dir.exists():
            raise SaveError(f"slot not found: {sid}")

        verify = _verify_slot_files(slot_dir, strict=strict)
        meta = verify["meta"]
        save_version = int(meta.get("save_version") or 0)
        if expected_save_version is not None and int(expected_save_version) != save_version:
            raise SaveError(
                f"save_version mismatch: expected={int(expected_save_version)} actual={save_version}"
            )

        slot_db_path = slot_dir / "league.sqlite3"
        state_path = slot_dir / "state.partial.json"

        # IMPORTANT: Never attach save-slot DB as live runtime DB.
        # The slot must remain immutable for reproducible restores/integrity checks.
        runtime_db_path = _prepare_runtime_db_from_slot(slot_db_path)

        state.reset_state_for_dev()
        state.set_db_path(str(runtime_db_path))
        state.startup_init_state()

        imported_partial = False
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                raise SaveError("state.partial.json is invalid")
            filtered = {k: v for k, v in payload.items() if k not in _DB_SSOT_KEYS}
            _coerce_snapshot_season(filtered)
            state.import_save_state_snapshot(filtered)
            imported_partial = True

        try:
            ui_cache_rebuild_all()
        except Exception:
            pass

        return {
            "ok": True,
            "slot_id": sid,
            "slot_name": meta.get("slot_name") or sid,
            "save_version": save_version,
            "saved_at": meta.get("saved_at"),
            "season_year": meta.get("season_year"),
            "current_date": meta.get("current_date"),
            "user_team_id": meta.get("user_team_id"),
            "strict": bool(strict),
            "integrity_issues": verify.get("issues") or [],
            "imported_partial_state": imported_partial,
            "db_path": str(runtime_db_path),
            "slot_db_path": str(slot_db_path),
        }


def create_new_game(
    *,
    slot_name: str,
    slot_id: Optional[str] = None,
    season_year: Optional[int] = None,
    user_team_id: Optional[str] = None,
    overwrite_if_exists: bool = False,
) -> Dict[str, Any]:
    with _SAVE_LOCK:
        name = str(slot_name or "").strip()
        if not name:
            raise SaveError("slot_name is required")

        sid = _validate_slot_id(slot_id) if slot_id else _new_slot_id()
        slot_dir = _slot_dir(sid)
        if slot_dir.exists() and any(slot_dir.iterdir()) and not overwrite_if_exists:
            raise SaveError(f"slot already exists: {sid}")

        slot_dir.mkdir(parents=True, exist_ok=True)
        new_db_path = slot_dir / "league.sqlite3"

        if new_db_path.exists() and overwrite_if_exists:
            new_db_path.unlink()

        state.reset_state_for_dev()
        state.set_db_path(str(new_db_path))
        state.startup_init_state()

        # New game bootstrap: import the roster Excel into the new DB snapshot.
        # This runs synchronously so API callers can expose an actual loading phase.
        with LeagueRepo(str(new_db_path)) as repo:
            repo.init_db()
            repo.import_roster_excel(DEFAULT_ROSTER_PATH, mode="replace", strict_ids=False)

        if season_year is not None:
            state.start_new_season(int(season_year), rebuild_schedule=True)

        try:
            ui_cache_rebuild_all()
        except Exception:
            pass

        save_payload = save_game(slot_id=sid, save_name="new_game_init", note="initial save after new game")

        meta_path = slot_dir / "meta.json"
        meta = _read_meta(meta_path)
        meta["slot_name"] = name
        meta["user_team_id"] = user_team_id
        _atomic_write_json(meta_path, meta)

        out = dict(save_payload)
        out.update(
            {
                "slot_id": sid,
                "slot_name": name,
                "db_path": str(new_db_path),
                "user_team_id": user_team_id,
                "created_at": meta.get("saved_at"),
            }
        )
        return out


def set_save_user_team(*, slot_id: str, user_team_id: str) -> Dict[str, Any]:
    with _SAVE_LOCK:
        sid = _validate_slot_id(slot_id)
        slot_dir = _slot_dir(sid)
        if not slot_dir.exists():
            raise SaveError(f"slot not found: {sid}")

        normalized_team_id = str(user_team_id or "").strip().upper()
        if not normalized_team_id:
            raise SaveError("user_team_id is required")

        meta_path = slot_dir / "meta.json"
        meta = _read_meta(meta_path)
        if not meta:
            raise SaveError(f"meta not found: {sid}")

        meta["user_team_id"] = normalized_team_id
        _atomic_write_json(meta_path, meta)

        return {
            "ok": True,
            "slot_id": sid,
            "user_team_id": normalized_team_id,
            "slot_name": meta.get("slot_name") or sid,
            "save_version": int(meta.get("save_version") or 0),
        }
