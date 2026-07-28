# hermes-open-code-review

Hermes Agent native tool for [alibaba/open-code-review](https://github.com/alibaba/open-code-review) — AI-powered code review with two modes:

- **Delegation** — Deterministic file selection + rule resolution by OCR; Hermes agent performs the review using its own LLM. **No OCR LLM config needed.**
- **Direct** — Full AI review pipeline by OCR's configured LLM with line-level comments.

## Install

```bash
# 1. Install OCR CLI
npm install -g @alibaba-group/open-code-review

# 2. Install Hermes tool
git clone https://github.com/lesterppo/hermes-open-code-review.git
cd hermes-open-code-review
./install.sh ~/.hermes/hermes-agent
```

Then add `"ocr"` to `_HERMES_CORE_TOOLS` in `toolsets.py` and restart Hermes.

## Usage

### Delegation mode (recommended — no OCR LLM needed)

```python
# Step 1: See which files changed
ocr(action='preview', from_ref='main', to_ref='feature-branch')

# Step 2: Get review rules for those files
ocr(action='rule', files=['src/app.py', 'src/utils.py'])

# Step 3: Get diffs (git) and review (Hermes agent)
```

### Direct mode (needs OCR LLM configured)

```python
# Review diff
ocr(action='review', from_ref='main', to_ref='feature-branch')

# Scan entire directory
ocr(action='scan', path='internal/agent')

# Preview without LLM call
ocr(action='review', preview=True)
```

## Actions

| Action | Purpose |
|--------|---------|
| `preview` | Which files to review + mode/ref metadata |
| `rule` | Matched review rules grouped by content |
| `review` | Diff-based AI review |
| `scan` | Full-file AI scan |
| `session_list` | List review sessions |
| `session_view` | Inspect a session |
| `llm_test` | Check LLM connectivity |
| `version` | Show OCR version |

## License

MIT — Copyright 2026 Peter/lesterppo

OCR itself is Apache-2.0 by Alibaba.
