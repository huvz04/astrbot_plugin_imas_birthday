"""Own historical registrations, overwrite consent and target-group member checks."""
import asyncio
import copy
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_tantou import At, Event, Plain, make_plugin
from test_tantou_members import install_bot, member


class TantouSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = make_plugin()
        self.plugin.plugin_id = "dache/astrbot_plugin_imas_birthday"
        self.event = Event()
        self.target = self.event.unified_msg_origin
        self.a = "bot:GroupMessage:191233724"
        self.b = "bot:GroupMessage:971234519"
        self.plugin.storage.update({
            f"tantou_v1:{self.target}": {"1001": ["花海佑芽"], "1002": ["如月千早"]},
            f"tantou_v1:{self.a}": {"1001": ["月村手毬", "va:21149", "月村 手毬"], "1002": ["天海春香"]},
            f"tantou_v1:{self.b}": {"1001": ["天海春香"]},
            "tantou_v1:bot:GroupMessage:333333333": {"1001": [], "1002": ["月村手毬"]},
            "tantou_v1:bot:FriendMessage:1001": {"1001": ["月村手毬"]},
            "tantou_v1:other_platform:GroupMessage:555555555": {"1001": ["月村手毬"]},
            f"tantou_group_header_v1:{self.a}": {"name": "Alpha秘密群"},
            f"tantou_group_header_v1:{self.b}": {"name": "Beta秘密群"},
            f"tantou_profiles_v1:{self.target}": {"1001": {"name": "本群CN", "nickname": "本群昵称"}},
            f"tantou_profiles_v1:{self.a}": {"1001": {"name": "来源CN"}},
        })
        self.store = types.SimpleNamespace(range_get_async=AsyncMock(side_effect=self.preference_rows))
        core = types.ModuleType("astrbot.core")
        core.sp = self.store
        self.modules = patch.dict(sys.modules, {"astrbot.core": core})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def preference_rows(self, scope, plugin_id):
        return [types.SimpleNamespace(scope=scope, scope_id=plugin_id, key=key, value={"val": copy.deepcopy(value)})
                for key, value in self.plugin.storage.items()]

    def follows(self):
        return self.plugin.storage[f"tantou_v1:{self.target}"]["1001"]

    async def start(self):
        return await self.plugin._tantou_sync_action(self.event)

    async def choose(self, number="1"):
        return await self.plugin._tantou_sync_action(self.event, number)

    async def confirm(self):
        return await self.plugin._tantou_sync_action(self.event, "确认")

    async def test_only_saved_own_other_groups_appear_masked_without_common_group_enumeration(self):
        async def info(action, **kwargs):
            self.assertEqual(action, "get_group_info")
            return {"group_id": kwargs["group_id"], "group_name": "Fresh秘密群"}
        self.event.bot = types.SimpleNamespace(call_action=AsyncMock(side_effect=info))
        result = await self.start()
        self.assertIn("1. F***群（19***3724）", result)
        self.assertIn("2. F***群（97***4519）", result)
        for hidden in ("Fresh", "Alpha", "Beta", "秘密群", "191233724", "971234519", "333333333", "555555555"):
            self.assertNotIn(hidden, result)
        self.assertEqual(self.event.bot.call_action.await_count, 2)
        self.store.range_get_async.assert_awaited_once_with("plugin", self.plugin.plugin_id)
        self.assertEqual(self.follows(), ["花海佑芽"])

    async def test_preview_then_explicit_confirmation_overwrites_only_self_and_keeps_order_and_profile(self):
        source = copy.deepcopy(self.plugin.storage[f"tantou_v1:{self.a}"])
        profiles = copy.deepcopy(self.plugin.storage[f"tantou_profiles_v1:{self.target}"])
        await self.start()
        self.assertIn("群序号", await self.confirm())
        preview = await self.choose()
        self.assertIn("当前担当：花海 佑芽", preview)
        self.assertIn("同步后：月村 手毬、小鹿 なお", preview)
        self.assertIn("覆盖", preview)
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.assertIn("回复「确认」", await self.choose())
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.assertIn("同步成功", await self.confirm())
        self.assertEqual(self.follows(), ["月村手毬", "va:21149"])
        self.assertEqual(self.plugin.storage[f"tantou_v1:{self.target}"]["1002"], ["如月千早"])
        self.assertEqual(self.plugin.storage[f"tantou_v1:{self.a}"], source)
        self.assertEqual(self.plugin.storage[f"tantou_profiles_v1:{self.target}"], profiles)
        self.assertEqual(await self.plugin._tantou_birthday_users(self.target, ["月村手毬"]), ["1001"])
        self.assertNotIn((self.target, "1001"), self.plugin._tantou_sync_pending)

    async def test_native_bare_and_explicit_sync_commands_do_not_process_same_event_twice(self):
        native = Event(text="/担当同步")
        result = [reply async for reply in self.plugin.tantou_sync(native)]
        self.assertIn("选择", result[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(native)], [])
        numeric = Event(text="1", mentions=["9999"])
        self.assertIn("同步后", ([reply async for reply in self.plugin.tantou_text_fallback(numeric)])[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(numeric)], [])
        confirmed = Event(text="确认")
        self.assertIn("同步成功", ([reply async for reply in self.plugin.tantou_text_fallback(confirmed)])[0])
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(confirmed)], [])
        restart = Event(text="担当同步")
        self.assertIn("选择", ([reply async for reply in self.plugin.tantou_text_fallback(restart)])[0])
        explicit = Event(text="/担当同步 2")
        self.assertIn("天海 春香", ([reply async for reply in self.plugin.tantou_sync(explicit, "2")])[0])

    async def test_cancel_expiry_wrong_owner_and_wrong_group_never_overwrite(self):
        await self.start()
        for event in (Event(user="1002", text="1"), Event(group="200", text="1"), Event(text="1", mentions=["2002"])):
            self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(event)], [])
            self.assertFalse(event.get_extra("imasbd_tantou_handled", False))
        self.assertIn("有效", await self.choose("1 1"))
        self.assertIn("有效", await self.choose("9" * 4000))
        self.assertEqual(await self.choose("0"), "已取消同步。")
        self.assertEqual([reply async for reply in self.plugin.tantou_text_fallback(Event(text="确认"))], [])
        await self.start()
        await self.choose()
        self.assertEqual(await self.plugin._tantou_sync_reply(Event(text="取消")), "已取消同步。")
        await self.start()
        self.plugin._tantou_sync_pending[(self.target, "1001")]["expires"] = 0
        expired = Event(text="1")
        self.assertIsNone(await self.plugin._tantou_sync_reply(expired))
        self.assertFalse(expired.get_extra("imasbd_tantou_handled", False))
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.assertIn("目标群", await self.plugin._tantou_sync_action(Event(group="")))

    async def test_no_records_or_only_other_users_records_do_not_create_a_selector(self):
        for key in (f"tantou_v1:{self.a}", f"tantou_v1:{self.b}"):
            self.plugin.storage[key].pop("1001")
        self.assertIn("还没有", await self.start())
        self.assertFalse(self.plugin._tantou_sync_pending)

    async def test_source_or_destination_changed_after_preview_requires_new_review(self):
        await self.start()
        await self.choose()
        self.plugin.storage[f"tantou_v1:{self.a}"]["1001"] = ["天海春香"]
        self.assertIn("已变化", await self.confirm())
        self.assertEqual(self.follows(), ["花海佑芽"])
        await self.start()
        await self.choose()
        self.plugin.storage[f"tantou_v1:{self.target}"]["1001"] = ["如月千早"]
        self.assertIn("已变化", await self.confirm())
        self.assertEqual(self.follows(), ["如月千早"])

    async def test_add_remove_clear_cancel_sync_and_new_sync_cancels_add_confirmation(self):
        await self.plugin._change_tantou(self.event, "加推", "手毬")
        self.assertTrue(self.plugin._tantou_pending)
        await self.start()
        self.assertFalse(self.plugin._tantou_pending)
        for command, args in (("加推", "手毬"), ("减推", "花海佑芽"), ("清空担当", "")):
            await self.start()
            await self.plugin._change_tantou(self.event, command, args)
            self.assertNotIn((self.target, "1001"), self.plugin._tantou_sync_pending)
            self.assertIsNone(await self.plugin._tantou_sync_reply(Event(text="确认")))

    async def test_add_during_source_discovery_cannot_be_hijacked_by_late_sync_selector(self):
        entered, release = asyncio.Event(), asyncio.Event()
        real = self.plugin._tantou_saved_groups
        async def delayed():
            entered.set()
            await release.wait()
            return await real()
        with patch.object(self.plugin, "_tantou_saved_groups", side_effect=delayed):
            task = asyncio.create_task(self.start())
            await entered.wait()
            await self.plugin._change_tantou(self.event, "加推", "手毬")
            release.set()
            self.assertIn("已失效", await task)
        self.assertFalse(self.plugin._tantou_sync_pending)
        reply = [item async for item in self.plugin.tantou_text_fallback(Event(text="1"))]
        self.assertIn("添加成功", reply[0])
        self.assertEqual(self.follows(), ["花海佑芽", "月村手毬"])

    async def test_saved_group_enumeration_is_restricted_to_own_plugin_namespace(self):
        rows = await self.preference_rows("plugin", self.plugin.plugin_id)
        rows.extend((types.SimpleNamespace(scope="plugin", scope_id="other/plugin", key="tantou_v1:bot:GroupMessage:777777777", value={"val": {"1001": ["月村手毬"]}}),
                     types.SimpleNamespace(scope="umo", scope_id=self.plugin.plugin_id, key="tantou_v1:bot:GroupMessage:888888888", value={"val": {"1001": ["月村手毬"]}})))
        self.store.range_get_async.side_effect = None
        self.store.range_get_async.return_value = rows
        groups = await self.plugin._tantou_saved_groups()
        self.assertNotIn("bot:GroupMessage:777777777", groups)
        self.assertNotIn("bot:GroupMessage:888888888", groups)
        self.assertIn(self.a, groups)

    async def test_storage_failures_do_not_clear_target_and_can_be_retried(self):
        self.store.range_get_async.side_effect = RuntimeError("database unavailable")
        with self.assertLogs("test_tantou", level="ERROR"):
            self.assertIn("失败", await self.start())
        self.assertFalse(self.plugin._tantou_sync_pending)
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.store.range_get_async.side_effect = self.preference_rows
        await self.start()
        await self.choose()
        real = self.plugin.put_kv_data
        async def fail_target(key, value):
            if key == f"tantou_v1:{self.target}":
                raise RuntimeError("database unavailable")
            await real(key, value)
        with patch.object(self.plugin, "put_kv_data", side_effect=fail_target), self.assertLogs("test_tantou", level="ERROR"):
            self.assertIn("失败", await self.confirm())
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.assertIn("同步成功", await self.confirm())

    async def test_group_members_are_reverified_and_nonmembers_are_shown_as_skipped(self):
        self.plugin.storage[f"tantou_v1:{self.a}"]["1001"] = ["qq:2002", "月村手毬", "qq:3003"]
        self.plugin.storage[f"tantou_members_v1:{self.a}"] = {"2002": {"nickname": "来源群名片"}, "3003": {"nickname": "不在本群的人"}}
        self.plugin.storage[f"tantou_profiles_v1:{self.target}"]["2002"] = {"name": "本群绑定名"}
        install_bot(self.event, [member("2002", nickname="当前群名片")])
        await self.start()
        preview = await self.choose()
        self.assertIn("同步后：本群绑定名、月村 手毬", preview)
        self.assertIn("跳过：不在本群的人", preview)
        self.assertIn("同步成功", await self.confirm())
        self.assertEqual(self.follows(), ["qq:2002", "月村手毬"])
        saved = self.plugin.storage[f"tantou_members_v1:{self.target}"]
        self.assertEqual(saved["2002"]["nickname"], "当前群名片")
        self.assertNotIn("3003", saved)
        self.assertNotIn("avatar", saved["2002"])
        self.event.bot.call_action.assert_any_await("get_group_member_info", group_id=100, user_id=2002, no_cache=True, self_id="9999")

    async def test_member_identity_failure_or_membership_change_never_bypasses_preview(self):
        self.plugin.storage[f"tantou_v1:{self.a}"]["1001"] = ["月村手毬", "qq:2002"]
        install_bot(self.event, [member()])
        await self.start()
        real = self.event.bot.call_action.side_effect
        async def foreign(action, **kwargs):
            return member(group_id="200") if action == "get_group_member_info" else await real(action, **kwargs)
        self.event.bot.call_action.side_effect = foreign
        self.assertIn("未通过", await self.choose())
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.event.bot.call_action.side_effect = real
        self.assertIn("同步后", await self.choose())
        install_bot(self.event, [])
        self.assertIn("状态已变化", await self.confirm())
        self.assertEqual(self.follows(), ["花海佑芽"])
        self.plugin.storage[f"tantou_v1:{self.a}"]["1001"] = ["qq:2002"]
        await self.start()
        self.assertIn("没有可同步", await self.choose())
        self.assertEqual(self.follows(), ["花海佑芽"])
