"""Per-session test case staging store.

In-memory buffer that accumulates test cases across multiple tool calls within
the same conversation, so that Excel export can emit the full set even when the
LLM only passes the latest batch to a single tool invocation.

Deduplication: cases with the same `id` / `用例编号` overwrite earlier ones,
preserving insertion order of the first occurrence.
"""

from __future__ import annotations

import threading
from typing import Any

_DEFAULT_KEY = "__default__"

_lock = threading.Lock()
_buffers: dict[str, list[dict[str, Any]]] = {}
_uat_buffers: dict[str, list[dict[str, Any]]] = {}


def _case_id(case: dict[str, Any]) -> str:
    for key in ("id", "用例编号", "case_id"):
        val = case.get(key)
        if val:
            return str(val)
    return ""


def _biz_code(scenario: dict[str, Any]) -> str:
    for key in ("biz_code", "业务编号", "code"):
        val = scenario.get(key)
        if val:
            return str(val)
    return ""


def _normalize_key(session_key: str | None) -> str:
    return session_key.strip() if session_key and session_key.strip() else _DEFAULT_KEY


def add(cases: list[dict[str, Any]], session_key: str | None = None) -> int:
    """Append cases to the buffer, dedup by id. Returns total count after insert."""
    key = _normalize_key(session_key)
    with _lock:
        buf = _buffers.setdefault(key, [])
        id_to_idx = {_case_id(c): i for i, c in enumerate(buf) if _case_id(c)}
        for case in cases:
            cid = _case_id(case)
            if cid and cid in id_to_idx:
                buf[id_to_idx[cid]] = case
            else:
                buf.append(case)
                if cid:
                    id_to_idx[cid] = len(buf) - 1
        return len(buf)


def list_all(session_key: str | None = None) -> list[dict[str, Any]]:
    key = _normalize_key(session_key)
    with _lock:
        return list(_buffers.get(key, []))


def list_ids(session_key: str | None = None) -> list[str]:
    return [_case_id(c) or "(无编号)" for c in list_all(session_key)]


def count(session_key: str | None = None) -> int:
    key = _normalize_key(session_key)
    with _lock:
        return len(_buffers.get(key, []))


def clear(session_key: str | None = None) -> int:
    key = _normalize_key(session_key)
    with _lock:
        n = len(_buffers.get(key, []))
        _buffers[key] = []
        return n


def add_uat(scenarios: list[dict[str, Any]], session_key: str | None = None) -> int:
    """Append UAT business scenarios to the dedicated UAT buffer.

    Dedup by `biz_code` / `业务编号` (later writes overwrite earlier ones,
    preserving the first-occurrence insertion order).
    """
    key = _normalize_key(session_key)
    with _lock:
        buf = _uat_buffers.setdefault(key, [])
        code_to_idx = {_biz_code(s): i for i, s in enumerate(buf) if _biz_code(s)}
        for sc in scenarios:
            code = _biz_code(sc)
            if code and code in code_to_idx:
                buf[code_to_idx[code]] = sc
            else:
                buf.append(sc)
                if code:
                    code_to_idx[code] = len(buf) - 1
        return len(buf)


def list_all_uat(session_key: str | None = None) -> list[dict[str, Any]]:
    key = _normalize_key(session_key)
    with _lock:
        return list(_uat_buffers.get(key, []))


def list_uat_codes(session_key: str | None = None) -> list[str]:
    return [_biz_code(s) or "(无编号)" for s in list_all_uat(session_key)]


def count_uat(session_key: str | None = None) -> int:
    key = _normalize_key(session_key)
    with _lock:
        return len(_uat_buffers.get(key, []))


def clear_uat(session_key: str | None = None) -> int:
    key = _normalize_key(session_key)
    with _lock:
        n = len(_uat_buffers.get(key, []))
        _uat_buffers[key] = []
        return n
