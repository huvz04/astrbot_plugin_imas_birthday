"""Voice actors, category rankings, birthday copy, and optional AstrBot commentary."""
import asyncio
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from test_tantou import Event, make_plugin, plugin_module


class VoiceActorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = make_plugin()
        self.event = Event()

    async def test_directory_actors_are_distinct_from_idols_and_support_ordered_confirmation(self):
        actors = plugin_module.VOICE_ACTOR_CATALOGUE
        self.assertGreaterEqual(len(actors), 1800)
        self.assertEqual(actors["va:10001"]["name"], "会 一太郎")
        self.assertEqual(self.plugin._tantou_display_name("va:21149"), "小鹿 なお")
        result = await self.plugin._change_tantou(self.event, "加推", "小鹿奈绪 不存在的声优 希水汐")
        self.assertIn("没找到", result)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001", []), [])
        confirmed = await self.plugin._change_tantou(self.event, "加推确认", "0")
        self.assertIn("添加成功", confirmed)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"], ["va:21149", "va:21026"])
        self.assertIn("小鹿 なお · 本群 1人", await self.plugin._tantou_followers(self.event, "小鹿 なお"))
        await self.plugin._change_tantou(self.event, "减推", "小鹿奈绪")
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"], ["va:21026"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, ["月村手毬"]), [])

    async def test_direct_add_searches_idols_actors_and_verified_mentions(self):
        self.assertIn("添加成功：小鹿 なお", await self.plugin._change_tantou(self.event, "加推", "小鹿なお"))
        self.assertIn("添加成功：会 一太郎", await self.plugin._change_tantou(self.event, "加推", "会 一太郎"))
        self.assertIn("添加成功：月村 手毬", await self.plugin._change_tantou(self.event, "加推", "月村手毬"))
        names = (await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"]
        self.assertEqual(names, ["va:21149", "va:10001", "月村手毬"])

    async def test_ambiguous_actor_and_producer_wait_for_confirmation_as_one_name(self):
        answer = await self.plugin._change_tantou(self.event, "加推", "月村手毬 武内 駿輔 小鹿なお")
        self.assertIn("武内 駿輔", answer)
        self.assertIn("武内P", answer)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001", []), [])
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", "1"))
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"],
                         ["月村手毬", "va:10293", "va:21149"])

    async def test_rankings_separate_votes_and_dd_counts_unique_follows(self):
        umo = self.event.unified_msg_origin
        self.plugin.storage[f"tantou_v1:{umo}"] = {
            "1001": ["月村手毬", "月村手毬", "va:21149", "qq:2002"],
            "1002": ["月村手毬", "va:21026", "va:10001"],
        }
        self.plugin.storage[f"tantou_members_v1:{umo}"] = {"2002": {"nickname": "群友A"}}
        self.plugin.storage[f"tantou_profiles_v1:{umo}"] = {"1001": {"name": "甲"}, "1002": {"name": "乙"}}
        with patch.object(self.plugin, "_render_tantou_ranking", return_value="rank.png") as render, patch.object(self.plugin, "_cache_tantou_member_avatars", new_callable=AsyncMock):
            expected = {
                "idol": {"月村手毬": 2},
                "member": {"qq:2002": 1},
                "voice_actor": {"va:21149": 1, "va:21026": 1, "va:10001": 1},
                "dd": {"qq:1001": 3, "qq:1002": 3},
            }
            for kind, counts in expected.items():
                result = await self.plugin._tantou_ranking(self.event, kind=kind)
                self.assertEqual(dict(render.call_args.args[0]), counts)
                self.assertIn("排行榜", result["message"])
                if kind == "idol":
                    self.assertEqual(render.call_args.kwargs["brand_counts"], Counter({"GAKUEN_IDOLMASTER": 2}))
                if kind == "dd":
                    self.assertIn("甲P · 3推", result["message"])
                    self.assertIn("乙P · 3推", result["message"])
        self.assertNotIn("va:", result["message"])

    async def test_role_labels_only_use_matched_cast_and_birthday_text_uses_japanese(self):
        self.assertEqual(self.plugin._voice_actor_role_label("va:21149"), "月村 手毬役")
        self.assertEqual(self.plugin._birthday_voice_actor_labels(["小鹿奈绪", "未收录声优"]), ["小鹿 なお（月村 手毬役）", "未收录声优"])
        text = self.plugin._build_message_from_entry(6, 3, {"characters": ["月村手毬"], "seiyuu": ["小鹿奈绪"], "related_people": [], "events": []})
        self.assertIn("月村 手毬", text)
        self.assertIn("小鹿 なお（月村 手毬役）", text)
        self.assertNotIn("角色：月村手毬", text)
        html = self.plugin._birthday_card_html(month=6, day=3, items=[], seiyuu=["小鹿 なお（月村 手毬役）"], related_people=[], events=[], layout=self.plugin._card_layout(0))
        self.assertIn("同日生日の声優", html)
        self.assertNotIn("Character images are sourced", html)

    async def test_bare_and_native_actor_commands_do_not_duplicate(self):
        bare = Event(text="加推 小鹿なお")
        replies = [text async for text in self.plugin.tantou_text_fallback(bare)]
        self.assertIn("添加成功", replies[0])
        self.assertEqual([text async for text in self.plugin.tantou_add(bare, "小鹿なお")], [])
        slash = Event(text="/声优排行")
        self.plugin._tantou_ranking = AsyncMock(return_value={"message": "rank", "card_path": "", "card_paths": []})
        self.assertEqual([text async for text in self.plugin.tantou_text_fallback(slash)], [])
        self.assertEqual([text async for text in self.plugin.tantou_actor_rank(slash)], ["rank"])
        self.plugin._tantou_ranking.assert_awaited_once_with(slash, "", kind="voice_actor")

    async def test_birthday_card_omits_chat_paragraph_but_keeps_mentions_and_text_fallback(self):
        from test_tantou import At, MessageChain, Plain
        with patch.object(self.plugin, "_build_birthday_message_chain", side_effect=lambda message, card, mode: MessageChain().message(message)) as chain:
            await self.plugin._send_birthday_message(self.event.unified_msg_origin, "详细生日资料", "card.png", ["1001"])
            self.assertEqual(chain.call_args.args[0], "")
            self.assertEqual([part.qq for part in self.plugin.sent[-1][1].chain if isinstance(part, At)], ["1001"])
            self.plugin.config["birthday_text_with_card"] = True
            await self.plugin._send_birthday_message(self.event.unified_msg_origin, "详细生日资料", "card.png")
            self.assertEqual(chain.call_args.args[0], "详细生日资料")
            await self.plugin._send_birthday_message(self.event.unified_msg_origin, "无图文字", "")
            self.assertIn("无图文字", [part.text for part in self.plugin.sent[-1][1].chain if isinstance(part, Plain)])

    async def test_actor_photo_cache_uses_fixed_directory_url_and_validates_image(self):
        import httpx
        from io import BytesIO
        photo = BytesIO(); Image.new("RGB", (100, 100), "purple").save(photo, format="JPEG")
        requests = []
        def respond(request):
            requests.append(str(request.url))
            return httpx.Response(200, content=photo.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            self.plugin.tantou_icons_dir = Path(folder)
            original = httpx.AsyncClient
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)):
                await plugin_module.ImasBirthdayPlugin._prepare_tantou_icons(self.plugin, ["va:21149", "va:21149"])
            self.assertEqual(requests, ["https://seigura.secureserv.jp/img/talent/21149.jpg"])
            self.assertTrue(self.plugin._tantou_icon_path("va:21149").is_file())

    async def test_llm_commentary_uses_temari_voice_and_fails_closed(self):
        self.plugin.context.get_current_chat_provider_id = AsyncMock(return_value="configured-model")
        self.plugin.context.llm_generate = AsyncMock(return_value=types.SimpleNamespace(completion_text="ふん、手毬を選ぶなんて、見る目はあるじゃない。"))
        result = await self.plugin._tantou_llm_commentary(self.event.unified_msg_origin, ["月村手毬", "qq:2002"])
        self.assertIn("見る目", result)
        prompt = self.plugin.context.llm_generate.call_args.kwargs["prompt"]
        self.assertIn("月村手毬", prompt)
        self.assertNotIn("2002", prompt)
        self.plugin.context.llm_generate.side_effect = RuntimeError("offline")
        self.assertEqual(await self.plugin._tantou_llm_commentary(self.event.unified_msg_origin, ["月村手毬"]), "")


if __name__ == "__main__":
    unittest.main()
