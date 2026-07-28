"""Open Code Review (OCR) — AI-powered code review via `ocr` CLI.

Wraps alibaba/open-code-review v1.8+ with two modes:

  DELEGATION (always available, no LLM needed on OCR side):
    preview — which files to review + mode/ref metadata
    rule    — matched review rules grouped by content

  DIRECT (requires OCR LLM config; gated via ocr llm test):
    review       — diff-based review
    scan         — full-file scan (no diff needed)
    session_list — list saved review sessions
    session_view — inspect a session

Utility: llm_test, version.

Output: compact 1-2 char keys. Large results saved to disk.
Gated via check_fn on `ocr` binary presence.
"""

import json
import os
import subprocess
import shutil
from pathlib import Path

from tools.registry import registry

# ── Config ────────────────────────────────────────────────────────────────────

MAX_INLINE_CHARS = 9000
OCR_BIN = shutil.which("ocr") or "ocr"
TIMEOUT_REVIEW = 900   # 15 min for review/scan
TIMEOUT_DEFAULT = 60


def _ocr_available() -> bool:
    return shutil.which("ocr") is not None


def _ocr_llm_configured() -> bool:
    """Check if OCR has a working LLM endpoint (for review/scan)."""
    try:
        r = subprocess.run(
            [OCR_BIN, "llm", "test"],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def _out_dir() -> Path:
    from hermes_constants import get_hermes_home
    d = get_hermes_home() / "ocr_output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _trunc(s: str, n: int) -> str:
    if not isinstance(s, str):
        return s
    if len(s) <= n:
        return s
    return s[:n] + f"...[{len(s) - n} more]"


# ── CLI runner ────────────────────────────────────────────────────────────────

def _run(args: list, timeout: int = TIMEOUT_DEFAULT, cwd: str = None) -> dict:
    """Run ocr CLI and return compact result dict."""
    try:
        r = subprocess.run(
            [OCR_BIN] + args,
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return {"e": f"timeout ({timeout}s)", "h": "Increase timeout for large repos."}
    except FileNotFoundError:
        return {"e": "ocr not found", "h": "Install: npm i -g @alibaba-group/open-code-review"}
    except Exception as e:
        return {"e": str(e)[:200]}

    stdout = (r.stdout or "").strip()
    stderr = (r.stderr or "").strip()

    result = {"ok": r.returncode == 0, "code": r.returncode}

    if stdout:
        if len(stdout) > MAX_INLINE_CHARS:
            saved = _save_output(stdout, "_".join(args[:2]) if args else "ocr")
            result["@"] = saved
            result["out"] = stdout[:2000] + f"... [{len(stdout)} chars, full at @]"
        else:
            result["out"] = stdout
    if stderr:
        result["err"] = _trunc(stderr, 2000)

    return result


def _save_output(text: str, tag: str) -> str:
    import time
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:50]
    path = _out_dir() / f"{safe}_{int(time.time())}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ── Action handlers ───────────────────────────────────────────────────────────

def _preview(from_ref: str, to_ref: str, commit: str, repo: str,
             exclude: str, background: str, background_file: str) -> dict:
    """ocr delegate preview — deterministic file selection."""
    args = ["delegate", "preview"]
    if from_ref:
        args += ["--from", from_ref]
    if to_ref:
        args += ["--to", to_ref]
    if commit:
        args += ["-c", commit]
    if repo:
        args += ["--repo", repo]
    if exclude:
        args += ["--exclude", exclude]
    if background:
        args += ["-b", background]
    if background_file:
        args += ["-B", background_file]

    result = _run(args, cwd=repo if repo else None)
    if result.get("ok"):
        result["_action"] = "preview"
    return result


def _rule(files: list, repo: str, rule_file: str) -> dict:
    """ocr delegate rule — matched review rules grouped by content."""
    if not files:
        return {"e": "files required (list of repo-relative paths)"}

    args = ["delegate", "rule"] + list(files)
    if repo:
        args += ["--repo", repo]
    if rule_file:
        args += ["--rule", rule_file]

    result = _run(args, cwd=repo if repo else None)
    if result.get("ok"):
        result["_action"] = "rule"
        result["fs"] = files
    return result


def _review(from_ref: str, to_ref: str, commit: str, repo: str,
            background: str, background_file: str, preview: bool,
            concurrency: int, timeout_mins: int, rule_file: str) -> dict:
    """ocr review — diff-based review (needs LLM for non-preview)."""
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use preview+rule delegation mode, or run: ocr config provider"}

    args = ["review", "--audience", "agent", "--format", "json"]
    if from_ref:
        args += ["--from", from_ref]
    if to_ref:
        args += ["--to", to_ref]
    if commit:
        args += ["-c", commit]
    if repo:
        args += ["--repo", repo]
    if background:
        args += ["-b", background]
    if background_file:
        args += ["-B", background_file]
    if preview:
        args += ["--preview"]
    if concurrency and concurrency > 0:
        args += ["--concurrency", str(concurrency)]
    if timeout_mins and timeout_mins > 0:
        args += ["--timeout", str(timeout_mins)]
    if rule_file:
        args += ["--rule", rule_file]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=repo if repo else None)
    if result.get("ok") and not preview:
        # Parse structured JSON from ocr review output
        try:
            parsed = json.loads(result["out"])
            result["data"] = _compact_review(parsed)
            # Save full output
            saved = _save_output(json.dumps(parsed, indent=2), "review")
            result["@"] = saved
            # Remove raw stdout to save tokens
            del result["out"]
        except (json.JSONDecodeError, KeyError):
            pass
    return result


def _scan(path: str, repo: str, preview: bool, background: str,
          exclude: str, no_plan: bool, concurrency: int, timeout_mins: int) -> dict:
    """ocr scan — full-file scan (needs LLM for non-preview)."""
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use delegation mode, or run: ocr config provider"}

    args = ["scan", "--audience", "agent", "--format", "json"]
    if path:
        args += ["--path", path]
    if repo:
        args += ["--repo", repo]
    if background:
        args += ["-b", background]
    if exclude:
        args += ["--exclude", exclude]
    if preview:
        args += ["--preview"]
    if no_plan:
        args += ["--no-plan"]
    if concurrency and concurrency > 0:
        args += ["--concurrency", str(concurrency)]
    if timeout_mins and timeout_mins > 0:
        args += ["--timeout", str(timeout_mins)]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=repo if repo else None)
    if result.get("ok") and not preview:
        try:
            parsed = json.loads(result["out"])
            result["data"] = _compact_review(parsed)
            saved = _save_output(json.dumps(parsed, indent=2), "scan")
            result["@"] = saved
            del result["out"]
        except (json.JSONDecodeError, KeyError):
            pass
    return result


def _session_list(repo: str, limit: int) -> dict:
    args = ["session", "list", "--json"]
    if repo:
        args += ["--repo", repo]
    if limit and limit > 0:
        args += ["--limit", str(limit)]

    result = _run(args, cwd=repo if repo else None)
    if result.get("ok"):
        try:
            parsed = json.loads(result["out"])
            result["sessions"] = parsed if isinstance(parsed, list) else []
            result["n"] = len(result["sessions"])
            del result["out"]
        except json.JSONDecodeError:
            pass
    return result


def _session_view(session_id: str, repo: str) -> dict:
    if not session_id:
        return {"e": "session_id required"}

    args = ["session", "show", "--json", session_id]
    if repo:
        args += ["--repo", repo]

    result = _run(args, cwd=repo if repo else None)
    if result.get("ok"):
        try:
            parsed = json.loads(result["out"])
            result["data"] = parsed
            del result["out"]
        except json.JSONDecodeError:
            pass
    return result


def _llm_test() -> dict:
    result = _run(["llm", "test"])
    if result.get("ok"):
        result["configured"] = True
    else:
        result["configured"] = False
        result["h"] = "Configure: ocr config provider / ocr config model"
    return result


def _version() -> dict:
    result = _run(["--version"])
    if result.get("ok") and "out" in result:
        lines = result["out"].split("\n")
        result["version"] = lines[0] if lines else result["out"]
        del result["out"]
    return result


# ── Review JSON compaction ────────────────────────────────────────────────────

def _compact_review(data: dict) -> dict:
    """Compact a full review/scan result to token-efficient summary."""
    out = {}
    if isinstance(data, dict):
        if "comments" in data:
            comments = data["comments"]
            out["nc"] = len(comments) if isinstance(comments, list) else 0
            out["high"] = sum(1 for c in (comments or [])
                            if isinstance(c, dict) and c.get("severity") in ("critical", "high"))
            out["comments"] = [_compact_comment(c) for c in (comments or [])[:20]]
        if "files_reviewed" in data:
            out["nf"] = data["files_reviewed"]
        if "session_id" in data:
            out["sid"] = data["session_id"]
        if "summary" in data:
            out["sum"] = _trunc(str(data["summary"]), 2000)
    return out


def _compact_comment(c: dict) -> dict:
    return {
        "f": c.get("path", ""),
        "l": [c.get("start_line", 0), c.get("end_line", 0)],
        "s": c.get("severity", ""),
        "c": c.get("category", ""),
        "msg": _trunc(str(c.get("content", "")), 500),
        "fix": _trunc(str(c.get("suggestion_code", "")), 300) if c.get("suggestion_code") else "",
    }


# ── Dispatch ──────────────────────────────────────────────────────────────────

_HANDLERS = {
    "preview": lambda kw: _preview(
        str(kw.get("from_ref", "")), str(kw.get("to_ref", "")),
        str(kw.get("commit", "")), str(kw.get("repo", "")),
        str(kw.get("exclude", "")), str(kw.get("background", "")),
        str(kw.get("background_file", ""))),
    "rule": lambda kw: _rule(
        list(kw.get("files", []) or []), str(kw.get("repo", "")),
        str(kw.get("rule_file", ""))),
    "review": lambda kw: _review(
        str(kw.get("from_ref", "")), str(kw.get("to_ref", "")),
        str(kw.get("commit", "")), str(kw.get("repo", "")),
        str(kw.get("background", "")), str(kw.get("background_file", "")),
        bool(kw.get("preview", False)), int(kw.get("concurrency", 0) or 0),
        int(kw.get("timeout_mins", 0) or 0), str(kw.get("rule_file", ""))),
    "scan": lambda kw: _scan(
        str(kw.get("path", "")), str(kw.get("repo", "")),
        bool(kw.get("preview", False)), str(kw.get("background", "")),
        str(kw.get("exclude", "")), bool(kw.get("no_plan", False)),
        int(kw.get("concurrency", 0) or 0), int(kw.get("timeout_mins", 0) or 0)),
    "session_list": lambda kw: _session_list(
        str(kw.get("repo", "")), int(kw.get("limit", 0) or 0)),
    "session_view": lambda kw: _session_view(
        str(kw.get("session_id", "")), str(kw.get("repo", ""))),
    "llm_test": lambda kw: _llm_test(),
    "version": lambda kw: _version(),
}


def ocr_tool(action: str, task_id: str = None, **kwargs) -> str:
    action = (action or "").strip().lower()
    if not _ocr_available():
        return json.dumps({"e": "ocr not installed",
                           "h": "Install: npm i -g @alibaba-group/open-code-review"})

    if not action:
        return json.dumps({"e": "action required",
                           "actions": sorted(_HANDLERS.keys()),
                           "h": "Start with 'preview' (no LLM needed)."})

    fn = _HANDLERS.get(action)
    if fn is None:
        return json.dumps({"e": f"unknown action '{action}'",
                           "actions": sorted(_HANDLERS.keys())})

    result = fn(kwargs)
    return json.dumps(result, indent=2, default=str)


# ── Schema ────────────────────────────────────────────────────────────────────

OCR_SCHEMA = {
    "name": "ocr",
    "description": (
        "AI code review via alibaba/open-code-review CLI. DELEGATION mode "
        "(preview/rule: no LLM needed) = deterministic file selection + rule "
        "resolution. DIRECT mode (review/scan: needs OCR LLM config) = full "
        "AI review with line-level comments. Use preview→rule→review workflow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "preview|rule: delegation (no LLM). review|scan: needs LLM. session_*|llm_test|version: utility.",
                "enum": ["preview", "rule", "review", "scan",
                         "session_list", "session_view", "llm_test", "version"],
            },
            "files": {
                "type": "array", "items": {"type": "string"},
                "description": "For 'rule': repo-relative file paths to get rules for."
            },
            "from_ref": {"type": "string", "description": "Source ref (e.g. 'main') for preview/review."},
            "to_ref": {"type": "string", "description": "Target ref (e.g. 'feature') for preview/review."},
            "commit": {"type": "string", "description": "Single commit hash (preview/review)."},
            "repo": {"type": "string", "description": "Repo root directory (default: cwd)."},
            "path": {"type": "string", "description": "For 'scan': comma-sep dirs/files to scan."},
            "exclude": {"type": "string", "description": "Comma-sep gitignore-style patterns to exclude."},
            "background": {"type": "string", "description": "Business/requirement context."},
            "background_file": {"type": "string", "description": "Markdown file with business context."},
            "rule_file": {"type": "string", "description": "Custom rule.json path."},
            "preview": {"type": "boolean", "description": "Preview which files reviewed (no LLM call)."},
            "no_plan": {"type": "boolean", "description": "For 'scan': skip per-file PLAN_TASK pre-pass."},
            "concurrency": {"type": "integer", "description": "Max concurrent file reviews (default: 8)."},
            "timeout_mins": {"type": "integer", "description": "Per-file timeout in minutes (default: 10)."},
            "limit": {"type": "integer", "description": "session_list: max sessions (default: 20)."},
            "session_id": {"type": "string", "description": "Session ID for session_view."},
        },
        "required": ["action"],
    },
}

registry.register(
    name="ocr",
    toolset="code_review",
    schema=OCR_SCHEMA,
    handler=lambda args, **kw: ocr_tool(
        args.get("action", ""),
        task_id=kw.get("task_id"),
        files=args.get("files", []),
        from_ref=args.get("from_ref", ""),
        to_ref=args.get("to_ref", ""),
        commit=args.get("commit", ""),
        repo=args.get("repo", ""),
        path=args.get("path", ""),
        exclude=args.get("exclude", ""),
        background=args.get("background", ""),
        background_file=args.get("background_file", ""),
        rule_file=args.get("rule_file", ""),
        preview=args.get("preview", False),
        no_plan=args.get("no_plan", False),
        concurrency=args.get("concurrency", 0),
        timeout_mins=args.get("timeout_mins", 0),
        limit=args.get("limit", 0),
        session_id=args.get("session_id", ""),
    ),
    check_fn=_ocr_available,
    emoji="🔍",
    max_result_size_chars=MAX_INLINE_CHARS,
)
