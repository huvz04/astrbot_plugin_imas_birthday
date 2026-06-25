from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

from fetch_portrait_assets import MLTD_ID_TO_CN, ROOT


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


OUT = ROOT / "asset_candidates"
MATSURIHI_CARDS_API = "https://api.matsurihi.me/api/mltd/v2/cards?prettyPrint=false"
MATSURIHI_VERSION_API = "https://api.matsurihi.me/api/mltd/v2/version/latest"
MATSURIHI_CARD_BG = "https://fruity-love.matsurihi.me/mltd/card_bg/{resource_id}_{variant}.jpg?w={width}"
MATSURIHI_CARD_DETAIL = "https://mltd.matsurihi.me/zh/cards/{card_id}"
MLTH_CHARACTER_PAGE = "https://imas.gamedbs.jp/mlth/chara/show/{idol_id}"
MLTH_BASE = "https://imas.gamedbs.jp/mlth/"


RARITY_NAMES = {
    1: "N",
    2: "R",
    3: "SR",
    4: "SSR",
}

CATEGORY_NAMES = {
    10: "normal",
    20: "limited",
    30: "fes",
    40: "anniversary",
    50: "song",
    60: "event",
    70: "special",
}

CARD_SUMMARY_FIELDS = [
    "id",
    "sortId",
    "idolId",
    "character_cn",
    "name",
    "rarity",
    "rarity_name",
    "category",
    "category_name",
    "resourceId",
    "card_bg_0",
    "card_bg_1",
    "detail_url",
]

PORTRAIT_FIELDS = [
    "idolId",
    "character_cn",
    "brand_dir",
    "page_url",
    "portrait_url",
    "local_path",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch useful Million Live DB data for asset browsing.")
    parser.add_argument("--output-dir", type=Path, default=OUT, help="Directory to write JSON/CSV data.")
    parser.add_argument("--width", type=int, default=960, help="Width parameter for Matsurihi card_bg URLs.")
    parser.add_argument("--skip-matsurihi", action="store_true", help="Do not fetch Matsurihi card data.")
    parser.add_argument("--skip-gamedbs", action="store_true", help="Do not fetch gamedbs character portrait URLs.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    with httpx.Client(headers={"User-Agent": "AstrBot-IMAS-Birthday-DataFetcher/0.1"}, follow_redirects=True, timeout=60) as client:
        if not args.skip_matsurihi:
            version = get_json(client, MATSURIHI_VERSION_API)
            cards = get_json(client, MATSURIHI_CARDS_API)
            write_json(output_dir / "mltd_matsurihi_version_latest.json", version)
            write_json(output_dir / "mltd_matsurihi_cards_raw.json", cards)
            write_card_summary(output_dir / "mltd_matsurihi_cards_summary.csv", cards, args.width)
            print(f"Matsurihi cards: {len(cards)}")
            print(f"Matsurihi app version: {version.get('app', {}).get('version')}")
            print(f"Matsurihi asset version: {version.get('asset', {}).get('version')}")

        if not args.skip_gamedbs:
            rows = fetch_gamedbs_portraits(client)
            write_csv(output_dir / "mltd_gamedbs_portraits.csv", PORTRAIT_FIELDS, rows)
            print(f"gamedbs portrait rows: {len(rows)}")

    print(f"Wrote data under {output_dir}")
    return 0


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")


def write_card_summary(path: Path, cards: list[dict[str, object]], width: int) -> None:
    rows = []
    for card in cards:
        resource_id = str(card.get("resourceId") or "")
        idol_id = int(card.get("idolId") or 0)
        rarity = int(card.get("rarity") or 0)
        category = int(card.get("category") or 0)
        rows.append(
            {
                "id": card.get("id", ""),
                "sortId": card.get("sortId", ""),
                "idolId": idol_id,
                "character_cn": MLTD_ID_TO_CN.get(idol_id, ""),
                "name": card.get("name", ""),
                "rarity": rarity,
                "rarity_name": RARITY_NAMES.get(rarity, str(rarity)),
                "category": category,
                "category_name": CATEGORY_NAMES.get(category, str(category)),
                "resourceId": resource_id,
                "card_bg_0": card_bg_url(resource_id, 0, width),
                "card_bg_1": card_bg_url(resource_id, 1, width),
                "detail_url": MATSURIHI_CARD_DETAIL.format(card_id=card.get("id", "")),
            }
        )
    write_csv(path, CARD_SUMMARY_FIELDS, rows)


def card_bg_url(resource_id: str, variant: int, width: int) -> str:
    if not resource_id:
        return ""
    return MATSURIHI_CARD_BG.format(resource_id=resource_id, variant=variant, width=width)


def fetch_gamedbs_portraits(client: httpx.Client) -> list[dict[str, str]]:
    rows = []
    for idol_id, name in MLTD_ID_TO_CN.items():
        page_url = MLTH_CHARACTER_PAGE.format(idol_id=idol_id)
        text = get_text(client, page_url)
        rels = unique(re.findall(r"image/chara/img/[^\"']+\.png", text))
        portrait_url = urllib.parse.urljoin(MLTH_BASE, rels[0]) if rels else ""
        brand = "the_idolmaster" if idol_id <= 13 else "millionlive"
        rows.append(
            {
                "idolId": str(idol_id),
                "character_cn": name,
                "brand_dir": brand,
                "page_url": page_url,
                "portrait_url": portrait_url,
                "local_path": f"assets/portraits/{brand}/{name}.png",
            }
        )
    return rows


def unique(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def get_json(client: httpx.Client, url: str) -> object:
    return json.loads(get_text(client, url))


def get_text(client: httpx.Client, url: str, retries: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(0.8 * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {path}")


if __name__ == "__main__":
    raise SystemExit(main())
