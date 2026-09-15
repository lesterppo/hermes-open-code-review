#!/usr/bin/env bash
# install.sh — idempotent installer for hermes-open-code-review
# Installs the native ocr tool + skill into the local-tools plugin dir
# (~/.hermes/plugins/hermes_local_tools/) so `hermes update` never wipes them.
#
# Usage:
#   ./install.sh
#   ./install.sh ~/.hermes/plugins/hermes_local_tools   # explicit plugin dir
#
# Requires: ocr CLI (npm i -g @alibaba-group/open-code-review)

set -euo pipefail

PLUGIN_DIR="${1:-$HOME/.hermes/plugins/hermes_local_tools}"
SKILL_DIR="$HOME/.hermes/skills/devops/ocr-code-review"

echo "=== hermes-open-code-review installer (v3, ocr CLI v1.12+) ==="
echo "Plugin target: $PLUGIN_DIR"
echo ""

# 1. Copy native tool into the plugin
if [ ! -d "$PLUGIN_DIR" ]; then
    echo "ERROR: $PLUGIN_DIR not found. Is the hermes_local_tools plugin installed?"
    echo "Create it with: mkdir -p $PLUGIN_DIR (then add a plugin.yaml)"
    exit 1
fi
echo "[1/3] Installing native tool..."
cp -v tools/ocr_tool.py "$PLUGIN_DIR/ocr_tool.py"

# 2. Ensure the plugin registers the ocr tool (idempotent)
echo "[2/3] Ensuring plugin registration..."
INIT="$PLUGIN_DIR/__init__.py"
if [ -f "$INIT" ]; then
    if ! grep -q "ocr_tool" "$INIT"; then
        echo "  → WARNING: '$INIT' does not import ocr_tool."
        echo "    Add to the import block:"
        echo "        ocr_tool,"
        echo "    and to _TOOLS:"
        echo '        ("ocr", "code_review"),'
    else
        echo "  ✓ ocr_tool already registered in __init__.py"
    fi
else
    echo "  → Note: no __init__.py in plugin dir; the tool file still"
    echo "    self-registers via registry.register() on import."
fi

# 3. Copy skill
echo "[3/3] Installing skill..."
mkdir -p "$SKILL_DIR"
cp -v skills/ocr-code-review/SKILL.md "$SKILL_DIR/SKILL.md"

echo ""
echo "=== Done ==="
echo "Restart Hermes to load the new tool."
echo "The 'code_review' toolset auto-enables (plugin toolsets default to enabled)."
echo "Verify with: ocr(action='version')"
echo "Start a review: ocr(action='preview')"
echo ""
echo "If the toolset doesn't appear after restart, run:"
echo "  hermes tools   # saves plugin toolset keys; then toggle code_review if needed"
