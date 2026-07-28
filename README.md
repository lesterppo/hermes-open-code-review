# Hermes Open Code Review — AI-Powered Code Review for Hermes Agent

[![GitHub stars](https://img.shields.io/github/stars/lesterppo/hermes-open-code-review?style=flat-square)](https://github.com/lesterppo/hermes-open-code-review/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes_Agent-native_tool-8A2BE2?style=flat-square)](https://github.com/NousResearch/hermes-agent)
[![OCR version](https://img.shields.io/badge/OCR-v1.8+-green?style=flat-square)](https://github.com/alibaba/open-code-review)

**AI-powered automated code review** for [Hermes Agent](https://github.com/NousResearch/hermes-agent), integrating Alibaba's [Open Code Review](https://github.com/alibaba/open-code-review) (OCR) as a native, token-efficient tool.

> OCR originated as Alibaba Group's internal AI code review assistant, serving tens of thousands of developers and identifying millions of code defects over two years. It achieves higher Precision and F1 than general-purpose agents while consuming ~1/9 of the tokens.

## Why Use This?

- **Two review modes** — Delegation (no OCR LLM needed, always works) and Direct (full AI pipeline with line-level comments)
- **Token-efficient** — Compact JSON output with 1-2 char keys, large results saved to disk
- **Deterministic + AI hybrid** — OCR handles file selection and rule matching deterministically; the review itself uses AI
- **Built-in security rules** — Detects SQL injection, XSS, hardcoded secrets, weak cryptography, shell injection, and more
- **Multi-language** — Built-in rules for Python, JavaScript, TypeScript, Java, Go, Rust, C/C++, and more
- **Zero LLM config for delegation** — OCR provides file lists and review rules; your Hermes agent does the review

## Quick Start

```bash
# 1. Install OCR CLI (one-time)
npm install -g @alibaba-group/open-code-review

# 2. Install the Hermes native tool
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh ~/.hermes/hermes-agent
```

Then add `"ocr"` to `_HERMES_CORE_TOOLS` in `toolsets.py` and restart Hermes.

## Delegation Mode (Recommended)

No OCR LLM configuration needed. OCR handles the deterministic work; your Hermes agent performs the review using its own LLM.

```python
# Step 1: See which files changed
ocr(action='preview', from_ref='main', to_ref='feature-branch')

# Step 2: Get review rules for those files
ocr(action='rule', files=['src/app.py', 'src/utils.py'])

# Step 3: Hermes agent gets diffs via git and performs the review
# (following the rules from Step 2, reporting findings by severity)
```

**Live-tested** against code with intentional bugs (SQL injection, shell injection, MD5 hashing, pickle deserialization, bare except, mutable defaults) — found all 9 issues correctly with severity classification.

## Direct Mode

Requires OCR LLM configured (`ocr config provider`). OCR runs the full AI review pipeline.

```python
# Diff-based review (branch comparison)
ocr(action='review', from_ref='main', to_ref='feature-branch')

# Full-file scan (no diff needed — audit unfamiliar code)
ocr(action='scan', path='internal/agent')

# Preview without LLM call (works without LLM config)
ocr(action='review', preview=True)
```

OCR returns structured JSON with line-level comments: severity, category, exact line ranges, and fix suggestions.

## All Actions

| Action | Mode | Purpose |
|--------|------|---------|
| `preview` | Delegation | Which files to review + ref metadata + merge base |
| `rule` | Delegation | Matched review rules grouped by content |
| `review` | Direct | Diff-based AI review with line-level comments |
| `scan` | Direct | Full-file AI scan (no git history needed) |
| `session_list` | Utility | List saved review sessions |
| `session_view` | Utility | Inspect a review session |
| `llm_test` | Utility | Check if OCR LLM is connected |
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
tools/ocr_tool.py              # Native Hermes tool (registry.register + dispatch)
skills/ocr-code-review/SKILL.md  # Hermes skill with full delegation workflow
install.sh                     # Idempotent installer
AGENTS.md                      # AI agent discoverability instructions
```

## Project Context

This tool wraps [alibaba/open-code-review](https://github.com/alibaba/open-code-review) (Apache 2.0), a production-hardened AI code review CLI that uses a hybrid architecture: deterministic pipelines for file selection and rule matching, plus LLM agents for deep review with tool-use capabilities.

**Related projects:**
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — Personal AI agent platform
- [Open Code Review](https://github.com/alibaba/open-code-review) — Upstream OCR CLI
- [OCR Documentation](https://open-codereview.ai/docs) — Full docs

## License

MIT — Copyright 2026 [lesterppo](https://github.com/lesterppo)

OCR is Apache-2.0 by Alibaba.
