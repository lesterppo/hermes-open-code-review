# AGENTS.md — AI agent instructions for hermes-open-code-review

This document tells AI coding agents (Hermes, Claude Code, Codex, Cursor,
OpenCode, and any skill-compatible agent) everything they need to install,
configure, and use the `ocr` tool in a Hermes Agent installation.

## What this repo provides

A Hermes Agent **native tool** that wraps the
[alibaba/open-code-review](https://github.com/alibaba/open-code-review) CLI
(`ocr` v1.12+). Three groups:

- **Delegation** (always available, no LLM needed on OCR side): `preview` +
  `rule` + `rules_check` actions for deterministic file selection and rule
  resolution. The Hermes agent performs the review using its own LLM.
  **Live-tested Jul/Aug 2026** against intentional bugs — found all issues
  correctly with severity classification.
- **Direct** (needs OCR LLM config): `review` and `scan` actions for full
  AI-powered review with line-level comments, fix suggestions, json or SARIF
  output, resume, and token budgets.
- **Utility**: `session_list`, `session_view`, `session_comments`,
  `session_compare` (new/persisting/resolved across two runs), `llm_test`,
  `llm_providers`, `viewer` / `viewer_stop` (session-history WebUI),
  `version`.

## Quick start (for an AI agent integrating this)

```bash
# 1. Install OCR CLI (v1.12.2 or newer)
npm install -g @alibaba-group/open-code-review

# 2. Clone and install (targets the local-tools plugin dir — survives
#    `hermes update`; no core files modified)
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh
```

The `ocr` tool registers via `registry.register(name="ocr", toolset="code_review", ...)`
and the plugin `~/.hermes/plugins/hermes_local_tools/__init__.py` re-registers
it through `ctx.register_tool(...)`, which auto-enables the `code_review`
toolset for all platforms (plugin toolsets default to enabled unless listed in
`_DEFAULT_OFF_TOOLSETS` or explicitly disabled via `hermes tools`). Restart
Hermes, then verify: `ocr(action='version')`.

If a stale cache hides the toolset: `hermes tools` (saves the plugin toolset
keys) or run `discover_plugins(force=True)` + `_persist_plugin_toolset_keys()`
from a Python REPL with `PYTHONPATH=~/.hermes/hermes-agent`.

## Delegation workflow (proven recipe — live-tested)

This is the recommended path. No OCR LLM config needed. The Hermes agent
handles the review using its own LLM; OCR provides deterministic engineering.

### Step 1: Preview — which files to review

```python
# Branch comparison
ocr(action='preview', from_ref='main', to_ref='feature-branch')

# Single commit
ocr(action='preview', commit='abc123')

# Workspace changes (staged + unstaged + untracked)
ocr(action='preview')
```

Response `data` contains:
- `mode`: workspace / range / commit
- `from` / `to` / `merge_base`: for constructing git commands
- `nf` / `tot`: reviewable / total file counts; `ins` / `del`: line counts
- `files`: `[{p, s, i, d}]` (path, status, insertions, deletions)

**Extract `merge_base`** from `data` for the git diff command in Step 3.

### Step 2: Get review rules

```python
ocr(action='rule', files=['path/to/file1.py', 'path/to/file2.py'])
```

- `data.groups`: compact group metadata `[{src, pat, fs, rule(truncated)}]`
- `out` (or `@` if > 9000 chars): the FULL rule text — read it to guide the
  review
- `ocr(action='rules_check', file='src/app.py')` → which single rule applies
  (File/Source/Pattern)

System rules cover Python (MD5, pickle, bare except, mutable defaults, SQL
injection, shell injection, exec/eval, timing attacks, identity comparisons,
resource leaks), JavaScript/TypeScript (XSS, prototype pollution, eval, unsafe
DOM, missing awaits), Java (NPE, thread safety, resource management, SQL
injection), Go (error handling, goroutine leaks, nil pointer, race
conditions), Rust (unsafe blocks, unwrap on Result/Option, deadlocks), C/C++
(buffer overflow, use-after-free, null dereference, format string), plus
Swift, R, Zig, Nim, Haskell, Elm, Jsonnet, Thrift, Cap'n Proto and more
(v1.9.x language additions).

### Step 3: Get diffs

Based on the mode from Step 1:

| Mode | Git command |
|------|-------------|
| range | `git diff <merge_base>..<to> -- <file>` |
| commit | `git show <commit> -- <file>` |
| workspace (tracked) | `git diff HEAD -- <file>` |
| workspace (untracked) | Read file directly with `read_file` (entire file is new) |

Run diffs from the repo root. Pass each file path individually for clean output.

### Step 4: Review each file

For each file in the preview:

1. Read its diff (Step 3)
2. Consult its rule group (Step 2) — match the categories to what you see
3. If the diff lacks surrounding context, read the full file
4. Review thoroughly — **prioritize security and correctness over style**

OCR's core rule: *"Favor precision over recall: only raise an issue when you
are confident it is a real defect, and stay silent when the surrounding context
is unclear — a false alarm costs more reviewer trust than a missed minor
issue."*

### Step 5: Classify and report

Classify every finding by severity:

| Severity | Criteria | Examples |
|----------|----------|---------|
| **Critical** | Security vulns, data loss, crashes | SQL injection, `exec()`, shell=True, pickle, hardcoded secrets |
| **High** | Real bugs, dead code, perf regressions | Mutable defaults, bare except, unused imports, off-by-one |
| **Medium** | Style, minor duplication | Missing type hints, function-level re-imports |
| **Low** | Nits, personal preference | **Discard silently** per OCR rule |

Report in structured markdown:

```markdown
## Code Review Results

**Files reviewed**: N
**Issues found**: X critical / Y high / Z medium

### Critical

- **`path/file:LINE`** — Brief description
  > Recommendation: exact fix with code snippet

### High

- **`path/file:LINE`** — Description

### Medium

- **`path/file:LINE`** — Description
```

## Live test results (Jul/Aug 2026)

Tested against `ocr_tool.py` + a scratch repo with 36 lines of intentionally
buggy code appended. OCR correctly identified the files and matched rules;
DeepSeek V4 Flash review/scan found all intentional issues:

| Issue | Severity | Correct |
|-------|----------|---------|
| `exec()`/command injection on arbitrary input | Critical | ✓ |
| MD5 for password hashing | Critical | ✓ |
| SQL injection via f-string | Critical | ✓ |
| Shell injection via shell=True | Critical | ✓ |
| Pickle deserialization of untrusted data | Critical | ✓ |
| Bare except swallows errors | High | ✓ |
| Mutable default argument | High | ✓ |
| Unused imports / dead code | High | ✓ |
| String building in loop (`+=`) | Low/Medium | ✓ |
| `== None` style | Low | ✓ |

Full scan of the 2-file repo: 12 comments (3 critical, 2 high, 2 medium, 5
low), 2m28s, 43k tokens — budget caps (`--max-tokens-budget`) and SARIF output
verified live.

## Direct mode

```python
# Diff-based review
ocr(action='review', from_ref='main', to_ref='feature-branch')

# Review effort preset (review-only in v1.12.x)
ocr(action='review', from_ref='main', to_ref='feature-branch', effort='high')

# Prompt/tool ceilings
ocr(action='scan', path='internal/agent', max_tokens=6000, max_tools=60)

# Full-file scan
ocr(action='scan', path='internal/agent')

# SARIF 2.1.0 (saved to disk; not allowed with preview=True)
ocr(action='review', from_ref='main', to_ref='feature-branch', format='sarif')

# Dry-run (no LLM, works without config)
ocr(action='review', preview=True)

# Resume + token budget + no post-filter
ocr(action='review', from_ref='main', to_ref='feature-branch',
    resume='<session-id>', max_tokens_budget=200000, no_filter=True)

# Session forensics
ocr(action='session_list', limit=20)
ocr(action='session_view', session_id='<id>')
ocr(action='session_comments', session_id='<id>', severity='critical,high',
    category='bug,security')

# Did the fixes land? new / persisting / resolved between two runs
ocr(action='session_compare', before_id='<older>', after_id='<newer>')

# Session-history WebUI (detached; returns the URL) + stop
ocr(action='viewer')
ocr(action='viewer_stop')
```

Direct mode returns compact JSON:
- `nf`: files reviewed, `nc`: comments, `toks`: {i,o,t,c} token counts
- `sid`: session ID, `llm`: {provider, model}, `retries`, `tc`: tool calls
- `budget`: true when a token budget stopped dispatch (plus `warns`)
- `cmts`: `[{f, l, s, c, msg, fix, ex}]` (file, line range, severity,
  category, message, fix code, existing code)
- Full output saved to `~/.hermes/ocr_output/` (`@` key)

Requires OCR LLM configured:
```bash
ocr config set provider deepseek
ocr config set model deepseek-v4-flash
ocr config set providers.deepseek.api_key "$DEEPSEEK_API_KEY"
# or interactive:
ocr config provider
ocr config model
ocr llm test   # verify connectivity
```

## Action reference

| Action | LLM needed? | Purpose |
|--------|-------------|---------|
| `preview` | No | File selection + mode/ref metadata (JSON) |
| `rule` | No | Matched review rules grouped by content |
| `rules_check` | No | Which rule applies to one file path |
| `review` | Yes (no for `preview=True`) | Diff-based AI review (json/sarif) |
| `scan` | Yes (no for `preview=True`) | Full-file AI scan (json/sarif) |
| `session_list` | No | List saved review sessions (compact) |
| `session_view` | No | Inspect a session by ID |
| `session_comments` | No | Extract comments from a session (filters) |
| `session_compare` | No | Diff two sessions: new / persisting / resolved |
| `viewer` | No | Start the session-history WebUI (detached, returns URL) |
| `viewer_stop` | No | Stop a viewer started by this tool |
| `llm_test` | No | Live OCR LLM connectivity check |
| `llm_providers` | No | List built-in LLM providers |
| `version` | No | Show OCR CLI version |

## Output format (compact, token-efficient)

All responses use short keys:

| Key | Meaning |
|-----|---------|
| `ok` | Success (boolean) |
| `e` | Error message |
| `h` | Hint (how to fix) |
| `out` | Raw stdout (truncated, full at `@`) |
| `err` | stderr / failure reason |
| `@` | Saved file path for full output |
| `code` | Exit code |
| `data` | Compact structured result (mode/files/groups/comments...) |
| `n` | Count (sessions, comments, providers) |
| `sid` | Session ID |
| `_action` | Which action ran |

## Configuring LLM for direct mode

OCR supports OpenAI and Anthropic-compatible endpoints; 25+ built-in
providers (deepseek, anthropic, openai, xai, kimi-global, siliconflow,
mistral, novita, dashscope, minimax, z-ai, ... — `ocr(action='llm_providers')`):

```bash
# Environment variables (CI)
export OCR_LLM_URL=https://api.openai.com/v1/chat/completions
export OCR_LLM_TOKEN=sk-...
export OCR_LLM_MODEL=gpt-4o
export OCR_USE_OPENAI=true

# Direct config
ocr config set provider deepseek
ocr config set model deepseek-v4-flash
ocr config set providers.deepseek.api_key "$KEY"
```

Custom providers: `ocr config set provider my-gateway` +
`ocr config set custom_providers.my-gateway.url ...` +
`ocr config set custom_providers.my-gateway.protocol openai|anthropic`.

## v3 update (OCR CLI v1.12.2) — live-tested 2026-09-15

Upstream moved v1.9.6 → v1.12.2 (~3 weeks). What changed on our side:

| Area | v2 (CLI 1.9.6) | v3 (CLI 1.12.2) |
|------|----------------|-----------------|
| `review` flags | resume, budget, no-filter | + `--effort low\|medium\|high`, `--max-tokens`, `--max-tools`, `--max-git-procs`, `-o/--output` |
| `scan` flags | batch, no-plan/summary/dedup | + `--max-tokens`, `--max-tools`, `--max-git-procs`, `-o/--output` |
| Session diff | — | `session_compare` action (`ocr session compare`) |
| Session WebUI | — | `viewer` / `viewer_stop` actions (`ocr viewer`) |
| Output hygiene | raw stdout | `--color never` forced (auto-retry on old CLIs); `[ocr] Results written to …` chatter filtered out of `err` |
| Token economy | thinking kept when parsing raw | `thinking` (model CoT, tens of KB) dropped by the compactor |

Traps verified against the real CLI (do not "fix" these blindly):

1. **`--effort` is review-only.** `ocr scan` rejects it with
   `Error: unknown flag: --effort`. The tool returns a clear error instead of
   silently dropping the parameter.
2. **Budget-stop now exits 0** when partial results are published (v1.12 help:
   "review exits 0; it exits non-zero only if every selected item failed").
   The old skill note claiming a non-zero budget exit is outdated — the tool
   reads `summary.budget_exceeded` + `warnings[]` regardless of exit code and
   surfaces them as `data.budget` / `data.warns`.
3. **`-o/--output` prints a status line to stderr** (`[ocr] Results written to
   <path>`). The tool routes review/scan JSON through its own `-o` file and
   re-reads it, then strips that line from `err` so it is not mistaken for a
   failure.
4. **`--color never` must be last-safe**: it is a global flag, accepted on
   every subcommand; the runner retries without it if an older CLI rejects it.
5. **Flag audit**: `scripts/flag_audit.py`-style check (see Live test below)
   cross-checks every flag the tool can emit against `ocr <cmd> --help` for the
   installed CLI — run it after every CLI upgrade.

### Live test — v3 against CLI v1.12.2 (32/32 actions, 0 failures)

Fixture: 2-commit git repo (`init` clean → `add buggy hash_pw` with shell
injection, MD5 password hashing, bare except, mutable default). Every action
exercised end-to-end against the real CLI + DeepSeek V4 Flash:

| Action | Result |
|--------|--------|
| `version` | `open-code-review v1.12.2` |
| `llm_providers` / `llm_test` | 2201B list / live 1.4s round-trip OK |
| `preview` (range / commit / workspace) | merge_base correct; 1 reviewable file each |
| `rule` / `rules_check` | rules returned (9020B) / source+pattern resolved |
| `review` (range, effort=low) | **4 findings** — shell=True injection (critical), bare except (high), MD5 password hashing (critical), unused mutable default (medium); 11.6s |
| `scan` (file) | 4 findings, 20.1s, tokens reported (i/o/t/c) |
| `scan --format sarif` | valid SARIF **2.1.0**, 1 run, 4 results, 8030B |
| `scan preview=True` | dry-run, no LLM |
| `review --format text` | text report OK |
| budget stop (`max_tokens_budget=300`) | `data.budget=true` + `warns[0].m="stopped in batch #0: used 0 tokens + next-file estimate exceeds budget 300"` |
| `session_list/view/comments` | 5+ sessions; filters (`severity=critical,high`) honoured |
| `session_compare` (review vs scan) | `persisting: 4`, `new: 0`, `resolved: 0` — real bucketing |
| `viewer` / `viewer_stop` | detached, HTTP 200 on `http://localhost:5483`, idempotent re-start, clean stop |
| error paths (8) | bad repo, bad format, resume-without-range, missing session id, rule without files, `effort` on scan, compare with one id, unknown action — all clear messages |

Payload sizes confirm the token economy: review 2.4KB, scan 3.7KB, preview
0.2-0.4KB, session_compare 2.8KB, schema 8.3KB.

### Dogfood: scanned the tool with itself (CLI v1.12.2, DeepSeek V4 Flash)

`ocr scan --path tools/ocr_tool.py` (102s, 130k tokens, 5 findings) — the tool
reviewed its own source. Four were real and are fixed in v3.0.1:

| Finding | Sev | Fix |
|---------|-----|-----|
| `-o` set but CLI wrote no report file → `out` popped, diagnostics lost | high | `_apply_output_file()` keeps meaningful stdout + sets `note` |
| viewer log handle `lf` never closed → fd leak per start | medium | `with open(...)` around `Popen` |
| `kw.get("files")` iterated char-by-char when a bare string is passed | medium | `_file_list()` accepts list / CSV string / single path |
| handler exception escapes as a traceback instead of JSON | low | dispatch wraps handlers → `{ok:false, e:"internal error: …"}` |
| `viewer_stop` signals a state-file PID without verifying it is ours | low | `_is_ocr_viewer()` reads `/proc/<pid>/cmdline` and refuses (clears stale state) |

Behaviour verified after the fixes: review/scan unchanged (4 findings each, no
stderr noise), bad `resume` returns a clear error + `note`, viewer start/HTTP
200/stop/idempotent-stop all good.

### Regression tests

`tests/test_ocr_tool.py` — stdlib runner (`python3 tests/test_ocr_tool.py`,
also pytest-compatible), 11 tests / all pass, no LLM or network:

- argument construction for review/scan/preview/rule/session_compare
  (including "scan must NOT receive --effort")
- validation errors (bad effort/batch/format, sarif+preview, missing ids)
- garbage inputs never crash (`concurrency="abc"`, `max_tokens=-5`, huge limit)
- `_file_list` coercion (`"a.py"` stays one path)
- compactors drop `thinking`; schema enum == handler set; schema < 6k chars

## Development

- Canonical tool: `tools/ocr_tool.py` (v3, ~870 lines, Python stdlib only)
- Runtime copy: `~/.hermes/plugins/hermes_local_tools/ocr_tool.py` — keep in
  sync (`cp tools/ocr_tool.py ~/.hermes/plugins/hermes_local_tools/ocr_tool.py`)
- Skill: `skills/ocr-code-review/SKILL.md` → also installed to
  `~/.hermes/skills/devops/ocr-code-review/`
- **Plugin registration pattern (must be preserved):** the plugin
  `~/.hermes/plugins/hermes_local_tools/__init__.py` imports each tool module
  in a per-module `try/except` loop (`_TOOL_MODULES`) so one broken module
  (syntax error, missing dep) degrades to "that tool missing" instead of
  killing the whole plugin and its other 12 tools. It then re-registers each
  tool via `ctx.register_tool(name, toolset, ...)` so the toolset
  (`code_review`) auto-enables. NEVER revert to a plain `from . import (...)` 
  block. Also: don't import the plugin package directly in a process that
  calls `discover_plugins()` — the module-level `registry.register()` becomes
  GLOBAL in that case and the plugin's scoped re-registration is correctly
  rejected by the shadow guard (test artifact, not a bug).
- Test harness: standalone Python + `PYTHONPATH=~/.hermes/hermes-agent`, import
  `ocr_tool`, exercise `ocr_tool(action=...)` against a scratch git repo with
  intentional bugs (see Live test results)
- Upstream CLI moves fast (v1.8 → v1.9.6 in ~3 weeks) — re-check `ocr --help`
  per subcommand when updating
