#!/usr/bin/env python3
"""Cuts a release, pushes main and deploys it to the droplet in one go (DECISIONS H9).

Run on your own PC, from anywhere in the repo, on `main`:

    python scripts/deploy.py              release (if anything users see changed), push, deploy
    python scripts/deploy.py --dry-run    only say what it would do, and show the release notes
    python scripts/deploy.py --no-deploy  release and push, but leave the server alone
    python scripts/deploy.py --version 2.0.0   choose the number instead of working it out

The release: when `scripts/version.py` gives a newer number than `VERSION` (a `feat:` or `fix:` commit
since the last release), it writes that number to `VERSION`, renames the "Unreleased" section of
CHANGELOG.md to "X.Y.Z (date)" under a new empty "Unreleased", commits only those two files as
`chore(release): vX.Y.Z` and tags it `vX.Y.Z` with the notes. Lines added to the changelog but not
committed yet (work still in progress) stay under the new "Unreleased". With no changelog entries,
the notes list the commit subjects. Only `docs:`/`chore:`/`test:` commits since: no release, it
pushes and deploys under the same number.

The deploy runs `sh scripts/vps_update.sh` on the server over SSH and checks it reports the version.
The server is read from git config (kept out of the repo, set once per clone):
    git config deploy.host root@203.0.113.10
    git config deploy.path /root/sanpascual-hr      (this is the default)
DEPLOY_HOST / DEPLOY_PATH in the environment override them. Standard library only.
"""

import argparse
import datetime
import difflib
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNRELEASED = re.compile(r"^## Unreleased\b")
LABEL = re.compile(r"^[A-Z][A-Za-z ]*:$")  # "Added:", "Fixed:", "Changed:"


class Stop(Exception):
    """Something to sort out before releasing: nothing has been changed yet."""


def _version_script():
    spec = importlib.util.spec_from_file_location("version_script", ROOT / "scripts" / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args, stdin=None):
    done = subprocess.run(
        ["git", *args], cwd=ROOT, input=stdin, capture_output=True, text=True, encoding="utf-8"
    )
    if done.returncode:
        raise Stop(f"git {' '.join(args)} failed:\n{done.stderr.strip()}")
    return done.stdout


def _section(lines):
    """(heading index, end index) of the Unreleased section; the end is the next "## " heading."""
    start = next((i for i, line in enumerate(lines) if UNRELEASED.match(line)), None)
    if start is None:
        raise Stop('CHANGELOG.md has no "## Unreleased" heading to release from.')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def release_changelog(text, version, date, fallback=()):
    """The changelog with its Unreleased entries under "## version (date)" and a new, empty Unreleased.
    fallback: lines to use as the notes when Unreleased has none (the commit subjects).
    Returns (new text, the release notes)."""
    lines = text.split("\n")
    start, end = _section(lines)
    notes = "\n".join(lines[start + 1 : end]).strip("\n")
    if not notes.strip():
        notes = "\n".join(fallback) or "No notes."
    released = ["## Unreleased", "", f"## {version} ({date})", "", *notes.split("\n"), ""]
    return "\n".join(lines[:start] + released + lines[end:]), notes


def carry_uncommitted(committed, working, released):
    """The working copy of the changelog after the release. Lines it has that the committed one does
    not (entries for work not committed yet) go under the new Unreleased, each under the label it was
    under ("Fixed:"). Anything else uncommitted in the changelog has to be sorted out first."""
    old, new = committed.split("\n"), working.split("\n")
    start, end = _section(old)
    carried = {}  # label -> lines, in the order the labels are met
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "insert" or not start < i1 <= end:
            raise Stop(
                "CHANGELOG.md has uncommitted changes outside its Unreleased section, or to lines already"
                " committed. Commit or undo them, then run this again."
            )
        label = next((line for line in reversed(old[start + 1 : i1]) if LABEL.match(line)), "")
        carried.setdefault(label, []).extend(new[j1:j2])
    if not carried:
        return released
    body = []
    for label, block in carried.items():
        block = "\n".join(block).strip("\n").split("\n")
        if label and not (block and block[0] == label):
            body.append(label)
        body += block + [""]
    lines = released.split("\n")
    start, _end = _section(lines)
    return "\n".join(lines[: start + 2] + body + lines[start + 2 :])


def _stage(path, content):
    sha = git("hash-object", "-w", "--stdin", "--path", path, stdin=content).strip()
    git("update-index", "--cacheinfo", f"100644,{sha},{path}")


def _write(path, content):
    """Writes the working copy, keeping its line endings (CRLF on a Windows checkout)."""
    target = ROOT / path
    crlf = target.exists() and b"\r\n" in target.read_bytes()
    target.write_bytes(content.replace("\n", "\r\n" if crlf else "\n").encode("utf-8"))


def _subjects(since):
    """The commit subjects users would notice, for a release with no changelog entries."""
    span = f"{since}..HEAD" if since else "HEAD"
    subjects = git("log", "--no-merges", "--format=%s", span).splitlines()
    skip = re.compile(r"^(docs|chore|test|ci|style|build)(\(|!|:)")
    return [f"- {s}" for s in subjects if not skip.match(s)]


def cut_release(version, dry_run):
    if git("diff", "--cached", "--name-only").strip():
        raise Stop("Some changes are staged (git add). The release commit must hold only VERSION and"
                   " CHANGELOG.md: commit or unstage them first.")
    if git("status", "--porcelain", "--", "VERSION").strip():
        raise Stop("VERSION has uncommitted changes: undo them, this script writes it.")
    today = datetime.date.today()
    date = f"{today:%B} {today.day}, {today.year}"
    committed = git("show", "HEAD:CHANGELOG.md").replace("\r\n", "\n")
    working = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    since = git("log", "-1", "--format=%H", "--", "VERSION").strip()
    released, notes = release_changelog(committed, version, date, _subjects(since))
    working_after = carry_uncommitted(committed, working, released)
    print(f"Release v{version} ({date}):\n\n{notes}\n")
    if dry_run:
        return
    _stage("CHANGELOG.md", released)
    _stage("VERSION", version + "\n")
    _write("CHANGELOG.md", working_after)
    _write("VERSION", version + "\n")
    git("commit", "-q", "-m", f"chore(release): v{version}")
    git("tag", "-a", f"v{version}", "-m", f"Release {version}", "-m", notes)
    print(f"Committed and tagged v{version}.")


def deploy(host, path, version):
    command = f"cd {path} && sh scripts/vps_update.sh"
    print(f"Deploying on {host} ...")
    process = subprocess.Popen(
        ["ssh", "-o", "BatchMode=yes", host, command],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    said = []
    for line in process.stdout:
        print(line, end="")
        said.append(line)
    if process.wait():
        raise Stop(f"The deploy failed on the server (exit {process.returncode}): see above.")
    if not any(f"Deployed v{version} " in line for line in said):
        raise Stop(f"The server did not report v{version}: check the footer and the output above.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="say what would happen, change nothing")
    parser.add_argument("--no-deploy", action="store_true", help="release and push only")
    parser.add_argument("--version", help="the release number, instead of working it out")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # the notes have → and – (a Windows console is cp1252)

    host = os.environ.get("DEPLOY_HOST") or git("config", "--default", "", "--get", "deploy.host").strip()
    path = os.environ.get("DEPLOY_PATH") or git("config", "--default", "/root/sanpascual-hr", "--get", "deploy.path").strip()
    if not host and not (args.no_deploy or args.dry_run):
        raise Stop("No server set. Once per clone:  git config deploy.host root@<droplet IP>")
    if git("rev-parse", "--abbrev-ref", "HEAD").strip() != "main":
        raise Stop("Switch to main first: the server deploys main.")
    git("fetch", "-q", "origin")
    if int(git("rev-list", "--count", "HEAD..origin/main")):
        raise Stop("origin/main has commits this clone does not: git pull first.")

    current = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    version = args.version or _version_script().current_version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise Stop(f"{version} is not MAJOR.MINOR.PATCH.")
    newer = tuple(map(int, version.split("."))) > tuple(map(int, current.split(".")))
    if version != current and not newer:
        raise Stop(f"{version} is not newer than the last release, {current}.")
    if newer:
        cut_release(version, args.dry_run)
    else:
        print(f"Nothing users see changed since v{current}: no new release.")
    if args.dry_run:
        print("Dry run: nothing committed, pushed or deployed.")
        return
    git("push", "-q", "--follow-tags", "origin", "main")
    print("Pushed main" + (f" and v{version}." if newer else "."))
    if not args.no_deploy:
        deploy(host, path, version)


if __name__ == "__main__":
    try:
        main()
    except Stop as problem:
        sys.exit(f"Stopped: {problem}")
