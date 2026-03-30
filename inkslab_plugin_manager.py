#!/usr/bin/python3
"""
Helpers for installing and removing community InkSlab plugins.
"""

import os
import shutil
import subprocess
import tempfile
import zipfile

from inkslab_paths import USER_PLUGIN_DIR
from inkslab_plugins import load_external_plugin_manifest


def _ensure_user_plugin_dir():
    os.makedirs(USER_PLUGIN_DIR, exist_ok=True)
    return USER_PLUGIN_DIR


def _safe_repo_name(url):
    slug = str(url or "").rstrip("/").rsplit("/", 1)[-1]
    if slug.endswith(".git"):
        slug = slug[:-4]
    return "".join(ch for ch in slug if ch.isalnum() or ch in ("_", "-"))[:48] or "plugin"


def _find_plugin_root(extract_root):
    manifest_at_root = os.path.join(extract_root, "manifest.json")
    if os.path.isfile(manifest_at_root):
        return extract_root
    for entry in sorted(os.listdir(extract_root)):
        candidate = os.path.join(extract_root, entry)
        if os.path.isdir(candidate) and os.path.isfile(os.path.join(candidate, "manifest.json")):
            return candidate
    raise ValueError("Could not find a plugin manifest.json in the uploaded package.")


def _validate_plugin_root(plugin_root):
    manifest_path = os.path.join(plugin_root, "manifest.json")
    plugin = load_external_plugin_manifest(manifest_path)
    if not plugin:
        raise ValueError("manifest.json is missing required InkSlab plugin fields.")
    if not os.path.isfile(os.path.join(plugin_root, plugin.entrypoint or "__init__.py")):
        raise ValueError("Plugin entrypoint file is missing.")
    return plugin


def _install_plugin_tree(plugin_root, replace_existing=False):
    plugin = _validate_plugin_root(plugin_root)
    target_root = _ensure_user_plugin_dir()
    target_dir = os.path.join(target_root, plugin.plugin_id)
    if os.path.exists(target_dir):
        if not replace_existing:
            raise ValueError("A plugin with that ID is already installed.")
        shutil.rmtree(target_dir)
    shutil.copytree(plugin_root, target_dir)
    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "path": target_dir,
        "source": "local-manifest",
    }


def install_plugin_from_zip(upload_path, replace_existing=False):
    with tempfile.TemporaryDirectory(prefix="inkslab_plugin_zip_") as tmpdir:
        try:
            with zipfile.ZipFile(upload_path, "r") as archive:
                archive.extractall(tmpdir)
        except zipfile.BadZipFile as exc:
            raise ValueError("The uploaded file was not a valid ZIP archive.") from exc
        plugin_root = _find_plugin_root(tmpdir)
        return _install_plugin_tree(plugin_root, replace_existing=replace_existing)


def install_plugin_from_git(repo_url, replace_existing=False):
    url = str(repo_url or "").strip()
    if not url.startswith("https://github.com/"):
        raise ValueError("Only https://github.com/ plugin URLs are supported right now.")
    with tempfile.TemporaryDirectory(prefix="inkslab_plugin_git_") as tmpdir:
        clone_dir = os.path.join(tmpdir, _safe_repo_name(url))
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, clone_dir],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("GitHub clone timed out.") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise ValueError(stderr or "Could not clone the GitHub repository.") from exc
        plugin_root = _find_plugin_root(clone_dir)
        return _install_plugin_tree(plugin_root, replace_existing=replace_existing)


def uninstall_plugin(plugin_id):
    safe_id = os.path.basename(str(plugin_id or "").strip())
    if not safe_id:
        raise ValueError("Missing plugin ID.")
    target_dir = os.path.join(USER_PLUGIN_DIR, safe_id)
    if not os.path.isdir(target_dir):
        raise ValueError("That community plugin is not installed in the user plugin folder.")
    shutil.rmtree(target_dir)
    return {"plugin_id": safe_id, "removed": True}
