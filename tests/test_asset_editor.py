"""Exercise persisted edits through the bot's normal lookup and rendering paths."""
import copy
import json
import importlib.util
import base64
import sys
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw
from test_tantou import Event, make_plugin, plugin_module

spec = importlib.util.spec_from_file_location("editor_under_test", Path(__file__).resolve().parents[1] / "asset_editor.py")
editor_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(editor_module)


class EditorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="imasbd-editor-test-")
        self.addCleanup(self.folder.cleanup)
        self.plugin = make_plugin()
        self.root = Path(self.folder.name)
        self.editor = self.attach(self.plugin)

    def attach(self, plugin):
        plugin.editor = editor_module.AssetEditor(plugin, self.root, plugin_module.BRAND_LABELS, plugin_module.CHARACTER_PROFILES)
        return plugin.editor

    async def save(self, name="月村手毬", **changes):
        payload = {"name": name, "revision": self.editor.records.get(name, {}).get("revision", ""), **changes}
        return await self.editor.save(payload)

    @staticmethod
    def picture():
        image = Image.new("RGB", (600, 300), "red")
        ImageDraw.Draw(image).rectangle((300, 0, 599, 299), fill="blue")
        data = BytesIO()
        image.save(data, format="PNG")
        return data.getvalue()

    async def test_new_character_survives_restart_and_can_be_registered_by_alias(self):
        await self.save("测试角色", name_jp="テスト キャラ", aliases=["测试推"], brand="SIDEM", birthday="02-29")
        restarted = make_plugin()
        self.attach(restarted)
        result = await restarted._change_tantou(Event(), "加推", "测试推")
        self.assertIn("添加成功", result)
        self.assertEqual(restarted._tantou_display_name("测试角色"), "テスト キャラ")
        self.assertEqual(restarted._character_brand("测试角色"), "SIDEM")
        self.assertEqual(restarted._lookup_character_profile("测试角色")["birthday"], "02-29")
        self.assertIn("测试角色", await restarted._tantou_records())

    async def test_chief_editor_merge_preserves_both_crops_and_backup_and_is_idempotent(self):
        token = self.editor.import_image(self.picture())["source"]
        crop = lambda x: {"source": token, "x": x, "y": .5, "zoom": 1}
        original = {"schema": 1, "records": {
            "百万ChiefP": {"name_jp": "チーフプロデューサー", "images": {"tantou": crop(0)}, "revision": "old"},
            "赤羽根P": {"name_jp": "プロデューサー", "images": {"birthday": crop(1)}, "revision": "current"},
        }}
        self.editor.file.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        editor = self.attach(self.plugin)
        self.assertNotIn("百万ChiefP", editor.records)
        self.assertEqual(editor.records["赤羽根P"]["revision"], "current")
        self.assertEqual(self.plugin._tantou_display_name("赤羽根P"), "赤羽根P")
        self.assertTrue(editor.source_path(token).is_file())
        for kind, color in (("tantou", (255, 0, 0, 255)), ("birthday", (0, 0, 255, 255))):
            with Image.open(editor.render_image("赤羽根P", kind, (100, 100))) as image:
                self.assertEqual(image.getpixel((50, 50)), color)
        backup = self.root / "characters.before-chief-merge.json"
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
        saved = editor.file.read_bytes()
        again = self.attach(self.plugin)
        self.assertEqual(again.file.read_bytes(), saved)
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), original)
        rows = (await again.list_records())["characters"]
        self.assertEqual(sum(row["name"] == "赤羽根P" for row in rows), 1)
        self.assertFalse(any(row["name"] == "百万ChiefP" for row in rows))
        self.assertEqual((await again.detail("百万ChiefP"))["name"], "赤羽根P")
        producer = next(row for row in rows if row["name"] == "百万动画P")
        self.assertEqual(producer["display_name"], "中村P")
        self.assertEqual((await again.detail("百万动画P"))["display_name"], "中村P")

    async def test_birthday_override_moves_removes_and_restores_without_changing_source(self):
        data = {"06-03": {"characters": ["月村手毬", "花海佑芽"], "seiyuu": ["声优"], "events": [], "related_people": []}}
        original = copy.deepcopy(data)
        self.plugin._get_source_birthdays = AsyncMock(return_value=data)
        await self.save(birthday="02-29")
        result = await self.plugin._get_birthdays()
        self.assertEqual(result["06-03"]["characters"], ["花海佑芽"])
        self.assertEqual(result["02-29"]["characters"], ["月村手毬"])
        self.assertEqual(result["06-03"]["seiyuu"], ["声优"])
        self.assertEqual(data, original)
        await self.save(birthday="")
        result = await self.plugin._get_birthdays()
        self.assertFalse(any("月村手毬" in entry["characters"] for entry in result.values()))
        await self.save(birthday=None)
        self.assertEqual(await self.plugin._get_birthdays(), original)

    async def test_custom_birthday_works_when_remote_unavailable_without_cache(self):
        await self.save(birthday="04-16")
        self.plugin._get_source_birthdays = AsyncMock(side_effect=RuntimeError("offline"))
        self.assertEqual((await self.plugin._get_birthdays())["04-16"]["characters"], ["月村手毬"])

    async def test_images_are_independent_crops_and_do_not_replace_original_files(self):
        original = self.root / "old.png"
        original.write_bytes(self.picture())
        token = self.editor.import_image(original.read_bytes())["source"]
        await self.save(images={
            "birthday": {"source": token, "x": 0, "y": .5, "zoom": 1},
            "tantou": {"source": token, "x": 1, "y": .5, "zoom": 1},
        })
        self.plugin.config["card_asset_mode"] = "portrait"
        item = self.plugin._card_item("月村手毬", image_size=(214, 300))
        self.assertEqual(item["asset_kind"], "image")
        with Image.open(item["path"]) as image:
            self.assertEqual(image.size, (214, 300))
            self.assertEqual(image.getpixel((100, 100)), (255, 0, 0, 255))
        canvas = Image.new("RGBA", (200, 200))
        self.plugin._draw_tantou_avatar(canvas, "月村手毬", 0, 0, 200)
        self.assertEqual(canvas.getpixel((100, 100)), (0, 0, 255, 255))
        self.assertEqual(original.read_bytes(), self.picture())
        new_plugin = make_plugin()
        self.attach(new_plugin)
        self.assertEqual(new_plugin._character_image_path("月村手毬"), self.plugin._character_image_path("月村手毬"))
        await self.save(images={"birthday": None})
        self.assertIsNone(self.editor.render_image("月村手毬", "birthday"))
        self.assertIsNotNone(self.editor.render_image("月村手毬", "tantou"))

    async def test_existing_local_image_is_copied_only_when_crop_saved(self):
        folder = self.root / "legacy"
        folder.mkdir()
        original = folder / "custom.png"
        original.write_bytes(self.picture())
        self.plugin.assets_dir = folder
        with patch.dict(plugin_module.CHARACTER_IMAGE_ASSETS, {"美作武史": "custom.png"}):
            detail = await self.editor.detail("美作武史")
            self.assertTrue(detail["images"]["birthday"]["image"].startswith("data:image/png"))
            self.assertFalse(self.editor.file.exists())
            await self.save("美作武史", images={"birthday": {"source": "", "x": 1, "y": .5, "zoom": 1}})
            self.assertNotEqual(self.plugin._character_image_path("美作武史"), original)
            await self.save("美作武史", images={"birthday": None})
            self.assertEqual(self.plugin._character_image_path("美作武史"), original)
            self.assertEqual(original.read_bytes(), self.picture())

    async def test_custom_member_and_blank_avatars_share_official_rounded_outline(self):
        opaque = self.root / "opaque.png"
        Image.new("RGB", (400, 400), "red").save(opaque)
        token = self.editor.import_image(opaque.read_bytes())["source"]
        await self.save(images={"tantou": {"source": token, "x": .5, "y": .5, "zoom": 1}})
        with Image.open(self.editor.render_image("月村手毬", "tantou")) as custom:
            self.assertEqual(custom.size, (600, 540))
        outlines = []
        for name, path in (("月村手毬", None), ("qq:2002", opaque), ("未收录占位", None)):
            canvas = Image.new("RGBA", (170, 170))
            self.plugin._draw_tantou_avatar(canvas, name, 0, 0, 170, avatar_path=path)
            alpha = canvas.getchannel("A")
            self.assertEqual(alpha.getbbox(), (0, 8, 170, 161))
            self.assertEqual(alpha.getpixel((43, 8)), 0)  # Rounded top corner, unlike the old polygon.
            self.assertGreater(alpha.getpixel((85, 8)), 200)
            self.assertTrue(any(0 < value < 255 for value in alpha.tobytes()))
            outlines.append(alpha.tobytes())
        self.assertEqual(outlines[0], outlines[1])
        self.assertEqual(outlines[1], outlines[2])

    async def test_invalid_input_never_changes_saved_data(self):
        await self.save(birthday="06-03")
        original = self.editor.file.read_bytes()
        invalid = [dict(birthday="02-30"), dict(birthday="6-3"), dict(brand="unknown"),
                   dict(name="qq:12345"), dict(name="角色（附注）"), dict(aliases=["qq:12345"]),
                   dict(images={"birthday": {"source": "../../secret", "x": 0, "y": 0, "zoom": 1}}),
                   dict(images={"birthday": {"source": "a"*64, "x": float("nan"), "y": 0, "zoom": 1}}),
                   dict(images={"birthday": {"source": "a"*64, "x": 0, "y": 0, "zoom": 99}}),
                   dict(images={"arbitrary": {}})]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.editor.save({"name": "月村手毬", "revision": self.editor.records["月村手毬"]["revision"], **changes})
            self.assertEqual(self.editor.file.read_bytes(), original)

    async def test_stale_save_is_rejected_and_alias_cannot_create_duplicate(self):
        await self.save(birthday="04-16")
        with self.assertRaisesRegex(ValueError, "其他页面"):
            await self.editor.save({"name": "月村手毬", "revision": "", "birthday": "05-01"})
        with self.assertRaisesRegex(ValueError, "现有角色"):
            await self.save("月村 手毬", name_jp="月村 手毬")
        self.assertEqual(self.editor.records["月村手毬"]["birthday"], "04-16")

    async def test_uploaded_images_are_validated_and_reencoded(self):
        for data in [b'<svg onload="alert(1)"></svg>', b'not an image', b'x'*(editor_module.MAX_UPLOAD+1)]:
            with self.assertRaises(ValueError):
                self.editor.import_image(data)
        oversized = Image.new("1", (9000, 10))
        data = BytesIO(); oversized.save(data, format="PNG")
        with self.assertRaises(ValueError):
            self.editor.import_image(data.getvalue())
        result = self.editor.import_image(self.picture())
        self.assertRegex(result["source"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.editor.source_path(result["source"]).suffix, ".png")

    async def test_preview_uses_actual_renderers_and_does_not_send_messages(self):
        await self.save(birthday="04-16", name_jp="月村 手毬")
        result = await self.editor.preview("月村手毬")
        self.assertTrue(result["birthday"].startswith("data:image/png;base64,"))
        self.assertTrue(result["tantou"].startswith("data:image/png;base64,"))
        self.assertEqual(self.plugin.sent, [])

    async def test_all_birthday_preview_layouts_match_saved_crop(self):
        token = self.editor.import_image(self.picture())["source"]
        await self.save(images={"birthday": {"source": token, "x": .8, "y": .2, "zoom": 1.4}})
        layouts = (await self.editor.list_records())["birthday_layouts"]
        for columns in (1, 2, 3):
            layout = layouts[str(columns)]
            width, height = layout["item_width"], layout["portrait_height"]
            result = await self.editor.preview("月村手毬", str(columns))
            with Image.open(BytesIO(base64.b64decode(result["birthday"].split(",", 1)[1]))) as card:
                left = (card.width - (width * columns + layout["grid_gap"] * (columns - 1))) // 2
                top = layout["card_padding"] + 108
                with Image.open(self.editor.render_image("月村手毬", "birthday", (width, height))) as crop:
                    # Ignore the card's rounded border, compare its actual image content.
                    self.assertEqual(card.convert("RGB").crop((left+20, top+20, left+width-20, top+height-20)).tobytes(),
                                     crop.convert("RGB").crop((20, 20, width-20, height-20)).tobytes())
        with self.assertRaises(ValueError):
            await self.editor.preview("月村手毬", "999")

    async def test_web_handlers_use_plugin_prefix_and_reject_bad_payload(self):
        routes = []
        request = types.SimpleNamespace(json=AsyncMock(return_value={"name": "qq:123"}))
        module = types.ModuleType("astrbot.api.web")
        module.request = request
        module.json_response = lambda data: data
        module.error_response = lambda message, status_code: {"status": "error", "message": message, "code": status_code}
        with patch.dict(sys.modules, {"astrbot.api.web": module}):
            self.assertTrue(self.editor.register(types.SimpleNamespace(register_web_api=lambda *args: routes.append(args))))
            self.assertEqual(len(routes), 5)
            for route, handler, methods, description in routes:
                self.assertTrue(route.startswith("/astrbot_plugin_imas_birthday/editor/"))
                if route.endswith("/save"):
                    result = await handler()
                    self.assertEqual(result["code"], 400)
            self.assertFalse(self.editor.file.exists())


if __name__ == "__main__":
    unittest.main()
