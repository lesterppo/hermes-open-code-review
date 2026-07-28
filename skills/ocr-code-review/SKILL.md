---
name: ocr-code-review
description: Use when user asks for AI code review via OCR CLI.
license: Apache-2.0
compatibility: Requires `ocr` CLI installed (npm i -g @alibaba-group/open-code-review).
metadata:
  author: Peter/lesterppo
  homepage: https://github.com/alibaba/open-code-review
  version: "1.0.0"
---

# OCR Code Review (Hermes-native)

AI-powered code review using alibaba/open-code-review, wrapped as a native Hermes
`ocr` tool with token-efficient compact keys.

## Two modes

### Delegation mode (no OCR LLM needed — always available)

Use `ocr(action='preview')` and `ocr(action='rule')` to let OCR handle
deterministic engineering (file selection + rule resolution). Then **you** (the
Hermes agent) perform the actual review using your own LLM, following the rules
and reviewing the files OCR selects.

### Direct mode (needs OCR LLM config)

Use `ocr(action='review')` or `ocr(action='scan')` to let OCR run the full
review pipeline using its own configured LLM. Results are compact JSON with
line-level comments.

## Delegation workflow (recommended)

### Step 1: Preview — which files?

```
ocr(action='preview'[, from_ref='main', to_ref='feature'][, commit='abc123'])
```

Returns mode, file list with status, insertions/deletions, ref metadata.

### Step 2: Get rules

```
ocr(action='rule', files=['path/to/file.py', ...])
```

Returns rules grouped by content — files sharing the same rule appear under one group.

### Step 3: Get diffs

Based on mode from step 1:

- **Range**: `git diff <merge_base>..<to> -- <file>`
- **Commit**: `git show <commit> -- <file>`
- **Workspace**: `git diff HEAD -- <file>` (tracked) or read directly (untracked)

### Step 4: Review each file

For each file: read its diff (step 3), consult its rules (step 2), conduct a
thorough review. Focus on bugs, security, correctness. Skip style nits.

### Step 5: Report findings

Format each finding as:
- `path`, `content`, `start_line`/`end_line`, `category`, `severity`

## Direct mode

```
ocr(action='review', from_ref='main', to_ref='feature')
ocr(action='scan', path='internal/agent')
```

Set `preview=True` to see which files would be reviewed without calling LLM.

## Utility actions

- `ocr(action='llm_test')` — check if OCR LLM is configured
- `ocr(action='version')` — get OCR version
- `ocr(action='session_list')` — list review sessions
- `ocr(action='session_view', session_id='...')` — inspect a session

## Pitfalls

- Delegation mode always available — no OCR LLM config needed
- Direct mode needs `ocr config provider` first — check with `llm_test`
- Working directory matters — `ocr` operates on the Git repo at cwd or `repo=`
- Untracked files in workspace mode included in preview; read directly for diff
- Large output saved to disk — `@` key in response points to saved file
