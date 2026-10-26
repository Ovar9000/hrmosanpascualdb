#!/usr/bin/env python3
"""Works out the version to show in the footer from git, so nobody has to remember to bump it (DECISIONS H9).

Starts from the committed VERSION file (the last release) and reads the commit messages since the
commit that last changed it:
- `feat!:` / `fix(x)!:` or "BREAKING CHANGE" in a message: next MAJOR (2.0.0)
- `feat:` or `feat(scope):`: next MINOR (1.2.0)
- `fix:`, `perf:`, `refactor:` and any message without a type: next PATCH (1.1.2)
- `docs:`, `chore:`, `test:`, `ci:`, `style:`, `build:`: no new version (nothing users see changed)
Merge commits are skipped; the commits they bring in are counted.

Usage:  python3 scripts/version.py            prints the version, e.g. 1.2.0
        python3 scripts/version.py --write    also writes it to RELEASE (git-ignored), which the
                                              app reads before VERSION. scripts/vps_update.sh does this.
Standard library only: it runs on the server before the image is built.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = re.compile(r"^(?P<type>[A-Za-z]+)(\([^)]*\))?(?P<bang>!)?:")
NO_RELEASE = {"docs", "chore", "test", "ci", "style", "build"}
RANK = {"patch": 1, "minor": 2, "major": 3}


def next_version(base, messages):
    """The version after `base` (MAJOR.MINOR.PATCH) once the commits with these messages are in."""
    major, minor, patch = (int(part) for part in base.strip().split("."))
    bump = None
    for message in messages:
        subject = message.strip().splitlines()[0] if message.strip() else ""
        match = HEADER.match(subject)
        if (match and match["bang"]) or "BREAKING CHANGE" in message:
            kind = "major"
        elif match and match["type"].lower() == "feat":
            kind = "minor"
        elif match and match["type"].lower() in NO_RELEASE:
            continue
        else:
            kind = "patch"
        if bump is None or RANK[kind] > RANK[bump]:
            bump = kind
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    return f"{major}.{minor}.{patch}"


def _git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def current_version():
    base = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    released_in = _git("log", "-1", "--format=%H", "--", "VERSION").strip()
    span = f"{released_in}..HEAD" if released_in else "HEAD"
    raw = _git("log", "--no-merges", "--format=%B%x00", span)
    return next_version(base, [m for m in raw.split("\x00") if m.strip()])


if __name__ == "__main__":
    version = current_version()
    if "--write" in sys.argv[1:]:
        (ROOT / "RELEASE").write_text(version + "\n", encoding="utf-8")
    print(version)
