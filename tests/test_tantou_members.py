"""Membership verification, message-chain parsing and bounded avatar downloads."""
import copy
import io
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image as PILImage, ImageDraw

from test_tantou import At, Event, Plain, make_plugin, plugin_module


def member(user_id="2002", group_id="100", nickname="真正的群友"):
    return {"user_id": int(user_id), "group_id": int(group_id), "card": nickname, "nickname": "QQ昵称", "avatar": "http://127.0.0.1/private"}


def mentioned_event(command="加推", ids=("2002",), *, group="100", wake=False):
    event = Event(group=group, text=command, owner="登记人")
    event.messages = [*([At("9999", "机器人")] if wake else []), Plain(command), *(At(user_id, "不能信任的@名字") for user_id in ids)]
    return event


def install_bot(event, rows):
    async def action(name, **kwargs):
        if name == "get_group_member_list":
            return copy.deepcopy(rows)
        if name == "get_group_member_info":
            return copy.deepcopy(next(row for row in rows if row["user_id"] == kwargs["user_id"]))
        raise AssertionError(name)
    event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=action))


class MemberTantouTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = make_plugin()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.plugin.tantou_icons_dir = Path(directory.name) / "icons"
        self.real_avatar_cache = self.plugin._cache_tantou_member_avatars
        self.plugin._cache_tantou_member_avatars = AsyncMock()

    async def follows(self, event):
        return (await self.plugin._tantou_group(event.unified_msg_origin)).get(event.user, [])

    async def test_real_at_adds_to_sender_preserves_order_duplicates_restart_and_bound_name(self):
        event = mentioned_event(ids=("3003", "2002", "3003"), wake=True)
        install_bot(event, [member("2002"), member("3003", nickname="ℒℴѵℯ•😀")])
        profiles_key = f"tantou_profiles_v1:{event.unified_msg_origin}"
        self.plugin.storage[profiles_key] = {"2002": {"name": "群友CN"}}
        other = Event(user="2002")
        await self.plugin._change_tantou(other, "加推", "花海佑芽")
        await self.plugin._change_tantou(Event(owner="登记人"), "加推", "月村手毬")
        result = await self.plugin._change_tantou(event, "加推", "@被截断的名字")
        self.assertEqual(result, "添加成功：ℒℴѵℯ•😀、群友CN")
        self.assertEqual(await self.follows(event), ["月村手毬", "qq:3003", "qq:2002"])
        self.assertEqual(await self.follows(other), ["花海佑芽"])
        event.bot.call_action.assert_any_await("get_group_member_list", group_id=100, self_id="9999")
        event.bot.call_action.assert_any_await("get_group_member_info", group_id=100, user_id=2002, no_cache=True, self_id="9999")
        self.assertEqual(event.bot.call_action.await_count, 3)
        self.plugin._cache_tantou_member_avatars.assert_awaited_once_with(event.unified_msg_origin, ["3003", "2002"])
        restored = make_plugin(self.plugin.storage)
        restored.tantou_icons_dir = self.plugin.tantou_icons_dir
        restored._cache_tantou_member_avatars = AsyncMock()
        with patch.object(restored, "_render_tantou_cards", return_value=[]):
            result = await restored._tantou_overview(Event(owner="登记人"))
        self.assertIn("月村 手毬、ℒℴѵℯ•😀、群友CN", result["message"])
        self.assertNotIn("qq:", result["message"])
        self.assertEqual(await self.plugin._tantou_birthday_users(event.unified_msg_origin, ["qq:2002"]), [])

    async def test_actual_astrbot_versions_spaces_parentheses_and_bot_wake(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures" / "astrbot_qq_mentions.json").read_text(encoding="utf-8"))
        for record in fixtures["records"]:
            with self.subTest(version=record["version"], case=record["case"]):
                plugin = make_plugin()
                plugin._cache_tantou_member_avatars = AsyncMock()
                event = Event(user="1002", text=record["message_str"].replace("担当", "加推", 1))
                event.messages = [Plain("加推" if part["text"] == "担当" else part["text"]) if part["type"] == "plain" else At(part["qq"], part["name"]) for part in record["messages"]]
                install_bot(event, [member("1001")])
                replies = [reply async for reply in plugin.tantou_add(event, record["native_args"])]
                self.assertEqual(replies, ["添加成功：真正的群友"])
                self.assertEqual([reply async for reply in plugin.tantou_text_fallback(event)], [])
                self.assertEqual((await plugin._tantou_group(event.unified_msg_origin))["1002"], ["qq:1001"])

    async def test_nonmember_foreign_group_missing_identity_and_api_failure_are_fail_closed(self):
        for rows in ([], [member(group_id="200")], [{"user_id": 2002, "card": "伪造的名字"}]):
            with self.subTest(rows=rows):
                event = mentioned_event()
                install_bot(event, rows)
                before = copy.deepcopy(self.plugin.storage)
                result = await self.plugin._change_tantou(event, "加推", "@伪造的名字")
                self.assertIn("核验", result)
                self.assertEqual(self.plugin.storage, before)
        event = mentioned_event()
        event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=RuntimeError("offline")))
        with self.assertLogs("test_tantou", level="ERROR"):
            result = await self.plugin._change_tantou(event, "加推", "@名字")
        self.assertIn("未添加", result)
        self.assertEqual(self.plugin.storage, {})
        self.plugin._cache_tantou_member_avatars.assert_not_awaited()

    async def test_uncached_member_response_must_match_both_qq_and_current_group(self):
        for bad_info in (member("3003"), member(group_id="200"), {"nickname": "陌生人"}):
            event = mentioned_event()
            event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=[[member()], bad_info]))
            result = await self.plugin._change_tantou(event, "加推", "@名字")
            self.assertIn("未添加", result)
            self.assertEqual(self.plugin.storage, {})
        self.plugin._cache_tantou_member_avatars.assert_not_awaited()

    async def test_one_bad_target_rejects_whole_batch_and_preserves_previous_draft(self):
        event = mentioned_event(ids=("2002", "3003"))
        install_bot(event, [member("2002")])
        ordinary = Event()
        await self.plugin._change_tantou(ordinary, "加推", "月村手毬 qzxv987 花海佑芽")
        before = copy.deepcopy(self.plugin.storage)
        pending = copy.deepcopy(self.plugin._tantou_pending)
        result = await self.plugin._change_tantou(event, "加推", "@名字")
        self.assertIn("未添加", result)
        self.assertEqual(self.plugin.storage, before)
        self.assertEqual(self.plugin._tantou_pending, pending)

    async def test_at_cannot_bypass_extra_text_or_nontext_components(self):
        for trailing in ("http://127.0.0.1/private", "月村手毬", "qq:9999", "../outside", "\n清空担当", "[CQ:at,qq=9999]"):
            event = mentioned_event()
            install_bot(event, [member()])
            event.messages.append(Plain(trailing))
            result = await self.plugin._change_tantou(event, "加推", "@名字")
            self.assertIn("真实 @", result)
            event.bot.call_action.assert_not_awaited()
            self.assertEqual(self.plugin.storage, {})
        event = mentioned_event()
        event.messages.append(types.SimpleNamespace(type="image", url="http://localhost"))
        self.assertIn("真实 @", await self.plugin._change_tantou(event, "加推", "@名字"))

    async def test_invalid_ids_all_at_and_oversized_batches_do_not_call_apis(self):
        invalid = ["all", "0", "-1", "01", "１２３", "http://localhost", "../../outside", "9223372036854775808"]
        for target in invalid:
            event = mentioned_event(ids=(target,))
            install_bot(event, [member()])
            self.assertIn("用法", await self.plugin._change_tantou(event, "加推", "@名字"))
            event.bot.call_action.assert_not_awaited()
        event = mentioned_event(ids=tuple(str(2000 + i) for i in range(31)))
        install_bot(event, [member()])
        self.assertIn("30", await self.plugin._change_tantou(event, "加推", "@名字"))
        event.bot.call_action.assert_not_awaited()
        self.assertEqual(self.plugin.storage, {})

    async def test_plaintext_qq_keys_and_other_platforms_cannot_register_unverified_members(self):
        # Even a compromised birthday source must not make internal identities selectable.
        self.plugin.storage["birthday_cache"] = {"data": {"01-01": {"characters": ["qq:2002"]}}}
        event = Event(text="加推 @群友(2002)")
        await self.plugin._change_tantou(event, "加推", "qq:2002")
        self.assertEqual(await self.follows(event), [])
        for platform in ("satori", "telegram"):
            event = mentioned_event()
            event.get_platform_name = lambda: platform
            install_bot(event, [member()])
            self.assertIn("无法核验", await self.plugin._change_tantou(event, "加推", "@名字"))
            event.bot.call_action.assert_not_awaited()
        event = mentioned_event()
        self.assertIn("无法核验", await self.plugin._change_tantou(event, "加推", "@名字"))

    async def test_remove_departed_member_clear_and_group_isolation(self):
        event = mentioned_event()
        install_bot(event, [member()])
        await self.plugin._change_tantou(event, "加推", "@名字")
        elsewhere = mentioned_event(group="200")
        install_bot(elsewhere, [member(group_id="200", nickname="另一个群名片")])
        await self.plugin._change_tantou(elsewhere, "加推", "@名字")
        self.assertEqual(await self.follows(event), ["qq:2002"])
        self.assertEqual(await self.follows(elsewhere), ["qq:2002"])
        remove = mentioned_event("减推")  # No member API: removal still works after departure.
        result = await self.plugin._change_tantou(remove, "减推", "@名字")
        self.assertEqual(result, "成功减推了：真正的群友")
        self.assertEqual(await self.follows(event), [])
        self.assertEqual(await self.follows(elsewhere), ["qq:2002"])
        await self.plugin._change_tantou(Event(group="200"), "清空担当", "")
        self.assertEqual(await self.follows(elsewhere), [])
        self.assertNotEqual(self.plugin._tantou_member_avatar_path(event.unified_msg_origin, "2002"), self.plugin._tantou_member_avatar_path(elsewhere.unified_msg_origin, "2002"))

    async def test_remove_members_only_names_existing_follows_in_mention_order(self):
        event = mentioned_event(ids=("2002", "3003"))
        install_bot(event, [member(), member("3003", nickname="第二位")])
        await self.plugin._change_tantou(event, "加推", "@群友")
        removal = mentioned_event("减推", ids=("3003", "4004", "2002"))
        self.assertEqual(await self.plugin._change_tantou(removal, "减推", "@群友"), "成功减推了：第二位、真正的群友")
        self.assertEqual(await self.follows(event), [])
        self.assertEqual(await self.plugin._change_tantou(removal, "减推", "@群友"), "你还没加推。")
        self.assertEqual(await self.plugin._change_tantou(mentioned_event("减推"), "减推", "@群友"), "你还没加推。")

    async def test_avatar_uses_fixed_host_ignores_metadata_url_and_caches_bounded_png(self):
        data = io.BytesIO()
        PILImage.new("RGB", (640, 640), "#487bda").save(data, format="JPEG")
        requests = []
        def respond(request):
            requests.append(request)
            return httpx.Response(200, content=data.getvalue())
        original = httpx.AsyncClient
        self.plugin._cache_tantou_member_avatars = self.real_avatar_cache
        event = mentioned_event()
        install_bot(event, [member()])
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
            self.assertIn("添加成功", await self.plugin._change_tantou(event, "加推", "@名字"))
            await self.real_avatar_cache(event.unified_msg_origin, ["2002"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "q1.qlogo.cn")
        self.assertEqual(requests[0].url.params["nk"], "2002")
        path = self.plugin._tantou_member_avatar_path(event.unified_msg_origin, "2002")
        with PILImage.open(path) as image:
            self.assertEqual(image.size, (320, 320))
            self.assertEqual(image.format, "PNG")
        cards = await self.plugin._tantou_member_cards(event.unified_msg_origin, ["qq:2002"])
        canvas = PILImage.new("RGB", (100, 100), "white")
        self.plugin._draw_tantou_avatar(canvas, "qq:2002", 0, 0, 100, avatar_path=cards["qq:2002"]["avatar_path"])
        self.assertGreater(canvas.getpixel((50, 50))[2], 180)

    async def test_redirect_oversized_or_invalid_images_use_blank_and_do_not_follow_urls(self):
        oversized = io.BytesIO()
        PILImage.new("RGB", (2049, 20)).save(oversized, format="PNG")
        for response in (httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
                         httpx.Response(200, content=b"x" * 2_000_001),
                         httpx.Response(200, content=b"<svg><script/></svg>"),
                         httpx.Response(200, content=oversized.getvalue())):
            requests = []
            def respond(request):
                requests.append(request)
                return response
            original = httpx.AsyncClient
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
                await self.real_avatar_cache("bot:GroupMessage:100", ["2002"])
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].url.host, "q1.qlogo.cn")
            self.assertFalse(self.plugin._tantou_member_avatar_path("bot:GroupMessage:100", "2002").exists())

    async def test_stale_and_forced_avatars_replace_png_but_failed_refresh_keeps_old_image(self):
        event = mentioned_event()
        path = self.plugin._tantou_member_avatar_path(event.unified_msg_origin, "2002")
        path.parent.mkdir(parents=True)
        PILImage.new("RGB", (20, 20), "red").save(path)
        requests = []
        colors = iter(("blue", "green"))
        def respond(request):
            requests.append(request)
            if len(requests) == 3:
                return httpx.Response(503)
            data = io.BytesIO()
            PILImage.new("RGB", (640, 640), next(colors)).save(data, format="PNG")
            return httpx.Response(200, content=data.getvalue())
        original = httpx.AsyncClient
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(respond), **kw)):
            self.assertEqual(await self.real_avatar_cache(event.unified_msg_origin, ["2002"]), {"2002"})
            self.assertEqual(requests, [])
            os.utime(path, (time.time() - 3601,) * 2)
            self.assertEqual(await self.real_avatar_cache(event.unified_msg_origin, ["2002"]), {"2002"})
            with PILImage.open(path) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255, 255))
            self.assertEqual(await self.real_avatar_cache(event.unified_msg_origin, ["2002"], force=True), {"2002"})
            before_failure = path.read_bytes()
            self.assertEqual(await self.real_avatar_cache(event.unified_msg_origin, ["2002"], force=True), set())
            self.assertEqual(path.read_bytes(), before_failure)
        self.assertEqual(len(requests), 3)
        self.assertEqual(len({request.url.params["t"] for request in requests}), 3)
        for request in requests:
            self.assertEqual(request.url.host, "q1.qlogo.cn")
            self.assertEqual(request.headers["cache-control"], "no-cache")
        with PILImage.open(path) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 128, 0, 255))

    async def test_overview_and_ranking_read_new_avatar_after_automatic_refresh(self):
        event = Event()
        self.plugin.storage[f"tantou_v1:{event.unified_msg_origin}"] = {event.user: ["qq:2002"]}
        self.plugin.storage[f"tantou_members_v1:{event.unified_msg_origin}"] = {"2002": {"nickname": "真正的群友"}}
        path = self.plugin._tantou_member_avatar_path(event.unified_msg_origin, "2002")
        path.parent.mkdir(parents=True)
        PILImage.new("RGB", (20, 20), "red").save(path)
        data = io.BytesIO()
        PILImage.new("RGB", (640, 640), "blue").save(data, format="PNG")
        original = httpx.AsyncClient
        self.plugin._cache_tantou_member_avatars = self.real_avatar_cache
        def inspect_card(*args, members, **kwargs):
            with PILImage.open(members["qq:2002"]["avatar_path"]) as image:
                self.assertEqual(image.getpixel((0, 0)), (0, 0, 255, 255))
            return []
        with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=data.getvalue())), **kw)), \
                patch.object(self.plugin, "_render_tantou_cards", side_effect=inspect_card), \
                patch.object(self.plugin, "_render_tantou_ranking", side_effect=inspect_card), \
                patch.object(self.plugin, "_tantou_ranking_group", return_value={"name": "本群", "avatar_path": None}):
            for query in (lambda: self.plugin._tantou_overview(event), lambda: self.plugin._tantou_ranking(event, kind="member")):
                os.utime(path, (time.time() - 3601,) * 2)
                await query()
            self.plugin._render_tantou_cards.assert_called_once()
            self.plugin._render_tantou_ranking.assert_called_once()

    async def test_manual_refresh_verifies_real_at_keeps_follows_and_bound_name_and_limits_repeats(self):
        event = mentioned_event(command="刷新群友头像", wake=True)
        install_bot(event, [member()])
        self.plugin.storage[f"tantou_v1:{event.unified_msg_origin}"] = {event.user: ["月村手毬", "qq:2002"]}
        self.plugin.storage[f"tantou_profiles_v1:{event.unified_msg_origin}"] = {"2002": {"name": "群友CN"}}
        self.plugin._cache_tantou_member_avatars.return_value = {"2002"}
        self.plugin._tantou_pending[(event.unified_msg_origin, event.user)] = {"unrelated": "pending batch"}
        result = [reply async for reply in self.plugin.tantou_refresh_member_avatar(event, "@截断名字")]
        self.assertEqual(result, ["头像已重新获取：群友CN。"])
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(event)], [])
        self.assertEqual(await self.follows(event), ["月村手毬", "qq:2002"])
        self.assertEqual(self.plugin._tantou_pending[(event.unified_msg_origin, event.user)], {"unrelated": "pending batch"})
        event.bot.call_action.assert_any_await("get_group_member_info", group_id=100, user_id=2002, no_cache=True, self_id="9999")
        self.plugin._cache_tantou_member_avatars.assert_awaited_once_with(event.unified_msg_origin, ["2002"], force=True)
        repeated = mentioned_event(command="刷新群友头像")
        install_bot(repeated, [member()])
        self.assertIn("1 分钟", await self.plugin._refresh_tantou_member_avatars(repeated))
        self.assertEqual(self.plugin._cache_tantou_member_avatars.await_count, 1)

    async def test_refresh_rejects_spoofed_at_nonmembers_foreign_identity_and_extra_content(self):
        command = "刷新群友头像"
        handwritten = Event(text=command + " @真正的群友")
        mixed = mentioned_event(command=command)
        mixed.messages.append(Plain(" https://evil.example/avatar"))
        too_many = mentioned_event(command=command, ids=tuple(str(2000 + i) for i in range(31)))
        private = mentioned_event(command=command, group="")
        nonmember = mentioned_event(command=command)
        install_bot(nonmember, [])
        foreign = mentioned_event(command=command)
        install_bot(foreign, [member()])
        foreign.bot.call_action.side_effect = lambda action, **kw: [member()] if action == "get_group_member_list" else member(group_id="200")
        for event in (handwritten, mixed, too_many, private, nonmember, foreign, mentioned_event(command=command, ids=("all",))):
            with self.subTest(text=event.message_str, ids=self.plugin._tantou_mentions(event)):
                result = await self.plugin._refresh_tantou_member_avatars(event)
                self.assertNotIn("已重新获取", result)
                self.assertEqual(await self.follows(event), [])
        self.plugin._cache_tantou_member_avatars.assert_not_awaited()
        self.assertFalse(any(key.startswith("tantou_avatar_refresh_v1:") for key in self.plugin.storage))

    async def test_fallback_refresh_reports_partial_failures_without_registering_members(self):
        event = mentioned_event(command="刷新群友头像", ids=("2002", "3003"))
        install_bot(event, [member(), member("3003", nickname="第二位")])
        self.plugin._cache_tantou_member_avatars.return_value = {"2002"}
        result = [reply async for reply in self.plugin.tantou_text_fallback(event)]
        self.assertEqual(result, ["头像已重新获取：真正的群友。\n获取失败，保留原图：第二位。"])
        self.assertEqual(await self.follows(event), [])

    async def test_new_profiles_and_unicode_member_names_render_with_blank_avatars(self):
        names = ["美作武史", "赤羽根P", "武内P", "闪耀色彩P", "石川P", "百万动画P", "今西部长", "训练员", "资深训练员", "新人训练员", "石川实", "冈本真奈美", "尾崎玲子", "武田苍一"]
        added_names = ["贺阳燐羽", "蓝井抚子", "白草四音", "白草月花", "学园Vo训练员", "学园Da训练员", "学园Vi训练员", "卓帕卡布拉", "ぴにゃこら太", "呆笔太郎"]
        names.extend(added_names)
        result = await self.plugin._change_tantou(Event(), "加推", " ".join(names))
        self.assertIn("添加成功", result)
        self.assertNotIn("需要确认", result)
        self.assertEqual(await self.follows(Event()), names)
        for name in names:
            self.assertNotEqual(self.plugin._tantou_display_name(name), "―")
            self.assertFalse(self.plugin._lookup_character_profile(name).get("birthday"))
        self.assertEqual(self.plugin._tantou_display_name("美作武史"), "美作 武史")
        canvas = PILImage.new("RGB", (100, 100), "white")
        with patch.object(ImageDraw.ImageDraw, "text", autospec=True) as draw_text:
            self.plugin._draw_tantou_avatar(canvas, "美作武史", 0, 0, 100)
        draw_text.assert_not_called()
        self.assertEqual(canvas.getpixel((50, 50)), (243, 245, 248))
        captured = []
        render = self.plugin._tantou_name_label
        def capture(text, *args, **kwargs):
            captured.append(text)
            return render(text, *args, **kwargs)
        cards = {"qq:2002": {"name": "ℒℴѵℯ•唯爱 丘比.✧=₂✭😀", "avatar_path": None}}
        with patch.object(self.plugin, "_tantou_name_label", side_effect=capture):
            paths = self.plugin._render_tantou_cards("登记人", ["美作武史", "武内P", "qq:2002", *added_names], members=cards)
        self.addCleanup(Path(paths[0]).unlink, missing_ok=True)
        self.assertIn("美作 武史", captured)
        self.assertIn("武内P", captured)
        for name in added_names:
            self.assertIn(self.plugin._tantou_display_name(name), captured)
        self.assertIn("ℒℴѵℯ•唯爱 丘比.✧=₂✭😀", "".join(captured))
        with patch.object(self.plugin, "_render_tantou_overview", return_value="card.png") as render_page:
            self.plugin._render_tantou_cards("登记人", ["qq:2002"], members=cards)
        self.assertEqual(render_page.call_args.kwargs["brands"], [])

    async def test_long_member_names_cannot_overflow_three_row_cards(self):
        cards = {"qq:2002": {"name": "👩🏽‍💻" * 80, "avatar_path": None}}
        paths = self.plugin._render_tantou_cards("登记人", list(self.plugin._idol_catalogue)[:17] + ["qq:2002"], members=cards)
        self.addCleanup(Path(paths[0]).unlink, missing_ok=True)
        with PILImage.open(paths[0]) as image:
            self.assertEqual(image.size, (1800, 1080))
