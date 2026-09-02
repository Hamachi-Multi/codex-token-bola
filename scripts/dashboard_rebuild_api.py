"""Analyze request handlers for the local dashboard server."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import uuid

import cancel_control
import dashboard_cleanup
import dashboard_managed_process
import dashboard_operation_state as operation_state
import progress_control
import service_lock
import service_paths
from runtime_command_runner import process_result_from_output, required_json_contract_error


AUTO_COMPACT_MIN_BYTES = 64 * 1024 * 1024


def terminate_rebuild_process(process, grace_seconds: float = 2.0) -> str:
    if process.poll() is not None:
        return "completed"
    terminate_group = getattr(process, "terminate_group", None)
    if callable(terminate_group):
        return str(terminate_group(grace_seconds))
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return "terminated"
    except subprocess.TimeoutExpired:
        process.kill()
        return "killed"


def rebuild_was_cancelled(result, *, cancel_enforced: bool) -> bool:
    metadata = result.payload or {}
    return (
        result.exit_code == cancel_control.CANCEL_EXIT_CODE
        or bool(metadata.get("cancelled"))
        or cancel_enforced
    )


class DashboardRebuildApiMixin:
    @staticmethod
    def canonical_operation_id(value) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError):
            return None
        canonical = str(parsed)
        return canonical if value == canonical else None

    def handle_rebuild(self):
        operation_id = self.canonical_operation_id(self.read_json_body().get("operation_id"))
        if operation_id is None:
            self.send_json({"error": "invalid_operation_id"}, 400)
            return
        manager = self.dashboard_operation_manager()
        try:
            starter = getattr(self, "begin_dashboard_operation", None)
            lease = (
                starter("analysis", operation_id=operation_id)
                if callable(starter)
                else manager.begin("analysis", self.dashboard_output_dir(), operation_id=operation_id)
            )
        except operation_state.ServerShuttingDown:
            self.send_json({"error": "server_shutting_down"}, 503)
            return
        except operation_state.OperationBusy:
            self.send_json(manager.busy_payload(), 409)
            return
        started = time.monotonic()
        cancel_file = self.dashboard_output_dir() / "state" / f"rebuild-cancel.{operation_id}.json"
        progress_file = self.dashboard_output_dir() / "state" / f"rebuild-progress.{operation_id}.json"
        try:
            cancel_file.unlink(missing_ok=True)
            progress_file.unlink(missing_ok=True)
            manager.set_files(operation_id, cancel_file=cancel_file, progress_file=progress_file)
            progress_control.write_progress_to_path(
                progress_file,
                operation_id=operation_id,
                status="running",
                phase="normalize",
                phase_index=0,
                checkpoint="queued",
                phase_progress=0.0,
            )
            script = self.dashboard_script_dir() / "bola.py"
            cmd = [
                sys.executable,
                str(script),
                "pipeline",
                "--codex-dir",
                str(self.dashboard_codex_dir()),
                "--output-dir",
                str(self.dashboard_output_dir()),
                "--incremental",
                "--recover",
            ]
            env = service_lock.scrub_lock_env(os.environ.copy())
            env["BOLA_CANCEL_FILE"] = str(cancel_file)
            env["BOLA_PROGRESS_FILE"] = str(progress_file)
            tmp_dir = service_paths.ensure_output_tmp_dir(self.dashboard_output_dir())
            with tempfile.TemporaryFile("w+", encoding="utf-8", dir=tmp_dir) as stdout_file, tempfile.TemporaryFile(
                "w+", encoding="utf-8", dir=tmp_dir
            ) as stderr_file:
                process = dashboard_managed_process.ManagedProcess.start(
                    cmd,
                    kind="analysis",
                    cwd=str(script.parent),
                    stdout=stdout_file,
                    stderr=stderr_file,
                    env=env,
                    lifetime_lock_fd=getattr(self, "dashboard_lifetime_lock_fd", lambda: None)(),
                )
                manager.attach_process(operation_id, process)
                active = manager.active_record()
                if active is not None and active.operation_id == operation_id and active.cancel_requested.is_set():
                    cancel_control.request_cancel(cancel_file, reason="user")
                try:
                    cancel_requested_at: float | None = None
                    cancel_enforced = False
                    while process.poll() is None:
                        active = manager.active_record()
                        cancel_requested = bool(
                            active is not None
                            and active.operation_id == operation_id
                            and active.cancel_requested.is_set()
                        )
                        if cancel_requested:
                            cancel_control.request_cancel(cancel_file, reason="user")
                            if cancel_requested_at is None:
                                cancel_requested_at = time.monotonic()
                                progress_control.write_progress_to_path(
                                    progress_file,
                                    operation_id=operation_id,
                                    status="running",
                                    phase="cancel",
                                    phase_index=1,
                                    checkpoint="cancel_requested",
                                    phase_progress=0.0,
                                )
                            elif time.monotonic() - cancel_requested_at >= 2.0:
                                status = terminate_rebuild_process(process)
                                cancel_enforced = status in {"terminated", "killed"}
                                progress_control.write_progress_to_path(
                                    progress_file,
                                    operation_id=operation_id,
                                    status="cancelled" if cancel_enforced else "running",
                                    phase="cancel",
                                    phase_index=1,
                                    checkpoint=status if cancel_enforced else "cancel_too_late",
                                    phase_progress=1.0 if cancel_enforced else 0.0,
                                )
                                break
                        time.sleep(0.1)
                    process.wait(timeout=2)
                    stdout_file.seek(0)
                    stderr_file.seek(0)
                    stdout = stdout_file.read()
                    stderr = stderr_file.read()
                finally:
                    manager.detach_process(operation_id, process)
            stdout = (stdout or "").strip()
            stderr = (stderr or "").strip()
            result = process_result_from_output("analysis", int(process.returncode or 0), stdout, stderr)
            metadata = result.payload or {}
            degraded = result.exit_code == 1 and metadata.get("status") == "degraded"
            if rebuild_was_cancelled(result, cancel_enforced=cancel_enforced):
                progress_control.write_progress_to_path(
                    progress_file,
                    operation_id=operation_id,
                    status="cancelled",
                    phase=str(metadata.get("phase") or "cancelled"),
                    phase_index=self.int_metadata(metadata, "phase_index"),
                    checkpoint=str(metadata.get("checkpoint") or ""),
                    phase_progress=self.numeric_metadata(metadata, "phase_progress"),
                )
                self.send_json(
                    {
                        "ok": False,
                        "cancelled": True,
                        **metadata,
                        "returncode": result.exit_code,
                        "elapsed_ms": round((time.monotonic() - started) * 1000),
                    }
                )
                return
            if result.exit_code != 0 and not degraded:
                progress_control.write_progress_to_path(
                    progress_file,
                    operation_id=operation_id,
                    status="failed",
                    phase="failed",
                    phase_index=0,
                    checkpoint="failed",
                )
                if metadata.get("error") == "analysis_or_cleanup_running":
                    self.send_json(
                        {
                            **operation_state.service_busy_payload(lock_path=metadata.get("lock_path")),
                            "returncode": result.exit_code,
                        },
                        409,
                    )
                    return
                if metadata.get("error") == "normalize_pending_publish_recovery_failed":
                    self.send_json(
                        {
                            "error": "normalize_pending_publish_recovery_failed",
                            "returncode": result.exit_code,
                            "message": metadata.get("message"),
                            "marker_path": metadata.get("marker_path"),
                            "recovery_required": bool(metadata.get("recovery_required")),
                        },
                        409,
                    )
                    return
                self.send_json(
                    {
                        "error": "rebuild_failed",
                        "returncode": result.exit_code,
                        "stderr": stderr[-4000:],
                        "stdout": stdout[-4000:],
                    },
                    500,
                )
                return
            contract_error = required_json_contract_error(result, operation="analysis")
            if contract_error is not None:
                progress_control.write_progress_to_path(
                    progress_file,
                    operation_id=operation_id,
                    status="failed",
                    phase="failed",
                    phase_index=0,
                    checkpoint="result-contract",
                )
                self.send_json(contract_error, 500)
                return
            if "elapsed_ms" in metadata:
                metadata["analysis_elapsed_ms"] = metadata.pop("elapsed_ms")
            metadata["pre_analysis_rotate"] = metadata.get("pre_analysis_rotate", {"skipped": True})
            try:
                progress_control.write_progress_to_path(
                    progress_file,
                    operation_id=operation_id,
                    status="running",
                    phase="refresh",
                    phase_index=2,
                    checkpoint="cleanup-retention-index",
                    phase_progress=0.65,
                )
                retention_index = dashboard_cleanup.refresh_retention_index_for_current_sources(self.dashboard_output_dir())
                metadata["cleanup_retention_index"] = {
                    "sources": len(retention_index.get("sources", [])),
                    "scanned_rows": sum(int(source.get("scanned_rows") or 0) for source in retention_index.get("sources", [])),
                }
            except Exception as exc:
                metadata["cleanup_retention_index"] = {"error": repr(exc)}
            progress_control.write_progress_to_path(
                progress_file,
                operation_id=operation_id,
                status="completed",
                phase="refresh",
                phase_index=2,
                checkpoint="completed-degraded" if degraded else "completed",
                phase_progress=1.0,
            )
            self.send_json(
                {
                    "ok": True,
                    "data_health": "degraded" if degraded else "ok",
                    **metadata,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
            )
        finally:
            cancel_file.unlink(missing_ok=True)
            progress_file.unlink(missing_ok=True)
            progress_control.forget_progress(progress_file)
            lease.close()

    def handle_rebuild_cancel(self):
        operation_id = self.canonical_operation_id(self.read_json_body().get("operation_id"))
        if operation_id is None:
            self.send_json({"error": "invalid_operation_id"}, 400)
            return
        manager = self.dashboard_operation_manager()
        try:
            active = manager.request_analysis_cancel(operation_id)
        except operation_state.AnalysisNotRunning:
            self.send_json({"error": "analysis_not_running"}, 409)
            return
        except operation_state.OperationIdMismatch:
            self.send_json({"error": "operation_id_mismatch"}, 409)
            return
        graceful = False
        process = active.process
        cancel_file = active.cancel_file
        if cancel_file is not None:
            try:
                cancel_control.request_cancel(cancel_file, reason="user")
                graceful = True
            except OSError:
                graceful = False
        self.send_json(
            {
                "ok": True,
                "operation_id": operation_id,
                "cancel_requested": True,
                "graceful": graceful,
                "process_running": process is not None and process.poll() is None,
            }
        )

    def handle_rebuild_progress(self):
        active, progress_file, _running = self.dashboard_operation_manager().progress_snapshot("analysis")
        process = active.process if active is not None else None
        payload = progress_control.read_progress(progress_file)
        if active is not None:
            payload["operation_id"] = active.operation_id
        payload["process_running"] = process is not None and process.poll() is None
        self.send_json(payload)
