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

    async def test_chinese_romanized_names_and_nickname_share_actor_identity(self):
        result = await self.plugin._change_tantou(self.event, "加推", "伊達 小百合 IWA Sayuri Date Kana Hanaiwa")
        self.assertIn("添加成功", result)
        self.assertFalse(self.plugin._tantou_pending)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"], ["va:20966", "va:21218"])
        for query in ("伊达小百合", "DATE SAYURI", "SayuriDate", "date sayuri"):
            self.assertIn("伊達 さゆり · 本群 1人", await self.plugin._tantou_followers(self.event, query))
        for query in ("iwa", "Iwa", "hanaiwa kana", "KanaHanaiwa"):
            self.assertIn("花岩 香奈 · 本群 1人", await self.plugin._tantou_followers(self.event, query))
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "date sayuri iwa"), "已移除：伊達 さゆり、花岩 香奈")

    async def test_romanized_long_vowels_keep_spelling_variants_and_original_order(self):
        answer = await self.plugin._change_tantou(self.event, "加推", "Nao Ojika Itou Mao Amasaki Kohei Mao Ito Kōhei Amasaki")
        self.assertIn("添加成功", answer)
        self.assertFalse(self.plugin._tantou_pending)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"],
                         ["va:21149", "va:21138", "va:10014"])
        self.assertEqual(self.plugin._tantou_display_name("va:21138"), "伊藤 舞音")
        actor = plugin_module.VOICE_ACTOR_CATALOGUE["va:21138"]
        self.assertTrue(actor["is_idolmaster"])
        self.assertIn("仓本千奈", actor["roles"])
        self.assertTrue(any("moegirl" in url for url in actor["sources"]))

    async def test_shared_nickname_waits_for_choice_and_does_not_overwrite_identity(self):
        actors = plugin_module.VOICE_ACTOR_CATALOGUE
        duplicate = {**actors["va:21149"], "aliases": [*actors["va:21149"]["aliases"], "iwa"]}
        with patch.dict(actors, {"va:21149": duplicate}):
            result = await self.plugin._change_tantou(self.event, "加推", "iwa")
            self.assertIn("需要确认", result)
            self.assertIn("花岩 香奈", result)
            self.assertIn("小鹿 なお", result)
            self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001", []), [])

    async def test_reported_gakuen_aliases_resolve_in_order_with_verified_roles(self):
        result = await self.plugin._change_tantou(self.event, "加推", "七濑紬 长月葵 饭田光 天音缘 小鹿ナオ 湊ミヤ")
        self.assertIn("添加成功", result)
        self.assertFalse(self.plugin._tantou_pending)
        identities = (await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"]
        self.assertEqual([self.plugin._tantou_display_name(ident) for ident in identities],
                         ["七瀬 つむぎ", "長月 あおい", "飯田 ヒカル", "天音 ゆかり", "小鹿 なお", "湊 みや"])
        for ident, role in zip(identities, ["有村 麻央役", "花海 咲季役", "藤田 ことね役", "雨夜 燕役", "月村 手毬役", "紫雲 清夏役"]):
            self.assertTrue(plugin_module.VOICE_ACTOR_CATALOGUE[ident]["is_idolmaster"])
            self.assertIn(role, self.plugin._voice_actor_role_label(ident))
        self.assertFalse(plugin_module.VOICE_ACTOR_CATALOGUE["va:21077"]["is_idolmaster"])

    async def test_candidate_only_numbers_leave_unknowns_for_ordered_correction(self):
        await self.plugin._change_tantou(self.event, "加推", "qzxv987 あおい 光 ゆかり なお qzxv999")
        key = (self.event.unified_msg_origin, self.event.user)
        pending = self.plugin._tantou_pending[key]
        self.assertEqual(len(pending["items"]), 6)
        candidates = [item for item in pending["items"] if item["candidates"]]
        self.assertEqual(len(candidates), 4)
        expected = ["月村手毬", candidates[0]["candidates"][0], candidates[2]["candidates"][2], candidates[3]["candidates"][0]]
        reply = await self.plugin._change_tantou(self.event, "加推确认", "1 0 3 1")
        self.assertIn("候选已确认", reply)
        self.assertEqual([item["query"] for item in pending["items"]], ["qzxv987", "qzxv999"])
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001", []), [])
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", "月村手毬 0"))
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"], expected)
        self.assertNotIn(key, self.plugin._tantou_pending)

    async def test_actor_avatar_preserves_both_ends_of_official_portrait(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "portrait.png"
            photo = Image.new("RGB", (80, 160), "white")
            photo.paste("red", (0, 0, 80, 20))
            photo.paste("blue", (0, 140, 80, 160))
            photo.save(path)
            canvas = Image.new("RGB", (170, 170), "white")
            with patch.object(self.plugin, "_tantou_icon_path", return_value=path):
                self.plugin._draw_tantou_avatar(canvas, "va:21149", 0, 0, 170)
            pixels = list(canvas.getdata())
            self.assertGreater(pixels.count((255, 0, 0)), 300)
            self.assertGreater(pixels.count((0, 0, 255)), 300)

    async def test_birthday_keeps_original_sizes_and_actor_info_only_in_text(self):
        for count, width, height in [(1, 300, 360), (2, 260, 320), (3, 214, 300)]:
            layout = self.plugin._card_layout(count)
            self.assertEqual((layout["item_width"], layout["portrait_height"]), (width, height))
        entry = {"characters": ["天海春香"], "seiyuu": ["小鹿奈绪"]}
        with patch.object(self.plugin, "_render_card_with_pillow", return_value="birthday.png") as render:
            await self.plugin._render_card(4, 3, entry)
            items = render.call_args.args[2]
            self.assertEqual(len(items), 1)
            self.assertEqual(render.call_args.args[3], [])
            self.plugin._prepare_tantou_icons.assert_not_awaited()
            html = self.plugin._birthday_card_html(4, 3, items, ["小鹿 なお（月村 手毬役）"], [], [], self.plugin._card_layout(1))
            self.assertNotIn("声優の誕生日", html)
            self.assertNotIn("小鹿", html)
            self.assertIn("声优：小鹿 なお（月村 手毬役）", self.plugin._build_message_from_entry(4, 3, entry))
            self.plugin.config["include_seiyuu"] = False
            await self.plugin._render_card(4, 3, entry)
            self.assertEqual(len(render.call_args.args[2]), 1)

    async def test_supplemental_actor_registry_has_stable_identity_and_blank_photo(self):
        matches = [(ident, row) for ident, row in plugin_module.VOICE_ACTOR_CATALOGUE.items()
                   if plugin_module.person_name_key(row["name"]) == "春野ななみ"]
        self.assertEqual(len(matches), 1)
        ident, row = matches[0]
        self.assertTrue(ident.startswith("va:imas-"))
        self.assertEqual(row["image_url"], "")
        self.assertTrue(row["is_idolmaster"])
        self.assertIn("上田铃帆", row["roles"])

    async def test_remove_shows_names_and_accepts_shindo_chinese_alias(self):
        added = await self.plugin._change_tantou(self.event, "加推", "花宮 初奈 月村手毬 进藤天音")
        self.assertIn("進藤 あまね", added)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))["1001"],
                         ["va:21077", "月村手毬", "va:20961"])
        removed = await self.plugin._change_tantou(self.event, "减推", "花宮 初奈 月村手毬 进藤天音")
        self.assertEqual(removed, "已移除：花宮 初奈、月村 手毬、進藤 あまね")
        self.assertNotIn("va:", removed)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001"), None)

    async def test_ambiguous_actor_and_producer_wait_for_confirmation_as_one_name(self):
        answer = await self.plugin._change_tantou(self.event, "加推", "月村手毬 武内 駿輔 小鹿なお")
        self.assertIn("武内 駿輔", answer)
        self.assertIn("武内P", answer)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin)).get("1001", []), [])
        candidates = self.plugin._tantou_pending[(self.event.unified_msg_origin, self.event.user)]["items"][0]["candidates"]
        self.assertEqual(candidates[0], "武内P")
        actor_choice = str(candidates.index("va:10293") + 1)
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推确认", actor_choice))
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
        self.assertNotIn("同日生日の声優", html)
        self.assertNotIn("小鹿", html)
        self.assertNotIn("今天没有匹配到本地角色图", html)
        self.assertNotIn("Character images are sourced", html)

    async def test_legacy_birthday_subtitle_is_shortened(self):
        self.plugin.config["card_subtitle"] = "THE IDOLM@STER Birthday"
        self.assertEqual(self.plugin._card_subtitle(), "THE IDOLM@STER")
        html = self.plugin._birthday_card_html(10, 7, [], [], [], [], self.plugin._card_layout(0))
        self.assertIn('<div class="subtitle">THE IDOLM@STER</div>', html)
        self.assertNotIn('<div class="subtitle">THE IDOLM@STER Birthday</div>', html)

    async def test_actor_only_birthday_sends_text_without_empty_image(self):
        for allow_empty in (False, True):
            self.plugin.config["render_card_without_character_image"] = allow_empty
            rendered = await self.plugin._render_card(6, 3, {"characters": [], "seiyuu": ["小鹿奈绪"]})
            self.assertEqual(rendered, "")

    async def test_single_and_august_first_cards_center_complete_rows_below_header(self):
        for date in ("10-06", "08-01"):
            characters = [name for name, profile in plugin_module.CHARACTER_PROFILES.items() if profile.get("birthday") == date]
            self.assertGreaterEqual(len(characters), 4 if date == "08-01" else 1)
            layout = self.plugin._card_layout(len(characters))
            items = [self.plugin._card_item(name, image_size=(layout["item_width"], layout["portrait_height"])) for name in characters]
            with patch.object(self.plugin, "_draw_pillow_idol_card") as draw:
                output = self.plugin._render_card_with_pillow(10, 6, items, [], [], [], layout)
                self.assertEqual(draw.call_count, len(characters))
                top = min(call.args[4] for call in draw.call_args_list)
                bottom = max(call.args[4] + call.args[7] for call in draw.call_args_list)
                with Image.open(output) as card:
                    self.assertLessEqual(abs((top - 138) - (card.height - 30 - bottom)), 1)
                    self.assertLessEqual(bottom, card.height - 30)
                if date == "10-06":
                    self.assertGreater(top, 138)
                else:
                    self.assertGreater(len({call.args[4] for call in draw.call_args_list}), 1)

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
            message = self.plugin._build_message_from_entry(10, 6, {"characters": ["乙仓悠贵"], "seiyuu": ["泰勇气"]})
            await self.plugin._send_birthday_message(self.event.unified_msg_origin, message, "card.png", ["1001"])
            self.assertEqual(chain.call_args.args[0], "声优：泰 勇気（ドラマCD プロデューサー役）")
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
