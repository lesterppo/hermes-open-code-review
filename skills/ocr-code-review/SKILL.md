---
name: ocr-code-review
description: Use when user asks for AI code review via OCR CLI.
license: Apache-2.0
compatibility: Requires `ocr` CLI v1.12+ installed. Uses native Hermes `ocr` tool.
metadata:
  author: Peter/lesterppo
  homepage: https://github.com/lesterppo/hermes-open-code-review
  version: "3.0.0"
---

# OCR Code Review (Hermes-native)

AI-powered code review using [alibaba/open-code-review](https://github.com/alibaba/open-code-review)
(`ocr` v1.12+), wrapped as a native Hermes `ocr` tool (toolset `code_review`).
Three mode groups: delegation (no OCR LLM needed, always works), direct (full
OCR pipeline, needs LLM config), and utility (session forensics, WebUI, checks).

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

## Delegation workflow (proven — live-tested Jul/Sep 2026)

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
C/C++, Swift, R, Objective-C, Ruby, PHP, Kotlin, Scala, SQL, Zig, Nim,
Haskell, Elm, OCaml/ReasonML, Jsonnet, Nix, Thrift, Cap'n Proto, `.ipynb`,
Solidity, Vyper, Rego, Verilog/SystemVerilog/VHDL, MATLAB, Handlebars/Mustache,
Pug (v1.10-v1.11 language additions).

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
- `effort='low'|'medium'|'high'` — **review only** (v1.12: `ocr scan` has no
  `--effort`; the tool rejects it on scan with a hint rather than dropping it)
- `format='sarif'` for SARIF 2.1.0 output (saved to `@`; NOT allowed with
  `preview=True`)
- `max_tokens_budget=N` caps total tokens (input+output); when exceeded OCR
  stops dispatch and reports `data.budget=true` + `data.warns`. **v1.12 exits 0
  when partial results are published** (non-zero only if every selected item
  failed) — detect a budget stop from `data.budget`, never from the exit code
- `max_tokens=N` (per-group/per-file prompt ceiling), `max_tools=N` (tool-call
  rounds; upstream min 50), `max_git_procs=N` (default 16)
- `resume='<session-id>'` continues an interrupted range/commit review (workspace
  resume NOT supported by OCR — needs `from_ref`/`to_ref` or `commit`)
- `no_filter=True` keeps all comments without LLM post-filtering
- `provider`/`model` override the configured LLM for this run only
- `concurrency=N`, `timeout_mins=N`, `exclude='**/generated/*,**/testdata/*'`,
  `background`/`background_file` (business context), `rule_file` (custom rules)
- scan extras: `batch='none|by-language|by-directory'`, `no_summary=True`,
  `no_dedup=True`, `no_plan=True`
- The tool routes json/sarif through its own `-o` file and re-reads it, so
  `result_file` points at the raw payload while stdout stays compact

Response `data` keys: `nf` (files reviewed), `nc` (comments), `toks` (i/o/t/c
token counts), `el` (elapsed), `budget` (bool), `warns`, `sid` (session ID),
`llm` (provider/model), `retries` (retry_report counts), `tc` (tool calls),
`cmts` (compact comments: f/l/s/c/msg/fix/ex), `psum` (scan project summary).
Full JSON saved to `@`. The model's private `thinking` traces are dropped from
`cmts` — tens of KB per finding, no review signal.

## Utility actions

| Action | Purpose |
|--------|---------|
| `ocr(action='llm_test')` | Live OCR LLM connectivity check (Source/URL/Model) |
| `ocr(action='llm_providers')` | List built-in providers (25+: deepseek, xai, kimi-global, siliconflow, mistral, novita, ...) |
| `ocr(action='version')` | Show OCR CLI version + tool version |
| `ocr(action='session_list', limit=N)` | Compact list: id/mode/model/time/dur/files/comments/aborted |
| `ocr(action='session_view', session_id=...)` | One session: metadata + per-file items with comment counts |
| `ocr(action='session_comments', session_id=..., severity='critical,high', category='bug,security')` | Extract comments from a saved session with filters |
| `ocr(action='session_compare', before_id=..., after_id=...)` | Diff two sessions: `new` / `persisting` / `resolved` (matched on path+category+snippet, so a finding that only moved down the file still counts as persisting) |
| `ocr(action='viewer')` | Start the `ocr viewer` WebUI detached; returns `url` + `pid`; idempotent |
| `ocr(action='viewer_stop')` | Stop the viewer this tool started |

Use `session_compare` after a fix round to prove what actually changed:
`new` = introduced, `persisting` = still there, `resolved` = gone.

## All actions reference

```
preview           — delegation: which files to review (JSON, compact)
rule              — delegation: matched review rules (full text + compact groups)
rules_check       — delegation: which rule applies to one file path
review            — direct: diff-based AI review (json|sarif, effort, resume, budget)
scan              — direct: full-file AI scan (json|sarif, batch, resume)
session_list      — list review sessions (compact)
session_view      — inspect one session
session_comments  — extract comments from a session (severity/category filters)
session_compare   — diff two sessions (new / persisting / resolved)
llm_test          — live OCR LLM connectivity check
llm_providers     — list built-in LLM providers
viewer            — start the session-history WebUI (detached)
viewer_stop       — stop the viewer
version           — show OCR CLI version
```

## Repository & install

- Tool source: https://github.com/lesterppo/hermes-open-code-review
- Upstream OCR: https://github.com/alibaba/open-code-review (v1.12.2+)
- OCR docs: https://open-codereview.ai/docs
- Hermes skill: `ocr-code-review` (v3.0.0)
- Runtime copy: `~/.hermes/plugins/hermes_local_tools/ocr_tool.py` (plugin —
  survives `hermes update`; toolset `code_review` auto-enables). Repo `tools/`
  copy is canonical; sync edits to the plugin via `cp`.
- Flag audit: `python3 scripts/flag_audit.py` cross-checks every flag the tool
  can emit against `ocr <cmd> --help`. Run it after every CLI upgrade —
  upstream ships a release every 1-2 days.
- Install: `npm i -g @alibaba-group/open-code-review` (CLI) +
  `./install.sh` (copies tool into the plugin dir)
- OCR LLM config for direct mode: `ocr config set provider deepseek`,
  `ocr config set model deepseek-v4-flash`,
  `ocr config set providers.deepseek.api_key <key>` (or interactive
  `ocr config provider`). `ocr llm test` verifies.

## Pitfalls (from live testing, CLI v1.12.2)

- **Delegation mode always available** — no OCR LLM config needed for
  preview/rule/rules_check
- **Direct mode gated on CONFIG presence, not `ocr llm test`** — the gate
  reads `~/.opencodereview/config.json` (fast, free); the `llm_test` action
  does the live (paying) check
- **`--effort` is review-only** — `ocr scan --effort` = `unknown flag`; the
  tool returns an explicit error instead of dropping the parameter
- **Budget stop exits 0 with partial results** in v1.12 (non-zero only when
  all items failed) — detect via `data.budget` / `data.warns`
- **`-o` prints a status line to stderr** (`[ocr] Results written to <path>`) —
  the tool strips it from `err` so success never looks like failure
- **`--color never` is injected globally** — keeps stdout ANSI-free; the
  runner retries without it on older CLIs
- **`preview=True` bypasses the LLM gate** — works for both `review` and `scan`
- **SARIF requires completed findings** — `--format sarif` + `--preview` is
  rejected by OCR; the tool surfaces the clear error
- **Working directory matters** — `ocr` operates on the Git repo at cwd or
  `repo=`; a nonexistent `repo=` returns a clear error, not "ocr not found"
- **Session `items[].comments` is an int count** — `session show --json` gives
  comment COUNTS per file, not lists; use `session_comments` for content
- **`session list` output can exceed 9KB** (run_manifest per session) — the
  tool parses with a large inline budget and compacts; never truncated
- **Resume needs a range/commit** — `ocr review --resume` without `--from/--to`
  or `--commit` errors "workspace resume is not supported"
- **Rule output groups by content** — files with identical rules share one
  group; `data.groups` has counts, `out`/`@` has full text (~8-9KB: read `out`)
- **ANSI escape codes in llm_test text output** — ignore; structured keys
  (src/url/model) are parsed
- **Save full results to disk** — `@` key for file path when output exceeds
  9000 chars (and always for review/scan/sarif/session outputs)
- **viewer binds localhost:5483** — the tool starts it detached and returns the
  URL; a stale viewer holding the port makes a fresh start fail cleanly (stop
  it first with `viewer_stop`)
