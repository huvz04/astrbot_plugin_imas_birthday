from __future__ import annotations

import argparse
import sys
from pathlib import Path

from import_character_assets import ASSETS_DIR, IMAGE_SUFFIXES, resolve_assets_dir, write_mapping

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate character_assets.py by scanning the configured local character image directory."
    )
    parser.add_argument("--assets-dir", default="", help="Directory that stores character images.")
    parser.add_argument("--dry-run", action="store_true", help="Print discovered mappings without writing.")
    args = parser.parse_args()

    assets_dir = resolve_assets_dir(args.assets_dir) if args.assets_dir else ASSETS_DIR
    mapping = scan_assets(assets_dir)
    if not mapping:
        print(f"No character images found in {assets_dir}")
        return 1

    print(f"Found {len(mapping)} local character images in {assets_dir}")
    if args.dry_run:
        for name in sorted(mapping):
            print(f"{name}: {mapping[name]}")
        return 0

    write_mapping(mapping, generated_by="tools/sync_character_assets.py")
    print("Wrote mapping from local images only.")
    return 0


def scan_assets(assets_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not assets_dir.exists():
        return mapping

    for path in sorted(assets_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            relative_path = path.relative_to(assets_dir).as_posix()
        except ValueError:
            continue
        name = path.stem.strip()
        if not name:
            continue
        mapping.setdefault(name, relative_path)
    return mapping


if __name__ == "__main__":
    raise SystemExit(main())
