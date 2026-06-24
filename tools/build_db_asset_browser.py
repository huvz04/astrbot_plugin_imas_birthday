from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import sys
from pathlib import Path

import httpx


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "asset_candidates"
DEFAULT_OUTPUT = OUT / "asset_candidates_db_browser.csv"
SHINY_ROWS_JSON = OUT / "shiny_full_rows.json"
MATSURIHI_CARDS_API = "https://api.matsurihi.me/mltd/v1/cards"
MATSURIHI_CARD_BG = "https://fruity-love.matsurihi.me/mltd/card_bg/{resource_id}_1.jpg?w=640"


SHINY_ID_TO_CN = {
    1: "樱木真乃",
    2: "风野灯织",
    3: "八宫巡",
    4: "月冈恋钟",
    5: "田中摩美美",
    6: "白濑咲耶",
    7: "三峰结华",
    8: "幽谷雾子",
    9: "小宫果穗",
    10: "园田智代子",
    11: "西城树里",
    12: "杜野凛世",
    13: "有栖川夏叶",
    14: "大崎甘奈",
    15: "大崎甜花",
    16: "桑山千雪",
    17: "芹泽朝日",
    18: "黛冬优子",
    19: "和泉爱依",
    20: "浅仓透",
    21: "樋口圆香",
    22: "福丸小糸",
    23: "市川雏菜",
    24: "七草日花",
    25: "绯田美琴",
    26: "斑鸠路加",
    27: "铃木羽那",
    28: "郁田阳希",
    29: "七草叶月",
}

MLTD_ID_TO_CN = {
    1: "天海春香",
    2: "如月千早",
    3: "星井美希",
    4: "萩原雪步",
    5: "高槻弥生",
    6: "菊地真",
    7: "水濑伊织",
    8: "四条贵音",
    9: "秋月律子",
    10: "三浦梓",
    11: "双海亚美",
    12: "双海真美",
    13: "我那霸响",
    14: "春日未来",
    15: "最上静香",
    16: "伊吹翼",
    17: "田中琴叶",
    18: "岛原埃琳娜",
    19: "佐竹美奈子",
    20: "所惠美",
    21: "德川茉莉",
    22: "箱崎星梨花",
    23: "野野原茜",
    24: "望月杏奈",
    25: "Roco",
    26: "七尾百合子",
    27: "高山纱代子",
    28: "松田亚利沙",
    29: "高坂海美",
    30: "中谷育",
    31: "天空桥朋花",
    32: "艾米莉·斯图亚特",
    33: "北泽志保",
    34: "舞滨步",
    35: "木下日向",
    36: "矢吹可奈",
    37: "横山奈绪",
    38: "二阶堂千鹤",
    39: "马场木实",
    40: "大神环",
    41: "丰川风花",
    42: "宫尾美也",
    43: "福田法子",
    44: "真壁瑞希",
    45: "篠宫可怜",
    46: "百濑莉绪",
    47: "永吉昴",
    48: "北上丽花",
    49: "周防桃子",
    50: "茱莉亚",
    51: "白石䌷",
    52: "樱守歌织",
    53: "青羽美咲",
}


FIELDNAMES = [
    "brand",
    "character",
    "card_name",
    "target_filename",
    "target_dir",
    "save_as_name",
    "save_as_file",
    "source",
    "kind",
    "file",
    "url",
    "detail_url",
]


def load_character_assets() -> dict[str, str]:
    tree = ast.parse((ROOT / "character_assets.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CHARACTER_IMAGE_ASSETS":
            return ast.literal_eval(node.value)
    return {}


def cid_from_detail(url: str) -> int | None:
    match = re.search(r"/chara/show/(\d+)(?:/|$)", url or "")
    return int(match.group(1)) if match else None


def card_title_from_shiny(value: str) -> str:
    value = str(value or "")
    value = value.replace(" 情報 | シャニマスギャラリー【シャニマス/シャニソンDB】", "")
    return value.strip() or "card"


def target_for(character_assets: dict[str, str], character: str, fallback_brand_dir: str, fallback_ext: str) -> str:
    mapped = character_assets.get(character)
    if mapped:
        return mapped.replace("\\", "/")
    return f"{fallback_brand_dir}/{character}{fallback_ext}"


def add_shiny_rows(rows: list[dict[str, str]], character_assets: dict[str, str]):
    if not SHINY_ROWS_JSON.exists():
        print(f"跳过闪彩：未找到 {SHINY_ROWS_JSON}。先运行旧的 full-still 抓取流程即可生成。")
        return
    source_rows = json.loads(SHINY_ROWS_JSON.read_text(encoding="utf-8"))
    for source in source_rows:
        cid = cid_from_detail(source.get("detail_url") or source.get("url"))
        character = SHINY_ID_TO_CN.get(cid or -1)
        if not character:
            continue
        target = target_for(character_assets, character, "shinycolors", ".png")
        rows.append(
            {
                "brand": "SHINY COLORS",
                "character": character,
                "card_name": card_title_from_shiny(source.get("name", "")),
                "target_filename": target,
                "target_dir": str(Path(target).parent).replace("\\", "/"),
                "save_as_name": Path(target).name,
                "save_as_file": source.get("save_as_file", ""),
                "source": "imassc.gamedbs.jp full still",
                "kind": "shiny_full_still",
                "file": source.get("file", ""),
                "url": source.get("url", ""),
                "detail_url": source.get("detail_url", ""),
            }
        )


def add_mltd_rows(rows: list[dict[str, str]], character_assets: dict[str, str]):
    cards = httpx.get(MATSURIHI_CARDS_API, timeout=60).json()
    for card in cards:
        # Matsurihi card_bg only exists for higher-rarity illustration cards.
        # Normal/R cards tend to 404 and are not useful as replacement artwork.
        if int(card.get("rarity") or 0) < 4:
            continue
        character = MLTD_ID_TO_CN.get(int(card.get("idolId") or 0))
        resource_id = card.get("resourceId")
        if not character or not resource_id:
            continue
        mapped = character_assets.get(character, "")
        fallback_dir = "the_idolmaster" if int(card.get("idolId") or 0) <= 13 else "millionlive"
        target = target_for(character_assets, character, fallback_dir, ".png")
        brand = "THE IDOLM@STER" if mapped.startswith("the_idolmaster/") or fallback_dir == "the_idolmaster" else "MILLION LIVE!"
        rows.append(
            {
                "brand": brand,
                "character": character,
                "card_name": str(card.get("name") or character),
                "target_filename": target,
                "target_dir": str(Path(target).parent).replace("\\", "/"),
                "save_as_name": Path(target).name,
                "save_as_file": "",
                "source": "mltd.matsurihi.me card_bg",
                "kind": "mltd_card_bg",
                "file": "",
                "url": MATSURIHI_CARD_BG.format(resource_id=resource_id),
                "detail_url": f"https://mltd.matsurihi.me/zh/cards/{card.get('id')}",
            }
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Build CSV for the local asset browser.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--source",
        action="append",
        choices=["shiny", "mltd"],
        help="Source to include. Repeatable. Default: shiny + mltd.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    sources = set(args.source or ["shiny", "mltd"])
    character_assets = load_character_assets()
    rows: list[dict[str, str]] = []
    if "shiny" in sources:
        add_shiny_rows(rows, character_assets)
    if "mltd" in sources:
        add_mltd_rows(rows, character_assets)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.output}")
    print(f"rows {len(rows)}")


if __name__ == "__main__":
    main()
