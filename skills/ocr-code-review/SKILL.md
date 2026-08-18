---
name: ocr-code-review
description: Use when user asks for AI code review via OCR CLI.
license: Apache-2.0
compatibility: Requires `ocr` CLI v1.9+ installed. Uses native Hermes `ocr` tool.
metadata:
  author: Peter/lesterppo
  homepage: https://github.com/lesterppo/hermes-open-code-review
  version: "2.0.0"
---

# OCR Code Review (Hermes-native)

AI-powered code review using [alibaba/open-code-review](https://github.com/alibaba/open-code-review)
(`ocr` v1.9+), wrapped as a native Hermes `ocr` tool (toolset `code_review`).
Three mode groups: delegation (no OCR LLM needed, always works), direct (full
OCR pipeline, needs LLM config), and utility.

## Two modes

### Delegation mode (recommended — no OCR LLM needed)

OCR handles deterministic engineering: file selection + rule resolution.
**You** (Hermes agent) perform the review using your own LLM. Always available
as long as `ocr` binary is on PATH.

### Direct mode (needs OCR LLM configured)

`ocr(action='review')` / `ocr(action='scan')` runs OCR's full pipeline with its
own LLM. Gated on OCR config presence (fast, no LLM call). Check live with
`ocr(action='llm_test')`. Configure with `ocr config set provider/model` or
`ocr config provider` (interactive).

## Delegation workflow (proven — live-tested Jul/Aug 2026)

### Step 1: Preview — which files?

```
ocr(action='preview', from_ref='main', to_ref='feature-branch')
ocr(action='preview', commit='abc123')
ocr(action='preview')   # workspace (staged + unstaged + untracked)
```

Response `data` keys:
- `mode`: workspace / range / commit
- `from` / `to` / `merge_base`: for constructing git commands
- `nf`/`tot`: reviewable / total file counts; `ins`/`del`: line counts
- `files`: `[{p, s, i, d}]` (path, status, insertions, deletions)
- `excl`: excluded paths (when non-empty)

Extract `merge_base` from `data` for the git diff command in Step 3.

### Step 2: Get rules

```
ocr(action='rule', files=['path/to/file1.py', 'path/to/file2.py'])
```

Returns compact `data.groups` (source/pattern/files + truncated rule) AND full
raw output in `out` (up to 9000 chars inline, saved to `@` when larger) — read
`out`/`@` for the FULL rule text to guide your review. Groups are deduplicated
by content. `ocr(action='rules_check', file='src/app.py')` shows which single
rule applies to one path (File/Source/Pattern).

System rules cover Python (MD5, pickle, bare except, mutable defaults, SQL
injection, shell injection, exec/eval, timing attacks, resource leaks),
JS/TS (XSS, prototype pollution, eval), Java (NPE, threads, SQL), Go, Rust,
C/C++, Swift, R, and more (v1.9.x added .ipynb, Thrift, Cap'n Proto, Zig,
Jsonnet, Elm, Nix, Haskell, Nim).

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
  imports
- **Low**: Nits, personal preference — **discard silently** per OCR rule

Report in structured markdown (Files reviewed / Issues found by severity /
Critical / High / Medium sections with `path:LINE` and fixes).

## Direct mode

```
ocr(action='review', from_ref='main', to_ref='feature-branch')
ocr(action='scan', path='internal/agent')
ocr(action='scan', path='src/app.py,src/util.py')   # multiple paths
```

- `preview=True` dry-runs (no LLM call) — works without OCR LLM config
- `format='sarif'` for SARIF 2.1.0 output (saved to `@`; NOT allowed with
  `preview=True`)
- `max_tokens_budget=N` caps total tokens (input+output); when exceeded OCR
  stops dispatch, reports `data.budget=true` + `data.warns` (token_budget_reached)
  and exits non-zero — the tool surfaces this compactly with `ok:false`
- `resume='<session-id>'` continues an interrupted range/commit review (workspace
  resume NOT supported by OCR)
- `no_filter=True` keeps all comments without LLM post-filtering
- `provider`/`model` override the configured LLM for this run only
- `concurrency=N`, `timeout_mins=N`, `exclude='**/generated/*,**/testdata/*'`,
  `background`/`background_file` (business context), `rule_file` (custom rules)
- scan extras: `batch='none|by-language|by-directory'`, `no_summary=True`,
  `no_dedup=True`, `no_plan=True`

Response `data` keys: `nf` (files reviewed), `nc` (comments), `toks` (i/o/t/c
token counts), `el` (elapsed), `budget` (bool), `warns`, `sid` (session ID),
`llm` (provider/model), `retries` (retry_report counts), `tc` (tool calls),
`cmts` (compact comments: f/l/s/c/msg/fix/ex), `psum` (scan project summary).
Full JSON saved to `@`. On budget-stop/partial failure `ok:false` + `err`
carries the reason.

## Utility actions

| Action | Purpose |
|--------|---------|
| `ocr(action='llm_test')` | Live OCR LLM connectivity check (Source/URL/Model) |
| `ocr(action='llm_providers')` | List built-in providers (25+: deepseek, xai, kimi-global, siliconflow, mistral, novita, ...) |
| `ocr(action='version')` | Show OCR CLI version |
| `ocr(action='session_list', limit=N)` | Compact list: id/mode/model/time/dur/files/comments/aborted |
| `ocr(action='session_view', session_id=...)` | One session: metadata + per-file items with comment counts |
| `ocr(action='session_comments', session_id=..., severity='critical,high', category='bug,security')` | Extract comments from a saved session with filters |

## All actions reference

```
preview           — delegation: which files to review (JSON, compact)
rule              — delegation: matched review rules (full text + compact groups)
rules_check       — delegation: which rule applies to one file path
review            — direct: diff-based AI review (json|sarif, resume, budget)
scan              — direct: full-file AI scan (json|sarif, batch, resume)
session_list      — list review sessions (compact)
session_view      — inspect one session
session_comments  — extract comments from a session (severity/category filters)
llm_test          — live OCR LLM connectivity check
llm_providers     — list built-in LLM providers
version           — show OCR CLI version
```

## Repository & install

- Tool source: https://github.com/lesterppo/hermes-open-code-review
- Upstream OCR: https://github.com/alibaba/open-code-review (v1.9.6+)
- OCR docs: https://open-codereview.ai/docs
- Hermes skill: `ocr-code-review` (v2.0.0)
- Runtime copy: `~/.hermes/plugins/hermes_local_tools/ocr_tool.py` (plugin —
  survives `hermes update`; toolset `code_review` auto-enables). Repo tools/
  copy is canonical; sync edits to the plugin via `cp`.
- Install: `npm i -g @alibaba-group/open-code-review` (CLI) +
  `./install.sh` (copies tool into the plugin dir)
- OCR LLM config for direct mode: `ocr config set provider deepseek`,
  `ocr config set model deepseek-v4-flash`,
  `ocr config set providers.deepseek.api_key <key>` (or interactive
  `ocr config provider`). `ocr llm test` verifies.

## Pitfalls (from live testing)

- **Delegation mode always available** — no OCR LLM config needed for
  preview/rule/rules_check
- **Direct mode gated on CONFIG presence, not `ocr llm test`** — the gate
  reads `~/.opencodereview/config.json` (fast, free); `llm_test` action does
  the live (paying) check
- **`preview=True` bypasses LLM gate** — works for both `review` and `scan`
- **SARIF requires completed findings** — `--format sarif` + `--preview` is
  rejected by OCR; the tool surfaces the clear error
- **Budget-stop exits non-zero but publishes JSON** — `summary.budget_exceeded`
  + `warnings[]`; the tool compacts this (ok:false + data.budget + warns)
- **Working directory matters** — `ocr` operates on Git repo at cwd or
  `repo=`; a nonexistent `repo=` returns a clear error, not "ocr not found"
- **Session `items[].comments` is an int count** — `session show --json`
  gives comment COUNTS per file, not lists; use `session_comments` for content
- **`session list` output can exceed 9KB** (run_manifest per session) — the
  tool parses with a large inline budget and compacts; never truncated
- **Resume needs a range/commit** — `ocr review --resume` without `--from/--to`
  or `--commit` errors "workspace resume is not supported"
- **Rule output groups by content** — files with identical rules share one
  group; `data.groups` has counts, `out`/`@` has full text
- **Large rule output** — full rules ~8KB; read `out` (≤9000 inline) or `@`
- **ANSI escape codes in llm_test text output** — ignore; structured keys
  (src/url/model) are parsed
- **Save full results to disk** — `@` key for file path when output exceeds
  9000 chars (and always for review/scan/sarif/session outputs)
