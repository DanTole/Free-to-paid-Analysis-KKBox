"""Refuse to commit anything that contains a KKBox user ID (msno).

    python -m src.check_no_msno            # scan every file git would commit
    python -m src.check_no_msno FILE ...   # scan specific files

Scans tracked files plus untracked files that aren't ignored, so new files are
checked before their first commit. A match is any string shaped like an msno
(definitions.MSNO_PATTERN: 44-character base64 ending in "="). When the local
member table exists (data/cache/members.parquet), matches are also checked
against the real IDs and reported as confirmed. Matched IDs are masked in the
output. Exit code 1 on any match, so the pre-commit hook in .githooks/ blocks
the commit.
"""

import re
import subprocess
import sys
from pathlib import Path

from src import definitions as d

PATTERN = re.compile(d.MSNO_PATTERN)
MAX_BYTES = 50_000_000


def find_ids(text: str) -> list[tuple[int, str]]:
    """(line number, match) for every msno-shaped string in text."""
    return [
        (text.count("\n", 0, m.start()) + 1, m.group(0))
        for m in PATTERN.finditer(text)
    ]


def candidate_files(root: Path) -> list[Path]:
    """Tracked files plus untracked files git doesn't ignore."""
    out = []
    for args in (["ls-files", "-z"], ["ls-files", "-z", "--others", "--exclude-standard"]):
        listed = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout
        out += [root / p for p in listed.decode("utf-8").split("\0") if p]
    return sorted({p for p in out if p.is_file()})


def known_ids(root: Path) -> set[str] | None:
    members = root / "data" / "cache" / "members.parquet"
    if not members.exists():
        return None
    import duckdb

    from src.logs import sql_path

    return {r[0] for r in duckdb.connect().execute(f"select msno from read_parquet({sql_path(members)})").fetchall()}


def mask(value: str) -> str:
    return value[:4] + "..." + value[-3:]  # ASCII: the Windows console can't print an ellipsis


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = d.ROOT
    files = [Path(a).resolve() for a in argv] if argv else candidate_files(root)
    hits = []
    for path in files:
        if path.stat().st_size > MAX_BYTES:
            continue
        for line, value in find_ids(path.read_bytes().decode("utf-8", errors="ignore")):
            hits.append((path, line, value))
    if not hits:
        print(f"check_no_msno: {len(files)} files clean")
        return 0
    ids = known_ids(root)
    for path, line, value in hits:
        status = "" if ids is None else (" (confirmed user ID)" if value in ids else " (ID-shaped)")
        print(f"{path.relative_to(root) if path.is_relative_to(root) else path}:{line}: {mask(value)}{status}")
    print(f"check_no_msno: {len(hits)} user-ID-shaped string(s) found; remove them before committing", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
