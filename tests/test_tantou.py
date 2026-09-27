"""Offline behavior tests; AstrBot transport is replaced with a small adapter double."""
import asyncio
import copy
import importlib.util
import logging
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo


class Plain:
    def __init__(self, text):
        self.text = text


class At:
    def __init__(self, qq):
        self.qq = qq


class Image:
    @staticmethod
    def fromBase64(data):
        return ("image_base64", data)

    @staticmethod
    def fromFileSystem(path):
        return ("image_file", path)


class MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def message(self, text):
        self.chain.append(Plain(text))
        return self

    def file_image(self, path):
        self.chain.append(Image.fromFileSystem(path))
        return self


def decorator(*args, **kwargs):
    def wrap(func):
        func.command = decorator
        return func
    return wrap


def load_plugin():
    modules = {name: types.ModuleType(name) for name in (
        "astrbot", "astrbot.api", "astrbot.api.event", "astrbot.api.star",
        "astrbot.api.message_components", "astrbot.core", "astrbot.core.star",
        "astrbot.core.star.filter", "astrbot.core.star.filter.command",
    )}
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api"].logger = logging.getLogger("test_tantou")
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.event"].MessageChain = MessageChain
    modules["astrbot.api.event"].filter = types.SimpleNamespace(
        command=decorator, command_group=decorator, on_astrbot_loaded=decorator,
        permission_type=decorator, llm_tool=decorator, event_message_type=decorator,
        PermissionType=types.SimpleNamespace(ADMIN="admin"),
        EventMessageType=types.SimpleNamespace(ALL="all"),
    )
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = object
    modules["astrbot.core.star.filter.command"].GreedyStr = str
    components = modules["astrbot.api.message_components"]
    components.Plain, components.At, components.Image = Plain, At, Image
    path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("imasbd_under_test", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


plugin_module = load_plugin()


class Event:
    def __init__(self, user="1001", group="100", text="", owner="测试P"):
        self.user, self.group, self.message_str, self.owner = user, group, text, owner
        self.unified_msg_origin = f"bot:GroupMessage:{group}" if group else f"bot:FriendMessage:{user}"
        self.extras = {}

    def get_group_id(self):
        return self.group

    def get_sender_id(self):
        return self.user

    def get_sender_name(self):
        return self.owner

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def stop_event(self):
        pass

    def plain_result(self, text):
        return text


def make_plugin(storage=None):
    plugin = object.__new__(plugin_module.ImasBirthdayPlugin)
    plugin.config = {"render_card": False, "send_time": "09:00", "white_umos": ["bot:GroupMessage:100", "bot:GroupMessage:200"]}
    plugin.plugin_dir = Path(__file__).resolve().parents[1]
    plugin.assets_dir = plugin.plugin_dir / "assets" / "characters"
    plugin.portraits_dir = plugin.plugin_dir / "assets" / "portraits"
    plugin.tantou_icons_dir = plugin.plugin_dir / "assets" / "tantou_icons"
    plugin._tantou_lock = asyncio.Lock()
    plugin._tantou_icons_lock = asyncio.Lock()
    plugin._tantou_pending = {}
    plugin._idol_catalogue = copy.deepcopy(plugin_module.CHARACTER_TANTOU_ICONS)
    plugin._idol_catalogue_loaded = False
    plugin._idol_catalogue_lock = asyncio.Lock()
    plugin._catalogue_task = None
    plugin._catalogue_next_refresh = 0.0
    plugin._prepare_tantou_icons = AsyncMock()
    plugin._last_sent_date = ""
    plugin._suppressed_first_start_date = ""
    plugin._pending_retry_date = ""
    plugin._pending_retry_umos = set()
    plugin._delivery_state_loaded = True
    plugin._delivery_state_exists = True
    plugin.storage = storage if storage is not None else {}
    plugin.sent = []

    async def get(key, default=None):
        await asyncio.sleep(0)
        return copy.deepcopy(plugin.storage.get(key, default))

    async def put(key, value):
        await asyncio.sleep(0)
        plugin.storage[key] = copy.deepcopy(value)

    async def send(umo, chain):
        plugin.sent.append((umo, chain))
        return True

    plugin.get_kv_data, plugin.put_kv_data = get, put
    plugin.context = types.SimpleNamespace(send_message=send)
    return plugin


class TantouTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = make_plugin()
        self.event = Event()

    async def follows(self, event=None):
        event = event or self.event
        return (await self.plugin._tantou_group(event.unified_msg_origin)).get(event.user, [])

    async def test_exact_batch_duplicates_and_restart(self):
        result = await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽 月村手毬")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["月村手毬", "花海佑芽"])
        result = await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        self.assertIn("已经加推", result)
        restarted = make_plugin(self.plugin.storage)
        self.assertEqual((await restarted._tantou_group(self.event.unified_msg_origin))[self.event.user], ["月村手毬", "花海佑芽"])

    async def test_fuzzy_needs_confirmation_even_with_one_candidate(self):
        result = await self.plugin._change_tantou(self.event, "加推", "月村手球")
        self.assertIn("需要确认", result)
        self.assertEqual(await self.follows(), [])
        pending = self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]
        choices = pending["items"][0]["candidates"]
        result = await self.plugin._change_tantou(self.event, "加推确认", str(choices.index("月村手毬") + 1))
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["月村手毬"])

    async def test_mixed_batch_and_individual_choices(self):
        result = await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 千早 qzxv987")
        self.assertIn("没找到「qzxv987」", result)
        self.assertEqual(await self.follows(), ["花海佑芽"])
        pending = self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]
        self.assertEqual(len(pending["items"]), 2)
        await self.plugin._change_tantou(self.event, "加推确认", "1 0")
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬"])

    async def test_confirmation_scoped_expiry_and_invalid_choices(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        for stranger in (Event(user="1002"), Event(group="200")):
            self.assertIn("没有待确认", await self.plugin._change_tantou(stranger, "加推确认", "1"))
        for choices in ("99", "-1", "abc", "1 2"):
            self.assertNotIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", choices))
        self.assertEqual(await self.follows(), [])
        self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]["expires"] = 0
        self.assertIn("过期", await self.plugin._change_tantou(self.event, "加推确认", "1"))

    async def test_new_request_cancels_old_confirmation(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        await self.plugin._change_tantou(self.event, "加推", "花海佑芽")
        self.assertIn("没有待确认", await self.plugin._change_tantou(self.event, "加推确认", "1"))

    async def test_normalized_match_is_not_exact(self):
        result = await self.plugin._change_tantou(self.event, "加推", "roco")
        self.assertIn("需要确认", result)
        self.assertEqual(await self.follows(), [])

    async def test_failed_storage_keeps_confirmation_for_retry(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        with patch.object(self.plugin, "put_kv_data", side_effect=RuntimeError("storage failure")):
            with self.assertLogs("test_tantou", level="ERROR"):
                result = await self.plugin._change_tantou(self.event, "加推确认", "1")
        self.assertIn("登记失败", result)
        self.assertEqual(await self.follows(), [])
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", "1"))

    async def test_simultaneous_users_do_not_overwrite(self):
        users = [Event(user=str(i)) for i in range(12)]
        await asyncio.gather(*(self.plugin._change_tantou(event, "加推", "月村手毬") for event in users))
        group = await self.plugin._tantou_group(self.event.unified_msg_origin)
        self.assertEqual(len(group), 12)
        self.assertTrue(all(names == ["月村手毬"] for names in group.values()))

    async def test_remove_and_private_chat(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽")
        await self.plugin._change_tantou(self.event, "减推", "月村手毬")
        self.assertEqual(await self.follows(), ["花海佑芽"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, ["月村手毬"]), [])
        self.assertIn("群聊", await self.plugin._change_tantou(Event(group=""), "加推", "月村手毬"))

    async def test_birthday_mentions_group_isolation_and_dedup(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽")
        await self.plugin._change_tantou(Event(user="1002", group="200"), "加推", "月村手毬")
        names = ["月村手毬", "花海佑芽"]
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, names), ["1001"])
        chain = self.plugin._with_tantou_mentions(MessageChain().message("生日快乐"), ["1001", "1001"])
        self.assertEqual([c.qq for c in chain.chain if isinstance(c, At)], ["1001"])
        self.plugin.config["tantou_birthday_mentions"] = False
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, names), [])

    async def test_scheduler_mentions_only_today_and_only_once(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽")
        await self.plugin._change_tantou(Event(user="1002", group="200"), "加推", "花海佑芽")
        self.plugin.storage["birthday_cache"] = {"updated_at": int(time.time()), "data": {
            "06-03": {"characters": ["月村手毬"], "seiyuu": [], "related_people": [], "events": []},
            "04-01": {"characters": ["花海佑芽"], "seiyuu": [], "related_people": [], "events": []},
        }}
        self.plugin._now = lambda: datetime(2026, 6, 3, 10, tzinfo=ZoneInfo("Asia/Tokyo"))
        await self.plugin._tick()
        self.assertEqual(len(self.plugin.sent), 2)
        self.assertEqual([c.qq for c in self.plugin.sent[0][1].chain if isinstance(c, At)], ["1001"])
        self.assertEqual([c.qq for c in self.plugin.sent[1][1].chain if isinstance(c, At)], [])
        await self.plugin._tick()
        self.assertEqual(len(self.plugin.sent), 2)

    async def test_bare_commands_and_no_duplicate_dispatch(self):
        event = Event(text="加推 月村手毬 花海佑芽")
        replies = [text async for text in self.plugin.tantou_text_fallback(event)]
        self.assertIn("添加成功", replies[0])
        self.assertEqual([text async for text in self.plugin.tantou_add(event, "月村手毬 花海佑芽")], [])
        chat = Event(text="我觉得担当很好")
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(chat)], [])

    async def test_slash_registered_command_and_empty_overview(self):
        event = Event(text="/加推 月村手毬")
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(event)], [])
        replies = [text async for text in self.plugin.tantou_add(event, "月村手毬")]
        self.assertIn("添加成功", replies[0])
        empty = await self.plugin._tantou_overview(Event(user="2000"))
        self.assertIn("还没有登记", empty["message"])
        self.assertEqual(empty["card_path"], "")

    async def test_overview_has_all_names_and_real_image(self):
        from PIL import Image as PILImage
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽 花海咲季 如月千早")
        result = await self.plugin._tantou_overview(self.event)
        for name in await self.follows():
            self.assertIn(self.plugin._idol_catalogue[name]["idol_name"], result["message"])
        path = Path(result["card_path"])
        self.addCleanup(path.unlink, missing_ok=True)
        with PILImage.open(path) as image:
            self.assertEqual(image.size, (1800, 1080))
            self.assertAlmostEqual(image.info["dpi"][0], 508, delta=.1)

    async def test_official_icon_cache_and_bad_response(self):
        import io
        import httpx
        from PIL import Image as PILImage

        data = io.BytesIO()
        PILImage.new("RGBA", (170, 154), "red").save(data, format="PNG")
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, content=data.getvalue())

        original_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as directory:
            self.plugin.tantou_icons_dir = Path(directory)
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
                await plugin_module.ImasBirthdayPlugin._prepare_tantou_icons(self.plugin, ["月村手毬", "月村手毬"])
                await plugin_module.ImasBirthdayPlugin._prepare_tantou_icons(self.plugin, ["月村手毬"])
            self.assertEqual(len(requests), 1)
            self.assertEqual(str(requests[0].url), "https://idolmaster-official.jp/assets/img/idol/hexagon/gakuen/temari_tsukimura.png")
            self.assertNotIn("authorization", requests[0].headers)
            self.assertEqual(self.plugin._tantou_icon_path("月村手毬").read_bytes(), data.getvalue())
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'<html>error</html>')), **kwargs)):
                with self.assertLogs("test_tantou", level="WARNING"):
                    await plugin_module.ImasBirthdayPlugin._prepare_tantou_icons(self.plugin, ["花海佑芽"])
            self.assertIsNone(self.plugin._tantou_icon_path("花海佑芽"))

    async def test_official_avatar_keeps_original_transparency(self):
        from PIL import Image as PILImage, ImageDraw

        with tempfile.TemporaryDirectory() as directory:
            self.plugin.tantou_icons_dir = Path(directory)
            filename = plugin_module.CHARACTER_TANTOU_ICONS["月村手毬"]["filename"]
            path = Path(directory) / filename
            path.parent.mkdir(parents=True)
            source = PILImage.new("RGBA", (170, 154))
            ImageDraw.Draw(source).polygon([(40, 0), (130, 0), (169, 77), (130, 153), (40, 153), (0, 77)], fill="red")
            source.save(path)
            canvas = PILImage.new("RGB", (220, 220), "white")
            self.plugin._draw_tantou_avatar(canvas, "月村手毬", 20, 20, 164)
            self.assertEqual(canvas.getpixel((102, 102)), (255, 0, 0))
            self.assertEqual(canvas.getpixel((20, 20)), (255, 255, 255))

    async def test_many_icons_wrap_and_long_names_render(self):
        from PIL import Image as PILImage

        names = list(plugin_module.CHARACTER_TANTOU_ICONS)[:18] + ["阿斯兰贝尔哲布II世"]
        paths = self.plugin._render_tantou_cards("长名字的制作人" * 12, names)
        self.assertEqual(len(paths), 2)
        for filename in paths:
            path = Path(filename)
            self.addCleanup(path.unlink, missing_ok=True)
            with PILImage.open(path) as image:
                self.assertEqual(image.size, (1800, 1080))

    async def test_avatar_mapping_uses_exact_identity(self):
        path = self.plugin.plugin_dir / "tools" / "fetch_tantou_icons.py"
        spec = importlib.util.spec_from_file_location("tantou_icon_fetcher", path)
        fetcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetcher)
        rows = [
            {"idol_name": "月村 手毬", "idol_code": "temari_tsukimura", "brand_code": "GAKUEN"},
            {"idol_name": "詩花", "idol_code": "shika", "brand_code": "OTHER"},
            {"idol_name": "エミリー", "idol_code": "emily_stewart", "brand_code": "MILLIONLIVE"},
            {"idol_name": "月村 手球", "idol_code": "unknown", "brand_code": "GAKUEN"},
        ]
        mapping, unmatched = fetcher.build_mapping(rows)
        self.assertEqual(set(mapping), {"月村手毬", "诗花", "艾米莉·斯图亚特"})
        self.assertEqual(unmatched, [{"name": "月村 手球", "code": "unknown"}])

    async def test_render_failure_keeps_text(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        with patch.object(self.plugin, "_render_tantou_overview", side_effect=RuntimeError("image failure")):
            with self.assertLogs("test_tantou", level="ERROR"):
                result = await self.plugin._tantou_overview(self.event)
        self.assertIn("月村 手毬", result["message"])
        self.assertEqual(result["card_path"], "")

    async def test_minimal_card_uses_official_names_and_only_involved_brand_colors(self):
        from collections import Counter
        from PIL import Image as PILImage, ImageColor, ImageDraw

        names = ["一之濑志希", "月村手毬", "花海佑芽"]
        texts = []
        original_text = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            texts.append(text)
            return original_text(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", autospec=True, side_effect=capture):
            path = Path(self.plugin._render_tantou_cards("花海", names)[0])
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertIn("花海P", texts)
        self.assertIn("担当アイドル", texts)
        for name in names:
            self.assertIn(self.plugin._idol_catalogue[name]["idol_name"], texts)
        self.assertNotIn("一之濑志希", texts)
        self.assertFalse(any("IDOLS" in text or "PRODUCER" in text or "位" in text or "COLLECTION" in text for text in texts))
        with PILImage.open(path) as image:
            colors = Counter(image.getpixel((x, 326)) for x in range(88, 1712))
            self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS["CINDERELLA_GIRLS"])], 806)
            self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS["GAKUEN_IDOLMASTER"])], 806)
            for brand in ("THE_IDOLMASTER", "MILLION_LIVE", "SIDEM", "SHINY_COLORS"):
                self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS[brand])], 0)
        single = Path(self.plugin._render_tantou_cards("花海P", ["一之濑志希"])[0])
        self.addCleanup(single.unlink, missing_ok=True)
        with PILImage.open(single) as image:
            colors = Counter(image.getpixel((x, 326)) for x in range(88, 1712))
            self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS["CINDERELLA_GIRLS"])], 1624)

    async def test_japanese_spaces_and_mixed_batch_share_identity(self):
        result = await self.plugin._change_tantou(self.event, "加推", "一ノ瀬 志希 月村 手毬 一之濑志希 花海佑芽")
        self.assertIn("添加成功", result)
        self.assertNotIn("需要确认", result)
        self.assertEqual(await self.follows(), ["一之濑志希", "月村手毬", "花海佑芽"])
        records = self.plugin.storage["idol_catalogue_v1"]["records"]
        shiki = records["一之濑志希"]
        self.assertEqual(shiki["idol_name"], "一ノ瀬 志希")
        self.assertEqual(shiki["idol_code"], "ichinose_shiki")
        self.assertEqual(shiki["id"], 32)
        result = await self.plugin._change_tantou(self.event, "减推", "一ノ瀬 志希")
        self.assertIn("已移除", result)
        self.assertEqual(await self.follows(), ["月村手毬", "花海佑芽"])

    async def test_japanese_abbreviation_requires_confirmation(self):
        result = await self.plugin._change_tantou(self.event, "加推", "一ノ瀬")
        self.assertIn("需要确认", result)
        self.assertEqual(await self.follows(), [])
        pending = self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]
        candidates = pending["items"][0]["candidates"]
        await self.plugin._change_tantou(self.event, "加推确认", str(candidates.index("一之濑志希") + 1))
        self.assertEqual(await self.follows(), ["一之濑志希"])

    async def test_existing_japanese_records_migrate_and_birthday_matches(self):
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {self.event.user: ["一ノ瀬 志希", "一之濑志希", "月村 手毬"]}
        self.assertEqual(await self.follows(), ["一之濑志希", "月村手毬"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, ["一ノ瀬 志希"]), [self.event.user])
        self.assertEqual(self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"][self.event.user], ["一之濑志希", "月村手毬"])

    async def test_birthday_lookup_accepts_official_japanese_name(self):
        self.plugin.storage["birthday_cache"] = {"updated_at": int(time.time()), "data": {
            "05-30": {"characters": ["一之濑志希"], "seiyuu": [], "related_people": [], "events": []},
        }}
        result = await self.plugin._build_character_profile_result("一ノ瀬 志希")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"]["name"], "一之濑志希")
        self.assertEqual(result["profile"]["official_name_jp"], "一ノ瀬 志希")
        self.assertEqual(result["profile"]["official_code"], "ichinose_shiki")

    async def test_catalogue_sync_uses_code_and_preserves_old_aliases(self):
        import httpx
        await self.plugin._load_idol_catalogue()
        rows = copy.deepcopy(list(plugin_module.CHARACTER_TANTOU_ICONS.values()))
        row = next(row for row in rows if row["idol_code"] == "ichinose_shiki")
        row["idol_name"] = "一ノ瀬 志希（更新）"
        original = httpx.AsyncClient
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=rows)), **kwargs)):
            await self.plugin._sync_idol_catalogue()
        self.assertEqual(self.plugin._idol_catalogue["一之濑志希"]["idol_name"], "一ノ瀬 志希（更新）")
        result = await self.plugin._change_tantou(self.event, "加推", "一ノ瀬 志希")
        self.assertIn("添加成功", result)
        restored = make_plugin(self.plugin.storage)
        await restored._load_idol_catalogue()
        self.assertEqual(restored._idol_catalogue["一之濑志希"]["idol_name"], "一ノ瀬 志希（更新）")
        self.assertGreater(restored._catalogue_next_refresh, time.time())

    async def test_empty_catalogue_cannot_replace_existing_records(self):
        import httpx
        await self.plugin._load_idol_catalogue()
        before = copy.deepcopy(self.plugin.storage["idol_catalogue_v1"])
        original = httpx.AsyncClient
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])), **kwargs)):
            with self.assertLogs("test_tantou", level="WARNING"):
                await self.plugin._sync_idol_catalogue()
        self.assertEqual(self.plugin.storage["idol_catalogue_v1"], before)

    async def test_cached_japanese_key_merges_with_bundled_chinese_identity(self):
        record = copy.deepcopy(self.plugin._idol_catalogue["一之濑志希"])
        self.plugin.storage["idol_catalogue_v1"] = {"updated_at": time.time(), "records": {"一ノ瀬 志希": record}}
        await self.plugin._load_idol_catalogue()
        same_code = [name for name, row in self.plugin._idol_catalogue.items() if row["idol_code"] == "ichinose_shiki"]
        self.assertEqual(same_code, ["一之濑志希"])
        result = await self.plugin._change_tantou(self.event, "加推", "一ノ瀬 志希 一之濑志希")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["一之濑志希"])

    async def test_new_official_character_can_be_registered_without_local_profile(self):
        import httpx
        await self.plugin._load_idol_catalogue()
        rows = copy.deepcopy(list(plugin_module.CHARACTER_TANTOU_ICONS.values()))
        rows.append({"id": 99999, "idol_code": "test_idol", "idol_name": "新規 アイドル", "brand_code": "GAKUEN"})
        original = httpx.AsyncClient
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=rows)), **kwargs)):
            await self.plugin._sync_idol_catalogue()
        result = await self.plugin._change_tantou(self.event, "加推", "新規 アイドル")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["新規 アイドル"])
        self.assertEqual(self.plugin._character_brand("新規 アイドル"), "GAKUEN_IDOLMASTER")

    async def test_paginated_overview_sends_every_business_card(self):
        names = list(self.plugin._idol_catalogue)[:19]
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {self.event.user: names}
        result = await self.plugin._tantou_overview(self.event)
        self.assertEqual(len(result["card_paths"]), 2)
        for path in result["card_paths"]:
            self.addCleanup(Path(path).unlink, missing_ok=True)
        for mode in ("combined_component_base64", "combined_component_file", "combined_file_image", "split_file_image"):
            self.plugin.config["birthday_send_mode"] = mode
            self.plugin.sent.clear()
            await self.plugin._send_tantou_cards(self.event, result)
            self.assertEqual(len(self.plugin.sent), 2)
            self.assertTrue(all(len(chain.chain) == 1 and not isinstance(chain.chain[0], Plain) for _, chain in self.plugin.sent))
        with patch.object(self.plugin, "_render_tantou_overview", return_value="card.png") as render:
            self.plugin._render_tantou_cards("测试P", names)
        self.assertEqual([len(call.args[1]) for call in render.call_args_list], [10, 9])
        self.assertEqual(render.call_args_list[0].kwargs["brands"], render.call_args_list[1].kwargs["brands"])


if __name__ == "__main__":
    unittest.main()
