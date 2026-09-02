from __future__ import annotations

try:
    from ._runtime.hook import main
except ModuleNotFoundError as exc:
    runtime_module = f"{__package__}._runtime"
    if exc.name not in {runtime_module, f"{runtime_module}.hook"}:
        raise
    from scripts.hook import main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
