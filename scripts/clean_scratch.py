#!/usr/bin/env python3
"""Garbage-collect the API scratch directory.

The FastAPI process treats `apps/api/storage/<category>/` as a disposable
cache for ffmpeg inputs/outputs. Nothing deletes from it, so on a long-
running server it grows forever until the disk fills.

This script walks the scratch tree and deletes files older than
--max-age-hours (default 48h). It's safe to run while the server is up:
every file here can be re-derived either from object storage
(uploads, variants, stitched, exports) or from re-running ffmpeg.

The `uploads/` directory is excluded by default because that's the only
category where the local scratch is sometimes the *only* copy during a
brief window between file receipt and S3 publish. Opt-in via --include-
uploads if you've confirmed everything is published.

# ── cron ──────────────────────────────────────────────────────────
# Install as a user crontab (runs every 6 hours):
#
#   crontab -e
#   0 */6 * * * cd /Users/matthewkim/Documents/fiebatt && \
#     /usr/bin/env python3 scripts/clean_scratch.py >> \
#     /tmp/fiebatt-clean-scratch.log 2>&1
#
# Or as a launchd agent on macOS (~/Library/LaunchAgents/fiebatt.clean.plist).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# canonical scratch layout — keep in sync with app.services.storage
DEFAULT_CATEGORIES = (
    "clips",
    "keyframes",
    "variants",
    "stitched",
    "exports",
)
OPTIONAL_CATEGORIES = ("uploads",)

log = logging.getLogger("fiebatt.clean_scratch")


def _resolve_scratch_root(explicit: Path | None) -> Path:
    """Find the scratch dir under the repository's canonical API app."""
    if explicit is not None:
        return explicit.resolve()
    here = Path(__file__).resolve().parent.parent
    candidate = here / "apps" / "api" / "storage"
    if candidate.exists():
        return candidate
    return (Path.cwd() / "apps" / "api" / "storage").resolve()


def _sweep(directory: Path, *, cutoff: float, dry_run: bool) -> tuple[int, int]:
    """Delete files in `directory` whose mtime is older than `cutoff` epoch.

    Returns (deleted_count, freed_bytes).
    """
    if not directory.exists():
        return (0, 0)
    freed = 0
    deleted = 0
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        if st.st_mtime >= cutoff:
            continue
        size = st.st_size
        log.debug("%s  age=%.1fh  size=%s", path, (time.time() - st.st_mtime) / 3600, size)
        if dry_run:
            deleted += 1
            freed += size
            continue
        try:
            path.unlink()
            deleted += 1
            freed += size
        except OSError as e:
            log.warning("could not delete %s: %s", path, e)
    return deleted, freed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="prune scratch videos/keyframes")
    ap.add_argument("--root", type=Path, default=None,
                    help="scratch dir (default: ./apps/api/storage)")
    ap.add_argument("--max-age-hours", type=float, default=48.0,
                    help="delete files older than this (default: 48)")
    ap.add_argument("--include-uploads", action="store_true",
                    help="also prune uploads/ (risky — uploads may be the only copy)")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be deleted without touching disk")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = _resolve_scratch_root(args.root)
    if not root.exists():
        log.error("scratch root does not exist: %s", root)
        return 1

    cutoff = time.time() - (args.max_age_hours * 3600)
    cats = list(DEFAULT_CATEGORIES)
    if args.include_uploads:
        cats += list(OPTIONAL_CATEGORIES)

    log.info("scratch root: %s", root)
    log.info("max age: %.1fh  (cutoff: %s)", args.max_age_hours,
             time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cutoff)))
    log.info("dry run: %s", args.dry_run)

    total_deleted = 0
    total_freed = 0
    for cat in cats:
        d, b = _sweep(root / cat, cutoff=cutoff, dry_run=args.dry_run)
        log.info("  %-10s  removed=%-5d  freed=%s", cat, d, _human(b))
        total_deleted += d
        total_freed += b

    log.info("total: removed=%d  freed=%s", total_deleted, _human(total_freed))
    return 0


def _human(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024.0:
            return f"{b:.1f}{unit}"
        b /= 1024.0
    return f"{b:.1f}TB"


if __name__ == "__main__":
    sys.exit(main())
