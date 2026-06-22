from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


PLUGIN_DIR = Path(__file__).resolve().parents[1]
GENERATED_MAPPING = PLUGIN_DIR / "character_assets.py"


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def configured_assets_dir() -> Path | None:
    config_path = PLUGIN_DIR.parent.parent / "config" / f"{PLUGIN_DIR.name}_config.json"
    if not config_path.exists():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = str(data.get("character_assets_dir", "") or "").strip()
    if not value:
        return None
    return resolve_assets_dir(value)


def resolve_assets_dir(value: str) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    return path if path.is_absolute() else PLUGIN_DIR / path


def default_assets_dir() -> Path:
    config_value = configured_assets_dir()
    if config_value:
        return config_value
    env_value = os.environ.get("IMAS_BIRTHDAY_ASSETS_DIR", "").strip()
    if env_value:
        return resolve_assets_dir(env_value)
    if PLUGIN_DIR.parent.name == "plugins":
        return PLUGIN_DIR.parent.parent / "imas_birthday_assets" / "characters"
    return PLUGIN_DIR / "assets" / "characters"


ASSETS_DIR = default_assets_dir()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local or remote character images for birthday cards.")
    parser.add_argument("csv_path", help="CSV with columns: name, brand, source, filename(optional)")
    parser.add_argument("--assets-dir", default="", help="Directory to store character images.")
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between downloads in seconds.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing image files.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned operations without writing files.")
    args = parser.parse_args()
    assets_dir = resolve_assets_dir(args.assets_dir) if args.assets_dir else ASSETS_DIR

    csv_path = Path(args.csv_path)
    rows = read_rows(csv_path)
    if not rows:
        print("No rows found.")
        return 1

    mapping: dict[str, str] = {}
    for row in rows:
        name = row["name"].strip()
        brand = normalize_brand(row.get("brand", "imas"))
        source = row["source"].strip()
        filename = row.get("filename", "").strip() or build_filename(name, source)
        relative_path = f"{brand}/{filename}"
        destination = assets_dir / relative_path
        mapping[name] = relative_path.replace("\\", "/")
        print(f"{name}: {source} -> {destination}")
        if args.dry_run:
            continue
        if destination.exists() and not args.overwrite:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        import_one(source, destination)
        if is_url(source) and args.sleep > 0:
            time.sleep(args.sleep)

    if not args.dry_run:
        write_mapping(mapping)
        print(f"Wrote mapping: {GENERATED_MAPPING}")
    return 0


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        required = {"name", "source"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"CSV missing columns: {', '.join(sorted(missing))}")
        return [row for row in reader if row.get("name", "").strip() and row.get("source", "").strip()]


def normalize_brand(value: str) -> str:
    value = re.sub(r"[^0-9a-zα]+", "_", value.strip().lower()).strip("_")
    aliases = {
        "the_idolmaster": "the_idolmaster",
        "idolmaster": "the_idolmaster",
        "imas": "the_idolmaster",
        "765": "the_idolmaster",
        "765as": "the_idolmaster",
        "765pro": "the_idolmaster",
        "cinderellagirls": "cinderellagirls",
        "cinderella_girls": "cinderellagirls",
        "cinderella": "cinderellagirls",
        "cg": "cinderellagirls",
        "346": "cinderellagirls",
        "millionlive": "millionlive",
        "million_live": "millionlive",
        "million": "millionlive",
        "ml": "millionlive",
        "sidem": "sidem",
        "315": "sidem",
        "shinycolors": "shinycolors",
        "shiny_colors": "shinycolors",
        "shiny": "shinycolors",
        "sc": "shinycolors",
        "283": "shinycolors",
        "gakuen_idolmaster": "gakuen_idolmaster",
        "gakuen": "gakuen_idolmaster",
        "gakumas": "gakuen_idolmaster",
        "gkm": "gakuen_idolmaster",
        "va_liv": "va_liv",
        "valiv": "va_liv",
        "va": "va_liv",
        "vα_liv": "va_liv",
        "dearlystars": "dearlystars",
        "dearly_stars": "dearlystars",
        "dearly": "dearlystars",
        "ds": "dearlystars",
        "876": "876_pro",
        "876pro": "876_pro",
        "876_pro": "876_pro",
        "starlitseason": "starlitseason",
        "starlit_season": "starlitseason",
        "starlit": "starlitseason",
        "961": "961_pro",
        "961pro": "961_pro",
        "961_pro": "961_pro",
        "kr": "kr",
    }
    return aliases.get(value, value or "other")


def build_filename(name: str, source: str) -> str:
    suffix = extension_from_source(source)
    return f"{safe_filename(name)}{suffix}"


def extension_from_source(source: str) -> str:
    path = urllib.parse.urlparse(source).path if is_url(source) else source
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return suffix
    return ".png"


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\s]+', "_", value.strip())
    value = value.strip("._")
    return value or "character"


def import_one(source: str, destination: Path):
    if is_url(source):
        request = urllib.request.Request(
            source,
            headers={
                "User-Agent": "AstrBot-IMAS-Birthday-AssetImporter/0.1",
                "Referer": urllib.parse.urljoin(source, "/"),
            },
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            destination.write_bytes(response.read())
        return

    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    shutil.copy2(source_path, destination)


def write_mapping(mapping: dict[str, str], generated_by: str = "tools/import_character_assets.py"):
    lines = [
        f"# Auto-generated by {generated_by}.",
        "# Edit the CSV source and regenerate this file instead of editing by hand.",
        "CHARACTER_IMAGE_ASSETS = {",
    ]
    for name in sorted(mapping):
        lines.append(f"    {name!r}: {mapping[name]!r},")
    lines.append("}")
    lines.append("")
    GENERATED_MAPPING.write_text("\n".join(lines), encoding="utf-8")


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


if __name__ == "__main__":
    raise SystemExit(main())
