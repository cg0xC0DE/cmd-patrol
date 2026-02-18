# patrol_mq.py — Publish events to cmd-patrol MQ
# Copy this file into any project that needs to push events.
# Zero external dependencies (stdlib only).
#
# Usage:
#   from patrol_mq import publish_event
#   publish_event("my-project", "Something needs attention", type="error", detail="...")
#
# Endpoint discovery:
#   Set CMD_PATROL_URL env var, or defaults to http://127.0.0.1:51314

import json
import os
import urllib.request

_PATROL_URL = os.environ.get("CMD_PATROL_URL", "http://127.0.0.1:51314")


def publish_event(source: str, title: str, type: str = "", detail: str = "", meta: dict = None):
    """
    Publish an event to cmd-patrol MQ. Fire-and-forget: never raises.

    Args:
        source: Your project name, e.g. "civitai-downloader"
        title:  Short summary of what happened
        type:   Category string, e.g. "metadata_error"
        detail: Optional longer description
        meta:   Optional dict with extra structured data
    """
    payload = json.dumps({
        "source": source,
        "type": type,
        "title": title,
        "detail": detail,
        "meta": meta or {},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{_PATROL_URL}/api/mq/publish",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # MQ is best-effort; never crash the main process
