"""Check real group registrations, command routing and ranking image output."""
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
