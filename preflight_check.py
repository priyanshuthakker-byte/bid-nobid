#!/usr/bin/env python3
"""
Preflight checks to catch deployment-breaking issues before uvicorn startup.
"""

from pathlib import Path


REQUIRED_FILES = [
    "main.py",
    "ai_analyzer.py",
    "index.html",
    "render.yaml",
]

CONFLICT_SCAN_FILES = [
    "main.py",
    "ai_analyzer.py",
    "index.html",
]

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def ensure_files_exist() -> None:
    for rel in REQUIRED_FILES:
        p = Path(rel)
        if not p.exists():
            raise SystemExit(f"Preflight failed: required file missing: {rel}")


def compile_python(rel: str) -> None:
    src = Path(rel).read_text(encoding="utf-8")
    compile(src, rel, "exec")


def scan_conflicts(rel: str) -> None:
    text = Path(rel).read_text(encoding="utf-8", errors="ignore")
    for marker in CONFLICT_MARKERS:
        if marker in text:
            raise SystemExit(f"Preflight failed: merge marker '{marker}' found in {rel}")


def main() -> None:
    ensure_files_exist()
    compile_python("main.py")
    compile_python("ai_analyzer.py")
    for rel in CONFLICT_SCAN_FILES:
        scan_conflicts(rel)
    render_text = Path("render.yaml").read_text(encoding="utf-8", errors="ignore")
    expected = "python preflight_check.py && uvicorn main:app --host 0.0.0.0 --port $PORT"
    if expected not in render_text:
        raise SystemExit("Preflight failed: render.yaml startCommand must run preflight before uvicorn.")
    print("Preflight passed: syntax and conflict checks are clean.")


if __name__ == "__main__":
    main()
