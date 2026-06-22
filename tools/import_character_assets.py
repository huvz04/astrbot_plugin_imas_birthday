from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_DIR / "assets" / "characters"
GENERATED_MAPPING = PLUGIN_DIR / "character_assets.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local or remote character images for birthday cards.")
    parser.add_argument("csv_path", help="CSV with columns: name, brand, source, filename(optional)")
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between downloads in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned operations without writing files.")
    args = parser.parse_args()

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
        destination = ASSETS_DIR / relative_path
        mapping[name] = relative_path.replace("\\", "/")
        print(f"{name}: {source} -> {destination}")
        if args.dry_run:
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
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
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


def write_mapping(mapping: dict[str, str]):
    lines = [
        "# Auto-generated by tools/import_character_assets.py.",
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
