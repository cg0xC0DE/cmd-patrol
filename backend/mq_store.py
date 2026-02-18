"""
Lightweight JSON-file-based message queue for cmd-patrol.

Message schema:
{
    "id": str (uuid),
    "source": str,        # e.g. "civitai-downloader"
    "type": str,           # e.g. "metadata_error", "download_needed"
    "title": str,
    "detail": str,
    "status": str,         # "new" | "ack" | "done"
    "created_at": str,     # ISO timestamp
    "acked_at": str|None,
    "done_at": str|None,
    "meta": dict           # arbitrary extra data
}

State machine:  new -> ack -> done
                new -> done  (skip ack if manually resolved immediately)
"""

import json
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

MQ_FILE = Path(__file__).parent / "mq.json"
_lock = threading.Lock()


def _load() -> list[dict]:
    if not MQ_FILE.exists():
        return []
    try:
        return json.loads(MQ_FILE.read_text(encoding="utf-8"))
    except:
        return []


def _save(messages: list[dict]):
    MQ_FILE.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")


def publish(source: str, type: str, title: str, detail: str = "", meta: dict = None) -> dict:
    msg = {
        "id": str(uuid.uuid4()),
        "source": source,
        "type": type,
        "title": title,
        "detail": detail,
        "status": "new",
        "created_at": datetime.now().isoformat(),
        "acked_at": None,
        "done_at": None,
        "meta": meta or {},
    }
    with _lock:
        messages = _load()
        messages.append(msg)
        _save(messages)
    return msg


def query(
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    with _lock:
        messages = _load()

    filtered = messages
    if status:
        statuses = status.split(",")
        filtered = [m for m in filtered if m["status"] in statuses]
    if source:
        filtered = [m for m in filtered if m["source"] == source]

    total = len(filtered)
    # newest first
    filtered = list(reversed(filtered))
    page = filtered[offset : offset + limit]
    return {"messages": page, "total": total, "offset": offset, "limit": limit}


def get(msg_id: str) -> Optional[dict]:
    with _lock:
        messages = _load()
    for m in messages:
        if m["id"] == msg_id:
            return m
    return None


def ack(msg_id: str) -> Optional[dict]:
    with _lock:
        messages = _load()
        for m in messages:
            if m["id"] == msg_id:
                if m["status"] == "new":
                    m["status"] = "ack"
                    m["acked_at"] = datetime.now().isoformat()
                _save(messages)
                return m
    return None


def done(msg_id: str) -> Optional[dict]:
    with _lock:
        messages = _load()
        for m in messages:
            if m["id"] == msg_id:
                if m["status"] in ("new", "ack"):
                    m["status"] = "done"
                    m["done_at"] = datetime.now().isoformat()
                    if not m["acked_at"]:
                        m["acked_at"] = m["done_at"]
                _save(messages)
                return m
    return None


def batch_done(before_id: str) -> int:
    """Mark all non-done messages created at or before the given message as done."""
    with _lock:
        messages = _load()
        target = None
        for m in messages:
            if m["id"] == before_id:
                target = m
                break
        if not target:
            return 0

        now = datetime.now().isoformat()
        target_time = target["created_at"]
        count = 0
        for m in messages:
            if m["status"] in ("new", "ack") and m["created_at"] <= target_time:
                m["status"] = "done"
                m["done_at"] = now
                if not m["acked_at"]:
                    m["acked_at"] = now
                count += 1
        _save(messages)
    return count


def batch_ack_new() -> int:
    """Mark all 'new' messages as 'ack'. Returns count. Used by scanner."""
    with _lock:
        messages = _load()
        now = datetime.now().isoformat()
        count = 0
        for m in messages:
            if m["status"] == "new":
                m["status"] = "ack"
                m["acked_at"] = now
                count += 1
        if count:
            _save(messages)
    return count


def stats() -> dict:
    with _lock:
        messages = _load()
    return {
        "total": len(messages),
        "new": sum(1 for m in messages if m["status"] == "new"),
        "ack": sum(1 for m in messages if m["status"] == "ack"),
        "done": sum(1 for m in messages if m["status"] == "done"),
    }
