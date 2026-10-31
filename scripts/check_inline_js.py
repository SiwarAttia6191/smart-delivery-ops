"""Syntax-check every page's inline module scripts.

The landing pages carry <script type="module"> blocks (3D hero,
reveal/chart/demo wiring, product-page behaviors). A patcher once
dropped CSS inside one — a parse error that silenced the ENTIRE module
(section reveals, live-demo wiring, route map) while CI stayed green,
because no CI step ever parsed the page's JavaScript. This gate makes
that impossible: every inline module on EVERY page under docs/ is
extracted and handed to `node --check` (node is preinstalled on GitHub
runners).

Usage:
    python scripts/check_inline_js.py            # all docs/*.html
    python scripts/check_inline_js.py --page docs/index.html
"""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


def check_page(page: Path, tmp: str) -> tuple[int, int]:
    html = page.read_text(encoding="utf-8")
    modules = re.findall(r'<script type="module">(.*?)</script>', html, re.S)
    if not modules:
        raise SystemExit(
            f"no inline module scripts found in {page} — did the page change shape?"
        )

    failures = 0
    for i, code in enumerate(modules):
        path = Path(tmp) / f"{page.stem}_module_{i}.mjs"
        path.write_text(code, encoding="utf-8", newline="\n")
        result = subprocess.run(
            ["node", "--check", "--experimental-detect-module", str(path)],
            capture_output=True,
            text=True,
        )
        status = "ok" if result.returncode == 0 else "SYNTAX ERROR"
        print(f"{page} inline module {i}: {len(code)} chars — {status}")
        if result.returncode != 0:
            print(result.stderr)
            failures += 1
    return failures, len(modules)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", default=None, help="check one page instead of all docs/*.html")
    args = parser.parse_args()

    pages = [Path(args.page)] if args.page else sorted(Path("docs").glob("*.html"))
    total_failures = 0
    total_modules = 0
    with tempfile.TemporaryDirectory() as tmp:
        for page in pages:
            failures, count = check_page(page, tmp)
            total_failures += failures
            total_modules += count

    if total_failures:
        raise SystemExit(f"{total_failures} inline module(s) failed to parse")
    print(f"{total_modules} inline module(s) across {len(pages)} page(s) parse clean")


if __name__ == "__main__":
    main()
