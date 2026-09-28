"""Check real group registrations, command routing and ranking image output."""
import unittest
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
        with patch.object(self.plugin, "_render_tantou_ranking", side_effect=RuntimeError("render unavailable")):
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
            self.assertEqual(image.size, (1200, 512))
            color = tuple(bytes.fromhex(plugin_module.BRAND_COLORS["GAKUEN_IDOLMASTER"][1:]))
            self.assertEqual(image.getpixel((900, 218)), color)
            self.assertEqual(image.getpixel((600, 330)), color)
            self.assertEqual(image.getpixel((900, 330)), (237, 241, 247))
            self.assertEqual(image.getpixel((400, 442)), (129, 149, 181))
            self.assertEqual(image.getpixel((600, 442)), (237, 241, 247))
            self.assertNotEqual(image.getpixel((150, 420)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
