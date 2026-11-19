"""Check real group registrations, command routing and ranking image output."""
import json
import unittest
import tempfile
import types
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image
from test_tantou import Event, make_plugin, plugin_module


class RankingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = make_plugin()
        self.event = Event()

    async def test_votes_use_unique_group_registrations_and_live_removals(self):
        for user, names in (("1001", "月村手毬 花海佑芽 月村手毬"), ("1002", "月村 手毬"), ("1003", "月村手毬")):
            await self.plugin._change_tantou(Event(user=user), "加推", names)
        await self.plugin._change_tantou(Event(user="1004", group="200"), "加推", "花海佑芽")
        await self.plugin._change_tantou(Event(user="1005"), "加推", "月村手球")
        with patch.object(self.plugin, "_render_tantou_ranking", return_value="rank.png") as render:
            result = await self.plugin._tantou_ranking(self.event)
            self.assertEqual(render.call_args.args[0], [("月村手毬", 3), ("花海佑芽", 1)])
            self.assertIn("月村 手毬 · 3人", result["message"])
            await self.plugin._change_tantou(Event(user="1002"), "减推", "月村手毬")
            await self.plugin._change_tantou(Event(user="1003"), "清空担当", "")
            await self.plugin._tantou_ranking(self.event)
            self.assertEqual(dict(render.call_args.args[0]), {"月村手毬": 1, "花海佑芽": 1})

    async def test_top_ten_and_ties_are_stable_across_storage_order_and_restart(self):
        names = list(self.plugin._idol_catalogue)[:12]
        key = "tantou_v1:" + self.event.unified_msg_origin
        self.plugin.storage[key] = {"1001": names, "1002": names[-1:] * 3}
        with patch.object(self.plugin, "_render_tantou_ranking", return_value="rank.png") as render:
            await self.plugin._tantou_ranking(self.event)
            first = render.call_args.args[0]
        self.assertEqual(len(first), 10)
        self.assertEqual(first[0], (names[-1], 2))
        self.plugin.storage[key] = {"1002": names[-1:], "1001": list(reversed(names))}
        restarted = make_plugin(self.plugin.storage)
        with patch.object(restarted, "_render_tantou_ranking", return_value="rank.png") as render:
            await restarted._tantou_ranking(self.event)
            self.assertEqual(render.call_args.args[0], first)

    async def test_single_ranks_the_entire_group_by_count_then_saved_registration_order(self):
        umo = self.event.unified_msg_origin
        singles = [str(6500 - i) for i in range(8)]
        two = ["va:21149", "qq:2100"]
        group = {"1001": ["月村手毬", *two], "9903": two, "8000": [], "9000": ["vt:retired"],
                 **{uid: ["月村手毬", "月村 手毬"] for uid in singles}, "9902": two, "9901": two}
        storage = {
            "tantou_v1:" + umo: group,
            "tantou_v1:bot:GroupMessage:200": {"1004": ["花海佑芽"]},
            "tantou_profiles_v1:" + umo: {uid: {"name": uid + "号"} for uid in [*group, "1005"]},
        }
        restarted = make_plugin(json.loads(json.dumps(storage)))
        expected = [("qq:" + uid, 1) for uid in singles] + [("qq:9903", 2), ("qq:9902", 2)]
        with patch.object(restarted, "_cache_tantou_member_avatars", new_callable=AsyncMock) as avatars, \
                patch.object(restarted, "_render_tantou_ranking", return_value="rank.png") as render:
            result = await restarted._tantou_ranking(self.event, kind="single")
            self.assertEqual(render.call_args.args[0], expected)
            self.assertEqual(render.call_args.kwargs["title"], "单推排行榜")
            self.assertEqual(render.call_args.kwargs["unit"], "推")
            self.assertIn("1. 6500号P · 1推", result["message"])
            self.assertIn("10. 9902号P · 2推", result["message"])
            avatars.assert_awaited_once_with(umo, [name[3:] for name, _ in expected])
            restarted._prepare_tantou_icons.assert_not_awaited()
            await restarted._tantou_ranking(self.event, kind="dd")
            self.assertEqual(render.call_args.args[0][0], ("qq:1001", 3))
        restored = make_plugin(json.loads(json.dumps(restarted.storage, sort_keys=True)))
        with patch.object(restored, "_cache_tantou_member_avatars", new_callable=AsyncMock), \
                patch.object(restored, "_render_tantou_ranking", return_value="rank.png") as render:
            await restored._tantou_ranking(self.event, kind="single")
            self.assertEqual(render.call_args.args[0], expected)

    async def test_single_seniority_starts_on_save_and_restarts_only_after_the_last_follow_is_removed(self):
        group_key = "tantou_v1:" + self.event.unified_msg_origin
        self.plugin.storage[group_key] = {"9001": []}
        change = self.plugin._change_tantou
        first, second, pending = Event(user="9007"), Event(user="9002"), Event(user="9001")
        await change(pending, "加推", "月村手球")
        await change(first, "加推", "月村手毬")
        await change(second, "加推", "月村手毬 花海佑芽")
        await change(pending, "加推确认", "月村手毬")
        await change(second, "减推", "花海佑芽")
        await change(first, "担当改名", "ZZZ")
        with patch.object(self.plugin, "_cache_tantou_member_avatars", new_callable=AsyncMock), \
                patch.object(self.plugin, "_render_tantou_ranking", return_value="rank.png") as render:
            await self.plugin._tantou_ranking(self.event, kind="single")
            self.assertEqual(render.call_args.args[0], [("qq:9007", 1), ("qq:9002", 1), ("qq:9001", 1)])
            await change(first, "清空担当", "")
            await change(first, "加推", "花海佑芽")
            await change(second, "减推", "月村手毬")
            await self.plugin._tantou_ranking(self.event, kind="single")
            self.assertEqual(render.call_args.args[0], [("qq:9001", 1), ("qq:9007", 1)])
            await change(second, "加推", "月村手毬")
            await self.plugin._tantou_ranking(self.event, kind="single")
            self.assertEqual(render.call_args.args[0][-1], ("qq:9002", 1))

    async def test_failed_new_registration_does_not_reserve_a_place_after_retry(self):
        umo = self.event.unified_msg_origin
        save = self.plugin.put_kv_data
        async def fail_group_write(key, value):
            if key == "tantou_v1:" + umo:
                raise OSError("write interrupted")
            await save(key, value)
        with patch.object(self.plugin, "put_kv_data", side_effect=fail_group_write), self.assertRaises(OSError):
            await self.plugin._append_tantou_batch(umo, "9001", ["花海佑芽"])
        with patch.object(self.plugin, "_render_tantou_ranking") as render:
            self.assertIn("还没有登记", (await self.plugin._tantou_ranking(self.event, kind="single"))["message"])
            render.assert_not_called()
        await self.plugin._append_tantou_batch(umo, "9002", ["月村手毬"])
        await self.plugin._append_tantou_batch(umo, "9001", ["花海佑芽"])
        self.assertEqual(self.plugin.storage["tantou_registration_order_v1:" + umo], ["9002", "9001"])

    async def test_single_rank_bare_and_slash_report_an_empty_group_once(self):
        self.plugin.storage["tantou_v1:" + self.event.unified_msg_origin] = {"1001": [], "9000": ["vt:retired"]}
        with patch.object(self.plugin, "_render_tantou_ranking") as render:
            for command in ("单推排行", "/单推排行"):
                event = Event(text=command)
                replies = [r async for r in self.plugin.tantou_text_fallback(event)]
                replies.extend([r async for r in self.plugin.tantou_single_rank(event)])
                self.assertEqual(len(replies), 1)
                self.assertIn("还没有登记", replies[0])
            render.assert_not_called()

    async def test_member_vote_uses_bound_name_and_render_failure_keeps_text(self):
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {"1001": ["qq:2002", "月村手毬"], "1003": ["qq:2002"]}
        self.plugin.storage[f"tantou_members_v1:{umo}"] = {"2002": {"nickname": "旧群名"}}
        self.plugin.storage[f"tantou_profiles_v1:{umo}"] = {"2002": {"name": "ℒℴѵℯ•唯爱 丘比.✧=₂✭"}}
        with patch.object(self.plugin, "_render_tantou_ranking", side_effect=RuntimeError("render unavailable")), \
                patch.object(self.plugin, "_cache_tantou_member_avatars", new_callable=AsyncMock):
            with self.assertLogs("test_tantou", level="ERROR"):
                result = await self.plugin._tantou_ranking(self.event)
        self.assertIn("1. ℒℴѵℯ•唯爱 丘比.✧=₂✭ · 2人", result["message"])
        self.assertNotIn("qq:2002", result["message"])
        self.assertEqual(result["card_paths"], [])

    async def test_empty_private_and_invalid_command_do_not_render(self):
        with patch.object(self.plugin, "_render_tantou_ranking") as render:
            empty = await self.plugin._tantou_ranking(self.event)
            private = await self.plugin._tantou_ranking(Event(group=""))
            bad = await self.plugin._tantou_ranking(self.event, "其他群")
            mention = await self.plugin._tantou_ranking(Event(mentions=["2002"]))
        self.assertIn("还没有登记", empty["message"])
        self.assertIn("群聊", private["message"])
        self.assertEqual(bad["message"], mention["message"])
        render.assert_not_called()

    async def test_bare_and_slash_commands_send_once_through_existing_transport(self):
        self.plugin._tantou_ranking = AsyncMock(return_value={"message": "rank", "card_path": "rank.png", "card_paths": ["rank.png"]})
        self.plugin._send_tantou_cards = AsyncMock()
        bare = Event(text="担当排行")
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(bare)], [])
        self.assertEqual([reply async for reply in self.plugin.tantou_rank(bare)], [])
        slash = Event(text="/担当排行")
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(slash)], [])
        self.assertEqual([reply async for reply in self.plugin.tantou_rank(slash)], [])
        self.assertEqual(self.plugin._send_tantou_cards.await_count, 2)
        self.assertEqual(self.plugin._tantou_ranking.await_count, 2)

    async def test_real_ranking_has_avatars_and_proportional_colored_bars(self):
        rows = [("月村手毬", 4), ("花海佑芽", 2), ("qq:2002", 1)]
        path = Path(self.plugin._render_tantou_ranking(rows, members={"qq:2002": {"name": "ℒℴѵℯ•唯爱 丘比.✧=₂✭"}}))
        self.addCleanup(path.unlink, missing_ok=True)
        with Image.open(path) as image:
            self.assertEqual(image.size, (1200, 608))
            color = tuple(bytes.fromhex(plugin_module.BRAND_COLORS["GAKUEN_IDOLMASTER"][1:]))
            self.assertEqual(image.getpixel((900, 314)), color)
            self.assertEqual(image.getpixel((600, 426)), color)
            self.assertEqual(image.getpixel((900, 426)), (237, 241, 247))
            self.assertEqual(image.getpixel((400, 538)), (129, 149, 181))
            self.assertEqual(image.getpixel((600, 538)), (237, 241, 247))
            self.assertNotEqual(image.getpixel((150, 516)), (255, 255, 255))

    async def test_group_header_is_live_scoped_and_survives_api_failure(self):
        self.event.bot = types.SimpleNamespace(call_action=AsyncMock(return_value={"group_id": 100, "group_name": "ℒℴѵℯ 偶像大师 🎉"}))
        with patch.object(self.plugin, "_cache_tantou_group_avatar", new_callable=AsyncMock):
            header = await self.plugin._tantou_ranking_group(self.event)
            self.assertEqual(header["name"], "ℒℴѵℯ 偶像大师 🎉")
            self.event.bot.call_action.assert_awaited_once_with("get_group_info", group_id=100, no_cache=True, self_id="9999")
            self.event.bot.call_action.side_effect = RuntimeError("offline")
            self.assertEqual((await self.plugin._tantou_ranking_group(self.event))["name"], header["name"])
            self.assertEqual((await self.plugin._tantou_ranking_group(Event(group="200")))["name"], "本群")
            self.event.bot.call_action.side_effect = None
            self.event.bot.call_action.return_value = {"group_id": 200, "group_name": "错误群资料"}
            self.assertEqual((await self.plugin._tantou_ranking_group(self.event))["name"], header["name"])

    async def test_group_avatar_uses_fixed_url_and_keeps_cache_on_bad_download(self):
        import httpx
        picture = BytesIO(); Image.new("RGB", (640, 640), "red").save(picture, format="PNG")
        requests = []
        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(200, content=picture.getvalue())
        original_client = httpx.AsyncClient
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "avatar.png"
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs)):
                await self.plugin._cache_tantou_group_avatar(path, "100")
                await self.plugin._cache_tantou_group_avatar(path, "100")
                await self.plugin._cache_tantou_group_avatar(path, "../200")
            self.assertEqual(requests, ["https://p.qlogo.cn/gh/100/100/640"])
            with Image.open(path) as avatar:
                self.assertEqual(avatar.size, (320, 320))
            before = path.read_bytes()
            with patch.object(plugin_module.time, "time", return_value=path.stat().st_mtime + 86401), patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://localhost/secret"})), **kwargs)):
                await self.plugin._cache_tantou_group_avatar(path, "100")
            self.assertEqual(path.read_bytes(), before)

    async def test_header_renders_circle_avatar_unicode_name_and_next_line_title(self):
        with tempfile.TemporaryDirectory() as folder:
            avatar = Path(folder) / "group.png"; Image.new("RGB", (100, 100), "red").save(avatar)
            path = Path(self.plugin._render_tantou_ranking([("月村手毬", 1)], group_header={"name": "ℒℴѵℯ 偶像大师 🎉", "avatar_path": avatar}))
            self.addCleanup(path.unlink, missing_ok=True)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((92, 82)), (255, 0, 0))
                self.assertEqual(image.getpixel((48, 38)), (241, 243, 247))
                self.assertTrue(any(low != high for low, high in image.crop((160, 38, 800, 126)).getextrema()))
                self.assertTrue(any(low != high for low, high in image.crop((48, 145, 500, 208)).getextrema()))


if __name__ == "__main__":
    unittest.main()
