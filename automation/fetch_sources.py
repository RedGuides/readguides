#!/usr/bin/env python3
"""Fetch every documentation source listed in sources.yml into docs/projects/.

Each repo is shallow-cloned into a temp dir; each slug's docs_dir is copied to
docs/projects/<slug>, then the clone is deleted. docs/projects/ is git-ignored:
a build product, overwritten on every run.

Sources that require credentials can be skipped with --skip-private 

Usage:
    python automation/fetch_sources.py                     # everything
    python automation/fetch_sources.py --skip-private      # public sources only, no credentials needed
    python automation/fetch_sources.py --only mq2nav --only aqo
    python automation/fetch_sources.py --check             # validate the manifest, clone nothing
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
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "sources.yml"
PROJECTS = ROOT / "docs" / "projects"
RETRIES = 2
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
KNOWN_KEYS = {"slug", "repo", "branch", "docs_dir", "upstream", "upstream_branch", "private"}

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


@dataclass
class Source:
    slug: str
    repo: str
    branch: str
    docs_dir: str
    upstream: str | None = None
    private: bool = False  # needs credentials to read; --skip-private leaves it out


@dataclass
class RepoJob:
    repo: str
    branch: str
    slugs: list[Source] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest(only: list[str] | None) -> list[Source]:
    with MANIFEST.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    defaults = data.get("defaults", {})
    sources: list[Source] = []
    seen: set[str] = set()
    private_by_repo: dict[str, bool] = {}
    for raw in data["sources"]:
        unknown = set(raw) - KNOWN_KEYS
        if unknown:
            sys.exit(f"sources.yml: {raw.get('slug', raw)}: unknown key(s) {', '.join(sorted(unknown))}")
        for key in ("slug", "repo"):
            if not raw.get(key):
                sys.exit(f"sources.yml: entry {raw} is missing {key!r}")
        if not isinstance(raw.get("private", False), bool):
            sys.exit(f"sources.yml: {raw['slug']}: private must be true or false")
        src = Source(
            slug=str(raw["slug"]),
            repo=str(raw["repo"]),
            branch=str(raw.get("branch", defaults.get("branch", "master"))),
            docs_dir=str(raw.get("docs_dir", defaults.get("docs_dir", "docs"))),
            upstream=raw.get("upstream"),
            private=raw.get("private", False),
        )
        if src.slug in seen:
            sys.exit(f"sources.yml: duplicate slug {src.slug!r}")
        if private_by_repo.setdefault(src.repo, src.private) != src.private:
            sys.exit(f"sources.yml: {src.slug}: every entry for {src.repo} must agree on private")
        if not SLUG_RE.fullmatch(src.slug):
            sys.exit(f"sources.yml: slug {src.slug!r} must be lowercase letters, digits, - or _")
        if not re.match(r"https://", src.repo):
            sys.exit(f"sources.yml: {src.slug}: repo must be an https:// URL, got {src.repo!r}")
        if ".." in src.docs_dir.split("/") or src.docs_dir.startswith("/"):
            sys.exit(f"sources.yml: {src.slug}: docs_dir must be a relative path inside the repo")
        seen.add(src.slug)
        sources.append(src)
    if only:
        missing = set(only) - seen
        if missing:
            sys.exit(f"--only: unknown slug(s): {', '.join(sorted(missing))}")
        sources = [s for s in sources if s.slug in only]
    return sources


def group_by_repo(sources: list[Source]) -> list[RepoJob]:
    jobs: dict[tuple[str, str], RepoJob] = {}
    for src in sources:
        jobs.setdefault((src.repo, src.branch), RepoJob(src.repo, src.branch)).slugs.append(src)
    return list(jobs.values())


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def with_retries(what: str, fn) -> None:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 2):
        try:
            fn()
            return
        except RuntimeError as exc:  # network flake, retry
            last = exc
            if attempt <= RETRIES:
                log(f"  retry {attempt}/{RETRIES} for {what}")
    raise RuntimeError(f"{what}: {last}")


def rmtree_force(path: Path) -> None:
    def on_error(func, target, _exc):  # read-only objects inside .git on Windows
        os.chmod(target, stat.S_IWRITE)
        func(target)
    shutil.rmtree(path, onerror=on_error)


# ---------------------------------------------------------------------------
# Publish into docs/projects
# ---------------------------------------------------------------------------

def publish(src: Source, checkout: Path) -> None:
    source_dir = checkout if src.docs_dir == "." else checkout / src.docs_dir
    if not source_dir.is_dir():
        raise RuntimeError(f"{src.slug}: {src.docs_dir!r} does not exist in {src.repo} ({src.branch})")
    target = PROJECTS / src.slug
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        rmtree_force(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target, symlinks=False, ignore=shutil.ignore_patterns(".git"))


def run_job(job: RepoJob, workdir: Path) -> None:
    # Unique per (repo, branch) job, so parallel clones never collide.
    dest = workdir / re.sub(r"[^A-Za-z0-9._-]+", "-", f"{job.repo.removeprefix('https://')}-{job.branch}")

    def clone() -> None:
        if dest.exists():
            rmtree_force(dest)
        git("clone", "--quiet", "--depth=1", "--single-branch", "--branch", job.branch, job.repo, str(dest))

    with_retries(f"clone {job.repo} ({job.branch})", clone)
    try:
        commit = git("rev-parse", "HEAD", cwd=dest)
        for src in job.slugs:
            publish(src, dest)
    finally:
        rmtree_force(dest)
    log(f"  {commit[:10]}  {job.repo}  ->  {', '.join(s.slug for s in job.slugs)}")


# ---------------------------------------------------------------------------
# --check: validate the manifest without cloning
# ---------------------------------------------------------------------------

def check_job(job: RepoJob) -> str | None:
    """Return a problem description, or None when the branch exists."""
    try:
        refs = git("ls-remote", "--heads", job.repo, job.branch)
    except RuntimeError as exc:
        reason = str(exc).splitlines()[-1]
        if job.slugs[0].private:  # load_manifest makes every slug of a repo agree
            return f"{job.repo}: needs credentials ({reason}); run with --skip-private to leave private sources out"
        return (f"{job.repo}: unreachable ({reason}); fix the URL, or mark its sources.yml entries "
                f"'private: true' and run with --skip-private if it needs credentials")
    if not refs:
        return f"{job.repo}: branch {job.branch!r} does not exist"
    return None


def run_check(jobs: list[RepoJob], workers: int) -> int:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        problems = [p for p in pool.map(check_job, jobs) if p]
    for p in problems:
        log(f"::error::{p}")
    log(f"Checked {len(jobs)} repo(s): {len(problems)} error(s)")
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", action="append", metavar="SLUG",
                        help="fetch only this slug (repeatable)")
    parser.add_argument("--check", action="store_true",
                        help="validate sources.yml and that each repo/branch exists; clone nothing")
    parser.add_argument("--skip-private", action="store_true",
                        help="leave out sources marked private: true (they need credentials to read)")
    parser.add_argument("--jobs", type=int, default=8, help="parallel git operations (default 8)")
    args = parser.parse_args()

    sources = load_manifest(args.only)
    skipped: list[Source] = []
    if args.skip_private:
        skipped = [s for s in sources if s.private]
        sources = [s for s in sources if not s.private]
    jobs = group_by_repo(sources)
    if args.check:
        log(f"sources.yml: {len(sources) + len(skipped)} slug(s), {len(jobs)} repo(s) to check, "
            f"{sum(1 for s in sources if s.upstream)} fork(s), {len(skipped)} private slug(s) skipped")
        return run_check(jobs, args.jobs)

    if skipped:
        log(f"Skipping {len(skipped)} private source(s): {', '.join(s.slug for s in skipped)}")
    if not sources:
        log("Nothing to fetch.")
        return 0
    log(f"Fetching {len(sources)} slug(s) from {len(jobs)} repo(s) with {args.jobs} worker(s)")
    workdir = Path(tempfile.mkdtemp(prefix="readguides-sources-"))
    failures: list[str] = []
    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            for job, future in [(j, pool.submit(run_job, j, workdir)) for j in jobs]:
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - report every failure, then exit non-zero
                    failures.append(f"{job.repo} ({job.branch}): {exc}")
    finally:
        rmtree_force(workdir)

    if failures:
        log(f"\n{len(failures)} source(s) FAILED:")
        for line in failures:
            log(f"  - {line}")
        return 1
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
