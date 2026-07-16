#!/usr/bin/env python3
"""Validate the canonical application layout and repository references."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    Path("apps/web/package.json"),
    Path("apps/web/src/app"),
    Path("apps/api/app/main.py"),
    Path("apps/api/tests"),
    Path("apps/vision-worker/main.py"),
    Path("scripts/dev_api.sh"),
)
FORBIDDEN_DIRS = (
    Path("apps") / "frontend",
    Path("apps") / "backend",
    Path("apps") / "api" / "vision-worker",
)
FORBIDDEN_TEXT = (
    "apps/" + "frontend",
    "apps/" + "backend",
    "NEXT_PUBLIC_" + "BACKEND_URL",
    "dev_" + "backend.sh",
)
SKIP_PARTS = {".git", ".next", ".venv", "node_modules", "storage"}
TEXT_SUFFIXES = {".md", ".mdx", ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yaml", ".yml", ".sh"}


def main() -> int:
    errors: list[str] = []

    for relative in REQUIRED:
        if not (ROOT / relative).exists():
            errors.append(f"missing required path: {relative}")

    for relative in FORBIDDEN_DIRS:
        if (ROOT / relative).exists():
            errors.append(f"stale application directory: {relative}")

    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path == Path(__file__).resolve():
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".env.example"}:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT)
        for token in FORBIDDEN_TEXT:
            if token in content:
                errors.append(f"stale reference {token!r}: {relative}")

    if errors:
        print("\n".join(errors))
        return 1
    print("repository layout is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
