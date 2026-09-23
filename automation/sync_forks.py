#!/usr/bin/env python3
"""Merge upstream into every fork listed in sources.yml and push the result.

A fork is any manifest entry with an ``upstream`` key. Each fork is cloned with
full history into a temporary directory, the upstream branch is merged into the
branch named in the manifest, and the merge is pushed back to the fork. A
conflict aborts the merge for that fork only; it is reported in the summary and
written to $GITHUB_OUTPUT so the workflow can ask a human for help. Nothing in
this repository is modified.

Authentication is left to git: the manifest holds plain HTTPS URLs and the
workflow's insteadOf rewrites plus SSH agent supply push access.

Usage:
    python automation/sync_forks.py             # merge and push
    python automation/sync_forks.py --dry-run   # merge and report, never push
    python automation/sync_forks.py --only mq2nav
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources.yml"


@dataclass
class Fork:
    repo: str
    branch: str
    upstream: str
    upstream_branch: str | None
    slugs: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.repo.rstrip("/").rsplit("/", 1)[1].removesuffix(".git")


@dataclass
class Result:
    fork: Fork
    status: str  # unchanged | merged | pushed | conflict | error
    detail: str = ""


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

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
    if only and not forks:
        sys.exit(f"--only: none of {', '.join(only)} is a fork")
    return list(forks.values())


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

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


def sync_fork(fork: Fork, workdir: Path, dry_run: bool) -> Result:
    dest = workdir / fork.name
    print(f"::group::{fork.repo} ({fork.branch})  <-  {fork.upstream}", flush=True)
    try:
        # Full history on both sides: a merge needs the common ancestor.
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
            detail = ", ".join(conflicts) or (merge.stderr.strip().splitlines() or ["merge failed"])[-1]
            print(f"::error::merge conflict in {fork.repo}: {detail}")
            return Result(fork, "conflict", detail)
        print(merge.stdout.strip())
        after = git("rev-parse", "HEAD", cwd=dest).stdout.strip()

        print(f"\n--- what {fork.branch} carries relative to upstream/{upstream_branch}:")
        stat_out = git("diff", "--stat", f"upstream/{upstream_branch}...{fork.branch}", cwd=dest).stdout.strip()
        print(stat_out or "(nothing: identical to upstream)")

        if before == after:
            return Result(fork, "unchanged")
        count = git("rev-list", "--count", f"{before}..{after}", cwd=dest).stdout.strip()
        if dry_run:
            return Result(fork, "merged", f"{count} new commit(s), not pushed (dry run)")
        git("push", "--quiet", "origin", f"{fork.branch}:{fork.branch}", cwd=dest)
        return Result(fork, "pushed", f"{count} new commit(s)")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def rmtree_force(path: Path) -> None:
    def on_error(func, target, _exc):  # read-only files inside .git on Windows
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


def write_outputs(results: list[Result]) -> None:
    conflicts = [r for r in results if r.status == "conflict"]
    summary = "; ".join(f"{r.fork.repo.removeprefix('https://')} ({r.fork.branch}): {r.detail}" for r in conflicts)
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"conflicts={summary}\n")
            fh.write(f"conflict_count={len(conflicts)}\n")


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
    write_outputs(results)

    errors = [r for r in results if r.status == "error"]
    conflicts = [r for r in results if r.status == "conflict"]
    if conflicts:
        print(f"\n{len(conflicts)} fork(s) need a human to resolve a merge conflict.")
    if errors:
        print(f"\n{len(errors)} fork(s) FAILED for reasons other than a conflict.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
