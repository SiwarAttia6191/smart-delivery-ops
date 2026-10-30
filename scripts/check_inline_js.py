"""Syntax-check the landing page's inline module scripts.

The landing (docs/index.html) carries two <script type="module"> blocks
(3D hero, reveal/chart/demo wiring). A patcher once dropped CSS inside
the second one — a parse error that silenced the ENTIRE module (section
reveals, live-demo wiring, route map) while CI stayed green, because no
CI step ever parsed the page's JavaScript. This gate makes that
impossible: every inline module is extracted and handed to
`node --check` (node is preinstalled on GitHub runners).

Usage:
    python scripts/check_inline_js.py [--page docs/index.html]
"""

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", default="docs/index.html")
    args = parser.parse_args()

    html = Path(args.page).read_text(encoding="utf-8")
    modules = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
    if not modules:
        raise SystemExit(f"no inline module scripts found in {args.page} — did the page change shape?")

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for i, code in enumerate(modules):
            path = Path(tmp) / f"inline_module_{i}.mjs"
            path.write_text(code, encoding="utf-8", newline="\n")
            result = subprocess.run(
                ["node", "--check", "--experimental-detect-module", str(path)],
                capture_output=True,
                text=True,
            )
            status = "ok" if result.returncode == 0 else "SYNTAX ERROR"
            print(f"inline module {i}: {len(code)} chars — {status}")
            if result.returncode != 0:
                print(result.stderr)
                failures += 1

    if failures:
        raise SystemExit(f"{failures} inline module(s) failed to parse")
    print(f"{len(modules)} inline module(s) parse clean")


if __name__ == "__main__":
    main()
