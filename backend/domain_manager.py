import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOMAINS_FILE = Path(__file__).parent / "domains.json"


@dataclass
class ApplyResult:
    ok: bool
    message: str


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _migrate_candidates(candidates: list) -> list[dict[str, str]]:
    result = []
    for c in candidates:
        if isinstance(c, str):
            result.append({"url": c.strip(), "ngrok_cmd": ""})
        elif isinstance(c, dict):
            result.append({"url": str(c.get("url", "")).strip(), "ngrok_cmd": str(c.get("ngrok_cmd", "")).strip()})
    return [r for r in result if r["url"]]


def load_domains() -> dict[str, Any]:
    if not DOMAINS_FILE.exists():
        return {"active": "", "candidates": [], "targets": {}}
    data = _read_json(DOMAINS_FILE)
    data["candidates"] = _migrate_candidates(data.get("candidates", []))
    return data


def save_domains(data: dict[str, Any]) -> None:
    DOMAINS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _normalize_domain(domain: str) -> str:
    domain = (domain or "").strip()
    if not domain:
        return ""
    return domain.rstrip("/")


def _apply_js_config(target_file: Path, suffix: str, domain: str) -> ApplyResult:
    if not target_file.exists():
        return ApplyResult(False, f"Target file not found: {target_file}")

    text = target_file.read_text(encoding="utf-8")

    new_api_base = _normalize_domain(domain) + (suffix or "")

    pattern = r"apiBase\s*:\s*\"[^\"]*\""
    if not re.search(pattern, text):
        return ApplyResult(False, f"apiBase not found in: {target_file}")

    updated = re.sub(pattern, f'apiBase: "{new_api_base}"', text, count=1)
    target_file.write_text(updated, encoding="utf-8")
    return ApplyResult(True, f"Updated {target_file}")


def _apply_json(target_file: Path, json_path: str, suffix: str, domain: str) -> ApplyResult:
    data: Any = {}
    if target_file.exists():
        data = _read_json(target_file)

    new_value = _normalize_domain(domain) + (suffix or "")

    if not json_path:
        return ApplyResult(False, "jsonPath missing")

    keys = json_path.split(".")
    cur = data
    for k in keys[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]

    cur[keys[-1]] = new_value

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return ApplyResult(True, f"Updated {target_file}")


def _find_git_root(file_path: Path) -> Path | None:
    cur = file_path.parent if file_path.is_file() else file_path
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    return None


def _git_commit_push(file_path: Path, commit_msg: str) -> str:
    git_root = _find_git_root(file_path)
    if not git_root:
        return "git root not found"
    try:
        rel = file_path.relative_to(git_root).as_posix()
        subprocess.run(["git", "add", rel], cwd=str(git_root), capture_output=True, timeout=10)
        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(git_root), capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            out = (result.stdout + result.stderr).strip()
            if "nothing to commit" in out:
                return "no changes to commit"
            return f"commit failed: {out}"
        push = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(git_root), capture_output=True, text=True, timeout=30
        )
        if push.returncode != 0:
            return f"committed but push failed: {(push.stdout + push.stderr).strip()}"
        return "committed & pushed"
    except subprocess.TimeoutExpired:
        return "git operation timed out"
    except Exception as e:
        return f"git error: {e}"


def apply_active_domain(active_domain: str) -> list[dict[str, Any]]:
    cfg = load_domains()
    targets = cfg.get("targets", {})

    results: list[dict[str, Any]] = []

    for name, t in targets.items():
        t_type = t.get("type")
        file_str = t.get("file")
        suffix = t.get("suffix", "")

        if not file_str:
            results.append({"target": name, "ok": False, "message": "file missing"})
            continue

        path = Path(file_str)
        if t_type == "js_config":
            r = _apply_js_config(path, suffix=suffix, domain=active_domain)
        elif t_type == "json":
            r = _apply_json(path, json_path=t.get("jsonPath", ""), suffix=suffix, domain=active_domain)
        else:
            r = ApplyResult(False, f"Unknown target type: {t_type}")

        git_msg = ""
        if r.ok and t.get("auto_commit"):
            commit_msg = t.get("commit_msg") or "change domain"
            git_msg = _git_commit_push(path, commit_msg)

        results.append({
            "target": name,
            "ok": r.ok,
            "message": r.message,
            "git": git_msg
        })

    return results
