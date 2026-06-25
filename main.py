from __future__ import annotations

import asyncio
import base64
import contextlib
import difflib
import importlib.util
import html
import os
import re
import shutil
import struct
import tempfile
import time
import zlib
from datetime import datetime, timedelta
from html.parser import HTMLParser
from mimetypes import guess_type
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

try:
    import astrbot.api.message_components as Comp
except Exception:
    Comp = None


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

# Put processed character images in the configured external character_assets_dir.
# Map character names to relative paths under that directory.
# Example:
# CHARACTER_IMAGE_ASSETS = {
#     "天海春香": "amami_haruka.png",
#     "如月千早": "kisaragi_chihaya.png",
# }
CHARACTER_IMAGE_ASSETS: dict[str, str] = {}
CHARACTER_PORTRAIT_ASSETS: dict[str, str] = {}
CHARACTER_COLORS: dict[str, str] = {}


def load_generated_character_assets() -> dict[str, str]:
    return load_generated_mapping("character_assets.py", "CHARACTER_IMAGE_ASSETS")


def load_generated_character_portraits() -> dict[str, str]:
    return load_generated_mapping("character_portraits.py", "CHARACTER_PORTRAIT_ASSETS")


def load_generated_character_colors() -> dict[str, str]:
    return load_generated_mapping("character_colors.py", "CHARACTER_COLORS")


def load_generated_mapping(filename: str, variable_name: str) -> dict[str, str]:
    path = Path(__file__).resolve().with_name(filename)
    if not path.exists():
        return {}
    spec = importlib.util.spec_from_file_location(f"imas_birthday_{path.stem}", path)
    if not spec or not spec.loader:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assets = getattr(module, variable_name, {})
    return assets if isinstance(assets, dict) else {}


CHARACTER_IMAGE_ASSETS.update(load_generated_character_assets())
CHARACTER_PORTRAIT_ASSETS.update(load_generated_character_portraits())
CHARACTER_COLORS.update(load_generated_character_colors())

BRAND_COLORS = {
    "THE_IDOLMASTER": "#f05a7e",
    "CINDERELLA_GIRLS": "#2f7fd3",
    "MILLION_LIVE": "#f2b84b",
    "SIDEM": "#1aa982",
    "SHINY_COLORS": "#5cc8f2",
    "GAKUEN_IDOLMASTER": "#f08a33",
    "VA_LIV": "#5c7cfa",
    "DEARLY_STARS": "#46b3a9",
    "STARLIT_SEASON": "#7c8ea6",
    "876_PRO": "#df6ea7",
    "961_PRO": "#4d465f",
    "KR": "#d94a4a",
    "OTHER": "#5b6472",
}

BIRTHDAY_BACKGROUND_BRANDS = [
    "THE_IDOLMASTER",
    "CINDERELLA_GIRLS",
    "MILLION_LIVE",
    "SIDEM",
    "SHINY_COLORS",
    "GAKUEN_IDOLMASTER",
]

BRAND_LABELS = {
    "THE_IDOLMASTER": "THE IDOLM@STER",
    "CINDERELLA_GIRLS": "シンデレラガールズ",
    "MILLION_LIVE": "ミリオンライブ！",
    "SIDEM": "SideM",
    "SHINY_COLORS": "シャイニーカラーズ",
    "GAKUEN_IDOLMASTER": "学園アイドルマスター",
    "VA_LIV": "ヴイアライヴ",
    "DEARLY_STARS": "THE IDOLM@STER Dearly Stars",
    "STARLIT_SEASON": "THE IDOLM@STER STARLIT SEASON",
    "876_PRO": "876 PRODUCTION",
    "961_PRO": "961 PRODUCTION",
    "KR": "KR",
    "OTHER": "THE IDOLM@STER",
}

BRAND_ALIASES = {
    "the_idolmaster": "THE_IDOLMASTER",
    "idolmaster": "THE_IDOLMASTER",
    "imas": "THE_IDOLMASTER",
    "765": "THE_IDOLMASTER",
    "765as": "THE_IDOLMASTER",
    "765pro": "THE_IDOLMASTER",
    "allstars": "THE_IDOLMASTER",
    "cinderellagirls": "CINDERELLA_GIRLS",
    "cinderella_girls": "CINDERELLA_GIRLS",
    "cinderella": "CINDERELLA_GIRLS",
    "cg": "CINDERELLA_GIRLS",
    "346": "CINDERELLA_GIRLS",
    "millionlive": "MILLION_LIVE",
    "million_live": "MILLION_LIVE",
    "million": "MILLION_LIVE",
    "ml": "MILLION_LIVE",
    "765ml": "MILLION_LIVE",
    "sidem": "SIDEM",
    "315": "SIDEM",
    "315pro": "SIDEM",
    "shinycolors": "SHINY_COLORS",
    "shiny_colors": "SHINY_COLORS",
    "shiny": "SHINY_COLORS",
    "sc": "SHINY_COLORS",
    "283": "SHINY_COLORS",
    "283pro": "SHINY_COLORS",
    "gakuen_idolmaster": "GAKUEN_IDOLMASTER",
    "gakuen": "GAKUEN_IDOLMASTER",
    "gakumas": "GAKUEN_IDOLMASTER",
    "gkm": "GAKUEN_IDOLMASTER",
    "va_liv": "VA_LIV",
    "va": "VA_LIV",
    "valiv": "VA_LIV",
    "va-liv": "VA_LIV",
    "vα_liv": "VA_LIV",
    "vα-liv": "VA_LIV",
    "vαliv": "VA_LIV",
    "dearlystars": "DEARLY_STARS",
    "dearly_stars": "DEARLY_STARS",
    "dearly": "DEARLY_STARS",
    "ds": "DEARLY_STARS",
    "876": "876_PRO",
    "876pro": "876_PRO",
    "876_pro": "876_PRO",
    "starlitseason": "STARLIT_SEASON",
    "starlit_season": "STARLIT_SEASON",
    "starlit": "STARLIT_SEASON",
    "st": "STARLIT_SEASON",
    "961": "961_PRO",
    "961pro": "961_PRO",
    "961_pro": "961_PRO",
    "kr": "KR",
}

CHARACTER_NAME_ALIASES = {
    "ミント": "Mint",
}

CHARACTER_REVERSE_ALIASES = {
    alias: name for name, alias in CHARACTER_NAME_ALIASES.items()
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

CHARACTER_BRAND_OVERRIDES = {
    "Mint": "KR",
    "寺本来可": "KR",
    "权势玲": "KR",
    "李睿恩": "KR",
    "李绣至": "KR",
    "许怜朱": "KR",
    "李智元": "KR",
    "车智瑟": "KR",
    "黄恩美": "KR",
    "金素利": "KR",
    "千宜英": "KR",
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
        self.assets_dir = self._resolve_character_assets_dir()
        self.portraits_dir = self._resolve_character_portraits_dir()
        self._task: asyncio.Task | None = None
        self._last_sent_date = ""
        self._suppressed_first_start_date = ""
        self._pending_retry_date = ""
        self._pending_retry_umos: set[str] = set()
        self._delivery_state_loaded = False
        self._delivery_state_exists = False
        self._scheduler_started_at = ""
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop()
            self._ensure_scheduler("init")

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        self._ensure_scheduler("astrbot_loaded")

    async def terminate(self):
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def _ensure_scheduler(self, reason: str):
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._scheduler())
        self._scheduler_started_at = self._now().strftime("%Y-%m-%d %H:%M:%S %Z")
        logger.info(f"偶像大师生日提醒定时任务已启动：reason={reason}, started_at={self._scheduler_started_at}")

    @filter.command_group("imasbd")
    def imasbd(self):
        """偶像大师生日提醒"""
        pass

    @imasbd.command("sid")
    async def imasbd_sid(self, event: AstrMessageEvent):
        """查看当前会话 UMO，填入白名单后可主动推送。"""
        self._stop_event(event)
        yield event.plain_result(f"当前 UMO：{event.unified_msg_origin}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("bind")
    async def imasbd_bind(self, event: AstrMessageEvent):
        """把当前会话加入生日推送白名单。"""
        self._stop_event(event)
        umo = event.unified_msg_origin
        white_umos = self._configured_white_umos()
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
        self._stop_event(event)
        try:
            data = await self._fetch_birthdays()
            await self._save_cache(data)
        except Exception as exc:
            logger.exception("刷新偶像大师生日表失败")
            yield event.plain_result(f"刷新失败：{exc}")
            return
        yield event.plain_result(f"刷新完成，共缓存 {len(data)} 个日期。")

    @imasbd.command("status")
    async def imasbd_status(self, event: AstrMessageEvent):
        """查看定时任务状态。"""
        self._stop_event(event)
        await self._load_delivery_state()
        yield event.plain_result(self._scheduler_status_text())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("reset-state")
    async def imasbd_reset_state(self, event: AstrMessageEvent):
        """清除每日自动推送的投递状态。"""
        self._stop_event(event)
        self._last_sent_date = ""
        self._suppressed_first_start_date = ""
        self._pending_retry_date = ""
        self._pending_retry_umos.clear()
        await self._save_delivery_state()
        yield event.plain_result("已清除生日提醒投递状态。若当前已过 send_time，定时器会在下一轮按配置判断是否补发。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("sendtest")
    async def imasbd_sendtest(self, event: AstrMessageEvent):
        """测试当前 OneBot/平台对多种图片发送方式的兼容性。"""
        self._stop_event(event)
        if not self._cfg_bool("enable_send_test", False):
            yield event.plain_result("发送测试默认关闭。请先在插件配置中打开 enable_send_test。")
            return
        logger.info(f"收到图片发送测试指令：umo={event.unified_msg_origin}")
        report = await self._run_send_tests(event.unified_msg_origin)
        yield event.plain_result(report)

    @imasbd.command("today")
    async def imasbd_today(self, event: AstrMessageEvent):
        """在当前会话预览并发送今天的生日祝贺。"""
        self._stop_event(event)
        now = self._now()
        result = await self._build_result(now.month, now.day)
        if not result["message"]:
            yield event.plain_result("今天没有匹配到偶像大师相关生日。")
            return
        await self._send_event_birthday_message(event, result["message"], result["card_path"])

    @imasbd.command("date")
    async def imasbd_date(self, event: AstrMessageEvent, date_text: str):
        """预览指定日期，格式 MM-DD，例如 /imasbd date 06-22。"""
        self._stop_event(event)
        parsed = self._parse_date_text(date_text)
        if not parsed:
            yield event.plain_result("日期格式不对，请使用 MM-DD，例如 06-22。")
            return
        month, day = parsed
        result = await self._build_result(month, day)
        if not result["message"]:
            yield event.plain_result(f"{month}月{day}日没有匹配到偶像大师相关生日。")
            return
        await self._send_event_birthday_message(event, result["message"], result["card_path"])

    @imasbd.command("assets")
    async def imasbd_assets(self, event: AstrMessageEvent, date_text: str = ""):
        """查看生日角色的本地图片匹配情况。"""
        self._stop_event(event)
        if date_text:
            parsed = self._parse_date_text(date_text)
            if not parsed:
                yield event.plain_result("日期格式不对，请使用 /imasbd assets MM-DD，例如 /imasbd assets 05-20。")
                return
            month, day = parsed
        else:
            now = self._now()
            month, day = now.month, now.day
        yield event.plain_result(await self._assets_text(month, day))
        return

    @imasbd.command("find")
    async def imasbd_find(self, event: AstrMessageEvent, query: GreedyStr):
        """按角色名反查生日，支持轻量模糊查询。"""
        self._stop_event(event)
        result = await self._build_find_character_result(query)
        if not result["message"]:
            yield event.plain_result(result["error"])
            return
        await self._send_event_birthday_message(event, result["message"], result["card_path"])

    @filter.permission_type(filter.PermissionType.ADMIN)
    @imasbd.command("migrate-assets")
    async def imasbd_migrate_assets(self, event: AstrMessageEvent, source_dir: str = ""):
        """把旧图片目录复制到当前配置的角色图片目录。"""
        self._stop_event(event)
        source = Path(source_dir) if source_dir else self.plugin_dir / "assets" / "characters"
        if not source.is_absolute():
            source = self.plugin_dir / source
        copied = self._copy_assets(source, self.assets_dir)
        yield event.plain_result(f"图片迁移完成：{source} -> {self.assets_dir}\n复制/更新 {copied} 个文件。")

    def _copy_assets(self, source: Path, destination: Path) -> int:
        if not source.exists():
            return 0
        copied = 0
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative_path = path.relative_to(source)
            target = destination / relative_path
            if target.exists() and target.stat().st_size == path.stat().st_size:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
        return copied

    async def _assets_text(self, month: int, day: int) -> str:
        data = await self._get_birthdays()
        date_key = f"{month:02d}-{day:02d}"
        entry = data.get(date_key) or {}
        characters = self._visible_characters(entry)
        lines = [
            f"图片目录：{self.assets_dir}",
            f"透明立绘目录：{self.portraits_dir}",
            f"素材模式：{self._card_asset_mode()}",
            f"企划素材覆盖：{self._card_asset_mode_overrides_text()}",
            f"日期：{date_key}",
        ]
        if not characters:
            lines.append("没有需要匹配图片的角色。")
            return "\n".join(lines)

        for character in characters:
            brand = self._character_brand(character)
            mode = self._card_asset_mode_for_brand(brand)
            mapped = self._character_asset_filename(character)
            portrait_mapped = self._character_portrait_filename(character)
            image_path = self._character_image_path(character)
            portrait_path = self._character_portrait_path(character)
            if portrait_path:
                lines.append(f"PORTRAIT {character} [{brand}/{mode}]: {portrait_mapped} -> {portrait_path}")
            if image_path:
                lines.append(f"IMAGE {character} [{brand}/{mode}]: {mapped} -> {image_path}")
            else:
                expected = self.assets_dir / mapped if mapped else "未生成映射"
                lines.append(f"MISS {character} [{brand}/{mode}]: {mapped or '未生成映射'} -> {expected}")
        return "\n".join(lines)

    async def _find_character_text(self, query: str) -> str:
        query, matches, error = await self._find_character_matches(query)
        if error:
            return error

        best_score, best = matches[0]
        lines = [f"查询：{query}"]
        if self._normalize_character_query(best["name"]) != self._normalize_character_query(query):
            lines.append(f"是否在找：{best['name']}")
        else:
            lines.append(f"找到：{best['name']}")

        for score, record in matches[:5]:
            marker = "★ " if record is best else "  "
            lines.append(
                f"{marker}{record['name']}：{self._format_date_key(record['date_key'])}"
                f"（{record['brand_label']}，匹配度 {score:.2f}）"
            )
        if len(matches) > 5:
            lines.append(f"还有 {len(matches) - 5} 个相近结果，换更完整的名字可以缩小范围。")
        return "\n".join(lines)

    async def _build_find_character_result(self, query: str) -> dict[str, str]:
        query, matches, error = await self._find_character_matches(query)
        if error:
            return {"message": "", "card_path": "", "error": error}

        best_score, best = matches[0]
        month, day = self._month_day_from_date_key(best["date_key"])
        if not month or not day:
            return {"message": "", "card_path": "", "error": f"找到 {best['name']}，但生日日期格式异常：{best['date_key']}"}

        entry = {
            "characters": [best["name"]],
            "seiyuu": [],
            "related_people": [],
            "events": [],
        }
        message = self._build_message_from_entry(month, day, entry)
        if not message:
            return {"message": "", "card_path": "", "error": f"找到 {best['name']}，但当前模板没有生成可发送内容。"}

        if self._normalize_character_query(best["name"]) != self._normalize_character_query(query):
            message = f"是否在找：{best['name']}\n{message}"

        card_path = ""
        if self._cfg_bool("render_card", True):
            card_path = await self._render_card(month, day, entry)
        return {"message": message, "card_path": card_path, "error": ""}

    async def _find_character_matches(self, query: str) -> tuple[str, list[tuple[float, dict[str, str]]], str]:
        query = clean_text(query or "")
        if not query:
            return query, [], "请提供要查询的角色名，例如：/imasbd find 天海春香"

        data = await self._get_birthdays()
        records = self._character_birthday_records(data)
        if not records:
            return query, [], "当前生日缓存里没有可查询的角色。"

        query_key = self._normalize_character_query(query)
        matches: list[tuple[float, dict[str, str]]] = []
        for record in records:
            name_key = self._normalize_character_query(record["name"])
            base_key = self._normalize_character_query(self._base_character_name(record["name"]))
            keys = [name_key, base_key]
            score = max(self._character_match_score(query_key, key) for key in keys if key)
            if score >= 0.45:
                matches.append((score, record))

        matches.sort(key=lambda item: (-item[0], item[1]["date_key"], item[1]["name"]))
        if not matches:
            return query, [], f"没有找到「{query}」对应的小偶像生日。"
        return query, matches, ""

    def _character_birthday_records(self, data: dict[str, dict[str, list[str]]]) -> list[dict[str, str]]:
        records_by_name: dict[str, dict[str, str]] = {}
        for date_key, entry in data.items():
            for character in self._visible_characters(entry):
                name = self._base_character_name(character)
                if not name:
                    continue
                brand = self._character_brand(name)
                record = {
                    "name": name,
                    "date_key": date_key,
                    "brand_label": BRAND_LABELS.get(brand, BRAND_LABELS["OTHER"]),
                }
                previous = records_by_name.get(name)
                if previous is None or date_key < previous["date_key"]:
                    records_by_name[name] = record
        return list(records_by_name.values())

    def _normalize_character_query(self, text: str) -> str:
        text = CHARACTER_NAME_ALIASES.get(text, text)
        text = self._base_character_name(text)
        text = text.lower()
        return re.sub(r"[\s·・．.。\-_/＿—~～（）()【】\[\]「」『』]+", "", text)

    def _character_match_score(self, query_key: str, name_key: str) -> float:
        if not query_key or not name_key:
            return 0.0
        if query_key == name_key:
            return 1.0
        if query_key in name_key or name_key in query_key:
            return 0.92
        return difflib.SequenceMatcher(None, query_key, name_key, autojunk=False).ratio()

    def _format_date_key(self, date_key: str) -> str:
        try:
            month_text, day_text = date_key.split("-", 1)
            return f"{int(month_text)}月{int(day_text)}日（{date_key}）"
        except Exception:
            return date_key

    def _month_day_from_date_key(self, date_key: str) -> tuple[int, int] | tuple[None, None]:
        try:
            month_text, day_text = date_key.split("-", 1)
            return int(month_text), int(day_text)
        except Exception:
            return None, None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def imasbd_text_fallback(self, event: AstrMessageEvent):
        """Fallback for adapters that log /imasbd text but do not dispatch command groups."""
        args = self._parse_imasbd_text(getattr(event, "message_str", ""))
        if args is None:
            return
        self._stop_event(event)
        subcommand = args[0] if args else "help"
        if subcommand == "sid":
            yield event.plain_result(f"当前 UMO：{event.unified_msg_origin}")
            return
        if subcommand == "status":
            await self._load_delivery_state()
            yield event.plain_result(self._scheduler_status_text())
            return
        if subcommand == "today":
            now = self._now()
            await self._send_event_birthday_message_for_date(event, now.month, now.day)
            return
        if subcommand == "date":
            date_text = args[1] if len(args) > 1 else ""
            parsed = self._parse_date_text(date_text)
            if not parsed:
                yield event.plain_result("日期格式不对，请使用 /imasbd date MM-DD，例如 /imasbd date 06-22。")
                return
            await self._send_event_birthday_message_for_date(event, parsed[0], parsed[1])
            return
        if subcommand == "assets":
            date_text = args[1] if len(args) > 1 else ""
            if date_text:
                parsed = self._parse_date_text(date_text)
                if not parsed:
                    yield event.plain_result("日期格式不对，请使用 /imasbd assets MM-DD，例如 /imasbd assets 05-20。")
                    return
                yield event.plain_result(await self._assets_text(parsed[0], parsed[1]))
                return
            now = self._now()
            yield event.plain_result(await self._assets_text(now.month, now.day))
            return
        if subcommand == "find":
            query = " ".join(args[1:]) if len(args) > 1 else ""
            result = await self._build_find_character_result(query)
            if not result["message"]:
                yield event.plain_result(result["error"])
                return
            await self._send_event_birthday_message(event, result["message"], result["card_path"])
            return
        if subcommand == "sendtest":
            if not self._cfg_bool("enable_send_test", False):
                yield event.plain_result("发送测试默认关闭。请先在插件配置中打开 enable_send_test。")
                return
            logger.info(f"收到图片发送测试 fallback 指令：umo={event.unified_msg_origin}")
            report = await self._run_send_tests(event.unified_msg_origin)
            yield event.plain_result(report)
            return
        yield event.plain_result(
            "可用指令：\n"
            "/imasbd sid\n"
            "/imasbd status\n"
            "/imasbd reset-state\n"
            "/imasbd today\n"
            "/imasbd date 06-22\n"
            "/imasbd assets 06-22\n"
            "/imasbd find 天海春香\n"
            "/imasbd sendtest"
        )

    async def _scheduler(self):
        logger.info("偶像大师生日提醒定时任务循环开始。")
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
        await self._load_delivery_state()
        now = self._now()
        send_time = str(self.config.get("send_time", "09:00"))
        send_minutes = self._parse_send_time_minutes(send_time)
        if send_minutes is None:
            logger.warning(f"偶像大师生日提醒 send_time 格式无效：{send_time}")
            return
        today_key = now.strftime("%Y-%m-%d")
        if self._suppressed_first_start_date == today_key and self._last_sent_date != today_key:
            return
        if (
            not self._delivery_state_exists
            and self._is_send_time_due(now, send_minutes)
            and not self._cfg_bool("catch_up_on_first_start", False)
        ):
            logger.warning(
                f"偶像大师生日提醒首次启动时已过今日推送时间，默认不补发以避免误推：date={today_key}, timezone={now.tzname()}"
            )
            self._suppressed_first_start_date = today_key
            await self._save_delivery_state()
            return
        if not self._is_send_time_due(now, send_minutes):
            return
        if self._last_sent_date == today_key:
            return

        white_umos = self._configured_white_umos()
        if not white_umos:
            logger.warning("偶像大师生日提醒白名单为空，跳过推送。")
            return

        result = await self._build_result(now.month, now.day)
        if not result["message"]:
            logger.info(f"今天没有偶像大师生日提醒内容，跳过推送：date={today_key}, timezone={now.tzname()}")
            self._suppressed_first_start_date = ""
            self._pending_retry_date = ""
            self._pending_retry_umos.clear()
            self._last_sent_date = today_key
            await self._save_delivery_state()
            return

        targets = white_umos
        if self._pending_retry_date == today_key and self._pending_retry_umos:
            targets = [umo for umo in white_umos if umo in self._pending_retry_umos]
        if not targets:
            logger.warning(f"偶像大师生日提醒没有可重试目标，清理 pending 状态：date={today_key}")
            self._pending_retry_date = ""
            self._pending_retry_umos.clear()
            await self._save_delivery_state()
            return

        logger.info(f"偶像大师生日提醒开始推送：date={today_key}, timezone={now.tzname()}, targets={len(targets)}")
        failed_umos: list[str] = []
        for umo in targets:
            if not await self._send_active_message(umo, result["message"], result["card_path"]):
                failed_umos.append(umo)
        if failed_umos:
            self._pending_retry_date = today_key
            self._pending_retry_umos = set(failed_umos)
            await self._save_delivery_state()
            logger.warning(f"偶像大师生日提醒部分目标发送失败，保留待重试状态：date={today_key}, failed={len(failed_umos)}")
            return
        self._suppressed_first_start_date = ""
        self._pending_retry_date = ""
        self._pending_retry_umos.clear()
        self._last_sent_date = today_key
        await self._save_delivery_state()

    async def _send_event_birthday_message_for_date(self, event: AstrMessageEvent, month: int, day: int):
        result = await self._build_result(month, day)
        if not result["message"]:
            yield_text = f"{month}月{day}日没有匹配到偶像大师相关生日。"
            await self.context.send_message(event.unified_msg_origin, MessageChain().message(yield_text))
            return
        await self._send_event_birthday_message(event, result["message"], result["card_path"])

    async def _send_active_message(self, umo: str, message: str, card_path: str = "") -> bool:
        return await self._send_birthday_message(umo, message, card_path)

    async def _send_event_birthday_message(self, event: AstrMessageEvent, message: str, card_path: str = ""):
        await self._send_birthday_message(event.unified_msg_origin, message, card_path)

    async def _send_birthday_message(self, umo: str, message: str, card_path: str = "") -> bool:
        mode = self._birthday_send_mode()
        if mode != "split_file_image" and card_path:
            try:
                ok = await self.context.send_message(umo, self._build_birthday_message_chain(message, card_path, mode))
                if not ok:
                    logger.warning(f"偶像大师生日提醒发送失败，未找到平台：{umo}")
                return bool(ok)
            except Exception:
                logger.exception(f"偶像大师生日提醒组合消息发送失败，降级为分开发送：{mode}")
                if mode != "combined_component_base64":
                    try:
                        ok = await self.context.send_message(
                            umo,
                            self._build_birthday_message_chain(message, card_path, "combined_component_base64"),
                        )
                        if not ok:
                            logger.warning(f"偶像大师生日提醒 base64 重试发送失败，未找到平台：{umo}")
                        return bool(ok)
                    except Exception:
                        logger.exception("偶像大师生日提醒 base64 组合消息重试失败，继续降级为分开发送。")

        try:
            ok = await self.context.send_message(umo, MessageChain().message(message))
            if not ok:
                logger.warning(f"偶像大师生日提醒文字发送失败，未找到平台：{umo}")
                return False
        except Exception:
            logger.exception(f"偶像大师生日提醒文字发送异常：{umo}")
            return False
        if not card_path:
            return True
        try:
            return bool(await self.context.send_message(umo, self._build_image_message_chain(card_path)))
        except Exception:
            logger.exception("发送生日卡片图片失败，已保留文字发送结果。")
            return False

    def _build_image_message_chain(self, card_path: str) -> MessageChain:
        chain = MessageChain()
        image_path = self._image_send_path(card_path)
        logger.info(self._image_send_debug("生日卡片分开发送图片", card_path, image_path))
        chain.file_image(image_path)
        return chain

    def _build_birthday_message_chain(self, message: str, card_path: str, mode: str) -> MessageChain:
        image_path = self._image_send_path(card_path)
        logger.info(self._image_send_debug(f"生日卡片组合发送图片 mode={mode}", card_path, image_path))
        if mode == "combined_component_file":
            if Comp is None:
                logger.warning("message_components 不可用，改用 combined_file_image。")
            else:
                return MessageChain(chain=[Comp.Plain(message), Comp.Image.fromFileSystem(image_path)])
        if mode == "combined_component_base64":
            if Comp is None:
                logger.warning("message_components 不可用，改用 combined_file_image。")
            else:
                data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
                return MessageChain(chain=[Comp.Plain(message), Comp.Image.fromBase64(data)])

        chain = MessageChain().message(message)
        chain.file_image(image_path)
        return chain

    def _birthday_send_mode(self) -> str:
        mode = str(self.config.get("birthday_send_mode", "combined_component_base64") or "").strip().lower()
        aliases = {
            "split": "split_file_image",
            "combined": "combined_file_image",
            "file": "combined_file_image",
            "component_file": "combined_component_file",
            "base64": "combined_component_base64",
        }
        mode = aliases.get(mode, mode)
        valid_modes = {
            "split_file_image",
            "combined_file_image",
            "combined_component_file",
            "combined_component_base64",
        }
        if mode not in valid_modes:
            logger.warning(f"未知 birthday_send_mode={mode}，改用 combined_component_base64。")
            return "combined_component_base64"
        return mode

    async def _run_send_tests(self, umo: str) -> str:
        image_path = self._send_test_image_path()
        timeout = self._send_test_timeout()
        debug = self._cfg_bool("debug_send_test", False)
        tests = [
            ("split_file_image", "文字和图片分开发送，图片使用 MessageChain.file_image"),
            ("combined_file_image", "同一 MessageChain 内使用 message + file_image"),
            ("combined_component_file", "同一消息内使用组件 Image.fromFileSystem"),
            ("combined_component_base64", "同一消息内使用组件 Image.fromBase64"),
        ]
        logger.info(f"图片发送测试开始：umo={umo}, image={image_path}, timeout={timeout}s, debug={debug}")
        lines = [
            "图片发送方式测试结果：",
            f"测试图：{image_path}",
            f"测试图大小：{Path(image_path).stat().st_size} bytes",
            f"单次发送超时：{timeout}s",
        ]
        if debug:
            with contextlib.suppress(Exception):
                await self._send_test_progress(umo, "[imasbd sendtest] start")
        for key, description in tests:
            started = time.monotonic()
            if debug:
                logger.info(f"图片发送测试开始：{key} - {description}")
                with contextlib.suppress(Exception):
                    await self._send_test_progress(umo, f"[imasbd sendtest] testing {key}")
            try:
                case_timeout = timeout * 2 + 2 if key == "split_file_image" else timeout + 2
                await asyncio.wait_for(
                    self._send_one_test_case(umo, key, image_path, timeout),
                    timeout=case_timeout,
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - started
                logger.warning(f"图片发送测试超时：{key}, elapsed={elapsed:.2f}s")
                lines.append(f"FAIL {key}: TimeoutError: 超过 {timeout}s 没有返回")
            except Exception as exc:
                elapsed = time.monotonic() - started
                logger.exception(f"图片发送测试失败：{key}, elapsed={elapsed:.2f}s")
                lines.append(f"FAIL {key}: {type(exc).__name__}: {exc}")
            else:
                elapsed = time.monotonic() - started
                logger.info(f"图片发送测试成功：{key}, elapsed={elapsed:.2f}s")
                lines.append(f"PASS {key}: {description} ({elapsed:.2f}s)")
        logger.info("图片发送测试结束。")
        return "\n".join(lines)

    async def _send_one_test_case(self, umo: str, key: str, image_path: str, timeout: int):
        text = f"[imasbd sendtest] {key}"
        if key == "split_file_image":
            await self._send_test_chain(umo, MessageChain().message(text), timeout, f"{key}: text")
            await self._send_test_chain(umo, self._build_image_message_chain(image_path), timeout, f"{key}: image")
            return
        if key == "combined_file_image":
            chain = MessageChain().message(text)
            chain.file_image(image_path)
            await self._send_test_chain(umo, chain, timeout, key)
            return
        if key == "combined_component_file":
            if Comp is None:
                raise RuntimeError("astrbot.api.message_components 不可用")
            await self._send_test_chain(
                umo,
                MessageChain(chain=[Comp.Plain(text), Comp.Image.fromFileSystem(image_path)]),
                timeout,
                key,
            )
            return
        if key == "combined_component_base64":
            if Comp is None:
                raise RuntimeError("astrbot.api.message_components 不可用")
            data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
            await self._send_test_chain(
                umo,
                MessageChain(chain=[Comp.Plain(text), Comp.Image.fromBase64(data)]),
                timeout,
                key,
            )
            return
        raise ValueError(f"未知发送测试类型：{key}")

    async def _send_test_chain(self, umo: str, chain: MessageChain, timeout: int, label: str):
        logger.info(f"图片发送测试发送中：{label}, timeout={timeout}s")
        try:
            ok = await asyncio.wait_for(self.context.send_message(umo, chain), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"图片发送测试单次发送超时：{label}, timeout={timeout}s")
            raise TimeoutError(f"{label} 超过 {timeout}s 没有返回") from None
        logger.info(f"图片发送测试发送完成：{label}, result={ok}")
        if not ok:
            raise RuntimeError(f"{label} 未找到平台或发送失败：{umo}")

    async def _send_test_progress(self, umo: str, text: str):
        timeout = min(self._send_test_timeout(), 5)
        await self._send_test_chain(umo, MessageChain().message(text), timeout, "debug progress")

    def _send_test_image_path(self) -> str:
        path = Path(tempfile.gettempdir()) / "astrbot_plugin_imas_birthday" / "send_test_large.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size < 4096:
            self._write_send_test_png(path)
        return str(path)

    def _write_send_test_png(self, path: Path, width: int = 960, height: int = 540):
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        rows = []
        for y in range(height):
            row = bytearray([0])
            for x in range(width):
                r = (70 + x * 120 // width + y * 20 // height) % 256
                g = (110 + y * 100 // height) % 256
                b = (180 + (x + y) * 60 // (width + height)) % 256
                if 32 < x < 928 and 32 < y < 508 and (x // 24 + y // 24) % 2 == 0:
                    r = min(255, r + 18)
                    g = min(255, g + 18)
                    b = min(255, b + 18)
                row.extend((r, g, b))
            rows.append(bytes(row))

        raw = b"".join(rows)
        data = b"\x89PNG\r\n\x1a\n"
        data += chunk("IHDR".encode("ascii"), struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        data += chunk("IDAT".encode("ascii"), zlib.compress(raw, level=6))
        data += chunk("IEND".encode("ascii"), b"")
        path.write_bytes(data)

    def _send_test_timeout(self) -> int:
        try:
            value = int(self.config.get("send_test_timeout", 10))
        except (TypeError, ValueError):
            value = 10
        return min(max(value, 3), 60)

    def _stop_event(self, event: AstrMessageEvent):
        stop_event = getattr(event, "stop_event", None)
        if callable(stop_event):
            stop_event()

    def _image_send_path(self, image_path: str) -> str:
        image_path = str(image_path or "").strip()
        if image_path.startswith("file://"):
            image_path = image_path.replace("file:///", "", 1).replace("file://", "", 1)
        path = Path(image_path)
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}:
            return str(path)

        destination = (
            Path(tempfile.gettempdir())
            / "astrbot_plugin_imas_birthday"
            / "rendered_cards"
            / f"{path.name}.png"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, destination)
            return str(destination)
        except Exception:
            logger.exception(f"复制生日卡片到带扩展名路径失败：{image_path}")
            return image_path

    def _image_send_debug(self, label: str, original_path: str, send_path: str) -> str:
        send = Path(send_path)
        size = send.stat().st_size if send.exists() else "missing"
        magic = self._file_magic(send)
        return f"{label}: original={original_path}, send={send_path}, suffix={send.suffix or 'none'}, size={size}, magic={magic}"

    def _file_magic(self, path: Path) -> str:
        try:
            return path.read_bytes()[:12].hex()
        except Exception:
            return "unreadable"

    async def _wait_for_stable_image(self, path: Path, attempts: int = 12, delay: float = 0.15) -> bool:
        last_size = -1
        stable_count = 0
        for _ in range(attempts):
            if path.exists():
                size = path.stat().st_size
                if size == last_size and size > 4096 and self._image_suffix_from_magic(path):
                    stable_count += 1
                    if stable_count >= 2:
                        return True
                else:
                    stable_count = 0
                last_size = size
            await asyncio.sleep(delay)
        return path.exists() and path.stat().st_size > 4096 and bool(self._image_suffix_from_magic(path))

    def _image_suffix_from_magic(self, path: Path) -> str:
        try:
            header = path.read_bytes()[:12]
        except Exception:
            return ""
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if header.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
            return ".gif"
        if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
            return ".webp"
        return ""

    def _resolve_character_assets_dir(self) -> Path:
        configured = str(self.config.get("character_assets_dir", "") or "").strip()
        env_value = os.environ.get("IMAS_BIRTHDAY_ASSETS_DIR", "").strip()
        value = configured or env_value
        if value:
            path = Path(os.path.expandvars(value)).expanduser()
            return path if path.is_absolute() else self.plugin_dir / path

        if self.plugin_dir.parent.name == "plugins":
            return self.plugin_dir.parent.parent / "imas_birthday_assets" / "characters"
        return self.plugin_dir / "assets" / "characters"

    def _resolve_character_portraits_dir(self) -> Path:
        configured = str(self.config.get("character_portraits_dir", "") or "").strip()
        env_value = os.environ.get("IMAS_BIRTHDAY_PORTRAITS_DIR", "").strip()
        value = configured or env_value
        if value:
            path = Path(os.path.expandvars(value)).expanduser()
            return path if path.is_absolute() else self.plugin_dir / path

        if self.plugin_dir.parent.name == "plugins":
            return self.plugin_dir.parent.parent / "imas_birthday_assets" / "portraits"
        return self.plugin_dir / "assets" / "portraits"

    def _parse_imasbd_text(self, message: str) -> list[str] | None:
        text = str(message or "").strip()
        for prefix in ("/imasbd", "／imasbd"):
            if text == prefix:
                return []
            if text.startswith(prefix + " "):
                return [part.strip().lower() for part in text[len(prefix) :].split() if part.strip()]
        return None

    async def _build_result(self, month: int, day: int) -> dict[str, str]:
        data = await self._get_birthdays()
        entry = data.get(f"{month:02d}-{day:02d}")
        if self._cfg_bool("require_character_birthday", True) and not self._visible_characters(entry or {}):
            return {"message": "", "card_path": ""}
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
            lines.extend(self._format_lines("characters", self._visible_characters(entry)))
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
        characters = self._visible_characters(entry)
        seiyuu = self._split_people(entry.get("seiyuu", []))
        related_people = self._split_people(entry.get("related_people", []))
        events = entry.get("events", [])

        items = [self._card_item(name) for name in characters]
        if not items and not self._cfg_bool("render_card_without_character_image", True):
            return ""

        layout = self._card_layout(len(items))
        card_related_people = related_people if self._cfg_bool("include_related_people", False) else []
        card_events = events if self._cfg_bool("include_events", False) else []
        render_mode = self._card_render_mode()
        if render_mode == "pillow":
            return self._render_card_with_pillow(month, day, items, seiyuu, card_related_people, card_events, layout)

        html_text = self._birthday_card_html(
            month=month,
            day=day,
            items=items,
            seiyuu=seiyuu,
            related_people=card_related_people,
            events=card_events,
            layout=layout,
        )
        try:
            card_path = await self.html_render(
                html_text,
                {},
                return_url=False,
                options={
                    "viewport": {"width": layout["render_width"], "height": layout["viewport_height"]},
                    "type": "png",
                    "full_page": True,
                },
            )
            prepared = await self._prepare_rendered_card(card_path)
            if prepared:
                return prepared
            logger.warning("AstrBot html_render 返回非图片产物，尝试使用本地 Pillow 渲染生日卡片。")
            if render_mode == "html":
                return ""
            return self._render_card_with_pillow(month, day, items, seiyuu, card_related_people, card_events, layout)
        except Exception:
            logger.exception("生日卡片 html_render 渲染失败，尝试使用本地 Pillow 渲染。")
            if render_mode == "html":
                return ""
            return self._render_card_with_pillow(month, day, items, seiyuu, card_related_people, card_events, layout)

    async def _prepare_rendered_card(self, card_path: str) -> str:
        if not card_path:
            return ""
        path = Path(str(card_path).replace("file:///", "", 1).replace("file://", "", 1))
        if not await self._wait_for_stable_image(path):
            logger.warning(
                self._image_send_debug("生日卡片渲染产物无效", str(card_path), str(path))
                + self._invalid_render_excerpt(path)
            )
            return ""
        suffix = self._image_suffix_from_magic(path) or path.suffix.lower() or ".png"
        destination = (
            Path(tempfile.gettempdir())
            / "astrbot_plugin_imas_birthday"
            / "rendered_cards"
            / f"{path.stem}{suffix}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        logger.info(self._image_send_debug("生日卡片渲染产物已准备", str(card_path), str(destination)))
        return str(destination)

    def _render_card_with_pillow(
        self,
        month: int,
        day: int,
        items: list[dict[str, str]],
        seiyuu: list[str],
        related_people: list[str],
        events: list[str],
        layout: dict[str, int],
    ) -> str:
        try:
            from PIL import Image, ImageDraw, ImageFilter, ImageFont
        except Exception:
            logger.exception("本地 Pillow 渲染不可用，请确认 requirements.txt 中的 Pillow 已安装。")
            return ""

        width = layout["card_width"]
        padding = layout["card_padding"]
        gap = layout["grid_gap"]
        item_width = layout["item_width"]
        portrait_height = layout["portrait_height"]
        card_height = portrait_height + 86
        max_columns = layout["columns"]
        rows = [items[index : index + max_columns] for index in range(0, len(items), max_columns)] or [[]]
        meta_blocks = [
            ("声优", seiyuu),
            ("相关人士", related_people),
            ("事件", events),
        ]
        meta_blocks = [(title, values) for title, values in meta_blocks if values]
        meta_rows = [meta_blocks[index : index + max_columns] for index in range(0, len(meta_blocks), max_columns)]
        height = max(
            720,
            padding * 2
            + 108
            + 20
            + len(rows) * card_height
            + max(0, len(rows) - 1) * gap
            + (14 + len(meta_rows) * 72 + max(0, len(meta_rows) - 1) * 10 if meta_rows else 0)
            + 54,
        )

        image = self._pillow_six_brand_background(Image, ImageDraw, ImageFilter, width, height)
        draw = ImageDraw.Draw(image)

        title_font = self._pil_font(ImageFont, 38, bold=True)
        subtitle_font = self._pil_font(ImageFont, 14)
        date_font = self._pil_font(ImageFont, 32, bold=True)
        small_font = self._pil_font(ImageFont, 12, bold=True)
        name_font = self._pil_font(ImageFont, 20, bold=True)
        meta_title_font = self._pil_font(ImageFont, 13, bold=True)
        meta_font = self._pil_font(ImageFont, 16, bold=True)
        footer_font = self._pil_font(ImageFont, 10)

        y = padding
        draw.text((padding, y), str(self.config.get("card_title", "Happy Birthday")), fill="#20242c", font=title_font)
        draw.text((padding, y + 50), str(self.config.get("card_subtitle", "THE IDOLM@STER Birthday")), fill="#5b6472", font=subtitle_font)
        date_text = f"{month:02d}.{day:02d}"
        date_bbox = draw.textbbox((0, 0), date_text, font=date_font)
        date_x = width - padding - (date_bbox[2] - date_bbox[0])
        draw.text((date_x, y + 3), date_text, fill="#f05a7e", font=date_font)
        draw.text((width - padding - 58, y + 43), "Birthday", fill="#5b6472", font=small_font)
        draw.line((padding, y + 88, width - padding, y + 88), fill=(32, 36, 44, 36), width=3)
        y += 108

        for row in rows:
            row_width = len(row) * item_width + max(0, len(row) - 1) * gap
            x = (width - row_width) // 2
            for item in row:
                self._draw_pillow_idol_card(draw, image, item, x, y, item_width, portrait_height, card_height, name_font, small_font)
                x += item_width + gap
            y += card_height + gap

        if meta_rows:
            y += 2
        for row in meta_rows:
            row_width = len(row) * item_width + max(0, len(row) - 1) * 10
            x = (width - row_width) // 2
            for title, values in row:
                draw.rounded_rectangle((x, y, x + item_width, y + 62), radius=6, fill=(255, 255, 255), outline=(232, 232, 232))
                draw.text((x + 14, y + 10), title, fill="#5b6472", font=meta_title_font)
                text = "、".join(values)
                draw.text((x + 14, y + 32), text, fill="#20242c", font=meta_font)
                x += item_width + 10
            y += 72

        footer = (
            "Character images are sourced from local assets prepared by the bot owner, Moegirlpedia, official sites, and fan DBs. "
            "Thanks to Moegirlpedia and the asset data providers. THE IDOLM@STER rights belong to their respective owners."
        )
        footer_lines = self._pil_wrap_text(draw, footer, footer_font, width - padding * 2)
        footer_y = min(height - padding - len(footer_lines) * 13, y + 12)
        for line in footer_lines:
            draw.text((padding, footer_y), line, fill="#6b7280", font=footer_font)
            footer_y += 13

        destination = (
            Path(tempfile.gettempdir())
            / "astrbot_plugin_imas_birthday"
            / "rendered_cards"
            / f"pillow_card_{int(time.time() * 1000)}_{month:02d}{day:02d}.png"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG")
        logger.info(self._image_send_debug("生日卡片本地 Pillow 渲染产物已准备", str(destination), str(destination)))
        return str(destination)

    def _pillow_six_brand_background(self, image_module: Any, image_draw: Any, image_filter: Any, width: int, height: int) -> Any:
        base = image_module.new("RGBA", (width, height), "#f7f3ec")
        sectors = image_module.new("RGBA", (width, height), (255, 255, 255, 0))
        draw = image_draw.Draw(sectors)
        radius = int(((width * width + height * height) ** 0.5) * 0.72)
        cx = width // 2
        cy = height // 2
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        for index, brand in enumerate(BIRTHDAY_BACKGROUND_BRANDS):
            rgb = self._hex_rgb(BRAND_COLORS[brand], (120, 130, 145))
            start = -90 + index * 60
            draw.pieslice(bbox, start=start, end=start + 60, fill=(*rgb, 104))

        softened = sectors.filter(image_filter.GaussianBlur(32))
        image = image_module.alpha_composite(base, softened)
        frost = image_module.new("RGBA", (width, height), (255, 255, 255, 100))
        image = image_module.alpha_composite(image, frost)
        veil = image_module.new("RGBA", (width, height), (255, 248, 238, 34))
        image = image_module.alpha_composite(image, veil)
        return image.convert("RGB")

    def _draw_pillow_idol_card(self, draw: Any, canvas: Any, item: dict[str, str], x: int, y: int, width: int, portrait_height: int, height: int, name_font: Any, small_font: Any) -> None:
        brand_rgb = self._hex_rgb(item.get("color", ""), (99, 111, 129))
        draw.rounded_rectangle((x, y, x + width, y + height), radius=8, fill=(255, 255, 255), outline=(232, 232, 232))
        image_path = item.get("path", "")
        if image_path and Path(image_path).exists():
            try:
                if item.get("asset_kind") == "portrait":
                    self._draw_pillow_portrait_panel(draw, canvas, Path(image_path), x, y, width, portrait_height, brand_rgb)
                else:
                    portrait = self._pil_cover_image(Path(image_path), width, portrait_height)
                    canvas.paste(portrait, (x, y))
            except Exception:
                logger.exception(f"本地卡片读取角色图失败：{image_path}")
                draw.rectangle((x, y, x + width, y + portrait_height), fill=brand_rgb)
        else:
            draw.rectangle((x, y, x + width, y + portrait_height), fill=brand_rgb)
            first = item.get("name", "?")[:1]
            bbox = draw.textbbox((0, 0), first, font=name_font)
            draw.text((x + (width - bbox[2] + bbox[0]) / 2, y + portrait_height / 2 - 18), first, fill=(255, 255, 255), font=name_font)
        draw.rounded_rectangle((x + 12, y + portrait_height + 10, x + width - 12, y + portrait_height + 15), radius=4, fill=brand_rgb)
        self._draw_pillow_brand_logo(canvas, item, x, y + portrait_height, width, 86)
        draw.text((x + 12, y + portrait_height + 25), item.get("name", ""), fill="#20242c", font=name_font)
        draw.text((x + 12, y + portrait_height + 53), item.get("label", ""), fill="#5b6472", font=small_font)

    def _draw_pillow_brand_logo(self, canvas: Any, item: dict[str, str], x: int, y: int, width: int, height: int) -> None:
        logo_path = item.get("logo_path", "")
        if not logo_path:
            return
        path = Path(logo_path)
        if not path.exists():
            return
        try:
            from PIL import Image

            logo = Image.open(path).convert("RGBA")
            resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            max_width = max(1, int(width * 0.42))
            max_height = max(1, int(height * 0.74))
            scale = min(max_width / logo.width, max_height / logo.height)
            target_width = max(1, int(logo.width * scale))
            target_height = max(1, int(logo.height * scale))
            logo = logo.resize((target_width, target_height), resampling)
            alpha = logo.getchannel("A").point(lambda value: int(value * 0.11))
            logo.putalpha(alpha)
            px = x + width - target_width - 8
            py = y + height - target_height - 6
            canvas.paste(logo, (px, py), logo)
        except Exception:
            logger.exception(f"企划 logo 底纹渲染失败：{logo_path}")

    def _draw_pillow_portrait_panel(self, draw: Any, canvas: Any, path: Path, x: int, y: int, width: int, height: int, brand_rgb: tuple[int, int, int]) -> None:
        from PIL import Image

        panel = Image.new("RGBA", (width, height), (*brand_rgb, 255))

        source = Image.open(path).convert("RGBA")
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        scale = min((width * 0.92) / source.width, (height * 0.96) / source.height)
        resized = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), resampling)
        px = (width - resized.width) // 2
        py = height - resized.height
        panel.alpha_composite(resized, (px, py))
        canvas.paste(panel.convert("RGB"), (x, y))

    def _pil_cover_image(self, path: Path, width: int, height: int) -> Any:
        from PIL import Image

        source = Image.open(path).convert("RGB")
        scale = max(width / source.width, height / source.height)
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        resized = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), resampling)
        left = max(0, (resized.width - width) // 2)
        top = 0
        return resized.crop((left, top, left + width, top + height))

    def _pil_font(self, image_font: Any, size: int, bold: bool = False) -> Any:
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf" if bold else r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\YuGothB.ttc" if bold else r"C:\Windows\Fonts\YuGothR.ttc",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                try:
                    return image_font.truetype(candidate, size)
                except Exception:
                    continue
        return image_font.load_default()

    def _pil_wrap_text(self, draw: Any, text: str, font: Any, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for char in text:
            test = current + char
            bbox = draw.textbbox((0, 0), test, font=font)
            if current and bbox[2] - bbox[0] > max_width:
                lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
        return lines

    def _hex_rgb(self, value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        value = value.strip().lstrip("#")
        if len(value) == 6:
            try:
                return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                return fallback
        return fallback

    def _invalid_render_excerpt(self, path: Path) -> str:
        try:
            raw = path.read_bytes()[:500]
            if raw.lstrip().lower().startswith((b"<!doctype", b"<html")):
                text = raw.decode("utf-8", errors="replace")
                text = re.sub(r"\s+", " ", text).strip()
                return f", html_excerpt={text[:220]}"
        except Exception:
            return ""
        return ""

    def _format_lines(self, category: str, values: list[str]) -> list[str]:
        label = CATEGORY_LABELS[category]
        return [f"{label}：{value}" for value in values if value]

    def _card_render_mode(self) -> str:
        mode = str(self.config.get("card_render_mode", "pillow") or "").strip().lower()
        aliases = {
            "local": "pillow",
            "pil": "pillow",
            "t2i": "auto",
            "html_render": "auto",
            "remote": "auto",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"pillow", "auto", "html"}:
            logger.warning(f"未知 card_render_mode={mode}，改用 pillow。")
            return "pillow"
        return mode

    def _card_item(self, character: str) -> dict[str, str]:
        brand = self._character_brand(character)
        portrait_path = self._character_portrait_path(character)
        image_path = self._character_image_path(character)
        asset_mode = self._card_asset_mode_for_brand(brand)
        selected_path: Path | None = None
        asset_kind = "placeholder"
        if asset_mode in {"auto", "portrait"} and portrait_path:
            selected_path = portrait_path
            asset_kind = "portrait"
        elif asset_mode in {"auto", "image"} and image_path:
            selected_path = image_path
            asset_kind = "image"
        return {
            "name": character,
            "brand": brand,
            "label": BRAND_LABELS.get(brand, BRAND_LABELS["OTHER"]),
            "color": self._character_color(character, brand),
            "project_color": BRAND_COLORS.get(brand, BRAND_COLORS["OTHER"]),
            "logo_path": str(self._brand_logo_path(brand, "png") or ""),
            "logo_image": self._image_data_uri(self._brand_logo_path(brand, "svg")),
            "path": str(selected_path) if selected_path else "",
            "image": self._image_data_uri(selected_path) if selected_path else "",
            "asset_kind": asset_kind,
        }

    def _card_asset_mode(self) -> str:
        return self._normalize_card_asset_mode(str(self.config.get("card_asset_mode", "image") or ""), "card_asset_mode")

    def _card_asset_mode_for_brand(self, brand: str) -> str:
        return self._card_asset_mode_overrides().get(brand, self._card_asset_mode())

    def _card_asset_mode_overrides_text(self) -> str:
        overrides = self._card_asset_mode_overrides()
        if not overrides:
            return "未配置"
        return ", ".join(f"{brand}={mode}" for brand, mode in sorted(overrides.items()))

    def _card_asset_mode_overrides(self) -> dict[str, str]:
        text = str(self.config.get("card_asset_mode_by_brand", "") or "").strip()
        if not text:
            return {}
        result: dict[str, str] = {}
        for raw_item in re.split(r"[\n;,]+", text):
            item = raw_item.strip()
            if not item:
                continue
            if "=" in item:
                raw_brand, raw_mode = item.split("=", 1)
            elif ":" in item:
                raw_brand, raw_mode = item.split(":", 1)
            else:
                logger.warning(f"忽略无效 card_asset_mode_by_brand 条目：{item}")
                continue
            brand = BRAND_ALIASES.get(self._normalize_brand_key(raw_brand), raw_brand.strip().upper())
            if brand not in BRAND_LABELS:
                logger.warning(f"忽略未知企划 card_asset_mode_by_brand={raw_brand}")
                continue
            mode = self._normalize_card_asset_mode(raw_mode, f"card_asset_mode_by_brand.{brand}")
            result[brand] = mode
        return result

    def _normalize_card_asset_mode(self, mode: str, config_key: str) -> str:
        mode = mode.strip().lower()
        aliases = {
            "": "image",
            "auto": "image",
            "transparent": "portrait",
            "transparent_portrait": "portrait",
            "portrait_asset": "portrait",
            "portraits": "portrait",
            "old": "image",
            "legacy": "image",
            "moegirl": "image",
            "card": "image",
        }
        mode = aliases.get(mode, mode)
        if mode not in {"portrait", "image"}:
            logger.warning(f"未知 {config_key}={mode}，改用 image。")
            return "image"
        return mode

    def _image_data_uri(self, path: Path | None) -> str:
        if not path:
            return ""
        try:
            mime_type = guess_type(str(path))[0] or "image/png"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime_type};base64,{data}"
        except Exception:
            logger.exception(f"读取角色图片失败：{path}")
            return ""

    def _character_image_path(self, character: str) -> Path | None:
        filename = self._character_asset_filename(character)
        if not filename:
            return None
        path = Path(filename)
        if not path.is_absolute():
            path = self.assets_dir / filename
        return path if path.exists() else None

    def _character_portrait_path(self, character: str) -> Path | None:
        filename = self._character_portrait_filename(character)
        if not filename:
            return None
        path = Path(filename)
        if not path.is_absolute():
            path = self.portraits_dir / filename
        return path if path.exists() else None

    def _character_color(self, character: str, brand: str) -> str:
        candidates = [
            character,
            CHARACTER_NAME_ALIASES.get(character, character),
            CHARACTER_REVERSE_ALIASES.get(character, character),
            self._base_character_name(character),
            CHARACTER_NAME_ALIASES.get(self._base_character_name(character), self._base_character_name(character)),
        ]
        for candidate in dict.fromkeys(candidates):
            color = CHARACTER_COLORS.get(candidate)
            if color:
                return color
        return BRAND_COLORS.get(brand, BRAND_COLORS["OTHER"])

    def _brand_logo_path(self, brand: str, suffix: str = "svg") -> Path | None:
        suffix = suffix.strip().lstrip(".") or "svg"
        path = self.plugin_dir / "assets" / "brand_marks" / f"{brand}.{suffix}"
        return path if path.exists() else None

    def _character_brand(self, character: str) -> str:
        character = CHARACTER_NAME_ALIASES.get(character, character)
        base_character = self._base_character_name(character)
        if character in CHARACTER_BRAND_OVERRIDES:
            return CHARACTER_BRAND_OVERRIDES[character]
        if base_character in CHARACTER_BRAND_OVERRIDES:
            return CHARACTER_BRAND_OVERRIDES[base_character]
        filename = self._character_asset_filename(character).lower()
        if "/" in filename or "\\" in filename:
            prefix = re.split(r"[/\\]", filename, maxsplit=1)[0].lower()
            return BRAND_ALIASES.get(self._normalize_brand_key(prefix), "OTHER")
        return "OTHER"

    def _character_asset_filename(self, character: str) -> str:
        candidates = [
            character,
            CHARACTER_NAME_ALIASES.get(character, character),
            CHARACTER_REVERSE_ALIASES.get(character, character),
            self._base_character_name(character),
            CHARACTER_NAME_ALIASES.get(self._base_character_name(character), self._base_character_name(character)),
        ]
        for candidate in dict.fromkeys(candidates):
            filename = CHARACTER_IMAGE_ASSETS.get(candidate)
            if filename:
                return filename
        return ""

    def _character_portrait_filename(self, character: str) -> str:
        candidates = [
            character,
            CHARACTER_NAME_ALIASES.get(character, character),
            CHARACTER_REVERSE_ALIASES.get(character, character),
            self._base_character_name(character),
            CHARACTER_NAME_ALIASES.get(self._base_character_name(character), self._base_character_name(character)),
        ]
        for candidate in dict.fromkeys(candidates):
            filename = CHARACTER_PORTRAIT_ASSETS.get(candidate)
            if filename:
                return filename
        return ""

    def _base_character_name(self, character: str) -> str:
        return re.sub(r"\s*[（(][^（）()]+[）)]\s*", "", character).strip()

    def _normalize_brand_key(self, value: str) -> str:
        return re.sub(r"[^0-9a-zα]+", "_", value.lower()).strip("_")

    def _visible_characters(self, entry: dict[str, list[str]]) -> list[str]:
        characters = self._split_people(entry.get("characters", []))
        if self._cfg_bool("include_kr_characters", False):
            return characters
        return [character for character in characters if not self._is_kr_character(character)]

    def _is_kr_character(self, character: str) -> bool:
        base_character = self._base_character_name(character)
        normalized = CHARACTER_NAME_ALIASES.get(character, character)
        normalized_base = CHARACTER_NAME_ALIASES.get(base_character, base_character)
        return (
            character in KR_CHARACTER_NAMES
            or normalized in KR_CHARACTER_NAMES
            or base_character in KR_CHARACTER_NAMES
            or normalized_base in KR_CHARACTER_NAMES
            or self._character_brand(normalized) == "KR"
        )

    def _split_people(self, values: list[str]) -> list[str]:
        people: list[str] = []
        for value in values:
            for item in re.split(r"[、,，]", value):
                item = item.strip()
                item = CHARACTER_NAME_ALIASES.get(item, item)
                if item and item not in people:
                    people.append(item)
        return people

    def _join_names(self, names: list[str]) -> str:
        return "、".join(names)

    def _card_layout(self, item_count: int) -> dict[str, int]:
        columns = max(1, min(item_count, 3))
        item_width = 300 if columns == 1 else 260 if columns == 2 else 214
        grid_gap = 12
        card_padding = 30
        card_width = 760
        render_width = 760
        portrait_height = 360 if columns == 1 else 320 if columns == 2 else 300
        item_min_height = portrait_height + 86
        viewport_height = 1280
        return {
            "columns": columns,
            "item_width": item_width,
            "grid_gap": grid_gap,
            "card_padding": card_padding,
            "card_width": card_width,
            "render_width": render_width,
            "portrait_height": portrait_height,
            "item_min_height": item_min_height,
            "viewport_height": viewport_height,
        }

    def _birthday_card_html(
        self,
        month: int,
        day: int,
        items: list[dict[str, str]],
        seiyuu: list[str],
        related_people: list[str],
        events: list[str],
        layout: dict[str, int],
    ) -> str:
        columns = layout["columns"]
        item_width = layout["item_width"]
        grid_gap = layout["grid_gap"]
        card_padding = layout["card_padding"]
        card_width = layout["card_width"]
        portrait_height = layout["portrait_height"]
        item_min_height = layout["item_min_height"]
        viewport_height = layout["viewport_height"]
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
html {{
  margin: 0;
  width: {card_width}px;
  min-height: {viewport_height}px;
  overflow: hidden;
  background: #f5f1ea;
}}
body {{
  margin: 0;
  width: {card_width}px;
  min-height: {viewport_height}px;
  overflow: hidden;
  font-family: "Noto Sans CJK SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  color: #20242c;
  background: #f5f1ea;
}}
.card {{
  position: relative;
  overflow: hidden;
  width: {card_width}px;
  min-height: {viewport_height}px;
  padding: {card_padding}px;
  background: #f7f3ec;
}}
.card::before {{
  content: "";
  position: absolute;
  inset: -42px;
  background:
    conic-gradient(from -90deg at 50% 50%,
      rgba(240,90,126,.40) 0deg 60deg,
      rgba(47,127,211,.36) 60deg 120deg,
      rgba(242,184,75,.38) 120deg 180deg,
      rgba(26,169,130,.36) 180deg 240deg,
      rgba(92,200,242,.36) 240deg 300deg,
      rgba(240,138,51,.38) 300deg 360deg);
  filter: blur(24px);
  transform: scale(1.04);
}}
.card::after {{
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(255,255,255,.39);
}}
.header,
.rule,
.grid,
.meta,
.footer {{
  position: relative;
  z-index: 1;
}}
.header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  border-bottom: 3px solid rgba(32,36,44,.14);
  padding-bottom: 20px;
}}
.title {{
  font-size: 36px;
  line-height: .95;
  font-weight: 800;
}}
.subtitle {{
  margin-top: 18px;
  font-size: 13px;
  color: #5b6472;
}}
.date {{
  text-align: right;
  font-size: 32px;
  font-weight: 800;
  color: #f05a7e;
}}
.date span {{
  display: block;
  font-size: 13px;
  color: #5b6472;
  font-weight: 700;
}}
.grid {{
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: stretch;
  gap: {grid_gap}px;
  margin-top: 20px;
}}
.idol {{
  position: relative;
  width: {item_width}px;
  min-height: {item_min_height}px;
  background: rgba(255,255,255,.58);
  border: 1px solid rgba(255,255,255,.70);
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  box-shadow: 0 18px 44px rgba(32,36,44,.12);
  backdrop-filter: blur(18px) saturate(1.18);
  -webkit-backdrop-filter: blur(18px) saturate(1.18);
}}
.portrait {{
  height: {portrait_height}px;
  display: flex;
  align-items: end;
  justify-content: center;
  background:
    linear-gradient(135deg, rgba(255,255,255,.28), rgba(255,255,255,0)),
    var(--brand);
}}
.portrait img {{
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center top;
}}
.portrait.is-portrait {{
  background: var(--brand);
}}
.portrait.is-portrait img {{
  width: 92%;
  height: 96%;
  object-fit: contain;
  object-position: center bottom;
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
  position: relative;
  z-index: 1;
  padding: 21px 12px 5px;
  font-size: 20px;
  font-weight: 800;
  line-height: 1.15;
}}
.idol-name::before {{
  content: "";
  position: absolute;
  left: 12px;
  right: 12px;
  top: 10px;
  height: 5px;
  border-radius: 999px;
  background: var(--brand);
}}
.brand {{
  position: relative;
  z-index: 1;
  padding: 0 12px 12px;
  color: #5b6472;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .02em;
}}
.brand-logo {{
  position: absolute;
  z-index: 0;
  right: 8px;
  bottom: 6px;
  width: 42%;
  height: 74%;
  object-fit: contain;
  object-position: right center;
  opacity: .11;
  pointer-events: none;
  user-select: none;
}}
.meta {{
  margin-top: 14px;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}}
.meta-block {{
  position: relative;
  width: {item_width}px;
  background: rgba(255,255,255,.56);
  border: 1px solid rgba(255,255,255,.72);
  padding: 12px 14px;
  border-radius: 6px;
  backdrop-filter: blur(16px) saturate(1.12);
  -webkit-backdrop-filter: blur(16px) saturate(1.12);
  box-shadow: 0 12px 30px rgba(32,36,44,.09);
}}
.meta-title {{
  font-size: 13px;
  font-weight: 800;
  color: #5b6472;
  margin-bottom: 8px;
}}
.meta-text {{
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
}}
.empty {{
  grid-column: 1 / -1;
  min-height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255,255,255,.68);
  border: 2px solid rgba(32,36,44,.11);
  border-radius: 8px;
  font-size: 22px;
  font-weight: 800;
  color: #5b6472;
}}
.footer {{
  margin-top: 18px;
  font-size: 10px;
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
    <section class="footer">Character images are sourced from local assets prepared by the bot owner, Moegirlpedia, official sites, and fan DBs. Thanks to Moegirlpedia and the asset data providers. THE IDOLM@STER rights belong to their respective owners.</section>
  </main>
</body>
</html>"""

    def _birthday_card_item_html(self, item: dict[str, str]) -> str:
        name = html.escape(item["name"])
        label = html.escape(item["label"])
        color = html.escape(item["color"])
        project_color = html.escape(item.get("project_color", color))
        logo_image = item.get("logo_image", "")
        portrait_class = "portrait is-portrait" if item.get("asset_kind") == "portrait" else "portrait"
        if item["image"]:
            portrait = f'<img src="{html.escape(item["image"], quote=True)}" alt="{name}">'
        else:
            portrait = f'<div class="placeholder">{html.escape(item["name"][:1])}</div>'
        logo_html = f'<img class="brand-logo" src="{html.escape(logo_image, quote=True)}" alt="">' if logo_image else ""
        return f"""<article class="idol" style="--brand:{color};--project:{project_color}">
  <div class="{portrait_class}">{portrait}</div>
  <div class="idol-name">{name}</div>
  <div class="brand">{label}</div>
  {logo_html}
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
            data = self._clean_birthdays_data(cache["data"])
            if data != cache["data"]:
                await self._save_cache(data)
            return data
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
        data = self._clean_birthdays_data(data)
        await self.put_kv_data("birthday_cache", {"updated_at": int(time.time()), "data": data})

    async def _load_delivery_state(self):
        if self._delivery_state_loaded:
            return
        state = await self.get_kv_data("delivery_state", None)
        if isinstance(state, dict):
            self._delivery_state_exists = True
            self._last_sent_date = str(state.get("last_sent_date", "") or "")
            self._suppressed_first_start_date = str(state.get("suppressed_first_start_date", "") or "")
            self._pending_retry_date = str(state.get("pending_retry_date", "") or "")
            pending = state.get("pending_retry_umos", [])
            if isinstance(pending, list):
                self._pending_retry_umos = {
                    normalized
                    for item in pending
                    if (normalized := self._normalize_umo(str(item).strip()))
                }
        self._delivery_state_loaded = True

    async def _save_delivery_state(self):
        self._delivery_state_loaded = True
        self._delivery_state_exists = True
        await self.put_kv_data(
            "delivery_state",
            {
                "last_sent_date": self._last_sent_date,
                "suppressed_first_start_date": self._suppressed_first_start_date,
                "pending_retry_date": self._pending_retry_date,
                "pending_retry_umos": sorted(self._pending_retry_umos),
            },
        )

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
        return self._clean_birthdays_data(parser.data)

    def _clean_birthdays_data(self, data: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
        if self._cfg_bool("include_kr_characters", False):
            return data
        cleaned: dict[str, dict[str, list[str]]] = {}
        removed_count = 0
        for date_key, entry in data.items():
            next_entry = {
                "characters": [],
                "seiyuu": list(entry.get("seiyuu", [])),
                "related_people": list(entry.get("related_people", [])),
                "events": list(entry.get("events", [])),
            }
            for character in self._split_people(entry.get("characters", [])):
                if self._is_kr_character(character):
                    removed_count += 1
                    continue
                if character not in next_entry["characters"]:
                    next_entry["characters"].append(character)
            cleaned[date_key] = next_entry
        if removed_count:
            logger.info(f"生日数据源清洗：已移除 KR 角色 {removed_count} 条。")
        return cleaned

    def _scheduler_status_text(self) -> str:
        now = self._now()
        send_time = str(self.config.get("send_time", "09:00"))
        send_minutes = self._parse_send_time_minutes(send_time)
        due_text = "unknown" if send_minutes is None else str(self._is_send_time_due(now, send_minutes))
        schedule_status = self._send_schedule_status(now, send_minutes)
        task_alive = bool(self._task and not self._task.done())
        white_umos = self._configured_white_umos()
        raw_white_umos = [str(item).strip() for item in self.config.get("white_umos", []) if str(item).strip()]
        normalized_warning_count = sum(1 for item in raw_white_umos if item and item != self._normalize_umo(item))
        lines = [
            "偶像大师生日提醒状态：",
            f"enabled: {self._cfg_bool('enabled', True)}",
            f"scheduler_alive: {task_alive}",
            f"scheduler_started_at: {self._scheduler_started_at or '未记录'}",
            f"timezone: {self._timezone_name()}",
            f"now: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"send_time: {send_time}",
            f"catch_up_send: {self._cfg_bool('catch_up_send', True)}",
            f"catch_up_on_first_start: {self._cfg_bool('catch_up_on_first_start', False)}",
            f"send_time_due_today: {due_text}",
            f"scheduled_send_at_today: {schedule_status['scheduled_send_at_today']}",
            f"send_pending_today: {schedule_status['send_pending_today']}",
            f"pending_retry_umos: {len(self._pending_retry_umos) if self._pending_retry_date == now.strftime('%Y-%m-%d') else 0}",
            f"next_send_at: {schedule_status['next_send_at']}",
            f"next_send_date: {schedule_status['next_send_date']}",
            f"next_regular_send_at: {schedule_status['next_regular_send_at']}",
            f"last_sent_date: {self._last_sent_date or '未发送'}",
            f"suppressed_first_start_date: {self._suppressed_first_start_date or '未记录'}",
            f"white_umos: {len(white_umos)}",
            f"white_umos_normalized: {normalized_warning_count}",
            f"card_render_mode: {self._card_render_mode()}",
            f"card_asset_mode: {self._card_asset_mode()}",
            f"card_asset_mode_by_brand: {self._card_asset_mode_overrides_text()}",
            f"character_assets_dir: {self.assets_dir}",
            f"character_portraits_dir: {self.portraits_dir}",
            f"birthday_send_mode: {self._birthday_send_mode()}",
        ]
        if self._task and self._task.done():
            with contextlib.suppress(asyncio.CancelledError):
                exc = self._task.exception()
                if exc:
                    lines.append(f"scheduler_error: {type(exc).__name__}: {exc}")
            if self._task.cancelled():
                lines.append("scheduler_error: task cancelled")
        return "\n".join(lines)

    def _configured_white_umos(self) -> list[str]:
        result: list[str] = []
        for raw_item in self.config.get("white_umos", []):
            item = str(raw_item).strip()
            if not item:
                continue
            normalized = self._normalize_umo(item)
            if normalized not in result:
                result.append(normalized)
        return result

    def _normalize_umo(self, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if value.count(":") >= 2:
            return value
        if re.fullmatch(r"\d+", value):
            normalized = f"aiocqhttp:GroupMessage:{value}"
            logger.warning(f"white_umos 使用了裸群号，已按 OneBot 群聊兼容为 UMO：{value} -> {normalized}")
            return normalized
        logger.warning(f"white_umos 条目不是合法 UMO，可能无法主动发送：{value}")
        return value

    def _parse_send_time_minutes(self, text: str) -> int | None:
        match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour * 60 + minute

    def _is_send_time_due(self, now: datetime, send_minutes: int) -> bool:
        now_minutes = now.hour * 60 + now.minute
        if self._cfg_bool("catch_up_send", True):
            return now_minutes >= send_minutes
        return now_minutes == send_minutes

    def _send_schedule_status(self, now: datetime, send_minutes: int | None) -> dict[str, str]:
        if send_minutes is None:
            return {
                "scheduled_send_at_today": "unknown",
                "send_pending_today": "unknown",
                "next_send_at": "unknown",
                "next_send_date": "unknown",
                "next_regular_send_at": "unknown",
            }
        scheduled_today = now.replace(
            hour=send_minutes // 60,
            minute=send_minutes % 60,
            second=0,
            microsecond=0,
        )
        today_key = now.strftime("%Y-%m-%d")
        tomorrow_scheduled = scheduled_today + timedelta(days=1)
        scheduled_today_text = scheduled_today.strftime("%Y-%m-%d %H:%M:%S %Z")
        tomorrow_text = tomorrow_scheduled.strftime("%Y-%m-%d %H:%M:%S %Z")
        if self._suppressed_first_start_date == today_key and self._last_sent_date != today_key:
            return {
                "scheduled_send_at_today": scheduled_today_text,
                "send_pending_today": "False",
                "next_send_at": tomorrow_text,
                "next_send_date": tomorrow_scheduled.strftime("%Y-%m-%d"),
                "next_regular_send_at": tomorrow_text,
            }
        if self._last_sent_date != today_key:
            if self._is_send_time_due(now, send_minutes):
                if self._pending_retry_date == today_key and self._pending_retry_umos:
                    return {
                        "scheduled_send_at_today": scheduled_today_text,
                        "send_pending_today": "True",
                        "next_send_at": f"ASAP retry for {today_key}",
                        "next_send_date": today_key,
                        "next_regular_send_at": tomorrow_text,
                    }
                return {
                    "scheduled_send_at_today": scheduled_today_text,
                    "send_pending_today": "True",
                    "next_send_at": f"ASAP catch-up for {today_key}",
                    "next_send_date": today_key,
                    "next_regular_send_at": tomorrow_text,
                }
            if now < scheduled_today:
                return {
                    "scheduled_send_at_today": scheduled_today_text,
                    "send_pending_today": "False",
                    "next_send_at": scheduled_today_text,
                    "next_send_date": today_key,
                    "next_regular_send_at": scheduled_today_text,
                }
        return {
            "scheduled_send_at_today": scheduled_today_text,
            "send_pending_today": "False",
            "next_send_at": tomorrow_text,
            "next_send_date": tomorrow_scheduled.strftime("%Y-%m-%d"),
            "next_regular_send_at": tomorrow_text,
        }

    def _timezone_name(self) -> str:
        return str(self.config.get("timezone", "Asia/Tokyo") or "Asia/Tokyo")

    def _now(self) -> datetime:
        timezone_name = self._timezone_name()
        try:
            return datetime.now(ZoneInfo(timezone_name))
        except Exception:
            logger.warning(f"无效时区 {timezone_name}，已回退到 Asia/Tokyo。")
            return datetime.now(ZoneInfo("Asia/Tokyo"))

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return bool(self.config.get(key, default))

    def _parse_date_text(self, text: str) -> tuple[int, int] | None:
        compact_match = re.fullmatch(r"\s*(\d{2})(\d{2})\s*", text)
        if compact_match:
            month, day = int(compact_match.group(1)), int(compact_match.group(2))
            if month < 1 or month > 12 or day < 1 or day > 31:
                return None
            return month, day
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
