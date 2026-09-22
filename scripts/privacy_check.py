"""Fail if anything that should stay local has entered the repository.

Two kinds of check:

*Structural* checks are always on and need no configuration. They look for
absolute local filesystem paths, tracked database files, and committed
environment files.

*Denylist* checks read one term per line from a file (default
``.privacy-denylist``, which is gitignored) and fail if any term appears in a
tracked file. Keep customer and project names there so they never need to be
written into a file that ships with the repository.

Usage:
    python scripts/privacy_check.py [--denylist PATH]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SELF = "scripts/privacy_check.py"

# Absolute paths leak the developer's username and real document locations.
PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/]Users[\\/]|/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/")

# Template payloads embed absolute source-PDF paths, so databases stay untracked.
FORBIDDEN_FILES = re.compile(r"\.sqlite3?$|\.db$|^\.env$|/\.env$|\.pem$|\.key$|id_rsa")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def read_text(path: str) -> str | None:
    try:
        return (REPO_ROOT / path).read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return None  # Binary or unreadable; the filename check still applies.


def load_denylist(path: Path) -> list[str]:
    if not path.is_file():
        return []
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--denylist",
        type=Path,
        default=REPO_ROOT / ".privacy-denylist",
        help="File of terms that must not appear in tracked files.",
    )
    args = parser.parse_args()

    files = tracked_files()
    denylist = load_denylist(args.denylist)
    failures: list[str] = []

    for path in files:
        if FORBIDDEN_FILES.search(path):
            failures.append(f"{path}: file type must not be tracked")

    for path in files:
        if path == SELF:
            continue  # This file necessarily contains the patterns it looks for.
        content = read_text(path)
        if content is None:
            continue

        for lineno, line in enumerate(content.splitlines(), 1):
            if PATH_PATTERN.search(line):
                failures.append(f"{path}:{lineno}: absolute local path")
            for term in denylist:
                if term.lower() in line.lower():
                    # Report the location, never the term itself.
                    failures.append(f"{path}:{lineno}: denylisted term")

    if failures:
        print("Privacy check FAILED:\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(f"\n{len(failures)} problem(s) found.", file=sys.stderr)
        return 1

    scope = f"{len(files)} tracked files"
    detail = f"{len(denylist)} denylist term(s)" if denylist else "no denylist configured"
    print(f"Privacy check passed ({scope}, {detail}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
