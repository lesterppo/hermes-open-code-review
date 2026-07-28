# AGENTS.md — AI agent instructions for hermes-open-code-review

This document tells AI coding agents (Hermes, Claude Code, Codex, Cursor,
OpenCode, and any skill-compatible agent) everything they need to install,
configure, and use the `ocr` tool in a Hermes Agent installation.

## What this repo provides

A Hermes Agent **native tool** that wraps the
[alibaba/open-code-review](https://github.com/alibaba/open-code-review) CLI
(`ocr` v1.8+). Two modes:

- **Delegation** (always available, no LLM needed on OCR side): `preview` +
  `rule` actions for deterministic file selection and rule resolution. The
  Hermes agent performs the review using its own LLM. **Live-tested Jul 2026**
  against intentional bugs — found all 9 issues correctly.
- **Direct** (needs OCR LLM config): `review` and `scan` actions for full
  AI-powered review with line-level comments and fix suggestions.

## Quick start (for an AI agent integrating this)

```bash
# 1. Install OCR CLI
npm install -g @alibaba-group/open-code-review

# 2. Clone and install
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh ~/.hermes/hermes-agent
```

Then wire `"ocr"` into `_HERMES_CORE_TOOLS` in `toolsets.py`:

```python
_HERMES_CORE_TOOLS = [
    ...
    "ocr",   # AI code review via alibaba/open-code-review
]
```

And add the toolset:

```python
TOOLSETS = {
    ...
    "code_review": {
        "description": "AI code review via alibaba/open-code-review (OCR).",
        "tools": ["ocr"],
        "includes": []
    },
    ...
}
```

Restart Hermes. The tool is gated by `check_fn` — it only appears when the
`ocr` binary is on PATH. Zero footprint otherwise.

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

Response contains:
- `mode`: workspace / range / commit
- `from` / `to` / `merge_base`: for constructing git commands
- `total_insertions` / `total_deletions`: diff size
- List of files with status (`[M]` / `[A]` / `[D]`), +N/-N line counts

**Extract `merge_base`** from the output for the git diff command in Step 3.

### Step 2: Get review rules

```python
ocr(action='rule', files=['path/to/file1.py', 'path/to/file2.py'])
```

Rules are grouped by content — files sharing identical rules appear under one
group (OCR deduplicates to save tokens). System rules cover:

- Python: MD5, pickle, bare except, mutable defaults, SQL injection, shell injection, exec/eval, timing attacks, identity comparisons, resource leaks
- JavaScript/TypeScript: XSS, prototype pollution, eval, unsafe DOM, missing awaits
- Java: NPE, thread safety, resource management, SQL injection
- Go: error handling, goroutine leaks, nil pointer, race conditions
- Rust: unsafe blocks, unwrap on Result/Option, deadlocks
- C/C++: buffer overflow, use-after-free, null dereference, format string

Rules output can be 8KB+ for comprehensive rulesets. Pass only the files you're
reviewing to keep it manageable.

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

## Live test results (Jul 2026)

Tested against `ocr_tool.py` with 36 lines of intentionally buggy code
appended. OCR correctly identified the file and matched Python rules. Hermes
agent found all 9 issues:

| Issue | Severity | Correct |
|-------|----------|---------|
| `exec()` on arbitrary input | Critical | ✓ |
| MD5 for password hashing | Critical | ✓ |
| SQL injection via f-string | Critical | ✓ |
| Shell injection via shell=True | Critical | ✓ |
| Bare except swallows errors | Critical | ✓ |
| Mutable default argument | High | ✓ |
| Unused imports | High | ✓ |
| Duplicate function-level imports | Medium | ✓ |
| Dead code (unreachable functions) | Medium | ✓ |

## Direct mode

```python
# Diff-based review
ocr(action='review', from_ref='main', to_ref='feature-branch')

# Full-file scan
ocr(action='scan', path='internal/agent')

# Dry-run (no LLM, works without config)
ocr(action='review', preview=True)
```

Direct mode returns compact JSON:
- `nc`: comment count, `nf`: files reviewed, `sid`: session ID
- `comments`: array of `{f, l, s, c, msg, fix}` (file, line range, severity, category, message, fix code)
- Full output saved to `~/.hermes/ocr_output/` (`@` key)

Requires OCR LLM configured:
```bash
ocr config provider    # interactive provider setup
ocr config model       # pick a model
ocr llm test           # verify connectivity
```

## Action reference

| Action | LLM needed? | Purpose |
|--------|-------------|---------|
| `preview` | No | File selection + mode/ref metadata |
| `rule` | No | Matched review rules grouped by content |
| `review` | Yes (no for `preview=True`) | Diff-based AI review |
| `scan` | Yes (no for `preview=True`) | Full-file AI scan |
| `session_list` | No | List saved review sessions |
| `session_view` | No | Inspect a session by ID |
| `llm_test` | No | Check OCR LLM connectivity |
| `version` | No | Show OCR CLI version |

## Output format (compact, token-efficient)

All responses use short keys:

| Key | Meaning |
|-----|---------|
| `ok` | Success (boolean) |
| `e` | Error message |
| `h` | Hint (how to fix) |
| `out` | Raw stdout (truncated, full at `@`) |
| `err` | stderr |
| `@` | Saved file path for full output |
| `code` | Exit code |
| `nc` | Comment count (direct mode) |
| `nf` | Files reviewed (direct mode) |
| `sid` | Session ID (direct mode) |
| `_action` | Which action ran |

## Configuring LLM for direct mode

OCR supports OpenAI and Anthropic-compatible endpoints:

```bash
# Interactive (recommended)
ocr config provider
ocr config model

# Environment variables (CI)
export OCR_LLM_URL=https://api.anthropic.com/v1/messages
export OCR_LLM_TOKEN=sk-...
export OCR_LLM_MODEL=claude-opus-4-6
export OCR_USE_ANTHROPIC=true

# Direct config
ocr config set llm.url https://api.openai.com/v1/chat/completions
ocr config set llm.auth_token sk-...
ocr config set llm.model gpt-4o
```

## Files

```
tools/ocr_tool.py              # Native tool (registry.register + dispatch)
skills/ocr-code-review/SKILL.md  # Hermes skill (auto-loaded)
install.sh                     # Idempotent installer
```

## Privacy rules

- No secrets, API keys, or personal paths in any committed file
- All paths use `get_hermes_home()`, never `/home/*`
- Repo is safe to fork and publicize
