from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from import_character_assets import ASSETS_DIR, PLUGIN_DIR, resolve_assets_dir

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy existing character images into the configured asset directory.")
    parser.add_argument(
        "--source-dir",
        default=str(PLUGIN_DIR / "assets" / "characters"),
        help="Existing character image directory.",
    )
    parser.add_argument("--assets-dir", default="", help="Destination character image directory.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned copies without writing.")
    parser.add_argument("--verbose", action="store_true", help="Print each copied file.")
    args = parser.parse_args()

    source_dir = resolve_assets_dir(args.source_dir)
    assets_dir = resolve_assets_dir(args.assets_dir) if args.assets_dir else ASSETS_DIR
    copied = copy_tree(source_dir, assets_dir, overwrite=args.overwrite, dry_run=args.dry_run, verbose=args.verbose)
    print(f"{'Would copy' if args.dry_run else 'Copied'} {copied} files: {source_dir} -> {assets_dir}")
    return 0


def copy_tree(source_dir: Path, assets_dir: Path, *, overwrite: bool, dry_run: bool, verbose: bool) -> int:
    if not source_dir.exists():
        print(f"Source directory does not exist: {source_dir}")
        return 0

    copied = 0
    for source in source_dir.rglob("*"):
        if not source.is_file():
            continue
        destination = assets_dir / source.relative_to(source_dir)
        if destination.exists() and not overwrite:
            continue
        copied += 1
        if dry_run:
            if verbose:
                print(f"{source} -> {destination}")
            continue
        if verbose:
            print(f"{source} -> {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return copied


if __name__ == "__main__":
    raise SystemExit(main())
