"""Codex hook registration application service."""

from __future__ import annotations

import copy
import json
import pathlib
import shlex
import sys
from dataclasses import dataclass
from typing import Callable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import service_paths
import atomic_io


@dataclass(frozen=True)
class InstallHookOptions:
    codex_dir: str | pathlib.Path | None = None
    output_dir: str | pathlib.Path | None = None
    persist_config: bool = False


@dataclass(frozen=True)
class InstallHookDependencies:
    resolve_paths: Callable[[str | pathlib.Path | None, str | pathlib.Path | None], service_paths.RuntimePaths]
    validate_codex_dir: Callable[[str | pathlib.Path], None]
    validate_codex_cli: Callable[[], None]
    validate_hook_runtime: Callable[[], None]
    persist_paths: Callable[[dict[str, str | pathlib.Path]], None]


@dataclass(frozen=True)
class InstallHookResult:
    payload: dict[str, object]

def hook_install_status(codex_dir: pathlib.Path) -> dict[str, object]:
    return {
        "module": "codex_token_bola.hook",
        "command": hook_command(),
        "legacy_copy": str(codex_dir / "hooks" / "token-usage.py"),
        "legacy_copy_exists": (codex_dir / "hooks" / "token-usage.py").exists(),
    }


HOOK_MARKER_ARG = "--bola-hook"
LEGACY_HOOK_MARKER_ARG = "--codex-token-bola-hook"


def hook_command() -> str:
    return shlex.join([sys.executable, "-m", "codex_token_bola.hook", HOOK_MARKER_ARG])


def is_owned_hook_command(command: str, codex_dir: pathlib.Path) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if HOOK_MARKER_ARG in argv or LEGACY_HOOK_MARKER_ARG in argv:
        return True
    legacy_hook = (codex_dir / "hooks" / "token-usage.py").resolve(strict=False)
    return (
        len(argv) == 2
        and pathlib.Path(argv[0]).name in {"python", "python3"}
        and pathlib.Path(argv[1]).expanduser().resolve(strict=False) == legacy_hook
    )


def _commands_from_entries(entries: object) -> list[str]:
    commands: list[str] = []
    if not isinstance(entries, list):
        return commands
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "")
        if command:
            commands.append(command)
        nested_hooks = entry.get("hooks")
        if isinstance(nested_hooks, list):
            for nested in nested_hooks:
                if not isinstance(nested, dict):
                    continue
                nested_command = str(nested.get("command") or "")
                if nested_command:
                    commands.append(nested_command)
    return commands


def _without_owned_entries(entries: object, codex_dir: pathlib.Path) -> object:
    if not isinstance(entries, list):
        return entries
    retained_entries: list[object] = []
    for entry in entries:
        if not isinstance(entry, dict):
            retained_entries.append(entry)
            continue
        direct_command = str(entry.get("command") or "")
        if direct_command and is_owned_hook_command(direct_command, codex_dir):
            continue
        nested_hooks = entry.get("hooks")
        if not isinstance(nested_hooks, list):
            retained_entries.append(entry)
            continue
        retained_nested = [
            item
            for item in nested_hooks
            if not (
                isinstance(item, dict)
                and str(item.get("command") or "")
                and is_owned_hook_command(str(item.get("command") or ""), codex_dir)
            )
        ]
        if retained_nested:
            copied = dict(entry)
            copied["hooks"] = retained_nested
            retained_entries.append(copied)
        elif direct_command:
            copied = dict(entry)
            copied["hooks"] = []
            retained_entries.append(copied)
        elif not nested_hooks:
            retained_entries.append(entry)
    return retained_entries


def _hook_containers(parsed: dict[str, object]) -> list[dict[str, object]]:
    containers = [parsed]
    hooks = parsed.get("hooks")
    if isinstance(hooks, dict):
        containers.append(hooks)
    return containers


def hooks_json_status(codex_dir: pathlib.Path) -> dict[str, object]:
    path = codex_dir / "hooks.json"
    expected_command = hook_command()
    events: dict[str, dict[str, object]] = {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        parsed = {}
        error = None
    except (OSError, json.JSONDecodeError) as exc:
        parsed = {}
        error = repr(exc)
    else:
        error = None
    for event in ("UserPromptSubmit", "Stop"):
        commands: list[str] = []
        if isinstance(parsed, dict):
            for container in _hook_containers(parsed):
                commands.extend(_commands_from_entries(container.get(event)))
        owned_commands = [command for command in commands if is_owned_hook_command(command, codex_dir)]
        expected_seen = False
        stale_commands: list[str] = []
        for command in owned_commands:
            if command == expected_command and not expected_seen:
                expected_seen = True
            else:
                stale_commands.append(command)
        events[event] = {
            "registered": expected_seen,
            "stale_commands": stale_commands,
            "commands": commands,
        }
    return {"path": str(path), "exists": path.exists(), "error": error, "events": events}


def write_text_atomic_owner_only(path: pathlib.Path, text: str, mode: int = 0o600) -> None:
    atomic_io.write_text_owner_only(path, text, mode)


def merge_hooks_json_registration(codex_dir: pathlib.Path) -> dict[str, object]:
    path = codex_dir / "hooks.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    original = copy.deepcopy(parsed)
    hooks = parsed.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        parsed["hooks"] = hooks
    command = hook_command()
    for container in _hook_containers(parsed):
        for event in ("UserPromptSubmit", "Stop"):
            if event in container:
                container[event] = _without_owned_entries(container.get(event), codex_dir)
    for event in ("UserPromptSubmit", "Stop"):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
        entries.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event] = entries
    updated = parsed != original
    if updated or not path.exists():
        write_text_atomic_owner_only(path, json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", 0o600)
    return {"path": str(path), "updated": updated, "events": hooks_json_status(codex_dir)["events"]}


def remove_hooks_json_registration(codex_dir: pathlib.Path) -> dict[str, object]:
    path = codex_dir / "hooks.json"
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"path": str(path), "updated": False}
    except (OSError, json.JSONDecodeError) as exc:
        raise service_paths.ConfigurationError(f"cannot update Codex hooks at {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise service_paths.ConfigurationError(f"Codex hooks must be a JSON object: {path}")
    original = copy.deepcopy(parsed)
    for container in _hook_containers(parsed):
        for event in ("UserPromptSubmit", "Stop"):
            if event in container:
                container[event] = _without_owned_entries(container.get(event), codex_dir)
    updated = parsed != original
    if updated:
        write_text_atomic_owner_only(path, json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", 0o600)
    return {"path": str(path), "updated": updated}


def run_install_hook(
    options: InstallHookOptions,
    dependencies: InstallHookDependencies,
) -> InstallHookResult:
    requested_paths = dependencies.resolve_paths(options.codex_dir, options.output_dir)
    dependencies.validate_codex_dir(requested_paths.codex_dir)
    dependencies.validate_codex_cli()
    dependencies.validate_hook_runtime()

    hooks_path = requested_paths.codex_dir / "hooks.json"
    hooks_snapshot = hooks_path.read_bytes() if hooks_path.exists() else None
    try:
        hooks_json = merge_hooks_json_registration(requested_paths.codex_dir)
        if options.persist_config:
            dependencies.persist_paths(
                {
                    "codex_dir": requested_paths.codex_dir,
                    "output_dir": requested_paths.output_dir,
                }
            )
            paths = dependencies.resolve_paths(None, None)
        else:
            paths = requested_paths
    except Exception:
        if hooks_snapshot is None:
            atomic_io.unlink_durable(hooks_path)
        else:
            write_text_atomic_owner_only(hooks_path, hooks_snapshot.decode("utf-8"), 0o600)
        raise

    return InstallHookResult(
        payload={
            "installed_hook": "codex_token_bola.hook",
            "command": hook_command(),
            "interpreter": sys.executable,
            "runtime_paths": paths.as_dict(),
            "hooks_json": hooks_json,
        }
    )
