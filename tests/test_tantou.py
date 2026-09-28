"""Offline behavior tests; AstrBot transport is replaced with a small adapter double."""
import asyncio
import copy
import importlib.util
import json
import logging
import shutil
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
    def __init__(self, qq, name=""):
        self.qq = qq
        self.name = name


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
    def __init__(self, user="1001", group="100", text="", owner="测试P", mentions=None):
        self.user, self.group, self.message_str, self.owner = user, group, text, owner
        self.unified_msg_origin = f"bot:GroupMessage:{group}" if group else f"bot:FriendMessage:{user}"
        self.extras = {}
        self.messages = [Plain(text), *(At(qq) for qq in (mentions or []))]

    def get_messages(self):
        return self.messages

    def get_self_id(self):
        return "9999"

    def get_platform_name(self):
        return "aiocqhttp"

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

    async def test_followers_query_scopes_aliases_names_and_saved_batches(self):
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {
            "1001": ["月村手毬", "月村 手毬"], "1002": ["月村手毬"], "1003": ["花海佑芽"],
        }
        self.plugin.storage["tantou_v1:bot:GroupMessage:200"] = {"9000": ["月村手毬"]}
        self.plugin.storage[f"tantou_profiles_v1:{umo}"] = {"1001": {"name": "自己的CN", "nickname": "旧名字"}}
        self.event.bot = types.SimpleNamespace(call_action=AsyncMock(return_value=[
            {"user_id": 1001, "group_id": 100, "card": "新群名片"},
            {"user_id": 1002, "group_id": 100, "card": "花体ℒℴѵℯ🌸"},
            {"user_id": 1002, "group_id": 200, "card": "其他群名字"},
        ]))
        await self.plugin._change_tantou(Event(user="1004"), "加推", "月村手球")
        result = await self.plugin._tantou_followers(self.event, "月村 手毬")
        self.assertIn("本群 2人", result)
        self.assertIn("自己的CNP（1001）", result)
        self.assertIn("花体ℒℴѵℯ🌸P（1002）", result)
        for text in ("其他群名字", "9000", "1003", "1004", "新群名片P"):
            self.assertNotIn(text, result)
        self.event.bot.call_action.assert_awaited_once_with("get_group_member_list", group_id=100, self_id="9999")

    async def test_followers_query_empty_unknown_private_and_transport_failure(self):
        self.assertIn("用法", await self.plugin._tantou_followers(self.event))
        self.assertIn("还没有人", await self.plugin._tantou_followers(self.event, "月村手毬"))
        self.assertIn("群聊", await self.plugin._tantou_followers(Event(group=""), "月村手毬"))
        self.assertIn("完整名字", await self.plugin._tantou_followers(self.event, "月村手球"))
        self.assertIn("没找到", await self.plugin._tantou_followers(self.event, "完全无关的测试词abcdef"))
        self.assertIn("用法", await self.plugin._tantou_followers(Event(mentions=["1002"]), "月村手毬"))
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {"1002": ["月村手毬"]}
        self.plugin.storage[f"tantou_profiles_v1:{umo}"] = {"1002": {"nickname": "缓存昵称"}}
        self.event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=RuntimeError("offline")))
        self.assertIn("缓存昵称P", await self.plugin._tantou_followers(self.event, "月村手毬"))

    async def test_followers_native_and_bare_commands_do_not_handle_twice(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        native = Event(text="担当查询 月村 手毬")
        result = [reply async for reply in self.plugin.tantou_query(native, "月村 手毬")]
        self.assertIn("本群 1人", result[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(native)], [])
        bare = Event(text="担当查询 月村手毬")
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(bare)], result)

    async def test_unique_short_name_queries_directly_but_addition_still_confirms(self):
        await self.plugin._change_tantou(self.event, "加推", "七尾百合子")
        full = await self.plugin._tantou_followers(self.event, "七尾百合子")
        self.assertEqual(await self.plugin._tantou_followers(self.event, "百合子"), full)
        self.assertIn("本群 1人", full)
        self.assertIn("需要确认", await self.plugin._change_tantou(Event(user="1002"), "加推", "百合子"))
        self.assertIn("还没有人", await self.plugin._tantou_followers(self.event, "春香"))
        self.assertIn("完整名字", await self.plugin._tantou_followers(self.event, "田中"))
        self.assertEqual(await self.follows(), ["七尾百合子"])

    async def test_member_followers_query_by_real_mention_and_qq_share_bound_name(self):
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {"1001": ["qq:3000", "qq:3000"], "1002": ["qq:3000"]}
        self.plugin.storage[f"tantou_members_v1:{umo}"] = {"3000": {"nickname": "登记时的群名"}}
        self.plugin.storage[f"tantou_profiles_v1:{umo}"] = {"3000": {"name": "群友CN"}, "1002": {"name": "另一个CN"}}
        self.plugin.storage["tantou_v1:bot:GroupMessage:200"] = {"9000": ["qq:3000"]}
        bot = types.SimpleNamespace(call_action=AsyncMock(return_value=[
            {"group_id": 100, "user_id": 3000, "card": "新群名"},
            {"group_id": 100, "user_id": 1001, "card": "查看者"},
        ]))
        numeric = Event(text="担当查询 3000")
        numeric.bot = bot
        result = [reply async for reply in self.plugin.tantou_text_fallback(numeric)]
        self.assertIn("群友CN · 本群 2人", result[0])
        self.assertIn("另一个CNP（1002）", result[0])
        self.assertNotIn("9000", result[0])
        # AstrBot produces synthetic @ text, while the chain contains Plain + At.
        mention = Event(text="担当查询 @昵称 有空格(3000)", mentions=["9999", "3000"])
        mention.messages = [Plain("担当查询"), At("9999"), At("3000", "昵称 有空格")]
        mention.bot = bot
        self.assertEqual([reply async for reply in self.plugin.tantou_query(mention, "@昵称")], result)
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(mention)], [])

    async def test_member_followers_queries_reject_invalid_targets_and_return_not_found(self):
        for ids, args in ((["all"], ""), (["0"], ""), (["1001", "1002"], ""), (["1001"], "1002"), (["1001"], "月村手毬"), ([], "0"), ([], "9999999999999999999999999")):
            self.assertIn("用法", await self.plugin._tantou_followers(Event(mentions=ids), args))
        self.plugin.storage["tantou_v1:bot:GroupMessage:200"] = {"9000": ["qq:3000"]}
        self.assertIn("查不到", await self.plugin._tantou_followers(self.event, "3000"))
        self.assertIn("查不到", await self.plugin._tantou_followers(Event(mentions=["3000"])))
        self.assertEqual(self.plugin.sent, [])

    async def test_member_followers_query_falls_back_to_cached_target_name(self):
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {"1001": ["qq:3000"]}
        self.plugin.storage[f"tantou_members_v1:{umo}"] = {"3000": {"nickname": "缓存群友🌸"}}
        self.event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=RuntimeError("offline")))
        self.assertIn("缓存群友🌸 · 本群 1人", await self.plugin._tantou_followers(self.event, "3000"))

    async def test_876_brand_is_independent_of_765_storage_and_catalogue(self):
        from collections import Counter
        from PIL import Image as PILImage, ImageColor
        for name in ("日高爱", "水谷绘理", "石川实", "冈本真奈美", "尾崎玲子", "武田苍一"):
            self.assertEqual(self.plugin._character_brand(name), "876_PRO")
            self.assertTrue(self.plugin._card_item(name)["logo_path"].endswith("876_PRO.png"))
        self.assertTrue(self.plugin._character_asset_filename("日高爱").startswith("the_idolmaster/"))
        self.assertEqual(self.plugin._character_brand("天海春香"), "THE_IDOLMASTER")
        self.assertEqual(self.plugin._character_brand("灯里爱夏"), "VA_LIV")
        path = Path(self.plugin._render_tantou_cards("876", ["日高爱", "水谷绘理"])[0])
        self.addCleanup(path.unlink, missing_ok=True)
        with PILImage.open(path) as image:
            colors = Counter(image.getpixel((x, 326)) for x in range(88, 1712))
            self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS["876_PRO"])], 1624)
            self.assertEqual(colors[ImageColor.getrgb(plugin_module.BRAND_COLORS["THE_IDOLMASTER"])], 0)

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

    async def test_numeric_single_and_repeated_choices_keep_original_batch_order(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        reply = Event(text="1")
        self.assertIn("添加成功", ([text async for text in self.plugin.tantou_text_fallback(reply)])[0])
        self.assertEqual(await self.follows(), ["月村手毬"])
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(reply)], [])
        await self.plugin._change_tantou(self.event, "清空担当", "")
        result = await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 千早 一ノ瀬 高木顺二朗")
        self.assertIn("直接回 1 1 1", result)
        self.assertEqual(await self.follows(), [])
        reply = Event(text="1 1 1")
        self.assertIn("添加成功", ([text async for text in self.plugin.tantou_text_fallback(reply)])[0])
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬", "如月千早", "一之濑志希", "高木顺二朗"])

    async def test_numeric_skip_and_validation_do_not_partially_save(self):
        await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 qzxv987 如月千早")
        for value, hint in (("1", "2 个"), ("9 0", "请填")):
            replies = [text async for text in self.plugin.tantou_text_fallback(Event(text=value))]
            self.assertIn(hint, replies[0])
            self.assertEqual(await self.follows(), [])
        replies = [text async for text in self.plugin.tantou_text_fallback(Event(text="1 0"))]
        self.assertIn("添加成功", replies[0])
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬", "如月千早"])
        await self.plugin._change_tantou(self.event, "加推", "qzxv987")
        replies = [text async for text in self.plugin.tantou_text_fallback(Event(text="0"))]
        self.assertIn("已跳过", replies[0])
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬", "如月千早"])

    async def test_numeric_reply_only_claims_current_owner_group_and_active_draft(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        for event in (Event(user="2002", text="1"), Event(group="200", text="1"), Event(group="", text="1"), Event(text="1次"), Event(text="1", mentions=["2002"])):
            self.assertEqual([text async for text in self.plugin.tantou_text_fallback(event)], [])
            self.assertFalse(event.get_extra("imasbd_tantou_handled", False))
        image_reply = Event(text="1"); image_reply.messages.append(Image())
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(image_reply)], [])
        self.assertFalse(image_reply.get_extra("imasbd_tantou_handled", False))
        key = (self.event.unified_msg_origin, self.event.user)
        self.plugin._tantou_pending[key]["expires"] = 0
        expired = Event(text="1")
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(expired)], [])
        self.assertFalse(expired.get_extra("imasbd_tantou_handled", False))
        self.assertNotIn(key, self.plugin._tantou_pending)
        fresh = Event(text="1 1 1")
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(fresh)], [])
        self.assertFalse(fresh.get_extra("imasbd_tantou_handled", False))
        self.assertEqual(await self.follows(), [])

    async def test_numeric_confirmation_reads_plain_chain_when_bot_is_mentioned(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        reply = Event(text="@测试机器人(9999) 1", mentions=["9999"])
        reply.messages[0].text = "1"
        replies = [text async for text in self.plugin.tantou_text_fallback(reply)]
        self.assertIn("添加成功", replies[0])
        self.assertEqual(await self.follows(), ["月村手毬"])

    async def test_mixed_batch_and_individual_choices(self):
        result = await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 千早 qzxv987")
        self.assertIn("没找到「qzxv987」", result)
        self.assertEqual(await self.follows(), [])
        pending = self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]
        self.assertEqual(len(pending["items"]), 3)
        await self.plugin._change_tantou(self.event, "加推确认", "1 0 0")
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬"])

    async def test_corrected_batch_keeps_original_positions_until_every_name_is_valid(self):
        await self.plugin._change_tantou(self.event, "加推", "花海咲季")
        result = await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 一ノ瀬 志希 qzxv987 如月千早")
        self.assertIn("加推确认", result)
        self.assertEqual(await self.follows(), ["花海咲季"])
        result = await self.plugin._change_tantou(self.event, "加推确认", "1 another_typo")
        self.assertIn("仍没找到", result)
        self.assertEqual(await self.follows(), ["花海咲季"])
        result = await self.plugin._change_tantou(self.event, "加推确认", "月村 手毬 高木 順二朗")
        self.assertIn("添加成功", result)
        expected = ["花海咲季", "花海佑芽", "月村手毬", "一之濑志希", "高木顺二朗", "如月千早"]
        self.assertEqual(await self.follows(), expected)
        restored = make_plugin(self.plugin.storage)
        self.assertEqual((await restored._tantou_group(self.event.unified_msg_origin))[self.event.user], expected)

    async def test_repeated_confirmation_choices_and_duplicates_preserve_first_position(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬 一ノ瀬 花海佑芽 月村手毬")
        self.assertEqual(await self.follows(), [])
        result = await self.plugin._change_tantou(self.event, "加推确认", "1 1")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["月村手毬", "一之濑志希", "花海佑芽"])

    async def test_expired_or_replaced_draft_never_adds_exact_names_early(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 qzxv987 花海佑芽")
        self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]["expires"] = 0
        self.assertIn("过期", await self.plugin._change_tantou(self.event, "加推确认", "0"))
        self.assertEqual(await self.follows(), [])
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 qzxv987 花海佑芽")
        await self.plugin._change_tantou(self.event, "加推", "一ノ瀬 志希 月村手毬 花海佑芽")
        self.assertEqual(await self.follows(), ["一之濑志希", "月村手毬", "花海佑芽"])
        self.assertNotIn((self.event.unified_msg_origin, self.event.user), self.plugin._tantou_pending)

    async def test_clear_only_own_group_and_cancels_draft_but_keeps_p_name(self):
        other = Event(user="1002")
        another_group = Event(group="200")
        for event in (self.event, other, another_group):
            await self.plugin._change_tantou(event, "加推", "月村手毬")
        await self.plugin._change_tantou(self.event, "担当改名", "花海")
        await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬")
        result = await self.plugin._change_tantou(self.event, "清空担当", "")
        self.assertIn("已清空", result)
        self.assertEqual(await self.follows(), [])
        self.assertEqual(await self.follows(other), ["月村手毬"])
        self.assertEqual(await self.follows(another_group), ["月村手毬"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, ["月村手毬"]), ["1002"])
        self.assertIn("没有待确认", await self.plugin._change_tantou(self.event, "加推确认", "1"))
        self.assertEqual(await self.plugin._tantou_owner(self.event.unified_msg_origin, self.event.user), "花海")
        await self.plugin._change_tantou(self.event, "加推", "花海佑芽 月村手毬")
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬"])
        for event, args in ((self.event, "1002"), (Event(mentions=["1002"]), "")):
            self.assertIn("只清空你", await self.plugin._change_tantou(event, "清空担当", args))
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬"])

    async def test_other_overview_uses_saved_p_name_and_stays_in_current_group(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬 花海佑芽")
        await self.plugin._change_tantou(self.event, "担当改名", "ℒℴѵℯ•唯爱 丘比.✧=₂✭")
        viewer = Event(user="1002", owner="这是查看者")
        with patch.object(self.plugin, "_render_tantou_cards", return_value=["card.png"]) as render:
            by_id = await self.plugin._tantou_overview(viewer, "1001")
            by_at = await self.plugin._tantou_overview(Event(user="1002", mentions=["9999", "1001"]))
        self.assertEqual(by_id["message"], by_at["message"])
        self.assertTrue(by_id["message"].startswith("ℒℴѵℯ•唯爱 丘比.✧=₂✭P\n"))
        self.assertEqual(render.call_args.args[1], ["月村手毬", "花海佑芽"])
        elsewhere = await self.plugin._tantou_overview(Event(user="1002", group="200"), "1001")
        self.assertEqual(elsewhere["message"], "这位群友还没有在本群登记担当。")
        self.assertEqual(elsewhere["card_path"], "")

    async def test_p_name_survives_nickname_change_and_restart_and_can_reset(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        await self.plugin._change_tantou(self.event, "担当改名", "花海P")
        restored = make_plugin(self.plugin.storage)
        with patch.object(restored, "_render_tantou_cards", return_value=["card.png"]):
            result = await restored._tantou_overview(Event(owner="改过的群昵称"))
            self.assertTrue(result["message"].startswith("花海P\n"))
            self.assertIn("恢复", await restored._change_tantou(Event(owner="改过的群昵称"), "担当改名", "重置"))
            result = await restored._tantou_overview(Event(user="1002"), "1001")
            self.assertTrue(result["message"].startswith("改过的群昵称P\n"))
        self.assertEqual((await restored._tantou_group(self.event.unified_msg_origin))[self.event.user], ["月村手毬"])

    async def test_qq_lookup_fetches_current_group_card_even_without_saved_name(self):
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {"3000": ["月村手毬"]}
        viewer = Event(owner="查看者名字")
        lookup = AsyncMock(return_value={"user_id": 3000, "card": "当前群名片", "nickname": "QQ昵称"})
        viewer.bot = types.SimpleNamespace(call_action=lookup)
        with patch.object(self.plugin, "_render_tantou_cards", return_value=["card.png"]) as render:
            result = await self.plugin._tantou_overview(viewer, "3000")
        self.assertTrue(result["message"].startswith("当前群名片P\n"))
        self.assertEqual(render.call_args.args[0], "当前群名片")
        lookup.assert_awaited_once_with("get_group_member_info", group_id=100, user_id=3000, no_cache=True, self_id="9999")
        profile = self.plugin.storage[f"tantou_profiles_v1:{viewer.unified_msg_origin}"]["3000"]
        self.assertEqual(profile["nickname"], "当前群名片")

    async def test_self_and_other_queries_share_custom_name_then_current_nickname_priority(self):
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        await self.plugin._change_tantou(self.event, "担当改名", "自己的CN")
        viewer = Event(user="1002", owner="查看者")
        viewer.bot = types.SimpleNamespace(call_action=AsyncMock(return_value={"card": "新群昵称", "nickname": "QQ昵称"}))
        with patch.object(self.plugin, "_render_tantou_cards", return_value=["card.png"]):
            own = await self.plugin._tantou_overview(Event(owner="新群昵称"))
            other = await self.plugin._tantou_overview(viewer, "1001")
            self.assertEqual(own["message"], other["message"])
            self.assertTrue(other["message"].startswith("自己的CNP\n"))
            await self.plugin._change_tantou(self.event, "担当改名", "重置")
            own = await self.plugin._tantou_overview(Event(owner="新群昵称"))
            other = await self.plugin._tantou_overview(viewer, "1001")
            self.assertEqual(own["message"], other["message"])
            self.assertTrue(other["message"].startswith("新群昵称P\n"))

    async def test_empty_group_card_uses_qq_nickname(self):
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {"3000": ["月村手毬"]}
        viewer = Event()
        viewer.bot = types.SimpleNamespace(call_action=AsyncMock(return_value={"card": "", "nickname": "QQ昵称"}))
        with patch.object(self.plugin, "_render_tantou_cards", return_value=[]):
            result = await self.plugin._tantou_overview(viewer, "3000")
        self.assertTrue(result["message"].startswith("QQ昵称P\n"))

    async def test_failed_group_lookup_uses_mention_name_then_cached_name_without_numeric_heading(self):
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {"3000": ["月村手毬"]}
        viewer = Event()
        viewer.messages.append(At("3000", "@里的群昵称"))
        viewer.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=RuntimeError("offline")))
        with patch.object(self.plugin, "_render_tantou_cards", return_value=[]):
            mentioned = await self.plugin._tantou_overview(viewer)
            viewer.messages = [Plain("担当 3000")]
            cached = await self.plugin._tantou_overview(viewer, "3000")
            self.assertEqual(mentioned["message"], cached["message"])
            self.assertTrue(cached["message"].startswith("@里的群昵称P\n"))
            self.plugin.storage.pop(f"tantou_profiles_v1:{viewer.unified_msg_origin}")
            unknown = await self.plugin._tantou_overview(viewer, "3000")
        self.assertTrue(unknown["message"].startswith("制作人P\n"))

    async def test_other_platform_member_lookup_keeps_group_scope(self):
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {"3000": ["月村手毬"]}
        viewer = Event()
        viewer.get_platform_name = lambda: "satori"
        viewer.get_group = AsyncMock(return_value=types.SimpleNamespace(members=[
            types.SimpleNamespace(user_id="4000", nickname="其他人"),
            types.SimpleNamespace(user_id="3000", nickname="这位群友"),
        ]))
        with patch.object(self.plugin, "_render_tantou_cards", return_value=[]):
            result = await self.plugin._tantou_overview(viewer, "3000")
        self.assertTrue(result["message"].startswith("这位群友P\n"))
        viewer.get_group.assert_awaited_once_with()

    async def test_unknown_name_prompt_is_short_but_waits_for_whole_ordered_batch(self):
        result = await self.plugin._change_tantou(self.event, "加推", "月村手毬 qzxv987 花海佑芽")
        self.assertEqual(result, "没找到「qzxv987」。\n加推确认 完整名字（序号可直接回，0 跳过）")
        self.assertEqual(await self.follows(), [])
        await self.plugin._change_tantou(self.event, "加推确认", "齋藤孝司")
        self.assertEqual(await self.follows(), ["月村手毬", "斋藤孝司", "花海佑芽"])

    async def test_supporting_characters_register_without_birthdays_and_render_japanese_names(self):
        result = await self.plugin._change_tantou(self.event, "加推", "美诚常务 315社长 十王 邦夫 根緒 亜紗里 美城専務 斎藤孝司")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["美城常务", "斋藤孝司", "十王邦夫", "根绪亚纱里"])
        with patch.object(self.plugin, "_render_tantou_cards", return_value=[]):
            overview = await self.plugin._tantou_overview(self.event)
        self.assertIn("美城常務、齋藤孝司、十王 邦夫、根緒 亜紗里", overview["message"])
        self.assertEqual([self.plugin._character_brand(name) for name in await self.follows()], ["CINDERELLA_GIRLS", "SIDEM", "GAKUEN_IDOLMASTER", "GAKUEN_IDOLMASTER"])
        for name in await self.follows():
            self.assertFalse(self.plugin._lookup_character_profile(name).get("birthday"))
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, ["月村手毬"]), [])

    async def test_target_validation_and_new_commands_dispatch_once(self):
        for args, mentions in (("随便聊聊", []), ("", ["1001", "1002"]), ("", ["all"]), ("1002", ["1001"])):
            result = await self.plugin._tantou_overview(Event(mentions=mentions), args)
            self.assertIn("用法", result["message"])
            self.assertEqual(result["card_path"], "")
        rename = Event(text="担当改名 花海")
        self.assertIn("花海P", ([reply async for reply in self.plugin.tantou_text_fallback(rename)])[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_rename(rename, "花海")], [])
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        with patch.object(self.plugin, "_render_tantou_cards", return_value=["card.png"]), patch.object(self.plugin, "_send_tantou_cards", new_callable=AsyncMock) as send:
            query = Event(user="1002", text="担当", mentions=["1001"])
            self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(query)], [])
            self.assertEqual([reply async for reply in self.plugin.tantou_show(query)], [])
            self.assertEqual(send.await_count, 1)
            self.assertTrue(send.call_args.args[1]["message"].startswith("花海P\n"))
            native_query = Event(user="1002", text="/担当 1001")
            self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(native_query)], [])
            self.assertEqual([reply async for reply in self.plugin.tantou_show(native_query, "1001")], [])
            self.assertEqual(send.await_count, 2)
        rejected = [reply async for reply in self.plugin.tantou_clear(Event(text="/清空担当 1002"), "1002")]
        self.assertIn("只清空你", rejected[0])
        self.assertEqual(await self.follows(), ["月村手毬"])
        clear = Event(text="清空担当")
        self.assertIn("已清空", ([reply async for reply in self.plugin.tantou_text_fallback(clear)])[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_clear(clear)], [])

    async def test_qq_mentions_from_real_astrbot_conversion_and_command_parsing(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures" / "astrbot_qq_mentions.json").read_text(encoding="utf-8"))
        await self.plugin._change_tantou(self.event, "加推", "月村手毬")
        await self.plugin._change_tantou(self.event, "担当改名", "花海")
        await self.plugin._change_tantou(Event(user="1002"), "加推", "一之濑志希")

        def from_fixture(record):
            event = Event(user="1002", text=record["message_str"])
            event.messages = [Plain(part["text"]) if part["type"] == "plain" else At(part["qq"], part["name"]) for part in record["messages"]]
            return event

        with patch.object(self.plugin, "_render_tantou_cards", return_value=["card.png"]) as render, patch.object(self.plugin, "_send_tantou_cards", new_callable=AsyncMock) as send:
            for record in fixtures["records"]:
                with self.subTest(version=record["version"], case=record["case"]):
                    native = from_fixture(record)
                    target, error = self.plugin._tantou_target(native, record["native_args"])
                    self.assertEqual((target, error), (record["expected_target"], ""))
                    self.assertEqual([reply async for reply in self.plugin.tantou_show(native, record["native_args"])], [])
                    self.assertTrue(send.call_args.args[1]["message"].startswith("花海P\n"))
                    self.assertEqual(render.call_args.args[1], ["月村手毬"])
                    bare = from_fixture(record)
                    self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(bare)], [])
                    self.assertTrue(send.call_args.args[1]["message"].startswith("花海P\n"))
                    self.assertEqual(render.call_args.args[1], ["月村手毬"])
            self.assertEqual(send.await_count, 2 * len(fixtures["records"]))

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
        await self.plugin._change_tantou(self.event, "加推", "花海佑芽 手毬 一ノ瀬 志希")
        with patch.object(self.plugin, "put_kv_data", side_effect=RuntimeError("storage failure")):
            with self.assertLogs("test_tantou", level="ERROR"):
                result = await self.plugin._change_tantou(self.event, "加推确认", "1")
        self.assertIn("登记失败", result)
        self.assertEqual(await self.follows(), [])
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", "1"))
        self.assertEqual(await self.follows(), ["花海佑芽", "月村手毬", "一之濑志希"])

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
        self.assertEqual(empty["message"], "还没有登记担当，发送「加推 月村手毬」试试。")
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

    async def test_nickname_keeps_styled_letters_symbols_and_emoji_without_missing_glyphs(self):
        from PIL import ImageFont

        owner = "ℒℴѵℯ•唯爱 丘比.✧=₂✭"
        self.assertEqual(self.plugin._tantou_producer_name(owner), owner + "P")
        primary = self.plugin._pil_font(ImageFont, 62, bold=True)
        renderer = plugin_module.NicknameText(primary, self.plugin.plugin_dir / "assets" / "fonts", 62)
        text = owner + " 😀🎉❤️P"
        runs = renderer.runs(text)
        self.assertEqual("".join(value for value, _ in runs), text)
        for cluster in renderer.graphemes(text):
            if cluster.isspace():
                continue
            font = renderer.font_for(cluster)
            with self.subTest(cluster=cluster):
                self.assertNotEqual(bytes(font.getmask(cluster)), bytes(font.getmask("\U0010ffff")))
                required = {ord(char) for char in cluster if char not in "\u200d\ufe0f"}
                self.assertTrue(required <= renderer.coverage[id(font)])
        image = renderer.render(text, "#3c4d66")
        self.assertIsNotNone(image.getchannel("A").getbbox())
        self.assertLess(image.width, 1550)
        # A narrow line must not cut a flag, modifier or joined emoji in half.
        clusters = ["👩🏽‍💻", "🇯🇵", "❤️", "e\u0301"]
        lines = renderer.wrap("".join(clusters), 1)
        self.assertEqual(lines, clusters)
        path = Path(self.plugin._render_tantou_cards(owner, ["高木顺二朗"])[0])
        self.addCleanup(path.unlink, missing_ok=True)

    async def test_live_fonts_allow_windows_update_without_changing_unicode_rendering(self):
        from PIL import ImageFont

        source = self.plugin.plugin_dir / "assets" / "fonts"
        primary = self.plugin._pil_font(ImageFont, 62, bold=True)
        # The default Windows temp path is ASCII, like the reported deployment.
        # FreeType may load non-ASCII paths into memory itself, hiding the bug.
        with tempfile.TemporaryDirectory(prefix="imasbd-font-update-") as folder:
            fonts_dir = Path(folder) / "fonts"
            fonts_dir.mkdir()
            for path in source.glob("*.ttf"):
                shutil.copyfile(path, fonts_dir / path.name)
            renderer = plugin_module.NicknameText(primary, fonts_dir, 62)
            try:
                text = "ℒℴѵℯ•唯爱 丘比.✧=₂✭ 😀🎉❤️P"
                before = renderer.render(text, "#3c4d66")
                self.assertIs(renderer.font_for("🎉"), renderer.emoji)
                self.assertEqual(len(renderer.fonts), 5)
                # Keep the renderer and its cached fonts live while replacing
                # the entire bundled font directory, as the updater does.
                for path in fonts_dir.iterdir():
                    path.unlink()
                fonts_dir.rmdir()
                after = renderer.render(text, "#3c4d66")
                self.assertEqual(after.size, before.size)
                self.assertEqual(after.tobytes(), before.tobytes())
                fonts_dir.mkdir()
                for path in source.glob("*.ttf"):
                    shutil.copyfile(path, fonts_dir / path.name)
                refreshed = plugin_module.NicknameText(primary, fonts_dir, 62)
                self.assertEqual(refreshed.render(text, "#3c4d66").tobytes(), before.tobytes())
            finally:
                del renderer
                plugin_module._fallback_font.cache_clear()
                plugin_module._font_codepoints.cache_clear()

    async def test_terminate_releases_font_caches_and_cancels_background_tasks(self):
        from PIL import ImageFont

        primary = self.plugin._pil_font(ImageFont, 62, bold=True)
        renderer = plugin_module.NicknameText(primary, self.plugin.plugin_dir / "assets" / "fonts", 62)
        self.assertGreater(plugin_module._fallback_font.cache_info().currsize, 0)
        self.assertGreater(plugin_module._font_codepoints.cache_info().currsize, 0)
        self.plugin._catalogue_task = asyncio.create_task(asyncio.sleep(3600))
        self.plugin._task = asyncio.create_task(asyncio.sleep(3600))
        await asyncio.sleep(0)
        await self.plugin.terminate()
        self.assertTrue(self.plugin._catalogue_task.cancelled())
        self.assertTrue(self.plugin._task.cancelled())
        self.assertEqual(plugin_module._fallback_font.cache_info().currsize, 0)
        self.assertEqual(plugin_module._font_codepoints.cache_info().currsize, 0)
        # A render already in progress retains its in-memory font objects.
        self.assertIsNotNone(renderer.render("🎉 ℒℴѵℯ", "#3c4d66").getchannel("A").getbbox())

    async def test_profile_names_remove_ruby_and_separators_and_remain_matchable(self):
        expected = {"高木顺一朗": "高木 順一朗", "高木顺二朗": "高木 順二朗", "黑井崇男": "黒井 崇男"}
        for name, japanese in expected.items():
            with self.subTest(name=name):
                self.assertEqual(self.plugin._tantou_display_name(name), japanese)
                self.assertEqual(self.plugin._lookup_character_profile(name)["name_jp"], japanese)
        old_profile = {"name_jp": "黒井（くろい） 崇男（たかお）、(Kuroi Takao)", "raw": {"日文名": "original"}}
        with patch.dict(plugin_module.CHARACTER_PROFILES, {"黑井崇男": old_profile}):
            result = self.plugin._lookup_character_profile("黑井崇男")
            self.assertEqual(result["name_jp"], "黒井 崇男")
            self.assertEqual(result["raw"], old_profile["raw"])
            self.assertIn("、", old_profile["name_jp"])
        result = await self.plugin._change_tantou(self.event, "加推", "黒井 崇男")
        self.assertIn("添加成功", result)
        self.assertEqual(await self.follows(), ["黑井崇男"])

    async def test_logo_and_name_share_visible_vertical_center(self):
        from PIL import Image as PILImage, ImageDraw, ImageFont

        font = self.plugin._pil_font(ImageFont, 30, bold=True)
        # Include substantial transparent margins, as the supplied brand assets do.
        logo = PILImage.new("RGBA", (60, 60))
        ImageDraw.Draw(logo).rectangle((13, 19, 36, 34), fill="#f05a7e")
        label = self.plugin._tantou_name_label("高木 順二朗", font, logo)
        self.assertEqual(label.getchannel("A").getbbox(), (0, 0, label.width, label.height))
        icon_bounds = label.crop((0, 0, 24, label.height)).getchannel("A").getbbox()
        text_bounds = label.crop((31, 0, label.width, label.height)).getchannel("A").getbbox()
        self.assertLessEqual(abs((icon_bounds[1] + icon_bounds[3]) - (text_bounds[1] + text_bounds[3])), 1)

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
        self.assertEqual([len(call.args[1]) for call in render.call_args_list], [12, 7])
        self.assertEqual(render.call_args_list[0].kwargs["brands"], render.call_args_list[1].kwargs["brands"])

    async def test_pagination_fills_rows_keeps_order_and_limits_page_size(self):
        catalogue = list(self.plugin._idol_catalogue)
        for count, sizes in ((12, [12]), (18, [18]), (19, [12, 7]), (20, [12, 8]), (36, [18, 18]), (37, [18, 12, 7]), (55, [18, 18, 12, 7])):
            with self.subTest(count=count), patch.object(self.plugin, "_render_tantou_overview", return_value="card.png") as render:
                self.plugin._render_tantou_cards("花海", catalogue[:count])
                pages = [call.args[1] for call in render.call_args_list]
                self.assertEqual([len(page) for page in pages], sizes)
                self.assertEqual([name for page in pages for name in page], catalogue[:count])
                self.assertTrue(all(len(page) % 6 == 0 for page in pages[:-1]))
                self.assertTrue(all(0 < len(page) <= 18 for page in pages))


if __name__ == "__main__":
    unittest.main()
