#!/usr/bin/env python3
"""Fail if backend/ and runtime/backend/ have diverged.

runtime/ is a SEPARATE codebase: it is what Bedrock AgentCore executes, while
backend/ is what the Lambda poller, worker and console execute. A fix applied to
one and not the other produces a system that behaves differently from its source,
which is indistinguishable from a mysterious bug.

Run before every deploy:
    python3 scripts/check_sync.py
"""
from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
RUNTIME = ROOT / "runtime" / "backend"

SKIP = {"__pycache__", ".pytest_cache", "tests", ".venv"}


def digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def main() -> int:
    if not RUNTIME.exists():
        print("runtime/backend not found — nothing to compare.")
        return 0

    drift: list[tuple[str, str]] = []
    checked = 0

    for rt in sorted(RUNTIME.rglob("*")):
        if not rt.is_file() or rt.suffix not in {".py", ".md", ".cedar"}:
            continue
        if any(part in SKIP for part in rt.parts):
            continue
        rel = rt.relative_to(RUNTIME)
        src = BACKEND / rel
        if not src.exists():
            drift.append((str(rel), "missing in backend/"))
            continue
        checked += 1
        if digest(src) != digest(rt):
            drift.append((str(rel), "CONTENT DIFFERS"))

    if not drift:
        print(f"in sync — {checked} shared files identical")
        return 0

    print(f"OUT OF SYNC — {len(drift)} file(s) differ between backend/ and runtime/backend/\n")
    for rel, why in drift:
        print(f"  {why:<22} {rel}")
    print("\nThe deployed agent will not match your source. Copy the changed files")
    print("into runtime/backend/ (or back) before deploying, then re-run this check.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
