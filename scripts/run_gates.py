"""The pre-push gate, exactly CI's set — one command, first failure wins.

Born 2026-11-03: an ad-hoc ``gate | tail`` masked a failure exit and CI
caught what the local chain waved through (second time a pipe masked an
exit code). The rule this file enforces: gates run as ONE process with a
real exit code — never a hand-typed pipe, never a per-gate shortcut.

Usage: ``python scripts/run_gates.py`` (or ``make gates``).
"""

import subprocess
import sys

GATES: list[list[str]] = [
    [sys.executable, "-m", "ruff", "check", "."],
    [sys.executable, "-m", "ruff", "format", "--check", "."],
    [sys.executable, "scripts/check_inline_js.py"],
    [sys.executable, "-m", "mypy", "apps", "core", "domain", "scripts", "tests"],
    [sys.executable, "-m", "pytest", "-q", "--cov=apps", "--cov=core", "--cov=domain"],
    [sys.executable, "-m", "pip_audit", "-r", "requirements.txt", "--progress-spinner", "off"],
]


def main() -> int:
    for gate in GATES:
        name = " ".join(gate[2:] if gate[1] == "-m" else gate[1:])
        print(f"── {name}", flush=True)
        try:
            # output streams unfiltered — a failure is visible AND fatal
            subprocess.run(gate, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"── GATE FAILED ({name}, exit {exc.returncode})", flush=True)
            return exc.returncode
    print("── ALL GATES GREEN", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
