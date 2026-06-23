from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SOURCE_URL = (
    "https://zh.moegirl.org.cn/"
    "%E5%81%B6%E5%83%8F%E5%A4%A7%E5%B8%88%E7%B3%BB%E5%88%97/"
    "%E7%9B%B8%E5%85%B3%E4%BA%BA%E5%A3%AB%E7%94%9F%E6%97%A5%E4%BF%A1%E6%81%AF"
)

MONTH_NAMES = {
    1: "一月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "十一月",
    12: "十二月",
}

KR_CHARACTER_NAMES = {
    "Mint",
    "ミント",
    "寺本来可",
    "权势玲",
    "李睿恩",
    "李绣至",
    "许怜朱",
    "李智元",
    "车智瑟",
    "黄恩美",
    "金素利",
    "千宜英",
}

KR_SUSPICIOUS_PATTERNS = [
    re.compile(r"\bkim\b", re.I),
    re.compile(r"\bso-?ri\b", re.I),
    re.compile(r"\bmin-?hee\b", re.I),
    re.compile(r"\bji-?[a-z]+\b", re.I),
    re.compile(r"[\uac00-\ud7af]"),
]


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" 、", "、").replace("、 ", "、")
    text = text.replace(" （", "（").replace("） ", "）")
    return text.strip()


def split_people(values: list[str]) -> list[str]:
    people: list[str] = []
    for value in values:
        for item in re.split(r"[、,，]", value):
            item = item.strip()
            if item and item not in people:
                people.append(item)
    return people


def base_character_name(character: str) -> str:
    return re.sub(r"\s*[（(][^（）()]+[）)]\s*", "", character).strip()


def is_kr_character(character: str) -> bool:
    base = base_character_name(character)
    return character in KR_CHARACTER_NAMES or base in KR_CHARACTER_NAMES


def is_suspicious_character(character: str) -> bool:
    if re.search(r"[（(][A-Za-z][^）)]*[）)]", character):
        return True
    return any(pattern.search(character) for pattern in KR_SUSPICIOUS_PATTERNS)


class BirthdayPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.data: dict[str, dict[str, list[str]]] = {}
        self._month_by_name = {name: month for month, name in MONTH_NAMES.items()}
        self._current_month: int | None = None
        self._in_h2 = False
        self._h2_text: list[str] = []
        self._in_table = False
        self._in_tr = False
        self._in_td = False
        self._current_cells: list[str] = []
        self._current_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attrs_dict = dict(attrs)
        if tag == "h2":
            self._in_h2 = True
            self._h2_text = []
        if self._current_month and tag == "table" and "wikitable" in attrs_dict.get("class", ""):
            self._in_table = True
        if self._in_table and tag == "tr":
            self._in_tr = True
            self._current_cells = []
        if self._in_tr and tag == "td":
            self._in_td = True
            self._current_cell = []
            return
        if self._in_td:
            self._current_cell.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if self._in_td:
            attrs_text = "".join(f' {name}="{html.escape(value or "", quote=True)}"' for name, value in attrs)
            self._current_cell.append(f"<{tag}{attrs_text}/>")

    def handle_endtag(self, tag: str):
        if tag == "h2" and self._in_h2:
            month_name = "".join(self._h2_text).strip()
            self._current_month = self._month_by_name.get(month_name)
            self._in_h2 = False
            self._h2_text = []
            return
        if self._in_td and tag == "td":
            self._current_cells.append("".join(self._current_cell))
            self._current_cell = []
            self._in_td = False
            return
        if self._in_td:
            self._current_cell.append(f"</{tag}>")
        if self._in_table and tag == "tr":
            self._parse_row()
            self._in_tr = False
            self._current_cells = []
            return
        if self._in_table and tag == "table":
            self._in_table = False
            self._current_month = None

    def handle_data(self, data: str):
        if self._in_h2:
            self._h2_text.append(data)
        if self._in_td:
            self._current_cell.append(html.escape(data))

    def _parse_row(self):
        if self._current_month is None or len(self._current_cells) < 2:
            return
        day_text = html.unescape(re.sub(r"<[^>]+>", "", self._current_cells[0]))
        day_match = re.search(r"(\d{1,2})日", day_text)
        if not day_match:
            return
        date_key = f"{self._current_month:02d}-{int(day_match.group(1)):02d}"
        entry = self.data.setdefault(date_key, {"characters": [], "seiyuu": [], "related_people": [], "events": []})
        line_parser = BirthdayCellParser()
        line_parser.feed(self._current_cells[1])
        line_parser.close()
        for category, text in line_parser.lines:
            if text and text not in entry[category]:
                entry[category].append(text)


class BirthdayCellParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, str]] = []
        self._text: list[str] = []
        self._has_color_square = False
        self._is_gray = False
        self._is_italic = False
        self._sup_depth = 0
        self._italic_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "br":
            self._finish_line()
            return
        attrs_dict = dict(attrs)
        style = attrs_dict.get("style", "").lower()
        if "background-color" in style:
            self._has_color_square = True
        if "color:gray" in style or "color: gray" in style:
            self._is_gray = True
        if tag == "i":
            self._italic_depth += 1
            self._is_italic = True
        if tag == "sup":
            self._sup_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag == "br":
            self._finish_line()

    def handle_endtag(self, tag: str):
        if tag == "i" and self._italic_depth:
            self._italic_depth -= 1
        if tag == "sup" and self._sup_depth:
            self._sup_depth -= 1

    def handle_data(self, data: str):
        if self._sup_depth:
            return
        self._text.append(data)

    def close(self):
        super().close()
        self._finish_line()

    def _finish_line(self):
        text = clean_text("".join(self._text))
        if text:
            if self._is_gray:
                category = "events"
            elif self._is_italic:
                category = "related_people"
            elif self._has_color_square:
                category = "characters"
            else:
                category = "seiyuu"
            self.lines.append((category, text))
        self._text = []
        self._has_color_square = False
        self._is_gray = False
        self._is_italic = False
        self._sup_depth = 0
        self._italic_depth = 0


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "AstrBot-IdolmasterBirthdayBot/preview"})
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as urllib_error:
        try:
            import httpx

            with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "AstrBot-IdolmasterBirthdayBot/preview"}) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        except Exception as httpx_error:
            raise RuntimeError(f"failed to fetch source page: urllib={urllib_error!r}; httpx={httpx_error!r}") from httpx_error


def parse_birthdays(source_html: str) -> dict[str, dict[str, list[str]]]:
    parser = BirthdayPageParser()
    parser.feed(source_html)
    parser.close()
    return parser.data


def filter_kr(data: dict[str, dict[str, list[str]]]) -> tuple[dict[str, dict[str, list[str]]], list[tuple[str, str]]]:
    filtered: dict[str, dict[str, list[str]]] = {}
    removed: list[tuple[str, str]] = []
    for date_key, entry in sorted(data.items()):
        next_entry = {
            "characters": [],
            "seiyuu": list(entry.get("seiyuu", [])),
            "related_people": list(entry.get("related_people", [])),
            "events": list(entry.get("events", [])),
        }
        for character in split_people(entry.get("characters", [])):
            if is_kr_character(character):
                removed.append((date_key, character))
                continue
            if character not in next_entry["characters"]:
                next_entry["characters"].append(character)
        filtered[date_key] = next_entry
    return filtered, removed


def write_csv(path: Path, data: dict[str, dict[str, list[str]]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "characters", "seiyuu", "related_people", "events"])
        for date_key, entry in sorted(data.items()):
            writer.writerow(
                [
                    date_key,
                    "、".join(entry.get("characters", [])),
                    "、".join(entry.get("seiyuu", [])),
                    "、".join(entry.get("related_people", [])),
                    "、".join(entry.get("events", [])),
                ]
            )


def write_removed_csv(path: Path, removed: list[tuple[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["date", "removed_kr_character", "base_name"])
        for date_key, character in removed:
            writer.writerow([date_key, character, base_character_name(character)])


def write_markdown(
    path: Path,
    data: dict[str, dict[str, list[str]]],
    removed: list[tuple[str, str]],
    suspicious: list[tuple[str, str]],
) -> None:
    lines = [
        "# THE IDOLM@STER Birthday Preview",
        "",
        f"- Dates: {len(data)}",
        f"- Removed KR characters: {len(removed)}",
        f"- Suspicious remaining characters: {len(suspicious)}",
        "",
        "## Removed KR Characters",
        "",
        "| Date | Character | Base |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {date_key} | {character} | {base_character_name(character)} |" for date_key, character in removed)
    lines.extend(["", "## Suspicious Remaining Characters", "", "| Date | Character |", "| --- | --- |"])
    lines.extend(f"| {date_key} | {character} |" for date_key, character in suspicious)
    lines.extend(["", "## Filtered Birthday Data", "", "| Date | Characters | Seiyuu | Related | Events |", "| --- | --- | --- | --- | --- |"])
    for date_key, entry in sorted(data.items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    date_key,
                    "、".join(entry.get("characters", [])),
                    "、".join(entry.get("seiyuu", [])),
                    "、".join(entry.get("related_people", [])),
                    "、".join(entry.get("events", [])),
                ]
            )
            + " |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export searchable birthday data previews with KR characters removed.")
    parser.add_argument("--url", default=SOURCE_URL, help="Moegirlpedia birthday page URL.")
    parser.add_argument("--source-html", default="", help="Use a local HTML file instead of downloading.")
    parser.add_argument("--output-dir", default="previews", help="Output directory.")
    args = parser.parse_args()

    if args.source_html:
        source_html = Path(args.source_html).read_text(encoding="utf-8", errors="replace")
    else:
        source_html = fetch_html(args.url)

    raw = parse_birthdays(source_html)
    filtered, removed = filter_kr(raw)
    suspicious = [
        (date_key, character)
        for date_key, entry in sorted(filtered.items())
        for character in entry.get("characters", [])
        if is_suspicious_character(character)
    ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "birthday_preview_filtered.csv", filtered)
    write_removed_csv(output_dir / "birthday_preview_removed_kr.csv", removed)
    write_markdown(output_dir / "birthday_preview.md", filtered, removed, suspicious)
    print(f"Wrote {output_dir / 'birthday_preview.md'}")
    print(f"Wrote {output_dir / 'birthday_preview_filtered.csv'}")
    print(f"Wrote {output_dir / 'birthday_preview_removed_kr.csv'}")
    print(f"Removed KR characters: {len(removed)}")
    print(f"Suspicious remaining characters: {len(suspicious)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
