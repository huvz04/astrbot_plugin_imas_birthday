"""Keep supplementary name collection tied to verified actor identity."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "tools" / "fetch_voice_actor_names.py"
spec = importlib.util.spec_from_file_location("voice_names_source", path)
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class NameSourceTests(unittest.TestCase):
    def test_ruby_and_reference_text_do_not_become_names(self):
        markup = '''<table class="moe-infobox"><tr><td>姓名</td><td>
        <ruby><rb>伊達<span class="template-ruby-hidden">（</span></rb><rt>だて</rt>
        <span class="template-ruby-hidden">）</span></ruby> さゆり<br>(Date Sayuri)<sup>[1]</sup>
        </td></tr><tr><td>昵称</td><td><s>错误玩笑名</s>Something</td></tr></table>'''
        result = source.parse_names(markup, "伊達 さゆり", "伊达小百合")
        self.assertIn("伊达小百合", result["aliases"])
        self.assertEqual(result["romanized_names"], ["Date Sayuri"])
        self.assertNotIn("だて", "".join(result["aliases"]))
        self.assertNotIn("Something", "".join(result["aliases"]))
        self.assertNotIn("错误玩笑名", "".join(result["aliases"]))

    def test_wrong_person_and_unreadable_pages_cannot_supply_aliases(self):
        with self.assertRaises(ValueError):
            source.parse_names('<table class="moe-infobox"><tr><td>姓名</td><td>別 人</td></tr></table>', "伊達 さゆり", "伊达小百合")
        with self.assertRaises(ValueError):
            source.parse_names('<html>Verification required</html>', "伊達 さゆり", "伊达小百合")
