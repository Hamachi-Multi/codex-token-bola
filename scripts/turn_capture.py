"""Shared turn capture primitives without runtime path side effects."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import raw_segments
from raw_segments_common import raw_segment_manifest_lock_path
import turn_resolution

USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")
DEFAULT_APPEND_LOCK_TIMEOUT_MS = 1000
CODE_FENCE_RE = re.compile(r"```([A-Za-z0-9_+.#-]*)[^\n]*\n([\s\S]*?)```", re.MULTILINE)


@dataclass(frozen=True)
class AppendResult:
    ok: bool
    failure_stage: str | None = None
    failure_reason: str | None = None
    error_number: int | None = None

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class TurnFinalizationResult:
    status: str
    append_result: AppendResult | None = None

    def __bool__(self) -> bool:
        return self.status in {"appended", "appended_state_cleanup_pending", "duplicate"}


@dataclass(frozen=True)
class MissingStartPendingResult:
    status: str
    append_result: AppendResult | None = None

    def __bool__(self) -> bool:
        return self.status in {"appended", "appended_state_update_pending", "duplicate"}


@dataclass(frozen=True)
class RawSegmentSnapshot:
    path: pathlib.Path
    device: int
    inode: int


@dataclass(frozen=True)
class ResolvedTurnUsage:
    start_usage: dict[str, int]
    end_usage: dict[str, int]
    usage: dict[str, Any]
    start_usage_source: str
    estimated: bool


def _append_failure(stage: str, reason: str, exc: OSError | None = None) -> AppendResult:
    return AppendResult(False, failure_stage=stage, failure_reason=reason, error_number=exc.errno if exc is not None else None)


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl_result(path: pathlib.Path, record: dict[str, Any], *, durable: bool = False) -> AppendResult:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _append_failure("append", "append_parent_prepare_failed", exc)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    except OSError as exc:
        return _append_failure("append", "append_open_failed", exc)
    descriptor: int | None = fd
    failure_reason = "append_write_failed"
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            descriptor = None
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            if durable:
                handle.flush()
                failure_reason = "append_sync_failed"
                sync = getattr(os, "fdatasync", os.fsync)
                sync(handle.fileno())
    except OSError as exc:
        return _append_failure("append", failure_reason, exc)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return AppendResult(True)


def safe_append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> bool:
    return bool(_append_jsonl_result(path, record))


def append_current_segment_jsonl_result(
    record: dict[str, Any],
    *,
    base_dir: pathlib.Path | str,
    kind: str,
    source_name: str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
    durable: bool = False,
) -> AppendResult:
    base = pathlib.Path(base_dir).expanduser()
    deadline = time.monotonic() + max(0, lock_timeout_ms) / 1000
    lock_path = raw_segments.raw_segment_lock_path(base)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _append_failure("lock", "lock_parent_prepare_failed", exc)
    fd: int | None = None
    try:
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            return _append_failure("lock", "lock_open_failed", exc)
        os.fchmod(fd, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return _append_failure("lock", "lock_timeout")
                time.sleep(0.025)
            except OSError as exc:
                return _append_failure("lock", "lock_acquire_failed", exc)
        try:
            current = raw_segments.ensure_current_segment(
                base,
                kind=kind,
                source_name=source_name,
            )
        except raw_segments.ManifestError:
            return _append_failure("segment", "segment_manifest_error")
        except OSError as exc:
            return _append_failure("segment", "segment_io_error", exc)
        return _append_jsonl_result(pathlib.Path(current["path"]), record, durable=durable)
    except OSError as exc:
        return _append_failure("lock", "lock_prepare_failed", exc)
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


def append_current_segment_jsonl(
    record: dict[str, Any],
    *,
    base_dir: pathlib.Path | str,
    kind: str,
    source_name: str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> bool:
    return bool(
        append_current_segment_jsonl_result(
            record,
            base_dir=base_dir,
            kind=kind,
            source_name=source_name,
            lock_timeout_ms=lock_timeout_ms,
        )
    )


def append_prompt_usage_result(
    record: dict[str, Any],
    *,
    base_dir: pathlib.Path | str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
    durable: bool = False,
) -> AppendResult:
    return append_current_segment_jsonl_result(
        record,
        base_dir=base_dir,
        kind="prompt_usage",
        source_name=raw_segments.PROMPT_RAW_NAME,
        lock_timeout_ms=lock_timeout_ms,
        durable=durable,
    )


def _append_prompt_usage_unlocked_result(record: dict[str, Any], *, base_dir: pathlib.Path) -> AppendResult:
    try:
        current = raw_segments.ensure_current_segment(
            base_dir,
            kind="prompt_usage",
            source_name=raw_segments.PROMPT_RAW_NAME,
        )
    except raw_segments.ManifestError:
        return _append_failure("segment", "segment_manifest_error")
    except OSError as exc:
        return _append_failure("segment", "segment_io_error", exc)
    return _append_jsonl_result(pathlib.Path(current["path"]), record)


def append_prompt_usage(
    record: dict[str, Any],
    *,
    base_dir: pathlib.Path | str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> bool:
    return bool(append_prompt_usage_result(record, base_dir=base_dir, lock_timeout_ms=lock_timeout_ms))


def _locked_state_payload(descriptor: int) -> dict[str, Any]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        payload = json.loads(os.read(descriptor, 1024 * 1024).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_state_atomic_result(
    path: pathlib.Path | str,
    state: dict[str, Any],
    *,
    exclusive: bool,
) -> AppendResult:
    target = pathlib.Path(path).expanduser()
    state_stage = "state_create" if exclusive else "state_write"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _append_failure(state_stage, "state_parent_prepare_failed", exc)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{time.time_ns()}.tmp")
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError as exc:
            return _append_failure(state_stage, "state_temp_open_failed", exc)
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("state write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if exclusive:
            try:
                os.link(temporary, target)
            except FileExistsError:
                return AppendResult(True)
            except OSError as exc:
                return _append_failure("state_create", "state_link_failed", exc)
        else:
            try:
                os.replace(temporary, target)
            except OSError as exc:
                return _append_failure("state_write", "state_replace_failed", exc)
        try:
            parent_descriptor = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            parent_descriptor = None
        if parent_descriptor is not None:
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        return AppendResult(True)
    except OSError as exc:
        return _append_failure(state_stage, "state_write_failed", exc)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def create_json_state_exclusive_result(path: pathlib.Path | str, state: dict[str, Any]) -> AppendResult:
    """Atomically create a state marker without replacing an existing marker."""

    return _write_json_state_atomic_result(path, state, exclusive=True)


def replace_json_state_atomic_result(path: pathlib.Path | str, state: dict[str, Any]) -> AppendResult:
    """Durably replace a state marker without exposing partial JSON."""

    return _write_json_state_atomic_result(path, state, exclusive=False)


def _missing_start_lock_path(state_path: pathlib.Path) -> pathlib.Path:
    return state_path.parent / f".missing-start-{state_path.stem}.lock"


def _acquire_file_lock_until(
    lock_path: pathlib.Path,
    deadline: float,
    *,
    failure_stage: str,
    timeout_reason: str,
) -> tuple[int | None, AppendResult | None]:
    if time.monotonic() >= deadline:
        return None, _append_failure(failure_stage, timeout_reason)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        return None, _append_failure(failure_stage, f"{timeout_reason}_open_failed", exc)
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor, None
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                return None, _append_failure(failure_stage, timeout_reason)
            time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
        except OSError as exc:
            os.close(descriptor)
            return None, _append_failure(failure_stage, f"{timeout_reason}_failed", exc)


def _release_file_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _acquire_missing_start_lock(
    state_path: pathlib.Path,
    deadline: float,
) -> tuple[int | None, AppendResult | None]:
    return _acquire_file_lock_until(
        _missing_start_lock_path(state_path),
        deadline,
        failure_stage="state_lock",
        timeout_reason="state_lock_timeout",
    )


def _read_json_state(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pending_rotation_prompt_paths(base_dir: pathlib.Path) -> list[pathlib.Path]:
    marker = raw_segments.load_pending_rotation(base_dir)
    if marker is None:
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    if hasattr(marker, "old_segment"):
        entries.append((str(getattr(marker, "kind", "")), getattr(marker, "old_segment")))
    else:
        for kind, pair in getattr(marker, "segments", {}).items():
            if isinstance(pair, dict) and isinstance(pair.get("old_segment"), dict):
                entries.append((str(kind), pair["old_segment"]))
    paths: list[pathlib.Path] = []
    for kind, segment in entries:
        if kind != "prompt_usage":
            continue
        valid = raw_segments.validate_current_segment_entry(base_dir, segment, kind=kind)
        paths.append(pathlib.Path(str(valid["path"])))
    return paths


def _snapshot_prompt_segments_locked(base_dir: pathlib.Path, deadline: float) -> list[RawSegmentSnapshot]:
    """Snapshot newest-to-oldest segment identities while caller holds both locks."""

    snapshots: list[RawSegmentSnapshot] = []
    sources = [
        *raw_segments.current_segment_paths(base_dir, kind="prompt_usage"),
        *_pending_rotation_prompt_paths(base_dir),
        *reversed(raw_segments.manifest_segments(base_dir, kind="prompt_usage")),
    ]
    seen: set[str] = set()
    for source in sources:
        if time.monotonic() >= deadline:
            raise TimeoutError("terminal segment snapshot deadline exceeded")
        resolved = str(source.resolve(strict=True))
        if resolved in seen:
            continue
        seen.add(resolved)
        metadata = source.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"raw segment snapshot is not a regular file: {source}")
        snapshots.append(RawSegmentSnapshot(source, metadata.st_dev, metadata.st_ino))
    return snapshots


def _scan_terminal_snapshots(
    snapshots: list[RawSegmentSnapshot],
    session_id: str,
    turn_id: str,
    deadline: float,
) -> bool:
    if time.monotonic() >= deadline:
        raise TimeoutError("terminal scan deadline exceeded")
    for snapshot in snapshots:
        if time.monotonic() >= deadline:
            raise TimeoutError("terminal scan deadline exceeded")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(snapshot.path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != (snapshot.device, snapshot.inode):
                raise OSError(f"raw segment changed after snapshot: {snapshot.path}")
            with os.fdopen(descriptor, "rb") as raw_handle:
                descriptor = -1
                handle = gzip.GzipFile(fileobj=raw_handle) if snapshot.path.suffix == ".gz" else raw_handle
                try:
                    lines = handle
                    for raw_line in lines:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("terminal scan deadline exceeded")
                        try:
                            row = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            continue
                        if not isinstance(row, dict):
                            continue
                        if str(row.get("session_id") or "") != session_id or str(row.get("turn_id") or "") != turn_id:
                            continue
                        try:
                            resolution_status = turn_resolution.status_from_row(row)
                        except turn_resolution.TokenResolutionError:
                            continue
                        if resolution_status in turn_resolution.TERMINAL_STATUSES:
                            return True
                finally:
                    if handle is not raw_handle:
                        handle.close()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return False


def append_missing_start_pending_result(
    *,
    state_path: pathlib.Path | str,
    initial_state: dict[str, Any] | None = None,
    append_record: Callable[[], AppendResult | bool] | None = None,
    record: dict[str, Any] | None = None,
    base_dir: pathlib.Path | str | None = None,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> MissingStartPendingResult:
    """Append one PENDING row under the missing-start lock ordering contract."""

    path = pathlib.Path(state_path).expanduser()
    deadline = time.monotonic() + max(0, lock_timeout_ms) / 1000
    descriptor, lock_failure = _acquire_missing_start_lock(path, deadline)
    if descriptor is None:
        return MissingStartPendingResult("failed", lock_failure)
    try:
        if not path.exists():
            if initial_state is None:
                return MissingStartPendingResult("duplicate")
            create_result = create_json_state_exclusive_result(path, initial_state)
            if not create_result:
                return MissingStartPendingResult("failed", create_result)
        state = _read_json_state(path)
        if state.get("record_type") != "turn_stop_missing_start":
            return MissingStartPendingResult("duplicate")
        if state.get("terminal_append_state") == "claimed":
            return MissingStartPendingResult("duplicate")
        pending_state = state.get("pending_append_state")
        if pending_state is None:
            # A legacy marker cannot reveal whether its old hook append happened.
            # Choose at-most-once: omit a possibly missing PENDING row and let
            # reconcile recover the terminal row.
            return MissingStartPendingResult("duplicate")
        if pending_state in {"claimed", "appended"}:
            return MissingStartPendingResult("duplicate")
        if pending_state != "required":
            return MissingStartPendingResult(
                "failed", _append_failure("state_lock", "pending_append_state_invalid")
            )

        def claim_and_append(raw_append: Callable[[], AppendResult | bool]) -> MissingStartPendingResult:
            claimed = dict(state)
            claimed["pending_append_state"] = "claimed"
            claim_result = replace_json_state_atomic_result(path, claimed)
            if not claim_result:
                return MissingStartPendingResult("failed", claim_result)
            try:
                raw_result = raw_append()
            except OSError as exc:
                raw_result = _append_failure("append", "append_callback_failed", exc)
            append_result = raw_result if isinstance(raw_result, AppendResult) else AppendResult(bool(raw_result))
            if not append_result:
                retryable = dict(claimed)
                retryable["pending_append_state"] = "required"
                replace_json_state_atomic_result(path, retryable)
                return MissingStartPendingResult("failed", append_result)
            appended = dict(claimed)
            appended["pending_append_state"] = "appended"
            append_state_result = replace_json_state_atomic_result(path, appended)
            if not append_state_result:
                return MissingStartPendingResult("appended_state_update_pending", append_result)
            return MissingStartPendingResult("appended", append_result)

        if record is not None and base_dir is not None:
            base = pathlib.Path(base_dir).expanduser()
            try:
                raw_descriptor, raw_failure = _acquire_file_lock_until(
                    raw_segments.raw_segment_lock_path(base),
                    deadline,
                    failure_stage="lock",
                    timeout_reason="lock_timeout",
                )
                if raw_descriptor is None:
                    return MissingStartPendingResult("failed", raw_failure)
                try:
                    manifest_descriptor, manifest_failure = _acquire_file_lock_until(
                        raw_segment_manifest_lock_path(base),
                        deadline,
                        failure_stage="segment",
                        timeout_reason="manifest_lock_timeout",
                    )
                    if manifest_descriptor is None:
                        return MissingStartPendingResult("failed", manifest_failure)
                    try:
                        snapshots = _snapshot_prompt_segments_locked(base, deadline)
                    finally:
                        _release_file_lock(manifest_descriptor)
                finally:
                    _release_file_lock(raw_descriptor)
                terminal_exists = _scan_terminal_snapshots(
                    snapshots,
                    str(record.get("session_id") or ""),
                    str(record.get("turn_id") or ""),
                    deadline,
                )
                if terminal_exists:
                    tombstone = dict(state)
                    tombstone.update(
                        {
                            "record_type": "turn_finalized",
                            "terminal_append_state": "appended",
                            "finalized_reason": "existing_durable_terminal",
                            "finalized_at": utc_now(),
                        }
                    )
                    tombstone_result = replace_json_state_atomic_result(path, tombstone)
                    if not tombstone_result:
                        return MissingStartPendingResult("failed", tombstone_result)
                    return MissingStartPendingResult("duplicate")
                # The sidecar lock excludes missing-start reconcile and other
                # late Stops. With no start marker, no normal terminal owner can
                # be created for this turn between the snapshot and this append.
                raw_descriptor, raw_failure = _acquire_file_lock_until(
                    raw_segments.raw_segment_lock_path(base),
                    deadline,
                    failure_stage="lock",
                    timeout_reason="lock_timeout",
                )
                if raw_descriptor is None:
                    return MissingStartPendingResult("failed", raw_failure)
                try:
                    return claim_and_append(lambda: _append_prompt_usage_unlocked_result(record, base_dir=base))
                finally:
                    _release_file_lock(raw_descriptor)
            except TimeoutError:
                return MissingStartPendingResult(
                    "failed", _append_failure("segment", "terminal_scan_timeout")
                )
            except raw_segments.ManifestError:
                return MissingStartPendingResult(
                    "failed", _append_failure("segment", "segment_manifest_error")
                )
            except (OSError, EOFError, zlib.error) as exc:
                return MissingStartPendingResult(
                    "failed",
                    AppendResult(
                        False,
                        failure_stage="segment",
                        failure_reason="segment_io_error",
                        error_number=exc.errno if isinstance(exc, OSError) else None,
                    ),
                )
        if append_record is None:
            return MissingStartPendingResult(
                "failed", _append_failure("append", "append_callback_missing")
            )
        return claim_and_append(append_record)
    finally:
        _release_file_lock(descriptor)


def finalize_missing_start_terminal_result(
    record: dict[str, Any],
    *,
    state_path: pathlib.Path | str,
    base_dir: pathlib.Path | str,
    terminal_exists: Callable[[], bool],
    before_terminal_append: Callable[[], None] | None = None,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> TurnFinalizationResult:
    """Append one missing-start terminal row under the shared sidecar lock."""

    path = pathlib.Path(state_path).expanduser()
    deadline = time.monotonic() + max(0, lock_timeout_ms) / 1000
    descriptor, lock_failure = _acquire_missing_start_lock(path, deadline)
    if descriptor is None:
        return TurnFinalizationResult("failed", lock_failure)
    try:
        state = _read_json_state(path)
        if not state or state.get("record_type") == "turn_finalized":
            return TurnFinalizationResult("duplicate")
        if state.get("record_type") != "turn_stop_missing_start":
            return TurnFinalizationResult("duplicate")

        claimed = dict(state)
        if claimed.get("terminal_append_state") != "claimed":
            claimed["terminal_append_state"] = "claimed"
            claim_result = replace_json_state_atomic_result(path, claimed)
            if not claim_result:
                return TurnFinalizationResult("failed", claim_result)

        if before_terminal_append is not None:
            try:
                before_terminal_append()
            except Exception as exc:
                return TurnFinalizationResult(
                    "failed",
                    AppendResult(
                        False,
                        failure_stage="health",
                        failure_reason="unavailable_evidence_write_failed",
                        error_number=exc.errno if isinstance(exc, OSError) else None,
                    ),
                )

        if terminal_exists():
            append_result = None
            status = "duplicate"
        else:
            append_result = append_prompt_usage_result(
                record,
                base_dir=base_dir,
                lock_timeout_ms=lock_timeout_ms,
                durable=True,
            )
            if not append_result:
                return TurnFinalizationResult("failed", append_result)
            status = "appended"

        tombstone = dict(claimed)
        tombstone.update(
            {
                "record_type": "turn_finalized",
                "terminal_append_state": "appended",
                "finalized_at": utc_now(),
                "session_id": record.get("session_id"),
                "turn_id": record.get("turn_id"),
            }
        )
        tombstone_result = replace_json_state_atomic_result(path, tombstone)
        if not tombstone_result:
            return TurnFinalizationResult("appended_state_cleanup_pending", append_result)
        return TurnFinalizationResult(status, append_result)
    finally:
        _release_file_lock(descriptor)


def finalize_missing_start_excluded_result(
    *,
    state_path: pathlib.Path | str,
    session_id: str,
    turn_id: str,
    reason: str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> TurnFinalizationResult:
    """Persistently close an excluded missing-start marker without a raw row."""

    path = pathlib.Path(state_path).expanduser()
    deadline = time.monotonic() + max(0, lock_timeout_ms) / 1000
    descriptor, lock_failure = _acquire_missing_start_lock(path, deadline)
    if descriptor is None:
        return TurnFinalizationResult("failed", lock_failure)
    try:
        state = _read_json_state(path)
        if not state or state.get("record_type") == "turn_finalized":
            return TurnFinalizationResult("duplicate")
        if state.get("record_type") != "turn_stop_missing_start":
            return TurnFinalizationResult("duplicate")
        tombstone = dict(state)
        tombstone.update(
            {
                "record_type": "turn_finalized",
                "terminal_append_state": "excluded",
                "finalized_reason": reason,
                "finalized_at": utc_now(),
                "session_id": session_id,
                "turn_id": turn_id,
            }
        )
        result = replace_json_state_atomic_result(path, tombstone)
        if not result:
            return TurnFinalizationResult("failed", result)
        return TurnFinalizationResult("excluded")
    finally:
        _release_file_lock(descriptor)


def _write_finalized_state_marker(descriptor: int, state: dict[str, Any], record: dict[str, Any]) -> None:
    marker = dict(state)
    marker.update(
        {
            "record_type": "turn_finalized",
            "finalized_at": utc_now(),
            "session_id": record.get("session_id"),
            "turn_id": record.get("turn_id"),
        }
    )
    payload = json.dumps(marker, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    os.write(descriptor, payload)


def finalize_prompt_usage_result(
    record: dict[str, Any],
    *,
    state_path: pathlib.Path | str,
    base_dir: pathlib.Path | str,
    lock_timeout_ms: int = DEFAULT_APPEND_LOCK_TIMEOUT_MS,
) -> TurnFinalizationResult:
    """Append one terminal turn while using its state inode as the ownership claim."""

    path = pathlib.Path(state_path).expanduser()
    deadline = time.monotonic() + max(0, lock_timeout_ms) / 1000
    try:
        descriptor = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return TurnFinalizationResult("duplicate")
    except OSError as exc:
        return TurnFinalizationResult("failed", _append_failure("state_lock", "state_open_failed", exc))
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    return TurnFinalizationResult("failed", _append_failure("state_lock", "state_lock_timeout"))
                time.sleep(0.025)
            except OSError as exc:
                return TurnFinalizationResult("failed", _append_failure("state_lock", "state_lock_failed", exc))

        try:
            current = path.stat()
        except FileNotFoundError:
            return TurnFinalizationResult("duplicate")
        except OSError as exc:
            return TurnFinalizationResult("failed", _append_failure("state_lock", "state_stat_failed", exc))
        held = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
            return TurnFinalizationResult("duplicate")

        state = _locked_state_payload(descriptor)
        if state.get("record_type") == "turn_finalized":
            try:
                path.unlink()
            except OSError:
                pass
            return TurnFinalizationResult("duplicate")

        append_result = append_prompt_usage_result(
            record,
            base_dir=base_dir,
            lock_timeout_ms=lock_timeout_ms,
            durable=True,
        )
        if not append_result:
            return TurnFinalizationResult("failed", append_result)
        try:
            path.unlink()
        except OSError:
            try:
                _write_finalized_state_marker(descriptor, state, record)
            except OSError:
                return TurnFinalizationResult("appended_state_cleanup_pending", append_result)
            return TurnFinalizationResult("appended_state_cleanup_pending", append_result)
        return TurnFinalizationResult("appended", append_result)
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def zero_usage() -> dict[str, int]:
    return {key: 0 for key in USAGE_KEYS}


def normalize_usage(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {key: safe_int(source.get(key)) for key in USAGE_KEYS}


def usage_delta(start: dict[str, int], end: dict[str, int]) -> dict[str, Any]:
    usage: dict[str, Any] = {key: safe_int(end.get(key)) - safe_int(start.get(key)) for key in USAGE_KEYS}
    usage["non_cached_input_tokens"] = usage["input_tokens"] - usage["cached_input_tokens"]
    usage["consistency_total_equals_input_plus_output"] = (
        usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"]
    )
    return usage


def usage_sum(items: list[dict[str, Any]]) -> dict[str, int]:
    total = zero_usage()
    for item in items:
        usage = normalize_usage(item)
        for key in USAGE_KEYS:
            total[key] += usage[key]
    return total


def usage_from_model_calls(snapshot: dict[str, Any]) -> dict[str, Any]:
    calls = snapshot.get("model_calls")
    if not isinstance(calls, list):
        return usage_delta(zero_usage(), zero_usage())
    return usage_delta(zero_usage(), usage_sum([call.get("usage") for call in calls if isinstance(call, dict)]))


def cumulative_usage_reset(start: dict[str, int], end: dict[str, int]) -> bool:
    return any(safe_int(end.get(key)) < safe_int(start.get(key)) for key in USAGE_KEYS)


def resolved_turn_usage(start_state: dict[str, Any], end_snapshot: dict[str, Any]) -> ResolvedTurnUsage:
    start_usage = normalize_usage(start_state.get("start_token_usage"))
    end_usage = normalize_usage(end_snapshot.get("total_token_usage"))
    start_usage_source = start_state.get("start_usage_source")
    if not start_usage_source:
        start_snapshot = start_state.get("start_token_snapshot")
        start_usage_source = "legacy_full_scan" if isinstance(start_snapshot, dict) and start_snapshot.get("found") else "unavailable"
    source = str(start_usage_source)
    if source != "unavailable" and cumulative_usage_reset(start_usage, end_usage):
        source = "counter_reset"
    use_model_calls = source in {"unavailable", "counter_reset"}
    return ResolvedTurnUsage(
        start_usage=start_usage,
        end_usage=end_usage,
        usage=usage_from_model_calls(end_snapshot) if use_model_calls else usage_delta(start_usage, end_usage),
        start_usage_source=source,
        estimated=use_model_calls,
    )


def compact_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    compact = dict(snapshot)
    compact.pop("model_calls", None)
    return compact


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    return sha256_text(value)[:32]


def prompt_metadata(text: str, *, preview_chars: int) -> dict[str, Any]:
    code_blocks = list(CODE_FENCE_RE.finditer(text))
    code_chars = sum(len(match.group(0)) for match in code_blocks)
    code_lines = sum(match.group(0).count("\n") + 1 for match in code_blocks)
    languages = sorted({match.group(1).strip().lower() for match in code_blocks if match.group(1).strip()})
    chars = len(text)
    preview = text[:preview_chars] if preview_chars > 0 else ""
    return {
        "prompt_preview": preview,
        "prompt_preview_chars": len(preview),
        "prompt_chars": chars,
        "prompt_lines": text.count("\n") + 1 if text else 0,
        "prompt_sha256": sha256_text(text) if text else None,
        "prompt_truncated": len(preview) < chars,
        "payload_stats": {
            "code_block_count": len(code_blocks),
            "code_block_chars": code_chars,
            "code_block_lines": code_lines,
            "languages": languages,
            "pasted_text_chars": chars,
            "payload_ratio": round(code_chars / chars, 4) if chars else 0.0,
        },
    }


def without_instruction_excerpt(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    sanitized = dict(value)
    sanitized.pop("instruction_excerpt", None)
    sanitized.pop("instruction_excerpt_chars", None)
    return sanitized


def assistant_metadata(data: dict[str, Any]) -> dict[str, Any]:
    text = data.get("last_assistant_message", "")
    text = text if isinstance(text, str) else ""
    return {
        "assistant_chars": len(text),
        "assistant_lines": text.count("\n") + 1 if text else 0,
        "assistant_sha256": sha256_text(text) if text else None,
    }
