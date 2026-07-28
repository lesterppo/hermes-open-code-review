# AGENTS.md — AI agent instructions for hermes-open-code-review

This document tells AI coding agents (Hermes, Claude Code, Codex, etc.)
everything they need to install, configure, and use the `ocr` tool in a
Hermes Agent installation.

## What this repo provides

A Hermes Agent **native tool** that wraps the
[alibaba/open-code-review](https://github.com/alibaba/open-code-review) CLI
(`ocr`). Two modes:

- **Delegation** (always available, no LLM needed on OCR side): `preview` +
  `rule` actions for deterministic file selection and rule resolution. The
  Hermes agent performs the review using its own LLM.
- **Direct** (needs OCR LLM config): `review` and `scan` actions for full
  AI-powered review with line-level comments.

## Quick start (for an AI agent integrating this)

```bash
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh ~/.hermes/hermes-agent
```

Then wire the tool into the agent's toolset by adding `"ocr"` to
`_HERMES_CORE_TOOLS` in `toolsets.py`:

```python
_HERMES_CORE_TOOLS = [
    ...
    "ocr",   # AI code review via alibaba/open-code-review
]
```

And add the toolset:

```python
"code_review": {
    "description": "AI code review via alibaba/open-code-review (OCR).",
    "tools": ["ocr"],
    "includes": []
},
```

Restart Hermes.

## Prerequisites

Install the `ocr` CLI:

```bash
npm install -g @alibaba-group/open-code-review
```

For delegation mode, no further config is needed. For direct mode:

```bash
ocr config provider
ocr config model
ocr llm test
```

## How the tool works

The tool exposes 8 actions through a single `ocr` registry entry:

| Action | Mode | LLM needed? | Purpose |
|--------|------|-------------|---------|
| `preview` | Delegation | No | Which files to review + mode/ref metadata |
| `rule` | Delegation | No | Matched review rules grouped by content |
| `review` | Direct | Yes (no for preview) | Diff-based AI review |
| `scan` | Direct | Yes (no for preview) | Full-file AI scan |
| `session_list` | Utility | No | List saved review sessions |
| `session_view` | Utility | No | Inspect a session |
| `llm_test` | Utility | No | Check LLM connectivity |
| `version` | Utility | No | Show OCR version |

### Delegation workflow (recommended)

1. `ocr(action='preview', from_ref='main', to_ref='feature')` → get file list
2. `ocr(action='rule', files=['path/to/file.py'])` → get review rules
3. Hermes agent reads diffs and performs review using its own LLM
4. Agent reports findings in structured format

### Direct mode

```python
# Review diff between branches
ocr(action='review', from_ref='main', to_ref='feature')

# Scan entire directory
ocr(action='scan', path='internal/agent')

# Preview without LLM
ocr(action='review', preview=True)
```

## Output format (compact, token-efficient)

All responses use short keys:

| Key | Meaning |
|-----|---------|
| `ok` | success |
| `e` | error |
| `d` | detail |
| `h` | hint |
| `out` | stdout |
| `err` | stderr |
| `@` | saved file path |
| `code` | exit code |
| `nc` | comment count |
| `nf` | files reviewed |
| `sid` | session ID |

Full skill with pitfalls and procedures: `skills/ocr-code-review/SKILL.md`

## Files

```
tools/ocr_tool.py              # The tool (registry.register + dispatch)
skills/ocr-code-review/SKILL.md  # Skill (auto-loaded by Hermes)
install.sh                     # Idempotent installer
```

## Privacy rules

- No secrets, API keys, or personal paths in any committed file.
- All paths use `get_hermes_home()`, never `/home/*`.
