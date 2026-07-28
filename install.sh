#!/usr/bin/env bash
# install.sh — idempotent installer for hermes-open-code-review
# Copies the native ocr tool + skill into a Hermes Agent installation.
#
# Usage:
#   ./install.sh ~/.hermes/hermes-agent
#   ./install.sh /path/to/hermes-agent
#
# Requires: ocr CLI (npm i -g @alibaba-group/open-code-review)

set -euo pipefail

HERMES_AGENT="${1:-$HOME/.hermes/hermes-agent}"
if [ ! -d "$HERMES_AGENT/tools" ]; then
    echo "ERROR: $HERMES_AGENT/tools not found. Is this a Hermes Agent installation?"
    exit 1
fi

SKILL_DIR="$HOME/.hermes/skills/devops/ocr-code-review"

echo "=== hermes-open-code-review installer ==="
echo "Target: $HERMES_AGENT"
echo ""

# 1. Copy native tool
echo "[1/3] Installing native tool..."
cp -v tools/ocr_tool.py "$HERMES_AGENT/tools/ocr_tool.py"

# 2. Copy skill
echo "[2/3] Installing skill..."
mkdir -p "$SKILL_DIR"
cp -v skills/ocr-code-review/SKILL.md "$SKILL_DIR/SKILL.md"

# 3. Wire into toolsets.py
echo "[3/3] Checking toolsets.py wiring..."

TOOLSETS="$HERMES_AGENT/toolsets.py"

# Check if ocr is already in _HERMES_CORE_TOOLS
if grep -q '"ocr"' "$TOOLSETS"; then
    echo "  ✓ ocr already in _HERMES_CORE_TOOLS"
else
    echo "  → Add the following to _HERMES_CORE_TOOLS in $TOOLSETS:"
    echo '    "ocr",  # AI code review via alibaba/open-code-review'
    echo ""
    echo "  → And add the code_review toolset to TOOLSETS dict:"
    echo '    "code_review": {'
    echo '        "description": "AI code review via alibaba/open-code-review (OCR).",'
    echo '        "tools": ["ocr"],'
    echo '        "includes": []'
    echo '    },'
fi

echo ""
echo "=== Done ==="
echo "Restart Hermes to load the new tool."
echo "Verify with: ocr(action='version')"
echo "Start a review: ocr(action='preview')"
