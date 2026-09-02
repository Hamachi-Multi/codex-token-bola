from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import gzip
import json
import pathlib
from typing import Any

try:
    from tests.support import _raw_segment, _turn_raw
except ModuleNotFoundError:
    from support import _raw_segment, _turn_raw


DEFAULT_ARCHIVED_SEGMENT = (
    "prompt-usage.raw.jsonl.20260501000000.20260501000000.1.jsonl.gz"
)


def write_archived_segment(
    raw_segments: Any,
    base: pathlib.Path,
    *,
    payload: bytes,
    min_time: float | None,
    max_time: float | None,
    rows: int,
    filename: str = DEFAULT_ARCHIVED_SEGMENT,
    **metadata: Any,
) -> tuple[pathlib.Path, dict[str, Any]]:
    archive = base / "raw" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / filename
    with gzip.open(path, "wb") as handle:
        handle.write(payload)
    segment = _raw_segment(
        path,
        payload=payload,
        min_time=min_time,
        max_time=max_time,
        rows=rows,
        **metadata,
    )
    return path, segment


def write_segment_manifest(
    raw_segments: Any,
    base: pathlib.Path,
    segments: Iterable[dict[str, Any]],
) -> None:
    raw_segments.write_manifest(
        base,
        raw_segments.empty_manifest(base) | {"segments": list(segments)},
    )


def prepare_archived_retention_plan(
    cleanup: Any,
    base: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = (
        json.dumps(
            _turn_raw("session", "old", total=100)
            | {"captured_at": "2026-05-01T00:00:00+00:00"}
        )
        + "\n"
    ).encode("utf-8")
    _path, segment = write_archived_segment(
        cleanup._retention.raw_segments,
        base,
        payload=payload,
        min_time=1777593600.0,
        max_time=1777593600.0,
        rows=1,
    )
    write_segment_manifest(cleanup._retention.raw_segments, base, [segment])
    plan = cleanup.plan_delete_logs_older_than(
        base,
        datetime(2026, 5, 20, tzinfo=timezone.utc).timestamp(),
    )
    return plan, segment
