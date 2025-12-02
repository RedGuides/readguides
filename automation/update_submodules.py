#!/usr/bin/env python3
"""
Auto-update git submodules by merging upstream changes. If markdown files
changed, push and open a PR.
"""

import os
import sys
import argparse
import re
from typing import Optional, Tuple, List
from contextlib import suppress

from git import Repo, GitCommandError
from github import Github, Auth
from github.GithubException import GithubException
import gitlab


# ========================
# Configuration
# ========================

def build_config(push_enabled: bool) -> dict:
    return {
        'gh_token': os.environ.get('GH_API_TOKEN', ''),
        'gl_token': os.environ.get('GITLAB_API_TOKEN', ''),
        'dry_run': not push_enabled,
    }


def get_github_client(cfg: dict) -> Optional[Github]:
    token = cfg.get('gh_token')
    return Github(auth=Auth.Token(token)) if token else None


# ========================
# GitHub/GitLab API helpers
# ========================

def parse_github_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    m = re.search(r'github\.com[/:]([^/]+)/([^/.]+)(?:\.git)?$', url)
    return m.groups() if m else None


def get_github_repo_info(cfg: dict, url: str) -> Optional[dict]:
    """Fetch GitHub repo info including parent fork and default branch."""
    if not (parsed := parse_github_owner_repo(url)):
        return None
    
    owner, repo = parsed
    
    # Try with token first, fallback to unauthenticated
    gh = get_github_client(cfg)
    if not gh:
        gh = Github()
    
    with suppress(Exception):
        repo_obj = gh.get_repo(f"{owner}/{repo}")
        return {
            'default_branch': repo_obj.default_branch,
            'parent': {'full_name': repo_obj.parent.full_name} if repo_obj.parent else None
        }


def get_remote_default_branch(repo: Repo, remote_name: str = 'origin') -> str:
    with suppress(GitCommandError):
        for ln in repo.git.remote('show', remote_name).splitlines():
            if 'HEAD branch:' in ln:
                return ln.split(':', 1)[1].strip()
    return 'main'


def create_github_pr(cfg: dict, owner: str, repo_name: str, head: str, base: str, title: str, body: str) -> Optional[str]:
    gh = get_github_client(cfg)
    if not gh:
        print('GH_API_TOKEN not set; cannot create GitHub PR.')
        return None
    try:
        repo = gh.get_repo(f"{owner}/{repo_name}")
        pr = repo.create_pull(title=title, body=body, head=head, base=base, maintainer_can_modify=True)
        return pr.html_url
    except GithubException as e:
        print(f"GitHub PR creation failed ({e.status}): {e.data or e}")
        return None


def find_existing_github_pr(cfg: dict, owner: str, repo_name: str, head_branch: str, base_branch: str) -> Optional[str]:
    gh = get_github_client(cfg)
    if not gh:
        return None
    with suppress(Exception):
        repo = gh.get_repo(f"{owner}/{repo_name}")
        return next((pr.html_url for pr in repo.get_pulls(state='open', base=base_branch) 
                    if pr.head.ref == head_branch), None)


def discover_github_upstream(cfg: dict, origin_url: str) -> Optional[str]:
    info = get_github_repo_info(cfg, origin_url)
    if not info:
        return None
    
    parent = info.get('parent')
    parent_full_name = parent.get('full_name') if parent else None
    if parent_full_name:
        upstream_url = f"git@github.com:{parent_full_name}.git"
        print(f"Found GitHub upstream: {upstream_url}")
        return upstream_url
    print("No parent fork detected")
    return None


def discover_gitlab_upstream(cfg: dict, origin_url: str) -> Optional[str]:
    m = re.search(r'gitlab\.com[/:]([^/]+)/([^/.]+)(?:\.git)?$', origin_url)
    if not m:
        return None
    
    project_path = f"{m.group(1)}/{m.group(2)}"
    with suppress(Exception):
        gl = gitlab.Gitlab(private_token=cfg.get('gl_token'))
        project = gl.projects.get(project_path)
        forked_from = getattr(project, 'forked_from_project', None)
        if isinstance(forked_from, dict) and (parent_ns := forked_from.get('path_with_namespace')):
            upstream_url = f"git@gitlab.com:{parent_ns}.git"
            print(f"Found GitLab upstream: {upstream_url}")
            return upstream_url
    return None


def get_upstream_default_branch(subrepo: Repo) -> str:
    # Ask the remote for its HEAD ref first (no local refs needed)
    with suppress(GitCommandError):
        for line in subrepo.git.ls_remote('--symref', 'upstream', 'HEAD').splitlines():
            if line.startswith('ref:'):
                return line.split('\t', 1)[0].replace('ref: refs/heads/', '').strip()
    # Fall back to common names
    for fallback in ('main', 'master'):
        with suppress(GitCommandError):
            subrepo.git.show_ref('--verify', f"refs/remotes/upstream/{fallback}")
            return fallback
    return 'main'


def discover_upstream_url(cfg: dict, origin_url: str) -> Optional[str]:
    if 'github.com' in origin_url:
        return discover_github_upstream(cfg, origin_url)
    if 'gitlab.com' in origin_url:
        return discover_gitlab_upstream(cfg, origin_url)
    return None


# ========================
# Submodule operations
# ========================

def get_submodules(repo: Repo) -> dict:
    return {sm.name: {'path': sm.path, 'branch': sm.branch_name or ''} 
            for sm in repo.submodules}


def determine_working_branch(subrepo: Repo, desired_branch: str) -> str:
    if desired_branch:
        return desired_branch
    # Try origin's default
    origin_default = get_remote_default_branch(subrepo, 'origin')
    if origin_default != 'main':  # get_remote_default_branch returns 'main' as fallback
        return origin_default
    # Fall back to current branch or 'main'
    with suppress(Exception):
        return subrepo.active_branch.name  # type: ignore
    return 'main'


def ensure_checked_out_branch(subrepo: Repo, branch: str):
    origin_ref = f"origin/{branch}"
    origin_refs = [r.name for r in subrepo.remotes.origin.refs]  # type: ignore
    with suppress(GitCommandError):
        if origin_ref in origin_refs:
            subrepo.git.checkout('-B', branch, origin_ref)
        else:
            subrepo.git.checkout('-B', branch)
    with suppress(GitCommandError):
        subrepo.git.checkout(branch)


def push_submodule(subrepo: Repo, branch: str) -> bool:
    print(f"Pushing to origin {branch}")
    try:
        subrepo.remotes.origin.push(f"{branch}:{branch}")  # type: ignore
        return True
    except GitCommandError as e:
        print(f"Failed to push: {e}")
        return False


def get_submodule_initial_commit(path: str) -> Optional[str]:
    """Get the current commit of a submodule, or None if not initialized."""
    if not os.path.exists(os.path.join(path, '.git')):
        return None
    with suppress(Exception):
        return Repo(path).head.commit.hexsha


def update_single_submodule(cfg: dict, name: str, path: str, desired_branch: str, commit_before: Optional[str]) -> Tuple[bool, dict]:
    print(f"::group::Processing submodule '{name}' at '{path}'")
    
    def _empty_result(success=True):
        return success, {
            'name': name, 'path': path, 'branch': desired_branch or '',
            'upstream_url': '', 'has_new_commits': False,
            'ahead_count': 0, 'changed_files': []
        }
    
    try:
        if not os.path.exists(os.path.join(path, '.git')):
            print(f"Skipping '{path}' (not initialized)")
            return _empty_result()

        subrepo = Repo(path)

        try:
            origin_url = subrepo.remotes.origin.url  # type: ignore
        except Exception:
            print(f"Could not get origin URL for {path}")
            return _empty_result(False)
        
        print(f"Origin: {origin_url}")

        try:
            subrepo.remotes.origin.fetch(prune=True)  # type: ignore
        except GitCommandError as e:
            print(f"Failed to fetch origin: {e}")
            return _empty_result(False)

        local_branch = determine_working_branch(subrepo, desired_branch)
        print(f"Branch: {local_branch}")
        ensure_checked_out_branch(subrepo, local_branch)

        # Discover or use existing upstream
        upstream_url = ''
        with suppress(Exception):
            upstream_url = subrepo.remotes.upstream.url  # type: ignore
        
        if upstream_url:
            print(f"Using upstream: {upstream_url}")
        else:
            upstream_url = discover_upstream_url(cfg, origin_url) or ''
            if upstream_url:
                with suppress(GitCommandError):
                    subrepo.create_remote('upstream', upstream_url)
                    print(f"Added upstream: {upstream_url}")

        # Merge from upstream (if fork) or origin (if canonical)
        if upstream_url:
            upstream_branch = get_upstream_default_branch(subrepo)
            print(f"Fetching upstream, branch '{upstream_branch}'")
            try:
                subrepo.remotes.upstream.fetch(prune=True)  # type: ignore
            except GitCommandError as e:
                print(f"Failed to fetch upstream: {e}")
                return _empty_result(False)

            # Find the upstream branch (with fallback logic)
            upstream_refs = [r.name for r in subrepo.remotes.upstream.refs]  # type: ignore
            if f"upstream/{upstream_branch}" not in upstream_refs:
                print(f"Warning: upstream/{upstream_branch} not found, looking for alternatives...")
                for fallback in ['main', 'master']:
                    if f"upstream/{fallback}" in upstream_refs:
                        upstream_branch = fallback
                        print(f"Using upstream/{fallback}")
                        break
                else:
                    print(f"::error::Upstream branch not found for '{path}'")
                    return _empty_result(False)

            # Merge upstream into local branch
            try:
                subrepo.git.merge('--no-edit', f"upstream/{upstream_branch}")
                print(f"Merged upstream/{upstream_branch}")
            except GitCommandError as e:
                print(f"::error::Merge conflict in '{path}': {e}")
                return _empty_result(False)
        else:
            # No upstream means this is the canonical repo - merge from origin instead
            print("No upstream detected; treating as canonical repo")
            try:
                subrepo.git.merge('--no-edit', '--ff-only', f"origin/{local_branch}")
                print(f"Fast-forwarded to origin/{local_branch}")
            except GitCommandError as e:
                print(f"::error::Merge error from origin/{local_branch} in '{path}': {e}")
                return _empty_result(False)

        # Detect changes by comparing to initial commit (passed from caller)
        commit_after = subrepo.head.commit.hexsha
        ahead_count = 0
        changed_files = []
        
        if commit_before is not None and commit_before != commit_after:
            # We pulled in new commits - get the diff
            with suppress(GitCommandError):
                changed_files = subrepo.git.diff('--name-only', f"{commit_before}..{commit_after}").splitlines()
                changed_files = [f.strip() for f in changed_files if f.strip()]
                result = subrepo.git.rev_list('--count', f"{commit_before}..{commit_after}")
                ahead_count = int(result) if result else 0
                print(f"Pulled {ahead_count} new commit(s)")

        return True, {
            'name': name, 'path': path, 'branch': local_branch,
            'upstream_url': upstream_url,
            'has_new_commits': ahead_count > 0,
            'ahead_count': ahead_count,
            'changed_files': changed_files,
        }
    finally:
        print("::endgroup::")


# ========================
# Superproject operations
# ========================

def commit_superproject_changes_and_open_pr(cfg: dict, super_repo: Repo, updated_modules: List[dict]) -> bool:
    try:
        # Checkout base branch
        base_branch = get_remote_default_branch(super_repo, 'origin')
        with suppress(GitCommandError):
            super_repo.remotes.origin.fetch(prune=True)  # type: ignore
        with suppress(GitCommandError):
            super_repo.git.checkout('-B', base_branch, f"origin/{base_branch}")
        
        # Create/checkout rolling update branch
        new_branch = "auto/submodule-updates"
        try:
            super_repo.git.checkout('-B', new_branch, f"origin/{base_branch}")
            print(f"Created/reset '{new_branch}' from 'origin/{base_branch}'")
        except GitCommandError as e:
            print(f"Failed to create or reset branch {new_branch}: {e}")
            return False

        # Stage changed submodule paths
        changed_paths = sorted({r['path'] for r in updated_modules})
        if not changed_paths:
            print('No submodule paths to stage.')
            return True
        
        for p in changed_paths:
            with suppress(GitCommandError):
                super_repo.git.add('--', p)

        if not super_repo.is_dirty(index=True, working_tree=True, untracked_files=False):
            print('Superproject has no changes to commit.')
            return True

        # Commit changes
        title = 'Update submodule references'
        body = f"Automated update of submodule references.\n\nUpdated paths:\n" + \
               "\n".join(f"- {p}" for p in changed_paths)
        
        try:
            super_repo.index.commit(title)
        except Exception as e:
            print(f"Failed to commit: {e}")
            return False

        if cfg['dry_run']:
            print(f"Dry run: would push '{new_branch}' and open PR -> '{base_branch}'")
            return True

        # Push branch (force-with-lease since this is an automation-only branch)
        try:
            super_repo.git.push('origin', '--force-with-lease', f"{new_branch}:{new_branch}")
        except GitCommandError as e:
            print(f"Failed to push: {e}")
            return False

        # Create or update PR
        origin_url = ''
        with suppress(Exception):
            origin_url = super_repo.remotes.origin.url  # type: ignore
        
        if owner_repo := parse_github_owner_repo(origin_url):
            owner, repo_name = owner_repo
            
            # Check if PR already exists
            if existing_pr := find_existing_github_pr(cfg, owner, repo_name, new_branch, base_branch):
                print(f"Updated existing PR: {existing_pr}")
            else:
                # Create new PR
                if pr_url := create_github_pr(cfg, owner, repo_name, new_branch, base_branch, title, body):
                    print(f"Opened PR: {pr_url}")
                    # Output PR URL for GitHub Actions
                    if 'GITHUB_OUTPUT' in os.environ:
                        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                            f.write(f"new_pr_created=true\n")
                            f.write(f"pr_url={pr_url}\n")
                            f.write(f"repo_url=https://github.com/{owner}/{repo_name}\n")
                            f.write(f"script_url=https://github.com/{owner}/{repo_name}/blob/{base_branch}/automation/update_submodules.py\n")
                else:
                    print("Failed to create PR")
        else:
            print('Origin is not a GitHub URL; skipping PR.')
        
        return True
    except Exception as e:
        print(f"Error creating PR: {e}")
        return False


# ========================
# Update workflow
# ========================

def update_all_submodules(cfg: dict) -> bool:
    repo = Repo('.', search_parent_directories=True)
    submods = get_submodules(repo)
    if not submods:
        print('No submodules found.')
        return True

    # Cache initial commits for all submodules
    print(f"Caching initial commits for {len(submods)} submodule(s)...")
    initial_commits = {name: get_submodule_initial_commit(info['path']) 
                      for name, info in submods.items()}

    # Update each submodule
    results = []
    for name, info in submods.items():
        ok, meta = update_single_submodule(cfg, name, info['path'], info['branch'], initial_commits[name])
        if not ok:
            return False
        results.append(meta)

    # Check for updates and markdown changes
    updated_modules = [r for r in results if r.get('has_new_commits')]
    any_md_changed = any(
        any(f.endswith('.md') for f in r.get('changed_files', []))
        for r in updated_modules
    )

    # Report updated modules
    if updated_modules:
        print(f'\nUpdated {len(updated_modules)} submodule(s):')
        for r in updated_modules:
            ahead = r['ahead_count']
            md_count = len([f for f in r.get('changed_files', []) if f.endswith('.md')])
            status = f"+{ahead} commits" + (f", {md_count} .md" if md_count else "")
            print(f"  - {r['name']}: {status}")
    else:
        print('No submodules ahead of origin.')

    # Dry run reporting
    if cfg['dry_run']:
        print(f"\nDry run: {len(updated_modules)} updated, .md changes = {any_md_changed}")
        if any_md_changed and updated_modules:
            print('  -> Would push submodules and open PR')
        else:
            print('  -> Would not push or open PR')
        return True

    # Push and create PR if markdown changed
    if any_md_changed and updated_modules:
        print(f'\nMarkdown changes detected; pushing {len(updated_modules)} submodule(s)...')
        for r in updated_modules:
            if not push_submodule(Repo(r['path']), r['branch']):  # type: ignore
                return False
        print('Opening PR for submodule pointer updates...')
        return commit_superproject_changes_and_open_pr(cfg, repo, updated_modules)
    
    print('No .md changes; skipping push/PR.')
    return True


# ========================
# CLI
# ========================

def main():
    parser = argparse.ArgumentParser(
        description='Merge upstream into submodules; if .md changed, push and open a PR.'
    )
    parser.add_argument('--dry-run', '--no-push', dest='no_push', action='store_true',
                       help='Do everything except push/PR/forum post')
    args = parser.parse_args()

    cfg = build_config(push_enabled=not args.no_push)
    sys.exit(0 if update_all_submodules(cfg) else 1)


if __name__ == '__main__':
    main()