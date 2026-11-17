"""Independent music metadata, saved crops, source bounds, and update-safe storage."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from PIL import Image

import test_asset_editor as assets_test
from test_asset_editor import editor_module
from test_tantou import make_plugin, plugin_module, Event

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("enrich_test_source", ROOT / "tools" / "enrich_music_people.py")
enrich = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(enrich)
except ModuleNotFoundError as exc:
    if exc.name != "bs4":
        raise
    enrich = None


class PeopleEditorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="imasbd-people-editor-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.plugin = make_plugin()
        # Keep the test override out of the shared source registry.
        self.registry = copy.deepcopy(plugin_module.PUBLIC_PEOPLE_CATALOGUE)
        self.registry_patch = patch.object(plugin_module, "PUBLIC_PEOPLE_CATALOGUE", self.registry)
        self.registry_patch.start(); self.addCleanup(self.registry_patch.stop)
        self.ident = "mc:manual:tanaka-hidekazu"
        self.editor = editor_module.PeopleEditor(self.plugin, self.root, self.registry)
        self.plugin.people_editor = self.editor

    async def save(self, **changes):
        return await self.editor.save({"id": self.ident, "name": "田中秀和", "aliases": ["测试曲师"],
                                       "birthday": None, "revision": self.editor.records.get(self.ident, {}).get("revision", ""), **changes})

    async def test_birthday_name_alias_crop_and_reload_do_not_touch_character_data(self):
        token = self.editor.import_image(assets_test.EditorTests.picture())["source"]
        await self.save(name="田中 秀和", birthday="02-29", image={"source": token, "x": 1, "y": .5, "zoom": 1, "fit": "cover"})
        self.assertEqual(self.plugin._tantou_display_name(self.ident), "田中 秀和")
        self.assertEqual(self.plugin._tantou_alias_index([self.ident])["测试曲师"], self.ident)
        self.assertEqual(self.registry[self.ident]["birthday"], "02-29")
        self.assertIn("田中秀和", self.registry[self.ident]["aliases"])
        path = self.plugin._editor_image(self.ident, "tantou", (100, 90))
        with Image.open(path) as image:
            self.assertEqual(image.getpixel((50, 45)), (0, 0, 255, 255))
        restarted = editor_module.PeopleEditor(self.plugin, self.root, self.registry)
        self.assertEqual(restarted.records[self.ident]["birthday"], "02-29")
        saved = json.loads((self.root / "music_creators.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["kind"], "music_creator")
        self.assertFalse((self.root / "characters.json").exists())
        self.assertFalse((self.root / "utaite.json").exists())
        self.assertNotIn(self.ident, await self.plugin._tantou_records())

    async def test_conflicts_and_invalid_dates_and_crops_are_rejected_before_saving(self):
        for changes in ({"birthday": "02-30"}, {"id": "vt:official:test"}, {"id": "月村手毬"}, {"aliases": "alias"},
                        {"image": {"source": "../escape", "x": .5, "y": .5, "zoom": 1}},
                        {"image": {"x": .5, "y": .5, "zoom": .5, "fit": "cover"}}):
            with self.assertRaises(ValueError):
                await self.save(**changes)
        self.assertFalse((self.root / "music_creators.json").exists())
        await self.save(birthday="")
        with self.assertRaises(ValueError):
            await self.save(revision="outdated", birthday="01-01")
        self.assertEqual(self.registry[self.ident]["birthday"], "")
        await self.save(birthday=None)
        self.assertEqual(self.registry[self.ident].get("birthday"), self.editor.base[self.ident].get("birthday"))

    async def test_contain_crop_keeps_entire_photo_and_restore_keeps_original_copy(self):
        token = self.editor.import_image(assets_test.EditorTests.picture())["source"]
        await self.save(image={"source": token, "x": .5, "y": .5, "zoom": .5, "fit": "contain"})
        with Image.open(self.plugin._editor_image(self.ident, "tantou", (100, 90))) as image:
            self.assertEqual(image.getpixel((0, 0)), (243, 245, 248, 255))
            self.assertEqual(image.getpixel((30, 45)), (255, 0, 0, 255))
            self.assertEqual(image.getpixel((70, 45)), (0, 0, 255, 255))
        await self.save(image=None)
        self.assertIsNone(self.plugin._editor_image(self.ident, "tantou"))
        self.assertTrue(self.editor.source_path(token).exists())

    async def test_missing_manual_original_can_be_replaced_without_breaking_cards(self):
        token = self.editor.import_image(assets_test.EditorTests.picture())["source"]
        await self.save(image={"source": token, "x": .5, "y": .5, "zoom": 1, "fit": "contain"})
        self.editor.source_path(token).unlink()
        self.assertIsNone(self.plugin._editor_image(self.ident, "tantou"))
        self.assertEqual((await self.editor.detail(self.ident))["image"]["image"], "")
        self.assertFalse(next(row for row in (await self.editor.list_records())["people"] if row["id"] == self.ident)["has_image"])
        replacement = self.editor.import_image(assets_test.EditorTests.picture())["source"]
        await self.save(image={"source": replacement, "x": .5, "y": .5, "zoom": 1, "fit": "contain"})
        self.assertTrue(self.plugin._editor_image(self.ident, "tantou").exists())

    async def test_moegirl_photo_fetch_rejects_redirects_and_arbitrary_urls(self):
        original = httpx.AsyncClient
        requests = []
        def response(request):
            requests.append(request)
            return httpx.Response(200, content=assets_test.EditorTests.picture())
        self.registry[self.ident]["moegirl"] = {"image_url": "https://storage.moegirl.org.cn/moegirl/commons/a/photo.png"}
        with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
            result = await self.editor.load_source(self.ident)
        self.assertTrue(self.editor.source_path(result["source"]).exists())
        self.assertNotIn(self.ident, self.editor.records)
        for url in ("http://127.0.0.1/", "https://storage.moegirl.org.cn.evil.test/x", "https://user:pass@storage.moegirl.org.cn/x"):
            self.registry[self.ident]["moegirl"]["image_url"] = url
            with self.assertRaises(ValueError):
                await self.editor.load_source(self.ident)
        self.assertEqual(len(requests), 1)
        self.registry[self.ident]["moegirl"]["image_url"] = "https://storage.moegirl.org.cn/x"
        with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/x"})), **kw)):
            with self.assertRaises(ValueError):
                await self.editor.load_source(self.ident)

    async def test_removed_vtuber_records_are_backed_up_and_no_longer_sync_or_rank(self):
        event = Event()
        key = "tantou_v1:" + event.unified_msg_origin
        before = {event.user: ["月村手毬", "vt:hololive:hoshimachi-suisei", "ut:utaitedb:23675", self.ident]}
        self.plugin.storage[key] = copy.deepcopy(before)
        group = await self.plugin._tantou_group(event.unified_msg_origin)
        self.assertEqual(group[event.user], ["月村手毬", self.ident])
        self.assertEqual(self.plugin.storage["tantou_before_vtuber_removal_v1:" + event.unified_msg_origin], before)
        self.assertEqual(await self.plugin._tantou_sync_names(event.unified_msg_origin, event.user), group[event.user])
        self.assertFalse(any(row["kind"] == "vtuber" for row in self.registry.values()))


@unittest.skipIf(enrich is None, "Optional enrichment dependency is not installed")
class EnrichmentTests(unittest.TestCase):
    def profile(self, value, categories='"网络歌手"'):
        return '<script>{"wgCategories":[' + categories + ']}</script><template><h1 id="firstHeading">テスト</h1><table class="infobox"><tr><td>生日</td><td>' + value + '</td></tr><tr><td><img width="280" height="400" src="https://storage.moegirl.org.cn/moegirl/commons/a/photo.jpg!/fw/280"></td></tr></table></template>'

    def test_public_infobox_template_dates_and_images_are_parsed_without_guessing(self):
        profile = enrich.parse_profile(self.profile("1991年10月18日"), "https://zh.moegirl.org.cn/Test")
        self.assertEqual(profile["birthday"], "10-18")
        self.assertEqual(profile["image_url"], "https://storage.moegirl.org.cn/moegirl/commons/a/photo.jpg!/fw/640")
        for value in ("未知", "2月30日", "1月2日或1月3日", "不公开（1月2日为纪念日）"):
            self.assertEqual(enrich.parse_profile(self.profile(value), "https://zh.moegirl.org.cn/Test")["birthday"], "")
        self.assertTrue(enrich.parse_profile(self.profile("1月2日", '"网络歌手","虚拟UP主"'), "https://zh.moegirl.org.cn/Test")["excluded"])
