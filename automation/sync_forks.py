#!/usr/bin/env python3
"""Merge upstream into each fork in sources.yml and push.

A fork is a sources.yml entry with an `upstream` key. Each one is cloned to a
temp dir, upstream is merged into its branch, and the merge is pushed back.

renderMember calls in the fork's docs are then converted to `/// member`
blocks (migrate_rendermember.py) and committed. readguides has no renderMember
macro, so a leftover call breaks the site build.

A fork with a merge conflict, or a call the converter can't handle, isn't
pushed. It goes to $GITHUB_OUTPUT so the workflow can post about it.

Push access comes from the workflow's insteadOf rewrite and ssh-agent. The
URLs in sources.yml are plain HTTPS.

    python automation/sync_forks.py
    python automation/sync_forks.py --dry-run     # don't push
    python automation/sync_forks.py --only mq2nav
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from migrate_rendermember import migrate_file

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources.yml"

# renderMember still inside {{ }} or {% %}
RE_LEFTOVER = re.compile(r"\{[{%][^}]*?\brenderMember\s*\(")
CONVERT_MESSAGE = (
    "docs: replace renderMember with member blocks\n\n"
    "Converted by automation/sync_forks.py after merging upstream."
)
STUCK = ("conflict", "unconverted")


@dataclass
class Fork:
    repo: str
    branch: str
    upstream: str
    upstream_branch: str | None
    slugs: list[str] = field(default_factory=list)
    docs_dirs: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.repo.rstrip("/").rsplit("/", 1)[1].removesuffix(".git")


@dataclass
class Result:
    fork: Fork
    status: str  # unchanged | merged | pushed | conflict | unconverted | error
    detail: str = ""


def load_forks(only: list[str] | None) -> list[Fork]:
    with MANIFEST.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    defaults = data.get("defaults", {})
    forks: dict[str, Fork] = {}
    for raw in data["sources"]:
        if not raw.get("upstream"):
            continue
        if only and raw["slug"] not in only:
            continue
        branch = raw.get("branch", defaults.get("branch", "master"))
        fork = forks.setdefault(
            raw["repo"], Fork(raw["repo"], branch, raw["upstream"], raw.get("upstream_branch")))
        if (fork.branch, fork.upstream) != (branch, raw["upstream"]):
            sys.exit(f"sources.yml: {raw['repo']} has inconsistent branch/upstream across slugs")
        fork.slugs.append(raw["slug"])
        fork.docs_dirs.append(raw.get("docs_dir", defaults.get("docs_dir", "docs")))
    if only and not forks:
        sys.exit(f"--only: none of {', '.join(only)} is a fork")
    return list(forks.values())


def git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def resolve_upstream_branch(fork: Fork, cwd: Path) -> str:
    if fork.upstream_branch:
        return fork.upstream_branch
    for line in git("ls-remote", "--symref", "upstream", "HEAD", cwd=cwd).stdout.splitlines():
        if line.startswith("ref: refs/heads/"):
            return line.split("\t", 1)[0][len("ref: refs/heads/"):]
    heads = git("ls-remote", "--heads", "upstream", cwd=cwd).stdout
    for fallback in ("main", "master"):
        if f"refs/heads/{fallback}" in heads:
            return fallback
    raise RuntimeError(f"cannot determine the default branch of {fork.upstream}")


def convert_render_member(fork: Fork, dest: Path) -> tuple[list[str], list[str]]:
    """Returns (converted files, files that still call renderMember)."""
    converted: list[str] = []
    left: list[str] = []
    for docs_dir in dict.fromkeys(fork.docs_dirs):
        for path in sorted((dest / docs_dir).rglob("*.md")):
            rel = path.relative_to(dest)
            if ".git" in rel.parts or b"renderMember" not in path.read_bytes():
                continue
            try:
                count, _ = migrate_file(path)
                remaining = RE_LEFTOVER.search(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                count, remaining = 0, True
            if count:
                converted.append(rel.as_posix())
            if remaining:
                left.append(rel.as_posix())
    return converted, left


def sync_fork(fork: Fork, workdir: Path, dry_run: bool) -> Result:
    dest = workdir / fork.name
    print(f"::group::{fork.repo} ({fork.branch})  <-  {fork.upstream}", flush=True)
    try:
        # no --depth, the merge needs the common ancestor
        git("clone", "--quiet", "--branch", fork.branch, "--single-branch", fork.repo, str(dest), cwd=workdir)
        git("remote", "add", "upstream", fork.upstream, cwd=dest)
        upstream_branch = resolve_upstream_branch(fork, dest)
        git("fetch", "--quiet", "upstream", upstream_branch, cwd=dest)
        git("config", "user.name", "github-actions[bot]", cwd=dest)
        git("config", "user.email", "github-actions[bot]@users.noreply.github.com", cwd=dest)

        before = git("rev-parse", "HEAD", cwd=dest).stdout.strip()
        merge = git("merge", "--no-edit", f"upstream/{upstream_branch}", cwd=dest, check=False)
        if merge.returncode != 0:
            conflicts = git("diff", "--name-only", "--diff-filter=U", cwd=dest).stdout.split()
            git("merge", "--abort", cwd=dest, check=False)
            files = ", ".join(conflicts) or (merge.stderr.strip().splitlines() or ["merge failed"])[-1]
            detail = f"merge conflict in {files}"
            print(f"::error::{fork.repo}: {detail}")
            return Result(fork, "conflict", detail)
        print(merge.stdout.strip())

        converted, left = convert_render_member(fork, dest)
        if left:
            detail = f"renderMember calls the converter can't handle in {', '.join(left)}"
            print(f"::error::{fork.repo}: {detail}")
            return Result(fork, "unconverted", detail)
        if converted:
            print(f"\nconverted renderMember in {len(converted)} file(s):")
            print("\n".join(f"  {name}" for name in converted))
            git("add", "--", *converted, cwd=dest)
            git("commit", "--quiet", "-m", CONVERT_MESSAGE, cwd=dest)
        note = f", renderMember converted in {len(converted)} file(s)" if converted else ""
        after = git("rev-parse", "HEAD", cwd=dest).stdout.strip()

        print(f"\n{fork.branch} vs upstream/{upstream_branch}:")
        stat_out = git("diff", "--stat", f"upstream/{upstream_branch}...{fork.branch}", cwd=dest).stdout.strip()
        print(stat_out or "(no differences)")

        if before == after:
            return Result(fork, "unchanged")
        count = git("rev-list", "--count", f"{before}..{after}", cwd=dest).stdout.strip()
        if dry_run:
            return Result(fork, "merged", f"{count} new commit(s){note}, not pushed (dry run)")
        git("push", "--quiet", "origin", f"{fork.branch}:{fork.branch}", cwd=dest)
        return Result(fork, "pushed", f"{count} new commit(s){note}")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip().splitlines()
        detail = detail[-1] if detail else f"git {' '.join(exc.cmd[1:3])} failed"
        print(f"::error::{fork.repo}: {detail}")
        return Result(fork, "error", detail)
    except RuntimeError as exc:
        print(f"::error::{fork.repo}: {exc}")
        return Result(fork, "error", str(exc))
    finally:
        print("::endgroup::", flush=True)


def rmtree_force(path: Path) -> None:
    def on_error(func, target, _exc):  # .git has read-only files on Windows
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def write_outputs(stuck: list[Result]) -> None:
    if "GITHUB_OUTPUT" not in os.environ:
        return
    summary = "; ".join(f"{r.fork.repo.removeprefix('https://')} ({r.fork.branch}): {r.detail}" for r in stuck)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
        fh.write(f"conflicts={summary}\n")
        fh.write(f"conflict_count={len(stuck)}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="merge and report, never push")
    parser.add_argument("--only", action="append", metavar="SLUG", help="sync only the fork behind this slug")
    args = parser.parse_args()

    forks = load_forks(args.only)
    print(f"Syncing {len(forks)} fork(s){' (dry run)' if args.dry_run else ''}")
    workdir = Path(tempfile.mkdtemp(prefix="sync-forks-"))
    try:
        results = [sync_fork(f, workdir, args.dry_run) for f in forks]
    finally:
        rmtree_force(workdir)

    print("\nSummary:")
    for r in results:
        line = f"  {r.status:9}  {r.fork.repo}"
        print(f"{line}  {r.detail}" if r.detail else line)

    stuck = [r for r in results if r.status in STUCK]
    errors = [r for r in results if r.status == "error"]
    write_outputs(stuck)
    if stuck:
        print(f"\n{len(stuck)} fork(s) not pushed: merge conflict or renderMember that wouldn't convert.")
    if errors:
        print(f"\n{len(errors)} fork(s) FAILED.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
