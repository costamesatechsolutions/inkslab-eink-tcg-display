#!/usr/bin/python3
"""
Git/update helpers for InkSlab OTA behavior.
"""

import subprocess


def configure_git_safe_directory(script_dir: str) -> None:
    """Allow git operations when the service user differs from repo ownership."""
    try:
        subprocess.run(
            ['git', 'config', '--global', 'safe.directory', script_dir],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        pass


def git_default_branch(script_dir: str) -> str:
    """Detect the remote default branch (main or master)."""
    try:
        result = subprocess.run(
            ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip().split('/')[-1]
    except Exception:
        pass

    for branch in ('main', 'master'):
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--verify', f'origin/{branch}'],
                cwd=script_dir,
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                return branch
        except Exception:
            pass
    return 'main'


def git_current_branch(script_dir: str) -> str:
    """Return the currently checked out branch name."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except Exception:
        pass
    return git_default_branch(script_dir)


def git_remote_branches(script_dir: str, fetch_first: bool = False):
    """Return available remote branches on origin."""
    if fetch_first:
        try:
            subprocess.run(
                ['git', 'fetch', '--prune', 'origin'],
                cwd=script_dir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception:
            pass

    branches = []
    try:
        result = subprocess.run(
            ['git', 'for-each-ref', '--format=%(refname:strip=3)', 'refs/remotes/origin'],
            cwd=script_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branches = [
                branch.strip()
                for branch in result.stdout.splitlines()
                if branch.strip() and branch.strip() != 'HEAD'
            ]
    except Exception:
        pass

    if not branches:
        branches = [git_default_branch(script_dir)]
    if 'master' in branches:
        branches = ['master'] + [branch for branch in branches if branch != 'master']
    return branches


def normalize_update_branch(script_dir: str, branch, available=None) -> str:
    """Normalize a selected update branch to a valid remote branch."""
    available = available or git_remote_branches(script_dir, fetch_first=False)
    candidate = (branch or '').strip()
    if candidate in available:
        return candidate
    current = git_current_branch(script_dir)
    if current in available:
        return current
    default = git_default_branch(script_dir)
    if default in available:
        return default
    return available[0]
