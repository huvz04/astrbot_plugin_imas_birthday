from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import html
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star


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

CATEGORY_LABELS = {
    "characters": "角色",
    "seiyuu": "声优",
    "related_people": "相关人士",
    "events": "事件",
}

FANCY_DIGITS = str.maketrans("0123456789", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗")

# Put your processed character images in assets/characters/ and map names here.
# Example:
# CHARACTER_IMAGE_ASSETS = {
#     "天海春香": "amami_haruka.png",
#     "如月千早": "kisaragi_chihaya.png",
# }
CHARACTER_IMAGE_ASSETS: dict[str, str] = {}


def load_generated_character_assets() -> dict[str, str]:
    path = Path(__file__).resolve().with_name("character_assets.py")
    if not path.exists():
        return {}
    spec = importlib.util.spec_from_file_location("imas_birthday_character_assets", path)
    if not spec or not spec.loader:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assets = getattr(module, "CHARACTER_IMAGE_ASSETS", {})
    return assets if isinstance(assets, dict) else {}


CHARACTER_IMAGE_ASSETS.update(load_generated_character_assets())

BRAND_COLORS = {
    "765": "#f05a7e",
    "CG": "#2f7fd3",
    "ML": "#f2b84b",
    "SideM": "#1aa982",
    "SC": "#8d72d9",
    "GKM": "#f08a33",
    "VA": "#5c7cfa",
    "KR": "#d94a4a",
    "IMAS": "#5b6472",
}


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
            attrs_text = "".join(
                f' {name}="{html.escape(value or "", quote=True)}"' for name, value in attrs
            )
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
        entry = self.data.setdefault(
            date_key,
            {"characters": [], "seiyuu": [], "related_people": [], "events": []},
        )
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


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.replace(" 、", "、").replace("、 ", "、")
    text = text.replace(" （", "（").replace("） ", "）")
    return text.strip()


class ImasBirthdayPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context, config)
        self.config = config or {}
        self.plugin_dir = Path(__file__).resolve().parent
        self._task: asyncio.Task | None = None
        self._last_sent_date = ""
        if self._cfg_bool("enabled", True):
            self._task = asyncio.create_task(self._scheduler())

    async def terminate(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @filter.command_group("imasbd")
    def imasbd(self):
        """偶像大师生日提醒"""
        pass

    @imasbd.command("sid")
    async def imasbd_sid(self, event: AstrMessageEvent):
        """查看当前会话 UMO，填入白名单后可主动推送。"""
        yield event.plain_result(f"当前 UMO：{event.unified_msg_origin}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("bind")
    async def imasbd_bind(self, event: AstrMessageEvent):
        """把当前会话加入生日推送白名单。"""
        umo = event.unified_msg_origin
        white_umos = list(self.config.get("white_umos", []))
        if umo in white_umos:
            yield event.plain_result("当前会话已经在白名单里。")
            return
        white_umos.append(umo)
        self.config["white_umos"] = white_umos
        self._save_config()
        yield event.plain_result(f"已加入白名单：{umo}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("refresh")
    async def imasbd_refresh(self, event: AstrMessageEvent):
        """立即刷新萌娘百科生日表缓存。"""
        try:
            data = await self._fetch_birthdays()
            await self._save_cache(data)
        except Exception as exc:
            logger.exception("刷新偶像大师生日表失败")
            yield event.plain_result(f"刷新失败：{exc}")
            return
        yield event.plain_result(f"刷新完成，共缓存 {len(data)} 个日期。")

    @imasbd.command("today")
    async def imasbd_today(self, event: AstrMessageEvent):
        """在当前会话预览并发送今天的生日祝贺。"""
        now = self._now()
        result = await self._build_result(now.month, now.day)
        if not result["message"]:
            yield event.plain_result("今天没有匹配到偶像大师相关生日。")
            return
        yield event.plain_result(result["message"])
        if result["card_path"]:
            yield event.image_result(result["card_path"])

    @imasbd.command("date")
    async def imasbd_date(self, event: AstrMessageEvent, date_text: str):
        """预览指定日期，格式 MM-DD，例如 /imasbd date 06-22。"""
        parsed = self._parse_date_text(date_text)
        if not parsed:
            yield event.plain_result("日期格式不对，请使用 MM-DD，例如 06-22。")
            return
        month, day = parsed
        result = await self._build_result(month, day)
        if not result["message"]:
            yield event.plain_result(f"{month}月{day}日没有匹配到偶像大师相关生日。")
            return
        yield event.plain_result(result["message"])
        if result["card_path"]:
            yield event.image_result(result["card_path"])

    @imasbd.command("assets")
    async def imasbd_assets(self, event: AstrMessageEvent):
        """查看今天生日角色的本地图片匹配情况。"""
        now = self._now()
        data = await self._get_birthdays()
        entry = data.get(f"{now.month:02d}-{now.day:02d}") or {}
        characters = self._split_people(entry.get("characters", []))
        if not characters:
            yield event.plain_result("今天没有需要匹配图片的角色。")
            return
        matched = []
        missing = []
        for character in characters:
            if self._character_image_path(character):
                matched.append(character)
            else:
                missing.append(character)
        yield event.plain_result(
            "今日图片匹配：\n"
            f"已匹配：{self._join_names(matched) or '无'}\n"
            f"缺图片：{self._join_names(missing) or '无'}"
        )

    async def _scheduler(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("偶像大师生日提醒定时任务异常")
            await asyncio.sleep(30)

    async def _tick(self):
        if not self._cfg_bool("enabled", True):
            return
        now = self._now()
        send_time = str(self.config.get("send_time", "09:00"))
        if now.strftime("%H:%M") != send_time:
            return
        today_key = now.strftime("%Y-%m-%d")
        if self._last_sent_date == today_key:
            return

        result = await self._build_result(now.month, now.day)
        self._last_sent_date = today_key
        if not result["message"]:
            logger.info("今天没有偶像大师生日提醒内容，跳过推送。")
            return

        white_umos = [str(item).strip() for item in self.config.get("white_umos", []) if str(item).strip()]
        if not white_umos:
            logger.warning("偶像大师生日提醒白名单为空，跳过推送。")
            return

        for umo in white_umos:
            chain = MessageChain().message(result["message"])
            if result["card_path"]:
                chain.file_image(result["card_path"])
            ok = await self.context.send_message(umo, chain)
            if not ok:
                logger.warning(f"偶像大师生日提醒发送失败，未找到平台：{umo}")

    async def _build_result(self, month: int, day: int) -> dict[str, str]:
        data = await self._get_birthdays()
        entry = data.get(f"{month:02d}-{day:02d}")
        message = self._build_message_from_entry(month, day, entry)
        if not message:
            return {"message": "", "card_path": ""}
        card_path = ""
        if self._cfg_bool("render_card", True):
            card_path = await self._render_card(month, day, entry)
        return {"message": message, "card_path": card_path}

    async def _build_message(self, month: int, day: int) -> str:
        data = await self._get_birthdays()
        date_key = f"{month:02d}-{day:02d}"
        return self._build_message_from_entry(month, day, data.get(date_key))

    def _build_message_from_entry(self, month: int, day: int, entry: dict[str, list[str]] | None) -> str:
        if not entry:
            return ""
        date_key = f"{month:02d}-{day:02d}"

        lines: list[str] = []
        if self._cfg_bool("include_characters", True):
            lines.extend(self._format_lines("characters", entry.get("characters", [])))
        if self._cfg_bool("include_seiyuu", True):
            lines.extend(self._format_lines("seiyuu", entry.get("seiyuu", [])))
        if self._cfg_bool("include_related_people", False):
            lines.extend(self._format_lines("related_people", entry.get("related_people", [])))
        if self._cfg_bool("include_events", False):
            lines.extend(self._format_lines("events", entry.get("events", [])))
        if not lines:
            return ""

        template = str(
            self.config.get(
                "message_template",
                "今天是 {month}月{day}日，偶像大师相关生日：\n{items}\n祝大家生日快乐！",
            )
        )
        return template.format(
            month=month,
            day=day,
            date=date_key,
            items="\n".join(lines),
            **self._date_template_vars(month, day),
        )

    def _date_template_vars(self, month: int, day: int) -> dict[str, str | int]:
        year = self._now().year
        slash_date = f"{year}/{month:02d}/{day:02d}"
        birthday_time = f"{slash_date} 00:00"
        fancy_birthday_time = self._fancy_digits(birthday_time)
        return {
            "year": year,
            "slash_date": slash_date,
            "birthday_time": birthday_time,
            "beijing_time": f"北京时间 {birthday_time}",
            "fancy_year": self._fancy_digits(str(year)),
            "fancy_slash_date": self._fancy_digits(slash_date),
            "fancy_birthday_time": fancy_birthday_time,
            "fancy_beijing_time": f"北京时间 {fancy_birthday_time}",
            "decorated_beijing_time": f"°.✩┈ 北京時間 {fancy_birthday_time} ┈✩.°",
        }

    def _fancy_digits(self, value: str) -> str:
        return value.translate(FANCY_DIGITS)

    async def _render_card(self, month: int, day: int, entry: dict[str, list[str]] | None) -> str:
        if not entry:
            return ""
        characters = self._split_people(entry.get("characters", []))
        seiyuu = self._split_people(entry.get("seiyuu", []))
        related_people = self._split_people(entry.get("related_people", []))
        events = entry.get("events", [])

        items = [self._card_item(name) for name in characters]
        if not items and not self._cfg_bool("render_card_without_character_image", True):
            return ""

        html_text = self._birthday_card_html(
            month=month,
            day=day,
            items=items,
            seiyuu=seiyuu,
            related_people=related_people if self._cfg_bool("include_related_people", False) else [],
            events=events if self._cfg_bool("include_events", False) else [],
        )
        try:
            return await self.html_render(
                html_text,
                {},
                return_url=False,
                options={"viewport": {"width": 1200, "height": 900}},
            )
        except Exception:
            logger.exception("生日卡片渲染失败，回退为纯文字。")
            return ""

    def _format_lines(self, category: str, values: list[str]) -> list[str]:
        label = CATEGORY_LABELS[category]
        return [f"{label}：{value}" for value in values if value]

    def _card_item(self, character: str) -> dict[str, str]:
        brand = self._character_brand(character)
        image_path = self._character_image_path(character)
        return {
            "name": character,
            "brand": brand,
            "color": BRAND_COLORS.get(brand, BRAND_COLORS["IMAS"]),
            "image": image_path.as_uri() if image_path else "",
        }

    def _character_image_path(self, character: str) -> Path | None:
        filename = CHARACTER_IMAGE_ASSETS.get(character)
        if not filename:
            return None
        path = Path(filename)
        if not path.is_absolute():
            path = self.plugin_dir / "assets" / "characters" / filename
        return path if path.exists() else None

    def _character_brand(self, character: str) -> str:
        filename = CHARACTER_IMAGE_ASSETS.get(character, "").lower()
        if "/" in filename or "\\" in filename:
            prefix = re.split(r"[/\\]", filename, maxsplit=1)[0].lower()
            brand_map = {
                "765": "765",
                "765as": "765",
                "cg": "CG",
                "cinderella": "CG",
                "ml": "ML",
                "million": "ML",
                "sidem": "SideM",
                "sc": "SC",
                "shiny": "SC",
                "gkm": "GKM",
                "gakumas": "GKM",
                "va": "VA",
                "valiv": "VA",
                "kr": "KR",
            }
            return brand_map.get(prefix, "IMAS")
        return "IMAS"

    def _split_people(self, values: list[str]) -> list[str]:
        people: list[str] = []
        for value in values:
            for item in re.split(r"[、,，]", value):
                item = item.strip()
                if item and item not in people:
                    people.append(item)
        return people

    def _join_names(self, names: list[str]) -> str:
        return "、".join(names)

    def _birthday_card_html(
        self,
        month: int,
        day: int,
        items: list[dict[str, str]],
        seiyuu: list[str],
        related_people: list[str],
        events: list[str],
    ) -> str:
        title = html.escape(str(self.config.get("card_title", "Happy Birthday")))
        subtitle = html.escape(str(self.config.get("card_subtitle", "THE IDOLM@STER Birthday")))
        item_html = "\n".join(self._birthday_card_item_html(item) for item in items)
        if not item_html:
            item_html = '<div class="empty">今天没有匹配到本地角色图，但祝福照常送达。</div>'
        seiyuu_html = self._meta_block("声优", seiyuu)
        related_html = self._meta_block("相关人士", related_people)
        events_html = self._meta_block("事件", events)
        return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  width: 1200px;
  min-height: 900px;
  font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  color: #20242c;
  background: #f7f3ec;
}}
.card {{
  width: 1200px;
  min-height: 900px;
  padding: 56px;
  background:
    linear-gradient(90deg, rgba(240,90,126,.16), rgba(47,127,211,.12) 45%, rgba(242,184,75,.18)),
    #f7f3ec;
}}
.header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 32px;
  border-bottom: 3px solid rgba(32,36,44,.14);
  padding-bottom: 28px;
}}
.title {{
  font-size: 72px;
  line-height: .95;
  font-weight: 800;
}}
.subtitle {{
  margin-top: 14px;
  font-size: 25px;
  color: #5b6472;
}}
.date {{
  text-align: right;
  font-size: 70px;
  font-weight: 800;
  color: #f05a7e;
}}
.date span {{
  display: block;
  font-size: 24px;
  color: #5b6472;
  font-weight: 700;
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(245px, 1fr));
  gap: 22px;
  margin-top: 34px;
}}
.idol {{
  min-height: 410px;
  background: rgba(255,255,255,.74);
  border: 2px solid rgba(32,36,44,.11);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}}
.portrait {{
  height: 320px;
  display: flex;
  align-items: end;
  justify-content: center;
  background: var(--brand);
}}
.portrait img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}}
.placeholder {{
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255,255,255,.88);
  font-size: 96px;
  font-weight: 800;
}}
.idol-name {{
  padding: 18px 18px 6px;
  font-size: 34px;
  font-weight: 800;
  line-height: 1.15;
}}
.brand {{
  padding: 0 18px 18px;
  color: #5b6472;
  font-size: 18px;
  font-weight: 700;
}}
.meta {{
  margin-top: 28px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}}
.meta-block {{
  background: rgba(255,255,255,.62);
  border-left: 8px solid #2f7fd3;
  padding: 18px 22px;
  border-radius: 6px;
}}
.meta-title {{
  font-size: 20px;
  font-weight: 800;
  color: #5b6472;
  margin-bottom: 8px;
}}
.meta-text {{
  font-size: 28px;
  font-weight: 700;
  line-height: 1.35;
}}
.empty {{
  grid-column: 1 / -1;
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255,255,255,.68);
  border: 2px solid rgba(32,36,44,.11);
  border-radius: 8px;
  font-size: 34px;
  font-weight: 800;
  color: #5b6472;
}}
.footer {{
  margin-top: 30px;
  font-size: 18px;
  color: #6d7684;
}}
</style>
</head>
<body>
  <main class="card">
    <section class="header">
      <div>
        <div class="title">{title}</div>
        <div class="subtitle">{subtitle}</div>
      </div>
      <div class="date">{month:02d}.{day:02d}<span>Birthday</span></div>
    </section>
    <section class="grid">{item_html}</section>
    <section class="meta">{seiyuu_html}{related_html}{events_html}</section>
    <section class="footer">Images are local assets prepared by the bot owner. THE IDOLM@STER rights belong to their respective owners.</section>
  </main>
</body>
</html>"""

    def _birthday_card_item_html(self, item: dict[str, str]) -> str:
        name = html.escape(item["name"])
        brand = html.escape(item["brand"])
        color = html.escape(item["color"])
        if item["image"]:
            portrait = f'<img src="{html.escape(item["image"], quote=True)}" alt="{name}">'
        else:
            portrait = f'<div class="placeholder">{html.escape(item["name"][:1])}</div>'
        return f"""<article class="idol" style="--brand:{color}">
  <div class="portrait">{portrait}</div>
  <div class="idol-name">{name}</div>
  <div class="brand">{brand}</div>
</article>"""

    def _meta_block(self, title: str, values: list[str]) -> str:
        if not values:
            return ""
        text = html.escape(self._join_names(values))
        return f"""<div class="meta-block">
  <div class="meta-title">{html.escape(title)}</div>
  <div class="meta-text">{text}</div>
</div>"""

    async def _get_birthdays(self) -> dict[str, dict[str, list[str]]]:
        cache = await self.get_kv_data("birthday_cache", None)
        if self._is_cache_fresh(cache):
            return cache["data"]
        try:
            data = await self._fetch_birthdays()
            await self._save_cache(data)
            return data
        except Exception:
            if cache and cache.get("data"):
                logger.exception("刷新萌娘百科生日表失败，继续使用旧缓存。")
                return cache["data"]
            raise

    async def _save_cache(self, data: dict[str, dict[str, list[str]]]):
        await self.put_kv_data("birthday_cache", {"updated_at": int(time.time()), "data": data})

    def _is_cache_fresh(self, cache: Any) -> bool:
        if not isinstance(cache, dict) or not cache.get("data"):
            return False
        updated_at = int(cache.get("updated_at", 0))
        cache_seconds = max(int(self.config.get("cache_hours", 24)), 1) * 3600
        return time.time() - updated_at < cache_seconds

    async def _fetch_birthdays(self) -> dict[str, dict[str, list[str]]]:
        url = str(self.config.get("source_url") or SOURCE_URL)
        headers = {"User-Agent": "AstrBot-IdolmasterBirthdayBot/0.1"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
        return self._parse_birthdays(response.text)

    def _parse_birthdays(self, html: str) -> dict[str, dict[str, list[str]]]:
        parser = BirthdayPageParser()
        parser.feed(html)
        parser.close()
        return parser.data

    def _now(self) -> datetime:
        timezone_name = str(self.config.get("timezone", "Asia/Shanghai"))
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            logger.warning(f"无效时区 {timezone_name}，已回退到 Asia/Shanghai。")
            return datetime.now(ZoneInfo("Asia/Shanghai"))

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return bool(self.config.get(key, default))

    def _parse_date_text(self, text: str) -> tuple[int, int] | None:
        match = re.fullmatch(r"\s*(\d{1,2})[-/月](\d{1,2})(?:日)?\s*", text)
        if not match:
            return None
        month, day = int(match.group(1)), int(match.group(2))
        if month < 1 or month > 12 or day < 1 or day > 31:
            return None
        return month, day

    def _save_config(self):
        save = getattr(self.config, "save_config", None)
        if callable(save):
            save()
