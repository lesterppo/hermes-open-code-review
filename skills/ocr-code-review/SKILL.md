---
name: ocr-code-review
description: Use when user asks for AI code review via OCR CLI.
license: Apache-2.0
compatibility: Requires `ocr` CLI v1.8+ installed. Uses native Hermes `ocr` tool.
metadata:
  author: Peter/lesterppo
  homepage: https://github.com/lesterppo/hermes-open-code-review
  version: "1.1.0"
---

# OCR Code Review (Hermes-native)

AI-powered code review using [alibaba/open-code-review](https://github.com/alibaba/open-code-review)
(`ocr` v1.8+), wrapped as a native Hermes `ocr` tool. Two modes: delegation (no
OCR LLM needed, always works) and direct (full OCR pipeline, needs LLM config).

## Two modes

### Delegation mode (recommended — no OCR LLM needed)

OCR handles deterministic engineering: file selection + rule resolution.
**You** (Hermes agent) perform the review using your own LLM. Always available
as long as `ocr` binary is on PATH.

### Direct mode (needs OCR LLM configured)

`ocr(action='review')` / `ocr(action='scan')` runs OCR's full pipeline with its
own LLM. Gated on `ocr llm test` passing. Check first with `ocr(action='llm_test')`.

## Delegation workflow (proven — live-tested Jul 2026)

### Step 1: Preview — which files?

```
ocr(action='preview', from_ref='main', to_ref='feature-branch')
ocr(action='preview', commit='abc123')
ocr(action='preview')   # workspace (staged + unstaged + untracked)
```

Response keys:
- `out`: human-readable file list with mode, refs, merge_base, insertions/deletions
- `_action`: "preview" (confirms delegation mode used)

Extract `merge_base` from `out` for the git diff command in Step 3.

### Step 2: Get rules

```
ocr(action='rule', files=['path/to/file1.py', 'path/to/file2.py'])
```

Returns rules grouped by content — files sharing identical rules appear under
one group (OCR deduplicates). System rules cover Python, JS/TS, Java, Go, Rust,
etc. Output can be 8K+ chars for comprehensive rulesets.

### Step 3: Get diffs

Based on mode from Step 1 preview:

| Mode | Git command |
|------|-------------|
| range | `git diff <merge_base>..<to> -- <file>` |
| commit | `git show <commit> -- <file>` |
| workspace (tracked) | `git diff HEAD -- <file>` |
| workspace (untracked) | `read_file <file>` (entire file is new) |

### Step 4: Review each file

For each file in the preview:
1. Read its diff (Step 3)
2. Read its rule group (Step 2) — focus on the rule categories
3. If the diff lacks context, read the full file for surrounding code
4. Conduct a thorough review — **prioritize security and correctness over
   style**. OCR's rules say: "Favor precision over recall: only raise an issue
   when you are confident it is a real defect."

### Step 5: Classify and report

Classify findings by severity per OCR's own rubric:

- **Critical**: Security vulnerabilities (exec, SQL injection, shell injection,
  pickle deserialization, hardcoded secrets), data loss risks
- **High**: Bugs (mutable defaults, bare except, off-by-one, None-handling),
  dead code, performance regressions on hot paths
- **Medium**: Style issues, missing type hints, minor duplication, unused
  imports, function-level re-imports when module-level already exists
- **Low**: Nits, personal preference — **discard silently** per OCR rule

Report format:

```markdown
## Code Review Results

**Files reviewed**: N
**Issues found**: X critical / Y high / Z medium

### Critical

- **`path/file:LINE`** — Brief description
  > Recommendation: exact fix

### High

- **`path/file:LINE`** — Brief description

### Medium

- **`path/file:LINE`** — Brief description
```

## Direct mode

```
ocr(action='review', from_ref='main', to_ref='feature-branch')
ocr(action='scan', path='internal/agent')
```

Use `preview=True` to dry-run (no LLM call) — works even without OCR LLM
configured. For real review, OCR output includes `nc` (comment count), `nf`
(files reviewed), `sid` (session ID), and compact per-comment dicts with `f`
(file), `l` (line range), `s` (severity), `c` (category), `msg`, `fix`.

## Utility actions

| Action | Purpose |
|--------|---------|
| `ocr(action='llm_test')` | Check if OCR LLM is configured |
| `ocr(action='version')` | Show OCR version |
| `ocr(action='session_list')` | List saved review sessions |
| `ocr(action='session_view', session_id='...')` | Inspect a session |

## All actions reference

```
preview          — delegation: which files to review
rule             — delegation: matched review rules
review           — direct: diff-based AI review
scan             — direct: full-file AI scan
session_list     — list review sessions
session_view     — inspect a session
llm_test         — check LLM connectivity
version          — show OCR version
```

## Repository

- Tool source: https://github.com/lesterppo/hermes-open-code-review
- Upstream OCR: https://github.com/alibaba/open-code-review
- OCR docs: https://open-codereview.ai/docs
- Hermes skill: `ocr-code-review`
- Installed at: `~/.hermes/hermes-agent/tools/ocr_tool.py`

## Pitfalls (from live testing)

- **Delegation mode always available** — no OCR LLM config needed for preview+rule
- **Direct mode gated** — `review`/`scan` fail with hint if LLM not configured
- **`preview=True` bypasses LLM gate** — works for both `review` and `scan`
- **Working directory matters** — `ocr` operates on Git repo at cwd or `repo=`
- **Untracked files in workspace mode** — included in preview; read directly
- **Large rule output** — system rules are ~8KB comprehensive; pass only files
  you're reviewing
- **Preview output includes merge_base** — extract it for the git diff command
- **ANSI escape codes in preview output** — `out` may contain terminal color
  codes, ignore them
- **Rule output groups by content** — files with identical rules share one group
- **Save full results to disk** — `@` key for file path when output exceeds
  9000 chars
