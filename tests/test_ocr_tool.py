#!/usr/bin/env python3
"""Unit tests for tools/ocr_tool.py — no LLM calls, no network.

Run:  python3 tests/test_ocr_tool.py          (stdlib runner)
      pytest tests/test_ocr_tool.py           (if pytest is installed)

Covers: argument construction for every action, parameter validation,
`_file_list` coercion, compactors, and schema/handler consistency.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "ocr_tool.py"
spec = importlib.util.spec_from_file_location("ocr_tool_under_test", TOOL)
T = importlib.util.module_from_spec(spec)
sys.modules["ocr_tool_under_test"] = T
spec.loader.exec_module(T)

CAPTURED: list[dict] = []
ORIG_RUN = T._run


def _stub_run(args, timeout=60, cwd=None, max_inline=9000, color=True):
    CAPTURED.append({"args": [str(a) for a in args], "cwd": cwd,
                     "timeout": timeout, "max_inline": max_inline})
    return {"ok": True, "code": 0,
            "out": json.dumps({"status": "success", "summary": {},
                               "comments": [], "session_id": "sid"})}


def setup_module(module=None):
    T._run = _stub_run
    T._read_output_file = lambda p: json.dumps(
        {"status": "success", "summary": {"files_reviewed": 1, "comments": 1},
         "comments": [{"path": "x.py", "content": "c", "category": "bug",
                       "severity": "high", "start_line": 1, "end_line": 1}],
         "session_id": "sid"})
    T._ocr_llm_configured = lambda: True


def _args(**kw):
    CAPTURED.clear()
    setup_module()
    result = json.loads(T.ocr_tool(**kw))
    return (CAPTURED[0]["args"] if CAPTURED else []), result


# ── argument construction ─────────────────────────────────────────────────────

def test_review_flags():
    a, r = _args(action="review", from_ref="main", to_ref="dev", effort="high",
                 max_tokens=9000, max_tools=60, max_git_procs=4, concurrency=3,
                 timeout_mins=20, max_tokens_budget=200000, no_filter=True,
                 exclude="**/gen/*", repo="/tmp/x")
    assert a[:4] == ["review", "--audience", "agent", "--format"], a
    assert a[a.index("--effort") + 1] == "high"
    assert a[a.index("--max-tokens") + 1] == "9000"
    assert a[a.index("--max-tools") + 1] == "60"
    assert a[a.index("--max-git-procs") + 1] == "4"
    assert "-o" in a                      # report routed through our own file
    assert r["data"]["nc"] == 1


def test_scan_flags():
    a, _ = _args(action="scan", path="a.py", batch="by-language",
                 rule_file="/tmp/r.json", no_summary=True, no_dedup=True,
                 max_tokens=4000, max_git_procs=2, repo="/tmp/x")
    assert a[:4] == ["scan", "--audience", "agent", "--format"]
    assert "--effort" not in a            # upstream: scan has no --effort
    assert a[a.index("--batch") + 1] == "by-language"
    assert "--no-summary" in a and "--no-dedup" in a and "--no-plan" not in a
    assert "--rule" in a


def test_preview_and_rule_flags():
    a, _ = _args(action="preview", from_ref="main", to_ref="dev",
                 max_git_procs=8, rule_file="/tmp/r.json", repo="/tmp/x")
    assert a[:3] == ["delegate", "preview", "--format"]
    assert "--max-git-procs" in a and "--rule" in a
    assert CAPTURED[0]["max_inline"] == 8_000_000

    a, _ = _args(action="rule", files=["a.py"], exclude="**/t/*", commit="HEAD",
                 repo="/tmp/x")
    assert a[:3] == ["delegate", "rule", "--format"] and "a.py" in a
    assert "--exclude" in a and "-c" in a


def test_session_compare_flags():
    a, _ = _args(action="session_compare", before_id="A", after_id="B",
                 repo="/tmp/x")
    assert a[:2] == ["session", "compare"] and "A" in a and "B" in a
    assert "--json" in a


def test_preview_uses_merge_base_passthrough():
    setup_module()
    T._run = lambda *a, **k: {"ok": True, "code": 0, "out": json.dumps(
        {"mode": "range", "repository": "/r", "from": "a", "to": "b",
         "merge_base": "BASE", "reviewable_count": 1, "total_files": 1,
         "total_insertions": 3, "total_deletions": 1, "excluded_files": [],
         "reviewable_files": [{"path": "a.py", "status": "modified",
                               "insertions": 3, "deletions": 1}]})}
    r = json.loads(T.ocr_tool(action="preview", repo="/tmp/x"))
    assert r["data"]["merge_base"] == "BASE"
    assert r["data"]["files"][0]["p"] == "a.py"


# ── validation ────────────────────────────────────────────────────────────────

def test_validation_errors():
    for kw, needle in [
        (dict(action="review", effort="turbo"), "unsupported effort"),
        (dict(action="scan", effort="low"), "review-only"),
        (dict(action="scan", batch="bad"), "unsupported batch"),
        (dict(action="review", format="sarif", preview=True), "sarif"),
        (dict(action="scan", format="xml"), "unsupported format"),
        (dict(action="session_compare", before_id="a"), "required"),
        (dict(action="session_comments"), "session_id required"),
        (dict(action="rule"), "files required"),
        (dict(action="zzz"), "unknown action"),
    ]:
        setup_module()
        r = json.loads(T.ocr_tool(**kw))
        assert needle.lower() in json.dumps(r).lower(), (kw, r)

    # repo validation needs the REAL runner (the stub bypasses the isdir check)
    T._run = ORIG_RUN
    r = json.loads(T.ocr_tool(action="preview", repo="/nonexistent-xyz"))
    assert "not found" in json.dumps(r).lower(), r
    setup_module()
    T._run = _stub_run


def test_no_crash_on_garbage_inputs():
    for kw in [dict(action="review", concurrency="abc", repo="/tmp/x"),
               dict(action="scan", max_tokens=-5, repo="/tmp/x"),
               dict(action="session_list", limit="x", repo="/tmp/x"),
               dict(action="session_list", limit="9" * 40, repo="/tmp/x")]:
        setup_module()
        r = json.loads(T.ocr_tool(**kw))
        assert isinstance(r, dict)


# ── file list coercion ────────────────────────────────────────────────────────

def test_file_list():
    assert T._file_list(["a.py", "b.py"]) == ["a.py", "b.py"]
    assert T._file_list("a.py") == ["a.py"]          # NOT ['a','.','p','y']
    assert T._file_list("a.py, b.py") == ["a.py", "b.py"]
    assert T._file_list(["a.py", "b.py, c.py"]) == ["a.py", "b.py", "c.py"]
    assert T._file_list("") == [] and T._file_list(None) == []
    assert T._file_list([]) == []


# ── compactors ────────────────────────────────────────────────────────────────

def test_compactors_drop_thinking():
    c = T._compact_comment({"path": "a.py", "start_line": 1, "end_line": 2,
                            "severity": "critical", "category": "security",
                            "content": "boom", "suggestion_code": "fix()",
                            "existing_code": "bad()", "thinking": "x" * 5000})
    assert "thinking" not in c and c["s"] == "critical" and c["l"] == [1, 2]

    d = T._compact_compare({"before": {"session_id": "A", "review_mode": "range"},
                            "after": {"session_id": "B", "review_mode": "full_scan"},
                            "new": [{"path": "a.py", "content": "c",
                                     "start_line": 1, "end_line": 1,
                                     "severity": "high", "category": "bug",
                                     "thinking": "y" * 4000}],
                            "persisting": [], "resolved": []}, "A", "B")
    assert d["new"]["n"] == 1 and d["persisting"]["n"] == 0
    assert "thinking" not in json.dumps(d)


# ── schema / dispatch integrity ───────────────────────────────────────────────

def test_schema_matches_handlers():
    props = T.OCR_SCHEMA["parameters"]["properties"]
    enum = props["action"]["enum"]
    assert set(enum) == set(T._HANDLERS), set(T._HANDLERS) ^ set(enum)
    for p in ("effort", "max_tokens", "max_tools", "max_git_procs", "before_id",
              "after_id", "addr", "open_browser", "files", "session_id"):
        assert p in props, p
    assert len(json.dumps(T.OCR_SCHEMA)) < 6000   # token budget guard


def test_empty_action_lists_options():
    r = json.loads(T.ocr_tool(action=""))
    assert "preview" in r["actions"] and len(r["actions"]) == len(T._HANDLERS)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:  # noqa: PERF203
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
