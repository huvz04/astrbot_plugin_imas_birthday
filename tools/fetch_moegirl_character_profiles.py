from __future__ import annotations

import argparse
import html
import pprint
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from fetch_moegirl_character_assets import SOURCE_URL, USER_AGENT, fetch_text, parse_character_links

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
GENERATED_PROFILES = ROOT / "character_profiles.py"

FIELD_ALIASES = {
    "name_jp": ("日文名", "日文原名", "原文名", "日语名", "本名"),
    "name_kana": ("假名", "平假名", "读音", "注音"),
    "name_en": ("罗马字", "罗马音", "英文名", "外文名"),
    "cv": ("声优", "CV", "配音", "声優"),
    "age": ("年龄", "年齡"),
    "height": ("身高",),
    "weight": ("体重", "體重"),
    "measurements": ("三围", "三圍", "BWH"),
    "birthday_text": ("生日", "出生日期", "诞生日", "誕生日"),
    "blood_type": ("血型",),
    "zodiac": ("星座",),
    "dominant_hand": ("惯用手", "慣用手"),
    "type": ("属性", "偶像属性"),
    "agency": ("事务所", "事務所", "所属团体", "所属團體"),
    "hometown": ("出身地", "出生地", "故乡", "故鄉", "出身"),
    "hobby": ("兴趣", "爱好", "趣味"),
    "specialty": ("特技", "特长", "特長"),
    "favorite": ("喜欢的东西", "喜欢", "好きなもの"),
    "school": ("学校",),
    "class": ("班级", "年级", "学年"),
    "unit": ("组合", "所属组合", "所属ユニット", "ユニット"),
    "debut": ("初登场", "初登場", "首次登场"),
}

DROP_RAW_KEYS = {
    "图片",
    "image",
    "Image",
    "图像",
    "背景颜色",
    "文字颜色",
    "萌点",
    "活动范围",
}

DROP_RAW_KEY_RE = re.compile(r"^(第[一二三四五六七八九十]+次|sfc\d{4}|总选|总選|人气投票|人氣投票)$", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch basic IM@S character profile fields from Moegirl pages.")
    parser.add_argument("--source-url", default=SOURCE_URL, help="Moegirl birthday page URL.")
    parser.add_argument("--sleep", type=float, default=0.35, help="Delay between page API calls.")
    parser.add_argument("--limit", type=int, default=0, help="Fetch at most N characters for testing.")
    parser.add_argument("--overwrite", action="store_true", help="Ignore existing character_profiles.py.")
    parser.add_argument("--dry-run", action="store_true", help="Print profiles without writing character_profiles.py.")
    args = parser.parse_args()

    existing = {} if args.overwrite else load_existing_profiles()
    profiles: dict[str, dict[str, Any]] = dict(existing)
    failed: list[str] = []
    skipped = 0

    with httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        characters = parse_character_links(fetch_text(client, args.source_url))
        if args.limit > 0:
            characters = characters[: args.limit]
        if not characters:
            print("No character links found.")
            return 1

        print(f"Found {len(characters)} linked character entries.")
        for index, character in enumerate(characters, start=1):
            if character.name in existing and not args.overwrite:
                skipped += 1
                print(f"[{index}/{len(characters)}] keep: {character.name}")
                continue
            try:
                page_html = fetch_text(client, character.href)
                profile = parse_profile(page_html)
                profile.update(
                    {
                        "source_title": character.page_title,
                        "source_url": character.href,
                        "brand": character.brand,
                    }
                )
                profiles[character.name] = profile
                print(f"[{index}/{len(characters)}] {character.name}: {summarize_profile(profile)}")
            except Exception as exc:
                print(f"[{index}/{len(characters)}] fail: {character.name}: {exc}")
                failed.append(character.name)
            if args.sleep > 0:
                time.sleep(args.sleep)

    if args.dry_run:
        pprint.pp(profiles)
        return 0

    write_profiles(profiles)
    print(f"Wrote profiles: {GENERATED_PROFILES}")
    if skipped:
        print(f"Kept existing profiles: {skipped}")
    if failed:
        print(f"Failed pages: {', '.join(failed)}")
    return 0


def load_existing_profiles() -> dict[str, dict[str, Any]]:
    if not GENERATED_PROFILES.exists():
        return {}
    namespace: dict[str, Any] = {}
    try:
        exec(GENERATED_PROFILES.read_text(encoding="utf-8"), namespace)
    except Exception:
        return {}
    profiles = namespace.get("CHARACTER_PROFILES", {})
    return profiles if isinstance(profiles, dict) else {}


def parse_profile(page_html: str) -> dict[str, Any]:
    raw_fields = parse_html_infobox_fields(page_html)
    normalized = {normalize_key(key): clean_html_value(value) for key, value in raw_fields.items()}
    profile: dict[str, Any] = {}
    for target_key, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            value = normalized.get(normalize_key(alias), "")
            if value:
                profile[target_key] = value
                break

    birthday = extract_birthday(profile.get("birthday_text", ""))
    if birthday:
        profile["birthday"] = birthday

    introduction = extract_introduction(page_html)
    if introduction:
        profile["summary"] = introduction[0]
        profile["introduction"] = introduction

    raw = {
        key: value
        for key, value in normalized.items()
        if value and key not in {normalize_key(item) for item in DROP_RAW_KEYS} and not DROP_RAW_KEY_RE.match(key)
    }
    if raw:
        profile["raw"] = raw
    return profile


def parse_html_infobox_fields(page_html: str) -> dict[str, str]:
    content_match = re.search(
        r'<div\b[^>]*class="[^"]*\bmw-parser-output\b[^"]*"[^>]*>(.*?)(?:<div class="printfooter"|</body>)',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )
    table_html = content_match.group(1) if content_match else page_html
    fields: dict[str, str] = {}
    for row_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
        row_html = row_match.group(1)
        cells = [
            (match.group(1).lower(), match.group(2))
            for match in re.finditer(r"<(th|td)\b[^>]*>(.*?)</\1>", row_html, re.IGNORECASE | re.DOTALL)
        ]
        index = 0
        while index < len(cells) - 1:
            tag, key_html = cells[index]
            next_tag, value_html = cells[index + 1]
            if tag == "th" and next_tag == "td":
                key = clean_html_value(key_html)
                value = clean_html_value(value_html)
                if key and value:
                    fields[key] = value
                index += 2
            else:
                index += 1
    return fields


def parse_template_fields(wikitext: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    current_key = ""
    current_value: list[str] = []

    def flush():
        nonlocal current_key, current_value
        if current_key:
            fields[current_key] = "\n".join(current_value).strip()
        current_key = ""
        current_value = []

    for line in wikitext.splitlines():
        match = re.match(r"^\s*\|\s*([^=|{}]{1,40}?)\s*=\s*(.*)$", line)
        if match:
            flush()
            current_key = match.group(1).strip()
            current_value = [match.group(2).strip()]
            continue
        if current_key:
            if line.strip().startswith("}}"):
                flush()
            else:
                current_value.append(line.strip())
    flush()
    return fields


def extract_introduction(page_html: str, max_paragraphs: int = 3, max_chars: int = 420) -> list[str]:
    content_start_match = re.search(
        r'<div\b[^>]*class="[^"]*\bmw-parser-output\b[^"]*"[^>]*>',
        page_html,
        re.IGNORECASE,
    )
    start = content_start_match.end() if content_start_match else 0
    heading_match = re.search(r'<div\b[^>]*class="[^"]*\bmw-heading\b[^"]*"[^>]*>\s*<h2\b', page_html[start:], re.IGNORECASE)
    end = start + heading_match.start() if heading_match else len(page_html)
    intro = page_html[start:end]
    intro = re.sub(r"<table\b.*?</table>", "", intro, flags=re.IGNORECASE | re.DOTALL)
    intro = re.sub(r"<div\b.*?</div>", "", intro, flags=re.IGNORECASE | re.DOTALL)
    paragraphs: list[str] = []
    for match in re.finditer(r"<p\b[^>]*>(.*?)</p>", intro, re.IGNORECASE | re.DOTALL):
        block = match.group(1).strip()
        block = block.strip()
        if not block or block.startswith(("{|", "|", "}", "*", "#", ";", ":")):
            continue
        text = clean_html_value(block)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 18:
            continue
        if text.startswith(("消歧义", "本条目", "此页面")):
            continue
        paragraphs.append(text[:max_chars])
        if len(paragraphs) >= max_paragraphs:
            break
    return paragraphs


def strip_top_level_templates(text: str) -> str:
    result: list[str] = []
    index = 0
    depth = 0
    while index < len(text):
        if text.startswith("{{", index):
            depth += 1
            index += 2
            continue
        if depth and text.startswith("}}", index):
            depth -= 1
            index += 2
            continue
        if depth == 0:
            result.append(text[index])
        index += 1
    return "".join(result)


def clean_wiki_value(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    value = re.sub(r"<br\s*/?>", "、", value, flags=re.IGNORECASE)
    value = re.sub(r"<ref\b.*?</ref>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[\[File:[^\]]+\]\]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[\[文件:[^\]]+\]\]", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\[\[([^|\]]+)\|([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{lj\|([^{}]+)\}\}", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{lang\|[^|{}]+\|([^{}]+)\}\}", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{ruby\|([^|{}]+)\|([^{}]+)\}\}", r"\1（\2）", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{color\|[^|{}]+\|([^{}]+)\}\}", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{(?:黑幕|heimu)\|([^{}]+)\}\}", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = value.replace("&nbsp;", " ")
    value = re.sub(r"'{2,5}", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.replace(" 、", "、").replace("、 ", "、")
    return value.strip(" \t\r\n、")


def clean_html_value(value: str) -> str:
    value = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    value = re.sub(r"<script\b.*?</script>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<style\b.*?</style>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<sup\b.*?</sup>", "", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<br\s*/?>", "、", value, flags=re.IGNORECASE)
    value = re.sub(r"<img\b[^>]*alt=\"([^\"]*)\"[^>]*>", r"\1", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"\[[0-9]+\]", "", value)
    value = re.sub(r"\s+", " ", value)
    value = value.replace(" 、", "、").replace("、 ", "、")
    return value.strip(" \t\r\n、")


def extract_birthday(value: str) -> str:
    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if match:
        return f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"
    match = re.search(r"(\d{1,2})[/-](\d{1,2})", value)
    if match:
        return f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"
    return ""


def normalize_key(value: str) -> str:
    return re.sub(r"[\s_　：:]+", "", value).lower()


def summarize_profile(profile: dict[str, Any]) -> str:
    parts = []
    for key in ("cv", "age", "height", "birthday_text", "summary"):
        if profile.get(key):
            parts.append(f"{key}={profile[key]}")
    return ", ".join(parts) or "no mapped fields"


def write_profiles(profiles: dict[str, dict[str, Any]]):
    formatted = pprint.pformat(dict(sorted(profiles.items())), width=120, sort_dicts=True)
    lines = [
        "# Auto-generated by tools/fetch_moegirl_character_profiles.py.",
        "# Re-run the generator instead of editing by hand.",
        f"CHARACTER_PROFILES = {formatted}",
        "",
    ]
    GENERATED_PROFILES.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
