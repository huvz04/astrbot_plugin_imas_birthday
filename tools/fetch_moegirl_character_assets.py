from __future__ import annotations

import argparse
import html
import re
import sys
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import httpx

from import_character_assets import ASSETS_DIR, GENERATED_MAPPING, safe_filename, write_mapping


SOURCE_URL = (
    "https://zh.moegirl.org.cn/"
    "%E5%81%B6%E5%83%8F%E5%A4%A7%E5%B8%88%E7%B3%BB%E5%88%97/"
    "%E7%9B%B8%E5%85%B3%E4%BA%BA%E5%A3%AB%E7%94%9F%E6%97%A5%E4%BF%A1%E6%81%AF"
)
API_URL = "https://zh.moegirl.org.cn/api.php"
USER_AGENT = "AstrBot-IMAS-Birthday-MoegirlAssetFetcher/0.1"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MONTH_NAMES = {
    "一月",
    "二月",
    "三月",
    "四月",
    "五月",
    "六月",
    "七月",
    "八月",
    "九月",
    "十月",
    "十一月",
    "十二月",
}

COLOR_BRANDS = {
    "#F34F6D": "the_idolmaster",
    "#2681C8": "cinderellagirls",
    "#FFC30B": "millionlive",
    "#0FBE94": "sidem",
    "#8DBBFF": "shinycolors",
    "#F39800": "gakuen_idolmaster",
    "#656A75": "va_liv",
    "#ED000C": "kr",
    "#E5000F": "kr",
}

CHARACTER_LINK_RE = re.compile(
    r"<span\b"
    r"(?=[^>]*title=\"(?P<color>#[0-9A-Fa-f]{6})\")"
    r"(?=[^>]*background-color:\s*(?P=color))"
    r"[^>]*>\s*</span>\s*"
    r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)

ATTR_RE = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['\"])(.*?)\2", re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
IMAGE_EXT_RE = re.compile(r"\.(png|jpe?g|webp)(?=[!/]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class CharacterLink:
    name: str
    page_title: str
    href: str
    brand: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Idolmaster character card images from Moegirl birthday links."
    )
    parser.add_argument("--source-url", default=SOURCE_URL, help="Moegirl birthday page URL.")
    parser.add_argument("--size", type=int, default=900, help="Requested thumbnail size.")
    parser.add_argument("--sleep", type=float, default=0.25, help="Delay between pageimage API calls.")
    parser.add_argument("--limit", type=int, default=0, help="Fetch at most N characters, for testing.")
    parser.add_argument("--overwrite", action="store_true", help="Redownload images even if files exist.")
    parser.add_argument("--quiet", action="store_true", help="Only print skipped, failed, and summary lines.")
    parser.add_argument("--dry-run", action="store_true", help="Print planned operations without writing files.")
    args = parser.parse_args()

    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        characters = parse_character_links(fetch_text(client, args.source_url))
        if args.limit > 0:
            characters = characters[: args.limit]

        if not characters:
            print("No character links found.")
            return 1

        print(f"Found {len(characters)} linked character entries.")
        mapping: dict[str, str] = {}
        missing: list[str] = []
        failed: list[str] = []

        for index, character in enumerate(characters, start=1):
            try:
                image_url = fetch_pageimage_url(client, character.page_title, args.size)
            except httpx.HTTPError as exc:
                existing_path = existing_relative_path(character)
                if existing_path:
                    mapping[character.name] = existing_path
                    if not args.quiet:
                        print(
                            f"[{index}/{len(characters)}] keep: {character.name} uses existing {existing_path}"
                        )
                    continue
                print(f"[{index}/{len(characters)}] fail: {character.name} pageimage API error: {exc}")
                failed.append(character.name)
                continue
            if not image_url:
                existing_path = existing_relative_path(character)
                if existing_path:
                    mapping[character.name] = existing_path
                    if not args.quiet:
                        print(
                            f"[{index}/{len(characters)}] keep: {character.name} uses existing {existing_path}"
                        )
                    continue
                print(f"[{index}/{len(characters)}] skip: {character.name} has no page image.")
                missing.append(character.name)
                continue

            suffix = image_suffix(image_url)
            relative_path = f"{character.brand}/{safe_filename(character.name)}{suffix}"
            destination = ASSETS_DIR / relative_path
            mapping[character.name] = relative_path.replace("\\", "/")
            if not args.quiet:
                print(f"[{index}/{len(characters)}] {character.name}: {image_url} -> {destination}")

            if args.dry_run:
                continue
            if destination.exists() and not args.overwrite:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                download_binary(client, image_url, destination)
            except httpx.HTTPError as exc:
                print(f"[{index}/{len(characters)}] fail: {character.name} download error: {exc}")
                failed.append(character.name)
                mapping.pop(character.name, None)
                continue
            if args.sleep > 0:
                time.sleep(args.sleep)

        if args.dry_run:
            if missing:
                print(f"Missing page images: {', '.join(missing)}")
            return 0

        write_mapping(mapping, generated_by="tools/fetch_moegirl_character_assets.py")
        print(f"Wrote mapping: {GENERATED_MAPPING}")
        if missing:
            print(f"Missing page images: {', '.join(missing)}")
        if failed:
            print(f"Failed downloads/API calls: {', '.join(failed)}")
        return 0


def fetch_text(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    return response.text


def download_binary(client: httpx.Client, url: str, destination: Path):
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        original_url = original_image_url(url)
        if exc.response.status_code != 403 or original_url == url:
            raise
        response = client.get(original_url)
        response.raise_for_status()
    destination.write_bytes(response.content)


def fetch_pageimage_url(client: httpx.Client, page_title: str, size: int) -> str:
    params = {
        "action": "query",
        "format": "json",
        "titles": page_title,
        "prop": "pageimages",
        "pithumbsize": str(size),
    }
    response = client.get(API_URL, params=params)
    response.raise_for_status()
    data = response.json()
    for page in data.get("query", {}).get("pages", {}).values():
        source = page.get("thumbnail", {}).get("source", "")
        if source:
            return str(source)
    return ""


def parse_character_links(page_html: str) -> list[CharacterLink]:
    parser = BirthdayTableParser()
    parser.feed(page_html)
    parser.close()

    characters: dict[str, CharacterLink] = {}
    for cell in parser.character_cells:
        for match in CHARACTER_LINK_RE.finditer(cell):
            color = match.group("color").upper()
            brand = COLOR_BRANDS.get(color)
            if not brand:
                continue

            attrs = parse_attrs(match.group("attrs"))
            href = html.unescape(attrs.get("href", ""))
            if not href or "redlink=1" in href or attrs.get("class") == "new":
                continue

            name = clean_text(match.group("label"))
            page_title = clean_title(attrs.get("title", "")) or page_title_from_href(href) or name
            if not name or name.endswith("系列") or name.startswith("PROJECT IM@S"):
                continue

            characters.setdefault(
                name,
                CharacterLink(
                    name=name,
                    page_title=page_title,
                    href=urllib.parse.urljoin(SOURCE_URL, href),
                    brand=brand,
                ),
            )
    return list(characters.values())


def parse_attrs(attrs_text: str) -> dict[str, str]:
    return {name.lower(): html.unescape(value) for name, _quote, value in ATTR_RE.findall(attrs_text)}


def clean_text(value: str) -> str:
    return html.unescape(TAG_RE.sub("", value)).strip()


def clean_title(value: str) -> str:
    value = html.unescape(value).strip()
    value = re.sub(r"（页面不存在）$", "", value)
    return value


def page_title_from_href(href: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(href))
    if parsed.path == "/index.php":
        query = urllib.parse.parse_qs(parsed.query)
        return query.get("title", [""])[0]
    return urllib.parse.unquote(parsed.path.lstrip("/"))


def image_suffix(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    match = IMAGE_EXT_RE.search(path)
    if match:
        suffix = match.group(1).lower()
        return ".jpg" if suffix == "jpeg" else f".{suffix}"
    return ".png"


def existing_relative_path(character: CharacterLink) -> str:
    stem = safe_filename(character.name)
    brand_dir = ASSETS_DIR / character.brand
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        path = brand_dir / f"{stem}{suffix}"
        if path.exists():
            return f"{character.brand}/{path.name}"
    return ""


def original_image_url(url: str) -> str:
    if "!/" not in url:
        return url
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.split("!/", 1)[0]
    return urllib.parse.urlunparse(parsed._replace(path=path, query=""))


class BirthdayTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.character_cells: list[str] = []
        self._in_month_section = False
        self._in_h2 = False
        self._h2_text: list[str] = []
        self._in_table = False
        self._in_tr = False
        self._in_td = False
        self._cell_index = 0
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = dict(attrs)
        if tag == "h2":
            self._in_h2 = True
            self._h2_text = []
        if self._in_month_section and tag == "table" and "wikitable" in attrs_dict.get("class", ""):
            self._in_table = True
        if self._in_table and tag == "tr":
            self._in_tr = True
            self._cell_index = 0
        if self._in_tr and tag == "td":
            self._in_td = True
            self._current_cell = []
        if self._in_td:
            self._current_cell.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if self._in_td:
            attrs_text = "".join(
                f' {name}="{html.escape(value or "", quote=True)}"' for name, value in attrs
            )
            self._current_cell.append(f"<{tag}{attrs_text}/>")

    def handle_endtag(self, tag: str):
        if tag == "h2" and self._in_h2:
            title = "".join(self._h2_text).strip()
            self._in_month_section = title in MONTH_NAMES
            self._in_h2 = False
            self._h2_text = []
            return
        if self._in_td and tag == "td":
            if self._cell_index == 1:
                self.character_cells.append("".join(self._current_cell))
            self._cell_index += 1
            self._current_cell = []
            self._in_td = False
            return
        if self._in_td:
            self._current_cell.append(f"</{tag}>")
        if self._in_table and tag == "tr":
            self._in_tr = False
            return
        if self._in_table and tag == "table":
            self._in_table = False

    def handle_data(self, data: str):
        if self._in_h2:
            self._h2_text.append(data)
        if self._in_td:
            self._current_cell.append(html.escape(data))


if __name__ == "__main__":
    raise SystemExit(main())
