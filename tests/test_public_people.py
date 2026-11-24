"""Physical catalogue isolation, public identities and bounded portrait fetching."""
import importlib.util
import io
import json
import tempfile
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from test_tantou import Event, make_plugin, plugin_module

ROOT = Path(__file__).resolve().parents[1]
SINGER_WIKI = r"^https://(?:ja\.wikipedia\.org/wiki/|dic\.nicovideo\.jp/(?:a|id)/|seesaawiki\.jp/utaite/d/)[^?#]+$"
spec = importlib.util.spec_from_file_location("public_people_test_source", ROOT / "public_people.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("sync_public_people_test_source", ROOT / "tools" / "sync_public_people.py")
sync = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(sync)
except ModuleNotFoundError as exc:
    if exc.name != "bs4":
        raise
    sync = None


class PublicPeopleSourceTests(unittest.TestCase):
    def test_singer_and_staff_files_keep_separate_typed_ids_and_staff_credits(self):
        counts = Counter()
        for filename, (kind, prefix) in source.CATALOGUES.items():
            payload = json.loads((ROOT / "data" / "people" / filename).read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], kind)
            for row in [*payload["records"], *payload["manual_records"]]:
                self.assertTrue(row["id"].startswith(prefix + ":"))
                self.assertTrue(row["sources"])
                self.assertTrue({"avatar_url", "source_url", "license", "image_author", "verified_at"} <= row.keys())
                if row.get("avatar_url"):
                    self.assertTrue(row["source_url"])
                    self.assertTrue(row["verified_at"])
                if kind == "utaite":
                    self.assertRegex(row["wiki_url"], SINGER_WIKI)
                else:
                    self.assertEqual(row["directory"], "imas_staff")
                    self.assertIn(row["credit_url"], row["sources"])
                    self.assertTrue(row["credit"] and row["roles"])
                if row.get("birthday"):
                    self.assertRegex(row["birthday"], r"^\d{2}-\d{2}$")
                    self.assertTrue(row.get("birthday_source"))
                self.assertNotIn("brand_code", row)
                counts[kind] += 1
        self.assertNotIn("vtuber", counts)
        self.assertFalse((ROOT / "data" / "people" / "vtubers.json").exists())
        self.assertGreater(counts["utaite"], 48)
        self.assertNotIn("mc:vocadb:45", plugin_module.ImasBirthdayPlugin._public_people_records())
        self.assertTrue(source.PUBLIC_PERSON_REDIRECTS)
        for ident, row in source.PUBLIC_PEOPLE_CATALOGUE.items():
            self.assertEqual(row["is_idolmaster"], row.get("directory") == "imas_staff")
            self.assertNotIn(ident, plugin_module.CHARACTER_PROFILES)
            self.assertNotIn(ident, plugin_module.CHARACTER_TANTOU_ICONS)

    def test_loader_rejects_imas_namespace_redirects_and_cross_file_identity_forgery(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "utaite.json"
            record = {"id": "ut:utaitedb:123", "name": "テスト", "aliases": [], "sources": ["https://example.org/profile"]}
            payload = {"schema": 1, "kind": "utaite", "records": [record], "redirects": {"月村手毬": record["id"]}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                source.load_catalogues(Path(folder))
            payload["redirects"] = {}
            payload["records"][0]["id"] = "qq:2002"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                source.load_catalogues(Path(folder))

    def test_japanese_singer_scope_requires_evidence_and_keeps_existing_accounts(self):
        singers = [r for r in source.PUBLIC_PEOPLE_CATALOGUE.values() if not r["archived"]
                   and (r["kind"] == "utaite" or "utaite" in r.get("roles", []))]
        for row in singers:
            self.assertRegex(row["wiki_url"], SINGER_WIKI)
            if not row.get("japan_source"):
                self.assertEqual(row["eligibility_basis"], "japanese_wiki")
                self.assertIn(row["wiki_directory_url"], row["sources"])
                self.assertTrue(row["wiki_checked_at"])
                self.assertTrue(row["avatar_url"])
            self.assertIn(row["wiki_url"], row["sources"])
        self.assertTrue({"nayuta", "柊優花", "藍月なくる", "そらる", "まふまふ", "赤ティン", "コゲ犬", "recog"} <= {r["name"] for r in singers})
        self.assertTrue(any("seesaawiki.jp/utaite/d/" in u for r in singers for u in r["sources"]))
        self.assertTrue(any("dic.nicovideo.jp/a/" in r["wiki_url"] for r in singers))
        self.assertFalse({"银临", "茶理理", "熊子", "ダズビー", "花近", "ほんこーん", "Mes", "SquaDus", "グリリ"} & {r["name"] for r in singers})
        self.assertEqual(len(singers), len({r["wiki_url"] for r in singers}))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_nico_directory_does_not_require_an_unpublished_hometown(self):
        import community_utaite as community
        entry = community.Entry("テスト", community.NICO + "/a/test", "nico", True, providers={12})
        artist = {"id": 12, "defaultName": "テスト", "status": "Finished", "names": [],
                  "webLinks": [{"category": "Official", "url": "https://x.com/owned"}],
                  "mainPicture": {"urlThumb": "https://static.utaitedb.net/test.png"}}
        indexes = ({"テスト": {12}}, {"twitter:owned": {12}}, {}, {})
        error = httpx.HTTPStatusError("Forbidden", request=httpx.Request("GET", entry.url),
                                     response=httpx.Response(403))
        def discover():
            with patch.object(community, "inspect_profile", side_effect=error):
                return list(community.community_profiles(None, {entry.url: entry}, {12: (artist, "2026-11-24")},
                                                         indexes, {}, {}, {}))
        result = discover()
        self.assertEqual(result[0][3], 12)
        self.assertEqual(result[0][-2:], ("", ""))
        self.assertEqual(entry.verification, "directory_reference")
        entry.providers.clear()
        self.assertEqual(discover(), [])  # Same name alone cannot attach a portrait.
        entry.providers.add(12)
        artist["mainPicture"] = {}
        self.assertEqual(discover(), [])

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_biography_uses_own_accounts_and_does_not_infer_origin_from_a_tv_reference(self):
        from utaite_wiki_profiles import profile
        header = "{{Utaite\n|NNDuserpage = 123\n|mylist1 = 456\n|Twitter = Owned\n" + (
            "|birthday = 10|birthmonth = 08|birthref = <ref>https://example.org/birthday</ref>\n}}\n")
        row = profile(header + "'''Test''' is an utaite.<ref>Singaporean TV</ref>\n"
                      "Collaborator https://twitter.com/other", "Test", "2026-11-24")
        self.assertFalse(row["foreign"])
        self.assertEqual(row["birthday"], "08-10")
        self.assertEqual(row["tokens"], {"twitter:owned", "niconico:user/123", "niconico:mylist/456"})
        foreign = profile(header + "'''Test''' is a Korean singer.", "Test", "2026-11-24")
        self.assertTrue(foreign["foreign"])
        commented = profile(header.rstrip() + "<!-- note -->'''Test''' is an Israeli youtaite.", "Test", "2026-11-24")
        self.assertTrue(commented["foreign"])
        sectioned = profile(header + "==Description==\n'''Test''' is a Chinese singer.", "Test", "2026-11-24")
        self.assertTrue(sectioned["foreign"])

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_biography_name_alone_cannot_verify_an_unrelated_portrait(self):
        import utaite_wiki_profiles as wiki
        artist = {"defaultName": "Test", "names": [],
                  "webLinks": [{"category": "Official", "url": "https://x.com/owned"}]}
        def document(owner):
            return json.dumps({"query": {"pages": [{"title": "Test", "revisions": [
                {"slots": {"main": {"content": "{{Utaite\n|Twitter = " + owner + "\n}}\n'''Test''' is an utaite."}}}]}]}})
        for owner, expected in (("other", set()), ("owned", {12})):
            with patch.object(wiki, "snapshot", return_value=(document(owner), "2026-11-24")):
                result = wiki.singer_biographies(None, {12: (artist, "2026-11-24")})
                self.assertEqual(set(result), expected)

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_japanese_page_does_not_make_a_foreign_singer_or_group_eligible(self):
        from sync_japanese_utaite import eligible
        def entity(country, kind="Q5"):
            def claim(identifier):
                return [{"mainsnak": {"datavalue": {"value": {"id": identifier}}}}]
            return {"claims": {"P27": claim(country), "P31": claim(kind)}}
        page = {"extract": "テストは日本の男性歌い手。4人組ユニットのメンバー。"}
        self.assertTrue(eligible(page, entity("Q17"), True, True))
        self.assertFalse(eligible(page, entity("Q148"), True, True))
        self.assertFalse(eligible(page, entity("Q17", "Q215380"), True, True))
        self.assertFalse(eligible({"extract": "テストは台湾の歌手。"}, {}, True, True))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_public_wiki_checks_article_identity_and_foreign_or_group_profiles(self):
        import community_utaite as community
        e = community.Entry("テスト", community.NICO + "/id/123", "nico", True)
        def article(number, description, table=""):
            body = (f'<meta property="og:url" content="{community.NICO}/id/{number}">'
                    f'<meta property="og:description" content="{description}"><div id="article"><body>'
                    + table + '<a href="https://www.nicovideo.jp/mylist/12">作品</a></body></div>')
            with patch.object(community, "snapshot", return_value=(body, "2026-11-19")):
                return community.inspect_profile(None, e)
        self.assertIsNone(article(456, "日本の歌い手"))
        self.assertIsNone(article(123, "テストは二人組の歌い手ユニット。"))
        self.assertIsNone(article(123, "テストはバーチャルシンガー。"))
        profile = article(123, "テストは歌い手。", "<table><tr><th>出身地</th><td>台湾</td></tr></table>")
        self.assertEqual(profile[-1], "foreign")
        profile = article(123, "テストは歌い手。", "<table><tr><th>出身地</th><td>福岡県</td></tr></table>")
        self.assertEqual((profile[0], profile[2], profile[3]), ("ut:nicopedia:123", {"niconico:mylist/12"}, "birthplace"))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_hometown_requires_same_performer_and_account_and_foreign_citizenship_wins(self):
        import community_utaite as community
        names = {"テスト"}
        origins = {"twitter:owned": {("テスト", "source")}, "twitter:shared": {("別人", "other")}}
        self.assertEqual(community.matching_hometowns(names, {"twitter:shared"}, origins), set())
        self.assertEqual(community.matching_hometowns(names, {"twitter:owned"}, origins), {"source"})
        entry = community.Entry("テスト", community.SEESAA + "d/test", "seesaa", True)
        artist = {"defaultName": "テスト", "names": [], "webLinks": [{"category": "Official", "url": "https://x.com/owned"}]}
        indexes = ({"テスト": {1}}, {"twitter:owned": {1}}, {1: {("P2002", "owned")}}, {})
        def discover(tokens, country):
            profile = ("ut:seesaa:123", entry.url, tokens, "")
            with patch.object(community, "inspect_profile", return_value=profile):
                return list(community.community_profiles(None, {1: entry}, {1: (artist, "2026-11-19")}, indexes,
                                                        {("P2002", "owned"): {"Q1"}}, {"Q1": country}, origins))
        self.assertFalse(discover({"twitter:other"}, ("source", "citizenship")))
        self.assertFalse(discover({"twitter:owned"}, ("", "foreign")))
        self.assertEqual(discover({"twitter:owned"}, ("", ""))[0][-2:], ("source", "birthplace"))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_second_wiki_and_new_portrait_keep_existing_registration(self):
        from contextlib import ExitStack
        import community_utaite as community
        stable = "ut:seesaa:13"
        source_url = community.SEESAA + "d/test"
        bio = "https://www.wikidata.org/wiki/Q1#P27"
        old = {"records": [{"id": stable, "name": "テスト", "aliases": ["旧名"], "sources": [source_url],
                            "wiki_url": source_url, "japan_source": bio}], "removed_ids": ["ut:utaitedb:12"]}
        entry = community.Entry("テスト", community.NICO + "/a/test", "nico", True)
        second = community.Entry("テスト", source_url, "seesaa", True)
        artist = {"id": 12, "artistType": "Utaite", "status": "Finished", "defaultName": "テスト",
                  "webLinks": [{"category": "Official", "url": "https://x.com/test"}],
                  "mainPicture": {"urlThumb": "https://static.utaitedb.net/test.png"}}
        profiles = [(entry, "ut:nicopedia:45", entry.url, 12, "Q1", {"twitter:test"}, bio, "citizenship"),
                    (second, stable, source_url, None, None, {"twitter:test"}, bio, "citizenship")]
        rows, redirects = {}, {}
        with ExitStack() as stack:
            for name, result in (("read_index", {}), ("collect_links", ({}, {}, {}, {})), ("geography", ({}, {})),
                                 ("origins_by_account", {}), ("community_profiles", profiles)):
                stack.enter_context(patch.object(community, name, return_value=result))
            community.add_community_singers(None, {12: (artist, "2026-11-19")}, rows, old, redirects, sync.parse_artist)
        self.assertEqual(list(rows), [stable])
        self.assertEqual(redirects["ut:utaitedb:12"], stable)
        self.assertIn("旧名", rows[stable]["aliases"])
        self.assertTrue(rows[stable]["avatar_url"])
        self.assertTrue({entry.url, source_url} <= set(rows[stable]["sources"]))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_parser_filters_unverified_or_wrong_type_entries_but_keeps_names_and_sources(self):
        row = {"id": 123, "artistType": "Producer", "defaultName": "テストP", "status": "Approved",
               "names": [{"value": "TestP"}], "webLinks": [{"category": "Official", "url": "https://example.org/test"}]}
        parsed = sync.parse_artist(row, "vocadb", "mc", "Producer")
        self.assertEqual(parsed["id"], "mc:vocadb:123")
        self.assertIn("TestP", parsed["aliases"])
        for change in ({"artistType": "Vocaloid"}, {"status": "Draft"}, {"webLinks": []}):
            self.assertIsNone(sync.parse_artist({**row, **change}, "vocadb", "mc", "Producer"))

    @unittest.skipIf(sync is None, "Optional catalogue maintenance dependency is not installed")
    def test_staff_refresh_requires_an_existing_verified_credit_and_preserves_edits(self):
        row = {"id": "mc:staff:test", "name": "髙田龍一", "directory": "imas_staff",
               "credit": "M@STERPIECE · 編曲", "credit_url": "https://example.org/credits",
               "aliases": ["高田龙一"], "avatar_url": None}
        data = {"records": [row], "manual_records": [], "overrides": {row["id"]: {"aliases": ["测试"]}}}
        with patch.object(sync, "snapshot", return_value=("<p>編曲：高田 龍一</p>", "2026-11-19")):
            result = sync.refresh_staff(None, data)
            self.assertEqual(result["records"], [row])
            self.assertEqual(result["overrides"][row["id"]]["aliases"], ["测试"])
            with self.assertRaises(ValueError):
                sync.refresh_staff(None, {"records": [{**row, "name": "同姓の別人"}]})
            with self.assertRaises(ValueError):
                sync.refresh_staff(None, {"records": [{**row, "directory": "vocadb"}]})

    def test_external_per_kind_overrides_extend_aliases_without_changing_bundled_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base, custom = root / "bundled", root / "persistent"
            base.mkdir(); custom.mkdir()
            payload = {"schema": 1, "kind": "utaite", "records": [{"id": "ut:utaitedb:123", "name": "公式名", "aliases": ["Official Name"], "sources": ["https://example.org/profile"]}]}
            original = json.dumps(payload)
            (base / "utaite.json").write_text(original, encoding="utf-8")
            (custom / "utaite.json").write_text(json.dumps({"schema": 1, "kind": "utaite", "overrides": {"ut:utaitedb:123": {"aliases": ["中文别名"]}}}), encoding="utf-8")
            records, _ = source.load_catalogues(base, custom)
            self.assertEqual(records["ut:utaitedb:123"]["aliases"], ["公式名", "Official Name", "中文别名"])
            self.assertEqual((base / "utaite.json").read_text(encoding="utf-8"), original)


class PublicPeopleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin, self.event = make_plugin(), Event()
        self.registry = plugin_module.PUBLIC_PEOPLE_CATALOGUE
        self.tanaka = "mc:manual:tanaka-hidekazu"
        self.mafu = "ut:utaitedb:1097"
        self.singer = next(ident for ident, row in self.registry.items() if row["name"] == "そらる")

    async def test_community_singers_can_be_added_queried_and_removed_under_their_original_ids(self):
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推", "赤ティン コゲ犬"))
        group = await self.plugin._tantou_group(self.event.unified_msg_origin)
        self.assertEqual(group[self.event.user], ["ut:utaitedb:1948", "ut:utaitedb:1176"])
        for name in ("赤ティン", "コゲ犬"):
            self.assertIn("本群 1人", await self.plugin._tantou_followers(self.event, name))
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "コゲ犬 赤ティン"), "成功减推了：コゲ犬、赤ティン")

    async def test_new_nico_singers_can_be_added_in_order_queried_and_removed(self):
        names = "灯油 柿チョコ un:c 実谷なな"
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推", names))
        group = await self.plugin._tantou_group(self.event.unified_msg_origin)
        self.assertEqual(group[self.event.user], ["ut:utaitedb:1039", "ut:utaitedb:1029", "ut:utaitedb:1106", "ut:utaitedb:1448"])
        for name in names.split():
            self.assertIn("本群 1人", await self.plugin._tantou_followers(self.event, name))
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", names), "成功减推了：灯油、柿チョコ、un:c、実谷なな")

    async def test_singer_display_name_wins_over_another_singers_romanized_alias(self):
        reply = await self.plugin._change_tantou(self.event, "加推", "nayuta 泣ユた")
        self.assertEqual(reply, "添加成功：nayuta、泣ユた")
        group = await self.plugin._tantou_group(self.event.unified_msg_origin)
        self.assertEqual(group[self.event.user], ["ut:utaitedb:24", "ut:utaitedb:12001"])
        self.assertIn("nayuta · 本群 1人", await self.plugin._tantou_followers(self.event, "Nayuta"))
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "nayuta"), "成功减推了：nayuta")
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], ["ut:utaitedb:12001"])

    def test_community_singer_with_existing_biography_or_composer_gets_one_identity(self):
        for name in ("ゴム", "みきとP", "ラマーズP", "ゆよゆっぺ"):
            matching = [r for r in self.registry.values() if not r["archived"] and name == r["name"]]
            self.assertEqual(len(matching), 1, name)
            self.assertTrue(matching[0]["kind"] == "utaite" or "utaite" in matching[0]["roles"])

    async def test_restored_singers_and_tanaka_resolve_and_survive_retired_id_filter(self):
        expected = ["ut:utaitedb:17118", "ut:utaitedb:24", "ut:utaitedb:1447", self.tanaka]
        reply = await self.plugin._change_tantou(self.event, "加推", "蓝月 nayuta 优花 田中秀和")
        self.assertIn("添加成功", reply)
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], expected)
        self.assertEqual(await self.plugin._tantou_sync_names(self.event.unified_msg_origin, self.event.user), expected)
        for ident, alias in zip(expected, ["藍月なくる", "奶油糖", "柊優花", "田中 秀和"]):
            self.assertFalse(self.plugin._removed_public_person(ident))
            self.assertIn("本群 1人", await self.plugin._tantou_followers(self.event, alias))
            self.assertTrue(self.registry[ident]["avatar_url"])
            self.assertIn(self.registry[ident]["source_url"], self.registry[ident]["sources"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, expected), [])

    async def test_legacy_registrations_render_and_can_be_removed_without_reopening_discovery(self):
        names = ["排骨教主", "C418"]
        ids = [next(i for i, r in self.registry.items() if r["name"] == name and r["archived"]) for name in names]
        self.assertFalse(set(ids) & set(await self.plugin._all_tantou_records()))
        key = "tantou_v1:" + self.event.unified_msg_origin
        self.plugin.storage[key] = {self.event.user: ids}
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], ids)
        self.assertEqual([self.plugin._tantou_display_name(i) for i in ids], names)
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", " ".join(names)), "成功减推了：排骨教主、C418")

    async def test_plain_add_remove_query_and_typed_names_use_the_separate_catalogues(self):
        reply = await self.plugin._change_tantou(self.event, "加推", "田中 秀和 まふまふ そらる")
        self.assertIn("添加成功", reply)
        names = (await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user]
        self.assertEqual(names, [self.tanaka, self.mafu, self.singer])
        self.assertIn("田中秀和 · 本群 1人", await self.plugin._tantou_followers(self.event, "Hidekazu Tanaka"))
        self.assertIn("偶像大师幕后", self.plugin._tantou_candidate_label(self.tanaka))
        self.assertIn("唱见", self.plugin._tantou_candidate_label(self.mafu))
        self.assertIn("唱见", self.plugin._tantou_candidate_label(self.singer))
        self.assertEqual(await self.plugin._change_tantou(self.event, "加推", "曲师:田中秀和"), "已经加推：田中秀和")
        self.assertNotIn("曲师:まふまふ", self.plugin._tantou_aliases(self.mafu))
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "そらる 田中秀和"), "成功减推了：そらる、田中秀和")
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], [self.mafu])

    async def test_staff_aliases_and_archived_producers_follow_the_normal_group_commands(self):
        ids = ["mc:staff:takayama-yusuke", "mc:staff:komino-hidefumi", self.tanaka]
        reply = await self.plugin._change_tantou(self.event, "加推", "高山佑介 小美野日出文 田中秀和")
        self.assertEqual(reply, "添加成功：高山祐介、小美野日出文、田中秀和")
        umo = self.event.unified_msg_origin
        self.assertEqual((await self.plugin._tantou_group(umo))[self.event.user], ids)
        for alias in ("高山P", "制作人:小美野日出文", "作曲家:田中秀和"):
            self.assertIn("本群 1人", await self.plugin._tantou_followers(self.event, alias))
        self.assertEqual(await self.plugin._tantou_birthday_users(umo, ids), [])
        retired = "mc:vocadb:45"
        self.assertNotIn(retired, await self.plugin._all_tantou_records())
        self.plugin.storage[f"tantou_v1:{umo}"] = {self.event.user: [retired, "mc:vocadb:876", self.tanaka]}
        followed = (await self.plugin._tantou_group(umo))[self.event.user]
        self.assertEqual(followed, [retired, "ut:nicopedia:4517914", self.tanaka])
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "DECO*27"), "成功减推了：DECO*27")

    def test_old_creator_portrait_cache_is_still_usable_on_existing_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.plugin.tantou_icons_dir = root
            ident = "mc:vocadb:45"
            cached = root / "people" / "music_creator" / (self.registry[ident]["cached_avatar_key"] + ".png")
            cached.parent.mkdir(parents=True)
            Image.new("RGB", (12, 12)).save(cached)
            self.assertEqual(self.plugin._tantou_icon_path(ident), cached)

    async def test_explicit_public_type_bypasses_name_fragment_ambiguity_without_changing_imas_priority(self):
        self.assertIn("添加成功", await self.plugin._change_tantou(self.event, "加推", "唱见:そらる"))
        names = (await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user]
        self.assertEqual(names, [self.singer])
        records = await self.plugin._all_tantou_records()
        self.assertEqual(self.plugin._tantou_candidates("橘", records)[:2], ["橘爱丽丝", "橘志狼"])
        self.assertEqual(self.plugin._tantou_alias_index(records)["momoka"], "樱井桃华")
        self.assertEqual(self.plugin._tantou_candidates("qzxv987", records), [])
        self.assertEqual(self.plugin._tantou_candidates("不存在的声优", records), [])

    async def test_verified_cross_source_references_collapse_old_ids_and_preserve_first_order(self):
        self.assertNotIn("ut:utaitedb:23675", plugin_module.PUBLIC_PERSON_REDIRECTS)
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {self.event.user: ["mc:vocadb:1459", "月村手毬", self.mafu, "ut:utaitedb:23675", "vt:hololive:hoshimachi-suisei"]}
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], [self.mafu, "月村手毬"])
        self.assertEqual(await self.plugin._tantou_sync_names(self.event.unified_msg_origin, self.event.user), [self.mafu, "月村手毬"])

    async def test_public_people_are_excluded_from_idol_rank_brands_birthday_roles_and_character_catalogue(self):
        names = ["月村手毬", "va:21149", self.tanaka, self.mafu, self.singer]
        self.plugin.storage[f"tantou_v1:{self.event.unified_msg_origin}"] = {self.event.user: names}
        characters = await self.plugin._tantou_records()
        self.assertFalse(set(names[2:]) & set(characters))
        self.assertEqual(self.plugin._visible_characters({"characters": [self.tanaka, "月村手毬"]}), ["月村手毬"])
        self.assertEqual(await self.plugin._tantou_birthday_users(self.event.unified_msg_origin, names[2:]), [])
        for ident in names[2:]:
            self.assertEqual(self.plugin._character_brand(ident), "PUBLIC_PERSON")
            self.assertFalse(self.plugin._voice_actor_id(ident))
        with patch.object(self.plugin, "_render_tantou_ranking", return_value="rank.png") as render, \
                patch.object(self.plugin, "_cache_tantou_member_avatars", new_callable=AsyncMock), \
                patch.object(self.plugin, "_tantou_ranking_group", return_value={"name": "本群"}):
            await self.plugin._tantou_ranking(self.event, kind="idol")
            self.assertEqual(render.call_args.args[0], [("月村手毬", 1)])
            self.assertNotIn("PUBLIC_PERSON", render.call_args.kwargs["brand_counts"])
            await self.plugin._tantou_ranking(self.event, kind="all")
            self.assertEqual(set(dict(render.call_args.args[0])), set(names))
        self.assertEqual(await self.plugin._tantou_llm_commentary(self.event.unified_msg_origin, names[2:]), "")

    async def test_public_only_card_renders_without_imas_logos_or_project_bars(self):
        paths = self.plugin._render_tantou_cards("测试P", [self.tanaka, self.mafu, self.singer])
        self.assertEqual(len(paths), 1)
        self.addCleanup(lambda: Path(paths[0]).unlink(missing_ok=True))
        with Image.open(paths[0]) as image:
            self.assertEqual(image.size, (1800, 1080))

    async def test_public_portraits_are_bounded_and_reject_arbitrary_hosts_and_redirects(self):
        data = io.BytesIO()
        Image.new("RGB", (1200, 1200), "red").save(data, format="PNG")
        original = httpx.AsyncClient
        prepare = plugin_module.ImasBirthdayPlugin._prepare_tantou_icons
        with tempfile.TemporaryDirectory() as folder:
            self.plugin.tantou_icons_dir = Path(folder)
            requests = []
            def response(request):
                requests.append(request)
                return httpx.Response(200, content=data.getvalue())
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
                await prepare(self.plugin, [self.tanaka])
                await prepare(self.plugin, [self.tanaka])
            self.assertEqual(len(requests), 1)
            self.assertIn(requests[0].url.host, {"thumb.wikimedia.org", "upload.wikimedia.org"})
            path = self.plugin._tantou_icon_path(self.tanaka)
            with Image.open(path) as image:
                self.assertEqual(image.size, (960, 960))
                self.assertEqual(image.format, "PNG")
            path.unlink()
            for url in ("http://127.0.0.1/x", "https://images.ctfassets.net.evil.example/x", "https://user:pass@images.ctfassets.net/x"):
                replacement = {**self.registry[self.tanaka], "avatar_url": url}
                with patch.dict(self.registry, {self.tanaka: replacement}), patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
                    await prepare(self.plugin, [self.tanaka])
                self.assertFalse(self.plugin._tantou_icon_path(self.tanaka))
            self.assertEqual(len(requests), 1)
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/x"})), **kw)):
                await prepare(self.plugin, [self.tanaka])
            self.assertFalse(self.plugin._tantou_icon_path(self.tanaka))
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
                await prepare(self.plugin, [self.mafu])
            with Image.open(self.plugin._tantou_icon_path(self.mafu)) as image:
                self.assertEqual(image.size, (320, 320))
