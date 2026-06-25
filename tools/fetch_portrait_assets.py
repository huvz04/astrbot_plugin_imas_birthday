from __future__ import annotations

import argparse
import ast
import csv
import html
import io
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path

import httpx
from PIL import Image


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[1]
CHARACTER_ASSETS = ROOT / "character_assets.py"
PORTRAIT_MAPPING = ROOT / "character_portraits.py"
COLOR_MAPPING = ROOT / "character_colors.py"
OUT = ROOT / "asset_candidates"
UNMATCHED_CSV = OUT / "portrait_unmatched.csv"

MLTH_BASE = "https://imas.gamedbs.jp/mlth/"
CG_BASE = "https://imas.gamedbs.jp/cg/"
SIDEM_BASE = "https://idolmaster-official.jp"
GAKUMAS_BASE = "https://gakuen.idolmaster-official.jp"
COLOR_URL = "https://imas-db.jp/misc/color.html"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


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

SHINY_SLUG_TO_CN = {
    "mano": "樱木真乃",
    "hiori": "风野灯织",
    "meguru": "八宫巡",
    "kogane": "月冈恋钟",
    "mamimi": "田中摩美美",
    "sakuya": "白濑咲耶",
    "yuika": "三峰结华",
    "kiriko": "幽谷雾子",
    "kaho": "小宫果穗",
    "chiyoko": "园田智代子",
    "juri": "西城树里",
    "rinze": "杜野凛世",
    "natsuha": "有栖川夏叶",
    "amana": "大崎甘奈",
    "tenka": "大崎甜花",
    "chiyuki": "桑山千雪",
    "asahi": "芹泽朝日",
    "fuyuko": "黛冬优子",
    "mei": "和泉爱依",
    "toru": "浅仓透",
    "madoka": "樋口圆香",
    "koito": "福丸小糸",
    "hinana": "市川雏菜",
    "nichika": "七草日花",
    "mikoto": "绯田美琴",
    "luca": "斑鸠路加",
    "hana": "铃木羽那",
    "haruki": "郁田阳希",
    "hazuki": "七草叶月",
}

GAKUMAS_SLUG_TO_CN = {
    "saki": "花海咲季",
    "temari": "月村手毬",
    "kotone": "藤田言音",
    "mao": "有村麻央",
    "lilja": "葛城莉莉娅",
    "china": "仓本千奈",
    "hiro": "篠泽广",
    "sumika": "紫云清夏",
    "rinami": "姬崎莉波",
    "ume": "花海佑芽",
    "misuzu": "秦谷美铃",
    "sena": "十王星南",
    "tsubame": "雨夜燕",
}

SPECIAL_NAME_ALIASES = {
    "八宮めぐる": "八宫巡",
    "藤田ことね": "藤田言音",
    "葛城リーリヤ": "葛城莉莉娅",
    "倉本千奈": "仓本千奈",
    "篠澤広": "篠泽广",
    "紫雲清夏": "紫云清夏",
    "姫崎莉波": "姬崎莉波",
    "秦谷美鈴": "秦谷美铃",
    "天道輝": "天道辉",
    "桜庭薫": "樱庭薰",
    "アスラン＝ベルゼビュートⅡ世": "阿斯兰·别西卜II世",
    "アスラン=ベルゼビュートII世": "阿斯兰·别西卜II世",
    "古論クリス": "古论克里斯",
    "姫野かのん": "姬野花音",
    "猫柳キリオ": "猫柳桐生",
    "渡辺みのり": "渡边实",
    "ピエール": "皮埃尔",
    "東雲荘一郎": "东云庄一郎",
    "大河タケル": "大河武",
    "伊瀬谷四季": "伊濑谷四季",
    "秋月涼": "秋月凉",
    "水本ゆかり": "水本紫",
    "三村かな子": "三村加奈子",
    "五十嵐響子": "五十岚响子",
    "長富蓮実": "长富莲实",
    "関裕美": "关裕美",
    "棟方愛海": "栋方爱海",
    "大原みちる": "大原满",
    "遊佐こずえ": "游佐梢",
    "大沼くるみ": "大沼胡桃",
    "一ノ瀬志希": "一之濑志希",
    "一ﾉ瀬志希": "一之濑志希",
    "前川みく": "前川未来",
    "赤西瑛梨華": "赤西瑛梨华",
    "宮本フレデリカ": "宫本芙蕾德莉卡",
    "楊菲菲": "杨菲菲",
    "桃井あずき": "桃井小豆",
    "涼宮星花": "凉宫星花",
    "月宮雅": "月宫雅",
    "兵藤レナ": "兵藤蕾娜",
    "道明寺歌鈴": "道明寺歌铃",
    "栗原ネネ": "栗原宁宁",
    "佐久間まゆ": "佐久间麻由",
    "白菊ほたる": "白菊萤",
    "村松さくら": "村松樱",
    "渋谷凛": "涩谷凛",
    "桐野アヤ": "桐野绫",
    "東郷あい": "东乡爱",
    "古澤頼子": "古泽赖子",
    "橘ありす": "橘爱丽丝",
    "八神マキノ": "八神牧野",
    "松永涼": "松永凉",
    "高峯のあ": "高峰诺亚",
    "高垣楓": "高垣枫",
    "瀬名詩織": "濑名诗织",
    "吉岡沙紀": "吉冈沙纪",
    "氏家むつみ": "氏家睦",
    "成宮由愛": "成宫由爱",
    "脇山珠美": "胁山珠美",
    "岡崎泰葉": "冈崎泰叶",
    "森久保乃々": "森久保乃乃",
    "望月聖": "望月圣",
    "二宮飛鳥": "二宫飞鸟",
    "衛藤美紗希": "卫藤美纱希",
    "財前時子": "财前时子",
    "野々村そら": "野野村空",
    "浜川愛結奈": "滨川爱结奈",
    "諸星きらり": "诸星琪拉莉",
    "十時愛梨": "十时爱梨",
    "結城晴": "结城晴",
    "高森藍子": "高森蓝子",
    "並木芽衣子": "并木芽衣子",
    "木村夏樹": "木村夏树",
    "斉藤洋子": "齐藤洋子",
    "沢田麻理菜": "泽田麻理菜",
    "赤城みりあ": "赤城米莉亚",
    "愛野渚": "爱野渚",
    "真鍋いつき": "真锅斋",
    "姫川友紀": "姬川友纪",
    "北川真尋": "北川真寻",
    "難波笑美": "难波笑美",
    "浜口あやめ": "滨口菖蒲",
    "冴島清美": "冴岛清美",
    "乙倉悠貴": "乙仓悠贵",
    "桐生つかさ": "桐生司",
    "辻野あかり": "辻野朱里",
    "砂塚あきら": "砂冢明",
    "夢見りあむ": "梦见璃亚梦",
    "黒埼ちとせ": "黑埼千岁",
    "久川颯": "久川飒",
    "アナスタシア": "安娜斯塔西娅",
    "ナターリア": "娜塔莉亚",
    "イヴ・サンタクロース": "伊芙·珊德克罗丝",
    "ケイト": "凯特",
    "キャシー・グラハム": "凯茜·格拉汉姆",
    "クラリス": "克拉莉丝",
    "ヘレン": "海伦",
    "メアリー・コクラン": "玛丽·柯克兰",
    "ライラ": "莱拉",
    "ロコ": "Roco",
}

VARIANT_MAP = str.maketrans(
    {
        "亜": "亚",
        "亞": "亚",
        "実": "实",
        "實": "实",
        "恵": "惠",
        "惠": "惠",
        "桜": "樱",
        "櫻": "樱",
        "島": "岛",
        "澤": "泽",
        "沢": "泽",
        "瀬": "濑",
        "條": "条",
        "条": "条",
        "園": "园",
        "薗": "园",
        "廣": "广",
        "広": "广",
        "黒": "黑",
        "龍": "龙",
        "竜": "龙",
        "來": "来",
        "鈴": "铃",
        "穂": "穗",
        "凜": "凛",
        "凛": "凛",
        "紬": "䌷",
        "織": "织",
        "緒": "绪",
        "麗": "丽",
        "髙": "高",
        "輝": "辉",
        "薫": "薰",
        "楽": "乐",
        "巻": "卷",
        "邊": "边",
        "辺": "边",
        "鋭": "锐",
        "見": "见",
        "論": "论",
        "誠": "诚",
        "類": "类",
        "漣": "涟",
        "濱": "滨",
        "浜": "滨",
        "菫": "堇",
        "齋": "斋",
        "斎": "斋",
        "齊": "齐",
        "斉": "齐",
        "祐": "佑",
        "姫": "姬",
        "葉": "叶",
        "國": "国",
        "国": "国",
        "氣": "气",
        "気": "气",
        "圓": "圆",
        "円": "圆",
        "彌": "弥",
        "弥": "弥",
        "與": "与",
        "与": "与",
        "優": "优",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch transparent portrait assets for the IM@S birthday plugin.")
    parser.add_argument("--portraits-dir", default="", help="Directory to store transparent portrait PNGs.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Delay between network requests.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing portrait files.")
    parser.add_argument("--dry-run", action="store_true", help="Print operations without writing files.")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows per source for testing.")
    parser.add_argument(
        "--source",
        action="append",
        choices=["mltd", "shiny", "gakumas", "sidem", "cg", "colors"],
        help="Source to fetch. Repeatable. Default: all.",
    )
    args = parser.parse_args()

    portraits_dir = resolve_portraits_dir(args.portraits_dir)
    character_assets = load_character_assets()
    known_by_brand = known_characters_by_brand(character_assets)
    mapping: dict[str, str] = load_existing_mapping(PORTRAIT_MAPPING, "CHARACTER_PORTRAIT_ASSETS")
    colors: dict[str, str] = load_existing_mapping(COLOR_MAPPING, "CHARACTER_COLORS")
    unmatched: list[dict[str, str]] = []
    sources = set(args.source or ["mltd", "shiny", "gakumas", "sidem", "cg", "colors"])

    with httpx.Client(headers={"User-Agent": "AstrBot-IMAS-Birthday-PortraitFetcher/0.1"}, follow_redirects=True, timeout=40) as client:
        if "colors" in sources:
            colors.update(fetch_colors(client, known_by_brand))
        if "mltd" in sources:
            fetch_mltd(client, portraits_dir, mapping, args, unmatched)
        if "shiny" in sources:
            fetch_shiny_from_local_cache(portraits_dir, mapping, args, unmatched)
        if "gakumas" in sources:
            fetch_gakumas(client, portraits_dir, mapping, args, unmatched)
        if "sidem" in sources:
            fetch_sidem(client, portraits_dir, mapping, args, unmatched, known_by_brand)
        if "cg" in sources:
            fetch_cg(client, portraits_dir, mapping, args, unmatched, known_by_brand)

    if not args.dry_run:
        write_mapping(PORTRAIT_MAPPING, "CHARACTER_PORTRAIT_ASSETS", mapping, "tools/fetch_portrait_assets.py")
        write_mapping(COLOR_MAPPING, "CHARACTER_COLORS", colors, "tools/fetch_portrait_assets.py")
        write_unmatched(unmatched)
        print(f"Wrote {PORTRAIT_MAPPING}")
        print(f"Wrote {COLOR_MAPPING}")
        print(f"Wrote unmatched report: {UNMATCHED_CSV}")

    print(f"Portrait mappings: {len(mapping)}")
    print(f"Color mappings: {len(colors)}")
    print(f"Unmatched/skipped rows: {len(unmatched)}")
    return 0


def resolve_portraits_dir(value: str) -> Path:
    if value:
        path = Path(os.path.expandvars(value)).expanduser()
        return path if path.is_absolute() else ROOT / path
    env_value = os.environ.get("IMAS_BIRTHDAY_PORTRAITS_DIR", "").strip()
    if env_value:
        path = Path(os.path.expandvars(env_value)).expanduser()
        return path if path.is_absolute() else ROOT / path
    config_path = ROOT.parent.parent / "config" / f"{ROOT.name}_config.json"
    if config_path.exists():
        try:
            import json

            data = json.loads(config_path.read_text(encoding="utf-8"))
            configured = str(data.get("character_portraits_dir", "") or "").strip()
            if configured:
                path = Path(os.path.expandvars(configured)).expanduser()
                return path if path.is_absolute() else ROOT / path
        except Exception:
            pass
    if ROOT.parent.name == "plugins":
        return ROOT.parent.parent / "imas_birthday_assets" / "portraits"
    return ROOT / "assets" / "portraits"


def load_character_assets() -> dict[str, str]:
    return load_existing_mapping(CHARACTER_ASSETS, "CHARACTER_IMAGE_ASSETS")


def load_existing_mapping(path: Path, variable: str) -> dict[str, str]:
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == variable:
            return ast.literal_eval(node.value)
    return {}


def known_characters_by_brand(character_assets: dict[str, str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name, relative in character_assets.items():
        brand = relative.replace("\\", "/").split("/", 1)[0].lower()
        result.setdefault(brand, []).append(name)
    return result


def fetch_colors(client: httpx.Client, known_by_brand: dict[str, list[str]]) -> dict[str, str]:
    print(f"Fetching colors: {COLOR_URL}")
    text = client.get(COLOR_URL).text
    rows = re.findall(r"<tr[^>]*>\s*<(?:td|th)[^>]*>(.*?)</(?:td|th)>\s*<td[^>]*>.*?title=\"(#[0-9a-fA-F]{6})\"", text, re.S)
    all_names = [name for names in known_by_brand.values() for name in names]
    colors: dict[str, str] = {}
    for raw_name, color in rows:
        source_name = clean_source_name(strip_tags(raw_name))
        if not source_name or source_name in {"765AS", "765AS(MS)", "CG", "ML", "SideM", "学マス", "シャニマス"}:
            continue
        matched, score = match_name(source_name, all_names)
        if matched and score >= 0.72:
            colors[matched] = color.lower()
    return colors


def fetch_mltd(client: httpx.Client, portraits_dir: Path, mapping: dict[str, str], args: argparse.Namespace, unmatched: list[dict[str, str]]):
    rows = list(MLTD_ID_TO_CN.items())
    for index, (idol_id, name) in enumerate(limited(rows, args.limit), start=1):
        url = urllib.parse.urljoin(MLTH_BASE, f"chara/show/{idol_id}")
        print(f"[mltd {index}/{len(rows)}] {name}: {url}")
        try:
            text = client.get(url).text
            rels = unique(re.findall(r"image/chara/img/[^\"']+\.png", text))
            if not rels:
                add_unmatched(unmatched, "mltd", name, url, "no portrait image")
                continue
            image_url = urllib.parse.urljoin(MLTH_BASE, rels[0])
            brand = "the_idolmaster" if idol_id <= 13 else "millionlive"
            import_remote_image(client, portraits_dir, mapping, name, brand, image_url, args)
            sleep(args.sleep)
        except Exception as exc:
            add_unmatched(unmatched, "mltd", name, url, repr(exc))


def fetch_shiny_from_local_cache(portraits_dir: Path, mapping: dict[str, str], args: argparse.Namespace, unmatched: list[dict[str, str]]):
    cache_root = ROOT / "asset_candidates" / "shinycolors_official"
    rows = list(SHINY_SLUG_TO_CN.items())
    for index, (slug, name) in enumerate(limited(rows, args.limit), start=1):
        matches = sorted(cache_root.rglob(f"{slug}_full.png"))
        if not matches:
            add_unmatched(unmatched, "shiny", name, str(cache_root), "local full PNG cache missing")
            continue
        print(f"[shiny {index}/{len(rows)}] {name}: {matches[0]}")
        import_local_image(portraits_dir, mapping, name, "shinycolors", matches[0], args)


def fetch_gakumas(client: httpx.Client, portraits_dir: Path, mapping: dict[str, str], args: argparse.Namespace, unmatched: list[dict[str, str]]):
    rows = list(GAKUMAS_SLUG_TO_CN.items())
    for index, (slug, name) in enumerate(limited(rows, args.limit), start=1):
        image_url = f"{GAKUMAS_BASE}/assets/img/idol/{slug}/default.png"
        print(f"[gakumas {index}/{len(rows)}] {name}: {image_url}")
        try:
            import_remote_image(client, portraits_dir, mapping, name, "gakuen_idolmaster", image_url, args)
            sleep(args.sleep)
        except Exception as exc:
            add_unmatched(unmatched, "gakumas", name, image_url, repr(exc))


def fetch_sidem(client: httpx.Client, portraits_dir: Path, mapping: dict[str, str], args: argparse.Namespace, unmatched: list[dict[str, str]], known_by_brand: dict[str, list[str]]):
    index_url = f"{SIDEM_BASE}/sidem/idol"
    text = client.get(index_url).text
    links = sorted(set(re.findall(r"/sidem/idol/[a-z0-9_-]+", text)))
    candidates = known_by_brand.get("sidem", [])
    for index, link in enumerate(limited(links, args.limit), start=1):
        detail_url = urllib.parse.urljoin(SIDEM_BASE, link)
        try:
            detail = client.get(detail_url).text
            source_name = page_title_name(detail)
            matched, score = match_name(source_name, candidates)
            image_url = sidem_main_image(detail)
            print(f"[sidem {index}/{len(links)}] {source_name} -> {matched or 'UNMATCHED'} ({score:.2f})")
            if not matched or score < 0.66 or not image_url:
                add_unmatched(unmatched, "sidem", source_name, detail_url, f"match={matched} score={score:.2f} image={image_url}")
                continue
            import_remote_image(client, portraits_dir, mapping, matched, "sidem", urllib.parse.urljoin(SIDEM_BASE, image_url), args)
            sleep(args.sleep)
        except Exception as exc:
            add_unmatched(unmatched, "sidem", link, detail_url, repr(exc))


def fetch_cg(client: httpx.Client, portraits_dir: Path, mapping: dict[str, str], args: argparse.Namespace, unmatched: list[dict[str, str]], known_by_brand: dict[str, list[str]]):
    index_url = urllib.parse.urljoin(CG_BASE, "")
    text = client.get(index_url).text
    links = sorted(set(re.findall(r"/cg/idol/detail/\d+", text)), key=lambda value: int(value.rsplit("/", 1)[-1]))
    candidates = known_by_brand.get("cinderellagirls", [])
    for index, link in enumerate(limited(links, args.limit), start=1):
        detail_url = urllib.parse.urljoin("https://imas.gamedbs.jp", link)
        try:
            detail = client.get(detail_url).text
            source_name, image_rel = cg_main_image(detail)
            matched, score = match_name(source_name, candidates)
            print(f"[cg {index}/{len(links)}] {source_name} -> {matched or 'UNMATCHED'} ({score:.2f})")
            if not matched or score < 0.72 or not image_rel:
                add_unmatched(unmatched, "cg", source_name, detail_url, f"match={matched} score={score:.2f} image={image_rel}")
                continue
            import_remote_image(client, portraits_dir, mapping, matched, "cinderellagirls", urllib.parse.urljoin("https://imas.gamedbs.jp/cg/", image_rel), args)
            sleep(args.sleep)
        except Exception as exc:
            add_unmatched(unmatched, "cg", link, detail_url, repr(exc))


def import_remote_image(client: httpx.Client, portraits_dir: Path, mapping: dict[str, str], name: str, brand: str, url: str, args: argparse.Namespace):
    relative = f"{brand}/{safe_filename(name)}.png"
    destination = portraits_dir / relative
    mapping[name] = relative
    if destination.exists() and not args.overwrite:
        print(f"  keep {destination}")
        return
    print(f"  fetch {url} -> {destination}")
    if args.dry_run:
        return
    data = client.get(url).content
    save_as_rgba_png(data, destination)


def import_local_image(portraits_dir: Path, mapping: dict[str, str], name: str, brand: str, source: Path, args: argparse.Namespace):
    relative = f"{brand}/{safe_filename(name)}.png"
    destination = portraits_dir / relative
    mapping[name] = relative
    if destination.exists() and not args.overwrite:
        print(f"  keep {destination}")
        return
    print(f"  copy {source} -> {destination}")
    if args.dry_run:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    save_as_rgba_png(source.read_bytes(), destination)


def save_as_rgba_png(data: bytes, destination: Path):
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")


def sidem_main_image(text: str) -> str:
    matches = unique(re.findall(r"/assets/img/sidem/[^\"']+main_all[^\"']+\.png", text))
    if matches:
        return matches[0]
    matches = unique(re.findall(r"/assets/img/sidem/[^\"']+detail/main/[^\"']+\.png", text))
    return matches[0] if matches else ""


def cg_main_image(text: str) -> tuple[str, str]:
    match = re.search(r'<a[^>]+href="(/cg/image_sp/card/cm/[^"]+\.png)"[^>]+title="([^"]+)"', text)
    if match:
        return html.unescape(match.group(2)).strip(), match.group(1)
    match = re.search(r'<img[^>]+src="(/cg/image_sp/card/cm/[^"]+\.png)"[^>]+alt="([^"]+)"', text)
    if match:
        return html.unescape(match.group(2)).strip(), match.group(1)
    match = re.search(r'<a[^>]+href="(/cg/image_sp/card/quest/[^"]+\.png)"[^>]+title="([^"]+)"', text)
    if match:
        return html.unescape(match.group(2)).strip(), match.group(1)
    match = re.search(r'<img[^>]+src="(/cg/image_sp/card/quest/[^"]+\.png)"[^>]+alt="([^"]+)"', text)
    if match:
        return html.unescape(match.group(2)).strip(), match.group(1)
    return page_title_name(text), ""


def page_title_name(text: str) -> str:
    match = re.search(r"<title>(.*?)</title>", text, re.S)
    if not match:
        return ""
    title = html.unescape(strip_tags(match.group(1))).strip()
    title = re.split(r"\s*[｜|]\s*", title, maxsplit=1)[0]
    title = re.sub(r"-\s*SideM\s*$", "", title, flags=re.I)
    return title.replace(" ", "")


def match_name(source_name: str, candidates: list[str]) -> tuple[str, float]:
    if not source_name:
        return "", 0.0
    source_clean = unicodedata.normalize("NFKC", clean_source_name(source_name))
    aliased = SPECIAL_NAME_ALIASES.get(source_clean, source_clean)
    normalized_source = normalize_name(aliased)
    best = ""
    best_score = 0.0
    for candidate in candidates:
        normalized_candidate = normalize_name(candidate)
        if normalized_source == normalized_candidate:
            return candidate, 1.0
        score = SequenceMatcher(None, normalized_source, normalized_candidate).ratio()
        if normalized_source and normalized_source in normalized_candidate:
            score = max(score, 0.82)
        if normalized_candidate and normalized_candidate in normalized_source:
            score = max(score, 0.82)
        if score > best_score:
            best = candidate
            best_score = score
    return best, best_score


def normalize_name(value: str) -> str:
    value = clean_source_name(value)
    value = SPECIAL_NAME_ALIASES.get(value, value)
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(VARIANT_MAP)
    value = value.replace("・", "·").replace(" ", "")
    value = re.sub(r"[()（）].*?[)）]", "", value)
    value = re.sub(r"[\s·・=＝!！ⅡIIⅱ]+", "", value)
    value = value.replace("々", "")
    return value.lower()


def clean_source_name(value: str) -> str:
    value = html.unescape(strip_tags(value))
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\[[^\]]+\]", "", value)
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[（(].*?[）)]", "", value)
    return value.strip()


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "")


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\s]+', "_", value.strip())
    value = value.strip("._")
    return value or "character"


def unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def limited(values, limit: int):
    return values[:limit] if limit and limit > 0 else values


def sleep(seconds: float):
    if seconds > 0:
        time.sleep(seconds)


def add_unmatched(rows: list[dict[str, str]], source: str, name: str, url: str, reason: str):
    rows.append({"source": source, "name": name, "url": url, "reason": reason})


def write_unmatched(rows: list[dict[str, str]]):
    OUT.mkdir(parents=True, exist_ok=True)
    with UNMATCHED_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["source", "name", "url", "reason"])
        writer.writeheader()
        writer.writerows(rows)


def write_mapping(path: Path, variable: str, mapping: dict[str, str], generated_by: str):
    lines = [
        f"# Auto-generated by {generated_by}.",
        "# Re-run the generator instead of editing by hand.",
        f"{variable} = {{",
    ]
    for name in sorted(mapping):
        lines.append(f"    {name!r}: {mapping[name]!r},")
    lines.append("}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
