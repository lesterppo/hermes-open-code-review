"""Open Code Review (OCR) v3 — AI-powered code review via `ocr` CLI.

Wraps alibaba/open-code-review v1.12+ with three mode groups:

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
    session_compare  — diff two sessions (new / persisting / resolved)

  UTILITY:
    llm_test      — live OCR LLM connectivity check
    llm_providers — list built-in LLM providers
    viewer        — start the local session-history WebUI (background)
    viewer_stop   — stop a viewer started by this tool
    version       — OCR CLI version

v3 changes (CLI v1.12.x):
  * review gained --effort (low|medium|high, review-only in v1.12.x).
  * both gained --max-tokens (per-group prompt ceiling), --max-tools,
    --max-git-procs, and -o/--output (the tool now routes the report
    through its own -o file and re-reads it, keeping stdout compact).
  * new `session compare` subcommand → `session_compare` action (new /
    persisting / resolved findings between two runs).
  * new `ocr viewer` WebUI → `viewer` / `viewer_stop` actions (detached,
    prints the URL; never blocks the agent).
  * `--color never` is forced on every invocation so stdout stays free of
    ANSI escapes (auto-degrades on older CLIs without the flag).
  * rule languages added upstream (Handlebars/Mustache/Pug, Verilog/
    SystemVerilog/VHDL, Solidity/Vyper, OCaml/ReasonML, Rego, Objective-C,
    MATLAB) — no tool change needed, rules come from the CLI.

Output: compact 1-2 char keys. Large results saved to disk (@).
Gated via check_fn on `ocr` binary presence.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

from tools.registry import registry

# ── Config ────────────────────────────────────────────────────────────────────

MAX_INLINE_CHARS = 9000
OCR_BIN = shutil.which("ocr") or "ocr"
TIMEOUT_REVIEW = 1800   # 30 min for review/scan (LLM runs)
TIMEOUT_DEFAULT = 60
TOOL_VERSION = "3.0.1"
OCR_CONFIG = Path.home() / ".opencodereview" / "config.json"
_LLM_GATE_CACHE = {"mtime": 0.0, "ok": False}
_EFFORTS = ("low", "medium", "high")


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
         max_inline: int = MAX_INLINE_CHARS, color: bool = True) -> dict:
    """Run ocr CLI and return compact result dict.

    max_inline: actions that must PARSE stdout as JSON (session_*, preview,
    rule) pass a large budget so the full output stays in `out` for parsing —
    the 9000-char default would truncate big listings and break json.loads.
    color=False drops `--color never` (retry path for CLIs predating it).
    """
    if cwd and not os.path.isdir(cwd):
        return {"e": f"repo dir not found: {cwd}",
                "h": "Pass an existing git repo root (or omit repo= for cwd)."}
    argv = [OCR_BIN] + list(args)
    if color:
        argv += ["--color", "never"]
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"e": f"timeout ({timeout}s)", "h": "Increase timeout_mins / use resume."}
    except FileNotFoundError:
        return {"e": "ocr not found", "h": "Install: npm i -g @alibaba-group/open-code-review"}
    except Exception as e:
        return {"e": str(e)[:200]}

    stderr = (r.stderr or "").strip()
    if color and r.returncode != 0 and "unknown flag: --color" in stderr:
        return _run(args, timeout=timeout, cwd=cwd, max_inline=max_inline, color=False)

    stdout = (r.stdout or "").strip()
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


def _save_output(text: str, tag: str, suffix: str = ".txt") -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in tag)[:50]
    path = _out_dir() / f"{safe}_{int(time.time())}{suffix}"
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
    result["@"] = _save_output(json.dumps(parsed, indent=2, default=str), tag, ".json")
    if not result.get("ok"):
        msg = (parsed.get("message") or "").strip()[:300]
        result["err"] = msg or result.get("err", "review failed (see data)")
    del result["out"]
    return result


def _resolve_output_file(fmt: str) -> str:
    """v1.10+ `-o` target: our own temp path so stdout stays compact."""
    return str(_out_dir() / f"ocr_{fmt}_{int(time.time() * 1000)}.{fmt}")


def _read_output_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""


def _apply_output_file(result: dict, out_file: str, tag: str) -> None:
    """Fold an `-o <file>` report back into the result.

    The CLI writes the report to OUR file and prints only summary/progress
    lines to stdout. If it wrote nothing (failure before dispatch, aborted
    run, older build), keep meaningful stdout instead of discarding the only
    diagnostics we have.
    """
    payload = _read_output_file(out_file)
    if payload.strip():
        result["out"] = payload
        result["result_file"] = out_file
    else:
        result["note"] = "CLI wrote no report file (see err/out for the reason)"
        stdout = result.get("out") or ""
        if len(stdout) < 120:          # progress chatter only — drop it
            result.pop("out", None)
    cleaned = _strip_progress(result.get("err") or "")
    if cleaned:
        result["err"] = cleaned
    else:
        result.pop("err", None)


def _strip_progress(err: str) -> str:
    """Drop OCR's `-o` status line ("[ocr] Results written to <path>") and
    `--audience agent` progress chatter from `err` — they are not errors."""
    if not err:
        return ""
    keep = [ln for ln in err.splitlines()
            if "Results written to" not in ln
            and not ln.strip().startswith("[ocr] ")]
    return "\n".join(keep).strip()


# ── Action handlers ───────────────────────────────────────────────────────────

def _preview(from_ref: str, to_ref: str, commit: str, repo: str,
             exclude: str, background: str, background_file: str,
             rule_file: str, max_git_procs: int) -> dict:
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
    if rule_file:
        args += ["--rule", rule_file]
    if max_git_procs and max_git_procs > 0:
        args += ["--max-git-procs", str(max_git_procs)]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            result["data"] = _compact_preview(result["data"])
            result["_action"] = "preview"
    return result


def _rule(files: list, repo: str, rule_file: str, exclude: str,
          commit: str, max_git_procs: int = 0) -> dict:
    """ocr delegate rule --format json — matched review rules grouped by content."""
    if not files:
        return {"e": "files required (list of repo-relative paths)"}

    args = ["delegate", "rule", "--format", "json"] + list(files)
    if repo:
        args += ["--repo", repo]
    if rule_file:
        args += ["--rule", rule_file]
    if exclude:
        args += ["--exclude", exclude]
    if commit:
        args += ["-c", commit]
    if max_git_procs and max_git_procs > 0:
        args += ["--max-git-procs", str(max_git_procs)]

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
                result["@"] = _save_output(raw_out, "rule", ".json")
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


def _direct_args(mode: str, fmt: str) -> list:
    return [mode, "--audience", "agent", "--format", fmt]


def _review(kw: dict) -> dict:
    """ocr review — diff-based review (needs LLM unless preview=True)."""
    preview = _bool(kw.get("preview", False))
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use preview+rule delegation mode, or run: ocr config provider"}

    fmt = (kw.get("format") or "json").lower()
    bad = _check_format(fmt, preview)
    if bad:
        return bad
    effort = (kw.get("effort") or "").lower()
    if effort and effort not in _EFFORTS:
        return {"e": f"unsupported effort '{effort}'", "h": "Use low, medium or high."}

    out_file = ""
    args = _direct_args("review", fmt)
    for key, flag in (("from_ref", "--from"), ("to_ref", "--to"),
                      ("commit", "-c"), ("repo", "--repo"),
                      ("background", "-b"), ("background_file", "-B"),
                      ("rule_file", "--rule"), ("resume", "--resume"),
                      ("provider", "--provider"), ("model", "--model")):
        v = kw.get(key)
        if v:
            args += [flag, str(v)]
    if kw.get("exclude"):
        args += ["--exclude", str(kw["exclude"])]
    if preview:
        args += ["--preview"]
    if effort:
        args += ["--effort", effort]
    for key, flag in (("concurrency", "--concurrency"), ("timeout_mins", "--timeout"),
                      ("max_tokens_budget", "--max-tokens-budget"),
                      ("max_tokens", "--max-tokens"), ("max_tools", "--max-tools"),
                      ("max_git_procs", "--max-git-procs")):
        n = _int(kw.get(key))
        if n > 0:
            args += [flag, str(n)]
    if _bool(kw.get("no_filter", False)):
        args += ["--no-filter"]
    # v1.10+ --output: write the report to OUR file so stdout carries the
    # summary lines and we can read back the (possibly large) payload.
    if not preview and fmt in ("json", "sarif"):
        out_file = _resolve_output_file(fmt)
        args += ["-o", out_file]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=str(kw.get("repo") or "") or None)

    if out_file:
        _apply_output_file(result, out_file, "review")

    if not preview and fmt == "json":
        _attach_review_result(result, _compact_review, "review")
    elif result.get("ok") and fmt == "sarif":
        result["@"] = _save_output(result.get("out", ""), "review_sarif", ".sarif")
        result["out"] = "SARIF report saved to @"
    if result.get("ok") and not preview:
        result["_action"] = "review"
    return result


def _scan(kw: dict) -> dict:
    """ocr scan — full-file scan (needs LLM unless preview=True)."""
    preview = _bool(kw.get("preview", False))
    if not preview and not _ocr_llm_configured():
        return {"e": "LLM not configured on OCR side",
                "h": "Use delegation mode, or run: ocr config provider"}

    fmt = (kw.get("format") or "json").lower()
    bad = _check_format(fmt, preview)
    if bad:
        return bad
    # `ocr scan` has no --effort in v1.12.x (review-only control) — reject
    # explicitly rather than silently dropping the caller's intent.
    if kw.get("effort"):
        return {"e": "effort is review-only in OCR v1.12.x ('ocr scan' has no --effort)",
                "h": "Use action='review' with effort, or tune scan via max_tokens/max_tools."}
    batch = str(kw.get("batch") or "")
    if batch and batch not in ("none", "by-language", "by-directory"):
        return {"e": f"unsupported batch '{batch}'",
                "h": "Use none, by-language or by-directory."}

    out_file = ""
    args = _direct_args("scan", fmt)
    for key, flag in (("path", "--path"), ("repo", "--repo"),
                      ("background", "-b"), ("exclude", "--exclude"),
                      ("resume", "--resume"), ("provider", "--provider"),
                      ("model", "--model"), ("rule_file", "--rule")):
        v = kw.get(key)
        if v:
            args += [flag, str(v)]
    if preview:
        args += ["--preview"]
    if batch:
        args += ["--batch", batch]
    for key, flag in (("concurrency", "--concurrency"), ("timeout_mins", "--timeout"),
                      ("max_tokens_budget", "--max-tokens-budget"),
                      ("max_tokens", "--max-tokens"), ("max_tools", "--max-tools"),
                      ("max_git_procs", "--max-git-procs")):
        n = _int(kw.get(key))
        if n > 0:
            args += [flag, str(n)]
    for key, flag in (("no_plan", "--no-plan"), ("no_summary", "--no-summary"),
                      ("no_dedup", "--no-dedup")):
        if _bool(kw.get(key, False)):
            args += [flag]
    if not preview and fmt in ("json", "sarif"):
        out_file = _resolve_output_file(fmt)
        args += ["-o", out_file]

    t = TIMEOUT_REVIEW if not preview else TIMEOUT_DEFAULT
    result = _run(args, timeout=t, cwd=str(kw.get("repo") or "") or None)

    if out_file:
        _apply_output_file(result, out_file, "scan")

    if not preview and fmt == "json":
        _attach_review_result(result, _compact_review, "scan")
    elif result.get("ok") and fmt == "sarif":
        result["@"] = _save_output(result.get("out", ""), "scan_sarif", ".sarif")
        result["out"] = "SARIF report saved to @"
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


def _session_compare(before: str, after: str, repo: str) -> dict:
    """ocr session compare <before> <after> — new / persisting / resolved."""
    if not before or not after:
        return {"e": "before_id and after_id required (two session IDs)",
                "h": "Use session_list to find IDs."}

    args = ["session", "compare", before, after, "--json"]
    if repo:
        args += ["--repo", repo]

    result = _run(args, cwd=repo if repo else None, max_inline=8_000_000)
    if result.get("ok"):
        _parse_json(result)
        if "data" in result:
            result["data"] = _compact_compare(result["data"], before, after)
            result["_action"] = "session_compare"
    return result


def _is_ocr_viewer(pid: int) -> bool:
    """Guard before signalling: the state file could be stale and the pid
    recycled by an unrelated process. Only kill something that IS `ocr viewer`."""
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ")
        text = cmdline.decode("utf-8", "replace")
    except Exception:
        return False
    low = text.lower()
    return "ocr" in low and "viewer" in low


def _viewer(addr: str, stop: bool, open_browser: bool) -> dict:
    """Start/stop `ocr viewer` (session-history WebUI) detached."""
    state_path = _out_dir() / "viewer.json"
    addr = addr or "localhost:5483"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    running = False
    pid = _int(state.get("pid"))
    if pid > 0:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            running = False

    if stop:
        if not running:
            return {"ok": True, "_action": "viewer_stop", "e": "no viewer running"}
        if not _is_ocr_viewer(pid):
            state_path.unlink(missing_ok=True)
            return {"ok": True, "_action": "viewer_stop", "stale": True,
                    "e": f"pid {pid} is not an ocr viewer; state cleared"}
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception as e:
                return {"e": f"kill failed: {e}"}
        state_path.unlink(missing_ok=True)
        return {"ok": True, "_action": "viewer_stop", "pid": pid}

    if running:
        return {"ok": True, "_action": "viewer", "url": state.get("url", ""),
                "pid": pid, "already_running": True,
                "log": state.get("log", "")}

    url = f"http://{addr}"
    log_path = _out_dir() / f"viewer_{int(time.time())}.log"
    args = [OCR_BIN, "viewer", "--addr", addr,
            "--open", "always" if open_browser else "never"]
    try:
        # The child inherits the fd; the parent must close its own handle or
        # every viewer start leaks a descriptor.
        with open(log_path, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(args, stdout=lf, stderr=subprocess.STDOUT,
                                    start_new_session=True)
    except Exception as e:
        return {"e": f"viewer start failed: {e}"}

    deadline = time.time() + 12
    up = False
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                up = r.status == 200
                if up:
                    break
        except Exception:
            time.sleep(0.5)
    if not up:
        try:
            proc.terminate()
        except Exception:
            pass
        tail = _read_output_file(str(log_path))[-500:]
        return {"e": "viewer did not become reachable", "url": url,
                "log": str(log_path), "tail": tail}

    state_path.write_text(json.dumps({"pid": proc.pid, "url": url,
                                      "log": str(log_path)}), encoding="utf-8")
    return {"ok": True, "_action": "viewer", "url": url, "pid": proc.pid,
            "log": str(log_path)}


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
        result["tool"] = TOOL_VERSION
        del result["out"]
    return result


# ── Small helpers ─────────────────────────────────────────────────────────────

def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(v)


def _file_list(v) -> list:
    """Accept a list of paths, a comma-separated string, or a single path.
    A bare string must NOT be iterated char-by-char (['a','.','p','y'])."""
    if not v:
        return []
    if isinstance(v, str):
        return [p.strip() for p in v.split(",") if p.strip()]
    if isinstance(v, (list, tuple)):
        out = []
        for item in v:
            out.extend(_file_list(item) if "," in str(item) else [str(item).strip()])
        return [p for p in out if p]
    return [str(v)]


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _check_format(fmt: str, preview: bool) -> dict | None:
    if fmt not in ("json", "sarif", "text"):
        return {"e": f"unsupported format '{fmt}'", "h": "Use json, sarif or text."}
    if fmt == "sarif" and preview:
        return {"e": "--format sarif is not supported with --preview",
                "h": "SARIF requires a completed run; drop preview=True."}
    return None


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
    """Compact one finding. `thinking` (the model's private CoT) is dropped —
    it can be tens of KB per comment and carries no review signal."""
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


def _compact_compare(d: dict, before: str, after: str) -> dict:
    """session compare → counts + compact findings per bucket (thinking dropped)."""
    def bucket(key: str, cap: int = 20) -> dict:
        items = d.get(key) or []
        b: dict = {"n": len(items)}
        if items:
            b["items"] = [_compact_comment(c) for c in items[:cap]]
            if len(items) > cap:
                b["more"] = len(items) - cap
        return b

    out = {
        "before": (d.get("before") or {}).get("session_id", before),
        "after": (d.get("after") or {}).get("session_id", after),
        "before_mode": (d.get("before") or {}).get("review_mode", ""),
        "after_mode": (d.get("after") or {}).get("review_mode", ""),
        "new": bucket("new"),
        "persisting": bucket("persisting"),
        "resolved": bucket("resolved"),
    }
    # v1.10.2 also reports not-reviewed items when the coverage differs.
    if d.get("not_reviewed") is not None:
        out["not_reviewed"] = bucket("not_reviewed")
    return out


# ── Dispatch ──────────────────────────────────────────────────────────────────

_HANDLERS = {
    "preview": lambda kw: _preview(
        str(kw.get("from_ref", "")), str(kw.get("to_ref", "")),
        str(kw.get("commit", "")), str(kw.get("repo", "")),
        str(kw.get("exclude", "")), str(kw.get("background", "")),
        str(kw.get("background_file", "")), str(kw.get("rule_file", "")),
        _int(kw.get("max_git_procs"))),
    "rule": lambda kw: _rule(
        _file_list(kw.get("files")), str(kw.get("repo", "")),
        str(kw.get("rule_file", "")), str(kw.get("exclude", "")),
        str(kw.get("commit", "")), _int(kw.get("max_git_procs"))),
    "rules_check": lambda kw: _rules_check(
        str(kw.get("file", "")), str(kw.get("repo", "")),
        str(kw.get("rule_file", ""))),
    "review": _review,
    "scan": _scan,
    "session_list": lambda kw: _session_list(
        str(kw.get("repo", "")), _int(kw.get("limit"))),
    "session_view": lambda kw: _session_view(
        str(kw.get("session_id", "")), str(kw.get("repo", ""))),
    "session_comments": lambda kw: _session_comments(
        str(kw.get("session_id", "")), str(kw.get("repo", "")),
        str(kw.get("severity", "")), str(kw.get("category", ""))),
    "session_compare": lambda kw: _session_compare(
        str(kw.get("before_id", "") or kw.get("session_id", "")),
        str(kw.get("after_id", "")), str(kw.get("repo", ""))),
    "viewer": lambda kw: _viewer(
        str(kw.get("addr", "")), False, _bool(kw.get("open_browser", False))),
    "viewer_stop": lambda kw: _viewer(str(kw.get("addr", "")), True, False),
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

    try:
        result = fn(kwargs)
    except Exception as e:  # noqa: BLE001 — a tool must always answer in JSON
        result = {"ok": False, "e": f"internal error: {type(e).__name__}: {e}",
                  "h": "Report this with the action + params; the tool must never raise."}
        result["_action"] = action
    return json.dumps(result, indent=2, default=str)


# ── Schema ────────────────────────────────────────────────────────────────────

OCR_SCHEMA = {
    "name": "ocr",
    "description": (
        "AI code review via alibaba/open-code-review v1.12+ CLI. DELEGATION "
        "(preview/rule/rules_check: no LLM needed) = deterministic file "
        "selection + rule resolution. DIRECT (review/scan: needs OCR LLM "
        "config) = full AI review with line-level comments, json|sarif, "
        "effort, token budget. Utility: session_* (list/view/comments/"
        "compare), llm_test, llm_providers, viewer, version."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "preview|rule|rules_check: delegation (no LLM). review|scan: needs LLM (preview=True dry-runs). session_*|llm_*|viewer|version: utility.",
                "enum": ["preview", "rule", "rules_check", "review", "scan",
                         "session_list", "session_view", "session_comments",
                         "session_compare", "llm_test", "llm_providers",
                         "viewer", "viewer_stop", "version"],
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
            "commit": {"type": "string", "description": "Single commit hash (preview/review/rule)."},
            "repo": {"type": "string", "description": "Repo root directory (default: cwd)."},
            "path": {"type": "string", "description": "For 'scan': comma-sep dirs/files to scan."},
            "exclude": {"type": "string", "description": "Comma-sep gitignore-style patterns to exclude."},
            "background": {"type": "string", "description": "Business/requirement context."},
            "background_file": {"type": "string", "description": "Markdown file with business context."},
            "rule_file": {"type": "string", "description": "Custom rule.json path."},
            "preview": {"type": "boolean", "description": "Preview which files reviewed (no LLM call)."},
            "effort": {"type": "string", "description": "review only: effort preset low|medium|high (default medium; scan rejects it)."},
            "no_plan": {"type": "boolean", "description": "For 'scan': skip per-file PLAN_TASK pre-pass."},
            "concurrency": {"type": "integer", "description": "Max concurrent file reviews (default: 8)."},
            "timeout_mins": {"type": "integer", "description": "Concurrent task timeout in minutes (default: 15)."},
            "max_git_procs": {"type": "integer", "description": "Max concurrent git subprocesses (default: 16)."},
            "max_tokens": {"type": "integer", "description": "Per-group/per-file prompt token ceiling (0 = template default)."},
            "max_tools": {"type": "integer", "description": "Max tool-call rounds per subtask (0 = template default; min 50)."},
            "limit": {"type": "integer", "description": "session_list: max sessions (default: 20)."},
            "session_id": {"type": "string", "description": "Session ID for session_view/comments (or 'before' of session_compare)."},
            "before_id": {"type": "string", "description": "session_compare: the earlier session ID."},
            "after_id": {"type": "string", "description": "session_compare: the later session ID."},
            "addr": {"type": "string", "description": "viewer: listen address (default localhost:5483)."},
            "open_browser": {"type": "boolean", "description": "viewer: also open the system browser (default false)."},
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
        effort=args.get("effort", ""),
        no_plan=args.get("no_plan", False),
        concurrency=args.get("concurrency", 0),
        timeout_mins=args.get("timeout_mins", 0),
        max_git_procs=args.get("max_git_procs", 0),
        max_tokens=args.get("max_tokens", 0),
        max_tools=args.get("max_tools", 0),
        limit=args.get("limit", 0),
        session_id=args.get("session_id", ""),
        before_id=args.get("before_id", ""),
        after_id=args.get("after_id", ""),
        addr=args.get("addr", ""),
        open_browser=args.get("open_browser", False),
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
