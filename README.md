# Hermes Open Code Review — AI-Powered Code Review for Hermes Agent

[![GitHub stars](https://img.shields.io/github/stars/lesterppo/hermes-open-code-review?style=flat-square)](https://github.com/lesterppo/hermes-open-code-review/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](https://github.com/lesterppo/hermes-open-code-review/blob/main/LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-native_tool-8A2BE2?style=flat-square)](https://github.com/NousResearch/hermes-agent)
[![OCR version](https://img.shields.io/badge/OCR-v1.12%2B-green?style=flat-square)](https://github.com/alibaba/open-code-review)

**AI-powered automated code review** for [Hermes Agent](https://github.com/NousResearch/hermes-agent), integrating Alibaba's [Open Code Review](https://github.com/alibaba/open-code-review) (OCR) as a native, token-efficient tool.

> OCR originated as Alibaba Group's internal AI code review assistant, serving tens of thousands of developers and identifying millions of code defects over two years. It achieves higher Precision and F1 than general-purpose agents while consuming ~1/9 of the tokens.

## Why Use This?

- **Three mode groups** — Delegation (`preview`/`rule`/`rules_check`: no OCR LLM needed, always works), Direct (`review`/`scan`: full AI pipeline with line-level comments, json or SARIF), and Utility (session forensics, session compare, WebUI viewer, LLM checks)
- **v1.12.x CLI support** — `--effort low|medium|high`, `--max-tokens`, `--max-tools`, `--max-git-procs`, `--output`, `session compare` (new/persisting/resolved across runs), `ocr viewer` WebUI, `--audience agent` (summary-only output)
- **Token-efficient** — Compact JSON with 1-2 char keys; the model's private `thinking` traces (tens of KB per finding) are stripped; big results saved to disk (`@`); reports routed through `-o` and re-read so stdout stays small
- **Deterministic + AI hybrid** — OCR handles file selection and rule matching deterministically; the review itself uses AI
- **Built-in security rules** — SQL injection, XSS, hardcoded secrets, weak cryptography, shell injection, and more
- **Multi-language** — Python, JavaScript/TypeScript, Java, Go, Rust, C/C++, Swift, R, Objective-C, Ruby, PHP, Kotlin, Scala, SQL, Zig, Nim, Haskell, Elm, OCaml/ReasonML, Jsonnet, Nix, Thrift, Cap'n Proto, Solidity, Vyper, Rego, Verilog/SystemVerilog/VHDL, MATLAB, Handlebars/Mustache, Pug, `.ipynb` and more
- **Zero LLM config for delegation** — OCR provides file lists and review rules; your Hermes agent does the review

## Quick Start

```bash
# 1. Install OCR CLI (one-time) — v1.12.2 or newer
npm install -g @alibaba-group/open-code-review

# 2. Install the Hermes native tool (installs into the local-tools plugin —
#    survives `hermes update`)
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh
```

Restart Hermes. The `ocr` tool registers under the `code_review` toolset and
auto-enables (plugin toolsets default to enabled). Verify with
`ocr(action='version')`.

## Delegation Mode (Recommended)

No OCR LLM configuration needed. OCR handles the deterministic work; your Hermes agent performs the review using its own LLM.

```python
# Step 1: See which files changed (JSON: mode, from/to/merge_base, files, counts)
ocr(action='preview', from_ref='main', to_ref='feature-branch')

# Step 2: Get review rules for those files (full text in `out`/`@`, compact groups in `data`)
ocr(action='rule', files=['src/app.py', 'src/utils.py'])

# Step 2b: Which rule applies to one file?
ocr(action='rules_check', file='src/app.py')

# Step 3: Hermes agent gets diffs via git and performs the review
# (following the rules from Step 2, reporting findings by severity)
```

**Live-tested** against code with intentional bugs (SQL injection, shell injection, MD5 hashing, pickle deserialization, bare except, mutable defaults) — found all issues correctly with severity classification.

## Direct Mode

Requires OCR LLM configured (`ocr config set provider deepseek && ocr config set model deepseek-v4-flash && ocr config set providers.deepseek.api_key <key>`, or interactive `ocr config provider`; verify with `ocr llm test`).

```python
# Diff-based review (branch comparison) — json output
ocr(action='review', from_ref='main', to_ref='feature-branch')

# Review effort preset (review-only in v1.12.x)
ocr(action='review', from_ref='main', to_ref='feature-branch', effort='high')

# Full-file scan (no diff needed — audit unfamiliar code)
ocr(action='scan', path='internal/agent')

# Tune prompt / tool-round ceilings per group or file
ocr(action='scan', path='internal/agent', max_tokens=6000, max_tools=60)

# SARIF 2.1.0 report (saved to disk, `@` key)
ocr(action='review', from_ref='main', to_ref='feature-branch', format='sarif')

# Dry-run without LLM call
ocr(action='review', preview=True)

# Resume an interrupted range/commit review
ocr(action='review', from_ref='main', to_ref='feature-branch', resume='<session-id>')

# Cap token spend — budget-stop surfaces data.budget + warnings
ocr(action='scan', path='internal/agent', max_tokens_budget=200000)

# Session forensics
ocr(action='session_list')
ocr(action='session_view', session_id='<id>')
ocr(action='session_comments', session_id='<id>', severity='critical,high')

# Did the last round of fixes actually fix anything? (new / persisting / resolved)
ocr(action='session_compare', before_id='<older-id>', after_id='<newer-id>')

# Browse session history in the WebUI (starts detached, returns the URL)
ocr(action='viewer')
ocr(action='viewer_stop')
```

OCR returns structured JSON with line-level comments: severity, category, exact line ranges, fix suggestions, and existing code.

## All Actions

| Action | Mode | Purpose |
| --- | --- | --- |
| `preview` | Delegation | Which files to review + mode/ref/merge_base metadata (JSON) |
| `rule` | Delegation | Matched review rules grouped by content (full text + compact groups) |
| `rules_check` | Delegation | Which rule applies to a given file path |
| `review` | Direct | Diff-based AI review (json \| sarif, effort, resume, token budget) |
| `scan` | Direct | Full-file AI scan (json \| sarif, batch, resume, no-diff) |
| `session_list` | Utility | List saved review sessions (compact) |
| `session_view` | Utility | Inspect a session (files + comment counts) |
| `session_comments` | Utility | Extract comments from a session (severity/category filters) |
| `session_compare` | Utility | Diff two sessions: new / persisting / resolved findings |
| `llm_test` | Utility | Live OCR LLM connectivity check |
| `llm_providers` | Utility | List built-in LLM providers (25+) |
| `viewer` | Utility | Start the local session-history WebUI (detached, returns URL) |
| `viewer_stop` | Utility | Stop a viewer started by this tool |
| `version` | Utility | Show OCR CLI version |

## What OCR Detects

OCR's built-in deterministic rules cover:

- **Security**: SQL injection, shell injection, path traversal, weak cryptography (MD5/SHA1), pickle deserialization, XSS, hardcoded secrets
- **Correctness**: Null pointer exceptions, off-by-one errors, empty collection handling, float equality, identity vs equality
- **Error Handling**: Bare except clauses, swallowed exceptions, lost tracebacks, broad try blocks
- **Performance**: String building in loops, repeated computations, missing generators
- **Concurrency**: Check-then-act races, blocking calls in async, shared mutable state
- **Resource Management**: Unclosed files/sockets/connections, missing context managers

## Repository Structure

```
tools/ocr_tool.py # Native Hermes tool v3 (registry.register + dispatch)
skills/ocr-code-review/SKILL.md # Hermes skill v3.0.0 with full delegation workflow
scripts/flag_audit.py # Cross-checks every emitted flag against `ocr <cmd> --help`
install.sh # Installs tool + skill into ~/.hermes/plugins/hermes_local_tools/
AGENTS.md # AI agent discoverability instructions
```

The runtime copy lives in `~/.hermes/plugins/hermes_local_tools/ocr_tool.py`
(the local-tools plugin — survives `hermes update`). The repo `tools/` copy is
canonical; keep both in sync (`cp tools/ocr_tool.py ~/.hermes/plugins/hermes_local_tools/`).

## Compatibility notes

- `--effort` exists on `ocr review` only (v1.12.x). The tool rejects `effort`
  on `scan` with a hint instead of silently dropping it.
- `--color never` is injected into every call so stdout stays ANSI-free; the
  runner auto-retries without it on CLI versions that predate the flag.
- SARIF is not allowed with `preview=True` (upstream constraint, surfaced as a
  clear error).
- A token-budget stop publishes partial results and exits 0 in v1.12
  (non-zero only when every item failed). The tool reports the stop as
  `data.budget` + `data.warns` regardless of exit code.
- `ocr`'s `[ocr] Results written to <file>` status line is filtered out of
  `err`, so a successful run never looks like a failure.
- Workspace resume is unsupported upstream — `resume` needs a range or commit.

## Project Context

This tool wraps [alibaba/open-code-review](https://github.com/alibaba/open-code-review) (Apache 2.0), a production-hardened AI code review CLI that uses a hybrid architecture: deterministic pipelines for file selection and rule matching, plus LLM agents for deep review with tool-use capabilities.

**Related projects:**

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — Personal AI agent platform
- [Open Code Review](https://github.com/alibaba/open-code-review) — Upstream OCR CLI
- [OCR Documentation](https://open-codereview.ai/docs) — Full docs

## License

MIT — Copyright 2026 [lesterppo](https://github.com/lesterppo)

OCR is Apache-2.0 by Alibaba.
