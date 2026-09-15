#!/usr/bin/env python3
"""flag_audit.py — verify every CLI flag `ocr_tool.py` can emit exists in the
installed `ocr` CLI's help for the command that receives it.

Why: OCR ships ~1 release every 1-2 days; a flag added or removed upstream
silently turns a tool call into `Error: unknown flag: --x` at runtime. Run this
after every `npm i -g @alibaba-group/open-code-review` upgrade.

Usage:
    python3 scripts/flag_audit.py            # audit against `ocr` on PATH
    python3 scripts/flag_audit.py /path/to/ocr

Exit code 0 = every emitted flag is accepted; 1 = mismatch (prints details).
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

# which CLI command each tool function builds arguments for
FUNC_TO_CMDS = {
    "_preview": ["delegate preview"],
    "_rule": ["delegate rule"],
    "_rules_check": ["rules check"],
    "_direct_args": ["review", "scan"],   # shared review/scan preamble
    "_review": ["review"],
    "_scan": ["scan"],
    "_session_list": ["session list"],
    "_session_view": ["session show"],
    "_session_comments": ["session comments"],
    "_session_compare": ["session compare"],
    "_viewer": ["viewer"],
}
FLAG_RE = re.compile(r"^(--[a-z0-9][a-z0-9-]*|-[a-zA-Z])$")


def emitted_flags(source: str) -> dict[str, set[str]]:
    """Map CLI command -> flags that tool code passes to it."""
    tree = ast.parse(source)
    per_func: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in FUNC_TO_CMDS:
            continue
        flags: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if FLAG_RE.match(sub.value):
                    flags.add(sub.value)
            # tuple pairs like (("path", "--path"), ...) are covered above
        per_func[node.name] = flags
    out: dict[str, set[str]] = {}
    for fn, cmds in FUNC_TO_CMDS.items():
        for cmd in cmds:
            out.setdefault(cmd, set()).update(per_func.get(fn, set()))
    return out


def help_flags(ocr: str, cmd: str) -> set[str]:
    p = subprocess.run([ocr] + cmd.split() + ["--help"],
                       capture_output=True, text=True)
    return set(re.findall(r"(--[a-z0-9][a-z0-9-]*|-[a-zA-Z])\b", p.stdout + p.stderr))


def main() -> int:
    ocr = sys.argv[1] if len(sys.argv) > 1 else (shutil.which("ocr") or "ocr")
    tool = Path(__file__).resolve().parent.parent / "tools" / "ocr_tool.py"
    if not tool.exists():
        print(f"FAIL: {tool} not found")
        return 1

    version = subprocess.run([ocr, "--version"], capture_output=True,
                             text=True).stdout.splitlines()
    print(f"CLI   : {version[0] if version else 'unknown'} ({ocr})")
    print(f"tool  : {tool}")

    mapping = emitted_flags(tool.read_text(encoding="utf-8"))
    # --color is a global flag, valid on every command
    fails = 0
    for cmd in sorted(mapping):
        emitted = sorted(mapping[cmd])
        have = help_flags(ocr, cmd)
        missing = [f for f in emitted if f not in have]
        if missing:
            fails += 1
            print(f"FAIL {cmd:18} missing upstream: {missing}")
        else:
            print(f"OK   {cmd:18} {len(emitted)} flags: {' '.join(emitted)}")

    # reverse: upstream flags we never expose (informational only)
    for cmd in sorted(mapping):
        have = help_flags(ocr, cmd)
        unused = sorted(f for f in have
                        if f not in mapping[cmd]
                        and f not in ("-h", "--help", "--color"))
        if unused:
            print(f"     {cmd}: upstream flags not exposed -> {unused}")

    print(f"\n{'PASS' if not fails else 'FAIL'}: {len(mapping)} command surfaces")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
