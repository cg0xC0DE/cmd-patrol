"""
MQ Scanner - meant to be run periodically by openclaw heartbeat.

Checks for new (unacknowledged) messages in the MQ.
If any exist, prints a summary and exits with code 0.
If none, exits with code 0 but prints nothing (no events).

Usage:
    python mq_scanner.py [--url http://127.0.0.1:5050]

Output (JSON to stdout):
    {
        "has_events": true/false,
        "new_count": N,
        "summary": "human-readable summary",
        "messages": [...]
    }
"""

import argparse
import json
import sys
import urllib.request


def main():
    parser = argparse.ArgumentParser(description="cmd-patrol MQ scanner")
    parser.add_argument("--url", default="http://127.0.0.1:51314", help="cmd-patrol base URL")
    parser.add_argument("--ack", action="store_true", help="Mark scanned messages as ack after reporting")
    args = parser.parse_args()

    base = args.url.rstrip("/")

    # Fetch new messages
    try:
        req = urllib.request.Request(f"{base}/api/mq/messages?status=new&limit=50")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(json.dumps({"error": str(e), "has_events": False}))
        sys.exit(1)

    messages = data.get("messages", [])
    total_new = data.get("total", 0)

    if not messages:
        print(json.dumps({"has_events": False, "new_count": 0, "summary": "", "messages": []}))
        return

    # Build summary
    lines = [f"[cmd-patrol] {total_new} 条新事件:"]
    for m in messages[:20]:
        src = m.get("source", "?")
        title = m.get("title", "")
        detail = m.get("detail", "")
        line = f"  [{src}] {title}"
        if detail:
            line += f" - {detail[:100]}"
        lines.append(line)
    if total_new > 20:
        lines.append(f"  ... 及另外 {total_new - 20} 条")

    summary = "\n".join(lines)

    result = {
        "has_events": True,
        "new_count": total_new,
        "summary": summary,
        "messages": messages[:20],
    }
    print(json.dumps(result, ensure_ascii=False))

    # Optionally mark as ack
    if args.ack:
        try:
            req = urllib.request.Request(
                f"{base}/api/mq/batch-ack",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        except:
            pass


if __name__ == "__main__":
    main()
