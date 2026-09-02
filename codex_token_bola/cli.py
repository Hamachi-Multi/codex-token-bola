from __future__ import annotations

try:
    from ._runtime.bola import main
except ModuleNotFoundError as exc:
    runtime_module = f"{__package__}._runtime"
    if exc.name not in {runtime_module, f"{runtime_module}.bola"}:
        raise
    from scripts.bola import main

__all__ = ["main"]
