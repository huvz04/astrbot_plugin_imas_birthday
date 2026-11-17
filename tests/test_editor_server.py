import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
from test_tantou import make_plugin, plugin_module

spec = importlib.util.spec_from_file_location('editor_server_test', Path(__file__).resolve().parents[1] / 'editor_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EditorServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin = make_plugin()
        self.server = module.EditorServer(self.plugin)
        self.server.password = 'test-password-106'
        app = web.Application(middlewares=[self.server.security])
        app.router.add_route('*', '/login', self.server.login)
        app.router.add_route('*', '/api/{module}/{action}', self.server.api)
        app.router.add_get('/{path:.*}', self.server.asset)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    async def login(self):
        return await self.client.post('/login', json={'password': self.server.password}, headers={'X-Imas-Editor':'1'})

    async def test_authentication_csrf_expiry_and_static_containment(self):
        self.assertEqual((await self.client.get('/api/people/list')).status, 401)
        self.assertEqual((await self.client.post('/login', json={'password': self.server.password})).status, 403)
        self.assertEqual((await self.client.post('/login', json={'password':'wrong'}, headers={'X-Imas-Editor':'1'})).status,401)
        response = await self.login()
        self.assertEqual(response.status,200)
        self.assertTrue(response.cookies['imas_editor']['httponly'])
        page = await self.client.get('/')
        self.assertEqual(page.status,200)
        content = await page.text()
        self.assertIn('characters-panel',content)
        self.assertIn('music-panel',content)
        self.assertIn('window.AstrBotPluginPage',content)
        self.assertEqual((await self.client.get('/metadata.yaml')).status,404)
        self.assertEqual((await self.client.post('/api/people/save',json={},headers={'X-Imas-Editor':'1','Origin':'https://evil.example'})).status,403)
        self.server.sessions.clear()
        self.assertEqual((await self.client.get('/api/people/list')).status,401)

    async def test_rate_limit_and_password_required(self):
        for _ in range(10):
            await self.client.post('/login',json={'password':'wrong'},headers={'X-Imas-Editor':'1'})
        self.assertEqual((await self.login()).status,429)
        self.plugin.config.update(editor_web_enabled=True,editor_web_password='short')
        with self.assertRaises(ValueError):
            await self.server.start()
        self.assertIsNone(self.server.runner)

    async def test_non_imas_music_birthdays_are_not_in_reminder_or_card(self):
        self.plugin._get_source_birthdays = AsyncMock(return_value={'06-04': {'characters':[], 'seiyuu':[], 'related_people':[], 'events':[]}})
        self.assertTrue(plugin_module.PUBLIC_PEOPLE_CATALOGUE['mc:manual:tanaka-hidekazu']['birthday'])
        data = await self.plugin._get_birthdays()
        self.assertEqual(data['06-04']['characters'],[])
        result = await self.plugin._build_result(6,4)
        self.assertEqual(result,{'message':'','card_path':''})
