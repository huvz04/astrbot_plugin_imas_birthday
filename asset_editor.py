"""Local character overrides and the authenticated AstrBot Plugin Page API."""
from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import math
import re
import tempfile
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps

MAX_UPLOAD = 10 * 1024 * 1024
KINDS = ("birthday", "tantou")


def image_bytes(image):
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def image_uri(image):
    return "data:image/png;base64," + base64.b64encode(image_bytes(image)).decode("ascii")


def decode_image(data):
    if not data or len(data) > MAX_UPLOAD:
        raise ValueError("图片不能超过 10 MB。")
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("请使用 PNG、JPEG、WebP 或 GIF 图片。")
            if source.width * source.height > 20_000_000 or max(source.size) > 8192:
                raise ValueError("图片尺寸过大，请缩小到 2000 万像素以内。")
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            return image
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取这张图片。") from exc


def crop_image(image, size, crop):
    width, height = image.size
    ratio = size[0] / size[1]
    crop_width = min(width, height * ratio) / crop["zoom"]
    crop_height = crop_width / ratio
    left = (width - crop_width) * crop["x"]
    top = (height - crop_height) * crop["y"]
    return image.resize(size, Image.Resampling.LANCZOS, box=(left, top, left + crop_width, top + crop_height))


class AssetEditor:
    def __init__(self, plugin, root, brands, base_profiles):
        self.plugin, self.root = plugin, Path(root)
        self.brands, self.base_profiles = brands, base_profiles
        self.file = self.root / "characters.json"
        self.lock = asyncio.Lock()
        self.records = {}
        if self.file.is_file():
            payload = json.loads(self.file.read_text(encoding="utf-8"))
            if payload.get("schema") != 1 or not isinstance(payload.get("records"), dict):
                raise ValueError("角色编辑数据格式无效，请检查 characters.json。")
            self.records = payload["records"]
            old = self.records.get("百万ChiefP")
            if isinstance(old, dict):
                current = self.records.get("赤羽根P", {})
                merged = {**old, **current, "images": {**old.get("images", {}), **current.get("images", {})}}
                if "name_jp" not in current and merged.get("name_jp") == "チーフプロデューサー":
                    merged.pop("name_jp")
                self.records = {name: record for name, record in self.records.items() if name != "百万ChiefP"}
                self.records["赤羽根P"] = merged
                # Keep both original files and the pre-merge JSON recoverable.
                backup = self.file.with_name("characters.before-chief-merge.json")
                if not backup.exists():
                    with backup.open("x", encoding="utf-8") as stream:
                        json.dump(payload, stream, ensure_ascii=False, indent=2)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root, suffix=".json", delete=False) as stream:
                    json.dump({**payload, "records": self.records}, stream, ensure_ascii=False, indent=2)
                    temporary = Path(stream.name)
                try:
                    temporary.replace(self.file)
                finally:
                    temporary.unlink(missing_ok=True)

    def register(self, context):
        # Older AstrBot versions can still use the bot commands and saved overrides.
        try:
            from astrbot.api.web import request, json_response, error_response
        except ImportError:
            return False

        def handler(action):
            async def view():
                try:
                    if action == "list":
                        result = await self.list_records()
                    elif action == "detail":
                        result = await self.detail(request.query.get("name", ""))
                    elif action == "upload":
                        upload = (await request.files()).get("file")
                        if upload is None:
                            raise ValueError("请选择图片。")
                        data = await upload.read(MAX_UPLOAD + 1)
                        result = await asyncio.to_thread(self.import_image, data)
                    elif action == "save":
                        result = await self.save(await request.json(default={}))
                    else:
                        result = await self.preview(request.query.get("name", ""), request.query.get("columns", "1"))
                    return json_response(result)
                except (ValueError, TypeError) as exc:
                    return error_response(str(exc), status_code=400)
            return view

        for action in ("list", "detail", "upload", "save", "preview"):
            context.register_web_api(
                f"/astrbot_plugin_imas_birthday/editor/{action}", handler(action),
                ["POST" if action in {"upload", "save"} else "GET"], "角色图片与资料管理",
            )
        return True

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("角色名字需为 1—80 个字符。")
        name = name.strip()
        if name.lower().startswith("qq:") or any(ord(c) < 32 for c in name) or any(c in name for c in "、,，;；"):
            raise ValueError("角色名字不能使用群友内部标识、控制字符或名字分隔符。")
        return "赤羽根P" if name == "百万ChiefP" else name

    def source_path(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ValueError("图片标识无效，请重新上传。")
        path = self.root / "originals" / (token + ".png")
        if not path.is_file():
            raise ValueError("图片已不存在，请重新上传。")
        return path

    def import_image(self, data):
        image = decode_image(data)
        encoded = image_bytes(image)
        token = hashlib.sha256(encoded).hexdigest()
        directory = self.root / "originals"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (token + ".png")
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=directory, suffix=".png", delete=False) as temp:
                temp.write(encoded)
                temporary = Path(temp.name)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return {"source": token, "image": image_uri(image)}

    def render_image(self, name, kind, size=None):
        settings = self.records.get(name, {}).get("images", {}).get(kind)
        if not settings:
            return None
        layout = self.plugin._card_layout(1)
        size = size or ((layout["item_width"], layout["portrait_height"]) if kind == "birthday" else (600, 540))
        key = hashlib.sha256(json.dumps([settings, size], sort_keys=True).encode()).hexdigest()
        path = self.root / "renders" / (key + ".png")
        if not path.is_file():
            with Image.open(self.source_path(settings["source"])) as source:
                rendered = crop_image(source.convert("RGBA"), size, settings)
            if kind == "birthday":
                background = Image.new("RGBA", size, "white")
                background.alpha_composite(rendered)
                rendered = background
            path.parent.mkdir(parents=True, exist_ok=True)
            # Concurrent renders may request the same immutable image.
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as temp:
                temp.write(image_bytes(rendered))
                temporary = Path(temp.name)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def current_image(self, name, kind):
        if kind == "birthday":
            return self.plugin._character_image_path(name) or self.plugin._character_portrait_path(name)
        return self.plugin._tantou_icon_path(name) or self.plugin._character_image_path(name) or self.plugin._character_portrait_path(name)

    async def list_records(self):
        names = await self.plugin._tantou_records()
        cache = await self.plugin.get_kv_data("birthday_cache", {})
        dates = {row["name"]: row["date_key"] for row in self.plugin._character_birthday_records(cache.get("data", {}))}
        rows = []
        for name in names:
            record = self.records.get(name, {})
            profile = self.plugin._lookup_character_profile(name)
            birthday = record.get("birthday", dates.get(name) or profile.get("birthday", ""))
            rows.append({"name": name, "name_jp": self.plugin._tantou_display_name(name),
                         "display_name": self.plugin._tantou_display_name(name) if profile.get("display_name") else name,
                         "brand": self.plugin._character_brand(name), "birthday": birthday,
                         "custom": bool(record), "aliases": self.plugin._tantou_aliases(name)})
        return {"characters": rows, "brands": self.brands, "storage": str(self.root),
                "birthday_layouts": {str(n): self.plugin._card_layout(n) for n in (1, 2, 3)},
                "birthday_source": self.plugin.config.get("source_url") or "萌娘百科 · 偶像大师系列/相关人士生日信息",
                "idol_source": "偶像大师官网公共偶像目录"}

    async def detail(self, name):
        name = self.validate_name(name)
        if name not in await self.plugin._tantou_records():
            raise ValueError("没有找到这个角色，请先新增。")
        record = copy.deepcopy(self.records.get(name, {}))
        cache = await self.plugin.get_kv_data("birthday_cache", {})
        dates = {row["name"]: row["date_key"] for row in self.plugin._character_birthday_records(cache.get("data", {}))}
        images = {}
        for kind in KINDS:
            settings = record.get("images", {}).get(kind)
            path = self.source_path(settings["source"]) if settings else self.current_image(name, kind)
            uri = ""
            if path and path.is_file():
                with Image.open(path) as image:
                    image = ImageOps.exif_transpose(image).convert("RGBA")
                    image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                    uri = image_uri(image)
            images[kind] = {"image": uri, "source": settings.get("source", "") if settings else "",
                            "x": settings.get("x", .5) if settings else .5,
                            "y": settings.get("y", .5) if settings else .5,
                            "zoom": settings.get("zoom", 1) if settings else 1, "custom": bool(settings)}
        return {"name": name, "record": record, "images": images,
                "display_name": self.plugin._tantou_display_name(name) if self.base_profiles.get(name, {}).get("display_name") else name,
                "name_jp": self.plugin._tantou_display_name(name), "brand": self.plugin._character_brand(name),
                "base_birthday": dates.get(name) or self.base_profiles.get(name, {}).get("birthday", ""),
                "revision": record.get("revision", "")}

    async def save(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("提交内容无效。")
        name = self.validate_name(payload.get("name"))
        if self.plugin._base_character_name(name) != name:
            raise ValueError("登记名字不要带括号附注，请把说明填到备注里。")
        async with self.lock:
            previous = self.records.get(name, {})
            if payload.get("revision", "") != previous.get("revision", ""):
                raise ValueError("这个角色已被其他页面修改，请重新选择角色后再保存。")
            known = await self.plugin._tantou_records()
            if name not in known:
                aliases = self.plugin._tantou_alias_index(known)
                if self.plugin._exact_name_key(name) in aliases:
                    raise ValueError("这个名字已对应现有角色，请搜索并编辑原角色。")
            record = {}
            for field, limit in (("name_jp", 80), ("source_note", 500)):
                value = payload.get(field, "")
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError("名字或来源说明过长。")
                if value.strip():
                    record[field] = value.strip()
            if name not in known and not record.get("name_jp"):
                raise ValueError("新增角色请填写日文显示名。")
            brand = payload.get("brand", "")
            if brand:
                if brand not in self.brands:
                    raise ValueError("请选择有效企划。")
                record["brand"] = brand
            aliases = payload.get("aliases", [])
            if not isinstance(aliases, list) or len(aliases) > 30:
                raise ValueError("别名最多 30 个。")
            record["aliases"] = list(dict.fromkeys(self.validate_name(alias) for alias in aliases))
            birthday = payload.get("birthday")
            if birthday is not None:
                if not isinstance(birthday, str) or (birthday and not re.fullmatch(r"\d{2}-\d{2}", birthday)):
                    raise ValueError("生日格式为 MM-DD，例如 04-16。")
                if birthday:
                    try:
                        datetime.strptime("2000-" + birthday, "%Y-%m-%d")
                    except ValueError as exc:
                        raise ValueError("生日日期无效。") from exc
                record["birthday"] = birthday
            images = copy.deepcopy(previous.get("images", {}))
            changes = payload.get("images", {})
            if not isinstance(changes, dict) or set(changes) - set(KINDS):
                raise ValueError("图片类型无效。")
            for kind, settings in changes.items():
                if settings is None:
                    images.pop(kind, None)
                    continue
                if not isinstance(settings, dict):
                    raise ValueError("裁切参数无效。")
                crop = {}
                for key, low, high in (("x", 0, 1), ("y", 0, 1), ("zoom", 1, 4)):
                    value = settings.get(key)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                        raise ValueError("图片位置或缩放超出范围。")
                    crop[key] = value
                source = settings.get("source")
                if not source:
                    path = self.current_image(name, kind)
                    if not path:
                        raise ValueError("请先上传图片。")
                    source = self.import_image(path.read_bytes())["source"]
                self.source_path(source)
                images[kind] = {"source": source, **crop}
            record["images"] = images
            record["revision"] = uuid.uuid4().hex
            records = {**self.records, name: record}
            self.root.mkdir(parents=True, exist_ok=True)
            content = json.dumps({"schema": 1, "records": records}, ensure_ascii=False, indent=2)
            temporary = self.file.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.file)
            self.records = records
        return {"saved": True, "name": name, "revision": record["revision"]}

    def apply_birthdays(self, data):
        overrides = {name: row["birthday"] for name, row in self.records.items() if "birthday" in row}
        if not overrides:
            return data
        result = copy.deepcopy(data)
        for entry in result.values():
            entry["characters"] = [name for name in self.plugin._split_people(entry.get("characters", []))
                                   if self.plugin._base_character_name(name) not in overrides]
        for name, birthday in overrides.items():
            if birthday:
                entry = result.setdefault(birthday, {key: [] for key in ("characters", "seiyuu", "related_people", "events")})
                entry.setdefault("characters", []).append(name)
        return result

    async def preview(self, name, columns=1):
        name = self.validate_name(name)
        if str(columns) not in {"1", "2", "3"}:
            raise ValueError("预览列数需为 1、2 或 3。")
        columns = int(columns)
        if name not in await self.plugin._tantou_records():
            raise ValueError("请先保存角色。")
        def render():
            plugin = self.plugin
            birthday = self.records.get(name, {}).get("birthday") or plugin._lookup_character_profile(name).get("birthday") or "01-01"
            try:
                month, day = map(int, birthday.split("-"))
            except ValueError:
                month, day = 1, 1
            paths = []
            try:
                layout = plugin._card_layout(columns)
                item = plugin._card_item(name, image_size=(layout["item_width"], layout["portrait_height"]))
                paths.append(Path(plugin._render_card_with_pillow(month, day, [item] * columns, [], [], [], layout)))
                paths.append(Path(plugin._render_tantou_cards("预览", [name])[0]))
                return {"birthday": plugin._image_data_uri(paths[0]), "tantou": plugin._image_data_uri(paths[1]),
                        "date_note": "生日未设置时，预览日期使用 01-01，不会登记为生日。"}
            finally:
                for path in paths:
                    if path.is_file():
                        path.unlink()
        return await asyncio.to_thread(render)
