"""Open Code Review (OCR) v2 — AI-powered code review via `ocr` CLI.

Wraps alibaba/open-code-review v1.9+ with three mode groups:

  DELEGATION (always available, no LLM needed on OCR side):
    preview      — which files to review + mode/ref/merge_base metadata
    rule         — matched review rules grouped by content (JSON)
    rules_check  — which rule applies to a given file path

  DIRECT (requires OCR LLM config; gated via config check):
    review           — diff-based review (json | sarif output)
    scan             — full-file scan (json | sarif output, no diff needed)
    session_list     — list saved review sessions
    session_view     — inspect one session (files + comment counts)
    session_comments — extract comments from a saved session (filters)

  UTILITY:
    llm_test      — live OCR LLM connectivity check
    llm_providers — list built-in LLM providers
    version       — OCR CLI version

v2 changes (v1.9.6 CLI): delegate preview/rule now emit JSON (--format json);
review/scan gained --resume, --max-tokens-budget, --no-filter, --batch,
--no-summary, sarif format; new `rules`, `session comments`, `llm providers`
subcommands; files_reviewed moved into summary; scan/review summaries carry
token counts + conditional budget_exceeded.

Output: compact 1-2 char keys. Large results saved to disk (@).
Gated via check_fn on `ocr` binary presence.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from tools.registry import registry

# ── Config ────────────────────────────────────────────────────────────────────

MAX_INLINE_CHARS = 9000
OCR_BIN = shutil.which("ocr") or "ocr"
TIMEOUT_REVIEW = 900    # 15 min for review/scan (LLM runs)
TIMEOUT_DEFAULT = 60
OCR_CONFIG = Path.home() / ".opencodereview" / "config.json"
_LLM_GATE_CACHE = {"mtime": 0.0, "ok": False}


def _ocr_available() -> bool:
    return shutil.which("ocr") is not None


def _ocr_llm_configured() -> bool:
    """Fast gate: OCR config has a provider with an api_key. No LLM call.

    v1 called `ocr llm test` per gate, which now fires a real (paying) LLM
    request. Config presence is a free, ~instant proxy — the live connectivity
    check stays available via the `llm_test` action.
    """
    global _LLM_GATE_CACHE
    try:
        mt = OCR_CONFIG.stat().st_mtime
        if mt == _LLM_GATE_CACHE["mtime"]:
            return _LLM_GATE_CACHE["ok"]
        d = json.loads(OCR_CONFIG.read_text(encoding="utf-8"))
        prov = d.get("provider", "")
        p = d.get("providers", {}).get(prov, {})
        ok = bool(prov and (p.get("api_key") or d.get("llm", {}).get("api_key")))
        _LLM_GATE_CACHE = {"mtime": mt, "ok": ok}
        return ok
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

def _run(args: list, timeout: int = TIMEOUT_DEFAULT, cwd: str | None = None,
         max_inline: int = MAX_INLINE_CHARS) -> dict:
    """Run ocr CLI and return compact result dict.

    max_inline: actions that must PARSE stdout as JSON (session_*, preview,
    rule) pass a large budget so the full output stays in `out` for parsing —
    the 9000-char default would truncate big listings and break json.loads.
    """
    if cwd and not os.path.isdir(cwd):
        return {"e": f"repo dir not found: {cwd}",
                "h": "Pass an existing git repo root (or omit repo= for cwd)."}
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
        if len(stdout) > max_inline:
            saved = _save_output(stdout, "_".join(args[:2]) if args else "ocr")
            result["@"] = saved
            result["out"] = stdout[:2000] + f"... [{len(stdout)} chars, full at @]"
        else:
            result["out"] = stdout
    if stderr:
        result["err"] = _trunc(stderr, 2000)

    return result


def _save_output(text: str, tag: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:50]
    path = _out_dir() / f"{safe}_{int(time.time())}.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _parse_json(result: dict) -> dict:
    """If stdout parses as JSON, move it into `data` (compact) and drop raw."""
    try:
        parsed = json.loads(result["out"])
        result["data"] = parsed
        del result["out"]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass
    return result


def _attach_review_result(result: dict, compact_fn, tag: str) -> dict:
    """Parse review/scan stdout JSON into compact `data` on ANY exit code.

    Budget-stop / partial-failure runs exit non-zero but still publish JSON
    with summary.budget_exceeded + warnings — the agent must see those
    compactly, not as raw JSON. `ok` stays truthful; the failure reason is
    surfaced in `err`.
    """
    try:
        parsed = json.loads(result.get("out", ""))
    except (json.JSONDecodeError, TypeError):
        return result  # CLI-level error — keep raw out/err
    compact = compact_fn(parsed)
    if not compact:
        return result
    result["data"] = compact
    result["@"] = _save_output(json.dumps(parsed, indent=2, default=str), tag)
    if not result.get("ok"):
        msg = (parsed.get("message") or "").strip()[:300]
        result["err"] = msg or result.get("err", "review failed (see data)")
    del result["out"]
    return result


# ── Action handlers ───────────────────────────────────────────────────────────

def _preview(from_ref: str, to_ref: str, commit: str, repo: str,
             exclude: str, background: str, background_file: str) -> dict:
    """ocr delegate preview --format json — deterministic file selection."""
    args = ["delegate", "preview", "--format", "json"]
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

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            result["data"] = _compact_preview(result["data"])
            result["_action"] = "preview"
    return result


def _rule(files: list, repo: str, rule_file: str) -> dict:
    """ocr delegate rule --format json — matched review rules grouped by content."""
    if not files:
        return {"e": "files required (list of repo-relative paths)"}

    args = ["delegate", "rule", "--format", "json"] + list(files)
    if repo:
        args += ["--repo", repo]
    if rule_file:
        args += ["--rule", rule_file]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        # Keep full raw output (complete rule text) so the delegating agent
        # can read the FULL rules; add compact data for navigation (group
        # counts, sources, patterns). Oversized rulesets (> MAX_INLINE) are
        # saved to @ with a truncated inline preview.
        raw_out = result.get("out", "")
        try:
            parsed = json.loads(raw_out)
            result["data"] = _compact_rule(parsed)
            result["fs"] = files
            result["_action"] = "rule"
            if len(raw_out) > MAX_INLINE_CHARS:
                result["@"] = _save_output(raw_out, "rule")
                result["out"] = raw_out[:2000] + \
                    f"... [{len(raw_out)} chars, full at @]"
        except (json.JSONDecodeError, TypeError):
            pass
    return result


def _rules_check(file_path: str, repo: str, rule_file: str) -> dict:
    """ocr rules check <file> — which rule applies to a file path."""
    if not file_path:
        return {"e": "file required (repo-relative path)"}

    args = ["rules", "check", file_path]
    if repo:
        args += ["--repo", repo]
    if rule_file:
        args += ["--rule", rule_file]

    result = _run(args, cwd=repo if repo else None)
    if result.get("ok"):
        result["_action"] = "rules_check"
        out = result.get("out", "")
        meta = {}
        for line in out.splitlines():
            if line.startswith("File:"):
                meta["f"] = line.split(":", 1)[1].strip()
            elif line.startswith("Source:"):
                meta["src"] = line.split(":", 1)[1].strip()
            elif line.startswith("Pattern:"):
                meta["pat"] = line.split(":", 1)[1].strip()
        if meta:
            result["data"] = meta
    return result


def _review(from_ref: str, to_ref: str, commit: str, repo: str,
            background: str, background_file: str, preview: bool,
            concurrency: int, timeout_mins: int, rule_file: str,
            resume: str, max_tokens_budget: int, no_filter: bool,
            provider: str, model: str, fmt: str) -> dict:
    """ocr review — diff-based review (needs LLM unless preview=True)."""
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use preview+rule delegation mode, or run: ocr config provider"}

    fmt = (fmt or "json").lower()
    if fmt not in ("json", "sarif", "text"):
        return {"e": f"unsupported format '{fmt}'", "h": "Use json, sarif or text."}
    if fmt == "sarif" and preview:
        return {"e": "--format sarif is not supported with --preview",
                "h": "SARIF requires a completed review; drop preview=True."}

    args = ["review", "--audience", "agent", "--format", fmt]
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
    if resume:
        args += ["--resume", resume]
    if max_tokens_budget and max_tokens_budget > 0:
        args += ["--max-tokens-budget", str(max_tokens_budget)]
    if no_filter:
        args += ["--no-filter"]
    if provider:
        args += ["--provider", provider]
    if model:
        args += ["--model", model]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=repo if repo else None)
    if not preview and fmt == "json":
        _attach_review_result(result, _compact_review, "review")
    elif result.get("ok") and fmt == "sarif":
        # SARIF is verbose JSON — always save to disk, inline summary
        result["@"] = _save_output(result.get("out", ""), "review_sarif")
        result["out"] = "SARIF report saved to @" + (
            " | " + result["out"][:200] if result.get("out") else "")
    if result.get("ok") and not preview:
        result["_action"] = "review"
    return result


def _scan(path: str, repo: str, preview: bool, background: str,
          exclude: str, no_plan: bool, concurrency: int, timeout_mins: int,
          resume: str, batch: str, no_summary: bool, no_dedup: bool,
          max_tokens_budget: int, fmt: str) -> dict:
    """ocr scan — full-file scan (needs LLM unless preview=True)."""
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use delegation mode, or run: ocr config provider"}

    fmt = (fmt or "json").lower()
    if fmt not in ("json", "sarif", "text"):
        return {"e": f"unsupported format '{fmt}'", "h": "Use json, sarif or text."}
    if fmt == "sarif" and preview:
        return {"e": "--format sarif is not supported with --preview",
                "h": "SARIF requires a completed scan; drop preview=True."}

    args = ["scan", "--audience", "agent", "--format", fmt]
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
    if resume:
        args += ["--resume", resume]
    if batch:
        args += ["--batch", batch]
    if no_summary:
        args += ["--no-summary"]
    if no_dedup:
        args += ["--no-dedup"]
    if max_tokens_budget and max_tokens_budget > 0:
        args += ["--max-tokens-budget", str(max_tokens_budget)]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=repo if repo else None)
    if not preview and fmt == "json":
        _attach_review_result(result, _compact_review, "scan")
    elif result.get("ok") and fmt == "sarif":
        result["@"] = _save_output(result.get("out", ""), "scan_sarif")
        result["out"] = "SARIF report saved to @" + (
            " | " + result["out"][:200] if result.get("out") else "")
    if result.get("ok") and not preview:
        result["_action"] = "scan"
    return result


def _session_list(repo: str, limit: int) -> dict:
    args = ["session", "list", "--json"]
    if repo:
        args += ["--repo", repo]
    if limit and limit > 0:
        args += ["--limit", str(limit)]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            sessions = result["data"] if isinstance(result["data"], list) else []
            result["n"] = len(sessions)
            result["sessions"] = [_compact_session(s) for s in sessions[:20]]
            if len(sessions) > 20:
                result["more"] = len(sessions) - 20
            # Drop the raw (huge, run_manifest-laden) data — sessions carries
            # everything the agent needs.
            del result["data"]
            result["_action"] = "session_list"
    return result


def _session_view(session_id: str, repo: str) -> dict:
    if not session_id:
        return {"e": "session_id required"}

    args = ["session", "show", "--json", session_id]
    if repo:
        args += ["--repo", repo]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            result["data"] = _compact_session_view(result["data"])
            result["_action"] = "session_view"
    return result


def _session_comments(session_id: str, repo: str, severity: str,
                      category: str) -> dict:
    if not session_id:
        return {"e": "session_id required"}

    args = ["session", "comments", "--json", session_id]
    if repo:
        args += ["--repo", repo]
    if severity:
        args += ["--severity", severity]
    if category:
        args += ["--category", category]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            cs = result["data"] if isinstance(result["data"], list) else []
            result["sid"] = session_id
            result["n"] = len(cs)
            result["nc_hi"] = sum(1 for c in cs
                                  if isinstance(c, dict) and c.get("severity") in
                                  ("critical", "high"))
            result["comments"] = [_compact_comment(c) for c in cs[:30]]
            if len(cs) > 30:
                result["more"] = len(cs) - 30
            # Drop the raw comment list — compact `comments` carries all fields
            del result["data"]
            result["_action"] = "session_comments"
    return result


def _llm_test() -> dict:
    result = _run(["llm", "test"])
    if result.get("ok"):
        result["configured"] = True
        # Extract Source/URL/Model lines from the text output
        for line in (result.get("out", "") or "").splitlines():
            if line.startswith("Source:"):
                result["src"] = line.split(":", 1)[1].strip()
            elif line.startswith("URL:"):
                result["url"] = line.split(":", 1)[1].strip()
            elif line.startswith("Model:"):
                result["model"] = line.split(":", 1)[1].strip()
    else:
        result["configured"] = False
        result["h"] = "Configure: ocr config provider / ocr config model"
    return result


def _llm_providers() -> dict:
    result = _run(["llm", "providers"])
    if result.get("ok"):
        provs = []
        for line in (result.get("out", "") or "").splitlines():
            parts = line.split("  ")
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 2 and parts[0] != "NAME" and not parts[0].startswith("---"):
                provs.append({"n": parts[0],
                              "p": parts[1] if len(parts) > 1 else "",
                              "u": parts[2] if len(parts) > 2 else ""})
        if provs:
            result["providers"] = provs
            result["n"] = len(provs)
            result["_action"] = "llm_providers"
            del result["out"]
    return result


def _version() -> dict:
    result = _run(["--version"])
    if result.get("ok") and "out" in result:
        lines = result["out"].split("\n")
        result["version"] = lines[0] if lines else result["out"]
        del result["out"]
    return result


# ── Compactors ────────────────────────────────────────────────────────────────

def _compact_preview(d: dict) -> dict:
    out = {"mode": d.get("mode", ""), "repo": d.get("repository", "")}
    for k in ("from", "to", "merge_base"):
        if d.get(k):
            out[k] = d[k]
    out["nf"] = d.get("reviewable_count", 0)
    out["tot"] = d.get("total_files", 0)
    out["ins"] = d.get("total_insertions", 0)
    out["del"] = d.get("total_deletions", 0)
    files = d.get("reviewable_files") or []
    out["files"] = [{"p": f.get("path", ""), "s": f.get("status", ""),
                     "i": f.get("insertions", 0), "d": f.get("deletions", 0)}
                    for f in files]
    excl = d.get("excluded_files") or []
    if excl:
        out["excl"] = [f.get("path", "") for f in excl]
    return out


def _compact_rule(d: dict) -> dict:
    groups = d.get("groups") or []
    out = {"ng": len(groups)}
    gs = []
    for g in groups:
        gs.append({"src": g.get("source", ""),
                   "pat": g.get("pattern", ""),
                   "fs": g.get("files", []),
                   "rule": _trunc(str(g.get("rule", "")), 800)})
    out["groups"] = gs
    return out


def _compact_review(d: dict) -> dict:
    out: dict = {}
    s = d.get("summary") or {}
    out["nf"] = s.get("files_reviewed", 0)
    cs = d.get("comments") or []
    out["nc"] = s.get("comments", len(cs) if isinstance(cs, list) else 0)
    toks = {}
    for k in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens"):
        if s.get(k) is not None:
            toks[k[0]] = s[k]   # i / o / t / c
    if toks:
        out["toks"] = toks
    if s.get("elapsed"):
        out["el"] = s["elapsed"]
    if s.get("budget_exceeded"):
        out["budget"] = s["budget_exceeded"]
    ws = d.get("warnings")
    if isinstance(ws, list) and ws:
        out["warns"] = [{"f": w.get("file", ""),
                         "m": _trunc(str(w.get("message", "")), 200)}
                        for w in ws[:5]]
        if len(ws) > 5:
            out["warns_more"] = len(ws) - 5
    if d.get("session_id"):
        out["sid"] = d["session_id"]
    llm = d.get("llm")
    if isinstance(llm, dict) and (llm.get("model") or llm.get("provider")):
        out["llm"] = {k: v for k, v in llm.items() if v}
    rr = d.get("retry_report")
    if isinstance(rr, dict) and (rr.get("retried_requests") or rr.get("failed_requests")):
        out["retries"] = {"req": rr.get("total_requests"),
                          "retried": rr.get("retried_requests"),
                          "fail": rr.get("failed_requests")}
    tc = d.get("tool_calls")
    if isinstance(tc, dict) and tc.get("total"):
        out["tc"] = tc.get("total")
    if isinstance(cs, list) and cs:
        out["cmts"] = [_compact_comment(c) for c in cs[:20]]
        if len(cs) > 20:
            out["more"] = len(cs) - 20
    ps = d.get("project_summary")
    if ps:
        out["psum"] = _trunc(str(ps), 800)
    return out


def _compact_comment(c: dict) -> dict:
    return {
        "f": c.get("path", ""),
        "l": [c.get("start_line", 0), c.get("end_line", 0)],
        "s": c.get("severity", ""),
        "c": c.get("category", ""),
        "msg": _trunc(str(c.get("content", "")), 500),
        "fix": _trunc(str(c.get("suggestion_code", "")), 300) if c.get("suggestion_code") else "",
        "ex": _trunc(str(c.get("existing_code", "")), 200) if c.get("existing_code") else "",
    }


def _compact_session(s: dict) -> dict:
    return {
        "id": s.get("session_id", ""),
        "mode": s.get("review_mode", ""),
        "m": s.get("model", ""),
        "t": s.get("start_time", ""),
        "dur": round((s.get("duration_ns") or 0) / 1e9, 1),
        "sel": s.get("selected_files", 0),
        "done": s.get("completed_files", 0),
        "fail": s.get("failed_files", 0),
        "cmts": s.get("total_comments", 0),
        "ab": s.get("aborted", False),
    }


def _compact_session_view(d: dict) -> dict:
    out: dict = {}
    sm = d.get("summary") or {}
    out["id"] = sm.get("session_id", "")
    out["mode"] = sm.get("review_mode", "")
    out["m"] = sm.get("model", "")
    out["start"] = sm.get("start_time", "")
    out["end"] = sm.get("end_time", "")
    out["dur"] = round((sm.get("duration_ns") or 0) / 1e9, 1)
    out["sel"] = sm.get("selected_files", 0)
    out["done"] = sm.get("completed_files", 0)
    out["fail"] = sm.get("failed_files", 0)
    out["cmts"] = sm.get("total_comments", 0)
    items = d.get("items") or []
    out["items"] = [{"p": it.get("new_path") or it.get("file_path", ""),
                     "ty": it.get("type", ""),
                     "n": it.get("comments")
                     if isinstance(it.get("comments"), int)
                     else len(it.get("comments") or [])}
                    for it in items]
    return out


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
    "rules_check": lambda kw: _rules_check(
        str(kw.get("file", "")), str(kw.get("repo", "")),
        str(kw.get("rule_file", ""))),
    "review": lambda kw: _review(
        str(kw.get("from_ref", "")), str(kw.get("to_ref", "")),
        str(kw.get("commit", "")), str(kw.get("repo", "")),
        str(kw.get("background", "")), str(kw.get("background_file", "")),
        bool(kw.get("preview", False)), int(kw.get("concurrency", 0) or 0),
        int(kw.get("timeout_mins", 0) or 0), str(kw.get("rule_file", "")),
        str(kw.get("resume", "")), int(kw.get("max_tokens_budget", 0) or 0),
        bool(kw.get("no_filter", False)), str(kw.get("provider", "")),
        str(kw.get("model", "")), str(kw.get("format", ""))),
    "scan": lambda kw: _scan(
        str(kw.get("path", "")), str(kw.get("repo", "")),
        bool(kw.get("preview", False)), str(kw.get("background", "")),
        str(kw.get("exclude", "")), bool(kw.get("no_plan", False)),
        int(kw.get("concurrency", 0) or 0), int(kw.get("timeout_mins", 0) or 0),
        str(kw.get("resume", "")), str(kw.get("batch", "")),
        bool(kw.get("no_summary", False)), bool(kw.get("no_dedup", False)),
        int(kw.get("max_tokens_budget", 0) or 0), str(kw.get("format", ""))),
    "session_list": lambda kw: _session_list(
        str(kw.get("repo", "")), int(kw.get("limit", 0) or 0)),
    "session_view": lambda kw: _session_view(
        str(kw.get("session_id", "")), str(kw.get("repo", ""))),
    "session_comments": lambda kw: _session_comments(
        str(kw.get("session_id", "")), str(kw.get("repo", "")),
        str(kw.get("severity", "")), str(kw.get("category", ""))),
    "llm_test": lambda kw: _llm_test(),
    "llm_providers": lambda kw: _llm_providers(),
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
        "AI code review via alibaba/open-code-review v1.9+ CLI. DELEGATION "
        "(preview/rule/rules_check: no LLM needed) = deterministic file "
        "selection + rule resolution. DIRECT (review/scan: needs OCR LLM "
        "config) = full AI review with line-level comments, json|sarif. "
        "Utility: session_list/view/comments, llm_test, llm_providers, version."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "preview|rule|rules_check: delegation (no LLM). review|scan: needs LLM (preview=True dry-runs). session_*|llm_*|version: utility.",
                "enum": ["preview", "rule", "rules_check", "review", "scan",
                         "session_list", "session_view", "session_comments",
                         "llm_test", "llm_providers", "version"],
            },
            "files": {
                "type": "array", "items": {"type": "string"},
                "description": "For 'rule': repo-relative file paths to get rules for."
            },
            "file": {
                "type": "string",
                "description": "For 'rules_check': repo-relative path to check which rule applies."
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
            "session_id": {"type": "string", "description": "Session ID for session_view/comments."},
            "resume": {"type": "string", "description": "Resume review/scan from a previous session ID."},
            "max_tokens_budget": {"type": "integer", "description": "Cap total tokens (input+output); stops dispatch when exceeded."},
            "no_filter": {"type": "boolean", "description": "review: keep all comments without LLM post-filtering."},
            "provider": {"type": "string", "description": "Override LLM provider for this run only."},
            "model": {"type": "string", "description": "Override LLM model for this run only."},
            "format": {"type": "string", "description": "Output format for review/scan: json|sarif|text (default json)."},
            "batch": {"type": "string", "description": "scan: override BATCH_STRATEGY none|by-language|by-directory."},
            "no_summary": {"type": "boolean", "description": "scan: skip post-run PROJECT_SUMMARY_TASK."},
            "no_dedup": {"type": "boolean", "description": "scan: skip per-batch DEDUP_TASK."},
            "severity": {"type": "string", "description": "session_comments: comma-sep severities (critical,high,medium,low)."},
            "category": {"type": "string", "description": "session_comments: comma-sep categories (e.g. bug,security)."},
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
        file=args.get("file", ""),
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
        resume=args.get("resume", ""),
        max_tokens_budget=args.get("max_tokens_budget", 0),
        no_filter=args.get("no_filter", False),
        provider=args.get("provider", ""),
        model=args.get("model", ""),
        format=args.get("format", ""),
        batch=args.get("batch", ""),
        no_summary=args.get("no_summary", False),
        no_dedup=args.get("no_dedup", False),
        severity=args.get("severity", ""),
        category=args.get("category", ""),
    ),
    check_fn=_ocr_available,
    emoji="🔍",
    max_result_size_chars=MAX_INLINE_CHARS,
)
