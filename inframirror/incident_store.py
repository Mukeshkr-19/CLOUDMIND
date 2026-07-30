# incident_store.py – Atomic incident persistence for AIOps decision trail
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from typing import Any, Dict, List

try:
    from .aiops_models import IncidentRecord
except ImportError:
    from aiops_models import IncidentRecord


SHARED_DATA_DIR = os.getenv("SHARED_DATA_DIR", "/app/shared")
INCIDENTS_PATH = os.path.join(SHARED_DATA_DIR, "aiops_incidents.json")
LOCK_PATH = os.path.join(SHARED_DATA_DIR, ".aiops_incidents.lock")
MAX_RECORDS = 100

_SECRET_KEYS = {
    "api_key",
    "gemini_api_key",
    "discord_webhook_url",
    "whisper_token",
    "authorization",
    "password",
    "secret",
    "token",
    "key",
    "webhook_url",
}


def _sanitize_for_storage(data: Any) -> Any:
    """Recursively sanitize secrets and oversize/untrusted fields."""
    if isinstance(data, dict):
        sanitized: Dict[str, Any] = {}
        for key, value in data.items():
            if not isinstance(key, str):
                continue
            key_lower = key.lower()
            is_secret_key = (
                key_lower in _SECRET_KEYS
                or "api_key" in key_lower
                or "token" in key_lower
                or "secret" in key_lower
                or "password" in key_lower
                or "webhook" in key_lower
                or "authorization" in key_lower
                or key_lower == "raw_model_output"
            )
            if is_secret_key:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_for_storage(value)
        return sanitized
    if isinstance(data, list):
        return [_sanitize_for_storage(item) for item in data]
    if isinstance(data, str):
        if len(data) > 4096:
            return data[:4096] + "...[truncated]"
        return data
    return data


def _load_existing_records(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _write_records_atomically(records: List[Dict[str, Any]], path: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="aiops_incidents_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def persist_incident(record: IncidentRecord, path: str = INCIDENTS_PATH) -> None:
    record_dict = _sanitize_for_storage(record.to_dict())
    directory = os.path.dirname(path) or SHARED_DATA_DIR
    lock_path = os.path.join(directory, ".aiops_incidents.lock")
    os.makedirs(directory, exist_ok=True)
    lock_file = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        records = _load_existing_records(path)
        records.insert(0, record_dict)
        records = records[:MAX_RECORDS]
        _write_records_atomically(records, path)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def load_incidents(path: str = INCIDENTS_PATH) -> List[Dict[str, Any]]:
    return _load_existing_records(path)
