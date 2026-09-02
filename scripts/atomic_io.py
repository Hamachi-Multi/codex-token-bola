"""Durable owner-only file replacement primitives."""

from __future__ import annotations

import contextlib
import os
import pathlib
import time


def fsync_directory(path: pathlib.Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_text_owner_only(path: pathlib.Path, text: str, mode: int = 0o600) -> None:
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, target)
        target.chmod(mode)
        fsync_directory(target.parent)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def unlink_durable(path: pathlib.Path) -> bool:
    target = pathlib.Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    fsync_directory(target.parent)
    return True
