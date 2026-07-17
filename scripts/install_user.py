#!/usr/bin/env python3
"""Install the subagent orchestration skill plugin.

Default behavior is conservative:
- user scope installs direct skills only
- user scope never patches CODEX_HOME/config.toml
- project scope writes only under the selected repository root
- project scope activates the gate only when --activate-gate is passed
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any, NoReturn

from file_ops import backup_path, content_matches, next_backup_path, path_exists, remove_path
from toml_ops import (
    has_non_table_toml_content,
    line_opens_toml_table,
    read_toml_value,
    remove_toml_table_key,
    set_toml_table_key,
    toml_literal,
)

ROOT = Path(__file__).resolve().parents[1]
HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
PLUGIN_NAME = "subagent-orchestrator"
USING_SKILL_NAME = "using-subagent-orchestrator"
HOOK_FILE_NAME = "subagent_orchestration_gate.py"
PROJECT_MANIFEST_NAME = "subagent-orchestrator-install.json"
PROJECT_VENDOR_PATH = Path("vendor") / "subagent-orchestration-skill-plugin"
MANAGED_CONFIG_START = "# BEGIN subagent-orchestrator project gate"
MANAGED_CONFIG_END = "# END subagent-orchestrator project gate"
MANAGED_AGENTS_START = "<!-- BEGIN subagent-orchestrator project guidance -->"
MANAGED_AGENTS_END = "<!-- END subagent-orchestrator project guidance -->"
COPY_IGNORE_PATTERNS = (
    ".DS_Store",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage",
    "coverage",
    "dist",
    "build",
    "*.egg-info",
    ".venv",
    "node_modules",
)

PROJECT_AGENTS_SECTION = f"""
{MANAGED_AGENTS_START}
## Optional subagent orchestration

- This plugin is an execution-shape helper only.
- Host project rules win.
- Subagent output is not evidence.
- Keep subagents read-only by default.
- Do not invent sources, citekeys, page numbers, quotations, studies, or metadata.
- Do not install global hooks/config by default.
{MANAGED_AGENTS_END}
""".strip()


def copytree_replace(src: Path, dst: Path, dry_run: bool, label: str) -> None:
    if content_matches(src, dst, COPY_IGNORE_PATTERNS):
        if dry_run:
            print(f"would leave unchanged {label}: {dst}")
        return
    if dry_run:
        backup_path(dst, True, label)
        print(f"would install {label}: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    backup_path(dst, False, label)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*COPY_IGNORE_PATTERNS))
    print(f"installed {label}: {dst}")


def copy_file(src: Path, dst: Path, dry_run: bool, label: str, executable: bool = False) -> None:
    if content_matches(src, dst, COPY_IGNORE_PATTERNS):
        if dry_run:
            print(f"would leave unchanged {label}: {dst}")
        elif executable:
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return
    if dry_run:
        backup_path(dst, True, label)
        print(f"would install {label}: {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    backup_path(dst, False, label)
    shutil.copy2(src, dst)
    if executable:
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed {label}: {dst}")


def install_user_skill(dry_run: bool) -> None:
    skills_src = ROOT / "plugin" / PLUGIN_NAME / "skills"
    skills_dst = HOME / ".agents" / "skills"
    for src in sorted(skills_src.iterdir()):
        if src.is_dir() and (src / "SKILL.md").exists():
            copytree_replace(src, skills_dst / src.name, dry_run, "skill")


def hook_destination() -> Path:
    return CODEX_HOME / "hooks" / HOOK_FILE_NAME


def install_user_hook(dry_run: bool) -> Path:
    dst = hook_destination()
    copy_file(ROOT / "hooks" / HOOK_FILE_NAME, dst, dry_run, "hook", executable=True)
    return dst


def activation_snippet_path() -> str:
    if os.name == "nt":
        return "snippets/config.hooks.windows.toml"
    return "snippets/config.hooks.posix.toml"


def print_user_activation_notice(should_install_hook: bool) -> None:
    print("config.toml was not modified.")
    if not should_install_hook:
        print("Hook not staged; run with --with-hook before manually activating the hook.")
        return
    print("Hook staged but not active.")
    print(f"Activation required: manually merge {activation_snippet_path()} into CODEX_HOME/config.toml.")


def resolved_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def is_inside(base: Path, path: Path) -> bool:
    base_path = resolved_path(base)
    target_path = resolved_path(path)
    return target_path == base_path or target_path.is_relative_to(base_path)


def ensure_inside_repo(repo_root: Path, path: Path) -> Path:
    target_path = resolved_path(path)
    if not is_inside(repo_root, target_path):
        raise ValueError(f"refusing to write outside repo root: {target_path}")
    return target_path


def repo_relative_path(repo_root: Path, path: Path) -> str:
    return ensure_inside_repo(repo_root, path).relative_to(resolved_path(repo_root)).as_posix()


def add_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def new_manifest(repo_root: Path) -> dict[str, Any]:
    return {
        "version": 1,
        "plugin": PLUGIN_NAME,
        "repo_root": str(resolved_path(repo_root)),
        "installed_paths": [],
        "created_paths": [],
        "backups": [],
        "config_values": {},
    }


def manifest_path(repo_root: Path) -> Path:
    return repo_root / ".codex" / PROJECT_MANIFEST_NAME


def load_project_manifest(repo_root: Path) -> dict[str, Any]:
    path = manifest_path(repo_root)
    if not path.exists():
        return new_manifest(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: install manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: install manifest must be a JSON object: {path}")
    if data.get("plugin") != PLUGIN_NAME:
        raise SystemExit(f"error: install manifest is not owned by {PLUGIN_NAME}: {path}")
    for key, default_value in new_manifest(repo_root).items():
        data.setdefault(key, default_value)
    return data


def raise_manifest_error(repo_root: Path, field: str, message: str) -> NoReturn:
    raise SystemExit(f"error: install manifest {field} {message}: {manifest_path(repo_root)}") from None


def resolve_manifest_action_path(repo_root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or value == "":
        raise_manifest_error(repo_root, field, "must be a non-empty string")

    relative_path = Path(value)
    if relative_path.is_absolute() or relative_path.drive:
        raise_manifest_error(repo_root, field, "must be a relative path")
    if ".." in relative_path.parts:
        raise_manifest_error(repo_root, field, "must not contain parent traversal")

    resolved_repo_root = resolved_path(repo_root)
    action_path = resolved_repo_root / relative_path
    try:
        resolved_target = resolved_path(action_path)
    except (OSError, RuntimeError, ValueError):
        raise_manifest_error(repo_root, field, "cannot be resolved safely")

    if resolved_target == resolved_repo_root:
        raise_manifest_error(repo_root, field, "must not target the repository root")
    if not resolved_target.is_relative_to(resolved_repo_root):
        raise_manifest_error(repo_root, field, "resolves outside the repository root")
    return action_path


def validated_manifest_path_entries(
    repo_root: Path,
    manifest: dict[str, Any],
    field: str,
) -> list[tuple[str, Path]]:
    values = manifest.get(field, [])
    if not isinstance(values, list):
        raise_manifest_error(repo_root, field, "must be a list")
    return [
        (value, resolve_manifest_action_path(repo_root, value, f"{field}[{index}]"))
        for index, value in enumerate(values)
    ]


def validated_manifest_backups(
    repo_root: Path,
    manifest: dict[str, Any],
) -> list[tuple[str, Path, Path]]:
    backups = manifest.get("backups", [])
    if not isinstance(backups, list):
        raise_manifest_error(repo_root, "backups", "must be a list")

    validated_backups: list[tuple[str, Path, Path]] = []
    for index, backup in enumerate(backups):
        if not isinstance(backup, dict):
            raise_manifest_error(repo_root, f"backups[{index}]", "must be an object")
        path_value = backup.get("path")
        backup_path_value = backup.get("backup_path")
        path = resolve_manifest_action_path(repo_root, path_value, f"backups[{index}].path")
        backup_path = resolve_manifest_action_path(
            repo_root,
            backup_path_value,
            f"backups[{index}].backup_path",
        )
        validated_backups.append((path_value, path, backup_path))
    return validated_backups


def validate_project_manifest_paths(repo_root: Path, manifest: dict[str, Any]) -> None:
    validated_manifest_path_entries(repo_root, manifest, "installed_paths")
    validated_manifest_path_entries(repo_root, manifest, "created_paths")
    validated_manifest_backups(repo_root, manifest)


def record_created_path(manifest: dict[str, Any], repo_root: Path, path: Path) -> None:
    add_unique(manifest["created_paths"], repo_relative_path(repo_root, path))


def record_installed_path(manifest: dict[str, Any], repo_root: Path, path: Path) -> None:
    add_unique(manifest["installed_paths"], repo_relative_path(repo_root, path))


def is_manifest_owned(manifest: dict[str, Any], repo_root: Path, path: Path) -> bool:
    relative_path = repo_relative_path(repo_root, path)
    return relative_path in manifest.get("created_paths", []) or relative_path in manifest.get("installed_paths", [])


def backup_existing_path(
    path: Path,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
) -> Path | None:
    if not path_exists(path):
        return None
    backup_path = next_backup_path(path)
    ensure_inside_repo(repo_root, backup_path)
    if dry_run:
        print(f"would back up existing {label}: {path} -> {backup_path}")
        return backup_path
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(backup_path))
    manifest["backups"].append({
        "path": repo_relative_path(repo_root, path),
        "backup_path": repo_relative_path(repo_root, backup_path),
    })
    print(f"backed up existing {label}: {path} -> {backup_path}")
    return backup_path


def prepare_project_destination(
    path: Path,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
) -> None:
    ensure_inside_repo(repo_root, path)
    if not path_exists(path):
        if not dry_run:
            record_created_path(manifest, repo_root, path)
        return
    if is_manifest_owned(manifest, repo_root, path):
        remove_path(path, dry_run, label)
        return
    backup_existing_path(path, repo_root, manifest, dry_run, label)


def copy_project_tree(
    src: Path,
    dst: Path,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
) -> None:
    if dry_run:
        if path_exists(dst) and not is_manifest_owned(manifest, repo_root, dst):
            backup_existing_path(dst, repo_root, manifest, True, label)
        print(f"would copy {label}: {src} -> {dst}")
        return
    prepare_project_destination(dst, repo_root, manifest, False, label)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*COPY_IGNORE_PATTERNS))
    record_installed_path(manifest, repo_root, dst)
    print(f"copied {label}: {dst}")


def link_project_tree(
    src: Path,
    dst: Path,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
) -> bool:
    if dry_run:
        if path_exists(dst) and not is_manifest_owned(manifest, repo_root, dst):
            backup_existing_path(dst, repo_root, manifest, True, label)
        print(f"would symlink {label}: {dst} -> {src}")
        return True
    prepare_project_destination(dst, repo_root, manifest, False, label)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src, dst, target_is_directory=True)
    except OSError as exc:
        print(f"symlink failed for {label}; falling back to copy: {exc}")
        return False
    record_installed_path(manifest, repo_root, dst)
    print(f"symlinked {label}: {dst} -> {src}")
    return True


def copy_project_file(
    src: Path,
    dst: Path,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
    executable: bool = False,
    dry_run_verb: str = "copy",
    installed_verb: str = "copied",
) -> None:
    if dry_run:
        if path_exists(dst) and not is_manifest_owned(manifest, repo_root, dst):
            backup_existing_path(dst, repo_root, manifest, True, label)
        print(f"would {dry_run_verb} {label}: {src} -> {dst}")
        return
    prepare_project_destination(dst, repo_root, manifest, False, label)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if executable:
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    record_installed_path(manifest, repo_root, dst)
    print(f"{installed_verb} {label}: {dst}")


def write_project_text(
    path: Path,
    text: str,
    repo_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    label: str,
    executable: bool = False,
    record_installed: bool = True,
) -> None:
    ensure_inside_repo(repo_root, path)
    if path.exists() and path.is_file() and path.read_text(encoding="utf-8") == text:
        if dry_run:
            print(f"would leave unchanged {label}: {path}")
        return
    if dry_run:
        if path_exists(path) and not is_manifest_owned(manifest, repo_root, path):
            backup_existing_path(path, repo_root, manifest, True, label)
        action = "patch" if path_exists(path) else "create"
        print(f"would {action} {label}: {path}")
        return
    prepare_project_destination(path, repo_root, manifest, False, label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if record_installed:
        record_installed_path(manifest, repo_root, path)
    print(f"wrote {label}: {path}")


def write_manifest(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = manifest_path(repo_root)
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if dry_run:
        print(f"would write install manifest: {path}")
        return
    ensure_inside_repo(repo_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote install manifest: {path}")


def detect_repo_root(repo_root_arg: str | None) -> Path:
    if repo_root_arg:
        repo_root = resolved_path(Path(repo_root_arg))
        if not repo_root.exists() or not repo_root.is_dir():
            raise SystemExit(f"error: --repo-root must point to an existing directory: {repo_root}")
        return repo_root
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit("error: --scope project requires a git repository or --repo-root PATH")
    return resolved_path(Path(proc.stdout.strip()))


def validate_source_root(source_root: Path) -> None:
    required_paths = [
        source_root / "plugin" / PLUGIN_NAME / "skills" / PLUGIN_NAME / "SKILL.md",
        source_root / "plugin" / PLUGIN_NAME / "skills" / USING_SKILL_NAME / "SKILL.md",
        source_root / "hooks" / HOOK_FILE_NAME,
    ]
    missing_paths = [str(path) for path in required_paths if not path.exists()]
    if missing_paths:
        raise SystemExit("error: source plugin is missing required files: " + ", ".join(missing_paths))


def resolve_project_source_root(args: argparse.Namespace, repo_root: Path) -> Path:
    if not args.from_vendor:
        return ROOT
    source_root = resolved_path(Path(args.from_vendor))
    if not is_inside(repo_root, source_root):
        raise SystemExit("error: --from-vendor must point inside the project repo root")
    validate_source_root(source_root)
    return source_root


def project_config_block() -> str:
    return f"""
{MANAGED_CONFIG_START}
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = 'python3 "$(git rev-parse --show-toplevel)/.codex/hooks/{HOOK_FILE_NAME}"'
timeout = 5
statusMessage = "Evaluating subagent orchestration"
{MANAGED_CONFIG_END}
""".strip()


def remove_marked_block(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start == -1:
        return text
    end = text.find(end_marker, start)
    if end == -1:
        return text
    end += len(end_marker)
    before = text[:start].rstrip()
    after = text[end:].lstrip("\n")
    pieces = [piece for piece in [before, after] if piece]
    return "\n\n".join(pieces) + ("\n" if pieces else "")


def remember_config_value(
    manifest: dict[str, Any],
    text: str,
    table_name: str,
    key: str,
) -> None:
    manifest_key = f"{table_name}.{key}"
    if manifest_key not in manifest["config_values"]:
        manifest["config_values"][manifest_key] = read_toml_value(text, table_name, key)


def patch_project_config(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = repo_root / ".codex" / "config.toml"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    remember_config_value(manifest, original, "features", "hooks")
    remember_config_value(manifest, original, "agents", "max_threads")
    remember_config_value(manifest, original, "agents", "max_depth")

    updated = remove_marked_block(original, MANAGED_CONFIG_START, MANAGED_CONFIG_END)
    updated = set_toml_table_key(updated, "features", "hooks", "true")
    updated = set_toml_table_key(updated, "agents", "max_threads", "4")
    updated = set_toml_table_key(updated, "agents", "max_depth", "1")
    updated = updated.rstrip() + "\n\n" + project_config_block() + "\n"

    if updated == original:
        return
    if dry_run:
        if path.exists():
            print(f"would back up existing project config: {path} -> {next_backup_path(path)}")
        print(f"would patch project config: {path}")
        return
    if path.exists():
        backup_existing_path(path, repo_root, manifest, False, "project config")
    else:
        record_created_path(manifest, repo_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    print(f"patched project config: {path}")


def restore_project_config(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = repo_root / ".codex" / "config.toml"
    if not path.exists():
        return
    original = path.read_text(encoding="utf-8")
    updated = remove_marked_block(original, MANAGED_CONFIG_START, MANAGED_CONFIG_END)
    for manifest_key, stored_value in manifest.get("config_values", {}).items():
        table_name, key = manifest_key.split(".", 1)
        if stored_value.get("exists"):
            updated = set_toml_table_key(updated, table_name, key, toml_literal(stored_value["value"]))
        else:
            updated = remove_toml_table_key(updated, table_name, key)
    if not has_non_table_toml_content(updated) and repo_relative_path(repo_root, path) in manifest.get("created_paths", []):
        remove_path(path, dry_run, "project config")
        return
    if updated == original:
        return
    if dry_run:
        print(f"would patch project config during uninstall: {path}")
        return
    backup_existing_path(path, repo_root, manifest, False, "project config before uninstall")
    path.write_text(updated, encoding="utf-8")
    print(f"removed project config entries: {path}")


def project_hook_wrapper(relative_vendor_root: Path) -> str:
    relative_hook = (relative_vendor_root / "hooks" / HOOK_FILE_NAME).as_posix()
    template = (ROOT / "templates" / "project_hook_wrapper.py").read_text(encoding="utf-8")
    return template.replace("__RELATIVE_VENDOR_HOOK__", repr(relative_hook))


def install_project_hook(
    repo_root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    uses_vendor: bool,
) -> None:
    dst = repo_root / ".codex" / "hooks" / HOOK_FILE_NAME
    if uses_vendor:
        relative_vendor_root = source_root.relative_to(resolved_path(repo_root))
        write_project_text(
            dst,
            project_hook_wrapper(relative_vendor_root),
            repo_root,
            manifest,
            dry_run,
            "project hook",
            executable=True,
        )
        return

    src = source_root / "hooks" / HOOK_FILE_NAME
    copy_project_file(
        src,
        dst,
        repo_root,
        manifest,
        dry_run,
        "project hook",
        executable=True,
        dry_run_verb="install",
        installed_verb="installed",
    )


def install_project_skills(
    repo_root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
    link_skills: bool,
) -> None:
    skills_src = source_root / "plugin" / PLUGIN_NAME / "skills"
    skills_dst = repo_root / ".agents" / "skills"
    can_link_skills = link_skills and is_inside(repo_root, source_root)
    if link_skills and not can_link_skills:
        print("Skill symlink target is outside repo root; copying project skills instead.")
    for skill_name in [USING_SKILL_NAME, PLUGIN_NAME]:
        src = skills_src / skill_name
        dst = skills_dst / skill_name
        if can_link_skills:
            if link_project_tree(src, dst, repo_root, manifest, dry_run, "project skill"):
                continue
        copy_project_tree(src, dst, repo_root, manifest, dry_run, "project skill")


def install_project_agents(
    repo_root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
) -> None:
    agents_source_root = source_root / "custom-agents"
    if not agents_source_root.exists():
        agents_source_root = ROOT / "custom-agents"
    for src in sorted(agents_source_root.glob("*.toml")):
        dst = repo_root / ".codex" / "agents" / src.name
        copy_project_file(src, dst, repo_root, manifest, dry_run, "project agent")


def marketplace_plugin_entry(repo_root: Path, source_root: Path) -> dict[str, Any]:
    if is_inside(repo_root, source_root):
        plugin_path = "./" + (source_root.relative_to(resolved_path(repo_root)) / "plugin" / PLUGIN_NAME).as_posix()
    else:
        plugin_path = "./" + (PROJECT_VENDOR_PATH / "plugin" / PLUGIN_NAME).as_posix()
    return {
        "name": PLUGIN_NAME,
        "source": {
            "source": "local",
            "path": plugin_path,
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }


def install_project_marketplace_plugin(
    repo_root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
) -> None:
    if is_inside(repo_root, source_root):
        return

    src = source_root / "plugin" / PLUGIN_NAME
    dst = repo_root / PROJECT_VENDOR_PATH / "plugin" / PLUGIN_NAME
    copy_project_tree(src, dst, repo_root, manifest, dry_run, "repo marketplace plugin")


def default_marketplace() -> dict[str, Any]:
    return {
        "name": "local-repo-plugins",
        "interface": {"displayName": "Local Repo Plugins"},
        "plugins": [],
    }


def load_marketplace(path: Path, label: str) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not text.strip():
        return default_marketplace()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {label} marketplace is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"error: {label} marketplace must be a JSON object: {path}")
    plugins = data.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise SystemExit(f"error: {label} marketplace plugins must be a list: {path}")
    if any(not isinstance(plugin, dict) for plugin in plugins):
        raise SystemExit(f"error: {label} marketplace plugins must be objects: {path}")
    return data


def plugins_without_name(plugins: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [plugin for plugin in plugins if plugin.get("name") != name]


def upsert_plugin_named(plugins: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    return plugins_without_name(plugins, entry["name"]) + [entry]


def patch_project_marketplace(
    repo_root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    dry_run: bool,
) -> None:
    path = repo_root / ".agents" / "plugins" / "marketplace.json"
    original_text = path.read_text(encoding="utf-8") if path.exists() else ""
    data = load_marketplace(path, "repo")
    plugins = data.setdefault("plugins", [])
    previous_plugin = next((plugin for plugin in plugins if plugin.get("name") == PLUGIN_NAME), None)
    if "marketplace_previous_plugin" not in manifest:
        manifest["marketplace_previous_plugin"] = previous_plugin

    entry = marketplace_plugin_entry(repo_root, source_root)
    data["plugins"] = upsert_plugin_named(plugins, entry)
    updated_text = json.dumps(data, indent=2) + "\n"
    if updated_text == original_text:
        return
    if dry_run:
        if path.exists():
            print(f"would back up existing repo marketplace: {path} -> {next_backup_path(path)}")
        print(f"would patch repo marketplace: {path}")
        return
    if path.exists():
        backup_existing_path(path, repo_root, manifest, False, "repo marketplace")
    else:
        record_created_path(manifest, repo_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated_text, encoding="utf-8")
    print(f"patched repo marketplace: {path}")


def restore_project_marketplace(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = repo_root / ".agents" / "plugins" / "marketplace.json"
    if not path.exists():
        return
    original_text = path.read_text(encoding="utf-8")
    data = load_marketplace(path, "repo")
    previous_plugin = manifest.get("marketplace_previous_plugin")
    plugins = plugins_without_name(data.get("plugins", []), PLUGIN_NAME)
    if previous_plugin:
        plugins.append(previous_plugin)
    data["plugins"] = plugins
    updated_text = json.dumps(data, indent=2) + "\n"
    if not plugins and repo_relative_path(repo_root, path) in manifest.get("created_paths", []):
        remove_path(path, dry_run, "repo marketplace")
        return
    if updated_text == original_text:
        return
    if dry_run:
        print(f"would patch repo marketplace during uninstall: {path}")
        return
    backup_existing_path(path, repo_root, manifest, False, "repo marketplace before uninstall")
    path.write_text(updated_text, encoding="utf-8")
    print(f"removed repo marketplace entry: {path}")


def append_project_agents_md(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = repo_root / "AGENTS.md"
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    without_existing = remove_marked_block(original, MANAGED_AGENTS_START, MANAGED_AGENTS_END)
    updated = without_existing.rstrip() + "\n\n" + PROJECT_AGENTS_SECTION + "\n"
    if updated == original:
        return
    if dry_run:
        if path.exists():
            print(f"would back up existing project AGENTS.md: {path} -> {next_backup_path(path)}")
        print(f"would append project AGENTS.md guidance: {path}")
        return
    if path.exists():
        backup_existing_path(path, repo_root, manifest, False, "project AGENTS.md")
    else:
        record_created_path(manifest, repo_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    print(f"appended project AGENTS.md guidance: {path}")


def remove_project_agents_md(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    path = repo_root / "AGENTS.md"
    if not path.exists():
        return
    original = path.read_text(encoding="utf-8")
    updated = remove_marked_block(original, MANAGED_AGENTS_START, MANAGED_AGENTS_END)
    if updated.strip() == "" and repo_relative_path(repo_root, path) in manifest.get("created_paths", []):
        remove_path(path, dry_run, "project AGENTS.md")
        return
    if updated == original:
        return
    if dry_run:
        print(f"would remove project AGENTS.md guidance: {path}")
        return
    backup_existing_path(path, repo_root, manifest, False, "project AGENTS.md before uninstall")
    path.write_text(updated, encoding="utf-8")
    print(f"removed project AGENTS.md guidance: {path}")


def remove_installed_project_paths(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    installed_paths = validated_manifest_path_entries(repo_root, manifest, "installed_paths")
    for _, path in sorted(installed_paths, key=lambda value: value[0].count("/"), reverse=True):
        remove_path(path, dry_run, "project install path")


def restore_backups(repo_root: Path, manifest: dict[str, Any], dry_run: bool) -> None:
    specially_patched_paths = {
        ".codex/config.toml",
        ".agents/plugins/marketplace.json",
        "AGENTS.md",
    }
    backups = validated_manifest_backups(repo_root, manifest)
    for relative_path, path, backup_path in reversed(backups):
        if relative_path in specially_patched_paths:
            continue
        if not path_exists(backup_path):
            continue
        if dry_run:
            print(f"would restore backup: {backup_path} -> {path}")
            continue
        remove_path(path, False, "current project install path")
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(backup_path), str(path))
        print(f"restored backup: {path}")


def remove_empty_parent_dirs(repo_root: Path, dry_run: bool) -> None:
    for relative_path in [
        Path(".codex") / "agents",
        Path(".codex") / "hooks",
        Path(".agents") / "skills",
        Path(".agents") / "plugins",
        Path(".codex"),
        Path(".agents"),
    ]:
        path = repo_root / relative_path
        if path.exists() and path.is_dir() and not any(path.iterdir()):
            if dry_run:
                print(f"would remove empty directory: {path}")
            else:
                path.rmdir()
                print(f"removed empty directory: {path}")


def uninstall_project(args: argparse.Namespace) -> int:
    repo_root = detect_repo_root(args.repo_root)
    manifest_file = manifest_path(repo_root)
    if not manifest_file.exists():
        print(f"no project install manifest found: {manifest_file}")
        return 0
    manifest = load_project_manifest(repo_root)
    validate_project_manifest_paths(repo_root, manifest)
    restore_project_config(repo_root, manifest, args.dry_run)
    restore_project_marketplace(repo_root, manifest, args.dry_run)
    remove_project_agents_md(repo_root, manifest, args.dry_run)
    remove_installed_project_paths(repo_root, manifest, args.dry_run)
    restore_backups(repo_root, manifest, args.dry_run)
    if args.dry_run:
        print(f"would remove install manifest: {manifest_file}")
    else:
        manifest_file.unlink()
        print(f"removed install manifest: {manifest_file}")
    remove_empty_parent_dirs(repo_root, args.dry_run)
    return 0


def install_project(args: argparse.Namespace) -> int:
    repo_root = detect_repo_root(args.repo_root)
    source_root = resolve_project_source_root(args, repo_root)
    validate_source_root(source_root)
    manifest = load_project_manifest(repo_root)

    install_project_skills(repo_root, source_root, manifest, args.dry_run, args.link_skills)

    if args.activate_gate:
        install_project_hook(repo_root, source_root, manifest, args.dry_run, bool(args.from_vendor))
        patch_project_config(repo_root, manifest, args.dry_run)
    else:
        print("Project gate not activated; pass --activate-gate to patch .codex/config.toml.")

    if args.with_project_agents:
        install_project_agents(repo_root, source_root, manifest, args.dry_run)
    if args.with_repo_marketplace:
        install_project_marketplace_plugin(repo_root, source_root, manifest, args.dry_run)
        patch_project_marketplace(repo_root, source_root, manifest, args.dry_run)
        print("Repo marketplace entry added. Caveat: plugin UI enable/disable state is stored in ~/.codex/config.toml.")
    if args.append_project_agents_md:
        append_project_agents_md(repo_root, manifest, args.dry_run)

    write_manifest(repo_root, manifest, args.dry_run)
    return 0


def install_user(args: argparse.Namespace) -> int:
    should_install_hook = args.with_hook
    install_user_skill(args.dry_run)
    if should_install_hook:
        install_user_hook(args.dry_run)
    print_user_activation_notice(should_install_hook)
    return 0


def user_installed_targets() -> list[tuple[Path, Path, str]]:
    skills_src = ROOT / "plugin" / PLUGIN_NAME / "skills"
    targets = [
        (HOME / ".agents" / "skills" / PLUGIN_NAME, skills_src / PLUGIN_NAME, "skill"),
        (HOME / ".agents" / "skills" / USING_SKILL_NAME, skills_src / USING_SKILL_NAME, "skill"),
        (CODEX_HOME / "hooks" / HOOK_FILE_NAME, ROOT / "hooks" / HOOK_FILE_NAME, "hook"),
        (CODEX_HOME / "plugins" / PLUGIN_NAME, ROOT / "plugin" / PLUGIN_NAME, "plugin"),
    ]
    agent_targets = [
        (CODEX_HOME / "agents" / path.name, path, "custom agent")
        for path in sorted((ROOT / "custom-agents").glob("*.toml"))
    ]
    return targets + agent_targets


def remove_matching_user_path(path: Path, source: Path, label: str, dry_run: bool) -> None:
    if not path_exists(path):
        print(f"not found: {path}")
        return
    if not content_matches(source, path, COPY_IGNORE_PATTERNS):
        print(f"left different {label} unchanged: {path}")
        return
    remove_path(path, dry_run, label)


def copy_backup(path: Path, dry_run: bool, label: str) -> Path | None:
    return backup_path(path, dry_run, label, move=False)


def block_references_hook(block: list[str]) -> bool:
    return any(HOOK_FILE_NAME in line and not line.lstrip().startswith("#") for line in block)


def split_user_prompt_hook_block(block: list[str]) -> tuple[list[str], list[list[str]]]:
    header: list[str] = []
    hooks: list[list[str]] = []
    current_hook: list[str] | None = None
    for line in block:
        if line.strip() == "[[hooks.UserPromptSubmit.hooks]]":
            if current_hook is not None:
                hooks.append(current_hook)
            current_hook = [line]
            continue
        if current_hook is None:
            header.append(line)
        else:
            current_hook.append(line)
    if current_hook is not None:
        hooks.append(current_hook)
    return header, hooks


def remove_owned_user_prompt_hook_entries(block: list[str]) -> list[str]:
    header, hooks = split_user_prompt_hook_block(block)
    kept_hooks = [hook for hook in hooks if not block_references_hook(hook)]
    if hooks and not kept_hooks:
        return []
    if hooks:
        return header + [line for hook in kept_hooks for line in hook]
    if block_references_hook(block):
        return []
    return block


def remove_hook_config_block(text: str) -> str:
    lines = text.splitlines(True)
    output: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != "[[hooks.UserPromptSubmit]]":
            output.append(lines[index])
            index += 1
            continue

        block: list[str] = []
        while index < len(lines):
            if block and line_opens_toml_table(lines[index]) and lines[index].strip() != "[[hooks.UserPromptSubmit.hooks]]":
                break
            block.append(lines[index])
            index += 1
        output.extend(remove_owned_user_prompt_hook_entries(block))
    return "".join(output)


def remove_user_config_entries(dry_run: bool) -> None:
    config = CODEX_HOME / "config.toml"
    if not config.exists():
        print(f"not found: {config}")
        return
    original = config.read_text(encoding="utf-8")
    updated = remove_hook_config_block(original)
    if updated == original:
        print(f"no owned hook config found: {config}")
        return
    backup = copy_backup(config, dry_run, "config")
    if not dry_run:
        config.write_text(updated, encoding="utf-8")
    print(f"removed owned hook config from: {config} (backup: {backup})")


def remove_user_agents_guidance(dry_run: bool) -> None:
    agents_file = CODEX_HOME / "AGENTS.md"
    if not agents_file.exists():
        print(f"not found: {agents_file}")
        return
    snippet = (ROOT / "snippets" / "AGENTS.subagent-orchestration.md").read_text(encoding="utf-8").strip()
    original = agents_file.read_text(encoding="utf-8")
    updated = original.replace(snippet, "").strip() + "\n"
    if updated == original:
        print(f"no owned AGENTS guidance found: {agents_file}")
        return
    backup = copy_backup(agents_file, dry_run, "AGENTS guidance")
    if not dry_run:
        agents_file.write_text(updated, encoding="utf-8")
    print(f"removed owned AGENTS guidance from: {agents_file} (backup: {backup})")


def remove_user_marketplace_entry(dry_run: bool) -> None:
    path = HOME / ".agents" / "plugins" / "marketplace.json"
    if not path.exists():
        print(f"not found: {path}")
        return
    try:
        data = load_marketplace(path, "user")
    except SystemExit as exc:
        print(str(exc).removeprefix("error: ") + f"; leaving unchanged: {path}")
        return
    plugins = data.get("plugins", [])
    remaining_plugins = plugins_without_name(plugins, PLUGIN_NAME)
    if remaining_plugins == plugins:
        print(f"no owned marketplace entry found: {path}")
        return
    data["plugins"] = remaining_plugins
    backup = copy_backup(path, dry_run, "marketplace")
    if not dry_run:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"removed marketplace entry from: {path} (backup: {backup})")


def uninstall_user(dry_run: bool) -> int:
    for path, source, label in user_installed_targets():
        remove_matching_user_path(path, source, label, dry_run)
    remove_user_agents_guidance(dry_run)
    remove_user_config_entries(dry_run)
    remove_user_marketplace_entry(dry_run)
    return 0


def add_project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", help="Repository root for --scope project. Defaults to git rev-parse --show-toplevel.")
    parser.add_argument("--from-vendor", help="Vendored plugin root inside the repository.")
    parser.add_argument("--activate-gate", action="store_true", help="Patch project .codex/config.toml and install the project hook.")
    parser.add_argument("--link-skills", action="store_true", help="Symlink repo-local skills to the vendored plugin when possible.")
    parser.add_argument("--with-project-agents", action="store_true", help="Copy custom agents into project .codex/agents.")
    parser.add_argument("--append-project-agents-md", action="store_true", help="Append optional project guidance to AGENTS.md.")
    parser.add_argument("--with-repo-marketplace", action="store_true", help="Add or update .agents/plugins/marketplace.json.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install subagent orchestration skills and hook files."
    )
    parser.add_argument("--scope", choices=("user", "project"), default="user", help="Install scope. Defaults to user.")
    parser.add_argument("--skills-only", action="store_true", help="User scope: install only direct skills. This is the default.")
    parser.add_argument("--with-hook", action="store_true", help="User scope: copy the dormant UserPromptSubmit hook script.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without modifying files.")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall owned files and config entries for the selected scope.")
    add_project_arguments(parser)
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.skills_only and args.with_hook:
        parser.error("--skills-only cannot be combined with --with-hook")

    project_only_flags = [
        "--repo-root" if args.repo_root else "",
        "--from-vendor" if args.from_vendor else "",
        "--activate-gate" if args.activate_gate else "",
        "--link-skills" if args.link_skills else "",
        "--with-project-agents" if args.with_project_agents else "",
        "--append-project-agents-md" if args.append_project_agents_md else "",
        "--with-repo-marketplace" if args.with_repo_marketplace else "",
    ]
    used_project_flags = [flag for flag in project_only_flags if flag]
    if args.scope != "project" and used_project_flags:
        parser.error(f"{used_project_flags[0]} requires --scope project")
    if args.scope == "project" and (args.skills_only or args.with_hook):
        parser.error("--skills-only and --with-hook are only valid with --scope user")
    if args.scope == "user" and args.uninstall and (args.skills_only or args.with_hook):
        parser.error("--uninstall cannot be combined with --skills-only or --with-hook")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    if args.scope == "project":
        if args.uninstall:
            return uninstall_project(args)
        return install_project(args)
    if args.uninstall:
        return uninstall_user(args.dry_run)
    return install_user(args)


if __name__ == "__main__":
    raise SystemExit(main())
