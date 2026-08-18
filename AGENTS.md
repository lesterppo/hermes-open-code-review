# AGENTS.md — AI agent instructions for hermes-open-code-review

This document tells AI coding agents (Hermes, Claude Code, Codex, Cursor,
OpenCode, and any skill-compatible agent) everything they need to install,
configure, and use the `ocr` tool in a Hermes Agent installation.

## What this repo provides

A Hermes Agent **native tool** that wraps the
[alibaba/open-code-review](https://github.com/alibaba/open-code-review) CLI
(`ocr` v1.9+). Three groups:

- **Delegation** (always available, no LLM needed on OCR side): `preview` +
  `rule` + `rules_check` actions for deterministic file selection and rule
  resolution. The Hermes agent performs the review using its own LLM.
  **Live-tested Jul/Aug 2026** against intentional bugs — found all issues
  correctly with severity classification.
- **Direct** (needs OCR LLM config): `review` and `scan` actions for full
  AI-powered review with line-level comments, fix suggestions, json or SARIF
  output, resume, and token budgets.
- **Utility**: `session_list`, `session_view`, `session_comments`,
  `llm_test`, `llm_providers`, `version`.

## Quick start (for an AI agent integrating this)

```bash
# 1. Install OCR CLI
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

## Development

- Canonical tool: `tools/ocr_tool.py` (v2, ~690 lines, Python stdlib only)
- Runtime copy: `~/.hermes/plugins/hermes_local_tools/ocr_tool.py` — keep in
  sync (`cp tools/ocr_tool.py ~/.hermes/plugins/hermes_local_tools/ocr_tool.py`)
- Skill: `skills/ocr-code-review/SKILL.md` → also installed to
  `~/.hermes/skills/devops/ocr-code-review/`
- Test harness: standalone Python + `PYTHONPATH=~/.hermes/hermes-agent`, import
  `ocr_tool`, exercise `ocr_tool(action=...)` against a scratch git repo with
  intentional bugs (see Live test results)
- Upstream CLI moves fast (v1.8 → v1.9.6 in ~3 weeks) — re-check `ocr --help`
  per subcommand when updating
