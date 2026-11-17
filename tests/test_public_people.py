"""Physical catalogue isolation, public identities and bounded portrait fetching."""
import importlib.util
import io
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from test_tantou import Event, make_plugin, plugin_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_people_test_source", ROOT / "public_people.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
spec = importlib.util.spec_from_file_location("sync_public_people_test_source", ROOT / "tools" / "sync_public_people.py")
sync = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(sync)
except ModuleNotFoundError as exc:
    if exc.name != "bs4":
        raise
    sync = None


class PublicPeopleSourceTests(unittest.TestCase):
    def test_two_physical_files_have_typed_stable_ids_sources_and_no_imas_data(self):
        counts = Counter()
        for filename, (kind, prefix) in source.CATALOGUES.items():
            payload = json.loads((ROOT / "data" / "people" / filename).read_text(encoding="utf-8"))
            self.assertEqual(payload["kind"], kind)
            for row in [*payload["records"], *payload["manual_records"]]:
                self.assertTrue(row["id"].startswith(prefix + ":"))
                self.assertTrue(row["sources"])
                self.assertTrue(row.get("moegirl", {}).get("url"))
                if row.get("birthday"):
                    self.assertRegex(row["birthday"], r"^\d{2}-\d{2}$")
                    self.assertTrue(row.get("birthday_source"))
                self.assertNotIn("brand_code", row)
                counts[kind] += 1
        self.assertNotIn("vtuber", counts)
        self.assertFalse((ROOT / "data" / "people" / "vtubers.json").exists())
        self.assertEqual(counts["utaite"], 45)
        self.assertEqual(counts["music_creator"], 253)
        self.assertTrue(source.PUBLIC_PERSON_REDIRECTS)
        for ident, row in source.PUBLIC_PEOPLE_CATALOGUE.items():
            self.assertFalse(row["is_idolmaster"])
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
    def test_rebuild_keeps_existing_ids_manual_records_and_per_file_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "utaite.json"
            old = {"id": "ut:utaitedb:123", "name": "旧名", "aliases": [], "sources": ["https://utaitedb.net/Ar/123"]}
            manual = {**old, "id": "ut:manual:test"}
            old.update({"birthday": "01-02", "birthday_source": "https://zh.moegirl.org.cn/Test", "moegirl": {"title": "Test"}})
            payload = {"records": [old], "manual_records": [manual], "overrides": {old["id"]: {"aliases": ["中文别名"]}}, "enrichment": {"source": "moegirl"}, "removed_ids": ["ut:utaitedb:9"]}
            path.write_text(json.dumps(payload), encoding="utf-8")
            sync.write_catalogue(path, "utaite", [], 0)
            rebuilt = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["records"][0]["id"], old["id"])
            self.assertEqual(rebuilt["manual_records"], [manual])
            self.assertEqual(rebuilt["overrides"], payload["overrides"])
            self.assertEqual(rebuilt["enrichment"], payload["enrichment"])
            refreshed = {"id": old["id"], "name": "新名", "aliases": ["New Name"], "sources": ["https://utaitedb.net/Ar/123"]}
            sync.write_catalogue(path, "utaite", [refreshed, {**refreshed, "id": "ut:utaitedb:9"}], 2)
            rebuilt = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(rebuilt["records"]), 1)
            self.assertEqual(rebuilt["records"][0]["birthday"], "01-02")
            self.assertIn("New Name", rebuilt["records"][0]["aliases"])
            self.assertEqual(rebuilt["removed_ids"], payload["removed_ids"])

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

    async def test_plain_add_remove_query_and_typed_names_use_the_separate_catalogues(self):
        reply = await self.plugin._change_tantou(self.event, "加推", "田中 秀和 まふまふ そらる")
        self.assertIn("添加成功", reply)
        names = (await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user]
        self.assertEqual(names, [self.tanaka, self.mafu, self.singer])
        self.assertIn("田中秀和 · 本群 1人", await self.plugin._tantou_followers(self.event, "Hidekazu Tanaka"))
        self.assertIn("音乐创作者", self.plugin._tantou_candidate_label(self.tanaka))
        self.assertIn("唱见", self.plugin._tantou_candidate_label(self.mafu))
        self.assertIn("唱见", self.plugin._tantou_candidate_label(self.singer))
        self.assertEqual(await self.plugin._change_tantou(self.event, "加推", "曲师:まふまふ"), "已经加推：まふまふ")
        self.assertEqual(await self.plugin._change_tantou(self.event, "减推", "そらる 田中秀和"), "成功减推了：そらる、田中秀和")
        self.assertEqual((await self.plugin._tantou_group(self.event.unified_msg_origin))[self.event.user], [self.mafu])

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
        Image.new("RGB", (640, 640), "red").save(data, format="PNG")
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
            self.assertEqual(requests[0].url.host, "images.ctfassets.net")
            path = self.plugin._tantou_icon_path(self.tanaka)
            with Image.open(path) as image:
                self.assertEqual(image.size, (320, 320))
                self.assertEqual(image.format, "PNG")
            path.unlink()
            for url in ("http://127.0.0.1/x", "https://images.ctfassets.net.evil.example/x", "https://user:pass@images.ctfassets.net/x"):
                replacement = {**self.registry[self.tanaka], "image_url": url}
                with patch.dict(self.registry, {self.tanaka: replacement}), patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
                    await prepare(self.plugin, [self.tanaka])
                self.assertFalse(self.plugin._tantou_icon_path(self.tanaka))
            self.assertEqual(len(requests), 1)
            with patch.object(plugin_module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/x"})), **kw)):
                await prepare(self.plugin, [self.tanaka])
            self.assertFalse(self.plugin._tantou_icon_path(self.tanaka))
