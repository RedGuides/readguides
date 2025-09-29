#!/usr/bin/env python3
"""
WARNING: This is LLM spit and hasn't been reviewed closely. - Redbot
"""

"""
- Discover submodules and intended branches (from .gitmodules)
- For each submodule:
  - Fetch origin, determine/checkout working branch
  - Optionally discover/add upstream (GitHub/GitLab APIs)
  - Fetch upstream and merge its default branch
  - Compute ahead-of-origin commit count and changed files
  - Track session (pre->post HEAD) changed files to catch fast-forwards
- If any .md changed, push updated submodules, create a superproject branch, commit submodule pointers, open a GitHub PR, and post to RedGuides
- Supports --dry-run / --no-push

Requires: GitPython, PyGithub, python-gitlab, requests
"""

import os
import sys
import argparse
import json
import re
import urllib.request
import urllib.parse
from typing import Dict, Optional, Tuple, List
from datetime import datetime

from git import Repo, GitCommandError
from github import Github, Auth
from github.GithubException import GithubException
import gitlab
from gitlab.exceptions import GitlabError
import requests


# ========================
# Configuration and logging
# ========================

def build_config(push_enabled: bool) -> Dict[str, object]:
    gh_token = os.environ.get('GH_API_TOKEN', '').strip()
    gl_token = os.environ.get('GITLAB_API_TOKEN', '').strip()
    gh_api = os.environ.get('GH_API', 'https://api.github.com')
    gl_api = os.environ.get('GL_API', 'https://gitlab.com/api/v4')
    dry_run = not push_enabled

    xf_api_key = os.environ.get('XF_DONOTREPLY_KEY', '').strip()
    xf_api_user = os.environ.get('XF_API_USER', '7384').strip() or '7384'
    xf_base_url = os.environ.get('XF_BASE_URL', 'https://www.redguides.com/community/api').strip()
    try:
        xf_thread_id = int(os.environ.get('XF_THREAD_ID', '95078').strip())
    except Exception:
        xf_thread_id = 95078

    return {
        'gh_token': gh_token,
        'gl_token': gl_token,
        'gh_api': gh_api,
        'gl_api': gl_api,
        'push_enabled': push_enabled,
        'dry_run': dry_run,
        'xf_api_key': xf_api_key,
        'xf_api_user': xf_api_user,
        'xf_base_url': xf_base_url,
        'xf_thread_id': xf_thread_id,
    }


def start_log_group(title: str):
    print(f"::group::{title}")


def end_log_group():
    print("::endgroup::")


def log_error(message: str):
    print(f"::error::{message}")


# ========================
# HTTP utilities
# ========================

def http_get_json(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[dict]:
    headers = dict(headers or {})
    headers.setdefault('User-Agent', 'readguides-submodule-updater/1.0')
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"API request failed for {url}: {e}")
        return None


# ========================
# GitHub, GitLab, RedGuides helpers
# ========================

def parse_github_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    m = re.search(r'github\.com[/:]([^/]+)/([^/.]+)(?:\.git)?$', url)
    if not m:
        return None
    return m.group(1), m.group(2)


def get_remote_default_branch(repo: Repo, remote_name: str = 'origin') -> str:
    try:
        remote_show = repo.git.remote('show', remote_name)
        for ln in remote_show.splitlines():
            if 'HEAD branch:' in ln:
                return ln.split(':', 1)[1].strip()
    except GitCommandError:
        pass
    return 'main'


def create_github_pr(cfg: Dict[str, object], owner: str, repo_name: str, head: str, base: str, title: str, body: str) -> Optional[str]:
    gh_token = str(cfg.get('gh_token') or '')
    gh_api = str(cfg.get('gh_api') or 'https://api.github.com')
    if not gh_token:
        print('GH_API_TOKEN not set; cannot create GitHub PR.')
        return None
    try:
        auth = Auth.Token(gh_token)
        gh = Github(auth=auth, base_url=gh_api)
        repo = gh.get_repo(f"{owner}/{repo_name}")
        pr = repo.create_pull(title=title, body=body, head=head, base=base, maintainer_can_modify=True)
        return pr.html_url
    except GithubException as e:
        status = getattr(e, 'status', 'unknown')
        data = getattr(e, 'data', {})
        print(f"GitHub PR creation failed (status {status}): {data or e}")
        return None
    except Exception as e:
        print(f"Unexpected error creating PR: {e}")
        return None


def find_existing_github_pr(cfg: Dict[str, object], owner: str, repo_name: str, head_branch: str, base_branch: str) -> Optional[str]:
    gh_token = str(cfg.get('gh_token') or '')
    gh_api = str(cfg.get('gh_api') or 'https://api.github.com')
    if not gh_token:
        return None
    try:
        auth = Auth.Token(gh_token)
        gh = Github(auth=auth, base_url=gh_api)
        repo = gh.get_repo(f"{owner}/{repo_name}")
        open_prs = repo.get_pulls(state='open', base=base_branch)
        for pr in open_prs:
            try:
                if getattr(pr.head, 'ref', '') == head_branch:
                    return pr.html_url
            except Exception:
                continue
        return None
    except GithubException:
        return None
    except Exception:
        return None

def post_redguides_reply(cfg: Dict[str, object], message: str) -> bool:
    api_key = str(cfg.get('xf_api_key') or '').strip()
    api_user = str(cfg.get('xf_api_user') or '').strip()
    base_url = str(cfg.get('xf_base_url') or '').strip()
    thread_id = int(cfg.get('xf_thread_id') or 0)
    if not api_key or not api_user or not base_url or not thread_id:
        print('RedGuides/XenForo config incomplete; skipping forum post.')
        return False
    headers = {
        'XF-Api-Key': api_key,
        'XF-Api-User': api_user,
        'Accept': 'application/json'
    }
    data = {
        'thread_id': thread_id,
        'message': message
    }
    try:
        resp = requests.post(f"{base_url}/posts/", headers=headers, data=data, timeout=30)
        if 200 <= resp.status_code < 300:
            print('Posted RedGuides reply successfully.')
            return True
        print(f"Failed to post RedGuides reply: {resp.status_code} {resp.text}")
        return False
    except Exception as e:
        print(f"Error posting RedGuides reply: {e}")
        return False


def discover_github_upstream(cfg: Dict[str, object], origin_url: str) -> Optional[str]:
    m = re.search(r'github\.com[/:]([^/]+)/([^/.]+)(?:\.git)?$', origin_url)
    if not m:
        return None
    owner, repo = m.groups()
    print(f"Querying GitHub API for fork parent of {owner}/{repo}")

    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'readguides-submodule-updater/1.0'
    }
    gh_token = str(cfg.get('gh_token') or '')
    gh_api = str(cfg.get('gh_api') or 'https://api.github.com')
    if gh_token:
        headers['Authorization'] = f'Bearer {gh_token}'

    api_resp = http_get_json(f"{gh_api}/repos/{owner}/{repo}", headers)
    if not api_resp and 'Authorization' in headers:
        headers.pop('Authorization', None)
        api_resp = http_get_json(f"{gh_api}/repos/{owner}/{repo}", headers)
    if not api_resp:
        return None

    parent_full_name = api_resp.get('parent', {}).get('full_name')
    if parent_full_name:
        upstream_url = f"git@github.com:{parent_full_name}.git"
        print(f"Found GitHub upstream: {upstream_url}")
        return upstream_url
    print("No parent detected via GitHub API")
    return None


def discover_gitlab_upstream(cfg: Dict[str, object], origin_url: str) -> Optional[str]:
    m = re.search(r'gitlab\.com[/:]([^/]+)/([^/.]+)(?:\.git)?$', origin_url)
    if not m:
        return None
    owner, repo = m.groups()
    project_path = f"{owner}/{repo}"
    print(f"Querying GitLab API for fork parent of {project_path}")

    gl_token = str(cfg.get('gl_token') or '')
    gl_api = str(cfg.get('gl_api') or 'https://gitlab.com/api/v4')
    try:
        gl = gitlab.Gitlab(gl_api.replace('/api/v4', ''), private_token=gl_token) if gl_api.endswith('/api/v4') else gitlab.Gitlab(gl_api, private_token=gl_token)
        project = gl.projects.get(project_path)
        forked_from = getattr(project, 'forked_from_project', None)
        parent_ns = forked_from.get('path_with_namespace') if isinstance(forked_from, dict) else None
        if parent_ns:
            upstream_url = f"git@gitlab.com:{parent_ns}.git"
            print(f"Found GitLab upstream: {upstream_url}")
            return upstream_url
        print("No parent detected via GitLab API")
        return None
    except GitlabError as e:
        print(f"GitLab API error during upstream discovery: {e}")
        return None
    except Exception as e:
        print(f"Unexpected GitLab error: {e}")
        return None


def get_upstream_default_branch(cfg: Dict[str, object], subrepo: Repo) -> str:
    try:
        out = subrepo.git.ls_remote('--symref', 'upstream', 'HEAD')
        for line in out.splitlines():
            if line.startswith('ref:'):
                return line.split('\t', 1)[0].replace('ref: refs/heads/', '').strip()
    except GitCommandError:
        pass

    try:
        upstream_url = subrepo.remotes.upstream.url  # type: ignore[attr-defined]
    except Exception:
        upstream_url = None

    gh_token = str(cfg.get('gh_token') or '')
    gh_api = str(cfg.get('gh_api') or 'https://api.github.com')
    gl_token = str(cfg.get('gl_token') or '')
    gl_api = str(cfg.get('gl_api') or 'https://gitlab.com/api/v4')

    if upstream_url and 'github.com' in upstream_url:
        m = re.search(r'github\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$', upstream_url)
        if m:
            owner, repo = m.groups()
            headers = {
                'Accept': 'application/vnd.github+json',
                'User-Agent': 'readguides-submodule-updater/1.0'
            }
            if gh_token:
                headers['Authorization'] = f'Bearer {gh_token}'
            api_resp = http_get_json(f"{gh_api}/repos/{owner}/{repo}", headers)
            if not api_resp and 'Authorization' in headers:
                headers.pop('Authorization', None)
                api_resp = http_get_json(f"{gh_api}/repos/{owner}/{repo}", headers)
            if api_resp and 'default_branch' in api_resp:
                return api_resp['default_branch']

    if upstream_url and 'gitlab.com' in upstream_url:
        m = re.search(r'gitlab\.com[:/]([^/]+)/([^/.]+)(?:\.git)?$', upstream_url)
        if m:
            owner, repo = m.groups()
            project_path = f"{owner}/{repo}"
            try:
                gl = gitlab.Gitlab(gl_api.replace('/api/v4', ''), private_token=gl_token) if gl_api.endswith('/api/v4') else gitlab.Gitlab(gl_api, private_token=gl_token)
                project = gl.projects.get(project_path)
                if getattr(project, 'default_branch', None):
                    return str(project.default_branch)
            except GitlabError:
                pass
            except Exception:
                pass

    return 'main'


# ========================
# Submodule utilities
# ========================

def has_markdown_change(files: List[str]) -> bool:
    return any((f or '').lower().endswith('.md') for f in files or [])


def get_submodules(repo: Repo) -> Dict[str, Dict[str, str]]:
    submodules: Dict[str, Dict[str, str]] = {}
    try:
        regexp = r"submodule\..*\.path"
        out = repo.git.config('-f', '.gitmodules', '--get-regexp', regexp)
    except GitCommandError:
        return submodules
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(' ', 1)
        if len(parts) != 2:
            continue
        key, path = parts
        name = key.replace('submodule.', '').replace('.path', '')
        try:
            branch = repo.git.config('-f', '.gitmodules', '--get', f"submodule.{name}.branch")
        except GitCommandError:
            branch = ''
        submodules[name] = {'path': path, 'branch': branch}
    return submodules


def determine_working_branch(subrepo: Repo, desired_branch: str) -> str:
    if desired_branch:
        return desired_branch
    try:
        remote_show = subrepo.git.remote('show', 'origin')
        for ln in remote_show.splitlines():
            if 'HEAD branch:' in ln:
                return ln.split(':', 1)[1].strip()
    except GitCommandError:
        pass
    try:
        return subrepo.active_branch.name  # type: ignore[attr-defined]
    except Exception:
        return 'main'


def ensure_checked_out_branch(subrepo: Repo, branch: str):
    origin_ref_name = f"origin/{branch}"
    origin_refs = [r.name for r in subrepo.remotes.origin.refs]  # type: ignore[attr-defined]
    try:
        if origin_ref_name in origin_refs:
            subrepo.git.checkout('-B', branch, origin_ref_name)
        else:
            subrepo.git.checkout('-B', branch)
    except GitCommandError:
        try:
            subrepo.git.checkout(branch)
        except GitCommandError:
            pass


def compute_ahead_and_changed_files(subrepo: Repo, branch: str) -> Tuple[int, List[str]]:
    try:
        subrepo.git.show_ref('--verify', f"refs/remotes/origin/{branch}")
    except GitCommandError:
        return 0, []
    ahead = 0
    try:
        ahead_str = subrepo.git.rev_list('--count', f"origin/{branch}..HEAD")
        ahead = int(ahead_str.strip() or '0')
    except GitCommandError:
        ahead = 0
    files: List[str] = []
    try:
        out = subrepo.git.diff('--name-only', f"origin/{branch}..HEAD")
        files = [ln.strip() for ln in out.splitlines() if ln.strip()]
    except GitCommandError:
        files = []
    return ahead, files


def push_submodule(subrepo: Repo, branch: str) -> bool:
    print(f"Pushing to origin {branch}")
    try:
        subrepo.remotes.origin.push(f"{branch}:{branch}")  # type: ignore[attr-defined]
        return True
    except GitCommandError as e:
        print(f"Failed to push to origin: {e}")
        return False


# ========================
# Core operations
# ========================

def update_single_submodule(cfg: Dict[str, object], name: str, path: str, desired_branch: str) -> Tuple[bool, Dict[str, object]]:
    start_log_group(f"Processing submodule '{name}' at '{path}'")
    try:
        if not os.path.exists(os.path.join(path, '.git')):
            print(f"Skipping '{path}' (not initialized?)")
            return True, {
                'name': name, 'path': path, 'branch': desired_branch or '',
                'upstream_url': '', 'has_commits_to_push': False,
                'ahead_count': 0, 'changed_files': []
            }

        subrepo = Repo(path)
        pre_head = ''
        try:
            pre_head = subrepo.head.commit.hexsha  # type: ignore[attr-defined]
        except Exception:
            pass

        try:
            origin_url = subrepo.remotes.origin.url  # type: ignore[attr-defined]
        except Exception:
            print(f"Could not get origin URL for {path}")
            return False, {
                'name': name, 'path': path, 'branch': desired_branch or '',
                'upstream_url': '', 'has_commits_to_push': False,
                'ahead_count': 0, 'changed_files': []
            }
        print(f"Origin URL: {origin_url}")

        try:
            subrepo.remotes.origin.fetch(prune=True)  # type: ignore[attr-defined]
        except GitCommandError as e:
            print(f"Failed to fetch from origin: {e}")
            return False, {
                'name': name, 'path': path, 'branch': desired_branch or '',
                'upstream_url': '', 'has_commits_to_push': False,
                'ahead_count': 0, 'changed_files': []
            }

        local_branch = determine_working_branch(subrepo, desired_branch)
        print(f"Working with branch: {local_branch}")
        ensure_checked_out_branch(subrepo, local_branch)

        upstream_url = ''
        try:
            upstream_url = subrepo.remotes.upstream.url  # type: ignore[attr-defined]
            print(f"Using existing upstream: {upstream_url}")
        except Exception:
            if 'github.com' in origin_url:
                upstream_url = discover_github_upstream(cfg, origin_url) or ''
            elif 'gitlab.com' in origin_url:
                upstream_url = discover_gitlab_upstream(cfg, origin_url) or ''
            else:
                print("Non-GitHub/GitLab origin; skipping upstream discovery")
            if upstream_url:
                try:
                    subrepo.create_remote('upstream', upstream_url)
                    print(f"Added upstream: {upstream_url}")
                except GitCommandError:
                    pass

        if upstream_url:
            upstream_branch = get_upstream_default_branch(cfg, subrepo)
            print(f"Fetching upstream ({upstream_url}), default branch '{upstream_branch}'")
            try:
                subrepo.remotes.upstream.fetch(prune=True)  # type: ignore[attr-defined]
            except GitCommandError as e:
                print(f"Failed to fetch upstream: {e}")
                return False, {
                    'name': name, 'path': path, 'branch': local_branch,
                    'upstream_url': upstream_url, 'has_commits_to_push': False,
                    'ahead_count': 0, 'changed_files': []
                }

            upstream_ref_names = [r.name for r in subrepo.remotes.upstream.refs]  # type: ignore[attr-defined]
            desired_upstream_ref = f"upstream/{upstream_branch}"
            if desired_upstream_ref not in upstream_ref_names:
                print(f"Warning: {desired_upstream_ref} not found after fetch. Looking for candidates...")
                candidate = None
                if 'upstream/main' in upstream_ref_names:
                    candidate = 'main'
                elif 'upstream/master' in upstream_ref_names:
                    candidate = 'master'
                elif upstream_ref_names:
                    heads = [n.split('/', 1)[1] for n in upstream_ref_names if '/' in n]
                    candidate = heads[0] if heads else None
                if candidate and candidate != upstream_branch:
                    print(f"Falling back to upstream/{candidate}")
                    upstream_branch = candidate
                else:
                    log_error(f"Upstream branch '{upstream_branch}' not found for '{path}'.")
                    return False, {
                        'name': name, 'path': path, 'branch': local_branch,
                        'upstream_url': upstream_url, 'has_commits_to_push': False,
                        'ahead_count': 0, 'changed_files': []
                    }

            print(f"Merging upstream/{upstream_branch} into {local_branch}")
            try:
                subrepo.git.merge('--no-edit', f"upstream/{upstream_branch}")
            except GitCommandError as e:
                err = str(e)
                log_error(
                    f"Merge conflict in submodule '{path}'. Please resolve manually.\nGit error: {err}"
                )
                print(f"Full merge error details for {path}:\n{err}")
                return False, {
                    'name': name, 'path': path, 'branch': local_branch,
                    'upstream_url': upstream_url, 'has_commits_to_push': False,
                    'ahead_count': 0, 'changed_files': []
                }
        else:
            print(f"No upstream configured; skipping merge for '{path}'")

        ahead_count, changed_files = compute_ahead_and_changed_files(subrepo, local_branch)

        post_head = ''
        try:
            post_head = subrepo.head.commit.hexsha  # type: ignore[attr-defined]
        except Exception:
            pass
        session_changed_files: List[str] = []
        if pre_head and post_head and pre_head != post_head:
            try:
                out = subrepo.git.diff('--name-only', f"{pre_head}..{post_head}")
                session_changed_files = [ln.strip() for ln in out.splitlines() if ln.strip()]
            except GitCommandError:
                session_changed_files = []

        return True, {
            'name': name,
            'path': path,
            'branch': local_branch,
            'upstream_url': upstream_url,
            'has_commits_to_push': ahead_count > 0,
            'ahead_count': ahead_count,
            'changed_files': changed_files,
            'had_head_change': bool(pre_head and post_head and pre_head != post_head),
            'session_changed_files': session_changed_files,
        }
    finally:
        end_log_group()


def commit_superproject_changes_and_open_pr(cfg: Dict[str, object], super_repo: Repo, updated_modules: List[Dict[str, object]]) -> bool:
    try:
        base_branch = get_remote_default_branch(super_repo, 'origin')
        try:
            super_repo.remotes.origin.fetch(prune=True)  # type: ignore[attr-defined]
        except GitCommandError:
            pass
        try:
            super_repo.git.checkout('-B', base_branch, f"origin/{base_branch}")
        except GitCommandError:
            try:
                super_repo.git.checkout(base_branch)
            except GitCommandError:
                pass

        new_branch = "auto/submodule-updates"
        origin_refs = [r.name for r in super_repo.remotes.origin.refs]  # type: ignore[attr-defined]
        origin_new_branch_ref = f"origin/{new_branch}"
        try:
            if origin_new_branch_ref in origin_refs:
                super_repo.git.checkout('-B', new_branch, origin_new_branch_ref)
            else:
                super_repo.git.checkout('-B', new_branch, f"origin/{base_branch}")
        except GitCommandError as e:
            print(f"Failed to create/switch to branch {new_branch}: {e}")
            return False

        changed_paths = sorted({str(r.get('path')) for r in updated_modules if r.get('path')})
        if not changed_paths:
            print('No submodule paths detected to stage in superproject.')
            return True
        for p in changed_paths:
            try:
                super_repo.git.add('--', p)
            except GitCommandError as e:
                print(f"Failed to add path '{p}' to commit: {e}")

        if not super_repo.is_dirty(index=True, working_tree=True, untracked_files=False):
            print('Superproject has no changes to commit; skipping commit/PR.')
            return True

        title = 'Update submodule references'
        body_lines = [
            'Automated update of submodule references.',
            '',
            'Updated paths:'
        ]
        body_lines.extend([f"- {p}" for p in changed_paths])
        body = "\n".join(body_lines)

        try:
            super_repo.index.commit(title)
        except Exception as e:
            print(f"Failed to commit superproject changes: {e}")
            return False

        if bool(cfg.get('dry_run')):
            print(f"Dry run: would push to rolling branch '{new_branch}' and open or update PR -> base '{base_branch}'.")
            return True

        try:
            super_repo.remotes.origin.push(f"{new_branch}:{new_branch}")  # type: ignore[attr-defined]
        except GitCommandError as e:
            print(f"Failed to push branch '{new_branch}': {e}")
            return False

        try:
            origin_url = super_repo.remotes.origin.url  # type: ignore[attr-defined]
        except Exception:
            origin_url = ''
        owner_repo = parse_github_owner_repo(origin_url) if origin_url else None
        if owner_repo:
            owner, repo_name = owner_repo
            existing_pr = find_existing_github_pr(cfg, owner, repo_name, head_branch=new_branch, base_branch=base_branch)
            if existing_pr:
                print(f"Updated existing PR: {existing_pr}")
            else:
                pr_url = create_github_pr(cfg, owner, repo_name, head=new_branch, base=base_branch, title=title, body=body)
                if pr_url:
                    print(f"Opened PR: {pr_url}")
                    pr_msg = (
                        f"Hi I'm from update_submodules.py, Read📖Guides. I need a human to review this automated pull request: {pr_url}"
                    )
                    if bool(cfg.get('dry_run')):
                        print(f"Dry run: would post to RedGuides thread {cfg.get('xf_thread_id')}: {pr_msg}")
                    else:
                        post_redguides_reply(cfg, pr_msg)
                else:
                    print("Failed to open PR via GitHub API.")
        else:
            print('Origin is not a GitHub URL; skipping PR creation.')
        return True
    except Exception as e:
        print(f"Unexpected error while creating branch/PR: {e}")
        return False


# ========================
# Orchestration
# ========================

def update_all_submodules(cfg: Dict[str, object]) -> bool:
    repo = Repo('.', search_parent_directories=True)
    submods = get_submodules(repo)
    if not submods:
        print('No submodules found.')
        return True

    success = True
    results: List[Dict[str, object]] = []
    for name, cfg_item in submods.items():
        ok, meta = update_single_submodule(cfg, name, cfg_item['path'], cfg_item['branch'])
        if not ok:
            success = False
            break
        results.append(meta)

    if not success:
        return False

    any_md_changed = any(
        has_markdown_change(r.get('changed_files', [])) or has_markdown_change(r.get('session_changed_files', []))
        for r in results
    )
    updated_modules = [r for r in results if r.get('has_commits_to_push') or r.get('had_head_change')]

    if updated_modules:
        print('Updated submodules (commits ahead of origin):')
        for r in updated_modules:
            name = r.get('name')
            path = r.get('path')
            branch = r.get('branch')
            ahead = r.get('ahead_count')
            head_changed = r.get('had_head_change')
            print(f"  - {name} ({path}) on {branch}: ahead {ahead}{' (head changed)' if head_changed else ''}")
            changed_files = r.get('changed_files', [])
            if changed_files:
                print('    changed files:')
                for f in changed_files:
                    print(f'      {f}')
            session_changed_files = r.get('session_changed_files', [])
            if session_changed_files and session_changed_files != changed_files:
                print('    session changed files:')
                for f in session_changed_files:
                    print(f'      {f}')
            md_files = [f for f in (changed_files or []) if f.lower().endswith('.md')]
            md_files_session = [f for f in (session_changed_files or []) if f.lower().endswith('.md')]
            if md_files:
                print('    md files:')
                for f in md_files:
                    print(f'      {f}')
            if md_files_session and md_files_session != md_files:
                print('    md files (session):')
                for f in md_files_session:
                    print(f'      {f}')
    else:
        print('No submodules are ahead of origin.')

    if bool(cfg.get('dry_run')):
        print(f"Dry run: {len(updated_modules)} submodule(s) updated this run.")
        print(f"  - with commits to push (ahead): {len([r for r in updated_modules if r.get('has_commits_to_push')])}")
        print(f"  - with head changed (fast-forward/merge): {len([r for r in updated_modules if r.get('had_head_change')])}")
        print(f"Dry run gating: .md changes detected across submodules = {any_md_changed}")
        if any_md_changed and updated_modules:
            print('Dry run: would push updated submodules, create a new superproject branch, commit submodule pointers, and open a PR.')
        else:
            print('Dry run: would not push submodules or open a PR.')
        return True

    if any_md_changed:
        print(f'Detected .md changes; pushing all updated submodules ({len(updated_modules)}).')
        for r in updated_modules:
            subrepo = Repo(r['path'])
            if not push_submodule(subrepo, r['branch']):  # type: ignore[arg-type]
                return False
        print('Creating superproject branch and opening PR for submodule pointer updates.')
        if not commit_superproject_changes_and_open_pr(cfg, repo, updated_modules):
            return False
        return True

    print('No .md changes detected across submodules; skipping push and PR.')
    return True


# ========================
# CLI
# ========================

def main():
    parser = argparse.ArgumentParser(description='Update submodules; open PR if .md changed and post to RedGuides.')
    parser.add_argument(
        '--dry-run', '--no-push', dest='no_push', action='store_true',
        help='Perform all steps (fetch/merge) but skip pushing/PR/forum post'
    )
    args = parser.parse_args()

    cfg = build_config(push_enabled=not args.no_push)
    ok = update_all_submodules(cfg)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()


